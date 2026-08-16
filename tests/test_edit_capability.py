"""Unit tests for src/edit_capability.py - Dynamic Edit System, Step 2. Zero image
calls, zero DB calls: derive_edit_capabilities is a pure function of a plain dict, so
every fixture here is hand-built, never fetched via dedupe.get_artifact_by_id."""
from src.edit_capability import (
    derive_edit_capabilities, find_control, clamp_person_age, RULE_10_AGE_FLOOR,
    get_brand_wordmark_zone,
)


def _targets(controls):
    return {(c["target"], c["attribute"]) for c in controls}


# ---- Fixture 1: full-featured artifact - every control category present ----

FULL_ARTIFACT = {
    "generated_copy": {
        "headline": "Soften, Don't Settle",
        "primary_text": "Besque Magic Body Oil melts into skin fast.",
        "image_subtext": "Real results, real skin.",
        "cta": "Shop Now",
    },
    "offer_text": "Free shipping over £40",
    "text_in_image": True,
    "element_provenance": {"product": "substituted"},
    "blueprint": {
        "text_purpose": [
            {"text_verbatim": "ref headline", "purpose": "problem_hook", "placement": "top-centre"},
            {"text_verbatim": "ref cta", "purpose": "cta", "placement": "bottom"},
        ],
        "structural_zones": [
            {"zone_type": "sub_line", "position": "mid-left", "container": "none", "detail": "d"},
            {"zone_type": "cta", "position": "bottom", "container": "banner", "detail": "d"},
        ],
        "face_present": {"has_face": True, "prominence": "primary", "location": "centre"},
        "objects": [
            {"object_id": "obj_01", "kind": "prop", "description": "wooden shelf",
             "bbox": [0, 0.6, 1, 0.4], "colours": ["brown"], "ownership": "generic",
             "role": "environment", "carries_brand_mark": False,
             "persuasive_function": "backdrop prop", "disposition": "keep"},
            {"object_id": "obj_02", "kind": "person", "description": "model's hand",
             "bbox": [0.3, 0.4, 0.2, 0.2], "colours": [], "ownership": "person",
             "role": "supporting_prop", "carries_brand_mark": False,
             "persuasive_function": "holding bottle", "disposition": "keep"},
        ],
        "layout_detail": {
            "product_count": 1, "background_type": "bathroom counter",
            "has_corner_badge": True, "has_bottom_banner": True,
        },
        "visual": {
            "scene_lighting": {"light_direction": "left", "hardness": "soft", "colour_temperature": "warm"},
        },
        "typography": {"headline_face": "serif", "headline_weight": "bold", "case_treatment": "title case"},
    },
}


def test_full_artifact_has_every_control_category():
    controls = derive_edit_capabilities(FULL_ARTIFACT)
    targets = _targets(controls)
    assert ("headline", "text") in targets
    assert ("subtext", "text") in targets
    assert ("cta", "text") in targets
    assert ("offer", "text") in targets
    # person_face/age and person_face/expression are fail-closed (2026-08-14): neither
    # has a real per-artifact stored value anywhere in the data model, so neither is
    # ever emitted - see _person_face_controls' own docstring.
    assert ("person_face", "age") not in targets
    assert ("person_face", "expression") not in targets
    assert ("product", "placement") in targets
    assert ("background", "type") in targets
    assert ("lighting", "scene_lighting") in targets
    assert ("typography", "style") in targets
    assert ("badge", "corner_badge") in targets
    assert ("banner", "bottom_banner") in targets
    # Stage 4 (2026-08-17): one remove control per blueprint.objects row - REPLACES
    # scene_elements' prop/person_body routing (deleted; objects has no kind-based
    # target split any more, every object gets target="object").
    assert ("object", "obj_01") in targets
    assert ("object", "obj_02") in targets


def test_full_artifact_current_values_come_from_copy_columns_not_blueprint():
    controls = derive_edit_capabilities(FULL_ARTIFACT)
    headline = find_control(controls, "headline", "text")
    assert headline["current_value"] == "Soften, Don't Settle"
    offer = find_control(controls, "offer", "text")
    assert offer["current_value"] == "Free shipping over £40"


# essential/warning DELETED 2026-08-17 along with scene_elements: the objects schema
# (schema/blueprint.schema.json) has no `essential` field at all - _object_remove_
# controls is deliberately remove-only for every object uniformly, with no
# essential-flagged-extra-warning distinction the old scene_elements-based controls had.

def test_object_remove_control_is_remove_only_with_description_as_label():
    controls = derive_edit_capabilities(FULL_ARTIFACT)
    shelf = find_control(controls, "object", "obj_01")
    assert shelf["allowed_ops"] == ["remove"]
    assert shelf["label"] == "wooden shelf"
    assert shelf["current_value"] == "wooden shelf"


# ---- Fixture 2: minimal/empty artifact - fail-closed, nothing derivable ----

EMPTY_ARTIFACT = {
    "generated_copy": {},
    "offer_text": None,
    "text_in_image": False,
    "blueprint": {
        "text_purpose": [],
        "structural_zones": [],
        "face_present": {"has_face": False, "prominence": "none", "location": ""},
        "scene_elements": [],
        "layout_detail": {},
    },
}


def test_empty_artifact_yields_no_controls():
    controls = derive_edit_capabilities(EMPTY_ARTIFACT)
    assert controls == []


def test_missing_blueprint_key_does_not_raise():
    # A bare dict with no "blueprint" key at all (defensive - _blueprint() must default).
    controls = derive_edit_capabilities({"generated_copy": {}, "offer_text": None})
    assert controls == []


# ---- Fixture 3: partial artifact - product + cta only, no face, hand-only scene element ----

PARTIAL_ARTIFACT = {
    "generated_copy": {
        "headline": "",  # no headline was generated this run
        "cta": "Learn More",
    },
    "offer_text": "",
    "text_in_image": False,
    "element_provenance": {"product": "substituted"},
    "blueprint": {
        "text_purpose": [{"text_verbatim": "x", "purpose": "cta", "placement": "bottom"}],
        "structural_zones": [],
        "face_present": {"has_face": False, "prominence": "none", "location": ""},
        "objects": [
            {"object_id": "obj_01", "kind": "person", "description": "bare hand",
             "bbox": [0.4, 0.5, 0.2, 0.2], "colours": [], "ownership": "person",
             "role": "supporting_prop", "carries_brand_mark": False,
             "persuasive_function": "applying oil", "disposition": "keep"},
        ],
        "layout_detail": {"product_count": 2},
    },
}


def test_partial_artifact_no_headline_no_face_but_has_cta_and_product():
    controls = derive_edit_capabilities(PARTIAL_ARTIFACT)
    targets = _targets(controls)
    assert ("headline", "text") not in targets  # empty copy value -> no control
    assert ("person_face", "age") not in targets  # has_face False -> no age/expression
    assert ("person_face", "expression") not in targets
    assert ("cta", "text") in targets
    assert ("product", "placement") in targets
    assert ("object", "obj_01") in targets  # one remove control per objects row


def test_offer_empty_string_yields_no_offer_control():
    controls = derive_edit_capabilities(PARTIAL_ARTIFACT)
    assert find_control(controls, "offer", "text") is None


def test_product_count_zero_yields_no_product_control():
    artifact = {
        "generated_copy": {}, "offer_text": None,
        "element_provenance": {"product": "substituted"},
        "blueprint": {"text_purpose": [], "structural_zones": [], "scene_elements": [],
                       "face_present": {"has_face": False}, "layout_detail": {"product_count": 0}},
    }
    assert find_control(derive_edit_capabilities(artifact), "product", "placement") is None


# ---- Product control requires AGREEMENT: product_count > 0 AND
# element_provenance.product == "substituted" - fails closed on either alone ----

def _artifact_with(product_count, provenance_product, include_product=None):
    return {
        "generated_copy": {}, "offer_text": None,
        "include_product": include_product,
        "element_provenance": {"product": provenance_product} if provenance_product is not None else {},
        "blueprint": {"text_purpose": [], "structural_zones": [], "scene_elements": [],
                      "face_present": {"has_face": False},
                      "layout_detail": {"product_count": product_count}},
    }


def test_product_control_present_when_count_and_substituted_agree():
    artifact = _artifact_with(product_count=2, provenance_product="substituted")
    assert find_control(derive_edit_capabilities(artifact), "product", "placement") is not None


def test_product_control_absent_when_provenance_is_added_even_with_real_product_count():
    # The exact live case (artifact 1250, 2026-08-14): product_count=0 in the
    # reference blueprint but element_provenance says "added" - "added" is NEVER
    # trusted alone, so this must still fail closed even if a nonzero count existed.
    artifact = _artifact_with(product_count=2, provenance_product="added", include_product=True)
    assert find_control(derive_edit_capabilities(artifact), "product", "placement") is None


def test_product_control_absent_when_provenance_is_none():
    artifact = _artifact_with(product_count=3, provenance_product="none")
    assert find_control(derive_edit_capabilities(artifact), "product", "placement") is None


def test_product_control_absent_when_provenance_missing_entirely():
    # Pre-migration rows with no element_provenance at all - fail closed, never assume.
    artifact = _artifact_with(product_count=2, provenance_product=None)
    assert find_control(derive_edit_capabilities(artifact), "product", "placement") is None


def test_product_control_absent_when_count_zero_even_if_substituted():
    artifact = _artifact_with(product_count=0, provenance_product="substituted")
    assert find_control(derive_edit_capabilities(artifact), "product", "placement") is None


def test_product_control_ignores_include_product_toggle_alone():
    # include_product is operator INTENT, not evidence of what rendered - agreeing
    # with itself is not the same as agreeing with element_provenance.
    artifact = _artifact_with(product_count=0, provenance_product="added", include_product=True)
    assert find_control(derive_edit_capabilities(artifact), "product", "placement") is None


# ---- Product realism control (2026-08-15): re-render treatment only, same fail-closed
# agreement _product_control requires - product_count > 0 AND element_provenance.
# product == "substituted" ----

def test_product_realism_control_present_when_count_and_substituted_agree():
    artifact = _artifact_with(product_count=1, provenance_product="substituted")
    control = find_control(derive_edit_capabilities(artifact), "product", "realism")
    assert control is not None
    assert control["label"] == "Product — Realism"
    assert control["allowed_ops"] == ["change"]


def test_product_realism_control_current_value_reflects_production_style():
    artifact = _artifact_with(product_count=1, provenance_product="substituted")
    artifact["blueprint"]["production_style"] = {"style": "illustrated"}
    control = find_control(derive_edit_capabilities(artifact), "product", "realism")
    assert control["current_value"] == "illustrated"


def test_product_realism_control_current_value_unspecified_when_no_style_recorded():
    artifact = _artifact_with(product_count=1, provenance_product="substituted")
    control = find_control(derive_edit_capabilities(artifact), "product", "realism")
    assert control["current_value"] == "unspecified"


def test_product_realism_control_absent_when_provenance_is_added():
    artifact = _artifact_with(product_count=2, provenance_product="added", include_product=True)
    assert find_control(derive_edit_capabilities(artifact), "product", "realism") is None


def test_product_realism_control_absent_when_count_zero():
    artifact = _artifact_with(product_count=0, provenance_product="substituted")
    assert find_control(derive_edit_capabilities(artifact), "product", "realism") is None


def test_product_realism_control_absent_when_provenance_missing_entirely():
    artifact = _artifact_with(product_count=2, provenance_product=None)
    assert find_control(derive_edit_capabilities(artifact), "product", "realism") is None


def test_product_realism_and_placement_controls_coexist_as_distinct_controls():
    artifact = _artifact_with(product_count=1, provenance_product="substituted")
    controls = derive_edit_capabilities(artifact)
    assert find_control(controls, "product", "placement") is not None
    assert find_control(controls, "product", "realism") is not None


def test_headline_present_but_no_text_purpose_structure_is_omitted():
    # Fail-closed rule stated explicitly in the spec: "No copy column value and no
    # matching text_purpose entry -> no Headline control." Here the copy VALUE exists
    # but blueprint carries no text_purpose entries at all.
    artifact = {
        "generated_copy": {"headline": "Some Besque headline"},
        "offer_text": None,
        "blueprint": {"text_purpose": [], "structural_zones": [], "scene_elements": [],
                       "face_present": {"has_face": False}, "layout_detail": {}},
    }
    assert find_control(derive_edit_capabilities(artifact), "headline", "text") is None


def test_rule_10_age_floor_is_45():
    assert RULE_10_AGE_FLOOR == 45


# ---- clamp_person_age: older-only, never younger ----

def test_clamp_person_age_passes_through_an_older_request():
    resolved, clamped = clamp_person_age("make her look a bit older, more silver in the hair")
    assert clamped is False
    assert "older" in resolved


def test_clamp_person_age_rejects_younger_keyword():
    resolved, clamped = clamp_person_age("make her look younger")
    assert clamped is True
    assert "45" in resolved


def test_clamp_person_age_clamps_numeric_age_below_floor():
    resolved, clamped = clamp_person_age("make her 30 years old")
    assert clamped is True
    assert "45" in resolved


def test_clamp_person_age_numeric_age_above_floor_not_clamped():
    resolved, clamped = clamp_person_age("make her 55 years old")
    assert clamped is False
    assert "55" in resolved


# ---- Headline/subtext gating: text_in_image + a genuinely headline-shaped
# text_purpose entry, not "any entry at all" (the bug fixed 2026-08-14) ----

def test_generic_text_in_image_off_with_copy_present_yields_no_headline_or_subtext(monkeypatch=None):
    # copy exists, text_in_image is explicitly off - the general case the fix must cover
    # regardless of what any specific real artifact's stored flag happens to be.
    artifact = {
        "generated_copy": {"headline": "Some Besque headline", "image_subtext": "Some subtext"},
        "offer_text": None, "text_in_image": False,
        "blueprint": {
            "text_purpose": [{"text_verbatim": "x", "purpose": "problem_hook", "placement": "top"}],
            "structural_zones": [], "scene_elements": [],
            "face_present": {"has_face": False}, "layout_detail": {},
        },
    }
    controls = derive_edit_capabilities(artifact)
    assert find_control(controls, "headline", "text") is None
    assert find_control(controls, "subtext", "text") is None


def test_artifact_1251_shape_text_in_image_true_but_no_headline_shaped_zone_yields_no_headline():
    # Real shape found live on artifact 1251, 2026-08-14: text_in_image=True,
    # generated_copy.headline populated, but text_purpose entries are only "other"
    # (the COMPETITOR's own wordmark/tagline text) and "testimonial" - never anything
    # headline-shaped. The image never actually rendered the headline; the old "any
    # text_purpose entry" gate wrongly offered the control anyway.
    artifact = {
        "generated_copy": {"headline": "Summer is calling, feel ready for it",
                            "image_subtext": "Step into summer feeling at home in your skin."},
        "offer_text": None, "text_in_image": True,
        "blueprint": {
            "text_purpose": [
                {"text_verbatim": "Crépe Erase®", "purpose": "other", "placement": "top-centre"},
                {"text_verbatim": "by THE BODY FIRM™", "purpose": "other", "placement": "top-centre, directly below brand name"},
                {"text_verbatim": "I'm excited about the summer.", "purpose": "testimonial", "placement": "mid-centre"},
                {"text_verbatim": "Cherie, 55", "purpose": "testimonial", "placement": "centre, below quote"},
            ],
            "structural_zones": [
                {"zone_type": "brand_wordmark", "position": "top-centre", "container": "none", "detail": "BESQUE wordmark"},
                {"zone_type": "social_proof", "position": "mid-centre", "container": "none", "detail": "quote"},
            ],
            "scene_elements": [], "face_present": {"has_face": False}, "layout_detail": {},
        },
    }
    controls = derive_edit_capabilities(artifact)
    assert find_control(controls, "headline", "text") is None
    assert find_control(controls, "subtext", "text") is None


def test_headline_shaped_purpose_with_text_in_image_true_yields_control():
    artifact = {
        "generated_copy": {"headline": "Real headline"}, "offer_text": None, "text_in_image": True,
        "blueprint": {
            "text_purpose": [{"text_verbatim": "x", "purpose": "problem_hook", "placement": "top"}],
            "structural_zones": [], "scene_elements": [],
            "face_present": {"has_face": False}, "layout_detail": {},
        },
    }
    controls = derive_edit_capabilities(artifact)
    assert find_control(controls, "headline", "text") is not None


# ---- Never target the brand wordmark ----

def test_object_remove_controls_have_no_wordmark_exclusion_by_design():
    """2026-08-17: unlike the deleted scene_elements-based _scene_element_controls (which
    excluded any entry naming the wordmark/logo), _object_remove_controls has no such
    exclusion - see its own docstring for why: blueprint.objects describes the
    COMPETITOR reference, never the drafted image, so Besque's own wordmark is never
    one of these rows to begin with (it is ADDED by brand_rules() rule 9, not tracked
    here). A competitor logo object gets a remove control exactly like any other
    object - there is nothing dangerous about offering to remove IT, only about
    removing BESQUE's, which structurally cannot appear in this list."""
    artifact = {
        "generated_copy": {}, "offer_text": None, "text_in_image": False,
        "blueprint": {
            "face_present": {"has_face": False}, "layout_detail": {},
            "objects": [
                {"object_id": "obj_01", "kind": "logo", "description": "competitor wordmark",
                 "bbox": [0, 0, 0.2, 0.1], "colours": [], "ownership": "competitor_branded",
                 "role": "secondary", "carries_brand_mark": True,
                 "persuasive_function": "names the advertiser", "disposition": "drop"},
                {"object_id": "obj_02", "kind": "prop", "description": "wooden shelf",
                 "bbox": [0, 0.6, 1, 0.4], "colours": [], "ownership": "generic",
                 "role": "environment", "carries_brand_mark": False,
                 "persuasive_function": "backdrop prop", "disposition": "keep"},
            ],
        },
    }
    controls = derive_edit_capabilities(artifact)
    targets = {(c["target"], c["attribute"]) for c in controls}
    assert ("object", "obj_01") in targets
    assert ("object", "obj_02") in targets


def test_get_brand_wordmark_zone_finds_it_by_zone_type():
    blueprint = {"structural_zones": [
        {"zone_type": "brand_wordmark", "position": "top-centre", "container": "none", "detail": "d"},
    ]}
    zone = get_brand_wordmark_zone(blueprint)
    assert zone is not None
    assert zone["position"] == "top-centre"


def test_get_brand_wordmark_zone_none_when_absent():
    assert get_brand_wordmark_zone({"structural_zones": []}) is None


# ---- current_value must render as text, never a nested object ("[object Object]") ----

def test_current_value_is_a_string_where_populated():
    # person_face/age and person_face/expression are no longer emitted at all (fail-closed,
    # 2026-08-14 - see _person_face_controls), so they never reach this list. badge/banner
    # (current_value=True - a boolean presence flag, not free text) are excluded on
    # purpose: not the nested-dict shape that caused typography's "[object Object]" bug,
    # and fixing those two is not in scope here.
    excluded = {("badge", "corner_badge"), ("banner", "bottom_banner")}
    controls = derive_edit_capabilities(FULL_ARTIFACT)
    for c in controls:
        if (c["target"], c["attribute"]) in excluded:
            continue
        assert isinstance(c["current_value"], str), (c["target"], c["attribute"], c["current_value"])


def test_typography_current_value_is_flattened_to_a_string():
    controls = derive_edit_capabilities(FULL_ARTIFACT)
    typography = find_control(controls, "typography", "style")
    assert isinstance(typography["current_value"], str)
    assert "serif" in typography["current_value"]
    assert "bold" in typography["current_value"]
