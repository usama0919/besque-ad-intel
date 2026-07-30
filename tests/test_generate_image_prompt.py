"""Tests for the image-prompt generator (no image API call)."""
from src import generate_image_prompt


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


def test_prompt_includes_compliance_rules():
    prompt = generate_image_prompt.build_image_prompt(_blueprint())
    assert "C1. NO REAL PEOPLE" in prompt
    assert "C6. NO SEXUALIZED FRAMING" in prompt
    # Existing rules 6/7 must still be present, unmodified, not replaced by the new rules.
    assert "TEXT POLICY (STRICT)" in prompt
    assert "PRODUCT POLICY (STRICT)" in prompt


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
    assert generate_image_prompt.PRODUCTION_STYLE_GUIDANCE["illustrated"] in prompt
    assert generate_image_prompt.DEFAULT_STYLE_GUIDANCE not in prompt


def test_production_style_guidance_has_every_canonical_style():
    """Mirrors the module-level assertion in generate_image_prompt.py - a schema addition
    to validator.production_styles() can't silently ship without matching guidance text."""
    from src import validator
    assert set(validator.production_styles()) <= set(generate_image_prompt.PRODUCTION_STYLE_GUIDANCE)


# ---- Part 4: conditional brand_rules() ----

def test_brand_rules_default_reproduces_prior_rules_verbatim():
    """brand_rules(), called with defaults, must reproduce every character of the old flat
    BRAND_RULES constant through rule 7. OLD_BRAND_RULES_THROUGH_RULE_7 below is a plain
    string literal copied from the file as it existed before the constant->function
    refactor - it is NOT imported from or derived from generate_image_prompt in any way,
    so this can't pass by comparing the code to itself."""
    from src.generate_image_prompt import brand_rules, _RULE_8_LAYOUT_IS_COMPOSITION
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
    # Pins down the ENTIRE string: the only thing brand_rules() adds beyond the old
    # verbatim text is rule 8, in exactly this position - no reordering, no extra content.
    assert result == OLD_BRAND_RULES_THROUGH_RULE_7 + _RULE_8_LAYOUT_IS_COMPOSITION + COMPLIANCE_RULES


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