"""Tests for the image-prompt generator (no image API call)."""
import logging

from src import generate_image_prompt, generate_image_prompt_writer


def _blueprint():
    return {
        "visual": {
            "layout": "portrait, subject centered",
            "subject": "woman applying oil",
            "palette_mood": "warm golden tones",
            "text_placement": "lower third",
        }
    }


def test_prompt_includes_visual_details():
    prompt = generate_image_prompt.build_image_prompt(_blueprint())
    assert "warm golden tones" in prompt
    assert "portrait, subject centered" in prompt


def test_prompt_mentions_besque_and_avoids_competitor():
    prompt = generate_image_prompt.build_image_prompt(_blueprint())
    assert "Besque" in prompt
    assert "no competitor branding" in prompt


def test_prompt_handles_missing_visual_gracefully():
    prompt = generate_image_prompt.build_image_prompt({})
    assert isinstance(prompt, str)
    assert len(prompt) > 20


# ---- Text volume: subtext hard-capped so a copy step that ignores its own "under ~12
# words" instruction can't blow rule 6's in-image text budget ----

def test_cap_subtext_truncates_overlong_text():
    long_text = " ".join(f"word{i}" for i in range(30))
    capped = generate_image_prompt._cap_subtext(long_text)
    assert len(capped.split()) == generate_image_prompt.MAX_SUBTEXT_WORDS


def test_cap_subtext_leaves_short_text_unchanged():
    short_text = "7 oils. Deeper hydration. Visibly firmer skin."
    assert generate_image_prompt._cap_subtext(short_text) == short_text


def test_cap_subtext_handles_falsy_input():
    assert generate_image_prompt._cap_subtext(None) is None
    assert generate_image_prompt._cap_subtext("") == ""


def test_effective_authorised_text_caps_subtext():
    long_text = " ".join(f"word{i}" for i in range(30))
    _, capped = generate_image_prompt.effective_authorised_text(True, "Headline", long_text)
    assert len(capped.split()) == generate_image_prompt.MAX_SUBTEXT_WORDS


def test_rule6_text_in_image_states_entire_text_budget():
    rule6 = generate_image_prompt._rule6_text_policy(True, "Headline", "Short line.")
    assert "ENTIRE text budget for this image" in rule6
    assert "ingredient list" in rule6
    assert "mechanism or benefit paragraph" in rule6
    assert "CTA sentence" in rule6


def test_rule6_no_text_in_image_unaffected_by_budget_wording():
    rule6 = generate_image_prompt._rule6_text_policy(False)
    assert "ENTIRE text budget" not in rule6


def test_rule6_caps_overlong_subtext_end_to_end():
    long_text = " ".join(f"word{i}" for i in range(30))
    rule6 = generate_image_prompt._rule6_text_policy(True, "Headline", long_text)
    assert "word11" in rule6
    assert "word12" not in rule6


def test_prompt_includes_compliance_rules():
    prompt = generate_image_prompt.build_image_prompt(_blueprint())
    assert "C1. NO REAL PEOPLE" in prompt
    assert "C6. NO SEXUALIZED FRAMING" in prompt
    # Existing rules 6/7 must still be present, unmodified, not replaced by the new rules.
    assert "TEXT POLICY (STRICT)" in prompt
    assert "PRODUCT POLICY (STRICT)" in prompt


# ---- C7 (2026-08-13 evening): a live draft reproduced "170 lbs Start", "130 lbs on
# GLP-1 (Finished)", "180 lbs Rebound", "Still 130 lbs, healthier skin" verbatim, with
# the Besque bottle at the endpoint - asserting a body oil maintained a 40lb loss. The
# text survived through typography_zones, which only ever governs STYLING and
# explicitly defers content to "elsewhere" - when no structural_zones entry exists for
# a caption shape that doesn't fit any of the 9 known zone types, nothing ever assigns
# it a fate, so it falls through to the default reproduce-faithfully language
# unchanged. C7 closes this by naming the categories directly and stating the default
# for an ungoverned zone is REMOVE, not reproduce - regardless of which path (typography
# or structural) the content came through. ----

def test_prompt_includes_c7_weight_and_treatment_rule():
    prompt = generate_image_prompt.build_image_prompt(_blueprint())
    assert "C7. NO WEIGHT OR TREATMENT TEXT IN-IMAGE" in prompt
    assert "170 lbs" in prompt  # the example, not a hardcoded live value
    assert "GLP-1" in prompt and "Ozempic" in prompt and "semaglutide" in prompt
    assert "regardless of which zone the text sits in" in prompt
    assert "is REMOVED, never left showing the" in prompt


def test_c7_does_not_contradict_c5_glp1_angle_permission():
    """C5 explicitly permits referencing GLP-1 AS CONTEXT for a skin concern - C7 must
    state it governs literal in-image TEXT/brand-name rendering specifically, never
    read as banning the angle itself, or the two rules would contradict."""
    prompt = generate_image_prompt.build_image_prompt(_blueprint())
    assert "even on an approved GLP-1-context messaging angle" in prompt
    assert "the angle may be referenced in writing" in prompt


def test_c7_reaches_edit_mode_and_writer_paths_too():
    edit_mode_prompt = generate_image_prompt.build_image_prompt(_blueprint(), edit_mode=True)
    writer_prompt = generate_image_prompt.build_image_prompt(
        _blueprint(), creative_description="A scene."
    )
    assert "C7. NO WEIGHT OR TREATMENT TEXT IN-IMAGE" in edit_mode_prompt
    assert "C7. NO WEIGHT OR TREATMENT TEXT IN-IMAGE" in writer_prompt


# ---- C8 (2026-08-13 evening, item 6 narrow): "formulated with natural ingredients"
# in copy/in-image text with products.hero_claim blank and nothing supplied to
# substantiate it. Checked against C3's own wording so the two don't overlap. ----

def test_prompt_includes_c8_ingredient_formulation_rule():
    prompt = generate_image_prompt.build_image_prompt(_blueprint())
    assert "C8. NO UNSUBSTANTIATED INGREDIENT OR FORMULATION CLAIMS" in prompt
    assert "formulated with natural ingredients" in prompt


def test_c8_explicitly_distinguishes_itself_from_c3s_exception():
    """Must state the boundary explicitly - "deeply hydrating"/"improves skin texture"
    stay acceptable under C3, unchanged - or C8 would read as silently narrowing C3."""
    prompt = generate_image_prompt.build_image_prompt(_blueprint())
    assert "remain acceptable under C3, unchanged" in prompt
    assert "does not cover, a claim about what the product is COMPOSED of" in prompt


def test_c8_reaches_edit_mode_and_writer_paths_too():
    edit_mode_prompt = generate_image_prompt.build_image_prompt(_blueprint(), edit_mode=True)
    writer_prompt = generate_image_prompt.build_image_prompt(
        _blueprint(), creative_description="A scene."
    )
    assert "C8. NO UNSUBSTANTIATED INGREDIENT OR FORMULATION CLAIMS" in edit_mode_prompt
    assert "C8. NO UNSUBSTANTIATED INGREDIENT OR FORMULATION CLAIMS" in writer_prompt


# ---- C9 (2026-08-13, item 2 sharpened; extended 2026-08-13 evening for social
# handles/account chrome): a personal name, handle, or account identity borrowed from
# the reference must never appear in Besque's output. ----

def test_prompt_includes_c9_borrowed_attribution_rule():
    prompt = generate_image_prompt.build_image_prompt(_blueprint())
    assert "C9. NO BORROWED PERSONAL ATTRIBUTION OR ACCOUNT IDENTITY" in prompt
    assert "@fitness_ty" in prompt  # the example, not a hardcoded live value


def test_c9_covers_account_chrome_and_avatar_faces():
    prompt = generate_image_prompt.build_image_prompt(_blueprint())
    assert "account chrome" in prompt
    assert "avatar's face is a depicted person" in prompt
    assert "bound by C1 and rule 10" in prompt


def test_c9_reaches_edit_mode_and_writer_paths_too():
    edit_mode_prompt = generate_image_prompt.build_image_prompt(_blueprint(), edit_mode=True)
    writer_prompt = generate_image_prompt.build_image_prompt(
        _blueprint(), creative_description="A scene."
    )
    assert "C9. NO BORROWED PERSONAL ATTRIBUTION OR ACCOUNT IDENTITY" in edit_mode_prompt
    assert "C9. NO BORROWED PERSONAL ATTRIBUTION OR ACCOUNT IDENTITY" in writer_prompt


def test_person_clause_explicitly_covers_avatar_faces_in_chrome():
    """The general PERSON instruction (edit mode) must name avatar/profile-picture
    faces explicitly, not rely on "any person" being read broadly enough on its own -
    the whole point of this fix is that a nearby, more specific instruction (the
    social_proof card-styling clause) can otherwise win by default."""
    prompt = generate_image_prompt.build_image_prompt(_blueprint(), edit_mode=True)
    assert "face inside a small avatar or profile picture" in prompt
    assert "not only the ad's primary" in prompt


# ---- Account chrome carve-out on the social_proof testimonial card DELETED 2026-08-17:
# it lived entirely inside _structural_zones_clause's social_proof branch, which no
# longer exists - structural_zones/testimonial_zones are replaced by blueprint.objects
# (see schema/blueprint.schema.json, generate_image_prompt._objects_clause). The two
# tests that used to cover this (present-when-styling-supplied, absent-when-not) tested
# prompt text that no longer exists - the second one had already degraded to a
# vacuously-passing test (asserting an absence nothing could ever produce any more)
# rather than continuing to test anything real, so both were removed rather than left
# misleading. PERSON's own avatar/profile-picture coverage (test above this comment)
# is unaffected - that's a different, still-live mechanism. ----


def test_prompt_never_leaks_visual_subject():
    """Regression guard for the Rule C1 tension: visual.subject is where the vision step
    puts identity-carrying descriptions of the competitor's model (see deconstruct.py real
    data) - it must never reach the image-generation prompt verbatim."""
    bp = _blueprint()
    bp["visual"]["subject"] = "Blonde athletic woman 40+ in dark bikini, visibly muscular physique"
    prompt = generate_image_prompt.build_image_prompt(bp)
    assert "Blonde athletic woman" not in prompt
    assert "bikini" not in prompt


def test_prompt_has_defensive_clause_near_layout():
    """The layout field IS forwarded into the prompt, so the compliance override for
    whatever it might imply about a person must sit right next to it, not just be
    stated once somewhere earlier in a long prompt."""
    prompt = generate_image_prompt.build_image_prompt(_blueprint())
    layout_pos = prompt.index("portrait, subject centered")
    nearby = prompt[layout_pos:layout_pos + 300]
    assert "generic, non-identifiable model" in nearby


def test_illustrated_production_style_has_its_own_guidance_not_the_default():
    """glp1's seeded default_realism is "illustrated" - this is the prerequisite check
    that build_image_prompt gives it real guidance rather than silently falling through
    to DEFAULT_STYLE_GUIDANCE (which would happen for any unrecognized/missing style)."""
    bp = _blueprint()
    bp["production_style"] = {"style": "illustrated"}
    prompt = generate_image_prompt.build_image_prompt(bp)
    assert generate_image_prompt_writer.STYLE_GUIDANCE["illustrated"] in prompt
    assert generate_image_prompt.DEFAULT_STYLE_GUIDANCE not in prompt


def test_plain_template_branch_explicit_realism_wins_over_blueprint_style():
    """Line 369's STYLE_GUIDANCE lookup used to read prod_style unconditionally, ignoring
    any explicit realism argument entirely - the one branch (no edit_mode, no
    creative_description) that didn't match line 329's own precedence. An explicit realism
    must win over the blueprint's own detected production_style here too, same as edit
    mode - never a different resolution order for this branch."""
    bp = _blueprint()
    bp["production_style"] = {"style": "high_spec"}
    prompt = generate_image_prompt.build_image_prompt(bp, realism="illustrated")
    assert generate_image_prompt_writer.STYLE_GUIDANCE["illustrated"] in prompt
    assert generate_image_prompt_writer.STYLE_GUIDANCE["high_spec"] not in prompt


def test_plain_template_branch_falls_back_to_blueprint_style_when_realism_omitted():
    """Companion to the override test above - confirms the fallback leg of the same
    precedence still works when realism is None (today's existing, unchanged behaviour)."""
    bp = _blueprint()
    bp["production_style"] = {"style": "ugc"}
    prompt = generate_image_prompt.build_image_prompt(bp, realism=None)
    assert generate_image_prompt_writer.STYLE_GUIDANCE["ugc"] in prompt


def test_style_guidance_has_every_canonical_style():
    """Mirrors the module-level assertion in generate_image_prompt_writer.py - a schema
    addition to validator.production_styles() can't silently ship without matching
    guidance text, for any of STYLE_GUIDANCE's three consumers (writer, edit mode, and
    this flat-template branch). Full equality, not just subset (2026-08-11): the enum was
    tightened the same session (ugc_native/high_spec_studio renamed, hybrid dropped) - an
    orphaned STYLE_GUIDANCE key that no longer matches any enum value is exactly the drift
    this test exists to catch, in either direction."""
    from src import validator
    assert set(validator.production_styles()) == set(generate_image_prompt_writer.STYLE_GUIDANCE)


# ---- Part 4: conditional brand_rules() ----

def test_brand_rules_default_reproduces_prior_rules_verbatim():
    """brand_rules(), called with defaults, must reproduce every character of the old flat
    BRAND_RULES constant through rule 7. OLD_BRAND_RULES_THROUGH_RULE_7 below is a plain
    string literal copied from the file as it existed before the constant->function
    refactor - it is NOT imported from or derived from generate_image_prompt in any way,
    so this can't pass by comparing the code to itself."""
    from src.generate_image_prompt import (
        brand_rules, _RULE_8_LAYOUT_IS_COMPOSITION, _RULE_10_SUBJECT_AGE,
        _RULE_11_SKIN_TEXTURE_REALISM,
    )
    from src.compliance_rules import COMPLIANCE_RULES

    OLD_BRAND_RULES_THROUGH_RULE_7 = (
        "STRICT RULES - NEVER VIOLATE: "
        "1) Any Besque bottle label must show ONLY the exact product name provided, nothing else. "
        "2) NEVER copy the competitor's product name, brand name, claims, or any label text onto the Besque product. "
        "3) NEVER invent ingredients, percentages, or product names. "
        "4) If no product name is provided, the bottle shows only the word 'Besque'. "
        "5) The product is always a body OIL in a glass bottle unless stated otherwise - never a cream, jar, or tub. "
        "6) TEXT POLICY (STRICT): the Besque product's own printed label — exactly as shown on the reference product photo — is the ONLY text permitted anywhere in the image. NEVER render any headline, price, discount, percentage, offer, badge, sticker, sticky note, caption, tagline, watermark, or extra logo, whether copied from the competitor ad or invented. "
        "7) PRODUCT POLICY (STRICT): the single product in the reference product photo is the ONLY product permitted anywhere in the image — exactly one bottle, and it is that one. If no reference product photo is supplied, exactly one Besque bottle matching the product description is permitted. A multi-product range, collection, bundle, gift set or line-up in the source ad is a layout to borrow, not an inventory to reproduce: keep its composition, lighting and mood, collapse it to a single-product composition, and leave the freed area as clean negative space. NEVER add a second bottle, a variant, a size sibling, a refill, a carton, a box, or any further SKU, whether copied from the competitor ad or invented. "
    )

    result = brand_rules()  # all defaults

    assert result.startswith(OLD_BRAND_RULES_THROUGH_RULE_7)
    assert result.endswith(COMPLIANCE_RULES)
    # Pins down the ENTIRE string: the only things brand_rules() adds beyond the old
    # verbatim text are rule 8, rule 10 (SUBJECT AGE, 2026-08-11), and rule 11 (SKIN
    # TEXTURE REALISM, 2026-08-12) - both 10 and 11 unconditional, see their own docstring
    # for why they're positioned after the edit-mode-only rule 9 rather than renumbering
    # it - in exactly this position, no reordering, no extra content.
    assert result == (OLD_BRAND_RULES_THROUGH_RULE_7 + _RULE_8_LAYOUT_IS_COMPOSITION
                       + _RULE_10_SUBJECT_AGE + _RULE_11_SKIN_TEXTURE_REALISM + COMPLIANCE_RULES)


def test_rule7_relaxes_when_include_product_false():
    from src.generate_image_prompt import brand_rules
    default = brand_rules(include_product=True)
    productless = brand_rules(include_product=False)
    assert "the single product in the reference product photo is the ONLY product" in default
    assert "the single product in the reference product photo is the ONLY product" not in productless
    assert "PRODUCTLESS MODE" in productless
    assert "NO Besque bottle, product, label, or branding of any kind may appear" in productless


def test_rule6_allows_named_headline_when_text_in_image_true():
    from src.generate_image_prompt import brand_rules
    result = brand_rules(text_in_image=True, headline="Firmer Skin By Friday", subtext="7 cold-pressed oils")
    assert "TEXT-IN-IMAGE MODE" in result
    assert '"Firmer Skin By Friday"' in result
    assert '"7 cold-pressed oils"' in result
    # Still forbids everything else - only the named copy is allowed in.
    assert "NEVER render any price, discount, percentage" in result


def test_rule6_falls_back_to_default_when_text_in_image_true_but_no_headline():
    """text_in_image=True with nothing confirmed to render must NOT open the door -
    falls back to the same blanket ban as the default."""
    from src.generate_image_prompt import brand_rules, _rule6_text_policy
    assert _rule6_text_policy(text_in_image=True, headline=None) == _rule6_text_policy(text_in_image=False)


def test_rule8_layout_is_composition_present():
    """Regression guard for the real "Stacked HeadLine" bug: a layout descriptor's own
    words must never be rendered as literal image text."""
    from src.generate_image_prompt import brand_rules
    result = brand_rules()
    assert "LAYOUT DESCRIPTORS ARE COMPOSITION, NOT TEXT" in result
    assert "'headline'" in result and "'stacked'" in result


# ---- Rule 10: SUBJECT AGE (2026-08-11) - a brand constant, not a detection field. Must
# fire on EVERY generation path (flat template, writer/creative_description, edit mode),
# unlike rule 9 which is edit-mode-only. ----

def test_rule10_subject_age_present_by_default():
    """2026-08-12: rule 10 rewritten for specificity (item 3) - "must NOT be inherited"
    became "age is never one of the reproduced/matched attributes... with no exception",
    stated as winning over any competing age/appearance instruction elsewhere."""
    from src.generate_image_prompt import brand_rules
    result = brand_rules()
    assert "10) SUBJECT AGE" in result
    assert "45-60" in result
    assert "age is never one of the reproduced/matched attributes" in result
    assert "OVERRIDES ANY OTHER" in result


def test_rule10_subject_age_present_in_edit_mode_alongside_rule_9():
    """Additive to rule 9, not a replacement - both must be present in edit mode."""
    from src.generate_image_prompt import brand_rules
    result = brand_rules(edit_mode=True)
    assert "10) SUBJECT AGE" in result
    assert "9) SOURCE IMAGE IS THE COMPETITOR'S OWN AD" in result


def test_rule10_subject_age_reaches_flat_template_branch():
    """build_image_prompt's flat-template branch (no edit_mode, no creative_description)
    must carry rule 10 - it's inside brand_rules(), called unconditionally in every branch."""
    prompt = generate_image_prompt.build_image_prompt(_blueprint())
    assert "SUBJECT AGE" in prompt
    assert "45-60" in prompt


def test_rule10_subject_age_reaches_edit_mode_branch():
    prompt = generate_image_prompt.build_image_prompt(_blueprint(), edit_mode=True)
    assert "SUBJECT AGE" in prompt
    assert "45-60" in prompt


# ---- 2026-08-13 evening: rule 10 rewritten so the visible FEATURES are the primary,
# mandatory instruction (a number alone was reading as youthful-40s) - grey/silver hair
# is now REQUIRED, not "may show natural greying". The numeric bracket survives only as
# a secondary anchor. Also drops the hardcoded ad_id that was previously embedded
# directly in the rule text sent to the model on every generation (CLAUDE.md: "No
# ad_id... in src/"). ----

def test_rule10_requires_grey_or_silver_hair_not_optional():
    result = generate_image_prompt.brand_rules()
    assert "GREY OR SILVER HAIR" in result
    assert "hair that may show natural greying" not in result
    assert "never a uniform, fully-pigmented youthful" in result


def test_rule10_requires_visible_lines_and_mature_texture_as_primary():
    result = generate_image_prompt.brand_rules()
    assert "VISIBLE FACIAL LINES" in result
    assert "MATURE SKIN TEXTURE" in result
    assert "PRIMARY, MANDATORY specification of age" in result
    # the numeric bracket is explicitly demoted to secondary, not removed
    assert "SECONDARY anchor, not the headline of this rule" in result
    assert "45-60" in result


def test_rule10_independence_from_reference_covers_hair_and_texture_specifically():
    """Not just the numeric bracket - the reference's own model's actual hair colour
    and skin smoothness must never excuse rendering less grey hair or fewer lines."""
    result = generate_image_prompt.brand_rules()
    assert "applies SPECIFICALLY to hair colour and skin texture, not only to the numeric bracket" in result
    assert "is never a reason to render less grey hair, fewer lines, or smoother skin" in result


def test_rule10_no_longer_hardcodes_an_ad_id():
    result = generate_image_prompt.brand_rules()
    assert "1986367985280315" not in result


# ---- The catch-all "everything else carries over exactly" language must except a
# substituted competitor element too, or the substitution instruction gets
# contradicted by the reproduce-faithfully language later in the same prompt (the same
# failure shape already fixed for PERSON/competitor-branding/prop-scale) ----

# ---- _objects_clause's empty-objects early return must LOG at ERROR (2026-08-17) -
# this used to be silent: a legacy blueprint with no `objects` key skipped the whole
# objects model (substitute/keep/drop lines, the closure sentence, resolve_disposition)
# with nothing in any log naming why. ----

def test_objects_clause_missing_objects_logs_error_with_ad_id(caplog):
    with caplog.at_level(logging.ERROR, logger="generate_image_prompt"):
        clause = generate_image_prompt._objects_clause(None, {}, ad_id="AD123")
    assert clause == ""
    error_records = [r for r in caplog.records if r.levelname == "ERROR"]
    assert error_records, "expected an ERROR log record when objects is missing"
    assert any("AD123" in r.getMessage() for r in error_records)
    assert any("objects" in r.getMessage() for r in error_records)


def test_objects_clause_empty_list_also_logs_error(caplog):
    with caplog.at_level(logging.ERROR, logger="generate_image_prompt"):
        clause = generate_image_prompt._objects_clause([], {}, ad_id="AD456")
    assert clause == ""
    assert any(r.levelname == "ERROR" for r in caplog.records)


def test_objects_clause_no_ad_id_still_logs_error():
    """A caller that doesn't pass ad_id (e.g. an older test) must not raise - the log
    line degrades to naming no ad, it never skips logging or errors on formatting."""
    clause = generate_image_prompt._objects_clause([], {})
    assert clause == ""


def test_objects_clause_non_empty_does_not_log_error(caplog):
    with caplog.at_level(logging.ERROR, logger="generate_image_prompt"):
        clause = generate_image_prompt._objects_clause(
            [{"object_id": "obj_01", "kind": "prop", "description": "a towel",
              "role": "environment", "disposition": "keep"}],
            {}, ad_id="AD789",
        )
    assert clause != ""
    assert not any(r.levelname == "ERROR" for r in caplog.records)


# ---- _substitute_object_line's per-object copy lookup (2026-08-17 restoration) - the
# root-cause fix for the four-DM-bubble bug: a text object with no recognised
# text_purpose must use ITS OWN generated line from context["object_copy_by_id"],
# looked up by object_id, never the shared generic fallback when one is available. ----

def _other_text_object(object_id, description="a bubble"):
    return {"object_id": object_id, "kind": "text", "text_purpose": "other",
            "description": description, "disposition": "substitute"}


def test_substitute_object_line_uses_object_copy_by_id():
    obj = _other_text_object("obj_02", description="DM bubble asking about scent")
    context = {"object_copy_by_id": {
        "obj_01": "Does it help with stretch marks?",
        "obj_02": "What's the scent like?",
    }}
    line = generate_image_prompt._substitute_object_line(obj, "text", "other", obj["description"], context)
    assert "What's the scent like?" in line
    assert "Does it help with stretch marks?" not in line
    assert "Besque's own equivalent content" not in line


def test_substitute_object_line_falls_back_to_generic_when_object_id_not_in_copy():
    obj = _other_text_object("obj_03")
    context = {"object_copy_by_id": {"obj_01": "Some other bubble's line."}}
    line = generate_image_prompt._substitute_object_line(obj, "text", "other", obj["description"], context)
    assert "Besque's own equivalent content" in line


def test_substitute_object_line_falls_back_to_generic_when_no_object_copy_at_all():
    obj = _other_text_object("obj_01")
    line = generate_image_prompt._substitute_object_line(obj, "text", "other", obj["description"], {})
    assert "Besque's own equivalent content" in line


def test_substitute_object_line_object_copy_never_leaks_to_a_different_object_id():
    # Two DIFFERENT objects, two DIFFERENT context entries - each must get its OWN line,
    # never the other's (the exact defect the live four-bubble failure had: every
    # object shared ONE value because nothing was keyed by object_id at all).
    context = {"object_copy_by_id": {"obj_01": "Line for bubble one.",
                                      "obj_02": "Line for bubble two."}}
    line1 = generate_image_prompt._substitute_object_line(
        _other_text_object("obj_01"), "text", "other", "bubble one", context)
    line2 = generate_image_prompt._substitute_object_line(
        _other_text_object("obj_02"), "text", "other", "bubble two", context)
    assert "Line for bubble one." in line1
    assert "Line for bubble two." in line2
    assert "Line for bubble two." not in line1
    assert "Line for bubble one." not in line2


def test_build_image_prompt_threads_object_copy_by_object_id():
    """End-to-end: build_image_prompt's object_copy parameter must reach each text
    object's own SUBSTITUTE line via its object_id - closing the dead-key-shaped gap
    where four DM bubbles previously reused build_image_prompt's shared generic
    fallback because build_image_prompt never had an object_copy parameter at all."""
    bp = {
        "visual": {"layout": "flat lay", "subject": "", "palette_mood": "warm",
                   "text_placement": "lower"},
        "objects": [
            _other_text_object("obj_01", description="DM bubble one"),
            _other_text_object("obj_02", description="DM bubble two"),
        ],
    }
    prompt = generate_image_prompt.build_image_prompt(
        bp, object_copy=[
            {"object_id": "obj_01", "text": "Does it help with stretch marks?"},
            {"object_id": "obj_02", "text": "What's the scent like?"},
        ],
    )
    assert "Does it help with stretch marks?" in prompt
    assert "What's the scent like?" in prompt
    assert "Besque's own equivalent content" not in prompt


def test_build_image_prompt_object_copy_none_keeps_generic_fallback():
    bp = {
        "visual": {"layout": "flat lay", "subject": "", "palette_mood": "warm",
                   "text_placement": "lower"},
        "objects": [_other_text_object("obj_01", description="a bubble")],
    }
    prompt = generate_image_prompt.build_image_prompt(bp)
    assert "Besque's own equivalent content" in prompt


def test_non_carryover_exceptions_clause_excepts_scene_objects():
    # 2026-08-17: repointed at the SCENE OBJECTS inventory (_objects_clause), which
    # subsumed the deleted "COMPETITOR ELEMENTS TO SUBSTITUTE" instruction this
    # exception used to name - a dangling reference to a deleted clause is exactly the
    # contradiction class this codebase has repeatedly hit (see CLAUDE.md).
    clause = generate_image_prompt._non_carryover_exceptions_clause()
    assert "SCENE OBJECTS" in clause
    assert "COMPETITOR ELEMENTS TO SUBSTITUTE" not in clause


def test_edit_mode_opening_excepts_scene_objects_both_retheme_branches():
    on = generate_image_prompt._edit_mode_instruction(retheme_colours=True)
    off = generate_image_prompt._edit_mode_instruction(retheme_colours=False)
    assert "SCENE OBJECTS" in on
    assert "SCENE OBJECTS" in off
    assert "COMPETITOR ELEMENTS TO SUBSTITUTE" not in on
    assert "COMPETITOR ELEMENTS TO SUBSTITUTE" not in off


# ---- _competitor_props_clause DELETED 2026-08-17: folded into
# deconstruct.resolve_disposition (_is_competitor_argument_prop) instead - a prop
# tied to the competitor's own product-category argument now drops via the object's
# own mechanically-resolved disposition, the same single mechanism as every other
# object, never a second uncoordinated clause layered on top. See
# tests/test_objects_schema.py for the replacement coverage. ----


def test_build_image_prompt_default_closing_text_unchanged():
    """The closing paragraph was restructured into a conditional label_clause - prove the
    default (text_in_image=False, include_product=True) still produces the exact original
    wording, character for character."""
    OLD_CLOSING = (
        "Keep the base image completely free of overlaid marketing text — only the Besque product's "
        "own label may appear — and leave clean, uncluttered negative space where headline and offer "
        "text will be added later as a separate HTML overlay; no competitor branding anywhere."
    )
    prompt = generate_image_prompt.build_image_prompt(_blueprint())
    assert prompt.endswith(OLD_CLOSING)


def test_build_image_prompt_productless_mode_omits_product_description():
    product = {"name": "Magic Body Oil", "description": "seven cold-pressed oils",
               "ingredients": "almond; rosehip", "hero_claim": "Visibly firms"}
    prompt = generate_image_prompt.build_image_prompt(_blueprint(), product=product, include_product=False)
    assert "Magic Body Oil" not in prompt
    assert "almond; rosehip" not in prompt
    assert "do not place any Besque product" in prompt
    assert "PRODUCTLESS MODE" in prompt


def test_build_image_prompt_text_in_image_closing_does_not_contradict_rule6():
    """The old closing paragraph unconditionally said "keep completely free of text" -
    left unconditional, it would directly contradict rule 6 once text_in_image renders a
    headline. Confirms that contradiction is gone."""
    prompt = generate_image_prompt.build_image_prompt(_blueprint(), text_in_image=True, headline="Test headline")
    assert "completely free of overlaid marketing text" not in prompt
    assert "will be added later as a separate HTML overlay" not in prompt
    assert "Test headline" in prompt


# ---- Part 5: creative_description (writer output) slots into build_image_prompt ----

def test_creative_description_replaces_template_scene_text():
    """When the writer succeeds, its text replaces the template-assembled scene/
    composition/palette/production-style section - the template's own phrasing must not
    also appear (no double-description)."""
    prompt = generate_image_prompt.build_image_prompt(
        _blueprint(), creative_description="A serene marble bathroom counter at golden hour."
    )
    assert "A serene marble bathroom counter at golden hour." in prompt
    assert "Composition and setting:" not in prompt
    assert "Palette and mood:" not in prompt


# ---- Step 3, Part 3: verification only - visual_description must come straight from
# products.visual_description at generation time, with NO hardcoded fallback string that
# could silently override a future UI correction ----

def test_visual_description_read_from_product_dict_no_hardcoded_override():
    product = {"name": "Magic Body Oil", "description": "seven cold-pressed oils",
               "ingredients": "almond; rosehip", "hero_claim": "Visibly firms",
               "visual_description": "UNUSUAL_MARKER_9f3a: hex bottle, matte black cap"}
    prompt = generate_image_prompt.build_image_prompt(_blueprint(), product=product)
    assert "UNUSUAL_MARKER_9f3a: hex bottle, matte black cap" in prompt


def test_visual_description_absent_produces_no_fixed_appearance_clause():
    """No visual_description supplied -> no fabricated appearance text takes its place -
    confirms there's no hardcoded description hiding behind the missing value."""
    product = {"name": "Magic Body Oil", "description": "seven cold-pressed oils",
               "ingredients": "almond; rosehip", "hero_claim": "Visibly firms"}
    prompt = generate_image_prompt.build_image_prompt(_blueprint(), product=product)
    assert "Its fixed visual appearance:" not in prompt


def test_product_desc_no_longer_duplicates_visual_description():
    """2026-08-13 evening: removed from product_desc entirely, now that
    _bottle_identity_clause states it earlier with STRICT weight - the literal phrase
    "Its fixed visual appearance:" must never appear anywhere, even WITH a
    visual_description supplied (distinct from the test above, which only proves it
    with none supplied)."""
    product = {"name": "Magic Body Oil", "description": "seven cold-pressed oils",
               "visual_description": "amber glass bottle, gold pump top"}
    prompt = generate_image_prompt.build_image_prompt(_blueprint(), product=product)
    assert "Its fixed visual appearance:" not in prompt
    # the fact itself still reaches the prompt - via _bottle_identity_clause instead
    assert "amber glass bottle, gold pump top" in prompt


def test_product_desc_suppresses_key_claim_line_when_empty():
    product = {"name": "Magic Body Oil", "description": "seven cold-pressed oils", "hero_claim": ""}
    prompt = generate_image_prompt.build_image_prompt(_blueprint(), product=product)
    assert "Key claim:" not in prompt


def test_product_desc_includes_key_claim_line_when_present():
    product = {"name": "Magic Body Oil", "description": "seven cold-pressed oils",
               "hero_claim": "Visibly firms skin"}
    prompt = generate_image_prompt.build_image_prompt(_blueprint(), product=product)
    assert "Key claim: Visibly firms skin." in prompt


def test_creative_description_does_not_remove_guardrails():
    """brand_rules()/compliance and the product's factual visual_description must always
    be present regardless of what the writer returns - the writer only supplies the
    creative middle, never the guardrails around it."""
    product = {"name": "Magic Body Oil", "description": "seven cold-pressed oils",
               "ingredients": "almond; rosehip", "hero_claim": "Visibly firms",
               "visual_description": "amber glass bottle, gold pump top"}
    prompt = generate_image_prompt.build_image_prompt(
        _blueprint(), product=product, creative_description="A calm spa scene."
    )
    assert "A calm spa scene." in prompt
    assert "C1. NO REAL PEOPLE" in prompt  # compliance rules, always present
    assert "TEXT POLICY (STRICT)" in prompt  # rule 6, always present
    assert "amber glass bottle, gold pump top" in prompt  # product's visual_description, forced in
    assert "almond; rosehip" in prompt  # real ingredients, forced in
    assert "Square 1:1 aspect ratio composition." in prompt  # mechanical, always present


def test_creative_description_productless_mode_still_forces_no_product_clause():
    prompt = generate_image_prompt.build_image_prompt(
        _blueprint(), include_product=False, creative_description="An educational skin diagram."
    )
    assert "An educational skin diagram." in prompt
    assert "do not place any Besque product" in prompt


# ---- Ingredient list constrains the product's OWN label only - never scene callouts
# (2026-07-31): a real draft rendered "Almond, Primrose, Rosehip, Geranium, Lavender,
# Vitamin E and Patchouli" as floating labels around the bottle. ----

def test_ingredient_list_states_label_only_purpose():
    product = {"name": "Magic Body Oil", "description": "seven cold-pressed oils",
               "ingredients": "Almond, Primrose, Rosehip, Geranium, Lavender, Vitamin E, Patchouli",
               "hero_claim": "Visibly firms"}
    prompt = generate_image_prompt.build_image_prompt(_blueprint(), product=product)
    assert "Almond, Primrose, Rosehip, Geranium, Lavender, Vitamin E, Patchouli" in prompt
    assert "exists SOLELY to constrain what the product's own label may say" in prompt
    assert "it is not a list of scene elements" in prompt


def test_ingredient_callouts_forbidden_even_with_creative_description():
    """product_clause (which carries the ingredient ban) is always mechanically appended
    regardless of what the writer's creative_description says - this is the path a real
    incident actually went through."""
    product = {"name": "Magic Body Oil", "description": "seven cold-pressed oils",
               "ingredients": "Almond, Primrose, Rosehip", "hero_claim": "Visibly firms"}
    prompt = generate_image_prompt.build_image_prompt(
        _blueprint(), product=product, creative_description="A calm spa scene."
    )
    assert "NEVER render any ingredient name as a separate floating callout" in prompt


def test_no_creative_description_reproduces_default_path():
    """creative_description=None (the default) must be byte-identical to calling without
    the parameter at all - confirms this is purely additive."""
    bp = _blueprint()
    assert (generate_image_prompt.build_image_prompt(bp)
            == generate_image_prompt.build_image_prompt(bp, creative_description=None))


# ---- edit_image: minimal prompt - hard safety rules + bottle-fixed + targeted-edit
# preserve clause only, no body copy/text-policy/offer/layout bloat ----

def test_edit_preserve_clause_states_instruction_and_preserve_everything():
    clause = generate_image_prompt._edit_preserve_clause('make the background warmer')
    assert "Instruction: make the background warmer" in clause
    assert "TARGETED EDIT" in clause
    assert "Preserve EVERY element" in clause
    assert "except for what the instruction below explicitly names" in clause


def test_bottle_fixed_clause_states_fixed_unless_named():
    clause = generate_image_prompt._bottle_fixed_clause()
    assert "geometry, proportions, and label" in clause
    assert "FIXED" in clause
    assert "unless the operator's instruction explicitly names the bottle" in clause


def test_register_lighting_only_clause_states_lighting_adapts_not_geometry():
    clause = generate_image_prompt._register_lighting_only_clause()
    assert "lighting, grading, and finish adapt" in clause
    assert "same bottle, same shape, same label" in clause
    assert "hand-drawn bottle" in clause


# ---- Item 3 (2026-08-13): resolve the lighting contradiction - _bottle_register_clause
# anchored the bottle's shadow/grounding to the reference's own observed facts and
# demanded an exact match, while _bottle_integration_clause mandates a contact/grip
# shadow the reference may never have shown (a floating packshot has no contact point
# to observe a shadow from at all). Fixed by REWORDING, not stacking a new clause: the
# reference's facts inform the scene's CHARACTER; the bottle's own contact/grip shadow
# and grounding defer explicitly to BOTTLE INTEGRATION's actual composition. Also: an
# illustrated register must never read background.light at all (deconstruct.py's
# photographic-only field produced a live "Not applicable - no photographic lighting"
# value for an illustrated reference, which _scene_lighting_facts read as a real fact
# and asserted verbatim) - the drawing treatment for "illustrated" always follows
# _register_lighting_only_clause()'s own style-driven wording instead, unconditionally.
#
# REWIRED 2026-08-17: _bottle_register_clause/_scene_lighting_facts now take the new
# top-level `background` object ({"surface", "colour", "light"}), not the old six-field
# `scene_lighting` dict (light_direction/hardness/shadow_behaviour/colour_temperature/
# grain/depth_of_field) - those sub-fields no longer exist (schema/blueprint.schema.json,
# the objects-array refactor). `light` is one free-text phrase now; the tests below were
# updated to the new shape, not just renamed. ----

_REAL_BACKGROUND = {
    "light": "light falls from upper-left, slightly behind camera, soft with long "
             "shadows falling right",
}


def test_bottle_register_clause_falls_back_to_generic_when_no_facts():
    clause = generate_image_prompt._bottle_register_clause({})
    assert clause == generate_image_prompt._register_lighting_only_clause()


def test_bottle_register_clause_states_scene_character_not_exact_bottle_match():
    clause = generate_image_prompt._bottle_register_clause(_REAL_BACKGROUND)
    assert "light falls from upper-left" in clause
    assert "SCENE's overall lighting character" in clause
    assert "must match these observed facts about THIS scene EXACTLY" not in clause


def test_bottle_register_clause_defers_contact_shadow_to_bottle_integration():
    clause = generate_image_prompt._bottle_register_clause(_REAL_BACKGROUND)
    assert "does NOT govern the bottle's own contact or grip shadow" in clause
    assert "BOTTLE INTEGRATION" in clause
    assert "floating product with no" in clause


def test_bottle_register_clause_keeps_reference_photo_lighting_exclusion():
    clause = generate_image_prompt._bottle_register_clause(_REAL_BACKGROUND)
    assert "separate, unrelated studio lighting the product's own reference photo" in clause


def test_bottle_register_clause_keeps_geometry_fixed_regardless():
    clause = generate_image_prompt._bottle_register_clause(_REAL_BACKGROUND)
    assert "Geometry, proportions, and label stay exactly as stated above regardless" in clause


def test_bottle_register_clause_illustrated_never_reads_scene_lighting_facts():
    """The live bug: an illustrated reference's own background.light can carry a
    "Not applicable" value (deconstruct.py trying to fill a photographic-only field for
    a drawing) - style=="illustrated" must skip _scene_lighting_facts entirely, not
    just fall back when the dict happens to be empty."""
    garbage_background = {"light": "Not applicable - no photographic lighting"}
    clause = generate_image_prompt._bottle_register_clause(garbage_background, style="illustrated")
    assert clause == generate_image_prompt._register_lighting_only_clause()
    assert "Not applicable" not in clause


def test_bottle_register_clause_illustrated_ignores_even_real_facts():
    clause = generate_image_prompt._bottle_register_clause(_REAL_BACKGROUND, style="illustrated")
    assert clause == generate_image_prompt._register_lighting_only_clause()
    assert "upper-left" not in clause


def test_bottle_register_clause_photographic_style_still_uses_real_facts():
    clause = generate_image_prompt._bottle_register_clause(_REAL_BACKGROUND, style="ugc")
    assert "light falls from upper-left" in clause


def test_bottle_register_clause_no_style_given_keeps_old_behaviour():
    """Callers that predate the style param (style=None) must see the same photographic
    treatment as before this fix - only an explicit style=="illustrated" changes
    anything."""
    clause = generate_image_prompt._bottle_register_clause(_REAL_BACKGROUND, style=None)
    assert "light falls from upper-left" in clause


# ---- _scene_lighting_facts (2026-08-17 rewire): the six discrete sub-fields are
# genuinely gone, not reconstructed - this asserts the function states exactly the one
# phrase deconstruct.py records now, nothing invented in its place. ----

def test_scene_lighting_facts_empty_when_no_background():
    assert generate_image_prompt._scene_lighting_facts(None) == ""
    assert generate_image_prompt._scene_lighting_facts({}) == ""
    assert generate_image_prompt._scene_lighting_facts({"surface": "marble"}) == ""


def test_scene_lighting_facts_states_the_one_recorded_phrase():
    facts = generate_image_prompt._scene_lighting_facts(_REAL_BACKGROUND)
    assert facts != ""
    assert "light falls from upper-left" in facts
    assert "OBSERVED SCENE LIGHTING" in facts


class _CapturingGenaiClientForEdit:
    last_contents = None

    def __init__(self, *a, **k):
        self.models = self

    def generate_content(self, model, contents, config=None):
        _CapturingGenaiClientForEdit.last_contents = contents
        part = type("Part", (), {"inline_data": type("Data", (), {"data": b"fake-png-bytes"})()})()
        candidate = type("Candidate", (), {"content": type("Content", (), {"parts": [part]})()})()
        return type("Response", (), {"candidates": [candidate]})()


def _run_edit_image(monkeypatch, tmp_path, instruction="make the headline shorter"):
    monkeypatch.setattr(generate_image_prompt, "genai",
                         type("obj", (), {"Client": _CapturingGenaiClientForEdit}))
    monkeypatch.setattr(generate_image_prompt, "ASSET_DIR", tmp_path)
    generate_image_prompt.edit_image(b"fake-current-bytes", instruction, "AD_EDIT")
    return _CapturingGenaiClientForEdit.last_contents[-1]


def test_edit_image_prompt_contains_compliance_bottle_fixed_and_instruction(monkeypatch, tmp_path):
    prompt = _run_edit_image(monkeypatch, tmp_path, instruction="make the headline shorter")
    assert "BESQUE COMPLIANCE RULES" in prompt
    assert "FIXED" in prompt
    assert "Instruction: make the headline shorter" in prompt


def test_edit_image_prompt_excludes_body_copy_and_layout_bloat(monkeypatch, tmp_path):
    """"ingredient" dropped from this exclusion list 2026-08-13: C8 (compliance_rules.py)
    legitimately mentions it now ("NO UNSUBSTANTIATED INGREDIENT OR FORMULATION
    CLAIMS"), and edit_image's prompt always includes the full COMPLIANCE_RULES
    constant - this was never actually guarding against product_desc's own ingredient-
    list text (which edit_image never builds at all, since it doesn't call
    build_image_prompt), so the check was defensive against the wrong risk. "PRODUCT
    POLICY" below still guards the real concern (rule 7's own bloat leaking in)."""
    prompt = _run_edit_image(monkeypatch, tmp_path)
    for excluded in ("TEXT POLICY", "PRODUCT POLICY", "LAYOUT DESCRIPTORS",
                     "STRICT RULES - NEVER VIOLATE", "OFFER:", "REGISTER:"):
        assert excluded not in prompt


def test_edit_image_prompt_states_aspect_ratio(monkeypatch, tmp_path):
    prompt = _run_edit_image(monkeypatch, tmp_path)
    assert "Output aspect ratio: 1:1." in prompt


# ---- Part 5: generate_image() gates the writer pass on messaging_angle, end to end ----

class _FakeGenaiClient:
    """Stands in for genai.Client so generate_image() can run fully (prompt building,
    stem naming, file write) without a real network call. config is accepted (2026-08-06:
    generate mode now always passes one, to set ImageConfig.image_size) but ignored -
    this fake only cares about contents."""
    def __init__(self, *a, **k):
        self.models = self

    def generate_content(self, model, contents, config=None):
        part = type("Part", (), {"inline_data": type("Data", (), {"data": b"fake-png-bytes"})()})()
        candidate = type("Candidate", (), {"content": type("Content", (), {"parts": [part]})()})()
        return type("Response", (), {"candidates": [candidate]})()


def test_generate_image_calls_writer_only_when_angle_given(monkeypatch, tmp_path):
    monkeypatch.setattr(generate_image_prompt, "genai", type("obj", (), {"Client": _FakeGenaiClient}))
    monkeypatch.setattr(generate_image_prompt, "ASSET_DIR", tmp_path)
    calls = []
    monkeypatch.setattr(
        generate_image_prompt.generate_image_prompt_writer, "write_creative_description",
        lambda *a, **k: calls.append(k) or "Writer-provided scene."
    )

    generate_image_prompt.generate_image({}, "AD_NO_ANGLE", messaging_angle=None)
    assert calls == []  # no angle selected -> writer never invoked

    generate_image_prompt.generate_image(
        {}, "AD_WITH_ANGLE", messaging_angle={"name": "Crepey Skin", "notes": "warm light"},
        realism="ugc_native", body_area="knees", offer_text="20% off",
        reference_images=[b"x", b"y"], text_in_image=True, include_product=False,
        headline="Firmer Skin By Friday", subtext="7 cold-pressed oils",
    )
    assert len(calls) == 1
    assert calls[0]["angle"] == {"name": "Crepey Skin", "notes": "warm light"}
    assert calls[0]["realism"] == "ugc_native"
    assert calls[0]["body_area"] == "knees"
    assert calls[0]["offer_text"] == "20% off"
    assert calls[0]["reference_image_count"] == 2
    # Part A regression guard: the same mode flags brand_rules() enforces mechanically
    # must also reach the writer, not just build_image_prompt.
    assert calls[0]["text_in_image"] is True
    assert calls[0]["include_product"] is False
    assert calls[0]["headline"] == "Firmer Skin By Friday"
    assert calls[0]["subtext"] == "7 cold-pressed oils"
    assert "Writer-provided scene." in generate_image_prompt.generate_image.last_prompt

# ---- Item 2 (2026-08-13 build): bottle identity promoted to a dedicated STRICT
# clause, fed structurally from product.visual_description/substance_colour/
# certifications - never a hardcoded string. Plus a separate integration clause
# (participating object, not a pasted packshot). _bottle_register_clause and the
# material realism clause are untouched - lighting/finish still adapt. ----

_REAL_SHAPED_PRODUCT = {
    "name": "Besque Magic Body Oil",
    "visual_description": (
        "Clear cylindrical bottle filled with bright golden-amber oil. Black pump head "
        "with a chrome top face, mounted on a tall polished gold collar. Terracotta "
        "rust-red label with a gold geometric border band at both top and bottom."
    ),
    "substance_colour": "bright golden-amber oil",
    "certifications": ["Vegan", "Cruelty Free", "100% Natural"],
}


def test_bottle_identity_clause_fires_identically_for_every_production_style():
    """The identity clause does not take a style parameter at all - it's structurally
    invariant by construction. Verified end to end: the SAME identity text appears in
    the assembled prompt regardless of production_style.style."""
    identity_texts = []
    for style in ("ugc", "high_spec", "illustrated"):
        bp = _blueprint()
        bp["production_style"] = {"style": style}
        prompt = generate_image_prompt.build_image_prompt(
            bp, product=_REAL_SHAPED_PRODUCT, edit_mode=True,
        )
        assert "BOTTLE IDENTITY (STRICT" in prompt
        identity_section = prompt.split("BOTTLE IDENTITY (STRICT")[1].split("BOTTLE INTEGRATION")[0]
        identity_texts.append(identity_section)
    assert identity_texts[0] == identity_texts[1] == identity_texts[2]


def test_bottle_identity_clause_fires_identically_across_all_three_branches():
    """Edit mode, writer, and flat-template branches must all carry the identical
    identity clause - it's inserted right after brand_rules() in all three."""
    bp_edit = _blueprint()
    bp_writer = _blueprint()
    bp_flat = _blueprint()
    edit_prompt = generate_image_prompt.build_image_prompt(
        bp_edit, product=_REAL_SHAPED_PRODUCT, edit_mode=True,
    )
    writer_prompt = generate_image_prompt.build_image_prompt(
        bp_writer, product=_REAL_SHAPED_PRODUCT, creative_description="A scene.",
    )
    flat_prompt = generate_image_prompt.build_image_prompt(
        bp_flat, product=_REAL_SHAPED_PRODUCT,
    )
    for prompt in (edit_prompt, writer_prompt, flat_prompt):
        assert "BOTTLE IDENTITY (STRICT" in prompt
        assert "Clear cylindrical bottle filled with bright golden-amber oil" in prompt


def test_bottle_identity_clause_is_fed_structurally_not_hardcoded():
    """Change the product data, the clause's content changes with it - proves this
    isn't a fixed string with the real product's facts baked in."""
    other_product = {
        "name": "Something Else",
        "visual_description": "A short square amber jar with a black screw lid.",
        "substance_colour": "deep amber balm",
        "certifications": ["Organic"],
    }
    prompt = generate_image_prompt.build_image_prompt(
        _blueprint(), product=other_product, edit_mode=True,
    )
    assert "A short square amber jar with a black screw lid." in prompt
    assert "deep amber balm" in prompt
    assert "Organic" in prompt
    assert "Clear cylindrical bottle" not in prompt


def test_bottle_identity_clause_names_substance_colour_and_certifications_explicitly():
    clause = generate_image_prompt._bottle_identity_clause(_REAL_SHAPED_PRODUCT)
    assert "The oil itself is bright golden-amber oil." in clause
    assert "Certification icons present on the label: Vegan, Cruelty Free, 100% Natural." in clause


def test_bottle_identity_clause_falls_back_without_inventing_when_no_product():
    clause = generate_image_prompt._bottle_identity_clause(None)
    assert "must not be invented" in clause
    assert "BOTTLE IDENTITY (STRICT" in clause
    # no specific colour/material fact invented
    assert "golden" not in clause and "terracotta" not in clause.lower()


def test_bottle_identity_and_integration_absent_when_productless():
    bp = _blueprint()
    prompt = generate_image_prompt.build_image_prompt(
        bp, product=_REAL_SHAPED_PRODUCT, edit_mode=True, include_product=False,
    )
    assert "BOTTLE IDENTITY (STRICT" not in prompt
    assert "BOTTLE INTEGRATION (STRICT" not in prompt


def test_bottle_identity_clause_allows_faithful_simplification_never_invented_bottle():
    """2026-08-13 evening: reconciled with STYLE_GUIDANCE["illustrated"] - identity
    states what the label CONTAINS, defers what renders LEGIBLY to the register-
    specific guidance, and only widens the droppable category to SECONDARY content
    (matching that guidance's own list) rather than "fine print only"."""
    clause = generate_image_prompt._bottle_identity_clause(_REAL_SHAPED_PRODUCT)
    assert "This clause states what the label CONTAINS" in clause
    assert "SECONDARY content only" in clause
    assert "wordmark, product name, and colours are NEVER simplified away" in clause
    assert "never a licence to invent a different, generic, or simplified-beyond-recognition bottle" in clause


def test_bottle_identity_clause_defers_legibility_to_register_guidance():
    clause = generate_image_prompt._bottle_identity_clause(_REAL_SHAPED_PRODUCT)
    assert "governed separately" in clause
    assert "register-specific guidance and the small-scale simplification note" in clause


def test_bottle_integration_clause_requires_participating_object():
    clause = generate_image_prompt._bottle_integration_clause()
    assert "PARTICIPATING OBJECT" in clause
    assert "never a flat packshot pasted on top of it" in clause
    assert "contact shadow" in clause
    assert "fingers wrap convincingly around the bottle's body" in clause
    assert "natural angle for that grip" in clause
    assert "show the oil visibly on the skin" in clause
    assert "must NEVER overlap a text block or caption" in clause


def test_bottle_integration_clause_overrides_reference_floating_packshot():
    """Contradiction check requested before finalising: edit mode's photographic-
    substitute branch says to match the reference's composition "faithfully" - if the
    reference itself shows a floating packshot, that must not be reproduced."""
    clause = generate_image_prompt._bottle_integration_clause()
    assert "even when the reference ad itself shows the competitor's product as a " \
           "floating, ungrounded packshot" in clause
    assert "OVERRIDES ANY COMPOSITION-MATCHING INSTRUCTION" in clause


def test_bottle_integration_clause_reaches_all_three_branches():
    bp_edit, bp_writer, bp_flat = _blueprint(), _blueprint(), _blueprint()
    edit_prompt = generate_image_prompt.build_image_prompt(
        bp_edit, product=_REAL_SHAPED_PRODUCT, edit_mode=True,
    )
    writer_prompt = generate_image_prompt.build_image_prompt(
        bp_writer, product=_REAL_SHAPED_PRODUCT, creative_description="A scene.",
    )
    flat_prompt = generate_image_prompt.build_image_prompt(bp_flat, product=_REAL_SHAPED_PRODUCT)
    for prompt in (edit_prompt, writer_prompt, flat_prompt):
        assert "BOTTLE INTEGRATION (STRICT" in prompt


# ---- 2026-08-15: pump/cap orientation is a composition detail, not a fixed geometry
# fact - live evidence: every configured reference photo shows the pump facing the
# same way, and every generated bottle inherited that facing regardless of the scene ----

def test_bottle_integration_clause_pump_orientation_follows_scene_not_reference():
    clause = generate_image_prompt._bottle_integration_clause()
    assert "PUMP/CAP ORIENTATION follows THIS SCENE's own composition" in clause
    assert "never fixed to match the facing shown in Besque's own reference photo(s)" in clause
    assert "fix the pump's design and geometry only, never which way it points" in clause


# ---- 2026-08-15: BOTTLE GEOMETRY SOURCE - edit mode is the one branch where the
# competitor reference image AND Besque's own product reference photos are both in
# play; live evidence across a 5-ad OSEA batch showed the rendered bottle tracking each
# reference ad's own product geometry (silhouette/height/width/proportions) instead of
# Besque's ----

def test_bottle_geometry_source_clause_names_competitor_as_style_only():
    clause = generate_image_prompt._bottle_geometry_source_clause()
    assert "supplies RENDERING STYLE ONLY" in clause
    assert "It supplies NOTHING about the Besque bottle's own shape" in clause


def test_bottle_geometry_source_clause_defers_to_bottle_geometry_clause_not_photos():
    """2026-08-16: this clause no longer re-enumerates geometry categories (silhouette,
    height-to-width ratio, neck/shoulder/base, pump/collar hardware) or names product
    reference photos as a geometry source - a second, differently-worded geometry
    statement nearer the point of use is exactly the contradiction shape this file's
    guardrails note warns about. It defers entirely to _bottle_geometry_clause's fixed
    numbers, and reference photos are now scoped to identity (colour/label/hardware
    finish) only, never shape."""
    clause = generate_image_prompt._bottle_geometry_source_clause()
    assert "stated exactly, once, in the BOTTLE GEOMETRY clause above" in clause
    assert "may override, adjust, or re-derive them" in clause
    assert "confirm colour, label, and hardware finish only" in clause
    # None of the old per-category enumeration survives.
    assert "height-to-width ratio" not in clause
    assert "neck, shoulder, or base" not in clause
    assert "pump or collar hardware design" not in clause


def test_bottle_geometry_source_clause_reaches_edit_mode_prompt_when_product_shown():
    bp = _blueprint()
    prompt = generate_image_prompt.build_image_prompt(
        bp, product=_REAL_SHAPED_PRODUCT, edit_mode=True,
    )
    assert "BOTTLE GEOMETRY SOURCE (STRICT" in prompt


def test_bottle_geometry_source_clause_absent_when_productless():
    bp = _blueprint()
    prompt = generate_image_prompt.build_image_prompt(
        bp, product=_REAL_SHAPED_PRODUCT, edit_mode=True, include_product=False,
    )
    assert "BOTTLE GEOMETRY SOURCE (STRICT" not in prompt


def test_bottle_geometry_source_clause_absent_outside_edit_mode():
    """Only edit mode ever attaches a competitor reference image alongside the product's
    own reference photos - the writer and flat-template branches have no competitor
    image in play at all, so the source-attribution statement has nothing to attribute
    between and is correctly omitted there."""
    bp_writer, bp_flat = _blueprint(), _blueprint()
    writer_prompt = generate_image_prompt.build_image_prompt(
        bp_writer, product=_REAL_SHAPED_PRODUCT, creative_description="A scene.",
    )
    flat_prompt = generate_image_prompt.build_image_prompt(bp_flat, product=_REAL_SHAPED_PRODUCT)
    assert "BOTTLE GEOMETRY SOURCE (STRICT" not in writer_prompt
    assert "BOTTLE GEOMETRY SOURCE (STRICT" not in flat_prompt


def test_bottle_register_and_material_realism_clauses_unchanged_by_item_2():
    """Explicit instruction: keep _bottle_register_clause and the material realism
    clause as they are - lighting/finish should still adapt, untouched by identity."""
    prompt = generate_image_prompt.build_image_prompt(_blueprint(), product=_REAL_SHAPED_PRODUCT)
    assert generate_image_prompt._BOTTLE_MATERIAL_REALISM_CLAUSE in prompt
    assert "Only the bottle's lighting, grading, and finish adapt to match the rendering register" in prompt
