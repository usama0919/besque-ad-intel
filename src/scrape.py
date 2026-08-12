"""Live Apify Meta Ad Library scrape. Maps results to the pipeline ad dict,
including a direct downloadable image URL for verifiable image-based analysis."""
import logging
import os
import time
from apify_client import ApifyClient

log = logging.getLogger("scrape")

APIFY_ACTOR = os.getenv("APIFY_ACTOR_ID", "automly/facebook-ad-library-scraper")

# How often the watch loop below polls the run's status and its dataset's item count.
POLL_INTERVAL_SECONDS = 20

# How long a still-RUNNING/READY run's dataset may sit at the same item count before
# we give up waiting for more and abort it ourselves, using whatever's already there.
# Measured live 2026-08-12 (Crepe Erase, competitor 40, page_id 1503645236586955 -
# named here only as the incident that surfaced this, never hardcoded into the logic
# below): the actor reached 42 image ads against a cap of 50, then produced ZERO
# further dataset items for the rest of its own run - it did not hang forever, it
# self-terminated after 565s total ("Done! Total ads scraped: 42", confirmed via the
# Apify API against run gCRWoLSnIcL2pWYsj) having spent most of that time paginating
# for 8 ads that don't exist on the page. Our old code
# (client.actor(actor_id).call(...), removed here) had no timeout at all and no
# visibility into the dataset while waiting, so it just blocked - the fix isn't a
# shorter fixed wait, it's noticing growth has stopped and bailing early with what's
# already been collected. 3 minutes is generous slack for a genuinely slow next page
# while stopping well short of either ceiling below.
STAGNATION_TIMEOUT_SECONDS = 180

# Absolute backstop regardless of item-count growth - protects against a run that
# keeps trickling one item every few minutes forever, which would never trip the
# stagnation check above. Set with real headroom above the longest legitimate run
# observed in this account's own history (799s, measured 2026-08-12 across the most
# recent 100 runs of this actor - none failed, aborted, or timed out; all 100
# eventually reached SUCCEEDED), not merely above the incident that prompted this fix.
ACTOR_POLL_HARD_CEILING_SECONDS = 1500

# A run in either of these statuses is still doing real work on Apify's side.
ACTIVE_RUN_STATUSES = ("READY", "RUNNING")


def get_run_status(run_id):
    """Thin, stateless lookup of one Apify run's live status/dataset - used by
    pipeline.fetch_pool to check whether a run_id persisted on a prior fetch_jobs row
    (from an attempt whose own process died mid-poll) is STILL genuinely active on
    Apify before deciding whether starting a fresh actor run would create a real,
    billed, concurrent duplicate for the same competitor. Returns None if the token
    isn't set, run_id is falsy, or the lookup itself fails for any reason - never
    raises, since every caller treats "can't confirm it's still running" the same as
    "safe to start a new one", which is the current (pre-fix) behaviour anyway."""
    token = os.getenv("APIFY_TOKEN")
    if not token or not run_id:
        return None
    try:
        run = ApifyClient(token).run(run_id).get()
    except Exception as e:
        log.info("Could not check status of Apify run %s: %s", run_id, e)
        return None
    if run is None:
        return None
    return {"status": run.status, "dataset_id": run.default_dataset_id}


def _dataset_item_count(client, dataset_id):
    """Best-effort - a transient read failure must never crash the watch loop below,
    it only means this particular poll saw no evidence of growth."""
    try:
        ds = client.dataset(dataset_id).get()
        return ds.item_count if ds else None
    except Exception as e:
        log.info("Could not read Apify dataset item count for %s: %s", dataset_id, e)
        return None


def _abort_run(client, run_id):
    """gracefully=True asks Apify to flush anything already in flight to the dataset
    before stopping, rather than killing it mid-write - we're about to read that
    dataset ourselves, so a clean stop matters here more than a fast one."""
    try:
        client.run(run_id).abort(gracefully=True)
    except Exception as e:
        log.info("Abort request for Apify run %s failed (continuing anyway): %s", run_id, e)


def _watch_actor_run(client, run_id, dataset_id):
    """Poll one already-started Apify actor run until it reaches a terminal state, OR
    its dataset stops growing for STAGNATION_TIMEOUT_SECONDS while still active, OR
    ACTOR_POLL_HARD_CEILING_SECONDS elapses in total - whichever comes first. Replaces
    the old client.actor(actor_id).call(...), which blocked with no timeout and no
    visibility into whether the dataset was still growing (2026-08-12 incident, see
    STAGNATION_TIMEOUT_SECONDS above).

    On stagnation or the hard ceiling, aborts the run rather than merely walking away
    from it - the whole point of this fix is to stop paying for/waiting on a run we've
    decided not to wait for any longer, not to give up locally while it keeps running
    unattended on Apify's side. Either way, the dataset itself is never touched here -
    the caller always iterates whatever ended up in it, salvaging a partial pool
    instead of failing the whole fetch."""
    started = time.monotonic()
    last_count = _dataset_item_count(client, dataset_id) or 0
    last_growth = started
    while True:
        # Checked BEFORE any sleep, including the very first iteration - a run that's
        # already terminal by the time we look (common for a fast real run, and the
        # only way a test can exercise this without a real 20s wait) must return
        # immediately, never wait a full poll interval just to notice that.
        elapsed = time.monotonic() - started
        try:
            run = client.run(run_id).get()
        except Exception as e:
            log.info("Apify run status check failed for %s (will retry next poll): %s", run_id, e)
            run = None
        status = run.status if run else None
        if status not in ACTIVE_RUN_STATUSES:
            log.info("Apify run %s reached terminal state %s after %ds, %s items",
                      run_id, status, int(elapsed), last_count)
            return

        count = _dataset_item_count(client, dataset_id)
        if count is not None and count > last_count:
            last_count, last_growth = count, time.monotonic()
        stagnant_for = time.monotonic() - last_growth

        if stagnant_for >= STAGNATION_TIMEOUT_SECONDS:
            log.info(
                "Apify run %s produced no new dataset items for %ds (stuck at %s items) - "
                "aborting and using what's already in the dataset", run_id, int(stagnant_for), last_count,
            )
            _abort_run(client, run_id)
            return

        if elapsed >= ACTOR_POLL_HARD_CEILING_SECONDS:
            log.info(
                "Apify run %s exceeded the %ds hard ceiling (still growing, at %s items) - "
                "aborting and using what's already in the dataset",
                run_id, ACTOR_POLL_HARD_CEILING_SECONDS, last_count,
            )
            _abort_run(client, run_id)
            return

        log.info("Still waiting on Apify actor %s, elapsed %ds, %s items so far", run_id, int(elapsed), last_count)
        time.sleep(POLL_INTERVAL_SECONDS)


def _run_actor_and_get_dataset(client, actor_id, run_input, on_run_started=None, existing_run_id=None):
    """Start a fresh Apify actor run - or, if existing_run_id is given (a run_id
    persisted on a prior, since-abandoned attempt's fetch_jobs row and confirmed via
    get_run_status to still be active), adopt and watch THAT run instead of starting a
    second, real, billed duplicate for the same competitor.

    on_run_started(run_id, dataset_id), if given, fires the moment the run_id is known
    - BEFORE the (possibly long) watch loop - so a caller can persist it immediately.
    This is what makes the existing_run_id path possible at all: without persisting
    the id before waiting, a process that dies mid-poll leaves nothing for the next
    attempt to find, even though the real Apify run and its dataset are still there.

    Returns the dataset_id to iterate - always, regardless of whether the run ended
    normally, was aborted for stagnation, or hit the hard ceiling; see
    _watch_actor_run's own docstring for why the dataset is never gated on how the run
    ended."""
    if existing_run_id:
        run = client.run(existing_run_id).get()
        if run is None or run.status not in ACTIVE_RUN_STATUSES:
            # Finished (or vanished) since it was persisted - nothing left to watch,
            # just read whatever its dataset already holds.
            return run.default_dataset_id if run else None
        run_id, dataset_id = existing_run_id, run.default_dataset_id
        log.info("Adopting still-active Apify run %s (dataset %s) instead of starting a duplicate",
                  run_id, dataset_id)
    else:
        run = client.actor(actor_id).start(run_input=run_input)
        run_id, dataset_id = run.id, run.default_dataset_id
        log.info("Started Apify actor run %s (dataset %s)", run_id, dataset_id)

    if on_run_started:
        on_run_started(run_id, dataset_id)

    _watch_actor_run(client, run_id, dataset_id)
    return dataset_id


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
                 start_date_min=None, start_date_max=None, active_status="active",
                 on_run_started=None, existing_run_id=None):
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
    field alone would not have overridden it).

    on_run_started/existing_run_id (2026-08-12, fetch-hang fix): see
    _run_actor_and_get_dataset's own docstring - both None (the default) reproduces a
    plain fresh actor run with no persistence hook, unchanged for callers (run_once's
    plain scrape_ads) that have no fetch_jobs-shaped place to persist a run_id anyway.
    Only pipeline.fetch_pool passes them today."""
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
    dataset_id = _run_actor_and_get_dataset(client, APIFY_ACTOR, run_input,
                                             on_run_started=on_run_started,
                                             existing_run_id=existing_run_id)
    if not dataset_id:
        log.info("Apify run produced no dataset to read (adopted run had already vanished)")
        return []

    results = []
    for raw in client.dataset(dataset_id).iterate_items():
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
                         start_date_min=None, start_date_max=None, active_status="active",
                         on_run_started=None, existing_run_id=None):
    """Like _scrape_raw, but named for external callers: returns (raw, mapped, reason)
    triples for EVERY record Apify returned (mapped=None and reason set to one of
    the REJECT_* constants for anything the filter rejected). Used by
    pipeline.fetch_pool, which needs the full unmodified Apify record to persist as
    raw_meta and needs the per-reason skipped breakdown - scrape_ads alone only
    exposes survivors with no reason at all.

    on_run_started/existing_run_id: see _scrape_raw's own docstring - fetch_pool is
    the only caller that passes these, since it's the only one with a fetch_jobs row
    to persist a run_id onto."""
    return _scrape_raw(search_term, max_results, image_only, page_id,
                        start_date_min=start_date_min, start_date_max=start_date_max, active_status=active_status,
                        on_run_started=on_run_started, existing_run_id=existing_run_id)
