"""Tests for the image-prompt generator (no image API call)."""
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


# ---- scene_elements consumption (2026-08-11 schema addition, Item 9): a positive
# inclusion list, only entries flagged essential=true, phrased as MUST-include rather
# than a prohibition. ----

def test_scene_elements_clause_empty_when_absent():
    assert generate_image_prompt._scene_elements_clause(None) == ""
    assert generate_image_prompt._scene_elements_clause([]) == ""


def test_scene_elements_clause_omits_non_essential_entries():
    elements = [{"element": "a folded towel", "role": "background", "essential": False}]
    assert generate_image_prompt._scene_elements_clause(elements) == ""


def test_scene_elements_clause_includes_essential_entries_by_name_and_role():
    elements = [
        {"element": "wooden bathroom shelf", "role": "product rests on it", "essential": True},
        {"element": "a folded towel", "role": "background set-dressing", "essential": False},
    ]
    clause = generate_image_prompt._scene_elements_clause(elements)
    assert "SCENE ELEMENTS TO INCLUDE" in clause
    assert "wooden bathroom shelf" in clause
    assert "product rests on it" in clause
    assert "MUST appear" in clause
    # non-essential entry must not be forced into the inclusion list
    assert "a folded towel" not in clause


# ---- Item 5 (2026-08-13 build): the actual fix lives in deconstruct.py's own prompt
# (element phrases must never encode colour) - _scene_elements_clause itself is
# UNCHANGED. These confirm that once the element phrase is colour-neutral (as
# deconstruct now produces), the resulting "MUST appear" instruction no longer demands
# any competitor colour survive the retheme - no code change here, just verification. ----

def test_scene_elements_clause_colour_neutral_element_does_not_demand_a_colour():
    """A "background gradient" element (deconstruct's now-correct output shape) forces
    the STRUCTURE (a gradient) but names no colour at all - nothing here for the
    retheme instruction elsewhere in the prompt to contradict."""
    elements = [{"element": "background gradient", "role": "fills the frame behind the subject",
                 "essential": True}]
    clause = generate_image_prompt._scene_elements_clause(elements)
    assert "background gradient" in clause
    assert "MUST appear" in clause
    for hue in ("lavender", "purple", "salmon", "pink"):
        assert hue not in clause.lower()


def test_scene_elements_clause_colour_neutral_element_coexists_with_retheme():
    """End to end: a colour-neutral background-gradient element (SCENE ELEMENTS TO
    INCLUDE) alongside retheme_colours=True's palette remap (inside
    _edit_mode_instruction) - both present, neither contradicting the other, since the
    element instruction never names a colour for the remap to override."""
    bp = _blueprint()
    bp["scene_elements"] = [{"element": "background gradient", "role": "fills the frame",
                              "essential": True, "depicts_competitor_category": False}]
    prompt = generate_image_prompt.build_image_prompt(bp, edit_mode=True, retheme_colours=True)
    assert "background gradient" in prompt
    assert "re-maps to Besque's palette" in prompt
    for hue in ("lavender", "purple", "salmon", "pink"):
        assert hue not in prompt.lower()


def test_scene_elements_clause_reaches_flat_template_branch():
    bp = _blueprint()
    bp["scene_elements"] = [{"element": "a second person's hand", "role": "applying the product",
                              "essential": True}]
    prompt = generate_image_prompt.build_image_prompt(bp)
    assert "a second person's hand" in prompt
    assert "SCENE ELEMENTS TO INCLUDE" in prompt


def test_scene_elements_clause_reaches_edit_mode_branch():
    bp = _blueprint()
    bp["scene_elements"] = [{"element": "a wicker basket", "role": "holds the product",
                              "essential": True}]
    prompt = generate_image_prompt.build_image_prompt(bp, edit_mode=True)
    assert "a wicker basket" in prompt


def test_scene_elements_absent_produces_no_change():
    """A blueprint from before this field existed produces byte-for-byte the same prompt
    as before this existed."""
    with_key = generate_image_prompt.build_image_prompt({**_blueprint(), "scene_elements": []})
    without_key = generate_image_prompt.build_image_prompt(_blueprint())
    assert with_key == without_key
    assert "SCENE ELEMENTS TO INCLUDE" not in with_key


# ---- Item 2 REDESIGN (2026-08-13): depicts_competitor_category partitions
# scene_elements between _scene_elements_clause (KEEP, register-neutral) and
# _illustrated_elements_clause (SUBSTITUTE, competitor-category) - one shared field,
# mutually exclusive filters, so no entry can ever be named by both. Repointed at
# scene_elements instead of the separate, never-populated illustrated_elements field. ----

def test_scene_elements_clause_excludes_competitor_category_even_when_essential():
    elements = [
        {"element": "a grilled steak", "role": "centrepiece of the illustration",
         "essential": True, "depicts_competitor_category": True},
    ]
    assert generate_image_prompt._scene_elements_clause(elements) == ""


def test_scene_elements_clause_keeps_essential_neutral_alongside_excluded_competitor_entry():
    elements = [
        {"element": "wooden bathroom shelf", "role": "product rests on it",
         "essential": True, "depicts_competitor_category": False},
        {"element": "a grilled steak", "role": "centrepiece of the illustration",
         "essential": True, "depicts_competitor_category": True},
    ]
    clause = generate_image_prompt._scene_elements_clause(elements)
    assert "wooden bathroom shelf" in clause
    assert "a grilled steak" not in clause


def test_scene_elements_clause_missing_depicts_key_defaults_to_kept():
    """A dict built before this field existed (missing the key entirely, not just
    False) must still be treated as neutral/kept - .get() default, never a KeyError,
    and never silently excluded just because the key is absent."""
    elements = [{"element": "a folded towel", "role": "background", "essential": True}]
    clause = generate_image_prompt._scene_elements_clause(elements)
    assert "a folded towel" in clause


# ---- _illustrated_elements_clause (redesigned 2026-08-13 to read scene_elements
# filtered to depicts_competitor_category, not a separate illustrated_elements list;
# UNGATED from style=="illustrated" the same evening - what an object depicts is
# independent of how it's drawn, so a photographic/3D-rendered competitor-argument
# prop now gets a substitution instruction too, not just a drawn one. Header renamed
# ILLUSTRATED ELEMENTS TO SUBSTITUTE -> COMPETITOR ELEMENTS TO SUBSTITUTE, since
# calling a photographed prop "illustrated" would itself be inaccurate. Only the
# DRAWING instruction for the replacement stays register-conditional. ----

def test_illustrated_elements_clause_fires_on_any_register_now():
    """The old style=="illustrated" gate is gone - a photographic or 3D-rendered
    reference with a competitor-argument prop (chain, padlock, donut, weight-loss
    label) previously had NO clause covering it at all."""
    elements = [{"element": "a chain and padlock", "role": "visual metaphor for locked fat",
                 "essential": True, "depicts_competitor_category": True}]
    for style in ("ugc", "high_spec", "illustrated", None, ""):
        clause = generate_image_prompt._illustrated_elements_clause(elements, style)
        assert "COMPETITOR ELEMENTS TO SUBSTITUTE" in clause
        assert "a chain and padlock" in clause


def test_illustrated_elements_clause_empty_when_nothing_flagged():
    elements = [{"element": "a folded towel", "role": "background",
                 "essential": True, "depicts_competitor_category": False}]
    assert generate_image_prompt._illustrated_elements_clause(elements, "illustrated") == ""
    assert generate_image_prompt._illustrated_elements_clause(elements, "ugc") == ""
    assert generate_image_prompt._illustrated_elements_clause(None, "illustrated") == ""
    assert generate_image_prompt._illustrated_elements_clause([], "illustrated") == ""


def test_illustrated_elements_clause_drawing_instruction_native_when_illustrated():
    elements = [{"element": "a grilled steak", "role": "centrepiece", "essential": True,
                 "depicts_competitor_category": True}]
    clause = generate_image_prompt._illustrated_elements_clause(elements, "illustrated")
    assert "Draw the replacement NATIVELY in this scene's own illustrated style" in clause
    assert "never a photograph or photorealistic element composited into the drawing" in clause
    assert "photorealistically, integrated into this scene" not in clause


def test_illustrated_elements_clause_drawing_instruction_photorealistic_otherwise():
    elements = [{"element": "a chain and padlock", "role": "visual metaphor for locked fat",
                 "essential": True, "depicts_competitor_category": True}]
    for style in ("ugc", "high_spec", None):
        clause = generate_image_prompt._illustrated_elements_clause(elements, style)
        assert "Render the replacement photorealistically, integrated into this scene" in clause
        assert "never a flat illustration or drawn element composited into a photographic or 3D-rendered scene" in clause
        assert "Draw the replacement NATIVELY in this scene's own illustrated style" not in clause


def test_illustrated_elements_clause_fires_on_competitor_category_regardless_of_essential():
    """essential is NOT a gate on the substitute side - an incidental drawn steak
    argues "protein supplement" just as much as an essential one."""
    elements = [{"element": "a grilled steak", "role": "centrepiece of the illustration",
                 "essential": False, "depicts_competitor_category": True}]
    clause = generate_image_prompt._illustrated_elements_clause(elements, "illustrated")
    assert "COMPETITOR ELEMENTS TO SUBSTITUTE" in clause
    assert "a grilled steak" in clause
    assert "centrepiece of the illustration" in clause


def test_illustrated_elements_clause_never_suggests_identifiable_produce():
    """A drawn/depicted, nameable ingredient (a citrus slice, a flower) is itself an
    implied ingredient claim - the same class of fabrication compliance C3 already
    forbids. Only an oil droplet or an abstract botanical FORM survive as SUGGESTED
    example substitutes - "flower" may still appear as something explicitly FORBIDDEN
    (a stronger ban, not a suggestion), so this checks the old suggested-example
    phrasing is gone and the new one is present, not a blanket absence of the word."""
    elements = [{"element": "a spoon of collagen powder", "role": "hero prop",
                 "essential": True, "depicts_competitor_category": True}]
    clause = generate_image_prompt._illustrated_elements_clause(elements, "illustrated")
    assert "citrus" not in clause.lower()
    assert "a leaf, a flower, an oil droplet" not in clause  # the old suggested-example list
    assert "oil droplet" in clause
    assert "abstract botanical form" in clause
    assert "never a specific, identifiable, nameable flower or fruit" in clause


def test_illustrated_elements_clause_never_names_a_specific_replacement_ingredient():
    elements = [{"element": "a hair strand", "role": "argues hair growth", "essential": True,
                 "depicts_competitor_category": True}]
    clause = generate_image_prompt._illustrated_elements_clause(elements, "illustrated")
    assert "never a specific named ingredient not in this product's real ingredient list" in clause


def test_illustrated_elements_clause_lists_multiple_entries():
    elements = [
        {"element": "a grilled steak", "role": "hero prop", "essential": True,
         "depicts_competitor_category": True},
        {"element": "a hair strand", "role": "secondary prop", "essential": False,
         "depicts_competitor_category": True},
    ]
    clause = generate_image_prompt._illustrated_elements_clause(elements, "illustrated")
    assert clause.count("(1)") == 1 and clause.count("(2)") == 1
    assert "a grilled steak" in clause and "a hair strand" in clause


# ---- No object appears in both clauses - guaranteed by construction (one shared
# field, mutually exclusive filters), verified end to end via build_image_prompt ----

def test_no_scene_element_named_in_both_clauses_end_to_end():
    bp = _blueprint()
    bp["production_style"] = {"style": "illustrated"}
    bp["scene_elements"] = [
        {"element": "wooden bathroom shelf", "role": "product rests on it",
         "essential": True, "depicts_competitor_category": False},
        {"element": "a grilled steak", "role": "centrepiece of the illustration",
         "essential": True, "depicts_competitor_category": True},
    ]
    prompt = generate_image_prompt.build_image_prompt(bp, edit_mode=True)
    assert "SCENE ELEMENTS TO INCLUDE" in prompt
    assert "COMPETITOR ELEMENTS TO SUBSTITUTE" in prompt

    # build_image_prompt calls _scene_elements_clause before _illustrated_elements_clause
    # in every branch, so SCENE ELEMENTS TO INCLUDE always precedes COMPETITOR ELEMENTS
    # TO SUBSTITUTE in the assembled text - asserted here, not just assumed.
    assert prompt.index("SCENE ELEMENTS TO INCLUDE") < prompt.index("COMPETITOR ELEMENTS TO SUBSTITUTE")
    include_section = prompt.split("SCENE ELEMENTS TO INCLUDE")[1].split("COMPETITOR ELEMENTS TO SUBSTITUTE")[0]
    substitute_section = "COMPETITOR ELEMENTS TO SUBSTITUTE" + prompt.split("COMPETITOR ELEMENTS TO SUBSTITUTE")[1]

    assert "wooden bathroom shelf" in include_section
    assert "a grilled steak" not in include_section
    assert "a grilled steak" in substitute_section
    assert "wooden bathroom shelf" not in substitute_section


def test_no_scene_element_named_in_both_clauses_non_illustrated_register():
    """The same partition holds now that the substitute clause fires on every
    register, not only illustrated."""
    bp = _blueprint()
    bp["production_style"] = {"style": "high_spec"}
    bp["scene_elements"] = [
        {"element": "wooden bathroom shelf", "role": "product rests on it",
         "essential": True, "depicts_competitor_category": False},
        {"element": "a chain and padlock", "role": "visual metaphor for locked fat",
         "essential": True, "depicts_competitor_category": True},
    ]
    prompt = generate_image_prompt.build_image_prompt(bp, edit_mode=True)
    include_section = prompt.split("SCENE ELEMENTS TO INCLUDE")[1].split("COMPETITOR ELEMENTS TO SUBSTITUTE")[0]
    substitute_section = "COMPETITOR ELEMENTS TO SUBSTITUTE" + prompt.split("COMPETITOR ELEMENTS TO SUBSTITUTE")[1]
    assert "wooden bathroom shelf" in include_section
    assert "a chain and padlock" not in include_section
    assert "a chain and padlock" in substitute_section
    assert "wooden bathroom shelf" not in substitute_section


def test_illustrated_elements_clause_reaches_edit_mode_branch_via_build_image_prompt():
    bp = _blueprint()
    bp["production_style"] = {"style": "illustrated"}
    bp["scene_elements"] = [{"element": "a spoon of collagen powder", "role": "hero prop",
                              "essential": True, "depicts_competitor_category": True}]
    prompt = generate_image_prompt.build_image_prompt(bp, edit_mode=True)
    assert "a spoon of collagen powder" in prompt
    assert "COMPETITOR ELEMENTS TO SUBSTITUTE" in prompt


def test_illustrated_elements_clause_reaches_flat_template_branch():
    bp = _blueprint()
    bp["production_style"] = {"style": "illustrated"}
    bp["scene_elements"] = [{"element": "a spoon of collagen powder", "role": "hero prop",
                              "essential": True, "depicts_competitor_category": True}]
    prompt = generate_image_prompt.build_image_prompt(bp)
    assert "a spoon of collagen powder" in prompt


def test_illustrated_elements_clause_reaches_photographic_reference_via_build_image_prompt():
    """The concrete case this ungating fixes: a photographic (non-illustrated)
    reference with a competitor-argument prop."""
    bp = _blueprint()
    bp["production_style"] = {"style": "ugc"}
    bp["scene_elements"] = [{"element": "a donut", "role": "argues a sugary treat",
                              "essential": True, "depicts_competitor_category": True}]
    prompt = generate_image_prompt.build_image_prompt(bp, edit_mode=True)
    assert "a donut" in prompt
    assert "COMPETITOR ELEMENTS TO SUBSTITUTE" in prompt
    assert "Render the replacement photorealistically" in prompt


# ---- The catch-all "everything else carries over exactly" language must except a
# substituted competitor element too, or the substitution instruction gets
# contradicted by the reproduce-faithfully language later in the same prompt (the same
# failure shape already fixed for PERSON/competitor-branding/prop-scale) ----

def test_non_carryover_exceptions_clause_excepts_competitor_elements():
    assert "COMPETITOR ELEMENTS TO SUBSTITUTE" in generate_image_prompt._non_carryover_exceptions_clause()


def test_edit_mode_opening_excepts_competitor_elements_both_retheme_branches():
    on = generate_image_prompt._edit_mode_instruction(retheme_colours=True)
    off = generate_image_prompt._edit_mode_instruction(retheme_colours=False)
    assert "COMPETITOR ELEMENTS TO SUBSTITUTE" in on
    assert "COMPETITOR ELEMENTS TO SUBSTITUTE" in off


# ---- Contradiction check vs _competitor_props_clause (2026-08-13): the two clauses
# have different actions (remove-only vs substitute) and no shared identity key, so a
# same-named object could in principle trigger both - _competitor_props_clause now
# states an explicit precedence for that case. ----

def test_competitor_props_clause_defers_to_illustrated_elements_substitution():
    bp = {
        "product_category": {"signals": ["an anatomical diagram of the digestive tract"]},
        "visual": {},
    }
    clause = generate_image_prompt._competitor_props_clause(bp)
    assert "PROPS (STRICT)" in clause
    assert "UNLESS this same element is also named in the COMPETITOR ELEMENTS TO SUBSTITUTE" in clause
    assert "that instruction governs instead" in clause


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
# reference's facts inform the scene's CHARACTER (direction/hardness/colour temp/
# grain); the bottle's own contact/grip shadow and grounding defer explicitly to
# BOTTLE INTEGRATION's actual composition. Also: an illustrated register must never
# read scene_lighting at all (deconstruct.py's photographic-only fields produced a
# live "Not applicable - no photographic lighting" value for an illustrated reference,
# which _scene_lighting_facts read as a real fact and asserted verbatim) - the drawing
# treatment for "illustrated" always follows _register_lighting_only_clause()'s own
# style-driven wording instead, unconditionally. ----

_REAL_SCENE_LIGHTING = {
    "light_direction": "upper-left, slightly behind camera",
    "hardness": "soft",
    "shadow_behaviour": "long, soft shadows falling right",
    "colour_temperature": "warm/golden",
    "grain": "visible phone-camera noise/grain",
    "depth_of_field": "shallow, background softly blurred",
}


def test_bottle_register_clause_falls_back_to_generic_when_no_facts():
    clause = generate_image_prompt._bottle_register_clause({})
    assert clause == generate_image_prompt._register_lighting_only_clause()


def test_bottle_register_clause_states_scene_character_not_exact_bottle_match():
    clause = generate_image_prompt._bottle_register_clause(_REAL_SCENE_LIGHTING)
    assert "light falls from upper-left" in clause
    assert "SCENE's overall lighting character" in clause
    assert "must match these observed facts about THIS scene EXACTLY" not in clause


def test_bottle_register_clause_defers_contact_shadow_to_bottle_integration():
    clause = generate_image_prompt._bottle_register_clause(_REAL_SCENE_LIGHTING)
    assert "does NOT govern the bottle's own contact or grip shadow" in clause
    assert "BOTTLE INTEGRATION" in clause
    assert "floating product with no" in clause


def test_bottle_register_clause_keeps_reference_photo_lighting_exclusion():
    clause = generate_image_prompt._bottle_register_clause(_REAL_SCENE_LIGHTING)
    assert "separate, unrelated studio lighting the product's own reference photo" in clause


def test_bottle_register_clause_keeps_geometry_fixed_regardless():
    clause = generate_image_prompt._bottle_register_clause(_REAL_SCENE_LIGHTING)
    assert "Geometry, proportions, and label stay exactly as stated above regardless" in clause


def test_bottle_register_clause_illustrated_never_reads_scene_lighting_facts():
    """The live bug: an illustrated reference's own scene_lighting can carry a
    "Not applicable" value (deconstruct.py trying to fill photographic-only fields for
    a drawing) - style=="illustrated" must skip _scene_lighting_facts entirely, not
    just fall back when the dict happens to be empty."""
    garbage_lighting = {"light_direction": "Not applicable - no photographic lighting"}
    clause = generate_image_prompt._bottle_register_clause(garbage_lighting, style="illustrated")
    assert clause == generate_image_prompt._register_lighting_only_clause()
    assert "Not applicable" not in clause


def test_bottle_register_clause_illustrated_ignores_even_real_facts():
    clause = generate_image_prompt._bottle_register_clause(_REAL_SCENE_LIGHTING, style="illustrated")
    assert clause == generate_image_prompt._register_lighting_only_clause()
    assert "upper-left" not in clause


def test_bottle_register_clause_photographic_style_still_uses_real_facts():
    clause = generate_image_prompt._bottle_register_clause(_REAL_SCENE_LIGHTING, style="ugc")
    assert "light falls from upper-left" in clause


def test_bottle_register_clause_no_style_given_keeps_old_behaviour():
    """Callers that predate the style param (style=None) must see the same photographic
    treatment as before this fix - only an explicit style=="illustrated" changes
    anything."""
    clause = generate_image_prompt._bottle_register_clause(_REAL_SCENE_LIGHTING, style=None)
    assert "light falls from upper-left" in clause


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


def test_bottle_register_and_material_realism_clauses_unchanged_by_item_2():
    """Explicit instruction: keep _bottle_register_clause and the material realism
    clause as they are - lighting/finish should still adapt, untouched by identity."""
    prompt = generate_image_prompt.build_image_prompt(_blueprint(), product=_REAL_SHAPED_PRODUCT)
    assert generate_image_prompt._BOTTLE_MATERIAL_REALISM_CLAUSE in prompt
    assert "Only the bottle's lighting, grading, and finish adapt to match the rendering register" in prompt
