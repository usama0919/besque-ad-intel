"""Tests for Chunk 6, Part A (cosmetic pre-deploy fixes):
1. GET /api/pool/cards suppresses fields carrying an unrendered {{...}} Meta
   template token (DCO ads store the template, not the resolved copy).
2. Competitor page_id validity is a display-only check - GET /api/competitors
   itself must never rewrite the stored value; the rule is mirrored here in
   Python since there's no browser tool in this environment to execute the
   actual client-side JS (pool.html's isValidPageId()).
3. Default competitor selection - could not be reproduced as a live bug in the
   current code (selectedCompetitorId already starts null with no auto-select
   path), so this is a source-level regression guard rather than a fix.

Real DB rows (uuid-suffixed, cleaned up in finally). No Apify/Gemini/Claude
involved."""
import re
import uuid
import dashboard
from fastapi.testclient import TestClient
from src import dedupe


def _make_competitor(page_id="999999"):
    dedupe.init_competitors()
    name = f"__test_{uuid.uuid4().hex[:8]}__"
    return dedupe.add_competitor(name, page_id, "")


def _cleanup(competitor_id):
    with dedupe.get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM scraped_ads WHERE competitor_id=%s", (competitor_id,))
        conn.commit()
    dedupe.delete_competitor(competitor_id)


# ---- Item 1: {{...}} template token suppression ----

def test_api_pool_cards_suppresses_templated_creative_body_and_title():
    dedupe.init_scraped_ads()
    cid = _make_competitor()
    ad_id = f"TOK_{uuid.uuid4().hex[:8]}"
    raw = {
        "ad_archive_id": ad_id,
        "ad_creative_bodies": ["Try {{product.name}} today!", "This one is real copy"],
        "ad_creative_link_titles": ["{{product.brand}} Sale"],
        "cta_text": "Shop {{product.name}}",
    }
    dedupe.upsert_scraped_ad(ad_id=ad_id, competitor_id=cid, image_url="http://x/1.jpg", raw_meta=raw)
    try:
        client = TestClient(dashboard.app)
        r = client.get(f"/api/pool/cards?competitor_id={cid}")
        card = r.json()["cards"][0]
        # the templated body is dropped, the real one survives alongside it
        assert card["ad_creative_bodies"] == ["This one is real copy"]
        # the ONLY title was templated -> empty list, not a leaked token
        assert card["ad_creative_link_titles"] == []
        # a single-string field with a token -> None, not the raw token
        assert card["cta_text"] is None
        for body in card["ad_creative_bodies"]:
            assert "{{" not in body
    finally:
        _cleanup(cid)


def test_api_pool_cards_never_touches_untemplated_fields():
    dedupe.init_scraped_ads()
    cid = _make_competitor()
    ad_id = f"TOK_{uuid.uuid4().hex[:8]}"
    raw = {
        "ad_archive_id": ad_id,
        "ad_creative_bodies": ["Perfectly normal ad copy"],
        "ad_creative_link_titles": ["Shop Now"],
        "cta_text": "Shop Now",
    }
    dedupe.upsert_scraped_ad(ad_id=ad_id, competitor_id=cid, image_url="http://x/1.jpg", raw_meta=raw)
    try:
        client = TestClient(dashboard.app)
        r = client.get(f"/api/pool/cards?competitor_id={cid}")
        card = r.json()["cards"][0]
        assert card["ad_creative_bodies"] == ["Perfectly normal ad copy"]
        assert card["ad_creative_link_titles"] == ["Shop Now"]
        assert card["cta_text"] == "Shop Now"
    finally:
        _cleanup(cid)


def test_suppress_templated_never_attempts_to_resolve_the_token():
    """The suppression detector must drop the slot, never try to fill in the
    token - the resolved copy isn't in the data anywhere to recover."""
    assert dashboard._has_unrendered_template_token("Try {{product.name}} today") is True
    assert dashboard._has_unrendered_template_token("Try our oil today") is False
    assert dashboard._has_unrendered_template_token(None) is False
    assert dashboard._has_unrendered_template_token(123) is False
    assert dashboard._suppress_templated("{{product.brand}} Sale") is None
    assert dashboard._suppress_templated("Real Sale") == "Real Sale"
    assert dashboard._suppress_templated(["{{x}}", "real", "{{y}}"]) == ["real"]
    assert dashboard._suppress_templated([]) == []


# ---- Item 2: page_id validity is display-only ----

def _is_valid_page_id(page_id):
    """Python mirror of pool.html's isValidPageId() JS function - a real
    Facebook page_id is purely numeric. Kept here as the single source of the
    RULE for this test file; the actual enforcement is client-side JS, which
    this environment has no browser tool to execute directly."""
    return bool(re.fullmatch(r"\d+", str(page_id or "").strip()))


def test_page_id_validity_rule_matches_real_and_broken_examples():
    assert _is_valid_page_id("125531750889677") is True  # a real L'Occitane page_id
    assert _is_valid_page_id("CeraVe") is False  # a real observed case: name in page_id
    assert _is_valid_page_id("") is False
    assert _is_valid_page_id(None) is False
    assert _is_valid_page_id("12345abc") is False


def test_api_competitors_returns_page_id_unmodified_for_display_only_check():
    """Item 2 is display-only - GET /api/competitors must never rewrite page_id
    to "fix" or flag it server-side; the check happens purely client-side
    against the real stored value, so the API contract is untouched."""
    cid = _make_competitor(page_id="NotANumericPageId")
    try:
        client = TestClient(dashboard.app)
        r = client.get("/api/competitors")
        row = next(c for c in r.json() if c["id"] == cid)
        assert row["page_id"] == "NotANumericPageId"
        assert _is_valid_page_id(row["page_id"]) is False
    finally:
        dedupe.delete_competitor(cid)


def test_pool_html_defines_the_page_id_validity_check_client_side():
    """The badge/check must exist in the shipped template - a source-level
    presence check, since there's no browser to click through and observe it."""
    html = open("templates/pool.html", encoding="utf-8").read()
    assert "isValidPageId" in html
    assert "invalid-page-id" in html or "invalid page_id" in html


# ---- Item 3: default competitor selection starts at none ----

def test_pool_html_competitor_selection_defaults_to_none():
    """Could not reproduce "lands on the first row by id" in this code as
    written - selectedCompetitorId already starts null and loadCompetitors()
    never assigns it a real id as a side effect, only selectCompetitor() (an
    explicit click) does. This locks that invariant in as a regression guard."""
    html = open("templates/pool.html", encoding="utf-8").read()
    assert "let selectedCompetitorId = null;" in html

    m = re.search(r"async function loadCompetitors\(\)\s*\{(.*?)\n\}", html, re.S)
    assert m is not None, "loadCompetitors() not found in pool.html"
    body = m.group(1)
    # only comparisons (===) are allowed in the render function - no assignment
    assert re.search(r"selectedCompetitorId\s*=(?!==)", body) is None, (
        "loadCompetitors() must never assign selectedCompetitorId itself - "
        "only an explicit click (selectCompetitor) may"
    )
