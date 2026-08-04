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
