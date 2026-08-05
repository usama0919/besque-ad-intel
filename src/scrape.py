"""Live Apify Meta Ad Library scrape. Maps results to the pipeline ad dict,
including a direct downloadable image URL for verifiable image-based analysis."""
import logging
import os
import threading
import time
from apify_client import ApifyClient

log = logging.getLogger("scrape")

APIFY_ACTOR = os.getenv("APIFY_ACTOR_ID", "automly/facebook-ad-library-scraper")


def _call_actor_with_heartbeat(client, actor_id, run_input):
    """client.actor(actor_id).call(...) blocks until the Apify actor run reaches a
    terminal state, which can take several minutes with zero output otherwise - the exact
    silence that made a real run look hung (2026-08-04 diagnosis). Runs the call on a
    background thread and logs a heartbeat every 30s while it's still running. Deliberately
    NO timeout: the actor is doing real, billed work fetching the ad pool - killing it
    mid-run would waste the fetch, not save time; this only adds visibility.

    Also quiets one specific, expected failure while the call is in flight: apify_client
    spawns its OWN background thread (_stream_log) that streams the actor's live console
    output to ours - the "[apify...] -> Scraped N/M ads" lines. On a long-running actor
    (observed 2026-08-04: a ~5min run) that stream's own HTTP connection can time out
    mid-run and raise an uncaught impit.TimeoutException in that thread, which Python's
    default threading.excepthook dumps as a full traceback. The actor run itself is a
    separate, unrelated poll and is unaffected - this is a cosmetic failure of the
    log-streaming convenience feature, not a real error, so it's caught and logged as one
    line instead for the duration of this call only; the previous hook (whatever it
    actually was, not a stale module-level snapshot) is restored in `finally` and used for
    every other thread's exception."""
    previous_hook = threading.excepthook

    def _quiet_stream_log_timeout(args):
        if args.exc_type is not None and args.exc_type.__module__ == "impit" \
                and args.exc_type.__name__ == "TimeoutException":
            log.info("Apify log-stream connection timed out (actor run unaffected, cosmetic only): %s",
                      args.exc_value)
            return
        previous_hook(args)

    result, error = {}, {}

    def _target():
        try:
            result["run"] = client.actor(actor_id).call(run_input=run_input)
        except Exception as e:
            error["exc"] = e

    threading.excepthook = _quiet_stream_log_timeout
    try:
        thread = threading.Thread(target=_target, daemon=True)
        started = time.monotonic()
        thread.start()
        while thread.is_alive():
            thread.join(timeout=30)
            if thread.is_alive():
                log.info("Still waiting on Apify actor, elapsed %ss", int(time.monotonic() - started))
    finally:
        threading.excepthook = previous_hook
    if "exc" in error:
        raise error["exc"]
    return result["run"]


def _map_ad(raw):
    bodies = raw.get("ad_creative_bodies") or []
    text = next((b for b in bodies if "{{" not in b), bodies[0] if bodies else "")
    images = raw.get("images") or []
    return {
        "ad_id": raw.get("ad_archive_id"),
        "page_name": raw.get("page_name", ""),
        "page_id": str(raw.get("page_id") or raw.get("pageId") or ""),
        "text": text,
        "media_type": raw.get("media_type", ""),
        "image_url": images[0] if images else None,
        "start_date": raw.get("ad_delivery_start_time", ""),
        "cta": raw.get("cta_type", ""),
        "destination_url": raw.get("link_url", ""),
        "snapshot_url": raw.get("ad_snapshot_url", ""),
    }


def _page_matches(page_name, search_term):
    """True if the ad's page matches the competitor searched.
    Contains-match OR fuzzy similarity (catches near-miss names like
    '40 Plus & Fabulous' vs 'Over 40 & Fabulous')."""
    from difflib import SequenceMatcher
    a = (page_name or "").strip().lower()
    b = (search_term or "").strip().lower()
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    if SequenceMatcher(None, a, b).ratio() >= 0.65:
        return True
    wa, wb = set(a.replace("&", " ").split()), set(b.replace("&", " ").split())
    overlap = len(wa & wb) / max(1, min(len(wa), len(wb)))
    return overlap >= 0.6


# Reason keys a rejected record can carry - shared vocabulary between scrape.py's
# classification and pipeline.fetch_pool's per-reason skipped breakdown (Chunk 2,
# Part A/3a). "duplicate" is never produced here - it's a pool-level concept (same
# ad_id twice in one pull) that only fetch_pool can detect, since a single record's
# classification has no visibility into the rest of the batch.
REJECT_NOT_IMAGE = "not_image"
REJECT_WRONG_PAGE = "wrong_page"
REJECT_NO_IMAGE_URL = "no_image_url"


def _scrape_raw(search_term, max_results=None, image_only=True, page_id=None,
                 start_date_min=None, start_date_max=None, active_status="active"):
    """Shared core: call the Apify actor and iterate the dataset, applying the same
    image-only / page-match filter every caller must use. Returns a list of
    (raw, mapped, reason) triples for EVERY record Apify returned - mapped is None
    for anything filtered out, and reason is one of the REJECT_* constants above
    (None for survivors). scrape_ads and pipeline.fetch_pool (via
    scrape_ads_with_raw) both sit on top of this single pass rather than each
    re-implementing the filter, so the two can never drift apart the way
    update_competitor's read-modify-write once did for page_ids. Because scrape_ads
    shares this core, widening the filter here (Chunk 2C, below) also widens what
    run_once/process_ad ever see as candidate ads, not just fetch_pool - the same
    single-filter guarantee that motivated sharing this core in the first place.

    image_only, despite the name, no longer means "media_type == IMAGE" - a
    2026-08-04 investigation against real L'Occitane data found DCO and CAROUSEL
    records that carry a perfectly usable static image (verified live: real JPEGs,
    200/image-jpeg/real content-length) but were being rejected purely for having
    the "wrong" media_type. It now means "has a usable static image": accept any
    record whose first `images` entry is non-empty, regardless of media_type
    (IMAGE/DCO/CAROUSEL alike); reject only when that's genuinely absent (pure
    VIDEO with no images array, or a DCO/CAROUSEL that happens to carry none).
    REJECT_NOT_IMAGE keeps its name for continuity with the existing per-reason
    breakdown, but now means "no usable image", not "wrong media_type" literally.
    mediaType handling itself is UNCHANGED here (Chunk 6.2) - the actor's own
    mediaType enum (all/image/video/meme/none, confirmed 2026-08-04 against its
    real input schema) doesn't offer Meta's own image_and_meme value, and the
    actor doesn't honour it reliably anyway (CLAUDE.md), so client-side filtering
    stays the only real gate.

    A record missing ad_id is folded into REJECT_NO_IMAGE_URL - the caller-facing
    reason vocabulary has no separate bucket for it (it's a defensive branch, never
    observed in practice), and "this record can't produce a usable mapped ad" is
    the accurate characterization either way.

    start_date_min/start_date_max (Chunk 6.2) map to the actor's own top-level
    startDateMin/startDateMax fields, AND to the view_all_page_id URL's own
    start_date[min]/[max] query params when using the urls input path - the
    actor was observed live ignoring the top-level fields in that path, the
    same class of gap active_status had. Both None (the default) omits them
    entirely, matching today's behaviour exactly.

    active_status (Chunk 6.2) defaults to "active", matching today's behaviour -
    but that default is exactly why a page with ~1,200 ads returned zero live:
    ads paused (inactive) are invisible under an active-only filter. Threaded to
    BOTH surfaces that can carry it: the actor's own top-level activeStatus
    field, AND the view_all_page_id URL's active_status query param when using
    the urls input path - that URL hardcoded active_status=active before this
    change, which is the actual mechanism of the bug (a top-level activeStatus
    field alone would not have overridden it)."""
    token = os.getenv("APIFY_TOKEN")
    if not token:
        raise ValueError("APIFY_TOKEN must be set")

    client = ApifyClient(token)
    fetch_cap = int(max_results) if max_results is not None else int(os.getenv("SCRAPE_FETCH_CAP", "50"))
    use_page = bool(page_id) and str(page_id).strip() != "" and str(page_id).strip() != str(search_term).strip()
    if use_page:
        pid = str(page_id).strip()
        if "facebook.com" not in pid:
            pid = (f"https://www.facebook.com/ads/library/?active_status={active_status}"
                   f"&ad_type=all&country=ALL&view_all_page_id={pid}")
            if start_date_min:
                pid += f"&start_date%5Bmin%5D={start_date_min}"
            if start_date_max:
                pid += f"&start_date%5Bmax%5D={start_date_max}"
        run_input = {"urls": [{"url": pid}], "maxAds": fetch_cap, "mediaType": "image"}
    else:
        run_input = {"searchTerms": [search_term], "maxResults": fetch_cap, "maxAds": fetch_cap, "mediaType": "image"}
    run_input["activeStatus"] = active_status
    if start_date_min:
        run_input["startDateMin"] = start_date_min
    if start_date_max:
        run_input["startDateMax"] = start_date_max
    log.info("Apify run_input: %s", run_input)
    run = _call_actor_with_heartbeat(client, APIFY_ACTOR, run_input)

    results = []
    for raw in client.dataset(run.default_dataset_id).iterate_items():
        mapped = _map_ad(raw)
        if image_only and not mapped["image_url"]:
            # Previously silent (a bare `continue`, no print) - this is the single
            # biggest rejection bucket in practice (pure-video ads with no images
            # array at all), and it was invisible in every log until now.
            print(f"[scrape] rejected ad {mapped.get('ad_id','?')}: no usable static image "
                  f"(media_type={raw.get('media_type')!r})")
            results.append((raw, None, REJECT_NOT_IMAGE))
            continue
        if mapped["ad_id"] and mapped["image_url"] and (use_page or _page_matches(mapped.get("page_name", ""), search_term)):
            results.append((raw, mapped, None))
        else:
            if not mapped["ad_id"] or not mapped["image_url"]:
                reason_key = REJECT_NO_IMAGE_URL
                reason_text = "no ad_id" if not mapped["ad_id"] else "no image (video ad?)"
            else:
                reason_key = REJECT_WRONG_PAGE
                reason_text = f"page mismatch: got page_name={mapped.get('page_name','')!r} vs search={search_term!r}"
            print(f"[scrape] rejected ad {mapped.get('ad_id','?')}: {reason_text}")
            results.append((raw, None, reason_key))
    return results


def scrape_ads(search_term, max_results=None, image_only=True, page_id=None,
                start_date_min=None, start_date_max=None, active_status="active"):
    """Run the Apify actor. Returns mapped ad dicts, filtered to image ads
    that have both an ad_id and a downloadable image URL.

    max_results is an explicit per-call cap on how many ads Apify returns.
    None (the default) means use SCRAPE_FETCH_CAP.

    Deliberately NOT wired to the pipeline's max_per_competitor: that caps how
    many *new* ads get processed after the seen_ads gate, while this caps the
    candidate pool fetched before it. Scraping wide and processing narrow is the
    point - do not couple them again.

    start_date_min/start_date_max/active_status (Chunk 6.2): see _scrape_raw's
    own docstring - active_status defaults to "active", matching today's
    behaviour exactly.
    """
    return [mapped for raw, mapped, reason in _scrape_raw(
        search_term, max_results, image_only, page_id,
        start_date_min=start_date_min, start_date_max=start_date_max, active_status=active_status,
    ) if mapped]


def scrape_ads_with_raw(search_term, max_results=None, image_only=True, page_id=None,
                         start_date_min=None, start_date_max=None, active_status="active"):
    """Like _scrape_raw, but named for external callers: returns (raw, mapped, reason)
    triples for EVERY record Apify returned (mapped=None and reason set to one of
    the REJECT_* constants for anything the filter rejected). Used by
    pipeline.fetch_pool, which needs the full unmodified Apify record to persist as
    raw_meta and needs the per-reason skipped breakdown - scrape_ads alone only
    exposes survivors with no reason at all."""
    return _scrape_raw(search_term, max_results, image_only, page_id,
                        start_date_min=start_date_min, start_date_max=start_date_max, active_status=active_status)
