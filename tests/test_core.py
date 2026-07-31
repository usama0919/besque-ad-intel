"""Tests for the dedupe store and blueprint validator."""
import uuid
from src import dedupe, validator


def _valid_blueprint():
    return {
        "ad_id": "A1",
        "source_page": "TestPage",
        "captured_at": "2026-01-01T00:00:00Z",
        "format": "product_hero",
        "hook": {"type": "question", "headline_structure": "Q + benefit"},
        "awareness_stage": "problem",
        "claims": ["efficacy", "sensory"],
        "visual": {"layout": "centered", "subject": "bottle", "palette_mood": "warm", "text_placement": "top"},
        "cta": "Shop Now",
        "destination_url": "https://example.com",
    }


def test_dedupe_new_then_seen():
    dedupe.init_db()
    ad_id = f"TEST_{uuid.uuid4().hex[:8]}"
    assert dedupe.is_new(ad_id) is True
    dedupe.mark_seen(ad_id, "TestPage")
    assert dedupe.is_new(ad_id) is False


def test_dedupe_double_mark_is_safe():
    dedupe.init_db()
    ad_id = f"TEST_{uuid.uuid4().hex[:8]}"
    dedupe.mark_seen(ad_id, "TestPage")
    dedupe.mark_seen(ad_id, "TestPage")  # must not raise
    assert dedupe.is_new(ad_id) is False


def test_valid_blueprint_passes():
    assert validator.is_valid(_valid_blueprint()) is True


def test_missing_required_field_fails():
    bp = _valid_blueprint()
    del bp["cta"]
    assert validator.is_valid(bp) is False


def test_bad_enum_value_fails():
    bp = _valid_blueprint()
    bp["awareness_stage"] = "not_a_real_stage"
    assert validator.is_valid(bp) is False


def test_blueprint_without_new_creative_fields_still_valid():
    """_valid_blueprint() already has none of creative_objective/target_audience/
    typography/the expanded layout_detail fields - matches the shape of all 138 existing
    artifacts. They must keep validating after those fields were added to the schema."""
    assert validator.is_valid(_valid_blueprint()) is True


def test_blueprint_with_new_creative_fields_validates():
    """creative_objective/target_audience/typography/expanded layout_detail must validate
    when present - added purely to the schema, no validator.py code change needed since
    is_valid()/validation_error() are schema-driven (confirmed: no additionalProperties
    restriction anywhere blocks new optional fields)."""
    bp = _valid_blueprint()
    bp["creative_objective"] = "drive urgency around a limited-time offer"
    bp["target_audience"] = "women 40+ concerned about skin texture and firmness"
    bp["typography"] = {
        "headline_face": "serif",
        "headline_weight": "bold",
        "hierarchy_levels": ["large bold serif headline", "medium sans subhead", "small CTA label"],
        "case_treatment": "all caps headline, sentence case body",
    }
    bp["layout_detail"] = {
        "text_zone": "top third",
        "product_count": 1,
        "background_type": "gradient",
        "zone_positions": ["headline top-center", "product mid-frame", "CTA bottom-full-width"],
        "has_bottom_banner": True,
        "has_corner_badge": True,
        "frame_division": "three stacked horizontal bands",
    }
    assert validator.is_valid(bp) is True
    assert validator.validation_error(bp) is None


def test_production_styles_returns_canonical_list():
    styles = validator.production_styles()
    assert set(styles) == {"ugc_native", "high_spec_studio", "hybrid", "illustrated"}


def test_illustrated_production_style_validates():
    """glp1's seeded default_realism is "illustrated" - this is the prerequisite check
    that a blueprint carrying it doesn't fail schema validation."""
    bp = _valid_blueprint()
    bp["production_style"] = {"style": "illustrated", "confidence": "high", "signals": ["whiteboard diagram"]}
    assert validator.is_valid(bp) is True
def test_config_loads_competitors():
    from src import config_loader
    competitors = config_loader.get_competitors()
    assert isinstance(competitors, list)
    assert len(competitors) >= 1
    assert "name" in competitors[0]


def test_config_settings_present():
    from src import config_loader
    settings = config_loader.get_settings()
    assert settings.get("ads_type") == "static"
def test_record_and_get_decision():
    from src import dedupe
    import uuid
    dedupe.init_decisions()
    ad_id = f"DEC_{uuid.uuid4().hex[:8]}"
    dedupe.record_decision(ad_id, "approve")
    rows = dedupe.get_decisions(ad_id)
    assert len(rows) == 1
    assert rows[0][1] == "approve"


def test_invalid_decision_raises():
    from src import dedupe
    import pytest
    dedupe.init_decisions()
    with pytest.raises(ValueError):
        dedupe.record_decision("AD1", "maybe")
def test_save_and_get_artifact():
    from src import dedupe
    import uuid
    dedupe.init_artifacts()
    ad_id = f"ART_{uuid.uuid4().hex[:8]}"
    dedupe.save_artifact(
        ad_id=ad_id, page_name="TestBrand", image_path="assets/x.jpg",
        blueprint={"format": "hero"}, generated_copy={"headline": "H"},
        draft_image="assets/x_draft.png",
        metadata={"cta": "Shop", "destination_url": "http://x"},
    )
    rows = dedupe.get_artifacts(ad_id)
    assert len(rows) == 1
    assert rows[0][1]["format"] == "hero"
    assert rows[0][2]["headline"] == "H"


def test_save_artifact_persists_operator_instruction_via_get_artifacts_full():
    """Step 2 auditability: operator_instruction must round-trip through the DB exactly
    like image_prompt, self-migrated in via init_artifacts()'s ADD COLUMN IF NOT EXISTS."""
    from src import dedupe
    import uuid
    dedupe.init_artifacts()
    ad_id = f"ART_{uuid.uuid4().hex[:8]}"
    try:
        dedupe.save_artifact(
            ad_id=ad_id, page_name="TestBrand", image_path="assets/x.jpg",
            blueprint={"format": "hero"}, generated_copy={"headline": "H"},
            draft_image="assets/x_draft.png",
            metadata={"cta": "Shop", "destination_url": "http://x"},
            operator_instruction="make the background warmer",
        )
        rows = dedupe.get_artifacts_full(limit=500)
        match = next(r for r in rows if r["ad_id"] == ad_id)
        assert match["operator_instruction"] == "make the background warmer"
    finally:
        with dedupe.get_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM artifacts WHERE ad_id=%s", (ad_id,))
            conn.commit()


def test_save_artifact_operator_instruction_defaults_to_empty_string():
    from src import dedupe
    import uuid
    dedupe.init_artifacts()
    ad_id = f"ART_{uuid.uuid4().hex[:8]}"
    try:
        dedupe.save_artifact(
            ad_id=ad_id, page_name="TestBrand", image_path="assets/x.jpg",
            blueprint={"format": "hero"}, generated_copy={"headline": "H"},
            draft_image="assets/x_draft.png",
            metadata={"cta": "Shop", "destination_url": "http://x"},
        )
        rows = dedupe.get_artifacts_full(limit=500)
        match = next(r for r in rows if r["ad_id"] == ad_id)
        assert match["operator_instruction"] == ""
    finally:
        with dedupe.get_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM artifacts WHERE ad_id=%s", (ad_id,))
            conn.commit()


# ---- Prompt 4, Item 1: critic_findings - self-migrating, defaults empty, replaced whole ----

def test_save_artifact_critic_findings_defaults_to_empty_list():
    from src import dedupe
    import uuid
    dedupe.init_artifacts()
    ad_id = f"ART_{uuid.uuid4().hex[:8]}"
    try:
        dedupe.save_artifact(
            ad_id=ad_id, page_name="TestBrand", image_path="assets/x.jpg",
            blueprint={"format": "hero"}, generated_copy={"headline": "H"},
            draft_image="assets/x_draft.png",
            metadata={"cta": "Shop", "destination_url": "http://x"},
        )
        rows = dedupe.get_artifacts_full(limit=500)
        match = next(r for r in rows if r["ad_id"] == ad_id)
        assert match["critic_findings"] == []
    finally:
        with dedupe.get_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM artifacts WHERE ad_id=%s", (ad_id,))
            conn.commit()


def test_update_artifact_findings_replaces_wholesale():
    from src import dedupe
    import uuid
    dedupe.init_artifacts()
    ad_id = f"ART_{uuid.uuid4().hex[:8]}"
    try:
        dedupe.save_artifact(
            ad_id=ad_id, page_name="TestBrand", image_path="assets/x.jpg",
            blueprint={"format": "hero"}, generated_copy={"headline": "H"},
            draft_image="assets/x_draft.png",
            metadata={"cta": "Shop", "destination_url": "http://x"},
        )
        dedupe.update_artifact_findings(
            ad_id, [{"category": "testimonial", "description": "fabricated quote", "confidence": "high"}]
        )
        rows = dedupe.get_artifacts_full(limit=500)
        match = next(r for r in rows if r["ad_id"] == ad_id)
        assert match["critic_findings"] == [
            {"category": "testimonial", "description": "fabricated quote", "confidence": "high"}
        ]

        # A regenerate's findings REPLACE the old set entirely - never accumulate.
        dedupe.update_artifact_findings(
            ad_id, [{"category": "offer", "description": "promo code visible", "confidence": "medium"}]
        )
        rows = dedupe.get_artifacts_full(limit=500)
        match = next(r for r in rows if r["ad_id"] == ad_id)
        assert match["critic_findings"] == [
            {"category": "offer", "description": "promo code visible", "confidence": "medium"}
        ]
    finally:
        with dedupe.get_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM artifacts WHERE ad_id=%s", (ad_id,))
            conn.commit()


# ---- Prompt 4, Item 4: format_flag round-trips through save_artifact/get_artifacts_full ----

def test_save_artifact_persists_format_flag():
    from src import dedupe
    import uuid
    dedupe.init_artifacts()
    ad_id = f"ART_{uuid.uuid4().hex[:8]}"
    try:
        dedupe.save_artifact(
            ad_id=ad_id, page_name="TestBrand", image_path="assets/x.jpg",
            blueprint={"format": "offer_led"}, generated_copy={"headline": "H"},
            draft_image="assets/x_draft.png",
            metadata={"cta": "Shop", "destination_url": "http://x"},
            format_flag="reference was a 6-product bundle offer",
        )
        rows = dedupe.get_artifacts_full(limit=500)
        match = next(r for r in rows if r["ad_id"] == ad_id)
        assert match["format_flag"] == "reference was a 6-product bundle offer"
    finally:
        with dedupe.get_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM artifacts WHERE ad_id=%s", (ad_id,))
            conn.commit()


def test_save_artifact_format_flag_defaults_to_empty_string():
    from src import dedupe
    import uuid
    dedupe.init_artifacts()
    ad_id = f"ART_{uuid.uuid4().hex[:8]}"
    try:
        dedupe.save_artifact(
            ad_id=ad_id, page_name="TestBrand", image_path="assets/x.jpg",
            blueprint={"format": "hero"}, generated_copy={"headline": "H"},
            draft_image="assets/x_draft.png",
            metadata={"cta": "Shop", "destination_url": "http://x"},
        )
        rows = dedupe.get_artifacts_full(limit=500)
        match = next(r for r in rows if r["ad_id"] == ad_id)
        assert match["format_flag"] == ""
    finally:
        with dedupe.get_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM artifacts WHERE ad_id=%s", (ad_id,))
            conn.commit()


def test_get_artifact_returns_angle_id_and_text_in_image():
    """dashboard.py's api_edit_image reads these back to restore the original generation's
    rule-6 mode on edit - get_artifact must actually return them, not just accept angle_id
    as a disambiguation param."""
    from src import dedupe
    import uuid
    dedupe.init_artifacts()
    ad_id = f"ART_{uuid.uuid4().hex[:8]}"
    try:
        dedupe.save_artifact(
            ad_id=ad_id, page_name="TestBrand", image_path="assets/x.jpg",
            blueprint={"format": "hero"}, generated_copy={"headline": "H"},
            draft_image="assets/x_draft.png",
            metadata={"cta": "Shop", "destination_url": "http://x"},
            angle_id=None, text_in_image=True,
        )
        art = dedupe.get_artifact(ad_id)
        assert art["angle_id"] is None
        assert art["text_in_image"] is True
    finally:
        with dedupe.get_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM artifacts WHERE ad_id=%s", (ad_id,))
            conn.commit()