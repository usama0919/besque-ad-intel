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


def scrape_ads(search_term, max_results=None, image_only=True, page_id=None):
    """Run the Apify actor. Returns mapped ad dicts, filtered to image ads
    that have both an ad_id and a downloadable image URL.

    max_results is an explicit per-call cap on how many ads Apify returns.
    None (the default) means use SCRAPE_FETCH_CAP.

    Deliberately NOT wired to the pipeline's max_per_competitor: that caps how
    many *new* ads get processed after the seen_ads gate, while this caps the
    candidate pool fetched before it. Scraping wide and processing narrow is the
    point - do not couple them again.
    """
    token = os.getenv("APIFY_TOKEN")
    if not token:
        raise ValueError("APIFY_TOKEN must be set")

    client = ApifyClient(token)
    fetch_cap = int(max_results) if max_results is not None else int(os.getenv("SCRAPE_FETCH_CAP", "50"))
    use_page = bool(page_id) and str(page_id).strip() != "" and str(page_id).strip() != str(search_term).strip()
    if use_page:
        pid = str(page_id).strip()
        if "facebook.com" not in pid:
            pid = f"https://www.facebook.com/ads/library/?active_status=active&ad_type=all&country=ALL&view_all_page_id={pid}"
        run_input = {"urls": [{"url": pid}], "maxAds": fetch_cap, "mediaType": "image"}
    else:
        run_input = {"searchTerms": [search_term], "maxResults": fetch_cap, "maxAds": fetch_cap, "mediaType": "image"}
    run = _call_actor_with_heartbeat(client, APIFY_ACTOR, run_input)

    ads = []
    for raw in client.dataset(run.default_dataset_id).iterate_items():
        if image_only and raw.get("media_type") != "IMAGE":
            continue
        mapped = _map_ad(raw)
        if mapped["ad_id"] and mapped["image_url"] and (use_page or _page_matches(mapped.get("page_name", ""), search_term)):
            ads.append(mapped)
        else:
            reason = "no ad_id" if not mapped["ad_id"] else ("no image (video ad?)" if not mapped["image_url"] else f"page mismatch: got page_name={mapped.get('page_name','')!r} vs search={search_term!r}")
            print(f"[scrape] rejected ad {mapped.get('ad_id','?')}: {reason}")
    return ads
