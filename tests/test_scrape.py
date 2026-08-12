"""Tests for src/scrape.py's shared _scrape_raw core - scrape_ads and
scrape_ads_with_raw must apply the identical image-only/page-match filter, since
pipeline.fetch_pool's counts depend on that never drifting apart from run_once's
own scrape_ads path. No real Apify call: ApifyClient is monkeypatched.

2026-08-12 (fetch-hang fix): _scrape_raw no longer calls client.actor(id).call(...)
(blocked with no timeout) - it calls .start(...) then watches the run itself via
client.run(run_id).get() and client.dataset(dataset_id).get().item_count, so the
fakes below model THAT interaction. Every fake run defaults to an already-terminal
status (SUCCEEDED) so _watch_actor_run's very first check returns immediately with
zero real sleeps - tests that need to exercise the watch loop's own timing
(stagnation/hard-ceiling) monkeypatch time.monotonic/time.sleep with a fake clock
instead of waiting on wall-clock time."""
import os
from src import scrape


class _FakeRun:
    def __init__(self, run_id="run1", dataset_id="ds1", status="SUCCEEDED"):
        self.id = run_id
        self.default_dataset_id = dataset_id
        self.status = status


class _FakeDataset:
    def __init__(self, items):
        self._items = items
        self.item_count = len(items)

    def iterate_items(self):
        return iter(self._items)

    def get(self):
        return self


class _FakeRunHandle:
    def __init__(self, run, abort_capture=None):
        self._run = run
        self._abort_capture = abort_capture

    def get(self):
        return self._run

    def abort(self, gracefully=None):
        if self._abort_capture is not None:
            self._abort_capture.append(gracefully)


class _FakeActorHandle:
    def __init__(self, run_input_capture, run):
        self._capture = run_input_capture
        self._run = run

    def start(self, run_input=None):
        self._capture.append(run_input)
        return self._run


class _FakeApifyClient:
    def __init__(self, items, run_input_capture, run=None, abort_capture=None):
        self._items = items
        self._capture = run_input_capture
        self._run = run or _FakeRun()
        self._abort_capture = abort_capture

    def actor(self, actor_id):
        return _FakeActorHandle(self._capture, self._run)

    def dataset(self, dataset_id):
        return _FakeDataset(self._items)

    def run(self, run_id):
        return _FakeRunHandle(self._run, self._abort_capture)


RAW_ITEMS = [
    {"ad_archive_id": "A1", "page_name": "Bangn Body", "media_type": "IMAGE",
     "images": ["http://x/a1.jpg"], "ad_delivery_start_time": "2026-01-01",
     "impressions": {"lower_bound": "1000"}, "spend": {"lower_bound": "50"}},
    {"ad_archive_id": "A2", "page_name": "Some Other Brand", "media_type": "IMAGE",
     "images": ["http://x/a2.jpg"]},  # page mismatch
    {"ad_archive_id": "", "page_name": "Bangn Body", "media_type": "IMAGE",
     "images": ["http://x/a3.jpg"]},  # no ad_id
    {"ad_archive_id": "A4", "page_name": "Bangn Body", "media_type": "VIDEO",
     "images": []},  # not an image ad
]


def _patch_client(monkeypatch, items):
    monkeypatch.setenv("APIFY_TOKEN", "fake-token")
    capture = []
    monkeypatch.setattr(scrape, "ApifyClient", lambda token: _FakeApifyClient(items, capture))
    return capture


def test_scrape_ads_returns_only_survivors(monkeypatch):
    _patch_client(monkeypatch, RAW_ITEMS)
    ads = scrape.scrape_ads("Bangn Body")
    assert [a["ad_id"] for a in ads] == ["A1"]


def test_scrape_ads_with_raw_returns_full_set_with_none_for_rejects(monkeypatch):
    _patch_client(monkeypatch, RAW_ITEMS)
    triples = scrape.scrape_ads_with_raw("Bangn Body")
    assert len(triples) == len(RAW_ITEMS)
    survivors = [(raw, mapped, reason) for raw, mapped, reason in triples if mapped]
    assert len(survivors) == 1
    raw, mapped, reason = survivors[0]
    assert mapped["ad_id"] == "A1"
    assert reason is None
    # raw_meta must be the ENTIRE unmodified Apify record, not a subset
    assert raw == RAW_ITEMS[0]
    assert raw["impressions"] == {"lower_bound": "1000"}
    assert raw["spend"] == {"lower_bound": "50"}


def test_scrape_ads_with_raw_tags_each_reject_with_its_reason(monkeypatch):
    """Chunk 2, Part A/3a: fetch_pool's per-reason skipped breakdown depends on
    these exact reason keys never drifting - REJECT_NOT_IMAGE for the media_type
    filter (previously silent, now also printed), REJECT_WRONG_PAGE for a page
    mismatch, REJECT_NO_IMAGE_URL for a missing ad_id or image."""
    _patch_client(monkeypatch, RAW_ITEMS)
    triples = scrape.scrape_ads_with_raw("Bangn Body")
    by_ad_id = {raw.get("ad_archive_id"): reason for raw, mapped, reason in triples}
    assert by_ad_id["A2"] == scrape.REJECT_WRONG_PAGE
    assert by_ad_id[""] == scrape.REJECT_NO_IMAGE_URL  # missing ad_id folds into this bucket
    assert by_ad_id["A4"] == scrape.REJECT_NOT_IMAGE
    assert by_ad_id["A1"] is None  # the one survivor


def test_scrape_ads_and_scrape_ads_with_raw_agree_on_survivors(monkeypatch):
    """The two entry points must never disagree on which ads pass the filter -
    they share the same _scrape_raw core precisely so this can't drift."""
    _patch_client(monkeypatch, RAW_ITEMS)
    plain = scrape.scrape_ads("Bangn Body")
    with_raw = scrape.scrape_ads_with_raw("Bangn Body")
    survivors_from_raw = [mapped["ad_id"] for raw, mapped, reason in with_raw if mapped]
    assert [a["ad_id"] for a in plain] == survivors_from_raw


# ---- Chunk 2C: image_only widened to "has a usable static image", not literally
# "media_type == IMAGE" - a live L'Occitane investigation found real, fetchable
# static images on DCO and CAROUSEL records that were being rejected purely for
# their media_type. ----

RAW_ITEMS_WIDENED_FILTER = [
    {"ad_archive_id": "D1", "page_name": "Bangn Body", "media_type": "DCO",
     "images": ["http://x/d1a.jpg", "http://x/d1b.jpg"]},  # DCO WITH a usable image
    {"ad_archive_id": "D2", "page_name": "Bangn Body", "media_type": "DCO",
     "images": [], "videos": ["http://x/d2.mp4", "http://x/d2b.mp4"]},  # DCO, no image at all
    {"ad_archive_id": "C1", "page_name": "Bangn Body", "media_type": "CAROUSEL",
     "images": ["http://x/c1a.jpg", "http://x/c1b.jpg", "http://x/c1c.jpg"]},  # CAROUSEL with images
    {"ad_archive_id": "V1", "page_name": "Bangn Body", "media_type": "VIDEO",
     "images": [], "videos": ["http://x/v1.mp4"]},  # pure video, still rejected
]


def test_scrape_ads_with_raw_accepts_dco_and_carousel_with_a_usable_image(monkeypatch):
    _patch_client(monkeypatch, RAW_ITEMS_WIDENED_FILTER)
    triples = scrape.scrape_ads_with_raw("Bangn Body")
    by_ad_id = {raw.get("ad_archive_id"): (mapped, reason) for raw, mapped, reason in triples}

    d1_mapped, d1_reason = by_ad_id["D1"]
    assert d1_reason is None
    assert d1_mapped["media_type"] == "DCO"
    assert d1_mapped["image_url"] == "http://x/d1a.jpg"  # first image, not all of them

    c1_mapped, c1_reason = by_ad_id["C1"]
    assert c1_reason is None
    assert c1_mapped["media_type"] == "CAROUSEL"
    assert c1_mapped["image_url"] == "http://x/c1a.jpg"

    d2_mapped, d2_reason = by_ad_id["D2"]
    assert d2_mapped is None
    assert d2_reason == scrape.REJECT_NOT_IMAGE  # DCO with genuinely no image

    v1_mapped, v1_reason = by_ad_id["V1"]
    assert v1_mapped is None
    assert v1_reason == scrape.REJECT_NOT_IMAGE  # pure video, unchanged behaviour


def test_scrape_ads_also_surfaces_dco_and_carousel_survivors(monkeypatch):
    """scrape_ads (run_once's own path) shares _scrape_raw with scrape_ads_with_raw
    - the widened filter necessarily widens what run_once ever sees too, not just
    fetch_pool, since they're the same code path by design."""
    _patch_client(monkeypatch, RAW_ITEMS_WIDENED_FILTER)
    ads = scrape.scrape_ads("Bangn Body")
    assert {a["ad_id"] for a in ads} == {"D1", "C1"}


# ---- Chunk 6.2: start_date_min/start_date_max/active_status reach the actor,
# and active_status also lands in the constructed view_all_page_id URL (not
# just as a top-level field) - that URL hardcoded active_status=active before
# this change, which is the actual mechanism of the "1,200 ads, 0 returned"
# bug (all paused). mediaType handling is untouched by any of this. ----

def test_scrape_ads_with_raw_sends_date_window_and_active_status_to_actor(monkeypatch):
    capture = _patch_client(monkeypatch, [])
    scrape.scrape_ads_with_raw("Bangn Body", start_date_min="2026-01-01",
                                start_date_max="2026-02-01", active_status="inactive")
    run_input = capture[0]
    assert run_input["startDateMin"] == "2026-01-01"
    assert run_input["startDateMax"] == "2026-02-01"
    assert run_input["activeStatus"] == "inactive"
    assert run_input["mediaType"] == "image"  # untouched


def test_scrape_ads_with_raw_omits_date_window_when_not_given(monkeypatch):
    capture = _patch_client(monkeypatch, [])
    scrape.scrape_ads_with_raw("Bangn Body")
    run_input = capture[0]
    assert "startDateMin" not in run_input
    assert "startDateMax" not in run_input
    assert run_input["activeStatus"] == "active"  # default matches today's behaviour


def test_scrape_ads_with_raw_active_status_reaches_the_page_url_not_just_the_top_level_field(monkeypatch):
    """The view_all_page_id URL hardcoded active_status=active before this fix -
    a top-level activeStatus field alone would not have overridden it. This is
    the actual mechanism of the bug (a page with ~1,200 paused ads returning
    zero), so the URL itself must carry the requested value."""
    capture = _patch_client(monkeypatch, [])
    scrape.scrape_ads_with_raw("Brand", page_id="123456789", active_status="all")
    run_input = capture[0]
    assert run_input["activeStatus"] == "all"
    assert "active_status=all" in run_input["urls"][0]["url"]
    assert "active_status=active" not in run_input["urls"][0]["url"]


def test_scrape_ads_with_raw_active_status_default_preserves_existing_url(monkeypatch):
    capture = _patch_client(monkeypatch, [])
    scrape.scrape_ads_with_raw("Brand", page_id="123456789")
    run_input = capture[0]
    assert "active_status=active" in run_input["urls"][0]["url"]


def test_scrape_ads_with_raw_date_window_reaches_the_page_url(monkeypatch):
    capture = _patch_client(monkeypatch, [])
    scrape.scrape_ads_with_raw("Brand", page_id="123456789",
                                start_date_min="2026-07-27", start_date_max="2026-08-05")
    url = capture[0]["urls"][0]["url"]
    assert "start_date%5Bmin%5D=2026-07-27" in url
    assert "start_date%5Bmax%5D=2026-08-05" in url


def test_scrape_ads_with_raw_omits_date_window_from_url_when_not_given(monkeypatch):
    capture = _patch_client(monkeypatch, [])
    scrape.scrape_ads_with_raw("Brand", page_id="123456789")
    assert "start_date%5B" not in capture[0]["urls"][0]["url"]


def test_scrape_ads_threads_date_window_and_active_status_too(monkeypatch):
    """scrape_ads (run_once's own path) must accept and forward the same
    params, not just scrape_ads_with_raw."""
    capture = _patch_client(monkeypatch, [])
    scrape.scrape_ads("Bangn Body", start_date_min="2026-01-01", active_status="all")
    run_input = capture[0]
    assert run_input["startDateMin"] == "2026-01-01"
    assert run_input["activeStatus"] == "all"


# ---- 2026-08-12 fetch-hang fix: start()+watch replaces the old blocking .call(),
# which had no timeout and no visibility into the dataset while waiting (the
# incident: Crepe Erase's run reached 42/50 image ads then produced zero further
# output for the rest of its own 565s run before self-terminating). ----

class _FakeClock:
    """Deterministic stand-in for time.monotonic/time.sleep - advances only when
    the code under test calls sleep(), so stagnation/hard-ceiling logic can be
    exercised without any real wall-clock wait."""
    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.t

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.t += seconds


def _patch_clock(monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr(scrape.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(scrape.time, "sleep", clock.sleep)
    return clock


def test_scrape_ads_with_raw_uses_start_not_call(monkeypatch):
    """The old blocking call() must be gone from this path entirely - start() plus
    our own watch loop is what replaces it."""
    _patch_client(monkeypatch, RAW_ITEMS)
    triples = scrape.scrape_ads_with_raw("Bangn Body")
    assert [mapped["ad_id"] for raw, mapped, reason in triples if mapped] == ["A1"]


def test_watch_actor_run_returns_immediately_on_already_terminal_run(monkeypatch):
    """A run that's already SUCCEEDED by the first status check must return with
    zero sleeps - never wait a full poll interval just to notice that."""
    clock = _patch_clock(monkeypatch)
    run = _FakeRun(status="SUCCEEDED")
    client = _FakeApifyClient([], [], run=run)
    scrape._watch_actor_run(client, run.id, run.default_dataset_id)
    assert clock.sleeps == []


def test_watch_actor_run_aborts_on_stagnation(monkeypatch):
    """Dataset item count never grows while the run stays RUNNING - must abort once
    STAGNATION_TIMEOUT_SECONDS of no growth has elapsed, not wait for the (much
    longer) hard ceiling or a terminal status that never arrives."""
    clock = _patch_clock(monkeypatch)
    run = _FakeRun(status="RUNNING")
    abort_capture = []
    client = _FakeApifyClient([{"x": 1}] * 42, [], run=run, abort_capture=abort_capture)
    scrape._watch_actor_run(client, run.id, run.default_dataset_id)
    assert abort_capture == [True]  # aborted gracefully
    assert clock.t >= scrape.STAGNATION_TIMEOUT_SECONDS
    assert clock.t < scrape.ACTOR_POLL_HARD_CEILING_SECONDS  # stagnation fired first


def test_watch_actor_run_aborts_on_hard_ceiling_when_still_growing(monkeypatch):
    """Item count keeps growing every poll (never stagnant) but the run never
    reaches a terminal status - the absolute ceiling must still fire, or a run
    that trickles one item per poll forever would never be bounded."""
    clock = _patch_clock(monkeypatch)
    run = _FakeRun(status="RUNNING")
    items = [{"x": 1}]
    abort_capture = []

    class _GrowingDataset(_FakeDataset):
        def get(self):
            items.append({"x": len(items)})
            self.item_count = len(items)
            return self

    class _GrowingClient(_FakeApifyClient):
        def dataset(self, dataset_id):
            return _GrowingDataset(items)

    client = _GrowingClient(items, [], run=run, abort_capture=abort_capture)
    scrape._watch_actor_run(client, run.id, run.default_dataset_id)
    assert abort_capture == [True]
    assert clock.t >= scrape.ACTOR_POLL_HARD_CEILING_SECONDS


def test_watch_actor_run_stops_watching_once_terminal_even_if_never_stagnant(monkeypatch):
    """A run that keeps growing and then finishes normally must return via the
    terminal-status branch, not linger until some unrelated ceiling."""
    clock = _patch_clock(monkeypatch)

    class _FlipRun:
        def __init__(self):
            self.id = "run1"
            self.default_dataset_id = "ds1"
            self.calls = 0

        @property
        def status(self):
            self.calls += 1
            return "RUNNING" if self.calls < 3 else "SUCCEEDED"

    run = _FlipRun()
    client = _FakeApifyClient([{"x": 1}], [], run=run)
    scrape._watch_actor_run(client, run.id, run.default_dataset_id)
    assert run.calls == 3
    assert clock.t < scrape.STAGNATION_TIMEOUT_SECONDS


def test_run_actor_and_get_dataset_starts_fresh_when_no_existing_run_id(monkeypatch):
    capture = []
    run = _FakeRun(run_id="new_run", dataset_id="new_ds", status="SUCCEEDED")
    client = _FakeApifyClient([], capture, run=run)
    started = []
    dataset_id = scrape._run_actor_and_get_dataset(
        client, "actor1", {"maxAds": 50}, on_run_started=lambda rid, did: started.append((rid, did)),
    )
    assert dataset_id == "new_ds"
    assert capture == [{"maxAds": 50}]  # start() was called with our run_input
    assert started == [("new_run", "new_ds")]


def test_run_actor_and_get_dataset_adopts_active_existing_run_instead_of_starting(monkeypatch):
    """The duplicate-run guard: if a prior attempt's run_id is still genuinely
    RUNNING on Apify, do not start a second, real, billed run for the same
    competitor - adopt and watch the existing one instead. The run is left RUNNING
    for the whole test (clock patched so the resulting stagnation wait is instant),
    since this test cares about start() never being called, not about how the
    watch loop eventually ends."""
    clock = _patch_clock(monkeypatch)
    capture = []
    run = _FakeRun(run_id="old_run", dataset_id="old_ds", status="RUNNING")
    client = _FakeApifyClient([], capture, run=run, abort_capture=[])
    started = []
    dataset_id = scrape._run_actor_and_get_dataset(
        client, "actor1", {"maxAds": 50}, on_run_started=lambda rid, did: started.append((rid, did)),
        existing_run_id="old_run",
    )
    assert dataset_id == "old_ds"
    assert capture == []  # start() was NEVER called - no duplicate run
    assert started == [("old_run", "old_ds")]


def test_run_actor_and_get_dataset_starts_fresh_when_existing_run_already_finished(monkeypatch):
    """A persisted run_id whose run has since reached a terminal state (finished
    unattended after the prior process died) is not "still active" - a fresh run_id
    passed as existing_run_id but already SUCCEEDED must fall through to reading
    its own dataset, not silently loop forever waiting on a run that's already
    done. on_run_started must NOT fire again for a run this function didn't start
    or actively adopt-and-watch."""
    capture = []
    run = _FakeRun(run_id="finished_run", dataset_id="finished_ds", status="SUCCEEDED")
    client = _FakeApifyClient([], capture, run=run)
    started = []
    dataset_id = scrape._run_actor_and_get_dataset(
        client, "actor1", {"maxAds": 50}, on_run_started=lambda rid, did: started.append((rid, did)),
        existing_run_id="finished_run",
    )
    assert dataset_id == "finished_ds"
    assert capture == []
    assert started == []


def test_get_run_status_returns_status_and_dataset(monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "fake-token")
    run = _FakeRun(run_id="r1", dataset_id="d1", status="RUNNING")
    monkeypatch.setattr(scrape, "ApifyClient", lambda token: _FakeApifyClient([], [], run=run))
    result = scrape.get_run_status("r1")
    assert result == {"status": "RUNNING", "dataset_id": "d1"}


def test_get_run_status_returns_none_without_token(monkeypatch):
    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    assert scrape.get_run_status("r1") is None


def test_get_run_status_returns_none_without_run_id(monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "fake-token")
    assert scrape.get_run_status(None) is None


def test_get_run_status_never_raises_on_lookup_failure(monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "fake-token")

    class _RaisingClient:
        def run(self, run_id):
            raise RuntimeError("network error")

    monkeypatch.setattr(scrape, "ApifyClient", lambda token: _RaisingClient())
    assert scrape.get_run_status("r1") is None
