"""Tests for GET /api/pool/cards (Chunk 3, Part 1) - the flattened,
judgeable-fields-only view of the pool for the browse-and-pick grid. Real DB rows
(uuid-suffixed, cleaned up in finally). No Apify involved - rows are seeded
directly via dedupe.upsert_scraped_ad."""
import uuid
from datetime import datetime, timedelta, timezone
import dashboard
from fastapi.testclient import TestClient
from src import dedupe


def _make_competitor():
    dedupe.init_competitors()
    name = f"__test_{uuid.uuid4().hex[:8]}__"
    return dedupe.add_competitor(name, "999999", "")


def _cleanup(competitor_id):
    with dedupe.get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM scraped_ads WHERE competitor_id=%s", (competitor_id,))
        conn.commit()
    dedupe.delete_competitor(competitor_id)


def _iso(dt):
    return dt.isoformat()


def test_api_pool_cards_flattens_only_judgeable_fields_never_ships_raw_meta():
    dedupe.init_scraped_ads()
    cid = _make_competitor()
    ad_id = f"CARD_{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)
    raw = {
        "ad_archive_id": ad_id,
        "is_active": True,
        "ad_delivery_start_time": _iso(now - timedelta(days=5)),
        "ad_delivery_stop_time": None,
        "ad_creative_bodies": ["Buy now!"],
        "ad_creative_link_titles": ["Shop the sale"],
        "cta_text": "Shop Now",
        "page_name": "Brand X",
        # Deliberately NOT in the flattened schema - must never reach the browser
        "estimated_audience_size": {"lower_bound": "1000"},
        "spend": {"lower_bound": "50"},
        "impressions": {"lower_bound": "9000"},
    }
    dedupe.upsert_scraped_ad(ad_id=ad_id, competitor_id=cid, image_url="http://x/1.jpg",
                              raw_meta=raw, media_type="IMAGE")
    try:
        client = TestClient(dashboard.app)
        r = client.get(f"/api/pool/cards?competitor_id={cid}")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        card = body["cards"][0]
        assert set(card.keys()) == {
            "ad_id", "image_url", "media_type", "is_active", "days_running",
            "ad_delivery_start_time", "ad_delivery_stop_time", "ad_creative_bodies",
            "ad_creative_link_titles", "cta_text", "page_name", "fetched_at",
            "already_generated",  # Chunk 5, Item 3
        }
        assert "raw_meta" not in card
        assert "estimated_audience_size" not in card
        assert "spend" not in card
        assert "impressions" not in card
        assert card["ad_id"] == ad_id
        assert card["media_type"] == "IMAGE"
        assert card["is_active"] is True
        assert card["days_running"] == 5
        assert card["page_name"] == "Brand X"
        assert card["cta_text"] == "Shop Now"
        assert card["ad_creative_bodies"] == ["Buy now!"]
        assert card["ad_creative_link_titles"] == ["Shop the sale"]
    finally:
        _cleanup(cid)


def test_api_pool_cards_days_running_active_with_future_stop_time_is_capped_at_now():
    """A scheduled FUTURE stop_time must not inflate days_running by counting days
    that haven't happened yet - the min(now, stop_time) guard."""
    dedupe.init_scraped_ads()
    cid = _make_competitor()
    ad_id = f"CARD_{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)
    raw = {
        "ad_delivery_start_time": _iso(now - timedelta(days=10)),
        "ad_delivery_stop_time": _iso(now + timedelta(days=30)),
        "is_active": True,
    }
    dedupe.upsert_scraped_ad(ad_id=ad_id, competitor_id=cid, image_url="http://x/1.jpg", raw_meta=raw)
    try:
        client = TestClient(dashboard.app)
        r = client.get(f"/api/pool/cards?competitor_id={cid}")
        card = r.json()["cards"][0]
        assert card["days_running"] == 10  # NOT 40
    finally:
        _cleanup(cid)


def test_api_pool_cards_days_running_for_a_stopped_ad_uses_its_own_stop_time():
    """A stop_time in the PAST is used as-is (not capped, since it's already <= now)."""
    dedupe.init_scraped_ads()
    cid = _make_competitor()
    ad_id = f"CARD_{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)
    raw = {
        "ad_delivery_start_time": _iso(now - timedelta(days=20)),
        "ad_delivery_stop_time": _iso(now - timedelta(days=5)),
        "is_active": False,
    }
    dedupe.upsert_scraped_ad(ad_id=ad_id, competitor_id=cid, image_url="http://x/1.jpg", raw_meta=raw)
    try:
        client = TestClient(dashboard.app)
        r = client.get(f"/api/pool/cards?competitor_id={cid}")
        card = r.json()["cards"][0]
        assert card["days_running"] == 15
        assert card["is_active"] is False
    finally:
        _cleanup(cid)


def test_api_pool_cards_missing_start_time_is_none_never_zero_and_sorts_last():
    dedupe.init_scraped_ads()
    cid = _make_competitor()
    ad_with_start = f"CARD_{uuid.uuid4().hex[:8]}"
    ad_without_start = f"CARD_{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)
    dedupe.upsert_scraped_ad(ad_id=ad_with_start, competitor_id=cid, image_url="http://x/1.jpg",
                              raw_meta={"ad_delivery_start_time": _iso(now - timedelta(days=3))})
    dedupe.upsert_scraped_ad(ad_id=ad_without_start, competitor_id=cid, image_url="http://x/2.jpg",
                              raw_meta={"ad_delivery_start_time": ""})
    try:
        client = TestClient(dashboard.app)
        r = client.get(f"/api/pool/cards?competitor_id={cid}")
        cards = r.json()["cards"]
        by_id = {c["ad_id"]: c for c in cards}
        assert by_id[ad_without_start]["days_running"] is None
        ids_in_order = [c["ad_id"] for c in cards]
        assert ids_in_order.index(ad_with_start) < ids_in_order.index(ad_without_start)
    finally:
        _cleanup(cid)


def test_api_pool_cards_sorts_by_days_running_descending():
    dedupe.init_scraped_ads()
    cid = _make_competitor()
    now = datetime.now(timezone.utc)
    ads = {
        "short": (f"CARD_{uuid.uuid4().hex[:8]}", 2),
        "long": (f"CARD_{uuid.uuid4().hex[:8]}", 40),
        "mid": (f"CARD_{uuid.uuid4().hex[:8]}", 15),
    }
    for ad_id, days in ads.values():
        dedupe.upsert_scraped_ad(ad_id=ad_id, competitor_id=cid, image_url="http://x/1.jpg",
                                  raw_meta={"ad_delivery_start_time": _iso(now - timedelta(days=days))})
    try:
        client = TestClient(dashboard.app)
        r = client.get(f"/api/pool/cards?competitor_id={cid}")
        cards = r.json()["cards"]
        got_order = [c["days_running"] for c in cards]
        assert got_order == sorted(got_order, reverse=True)
        assert cards[0]["ad_id"] == ads["long"][0]
        assert cards[-1]["ad_id"] == ads["short"][0]
    finally:
        _cleanup(cid)


def test_api_pool_cards_limit_default_200_and_explicit_limit_applies_after_sort():
    dedupe.init_scraped_ads()
    cid = _make_competitor()
    now = datetime.now(timezone.utc)
    ad_days = []
    for days in (5, 50, 20):
        ad_id = f"CARD_{uuid.uuid4().hex[:8]}"
        ad_days.append((ad_id, days))
        dedupe.upsert_scraped_ad(ad_id=ad_id, competitor_id=cid, image_url="http://x/1.jpg",
                                  raw_meta={"ad_delivery_start_time": _iso(now - timedelta(days=days))})
    try:
        client = TestClient(dashboard.app)
        r_default = client.get(f"/api/pool/cards?competitor_id={cid}")
        assert r_default.json()["limit"] == 200
        assert len(r_default.json()["cards"]) == 3

        r_limited = client.get(f"/api/pool/cards?competitor_id={cid}&limit=2")
        body = r_limited.json()
        assert body["total"] == 3  # total reflects the full pool, not the truncated page
        assert len(body["cards"]) == 2
        # limit applied AFTER sorting - must be the two HIGHEST days_running, not
        # whichever two rows the database happened to return first
        top_two_days = sorted([d for _, d in ad_days], reverse=True)[:2]
        assert [c["days_running"] for c in body["cards"]] == top_two_days
    finally:
        _cleanup(cid)


def test_api_pool_cards_empty_for_unknown_competitor():
    client = TestClient(dashboard.app)
    r = client.get("/api/pool/cards?competitor_id=-999")
    assert r.status_code == 200
    assert r.json() == {"total": 0, "limit": 200, "cards": []}
