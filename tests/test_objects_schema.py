"""Tests for the objects-array schema replacement (2026-08-17): resolve_disposition's
mechanical enforcement, the SCENE OBJECTS closure sentence, and legacy (no `objects`
key) blueprints not raising when read by the edit-capability/dashboard path."""
from src import deconstruct, generate_image_prompt, validator
from src.edit_capability import derive_edit_capabilities, legacy_scene_summary, find_control


def _obj(**overrides):
    base = {
        "object_id": "obj_01", "kind": "prop", "description": "a wooden tray",
        "bbox": [0.1, 0.1, 0.3, 0.3], "colours": ["brown"], "ownership": "generic",
        "role": "supporting_prop", "carries_brand_mark": False,
        "persuasive_function": "staging", "disposition": "keep",
    }
    base.update(overrides)
    return base


# ---- Stage 2: resolve_disposition unit tests ----

def test_resolve_disposition_competitor_branded_product_substitutes():
    obj = _obj(kind="product", ownership="competitor_branded", carries_brand_mark=True,
               disposition="keep")
    assert deconstruct.resolve_disposition(obj) == "substitute"


def test_resolve_disposition_competitor_branded_prop_carrying_logo_drops():
    obj = _obj(kind="prop", ownership="competitor_branded", carries_brand_mark=True,
               disposition="keep")
    assert deconstruct.resolve_disposition(obj) == "drop"


def test_resolve_disposition_generic_prop_passes_through_unchanged():
    obj = _obj(kind="prop", ownership="generic", carries_brand_mark=False, disposition="keep")
    assert deconstruct.resolve_disposition(obj) == "keep"
    obj2 = _obj(kind="prop", ownership="generic", carries_brand_mark=False, disposition="drop")
    assert deconstruct.resolve_disposition(obj2) == "drop"


def test_resolve_disposition_besque_product_passes_through_unchanged():
    obj = _obj(kind="product", ownership="besque", carries_brand_mark=False, disposition="keep")
    assert deconstruct.resolve_disposition(obj) == "keep"


def test_resolve_disposition_person_passes_through_unchanged():
    obj = _obj(kind="person", ownership="person", carries_brand_mark=False, disposition="keep")
    assert deconstruct.resolve_disposition(obj) == "keep"


def test_resolve_disposition_carries_brand_mark_forces_regardless_of_kind():
    # A non-product, non-competitor_branded object that still visibly carries a brand
    # mark (e.g. a generic-looking prop with the competitor's logo printed on it) must
    # still never resolve to "keep" - carries_brand_mark is checked independently of
    # ownership, per the task's own second rule.
    obj = _obj(kind="surface", ownership="generic", carries_brand_mark=True, disposition="keep")
    assert deconstruct.resolve_disposition(obj) == "drop"


def test_assert_no_competitor_branded_object_kept_raises_if_invariant_violated():
    # Defence-in-depth: if a caller bypasses resolve_disposition and hands back a
    # blueprint where a competitor_branded object still says "keep", the self-check
    # must raise rather than silently pass it through to image generation.
    bp = {"objects": [_obj(ownership="competitor_branded", carries_brand_mark=True,
                           disposition="keep")]}
    import pytest
    with pytest.raises(deconstruct.BlueprintValidationError):
        deconstruct._assert_no_competitor_branded_object_kept(bp)


# ---- Stage 2 (restoration): text_purpose drives disposition, one case per value ----
# Rules read out of git history (the commit immediately before the objects-array
# refactor, a99eafb:src/generate_image_prompt.py's _structural_zones_clause) and
# reimplemented in deconstruct.resolve_disposition against the new per-object schema -
# not reinvented from the task description alone.

def _text_obj(text_purpose, **overrides):
    base = {
        "object_id": "obj_01", "kind": "text", "description": "reference text",
        "bbox": [0.1, 0.1, 0.6, 0.1], "colours": [], "ownership": "generic",
        "role": "secondary", "carries_brand_mark": False,
        "persuasive_function": "reference text", "disposition": "keep",
        "text_purpose": text_purpose,
    }
    base.update(overrides)
    return base


def test_resolve_disposition_award_always_drops():
    assert deconstruct.resolve_disposition(_text_obj("award")) == "drop"


def test_resolve_disposition_disclaimer_always_drops():
    assert deconstruct.resolve_disposition(_text_obj("disclaimer")) == "drop"


def test_resolve_disposition_offer_substitutes_when_offer_text_supplied():
    obj = _text_obj("offer")
    assert deconstruct.resolve_disposition(obj, {"offer_text": "20% off"}) == "substitute"


def test_resolve_disposition_offer_drops_when_no_offer_text():
    obj = _text_obj("offer")
    assert deconstruct.resolve_disposition(obj) == "drop"
    assert deconstruct.resolve_disposition(obj, {}) == "drop"


def test_resolve_disposition_price_anchor_substitutes_when_offer_text_supplied():
    obj = _text_obj("price_anchor")
    assert deconstruct.resolve_disposition(obj, {"offer_text": "20% off"}) == "substitute"


def test_resolve_disposition_price_anchor_drops_when_no_offer_text():
    assert deconstruct.resolve_disposition(_text_obj("price_anchor")) == "drop"


def test_resolve_disposition_certification_substitutes_when_certifications_supplied():
    obj = _text_obj("certification")
    assert deconstruct.resolve_disposition(obj, {"certifications": ["Vegan"]}) == "substitute"


def test_resolve_disposition_certification_drops_when_no_certifications():
    assert deconstruct.resolve_disposition(_text_obj("certification")) == "drop"
    assert deconstruct.resolve_disposition(_text_obj("certification"), {"certifications": []}) == "drop"


def test_resolve_disposition_testimonial_substitutes_when_testimonial_supplied():
    obj = _text_obj("testimonial")
    testimonial = {"quote": "It changed my skin.", "attribution": "Jane D."}
    assert deconstruct.resolve_disposition(obj, {"testimonial": testimonial}) == "substitute"


def test_resolve_disposition_testimonial_drops_when_no_testimonial():
    # Never "keep" - a competitor's own testimonial reaching a Besque draft is exactly
    # the fabricated/borrowed-testimonial violation class this codebase has hit before,
    # even though nothing here is literally inventing new text.
    assert deconstruct.resolve_disposition(_text_obj("testimonial")) == "drop"
    assert deconstruct.resolve_disposition(_text_obj("testimonial", disposition="keep")) == "drop"


def test_resolve_disposition_product_callout_always_substitutes():
    assert deconstruct.resolve_disposition(_text_obj("product_callout")) == "substitute"


def test_resolve_disposition_headline_always_substitutes():
    assert deconstruct.resolve_disposition(_text_obj("headline")) == "substitute"


def test_resolve_disposition_subtext_always_substitutes():
    assert deconstruct.resolve_disposition(_text_obj("subtext")) == "substitute"


def test_resolve_disposition_cta_always_substitutes():
    assert deconstruct.resolve_disposition(_text_obj("cta")) == "substitute"


def test_resolve_disposition_other_passes_through_when_not_branded():
    assert deconstruct.resolve_disposition(_text_obj("other", disposition="keep")) == "keep"
    assert deconstruct.resolve_disposition(_text_obj("other", disposition="drop")) == "drop"


def test_resolve_disposition_competitor_branded_text_other_purpose_drops_not_keep():
    # The task's own explicit case: ownership rules still win, whatever text_purpose
    # says - a competitor-branded, "other"-purposed text object (e.g. a wordmark/
    # tagline the model didn't otherwise classify) must never resolve to keep, even
    # though "other" alone (unbranded) passes the model's own guess through unchanged.
    obj = _text_obj("other", ownership="competitor_branded", disposition="keep")
    assert deconstruct.resolve_disposition(obj) == "drop"


def test_resolve_disposition_carries_brand_mark_text_other_purpose_drops_not_keep():
    obj = _text_obj("other", ownership="generic", carries_brand_mark=True, disposition="keep")
    assert deconstruct.resolve_disposition(obj) == "drop"


# ---- Stage 2 (restoration): integration - no competitor offer/certification/
# testimonial can reach a built prompt as KEEP, regardless of what the model's own
# stored disposition says. This is the compliance regression the task describes: with
# only the ownership-based override, a generic-looking offer/cert/testimonial object
# that the model scored disposition="keep" would render "KEEP: ... reproduce exactly
# as shown" - i.e. tell Gemini to keep the competitor's own offer/certification/
# testimonial verbatim. ----

def _leak_prone_blueprint():
    return {
        "visual": {"layout": "flat lay", "subject": "", "palette_mood": "warm",
                   "text_placement": "lower"},
        "background": {"surface": "marble", "colour": "white", "light": "soft"},
        "production_style": {"style": "high_spec"},
        "objects": [
            _text_obj("offer", object_id="obj_01", description="20% off badge",
                      ownership="generic", disposition="keep"),
            _text_obj("certification", object_id="obj_02", description="Vegan seal",
                      ownership="generic", disposition="keep"),
            _text_obj("testimonial", object_id="obj_03", description="5-star quote",
                      ownership="generic", disposition="keep"),
        ],
    }


def test_no_competitor_offer_certification_or_testimonial_reaches_prompt_as_keep():
    # No offer_text/certifications/testimonial supplied this run - all three must
    # resolve to drop (ABSENT in the prompt), never the stored "keep".
    prompt = generate_image_prompt.build_image_prompt(_leak_prone_blueprint())
    assert "KEEP: 20% off badge" not in prompt
    assert "KEEP: Vegan seal" not in prompt
    assert "KEEP: 5-star quote" not in prompt
    assert "ABSENT: the 20% off badge" in prompt
    assert "ABSENT: the Vegan seal" in prompt
    assert "ABSENT: the 5-star quote" in prompt


def test_offer_certification_testimonial_substitute_when_context_supplied():
    # The positive case: with real Besque values supplied this run, the same objects
    # substitute with the authorised content - never a stale KEEP, and never silently
    # dropped either.
    prompt = generate_image_prompt.build_image_prompt(
        _leak_prone_blueprint(), offer_text="Free shipping over £40",
        product={"certifications": ["Vegan", "Cruelty Free"]},
        testimonial={"quote": "This oil changed my skin.", "attribution": "Jane D."},
    )
    assert "KEEP: 20% off badge" not in prompt
    assert "KEEP: Vegan seal" not in prompt
    assert "KEEP: 5-star quote" not in prompt
    assert "Free shipping over £40" in prompt
    assert "Vegan, Cruelty Free" in prompt
    assert "This oil changed my skin." in prompt


# ---- Stage 3: the closure sentence is present in every built prompt ----

CLOSURE_SENTENCE = (
    "The scene contains these objects and no others. Do not add any object, "
    "body part, hair, hand, garment or prop that is not listed above."
)


def _blueprint_with_objects():
    return {
        "visual": {"layout": "centered", "subject": "", "palette_mood": "warm",
                   "text_placement": "lower"},
        "background": {"surface": "marble", "colour": "white", "light": "soft"},
        "objects": [
            _obj(object_id="obj_01", kind="product", ownership="competitor_branded",
                 carries_brand_mark=True, disposition="substitute"),
            _obj(object_id="obj_02", kind="prop", disposition="keep"),
        ],
        "production_style": {"style": "high_spec"},
    }


def test_closure_sentence_present_in_template_branch():
    prompt = generate_image_prompt.build_image_prompt(_blueprint_with_objects())
    assert CLOSURE_SENTENCE in prompt


def test_closure_sentence_present_in_writer_branch():
    prompt = generate_image_prompt.build_image_prompt(
        _blueprint_with_objects(), creative_description="A styled scene.")
    assert CLOSURE_SENTENCE in prompt


def test_closure_sentence_present_in_edit_mode_branch():
    prompt = generate_image_prompt.build_image_prompt(_blueprint_with_objects(), edit_mode=True)
    assert CLOSURE_SENTENCE in prompt


def test_closure_sentence_absent_when_no_objects_at_all():
    # No objects -> _objects_clause returns "" entirely, closure sentence included -
    # never fabricated when there's nothing to close over.
    prompt = generate_image_prompt.build_image_prompt({"objects": []})
    assert CLOSURE_SENTENCE not in prompt


def test_dropped_object_named_as_absent_not_silently_omitted():
    bp = _blueprint_with_objects()
    bp["objects"].append(_obj(object_id="obj_03", description="a competitor's charm",
                               disposition="drop"))
    prompt = generate_image_prompt.build_image_prompt(bp)
    assert "a competitor's charm" in prompt
    assert "REMOVED" in prompt


# ---- Stage 5: a legacy blueprint (no `objects` key) never raises on read ----

def _legacy_blueprint():
    return {
        "scene_elements": [{"element": "wooden tray", "role": "staging", "essential": True,
                            "depicts_competitor_category": False}],
        "structural_zones": [{"zone_type": "brand_wordmark", "position": "top-left",
                              "container": "none", "detail": ""}],
        "layout_detail": {"product_count": 1},
        "face_present": {"has_face": False},
    }


def test_legacy_blueprint_derive_edit_capabilities_does_not_raise():
    artifact = {"blueprint": _legacy_blueprint(), "generated_copy": {}, "text_in_image": False,
                "offer_text": None, "element_provenance": {}}
    controls = derive_edit_capabilities(artifact)  # must not raise
    assert isinstance(controls, list)
    # No object-remove controls for a legacy blueprint - it has no `objects` key.
    assert find_control(controls, "object", "obj_01") is None


def test_legacy_scene_summary_lists_old_fields_read_only():
    summary = legacy_scene_summary(_legacy_blueprint())
    assert any("wooden tray" in line for line in summary)
    assert any("brand_wordmark" in line for line in summary)


def test_legacy_scene_summary_empty_when_objects_present():
    bp = _blueprint_with_objects()
    assert legacy_scene_summary(bp) == []


def test_legacy_blueprint_build_image_prompt_does_not_raise():
    prompt = generate_image_prompt.build_image_prompt(_legacy_blueprint())
    assert isinstance(prompt, str)


def test_legacy_blueprint_fails_new_schema_validation_but_is_still_readable():
    # A legacy blueprint is NOT valid against the new schema (no `objects`/`background`)
    # - that's expected and correct (new writes use the new schema only per Stage 5) -
    # but reading it back for display/editing must never raise regardless.
    assert validator.validation_error(_legacy_blueprint()) is not None


# ---- Stage 3: prompt length has not grown more than 25% against the pre-refactor
# build for an equivalent-richness reference (product substitution + text + offer +
# badge + testimonial + typography + a competitor prop) ----

# Measured 2026-08-17, BEFORE this refactor, from the then-current build_image_prompt
# against an old-schema fixture with the same conceptual content this test's new-schema
# fixture below carries (one substituted product, one kept prop, one dropped
# competitor-argument prop, a brand wordmark, a sub-line, a CTA, an offer badge, and a
# testimonial quote) - a real number, not guessed:
#   edit_mode prompt:  35914 chars
#   template prompt:   23048 chars
_PRE_REFACTOR_EDIT_MODE_LEN = 35914
_PRE_REFACTOR_TEMPLATE_LEN = 23048
_MAX_GROWTH_RATIO = 1.25


def _rich_new_schema_blueprint():
    return {
        "visual": {"layout": "flat lay on marble", "subject": "",
                   "palette_mood": "warm golden", "text_placement": "lower third"},
        "background": {"surface": "marble counter", "colour": "white",
                       "light": "soft warm upper-left"},
        "layout_detail": {"product_count": 1, "zone_positions": ["product mid-frame"]},
        "production_style": {"style": "high_spec"},
        "headline_verbatim": "Get Glowing Skin",
        "objects": [
            _obj(object_id="obj_01", kind="product", description="competitor bottle",
                 bbox=[0.1, 0.1, 0.3, 0.5], colours=["blue"], ownership="competitor_branded",
                 role="hero", carries_brand_mark=True, persuasive_function="hero product",
                 disposition="substitute"),
            _obj(object_id="obj_02", kind="prop", description="wooden tray",
                 bbox=[0, 0.5, 1, 0.5], colours=["brown"], ownership="generic",
                 role="supporting_prop", persuasive_function="staging", disposition="keep"),
            _obj(object_id="obj_03", kind="prop", description="measuring spoon of powder",
                 bbox=[0.6, 0.6, 0.2, 0.2], colours=["white"], ownership="competitor_branded",
                 role="secondary", persuasive_function="illustrates ingredient", disposition="drop"),
            _obj(object_id="obj_04", kind="logo", description="brand wordmark top-left",
                 bbox=[0, 0, 0.2, 0.1], colours=["black"], ownership="competitor_branded",
                 role="secondary", carries_brand_mark=True, persuasive_function="names the advertiser",
                 disposition="drop"),
            _obj(object_id="obj_05", kind="text", description="tagline sub-line",
                 bbox=[0.2, 0.05, 0.6, 0.1], colours=["white"], ownership="competitor_branded",
                 role="secondary", persuasive_function="supports headline", disposition="drop"),
            _obj(object_id="obj_06", kind="text", description="CTA button Shop Now",
                 bbox=[0.4, 0.9, 0.2, 0.08], colours=["white"], ownership="competitor_branded",
                 role="secondary", persuasive_function="call to action", disposition="substitute"),
            _obj(object_id="obj_07", kind="graphic", description="20% off badge",
                 bbox=[0.8, 0.05, 0.15, 0.15], colours=["red"], ownership="competitor_branded",
                 role="secondary", persuasive_function="communicates a discount", disposition="substitute"),
            _obj(object_id="obj_08", kind="text",
                 description="customer testimonial quote with 5-star rating",
                 bbox=[0.0, 0.8, 0.4, 0.2], colours=["black"], ownership="competitor_branded",
                 role="secondary", persuasive_function="proves social validation", disposition="drop"),
        ],
        "face_present": {"has_face": False, "prominence": "none", "location": ""},
    }


_RICH_PRODUCT = {
    "visual_description": "Clear glass bottle, terracotta label, black pump.",
    "substance_colour": "golden-amber", "certifications": ["Vegan", "Cruelty Free"],
}


def test_prompt_length_edit_mode_within_25_percent_of_pre_refactor_baseline():
    prompt = generate_image_prompt.build_image_prompt(
        _rich_new_schema_blueprint(), product=_RICH_PRODUCT, edit_mode=True,
        include_product=True, text_in_image=True, headline="Get Glowing Skin",
        subtext="7 oils, deeper hydration", offer_text="20% off", cta_text="Shop Now",
    )
    assert len(prompt) <= _PRE_REFACTOR_EDIT_MODE_LEN * _MAX_GROWTH_RATIO, (
        f"edit-mode prompt grew to {len(prompt)} chars, more than "
        f"{_MAX_GROWTH_RATIO}x the {_PRE_REFACTOR_EDIT_MODE_LEN}-char pre-refactor baseline"
    )


def test_prompt_length_template_within_25_percent_of_pre_refactor_baseline():
    prompt = generate_image_prompt.build_image_prompt(
        _rich_new_schema_blueprint(), product=_RICH_PRODUCT, include_product=True,
        text_in_image=True, headline="Get Glowing Skin", subtext="7 oils, deeper hydration",
    )
    assert len(prompt) <= _PRE_REFACTOR_TEMPLATE_LEN * _MAX_GROWTH_RATIO, (
        f"template prompt grew to {len(prompt)} chars, more than "
        f"{_MAX_GROWTH_RATIO}x the {_PRE_REFACTOR_TEMPLATE_LEN}-char pre-refactor baseline"
    )
