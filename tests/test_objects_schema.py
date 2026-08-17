"""Tests for the objects-array schema replacement (2026-08-17): resolve_disposition's
mechanical enforcement, the SCENE OBJECTS closure sentence, and legacy (no `objects`
key) blueprints not raising when read by the edit-capability/dashboard path."""
from src import deconstruct, generate_copy, generate_image_prompt, validator
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


# 2026-08-17: a person in a competitor reference must NEVER resolve to "keep" - she is
# that competitor's own model by definition, and reproducing her pixel-identical is
# never correct. Rule 10's prompt-only age/skin-texture requirements cannot bind
# against a "keep" disposition sitting closer to the point of use in the assembled
# prompt - the same "prompt-only rules do not reliably bind" failure class this
# codebase has hit repeatedly (see CLAUDE.md). Fixed mechanically here, not by adding
# another prompt sentence - replaces the old passthrough-unchanged test below, which
# encoded the exact behaviour that produced the live failure.

def test_resolve_disposition_person_keep_forces_substitute():
    obj = _obj(kind="person", ownership="person", carries_brand_mark=False, disposition="keep")
    assert deconstruct.resolve_disposition(obj) == "substitute"


def test_resolve_disposition_person_already_substitute_unchanged():
    obj = _obj(kind="person", ownership="person", carries_brand_mark=False, disposition="substitute")
    assert deconstruct.resolve_disposition(obj) == "substitute"


def test_resolve_disposition_person_drop_still_forces_substitute():
    # Unconditional, per the task's own wording - never gated on what the model itself
    # guessed, unlike the generic ownership/carries_brand_mark passthrough below.
    obj = _obj(kind="person", ownership="person", carries_brand_mark=False, disposition="drop")
    assert deconstruct.resolve_disposition(obj) == "substitute"


def test_resolve_disposition_person_never_drops_even_when_branded():
    # A person object visibly carrying a brand mark (e.g. a competitor's logo on a
    # model's t-shirt) must still SUBSTITUTE, never DROP the way a branded prop/logo
    # does - the person dispatch is checked before the is_branded branch, so branding
    # can never redirect a person to "drop".
    obj = _obj(kind="person", ownership="competitor_branded", carries_brand_mark=True,
               disposition="keep")
    assert deconstruct.resolve_disposition(obj) == "substitute"


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


def test_person_object_substitute_disposition_survives_objects_clause_second_pass():
    """_objects_clause re-resolves via deconstruct.resolve_disposition a SECOND time,
    with this run's real context - but ONLY for a kind=="text" object with a real
    text_purpose (text_purpose is forced to None for every other kind, see
    _objects_clause's own dispatch: `text_purpose = obj.get("text_purpose") if kind ==
    "text" else None`). A kind=="person" object is never routed through that second
    call at all - it simply trusts obj.get("disposition") as already resolved. This
    confirms the person object's disposition (already "substitute" by the time a real
    blueprint reaches this function, per resolve_disposition's own unconditional person
    dispatch at deconstruct time) reaches the assembled prompt unchanged - never
    silently re-flipped toward "keep" by this second pass."""
    bp = _blueprint_with_objects()
    bp["objects"].append(_obj(object_id="obj_03", kind="person", ownership="person",
                              description="the competitor's model", disposition="substitute"))
    prompt = generate_image_prompt.build_image_prompt(bp)
    assert "SUBSTITUTE: replace this person (\"the competitor's model\")" in prompt
    assert "KEEP: the competitor's model" not in prompt


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


# ---- Per-object copy generation restoration (2026-08-17): root-cause fix for a live
# failure - a reference with four Instagram DM bubbles produced a draft where all four
# carried the IDENTICAL generated sentence. Every such bubble is a kind=="text" object
# with no recognised text_purpose ("other", or the field absent) - every OTHER purpose
# already has its own dedicated content source (rule 6, offer_text, certifications,
# testimonial, product_name, cta_text) and is unaffected by any of this.
#
# Restores text_zone_targets/_text_zone_copy_clause (per-zone copy, deleted a9b1e9f) and
# _text_purpose_clause's job-inheritance rule (deleted a9b1e9f), recovered from git
# history rather than reinvented - re-keyed to object_id instead of a free-text position
# string, since blueprint.objects makes a real, stable identifier available. ----

def _other_text_obj(object_id, description, persuasive_function, bbox, role="secondary", **overrides):
    overrides.setdefault("disposition", "substitute")
    return _text_obj(
        "other", object_id=object_id, description=description,
        persuasive_function=persuasive_function, bbox=bbox, role=role,
        **overrides,
    )


def _four_bubbles():
    return [
        _other_text_obj("obj_01", "DM bubble asking if it works on stretch marks",
                         "raises a doubt about efficacy", [0.1, 0.1, 0.5, 0.1]),
        _other_text_obj("obj_02", "DM bubble replying yes, within two weeks",
                         "answers the doubt with a benefit claim", [0.4, 0.25, 0.5, 0.1]),
        _other_text_obj("obj_03", "DM bubble asking about the smell",
                         "raises a second, different doubt", [0.1, 0.4, 0.5, 0.1]),
        _other_text_obj("obj_04", "DM bubble replying it smells like fresh citrus",
                         "answers the second doubt with a sensory detail", [0.4, 0.55, 0.5, 0.1]),
    ]


# ---- text_objects_needing_copy: identifies exactly the right candidates ----

def test_text_objects_needing_copy_finds_all_four_bubbles():
    bp = {"objects": _four_bubbles()}
    candidates = generate_copy.text_objects_needing_copy(bp)
    assert {c["object_id"] for c in candidates} == {"obj_01", "obj_02", "obj_03", "obj_04"}


def test_text_objects_needing_copy_empty_for_a_headline_only_blueprint():
    # "a blueprint with one text object is unaffected" - a single HEADLINE-purposed
    # object already has its own dedicated content source (rule 6 TEXT POLICY) and must
    # never be treated as an object-copy candidate.
    bp = {"objects": [_text_obj("headline", disposition="substitute")]}
    assert generate_copy.text_objects_needing_copy(bp) == []


def test_text_objects_needing_copy_excludes_non_substitute_disposition():
    bp = {"objects": [_other_text_obj("obj_01", "a bubble", "some job", [0, 0, 0.1, 0.1],
                                       disposition="drop")]}
    assert generate_copy.text_objects_needing_copy(bp) == []


def test_text_objects_needing_copy_excludes_recognised_purposes():
    bp = {"objects": [
        _text_obj("headline", object_id="obj_01", disposition="substitute"),
        _text_obj("testimonial", object_id="obj_02", disposition="substitute"),
        _other_text_obj("obj_03", "a bubble", "some job", [0, 0, 0.1, 0.1]),
    ]}
    candidates = generate_copy.text_objects_needing_copy(bp)
    assert {c["object_id"] for c in candidates} == {"obj_03"}


def test_text_objects_needing_copy_none_purpose_treated_same_as_other():
    # A text object with NO text_purpose key at all (legacy, or the model genuinely
    # omitted it) reaches the exact same content-free fallback in
    # _substitute_object_line as an explicit "other" - text_objects_needing_copy must
    # catch it too, not just the literal string "other".
    bp = {"objects": [_obj(object_id="obj_01", kind="text", description="a bubble",
                            disposition="substitute")]}
    assert "text_purpose" not in bp["objects"][0]
    candidates = generate_copy.text_objects_needing_copy(bp)
    assert {c["object_id"] for c in candidates} == {"obj_01"}


# ---- _object_copy_clause: reading order, single-vs-multi wording, empty case ----

def test_object_copy_clause_empty_when_no_candidates():
    assert generate_copy._object_copy_clause([]) == ""


def test_object_copy_clause_lists_all_four_object_ids_in_reading_order():
    clause = generate_copy._object_copy_clause(_four_bubbles())
    for oid in ("obj_01", "obj_02", "obj_03", "obj_04"):
        assert f'object_id "{oid}"' in clause
    # Reading order (top-to-bottom from each bbox's own y) must be preserved, not
    # blueprint list order - _four_bubbles() is already in order, so this also holds
    # for a deliberately out-of-order input.
    shuffled = list(reversed(_four_bubbles()))
    clause2 = generate_copy._object_copy_clause(shuffled)
    pos1 = clause2.index('object_id "obj_01"')
    pos4 = clause2.index('object_id "obj_04"')
    assert pos1 < pos4
    assert "EXACTLY 4 objects" in clause2


def test_object_copy_clause_single_candidate_has_no_repeat_rule():
    one = [_other_text_obj("obj_01", "a single bubble", "makes one point", [0, 0, 0.5, 0.1])]
    clause = generate_copy._object_copy_clause(one)
    assert "EXACTLY 1 objects" in clause
    assert "never repeat the same phrase across objects" not in clause


def test_object_copy_clause_multi_candidate_states_job_inheritance_rule():
    clause = generate_copy._object_copy_clause(_four_bubbles())
    assert "Inherit the JOB" in clause
    assert "never its WORDING" in clause
    assert "never repeat the same phrase across objects" in clause


def test_object_copy_clause_redacts_personal_attribution():
    # The clause's OWN boilerplate rule text legitimately names "Sean R." as an example
    # of a shape to never write - so this checks the specific combination that would
    # only appear if the OBJECT's own description leaked through unredacted, not the
    # bare substring "Sean R." (which the rule text contains on purpose).
    bubble = _other_text_obj("obj_01", "DM bubble signed Sean R.", "endorses the product",
                              [0, 0, 0.5, 0.1])
    clause = generate_copy._object_copy_clause([bubble])
    assert "signed Sean R." not in clause
    assert "the reference shows - DM bubble signed" in clause


# ---- build_copy_prompt: object copy coexists with angle language, never replaces it ----

_ANGLE_LANGUAGE = {
    "common_phrases": ["my skin feels so tight and dry"],
    "core_angle": "ageing skin feels tight",
    "main_pain_point": "dryness",
}


def test_build_copy_prompt_object_copy_coexists_with_angle_language():
    # "angle language rules still hold per object" - TIER 1/2/3's governing text must
    # still be present, byte-for-byte, alongside the new OBJECT COPY section - one must
    # never replace or weaken the other.
    bp = {"objects": _four_bubbles()}
    prompt = generate_copy.build_copy_prompt(bp, angle_language=_ANGLE_LANGUAGE)
    assert "TIER 1 - WRITE FROM THIS (REQUIRED)" in prompt
    assert "TIER 2 - TONE ONLY, NEVER EMIT" in prompt
    assert "No statistic and no timeframe" in prompt
    assert "my skin feels so tight and dry" in prompt
    assert "OBJECT COPY (STRICT)" in prompt
    assert 'object_id "obj_01"' in prompt


def test_build_copy_prompt_no_object_copy_section_without_candidates():
    bp = {"objects": [_text_obj("headline", disposition="substitute")]}
    prompt = generate_copy.build_copy_prompt(bp, angle_language=_ANGLE_LANGUAGE)
    assert "OBJECT COPY" not in prompt
    assert "TIER 1 - WRITE FROM THIS (REQUIRED)" in prompt


# ---- validate_copy: mechanical backstop for object_copy completeness ----

def test_validate_copy_accepts_four_distinct_object_copy_entries():
    copy = {
        "headline": "H", "primary_text": "P", "cta": "C",
        "object_copy": [
            {"object_id": "obj_01", "text": "Does it help with stretch marks?"},
            {"object_id": "obj_02", "text": "Most see softer skin within two weeks."},
            {"object_id": "obj_03", "text": "What's the scent like?"},
            {"object_id": "obj_04", "text": "A light, fresh citrus."},
        ],
    }
    generate_copy.validate_copy(copy, required_object_ids={"obj_01", "obj_02", "obj_03", "obj_04"})


def test_validate_copy_raises_when_object_copy_missing_entirely():
    copy = {"headline": "H", "primary_text": "P", "cta": "C"}
    try:
        generate_copy.validate_copy(copy, required_object_ids={"obj_01"})
        assert False, "expected ValueError"
    except ValueError as e:
        assert "obj_01" in str(e)


def test_validate_copy_raises_when_object_copy_missing_some_ids():
    copy = {"headline": "H", "primary_text": "P", "cta": "C",
            "object_copy": [{"object_id": "obj_01", "text": "Yes it does."}]}
    try:
        generate_copy.validate_copy(copy, required_object_ids={"obj_01", "obj_02"})
        assert False, "expected ValueError"
    except ValueError as e:
        assert "obj_02" in str(e)


def test_validate_copy_raises_when_object_copy_entry_is_empty():
    copy = {"headline": "H", "primary_text": "P", "cta": "C",
            "object_copy": [{"object_id": "obj_01", "text": "   "}]}
    try:
        generate_copy.validate_copy(copy, required_object_ids={"obj_01"})
        assert False, "expected ValueError"
    except ValueError as e:
        assert "obj_01" in str(e)


def test_validate_copy_ignores_object_copy_when_nothing_required():
    # No candidates this run - object_copy absent must not raise, byte-identical to
    # before this feature existed.
    copy = {"headline": "H", "primary_text": "P", "cta": "C"}
    generate_copy.validate_copy(copy)  # must not raise


# ---- find_object_copy_collisions: distinctness enforced in CODE ----

def test_find_object_copy_collisions_none_for_four_distinct_strings():
    objects = _four_bubbles()
    object_copy = [
        {"object_id": "obj_01", "text": "Does it help with stretch marks?"},
        {"object_id": "obj_02", "text": "Most see softer skin within two weeks."},
        {"object_id": "obj_03", "text": "What's the scent like?"},
        {"object_id": "obj_04", "text": "A light, fresh citrus."},
    ]
    assert generate_copy.find_object_copy_collisions(objects, object_copy) == []


def test_find_object_copy_collisions_detects_defect_for_different_inputs():
    # The exact live-failure shape: four objects with genuinely DIFFERENT description/
    # persuasive_function all resolving to the identical generated text.
    objects = _four_bubbles()
    same_line = "Love it - it absorbs very well into the skin !!"
    object_copy = [{"object_id": o["object_id"], "text": same_line} for o in objects]
    collisions = generate_copy.find_object_copy_collisions(objects, object_copy)
    assert len(collisions) == 6  # every pair among 4 objects: 4 choose 2
    assert all(c["text"] == same_line for c in collisions)


def test_find_object_copy_collisions_allows_shared_line_for_identical_inputs():
    # "two objects with identical purpose and identical description may share a line" -
    # the rule is distinctness-by-object, not forced variation: two objects that
    # genuinely describe the SAME thing producing the SAME line is not a defect.
    objects = [
        _other_text_obj("obj_01", "a repeated small badge reading 'New'", "flags novelty",
                         [0.1, 0.1, 0.1, 0.05]),
        _other_text_obj("obj_02", "a repeated small badge reading 'New'", "flags novelty",
                         [0.7, 0.1, 0.1, 0.05]),
    ]
    object_copy = [
        {"object_id": "obj_01", "text": "New"},
        {"object_id": "obj_02", "text": "New"},
    ]
    assert generate_copy.find_object_copy_collisions(objects, object_copy) == []


def test_find_object_copy_collisions_ignores_empty_or_missing_entries():
    objects = _four_bubbles()
    object_copy = [{"object_id": "obj_01", "text": "Real line."},
                   {"object_id": "obj_02", "text": ""},
                   {"object_id": "obj_03"}]
    assert generate_copy.find_object_copy_collisions(objects, object_copy) == []


def test_find_object_copy_collisions_empty_inputs_never_raise():
    assert generate_copy.find_object_copy_collisions([], []) == []
    assert generate_copy.find_object_copy_collisions(None, None) == []
