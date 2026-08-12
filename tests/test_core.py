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
        "structural_zones": [],
        "production_style": {"style": "ugc", "confidence": "high", "signals": ["handheld framing"]},
        "body_area_shown": "none",
        "face_present": {"has_face": False, "prominence": "none", "location": ""},
        "semantic_split": {"is_split": False, "split_axis": None, "left_or_before": "", "right_or_after": ""},
        "scene_elements": [],
        "testimonial_zones": [],
        "text_purpose": [],
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


def test_missing_structural_zones_fails():
    """structural_zones is now a required field (schema/blueprint.schema.json) - an ad
    with no zones must return an explicit empty array, not omit the key entirely."""
    bp = _valid_blueprint()
    del bp["structural_zones"]
    assert validator.is_valid(bp) is False


def test_missing_production_style_fails():
    """production_style is now required (2026-08-11 schema change) - promoted from
    optional; an ad must always classify its own production style, never omit it."""
    bp = _valid_blueprint()
    del bp["production_style"]
    assert validator.is_valid(bp) is False


def test_missing_body_area_shown_fails():
    """body_area_shown is now required (2026-08-11 schema change) - promoted from
    optional; an ad with no human subject must still say so explicitly ("none")."""
    bp = _valid_blueprint()
    del bp["body_area_shown"]
    assert validator.is_valid(bp) is False


def test_missing_face_present_fails():
    bp = _valid_blueprint()
    del bp["face_present"]
    assert validator.is_valid(bp) is False


def test_missing_semantic_split_fails():
    bp = _valid_blueprint()
    del bp["semantic_split"]
    assert validator.is_valid(bp) is False


def test_missing_scene_elements_fails():
    bp = _valid_blueprint()
    del bp["scene_elements"]
    assert validator.is_valid(bp) is False


def test_missing_testimonial_zones_fails():
    bp = _valid_blueprint()
    del bp["testimonial_zones"]
    assert validator.is_valid(bp) is False


def test_missing_text_purpose_fails():
    bp = _valid_blueprint()
    del bp["text_purpose"]
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
    """Enum tightened 2026-08-11: ugc_native/high_spec_studio renamed to ugc/high_spec,
    hybrid dropped entirely. generate_image_prompt_writer.STYLE_GUIDANCE still keys on
    the OLD names - that's a known, deliberately deferred consumption gap, not fixed by
    this schema/prompt change."""
    styles = validator.production_styles()
    assert set(styles) == {"ugc", "high_spec", "illustrated"}


def test_creative_formats_returns_canonical_list():
    formats = validator.creative_formats()
    assert set(formats) == {
        "testimonial_review", "before_after", "problem_solution", "product_hero",
        "offer_led", "comparison", "listicle_tips", "founder_story",
        "ingredient_focus", "lifestyle_scene", "text_led_editorial",
    }


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


# ---- Silent-override audit (2026-08-05): product_override_note round-trips through
# save_artifact/get_artifacts_full, same shape as format_flag above ----

def test_save_artifact_persists_product_override_note():
    from src import dedupe
    import uuid
    dedupe.init_artifacts()
    ad_id = f"ART_{uuid.uuid4().hex[:8]}"
    try:
        dedupe.save_artifact(
            ad_id=ad_id, page_name="TestBrand", image_path="assets/x.jpg",
            blueprint={"format": "before_after"}, generated_copy={"headline": "H"},
            draft_image="assets/x_draft.png",
            metadata={"cta": "Shop", "destination_url": "http://x"},
            product_override_note="Product suppressed for this draft: the reference ad "
                                   "has no product to substitute, so include_product was "
                                   "overridden off for this run.",
        )
        rows = dedupe.get_artifacts_full(limit=500)
        match = next(r for r in rows if r["ad_id"] == ad_id)
        assert match["product_override_note"] == (
            "Product suppressed for this draft: the reference ad has no product to "
            "substitute, so include_product was overridden off for this run."
        )
    finally:
        with dedupe.get_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM artifacts WHERE ad_id=%s", (ad_id,))
            conn.commit()


def test_save_artifact_product_override_note_defaults_to_empty_string():
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
        assert match["product_override_note"] == ""
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


def test_get_artifact_returns_stored_run_strip_inputs():
    """2026-08-06: include_product/retheme_colours/realism/body_area/offer_text/product_id
    must round-trip - pipeline._regenerate_existing_draft rebuilds a regenerated draft's
    prompt from exactly these, so a value that doesn't survive the round trip would
    silently corrupt every future regenerate of this ad."""
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
            include_product=False, retheme_colours=False, realism="illustrated",
            body_area="legs", offer_text="20% off", product_id=1,
        )
        art = dedupe.get_artifact(ad_id)
        assert art["include_product"] is False
        assert art["retheme_colours"] is False
        assert art["realism"] == "illustrated"
        assert art["body_area"] == "legs"
        assert art["offer_text"] == "20% off"
        assert art["product_id"] == 1
    finally:
        with dedupe.get_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM artifacts WHERE ad_id=%s", (ad_id,))
            conn.commit()


def test_get_artifact_run_strip_inputs_default_to_none_when_not_passed():
    """None must mean "never recorded" distinctly from a real False/empty value -
    pipeline._regenerate_existing_draft's missing-input logging depends on telling these
    apart, so a caller that omits these (every pre-2026-08-06 save_artifact call) must
    read back None, never a silently-assumed default."""
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
        art = dedupe.get_artifact(ad_id)
        assert art["include_product"] is None
        assert art["retheme_colours"] is None
        assert art["realism"] is None
        assert art["body_area"] is None
        assert art["offer_text"] is None
        assert art["product_id"] is None
    finally:
        with dedupe.get_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM artifacts WHERE ad_id=%s", (ad_id,))
            conn.commit()


# ---- Prompt 4, Item 5: brand_settings - single-row, self-migrating, editable palette ----

def test_brand_settings_defaults_to_besque_palette():
    from src import dedupe
    dedupe.init_brand_settings()
    settings = dedupe.get_brand_settings()
    assert settings["palette"] == dedupe.DEFAULT_PALETTE
    assert "terracotta" in settings["palette"]


def test_update_brand_settings_persists_new_palette():
    from src import dedupe
    dedupe.init_brand_settings()
    original = dedupe.get_brand_settings()["palette"]
    try:
        dedupe.update_brand_settings("sage, cream, gold")
        assert dedupe.get_brand_settings()["palette"] == "sage, cream, gold"
    finally:
        dedupe.update_brand_settings(original)  # restore - single shared row


def test_update_brand_settings_falls_back_to_default_when_blank():
    from src import dedupe
    dedupe.init_brand_settings()
    original = dedupe.get_brand_settings()["palette"]
    try:
        dedupe.update_brand_settings("")
        assert dedupe.get_brand_settings()["palette"] == dedupe.DEFAULT_PALETTE
    finally:
        dedupe.update_brand_settings(original)