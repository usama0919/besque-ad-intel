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


def test_fetch_pool_stores_survivors_and_reports_counts(monkeypatch):
    cid = _make_competitor()
    ad_id_1 = f"FP_{uuid.uuid4().hex[:8]}"
    ad_id_2 = f"FP_{uuid.uuid4().hex[:8]}"
    pairs = [
        ({"ad_archive_id": ad_id_1, "impressions": {"lower_bound": "100"}},
         {"ad_id": ad_id_1, "image_url": "http://x/1.jpg", "page_name": "brand"}),
        ({"ad_archive_id": "rejected"}, None),  # filtered out (e.g. page mismatch)
        ({"ad_archive_id": ad_id_2}, {"ad_id": ad_id_2, "image_url": "http://x/2.jpg", "page_name": "brand"}),
    ]
    monkeypatch.setattr(scrape, "scrape_ads_with_raw", lambda name, max_results=None, page_id=None: pairs)
    try:
        result = pipeline.fetch_pool(cid, cap=10)
        assert result == {"fetched": 3, "stored": 2, "skipped": 1}
        rows = {r["ad_id"]: r for r in dedupe.get_scraped_ads(competitor_id=cid)}
        assert set(rows) == {ad_id_1, ad_id_2}
        assert rows[ad_id_1]["image_url"] == "http://x/1.jpg"
        assert rows[ad_id_1]["raw_meta"]["impressions"] == {"lower_bound": "100"}
        assert rows[ad_id_1]["status"] == "pool"
        assert rows[ad_id_1]["gcs_path"] is None
    finally:
        _cleanup(cid)


def test_fetch_pool_upsert_refreshes_raw_meta_without_duplicating_row(monkeypatch):
    cid = _make_competitor()
    ad_id = f"FP_{uuid.uuid4().hex[:8]}"
    first_pairs = [({"ad_archive_id": ad_id, "impressions": {"lower_bound": "100"}},
                    {"ad_id": ad_id, "image_url": "http://x/1.jpg", "page_name": "brand"})]
    second_pairs = [({"ad_archive_id": ad_id, "impressions": {"lower_bound": "200"}},
                     {"ad_id": ad_id, "image_url": "http://x/1.jpg", "page_name": "brand"})]
    try:
        monkeypatch.setattr(scrape, "scrape_ads_with_raw", lambda name, max_results=None, page_id=None: first_pairs)
        pipeline.fetch_pool(cid, cap=10)
        monkeypatch.setattr(scrape, "scrape_ads_with_raw", lambda name, max_results=None, page_id=None: second_pairs)
        pipeline.fetch_pool(cid, cap=10)
        rows = dedupe.get_scraped_ads(competitor_id=cid)
        assert len(rows) == 1
        assert rows[0]["raw_meta"]["impressions"] == {"lower_bound": "200"}
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
    pairs = [({"ad_archive_id": ad_id}, {"ad_id": ad_id, "image_url": "http://x/1.jpg", "page_name": "brand"})]
    monkeypatch.setattr(scrape, "scrape_ads_with_raw", lambda name, max_results=None, page_id=None: pairs)
    try:
        pipeline.fetch_pool(cid, cap=10)
        assert dedupe.is_new(ad_id) is True
    finally:
        _cleanup(cid)
