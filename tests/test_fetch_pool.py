"""Tests for pipeline.fetch_pool - fetch-and-store ONLY, no deconstruct/generate,
never touches seen_ads/artifacts. Real DB connection (scraped_ads + a throwaway
competitor row), uuid-suffixed, cleaned up in finally - same pattern as
test_dedupe_angles.py / test_dedupe_products.py."""
import uuid
from src import pipeline, dedupe, scrape


def _make_competitor(**kw):
    dedupe.init_competitors()
    name = f"__test_{uuid.uuid4().hex[:8]}__"
    return dedupe.add_competitor(name, kw.get("page_id", "999999"), kw.get("category", ""))


def _cleanup(competitor_id):
    with dedupe.get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM scraped_ads WHERE competitor_id=%s", (competitor_id,))
        conn.commit()
    dedupe.delete_competitor(competitor_id)


def test_fetch_pool_stores_survivors_and_reports_per_reason_skipped(monkeypatch):
    cid = _make_competitor()
    ad_id_1 = f"FP_{uuid.uuid4().hex[:8]}"
    ad_id_2 = f"FP_{uuid.uuid4().hex[:8]}"
    triples = [
        ({"ad_archive_id": ad_id_1, "impressions": {"lower_bound": "100"}},
         {"ad_id": ad_id_1, "image_url": "http://x/1.jpg", "page_name": "brand"}, None),
        ({"ad_archive_id": "rejected1"}, None, "wrong_page"),
        ({"ad_archive_id": "rejected2"}, None, "not_image"),
        ({"ad_archive_id": ad_id_2}, {"ad_id": ad_id_2, "image_url": "http://x/2.jpg", "page_name": "brand"}, None),
    ]
    monkeypatch.setattr(scrape, "scrape_ads_with_raw", lambda name, max_results=None, page_id=None: triples)
    try:
        result = pipeline.fetch_pool(cid, cap=10)
        assert result == {
            "fetched": 4, "stored": 2,
            "skipped": {"not_image": 1, "wrong_page": 1, "no_image_url": 0, "duplicate": 0},
        }
        rows = {r["ad_id"]: r for r in dedupe.get_scraped_ads(competitor_id=cid)}
        assert set(rows) == {ad_id_1, ad_id_2}
        assert rows[ad_id_1]["image_url"] == "http://x/1.jpg"
        assert rows[ad_id_1]["raw_meta"]["impressions"] == {"lower_bound": "100"}
        assert rows[ad_id_1]["status"] == "pool"
        assert rows[ad_id_1]["gcs_path"] is None
    finally:
        _cleanup(cid)


def test_fetch_pool_counts_and_skips_a_within_pull_duplicate(monkeypatch):
    """The same ad_id appearing twice in one Apify pull (e.g. pagination overlap)
    must be counted under skipped.duplicate and stored exactly once, never twice."""
    cid = _make_competitor()
    ad_id = f"FP_{uuid.uuid4().hex[:8]}"
    triples = [
        ({"ad_archive_id": ad_id, "impressions": {"lower_bound": "100"}},
         {"ad_id": ad_id, "image_url": "http://x/1.jpg", "page_name": "brand"}, None),
        ({"ad_archive_id": ad_id, "impressions": {"lower_bound": "999"}},
         {"ad_id": ad_id, "image_url": "http://x/1.jpg", "page_name": "brand"}, None),
    ]
    monkeypatch.setattr(scrape, "scrape_ads_with_raw", lambda name, max_results=None, page_id=None: triples)
    try:
        result = pipeline.fetch_pool(cid, cap=10)
        assert result == {
            "fetched": 2, "stored": 1,
            "skipped": {"not_image": 0, "wrong_page": 0, "no_image_url": 0, "duplicate": 1},
        }
        rows = dedupe.get_scraped_ads(competitor_id=cid)
        assert len(rows) == 1
    finally:
        _cleanup(cid)


def test_fetch_pool_upsert_refreshes_raw_meta_without_duplicating_row(monkeypatch):
    cid = _make_competitor()
    ad_id = f"FP_{uuid.uuid4().hex[:8]}"
    first_triples = [({"ad_archive_id": ad_id, "impressions": {"lower_bound": "100"}},
                       {"ad_id": ad_id, "image_url": "http://x/1.jpg", "page_name": "brand"}, None)]
    second_triples = [({"ad_archive_id": ad_id, "impressions": {"lower_bound": "200"}},
                        {"ad_id": ad_id, "image_url": "http://x/1.jpg", "page_name": "brand"}, None)]
    try:
        monkeypatch.setattr(scrape, "scrape_ads_with_raw", lambda name, max_results=None, page_id=None: first_triples)
        pipeline.fetch_pool(cid, cap=10)
        monkeypatch.setattr(scrape, "scrape_ads_with_raw", lambda name, max_results=None, page_id=None: second_triples)
        pipeline.fetch_pool(cid, cap=10)
        rows = dedupe.get_scraped_ads(competitor_id=cid)
        assert len(rows) == 1
        assert rows[0]["raw_meta"]["impressions"] == {"lower_bound": "200"}
    finally:
        _cleanup(cid)


def test_fetch_pool_preserves_real_media_type_and_stores_one_row_per_multi_image_ad(monkeypatch):
    """Chunk 2C: the grid needs the REAL media_type on the row (not normalised to
    IMAGE), and a DCO/CAROUSEL record with several images must still produce
    exactly one scraped_ads row - first image as image_url, the rest untouched in
    raw_meta - never a row per variant (the unique index is (ad_id,
    competitor_id), changing it is out of scope)."""
    cid = _make_competitor()
    ad_id = f"FP_{uuid.uuid4().hex[:8]}"
    raw = {"ad_archive_id": ad_id, "media_type": "DCO",
           "images": ["http://x/first.jpg", "http://x/second.jpg"]}
    mapped = {"ad_id": ad_id, "image_url": "http://x/first.jpg", "page_name": "brand", "media_type": "DCO"}
    triples = [(raw, mapped, None)]
    monkeypatch.setattr(scrape, "scrape_ads_with_raw", lambda name, max_results=None, page_id=None: triples)
    try:
        result = pipeline.fetch_pool(cid, cap=10)
        assert result["stored"] == 1
        rows = dedupe.get_scraped_ads(competitor_id=cid)
        assert len(rows) == 1
        row = rows[0]
        assert row["media_type"] == "DCO"
        assert row["image_url"] == "http://x/first.jpg"
        assert row["raw_meta"]["images"] == ["http://x/first.jpg", "http://x/second.jpg"]
    finally:
        _cleanup(cid)


def test_fetch_pool_unknown_competitor_raises():
    try:
        pipeline.fetch_pool(-1, cap=10)
        assert False, "expected ValueError for unknown competitor_id"
    except ValueError:
        pass


def test_fetch_pool_never_touches_seen_ads(monkeypatch):
    """fetch_pool populates the candidate pool BEFORE any dedup gate - it must never
    call dedupe.mark_seen or dedupe.is_new, or a later run_once would wrongly skip
    ads that were only ever fetched into the pool, not processed."""
    dedupe.init_db()
    cid = _make_competitor()
    ad_id = f"FP_{uuid.uuid4().hex[:8]}"
    triples = [({"ad_archive_id": ad_id}, {"ad_id": ad_id, "image_url": "http://x/1.jpg", "page_name": "brand"}, None)]
    monkeypatch.setattr(scrape, "scrape_ads_with_raw", lambda name, max_results=None, page_id=None: triples)
    try:
        pipeline.fetch_pool(cid, cap=10)
        assert dedupe.is_new(ad_id) is True
    finally:
        _cleanup(cid)
