"""Tests for src/scrape.py's shared _scrape_raw core - scrape_ads and
scrape_ads_with_raw must apply the identical image-only/page-match filter, since
pipeline.fetch_pool's counts depend on that never drifting apart from run_once's
own scrape_ads path. No real Apify call: ApifyClient is monkeypatched."""
import os
from src import scrape


class _FakeRun:
    default_dataset_id = "ds1"


class _FakeDataset:
    def __init__(self, items):
        self._items = items

    def iterate_items(self):
        return iter(self._items)


class _FakeActorHandle:
    def __init__(self, run_input_capture):
        self._capture = run_input_capture

    def call(self, run_input=None):
        self._capture.append(run_input)
        return _FakeRun()


class _FakeApifyClient:
    def __init__(self, items, run_input_capture):
        self._items = items
        self._capture = run_input_capture

    def actor(self, actor_id):
        return _FakeActorHandle(self._capture)

    def dataset(self, dataset_id):
        return _FakeDataset(self._items)


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


def test_scrape_ads_threads_date_window_and_active_status_too(monkeypatch):
    """scrape_ads (run_once's own path) must accept and forward the same
    params, not just scrape_ads_with_raw."""
    capture = _patch_client(monkeypatch, [])
    scrape.scrape_ads("Bangn Body", start_date_min="2026-01-01", active_status="all")
    run_input = capture[0]
    assert run_input["startDateMin"] == "2026-01-01"
    assert run_input["activeStatus"] == "all"
