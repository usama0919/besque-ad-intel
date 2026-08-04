"""Tests for GET /api/pool and POST /api/fetch (Chunk 2, Part B). Real DB rows
(uuid-suffixed, cleaned up in finally) - only src.scrape is mocked, since these
endpoints must never hit Apify for real. No deconstruct/generation involved."""
import uuid
import dashboard
from fastapi.testclient import TestClient
from src import dedupe, scrape


def _make_competitor():
    dedupe.init_competitors()
    name = f"__test_{uuid.uuid4().hex[:8]}__"
    return dedupe.add_competitor(name, "999999", "")


def _cleanup(competitor_id):
    with dedupe.get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM scraped_ads WHERE competitor_id=%s", (competitor_id,))
        conn.commit()
    dedupe.delete_competitor(competitor_id)


# ---- GET /api/pool ----

def test_api_pool_returns_rows_as_stored_with_total_count():
    dedupe.init_scraped_ads()
    cid = _make_competitor()
    ad_id = f"POOL_{uuid.uuid4().hex[:8]}"
    raw = {"ad_archive_id": ad_id, "impressions": {"lower_bound": "500"}, "spend": None}
    dedupe.upsert_scraped_ad(ad_id=ad_id, competitor_id=cid, image_url="http://x/1.jpg", raw_meta=raw)
    try:
        client = TestClient(dashboard.app)
        r = client.get(f"/api/pool?competitor_id={cid}")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        assert body["limit"] == 100  # explicit default, not get_artifacts_full's 50
        row = body["rows"][0]
        assert row["ad_id"] == ad_id
        assert row["competitor_id"] == cid
        assert row["image_url"] == "http://x/1.jpg"
        # dumb passthrough - raw_meta must be the ENTIRE unmodified record, not a
        # derived subset of "card fields"
        assert row["raw_meta"] == raw
        assert row["status"] == "pool"
        assert isinstance(row["fetched_at"], str) and row["fetched_at"]
    finally:
        _cleanup(cid)


def test_api_pool_defaults_to_status_pool():
    dedupe.init_scraped_ads()
    cid = _make_competitor()
    ad_id = f"POOL_{uuid.uuid4().hex[:8]}"
    dedupe.upsert_scraped_ad(ad_id=ad_id, competitor_id=cid, image_url="http://x/1.jpg", raw_meta={})
    with dedupe.get_conn() as conn, conn.cursor() as cur:
        cur.execute("UPDATE scraped_ads SET status='promoted' WHERE ad_id=%s", (ad_id,))
        conn.commit()
    try:
        client = TestClient(dashboard.app)
        r = client.get(f"/api/pool?competitor_id={cid}")
        assert r.json()["total"] == 0  # default status filter excludes the 'promoted' row
        r2 = client.get(f"/api/pool?competitor_id={cid}&status=promoted")
        assert r2.json()["total"] == 1
    finally:
        _cleanup(cid)


def test_api_pool_pagination_limit_and_offset():
    dedupe.init_scraped_ads()
    cid = _make_competitor()
    ad_ids = [f"POOL_{uuid.uuid4().hex[:8]}" for _ in range(3)]
    for aid in ad_ids:
        dedupe.upsert_scraped_ad(ad_id=aid, competitor_id=cid, image_url="http://x/x.jpg", raw_meta={})
    try:
        client = TestClient(dashboard.app)
        r = client.get(f"/api/pool?competitor_id={cid}&limit=2&offset=0")
        body = r.json()
        assert body["total"] == 3
        assert len(body["rows"]) == 2
        r2 = client.get(f"/api/pool?competitor_id={cid}&limit=2&offset=2")
        body2 = r2.json()
        assert body2["total"] == 3
        assert len(body2["rows"]) == 1
    finally:
        _cleanup(cid)


def test_api_pool_empty_for_unknown_competitor():
    client = TestClient(dashboard.app)
    r = client.get("/api/pool?competitor_id=-999")
    assert r.status_code == 200
    assert r.json() == {"total": 0, "limit": 100, "offset": 0, "rows": []}


# ---- POST /api/fetch ----

def test_api_fetch_pool_stores_rows_and_wraps_result_in_ok_envelope(monkeypatch):
    """Supersedes the earlier "verbatim" contract: success now uses the dashboard's
    normal {"ok": True, ...} envelope, with fetch_pool's dict nested under "result" -
    including its per-reason skipped breakdown, passed through unchanged."""
    cid = _make_competitor()
    ad_id = f"POOL_{uuid.uuid4().hex[:8]}"
    triples = [
        ({"ad_archive_id": ad_id, "impressions": None},
         {"ad_id": ad_id, "image_url": "http://x/1.jpg", "page_name": "brand"}, None),
        ({"ad_archive_id": "rejected"}, None, "wrong_page"),
    ]
    monkeypatch.setattr(scrape, "scrape_ads_with_raw", lambda name, max_results=None, page_id=None: triples)
    try:
        client = TestClient(dashboard.app)
        r = client.post("/api/fetch", json={"competitor_id": cid, "cap": 10})
        assert r.status_code == 200
        body = r.json()
        assert body == {
            "ok": True,
            "result": {
                "fetched": 2, "stored": 1,
                "skipped": {"not_image": 0, "wrong_page": 1, "no_image_url": 0, "duplicate": 0},
            },
        }
        pool_rows = dedupe.get_scraped_ads(competitor_id=cid)
        assert len(pool_rows) == 1
        assert pool_rows[0]["ad_id"] == ad_id
    finally:
        _cleanup(cid)


def test_api_fetch_pool_default_cap_is_50(monkeypatch):
    cid = _make_competitor()
    captured = {}

    def fake_scrape(name, max_results=None, page_id=None):
        captured["max_results"] = max_results
        return []

    monkeypatch.setattr(scrape, "scrape_ads_with_raw", fake_scrape)
    try:
        client = TestClient(dashboard.app)
        r = client.post("/api/fetch", json={"competitor_id": cid})
        assert r.status_code == 200
        assert captured["max_results"] == 50
    finally:
        _cleanup(cid)


def test_api_fetch_pool_404_for_unknown_competitor(monkeypatch):
    monkeypatch.setattr(scrape, "scrape_ads_with_raw", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not call Apify")))
    client = TestClient(dashboard.app)
    r = client.post("/api/fetch", json={"competitor_id": -999})
    assert r.status_code == 404
    assert r.json()["ok"] is False
    assert "not found" in r.json()["error"]


def test_api_fetch_pool_missing_competitor_id_is_400():
    client = TestClient(dashboard.app)
    r = client.post("/api/fetch", json={})
    assert r.status_code == 400


def test_api_fetch_pool_non_integer_cap_is_400():
    cid = _make_competitor()
    try:
        client = TestClient(dashboard.app)
        r = client.post("/api/fetch", json={"competitor_id": cid, "cap": "not-a-number"})
        assert r.status_code == 400
    finally:
        _cleanup(cid)


def test_api_fetch_pool_never_touches_seen_ads_or_artifacts(monkeypatch):
    """fetch_pool populates the candidate pool BEFORE any dedup gate - the endpoint
    wrapping it must not change that."""
    dedupe.init_db()
    cid = _make_competitor()
    ad_id = f"POOL_{uuid.uuid4().hex[:8]}"
    triples = [({"ad_archive_id": ad_id}, {"ad_id": ad_id, "image_url": "http://x/1.jpg", "page_name": "brand"}, None)]
    monkeypatch.setattr(scrape, "scrape_ads_with_raw", lambda name, max_results=None, page_id=None: triples)
    try:
        client = TestClient(dashboard.app)
        client.post("/api/fetch", json={"competitor_id": cid, "cap": 10})
        assert dedupe.is_new(ad_id) is True
        rows = dedupe.get_artifacts_full(limit=500)
        assert not any(r["ad_id"] == ad_id for r in rows)
    finally:
        _cleanup(cid)
