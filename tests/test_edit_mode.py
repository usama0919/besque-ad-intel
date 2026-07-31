"""Tests for EDIT MODE (2026-08-01): reproduce the competitor's own ad image, substituting
only the product. Covers brand_rules()'s new rule 9, _edit_mode_instruction's agreement
with rule 6 (the same class of contradiction Part C guarded against for the writer), and
generate_image() actually attaching the competitor image bytes as an input Part only when
edit_mode is on."""
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


# ---- brand_rules(): rule 9 is edit-mode-only, additive ----

def test_brand_rules_default_unaffected_by_edit_mode_param_existing():
    """edit_mode=False (the default) must not change brand_rules()'s output at all -
    confirms rule 9 is purely additive, not a rewrite of the verbatim default path."""
    assert generate_image_prompt.brand_rules() == generate_image_prompt.brand_rules(edit_mode=False)


def test_brand_rules_edit_mode_adds_rule_9():
    rules = generate_image_prompt.brand_rules(edit_mode=True)
    assert "9) SOURCE IMAGE IS THE COMPETITOR'S OWN AD" in rules
    assert "NEVER let any of the competitor's logo, product, packaging, brand name, or label text survive" in rules
    # Still contains everything the default path has - additive, not a replacement.
    assert "TEXT POLICY" in rules
    assert "PRODUCT POLICY" in rules


def test_brand_rules_edit_mode_false_omits_rule_9():
    assert "SOURCE IMAGE IS THE COMPETITOR'S OWN AD" not in generate_image_prompt.brand_rules(edit_mode=False)


# ---- Step 2, Part 2 (2026-08-02): rule 9 strengthened - a live run reproduced the
# competitor's circular logo, top-right, exactly as in the source, despite rule 9 ----

def test_rule_9_covers_every_kind_of_brand_mark():
    rules = generate_image_prompt.brand_rules(edit_mode=True)
    for mark in ("logo", "emblem", "watermark", "roundel", "badge", "seal"):
        assert mark in rules


def test_rule_9_explicitly_covers_corner_marks_not_just_the_label():
    rules = generate_image_prompt.brand_rules(edit_mode=True)
    assert "corner mark or seal" in rules
    assert "just as much as the product label itself" in rules


def test_rule_9_states_logos_are_not_part_of_composition_to_preserve():
    rules = generate_image_prompt.brand_rules(edit_mode=True)
    assert "is NOT part of the composition to preserve" in rules
    assert "it is the ONE thing that must not survive" in rules


# ---- _edit_mode_instruction() and rule 6 must agree in both text_in_image states ----

def test_edit_mode_instruction_and_rule6_agree_text_in_image_true():
    headline = "Firmer Skin By Friday"
    subtext = "7 cold-pressed oils"
    instruction = generate_image_prompt._edit_mode_instruction(
        text_in_image=True, headline=headline, subtext=subtext
    )
    rule6 = generate_image_prompt._rule6_text_policy(text_in_image=True, headline=headline, subtext=subtext)

    assert f'"{headline}"' in instruction
    assert f'"{headline}"' in rule6
    assert f'"{subtext}"' in instruction
    assert f'"{subtext}"' in rule6
    assert "preserve the reference image's text zones" in instruction
    assert "RESERVED NEGATIVE SPACE" not in instruction
    assert "NEVER render any headline" not in rule6


def test_edit_mode_instruction_and_rule6_agree_text_in_image_false():
    instruction = generate_image_prompt._edit_mode_instruction(text_in_image=False)
    rule6 = generate_image_prompt._rule6_text_policy(text_in_image=False)

    assert "leave the reference image's text zones as clean, empty space" in instruction
    assert "do not render any text" in instruction
    assert "NEVER render any headline" in rule6
    # Neither may permit rendering wording in this mode.
    assert "preserve the reference image's text zones" not in instruction
    assert '"' not in instruction.split("TEXT:")[1]  # no quoted wording in the no-text branch


def test_edit_mode_instruction_true_without_headline_falls_back_to_no_text():
    """Same fallback precedent as the writer (Part C): text_in_image=True but no headline
    (e.g. copy generation produced none) must fall back to the SAME no-text branch as
    text_in_image=False, not a half-permitted state."""
    with_flag_no_headline = generate_image_prompt._edit_mode_instruction(text_in_image=True, headline=None)
    default = generate_image_prompt._edit_mode_instruction(text_in_image=False)
    assert with_flag_no_headline == default


# ---- build_image_prompt: edit_mode takes priority, still forces the guardrails ----

def test_build_image_prompt_edit_mode_uses_edit_instruction_not_template():
    prompt = generate_image_prompt.build_image_prompt(_blueprint(), edit_mode=True)
    assert "EDIT MODE: the FIRST attached image is the competitor's own advertisement" in prompt
    assert "Composition and setting:" not in prompt  # template scene text must NOT appear


def test_build_image_prompt_edit_mode_takes_priority_over_creative_description():
    prompt = generate_image_prompt.build_image_prompt(
        _blueprint(), edit_mode=True, creative_description="A writer-provided scene that should be ignored."
    )
    assert "EDIT MODE:" in prompt
    assert "A writer-provided scene that should be ignored." not in prompt


def test_build_image_prompt_edit_mode_still_forces_guardrails():
    product = {"name": "Magic Body Oil", "description": "seven cold-pressed oils",
               "ingredients": "almond; rosehip", "hero_claim": "Visibly firms"}
    prompt = generate_image_prompt.build_image_prompt(_blueprint(), product=product, edit_mode=True)
    assert "C1. NO REAL PEOPLE" in prompt  # compliance rules, always present
    assert "almond; rosehip" in prompt  # product_clause, always present
    assert "Square 1:1 aspect ratio composition." in prompt
    assert "9) SOURCE IMAGE IS THE COMPETITOR'S OWN AD" in prompt


def test_build_image_prompt_edit_mode_false_reproduces_default_path():
    bp = _blueprint()
    assert (generate_image_prompt.build_image_prompt(bp)
            == generate_image_prompt.build_image_prompt(bp, edit_mode=False))


# ---- generate_image(): competitor bytes reach Gemini only when edit_mode is on ----

class _CapturingGenaiClient:
    """Stands in for genai.Client, capturing the exact `contents` passed to
    generate_content so tests can inspect which Parts were actually attached."""
    last_contents = None

    def __init__(self, *a, **k):
        self.models = self

    def generate_content(self, model, contents):
        _CapturingGenaiClient.last_contents = contents
        part = type("Part", (), {"inline_data": type("Data", (), {"data": b"fake-png-bytes"})()})()
        candidate = type("Candidate", (), {"content": type("Content", (), {"parts": [part]})()})()
        return type("Response", (), {"candidates": [candidate]})()


def test_generate_image_attaches_competitor_bytes_when_edit_mode_on(monkeypatch, tmp_path):
    monkeypatch.setattr(generate_image_prompt, "genai", type("obj", (), {"Client": _CapturingGenaiClient}))
    monkeypatch.setattr(generate_image_prompt, "ASSET_DIR", tmp_path)

    generate_image_prompt.generate_image(
        _blueprint(), "AD_EDIT", edit_mode=True, competitor_image_bytes=b"\x89PNG\r\n\x1a\ncompetitor-bytes",
    )
    contents = _CapturingGenaiClient.last_contents
    assert isinstance(contents, list)
    assert contents[0].inline_data.data == b"\x89PNG\r\n\x1a\ncompetitor-bytes"
    assert "THE AD TO REPRODUCE" in contents[-1]


def test_generate_image_omits_competitor_bytes_when_edit_mode_off(monkeypatch, tmp_path):
    monkeypatch.setattr(generate_image_prompt, "genai", type("obj", (), {"Client": _CapturingGenaiClient}))
    monkeypatch.setattr(generate_image_prompt, "ASSET_DIR", tmp_path)

    generate_image_prompt.generate_image(
        _blueprint(), "AD_NO_EDIT", edit_mode=False, competitor_image_bytes=b"should-never-be-used",
    )
    contents = _CapturingGenaiClient.last_contents
    # No reference_images and edit_mode off -> contents is the bare prompt string, no Parts.
    assert isinstance(contents, str)
    assert "THE AD TO REPRODUCE" not in contents


def test_generate_image_edit_mode_orders_competitor_before_product_reference_images(monkeypatch, tmp_path):
    monkeypatch.setattr(generate_image_prompt, "genai", type("obj", (), {"Client": _CapturingGenaiClient}))
    monkeypatch.setattr(generate_image_prompt, "ASSET_DIR", tmp_path)

    generate_image_prompt.generate_image(
        _blueprint(), "AD_EDIT2", edit_mode=True,
        competitor_image_bytes=b"\x89PNG\r\n\x1a\ncompetitor-bytes",
        reference_images=[b"product-photo-1", b"product-photo-2"],
    )
    contents = _CapturingGenaiClient.last_contents
    assert contents[0].inline_data.data == b"\x89PNG\r\n\x1a\ncompetitor-bytes"
    assert [p.inline_data.data for p in contents[1:3]] == [b"product-photo-1", b"product-photo-2"]
    framing_and_prompt = contents[-1]
    assert "THE AD TO REPRODUCE" in framing_and_prompt
    assert "REFERENCE PRODUCT PHOTOS ABOVE" in framing_and_prompt


# ---- Step 2, Part 3 (2026-08-02): urgency/CTA text must not survive with offer_text
# empty - a live run rendered "Grab Before They're Gone!" as a button ----

def test_edit_mode_instruction_offer_text_absent_bans_urgency_and_cta():
    instruction = generate_image_prompt._edit_mode_instruction(offer_text=None)
    assert "no urgency phrasing, discount, price, or CTA button text may appear" in instruction
    assert "neutral wording only" in instruction


def test_edit_mode_instruction_offer_text_given_states_exact_wording_only():
    instruction = generate_image_prompt._edit_mode_instruction(offer_text="20% off this week")
    assert "ONLY this exact wording: 20% off this week" in instruction
    assert "no urgency phrasing, discount, price, or CTA button text may appear" not in instruction


def test_build_image_prompt_edit_mode_forwards_offer_text_to_edit_instruction():
    prompt_no_offer = generate_image_prompt.build_image_prompt(_blueprint(), edit_mode=True)
    assert "no urgency phrasing, discount, price, or CTA button text may appear" in prompt_no_offer

    prompt_with_offer = generate_image_prompt.build_image_prompt(
        _blueprint(), edit_mode=True, offer_text="free shipping this week"
    )
    assert "ONLY this exact wording: free shipping this week" in prompt_with_offer


# ---- Step 2, Part 4 (2026-08-02): don't invent a product the reference doesn't have ----

def test_edit_mode_instruction_reference_has_no_product_does_not_add_one():
    instruction = generate_image_prompt._edit_mode_instruction(reference_has_product=False)
    assert "do NOT add a Besque product" in instruction
    assert "there is nothing to substitute" in instruction
    assert "Remove the competitor's product entirely and place the Besque product" not in instruction


def test_edit_mode_instruction_reference_has_product_default_substitutes():
    instruction = generate_image_prompt._edit_mode_instruction()
    assert "Remove the competitor's product entirely and place the Besque product" in instruction


def test_build_image_prompt_edit_mode_product_count_zero_forces_no_product():
    """layout_detail.product_count==0 (deconstruct.py's schema) means the reference ad has
    no product in frame - the operator's own include_product=True toggle must not add one
    where the source has nothing to substitute."""
    bp = _blueprint()
    bp["layout_detail"] = {"product_count": 0}
    prompt = generate_image_prompt.build_image_prompt(bp, edit_mode=True, include_product=True)
    assert "do NOT add a Besque product" in prompt
    assert "This is a deliberately productless, educational/illustrative image" in prompt
    assert "Remove the competitor's product entirely and place the Besque product" not in prompt


def test_build_image_prompt_edit_mode_not_product_category_forces_no_product():
    bp = _blueprint()
    bp["product_category"] = {"category": "not_product"}
    prompt = generate_image_prompt.build_image_prompt(bp, edit_mode=True, include_product=True)
    assert "do NOT add a Besque product" in prompt
    assert "This is a deliberately productless, educational/illustrative image" in prompt


def test_build_image_prompt_edit_mode_product_present_still_substitutes():
    """Regression guard: the new reference_has_product logic must not accidentally
    suppress the normal substitution case when the reference DOES have a product."""
    bp = _blueprint()
    bp["layout_detail"] = {"product_count": 1}
    product = {"name": "Magic Body Oil", "description": "seven cold-pressed oils",
               "ingredients": "almond; rosehip", "hero_claim": "Visibly firms"}
    prompt = generate_image_prompt.build_image_prompt(bp, product=product, edit_mode=True, include_product=True)
    assert "Remove the competitor's product entirely and place the Besque product" in prompt
    assert "Place the Besque product described below as the subject" in prompt


# ---- Step 3, Part 2 (2026-08-03): product-derived substances must take OUR colour -
# a clear serum drip from the reference survived unchanged against our golden-amber oil ----

def test_edit_mode_instruction_product_substances_must_match_our_colour():
    instruction = generate_image_prompt._edit_mode_instruction()
    assert "drip, pour, pool, droplet, smear, texture swatch" in instruction
    assert "a smear on skin" in instruction
    assert "recolour and re-texture it to match OUR product's actual colour" in instruction
    assert "a clear serum drip must become our golden-amber oil, not stay clear" in instruction


def test_edit_mode_instruction_substance_recolour_states_product_derived_is_the_product():
    instruction = generate_image_prompt._edit_mode_instruction()
    assert "a product-derived substance is the product, even when it has left the bottle" in instruction


def test_edit_mode_instruction_substance_recolour_absent_when_nothing_to_substitute():
    """The recolour instruction only makes sense when a product IS being substituted in -
    absent when the reference has no product, and absent when the operator disabled the
    product entirely."""
    no_reference_product = generate_image_prompt._edit_mode_instruction(reference_has_product=False)
    assert "recolour and re-texture" not in no_reference_product

    operator_disabled = generate_image_prompt._edit_mode_instruction(include_product=False)
    assert "recolour and re-texture" not in operator_disabled


def test_build_image_prompt_edit_mode_include_product_false_unaffected_by_reference_has_product():
    """include_product=False (operator's own toggle) must still mean no product, exactly
    as before - reference_has_product only ever narrows include_product, never widens it."""
    bp = _blueprint()
    bp["layout_detail"] = {"product_count": 2}  # reference clearly HAS a product
    prompt = generate_image_prompt.build_image_prompt(bp, edit_mode=True, include_product=False)
    assert "This is a deliberately productless, educational/illustrative image" in prompt
    assert "Remove the competitor's product entirely and place the Besque product" not in prompt


def test_generate_image_edit_mode_skips_the_writer_even_with_an_angle(monkeypatch, tmp_path):
    """The reference image IS the brief in edit mode - a text creative_description would
    just fight it, so the writer must not run even when an angle is selected."""
    monkeypatch.setattr(generate_image_prompt, "genai", type("obj", (), {"Client": _CapturingGenaiClient}))
    monkeypatch.setattr(generate_image_prompt, "ASSET_DIR", tmp_path)
    calls = []
    monkeypatch.setattr(
        generate_image_prompt.generate_image_prompt_writer, "write_creative_description",
        lambda *a, **k: calls.append(k) or "Writer-provided scene."
    )

    generate_image_prompt.generate_image(
        _blueprint(), "AD_EDIT_ANGLE", edit_mode=True,
        messaging_angle={"name": "Crepey Skin", "notes": "warm light"},
        competitor_image_bytes=b"\x89PNG\r\n\x1a\ncompetitor-bytes",
    )
    assert calls == []


# ---- Prompt 4, Item 2: efficacy claims banned unconditionally in edit mode too ----

def test_edit_mode_instruction_bans_efficacy_claims_unconditionally():
    for kwargs in ({}, {"offer_text": "20% off"}, {"include_product": False},
                   {"reference_has_product": False}):
        instruction = generate_image_prompt._edit_mode_instruction(**kwargs)
        assert "describe NO quantified efficacy claim of any kind" in instruction
        assert "percentage improvement" in instruction
        assert "in just 7 days" in instruction


def test_build_image_prompt_edit_mode_forwards_efficacy_ban():
    prompt = generate_image_prompt.build_image_prompt(_blueprint(), edit_mode=True)
    assert "describe NO quantified efficacy claim of any kind" in prompt


# ---- Prompt 4, Item 5: colour palette substitution - ONE integrated instruction, not
# two competing ones. Contradictory prompt sections have caused every image failure this
# session, so geometry-preserved and colour-substituted must read as a single instruction. ----

def test_retheme_colours_on_states_one_integrated_instruction():
    instruction = generate_image_prompt._edit_mode_instruction(retheme_colours=True)
    assert "geometry is preserved, colour is substituted" in instruction
    assert "not two competing ones" in instruction
    # The preserved-geometry list and the colour-substitution clause appear in the SAME
    # sentence set - "colour palette" must NOT survive as something to reproduce, or it
    # would directly contradict the substitution instruction just stated.
    assert "colour palette" not in instruction


def test_retheme_colours_on_lists_geometry_elements_preserved():
    instruction = generate_image_prompt._edit_mode_instruction(retheme_colours=True)
    for element in ("composition", "camera angle", "spacing", "lighting direction",
                    "contrast relationships", "tonal hierarchy"):
        assert element in instruction.lower()


def test_retheme_colours_on_names_the_palette():
    instruction = generate_image_prompt._edit_mode_instruction(
        retheme_colours=True, palette="terracotta, maroon, gold, cream"
    )
    assert "terracotta, maroon, gold, cream" in instruction


def test_retheme_colours_on_default_palette_when_none_given():
    instruction = generate_image_prompt._edit_mode_instruction(retheme_colours=True, palette=None)
    assert "terracotta, maroon, gold, cream" in instruction


def test_retheme_colours_off_reverts_to_original_wording_exactly():
    """The doc's own stated exception, and the faithful-clone behaviour already
    validated in production - must revert byte-for-byte to what shipped before Item 5."""
    instruction = generate_image_prompt._edit_mode_instruction(retheme_colours=False)
    assert "colour palette" in instruction
    assert "geometry is preserved, colour is substituted" not in instruction
    assert "Remove the competitor's product entirely and place the Besque product" in instruction


def test_retheme_colours_still_removes_competitor_product_and_substitutes_ours():
    """The colour rewrite must not accidentally break the product-substitution branch it
    now precedes."""
    instruction = generate_image_prompt._edit_mode_instruction(retheme_colours=True)
    assert "Remove the competitor's product entirely and place the Besque product" in instruction


def test_build_image_prompt_edit_mode_forwards_retheme_and_palette():
    prompt = generate_image_prompt.build_image_prompt(
        _blueprint(), edit_mode=True, retheme_colours=True, brand_palette="terracotta, maroon, gold, cream"
    )
    assert "terracotta, maroon, gold, cream" in prompt
    assert "geometry is preserved, colour is substituted" in prompt


def test_build_image_prompt_edit_mode_retheme_false_reverts():
    prompt = generate_image_prompt.build_image_prompt(_blueprint(), edit_mode=True, retheme_colours=False)
    assert "colour palette" in prompt
    assert "geometry is preserved, colour is substituted" not in prompt


def test_build_image_prompt_non_edit_mode_unaffected_by_retheme_colours():
    """retheme_colours only matters in edit_mode - the template/writer paths must be
    byte-identical regardless of its value."""
    bp = _blueprint()
    assert (generate_image_prompt.build_image_prompt(bp, retheme_colours=True)
            == generate_image_prompt.build_image_prompt(bp, retheme_colours=False))


# ---- Typography: map creative_format to OUR typeface, never copy the reference's ----

def test_typography_guidance_used_in_text_branch():
    instruction = generate_image_prompt._edit_mode_instruction(
        text_in_image=True, headline="Firmer Skin By Friday", creative_format="product_hero"
    )
    assert "elegant serif" in instruction
    assert "never the reference's own font" in instruction


def test_typography_guidance_varies_by_creative_format():
    direct_response = generate_image_prompt._edit_mode_instruction(
        text_in_image=True, headline="H", creative_format="offer_led"
    )
    assert "clean, bold sans-serif" in direct_response

    premium = generate_image_prompt._edit_mode_instruction(
        text_in_image=True, headline="H", creative_format="founder_story"
    )
    assert "elegant serif" in premium

    testimonial = generate_image_prompt._edit_mode_instruction(
        text_in_image=True, headline="H", creative_format="testimonial_review"
    )
    assert "handwritten marker" in testimonial


def test_typography_guidance_default_when_creative_format_missing():
    instruction = generate_image_prompt._edit_mode_instruction(
        text_in_image=True, headline="H", creative_format=None
    )
    assert "clean sans-serif - versatile, legible default" in instruction


def test_typography_guidance_absent_when_no_text_rendered():
    instruction = generate_image_prompt._edit_mode_instruction(text_in_image=False, creative_format="product_hero")
    assert "elegant serif" not in instruction


def test_typography_guidance_has_every_creative_format():
    from src import validator
    for fmt in validator.creative_formats():
        assert fmt in generate_image_prompt.TYPOGRAPHY_GUIDANCE


def test_build_image_prompt_edit_mode_reads_creative_format_from_blueprint():
    bp = _blueprint()
    bp["creative_format"] = "founder_story"
    prompt = generate_image_prompt.build_image_prompt(bp, edit_mode=True, text_in_image=True, headline="H")
    assert "elegant serif" in prompt


# ---- generate_image(): palette fetched from brand_settings only when it will be used ----

def test_generate_image_fetches_palette_when_edit_mode_and_retheme_on(monkeypatch, tmp_path):
    from src import dedupe
    monkeypatch.setattr(generate_image_prompt, "genai", type("obj", (), {"Client": _CapturingGenaiClient}))
    monkeypatch.setattr(generate_image_prompt, "ASSET_DIR", tmp_path)
    calls = []
    monkeypatch.setattr(dedupe, "get_brand_settings", lambda: calls.append(1) or {"palette": "custom test palette"})

    generate_image_prompt.generate_image(_blueprint(), "AD_PALETTE", edit_mode=True, retheme_colours=True)
    assert calls == [1]
    assert "custom test palette" in generate_image_prompt.generate_image.last_prompt


def test_generate_image_does_not_fetch_palette_when_retheme_off(monkeypatch, tmp_path):
    from src import dedupe
    monkeypatch.setattr(generate_image_prompt, "genai", type("obj", (), {"Client": _CapturingGenaiClient}))
    monkeypatch.setattr(generate_image_prompt, "ASSET_DIR", tmp_path)
    calls = []
    monkeypatch.setattr(dedupe, "get_brand_settings", lambda: calls.append(1) or {"palette": "should not be used"})

    generate_image_prompt.generate_image(_blueprint(), "AD_NO_RETHEME", edit_mode=True, retheme_colours=False)
    assert calls == []


def test_generate_image_does_not_fetch_palette_when_not_edit_mode(monkeypatch, tmp_path):
    from src import dedupe
    monkeypatch.setattr(generate_image_prompt, "genai", type("obj", (), {"Client": _CapturingGenaiClient}))
    monkeypatch.setattr(generate_image_prompt, "ASSET_DIR", tmp_path)
    calls = []
    monkeypatch.setattr(dedupe, "get_brand_settings", lambda: calls.append(1) or {"palette": "should not be used"})

    generate_image_prompt.generate_image(_blueprint(), "AD_NO_EDIT", edit_mode=False, retheme_colours=True)
    assert calls == []
