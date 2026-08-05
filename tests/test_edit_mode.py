"""Tests for EDIT MODE (2026-08-01): reproduce the competitor's own ad image, substituting
only the product. Covers brand_rules()'s new rule 9, _edit_mode_instruction's agreement
with rule 6 (the same class of contradiction Part C guarded against for the writer), and
generate_image() actually attaching the competitor image bytes as an input Part only when
edit_mode is on."""
import io
from PIL import Image
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


def _png_bytes(width=100, height=100):
    """A real, readable PNG of the given shape - used wherever a test needs
    competitor_image_bytes to survive Image.open() (Item 6a's derive_aspect_ratio), as
    opposed to the old placeholder b"\\x89PNG...competitor-bytes" literal, which has a
    real PNG signature but no valid image data after it."""
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color=(200, 150, 100)).save(buf, format="PNG")
    return buf.getvalue()


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


def test_edit_mode_instruction_text_branch_states_entire_text_budget():
    instruction = generate_image_prompt._edit_mode_instruction(
        text_in_image=True, headline="Headline", subtext="Short line."
    )
    text_section = instruction.split("TEXT:")[1].split("OFFER:")[0]
    assert "ENTIRE text budget for this image" in text_section
    assert "ingredient list" in text_section


def test_edit_mode_instruction_caps_overlong_subtext():
    long_text = " ".join(f"word{i}" for i in range(30))
    instruction = generate_image_prompt._edit_mode_instruction(
        text_in_image=True, headline="Headline", subtext=long_text
    )
    assert "word11" in instruction
    assert "word12" not in instruction


def test_edit_mode_instruction_and_rule6_agree_text_in_image_false():
    instruction = generate_image_prompt._edit_mode_instruction(text_in_image=False)
    rule6 = generate_image_prompt._rule6_text_policy(text_in_image=False)

    assert "removed ENTIRELY along with its wording" in instruction  # Item 6c
    assert "no text, headline, or competitor wording rendered there either" in instruction
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
    assert "9) SOURCE IMAGE IS THE COMPETITOR'S OWN AD" in prompt


# ---- Item 6a (2026-08-04): edit mode's aspect ratio comes from the reference image via
# generation config, not a hardcoded "Square 1:1" prompt-text line ----

def test_build_image_prompt_edit_mode_has_no_aspect_ratio_instruction():
    """The old hardcoded line must be gone entirely from edit mode - not replaced by a
    different hardcoded string, since Item 6a moves this to the generation config
    (derive_aspect_ratio + genai_types.ImageConfig in generate_image), which needs the
    real reference image's dimensions, not something build_image_prompt has access to."""
    prompt = generate_image_prompt.build_image_prompt(_blueprint(), edit_mode=True)
    assert "1:1" not in prompt
    assert "aspect ratio" not in prompt.lower()


def test_build_image_prompt_generate_mode_still_has_hardcoded_square_1_1():
    """Generate mode (edit_mode=False, both the template and creative_description
    branches) is unaffected by Item 6a - it keeps its explicit prompt-text aspect ratio."""
    prompt = generate_image_prompt.build_image_prompt(_blueprint())
    assert "Square 1:1 aspect ratio composition." in prompt
    prompt_writer = generate_image_prompt.build_image_prompt(
        _blueprint(), creative_description="A calm spa scene."
    )
    assert "Square 1:1 aspect ratio composition." in prompt_writer


def test_build_image_prompt_edit_mode_false_reproduces_default_path():
    bp = _blueprint()
    assert (generate_image_prompt.build_image_prompt(bp)
            == generate_image_prompt.build_image_prompt(bp, edit_mode=False))


# ---- generate_image(): competitor bytes reach Gemini only when edit_mode is on ----

class _CapturingGenaiClient:
    """Stands in for genai.Client, capturing the exact `contents` (and, since Item 6a,
    `config`) passed to generate_content so tests can inspect which Parts were actually
    attached and which aspect ratio was requested."""
    last_contents = None
    last_config = None

    def __init__(self, *a, **k):
        self.models = self

    def generate_content(self, model, contents, config=None):
        _CapturingGenaiClient.last_contents = contents
        _CapturingGenaiClient.last_config = config
        part = type("Part", (), {"inline_data": type("Data", (), {"data": b"fake-png-bytes"})()})()
        candidate = type("Candidate", (), {"content": type("Content", (), {"parts": [part]})()})()
        return type("Response", (), {"candidates": [candidate]})()


def test_generate_image_attaches_competitor_bytes_when_edit_mode_on(monkeypatch, tmp_path):
    monkeypatch.setattr(generate_image_prompt, "genai", type("obj", (), {"Client": _CapturingGenaiClient}))
    monkeypatch.setattr(generate_image_prompt, "ASSET_DIR", tmp_path)
    competitor_bytes = _png_bytes()

    generate_image_prompt.generate_image(
        _blueprint(), "AD_EDIT", edit_mode=True, competitor_image_bytes=competitor_bytes,
    )
    contents = _CapturingGenaiClient.last_contents
    assert isinstance(contents, list)
    assert contents[0].inline_data.data == competitor_bytes
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
    competitor_bytes = _png_bytes()

    generate_image_prompt.generate_image(
        _blueprint(), "AD_EDIT2", edit_mode=True,
        competitor_image_bytes=competitor_bytes,
        reference_images=[b"product-photo-1", b"product-photo-2"],
    )
    contents = _CapturingGenaiClient.last_contents
    assert contents[0].inline_data.data == competitor_bytes
    assert [p.inline_data.data for p in contents[1:3]] == [b"product-photo-1", b"product-photo-2"]
    framing_and_prompt = contents[-1]
    assert "THE AD TO REPRODUCE" in framing_and_prompt
    assert "REFERENCE PRODUCT PHOTOS ABOVE" in framing_and_prompt


# ---- Item 6a (2026-08-04): derive_aspect_ratio - snap the reference image's own
# width:height to the nearest ratio Vertex's ImageConfig.aspect_ratio actually supports ----

def test_derive_aspect_ratio_square():
    assert generate_image_prompt.derive_aspect_ratio(_png_bytes(200, 200)) == "1:1"


def test_derive_aspect_ratio_landscape_16_9():
    assert generate_image_prompt.derive_aspect_ratio(_png_bytes(1920, 1080)) == "16:9"


def test_derive_aspect_ratio_portrait_9_16():
    assert generate_image_prompt.derive_aspect_ratio(_png_bytes(1080, 1920)) == "9:16"


def test_derive_aspect_ratio_landscape_4_3():
    assert generate_image_prompt.derive_aspect_ratio(_png_bytes(800, 600)) == "4:3"


def test_derive_aspect_ratio_portrait_3_4():
    assert generate_image_prompt.derive_aspect_ratio(_png_bytes(600, 800)) == "3:4"


def test_derive_aspect_ratio_portrait_4_5():
    """4:5 (2026-08-04) - added after a live probe confirmed gemini-3.1-flash-image
    accepts and honours it; Meta creative is commonly this shape (24/101 of this
    project's own stored competitor references sit here) and previously mis-snapped to
    3:4 under the old 8-value table."""
    assert generate_image_prompt.derive_aspect_ratio(_png_bytes(800, 1000)) == "4:5"


def test_derive_aspect_ratio_landscape_5_4():
    assert generate_image_prompt.derive_aspect_ratio(_png_bytes(1000, 800)) == "5:4"


def test_derive_aspect_ratio_exact_3_4_still_wins_over_nearby_4_5():
    """A true 3:4 reference (ratio 0.75 exactly) must still snap to 3:4, not 4:5 (0.8) -
    confirms adding 4:5 didn't regress the existing 3:4 boundary."""
    assert generate_image_prompt.derive_aspect_ratio(_png_bytes(750, 1000)) == "3:4"


def test_derive_aspect_ratio_none_bytes_returns_none():
    assert generate_image_prompt.derive_aspect_ratio(None) is None


def test_derive_aspect_ratio_empty_bytes_returns_none():
    assert generate_image_prompt.derive_aspect_ratio(b"") is None


def test_derive_aspect_ratio_unreadable_bytes_returns_none():
    """A PNG signature with no valid image data after it - Image.open() must raise, not
    crash the caller; derive_aspect_ratio swallows it and returns None."""
    assert generate_image_prompt.derive_aspect_ratio(b"\x89PNG\r\n\x1a\nnot-a-real-image") is None


# ---- Item 6a: generate_image() sets the derived ratio on the generation config in edit
# mode, and only in edit mode ----

def test_generate_image_edit_mode_sets_aspect_ratio_config_from_reference(monkeypatch, tmp_path):
    monkeypatch.setattr(generate_image_prompt, "genai", type("obj", (), {"Client": _CapturingGenaiClient}))
    monkeypatch.setattr(generate_image_prompt, "ASSET_DIR", tmp_path)

    generate_image_prompt.generate_image(
        _blueprint(), "AD_ASPECT", edit_mode=True, competitor_image_bytes=_png_bytes(1080, 1920),
    )
    config = _CapturingGenaiClient.last_config
    assert config is not None
    assert config.image_config.aspect_ratio == "9:16"


def test_generate_image_generate_mode_passes_no_config(monkeypatch, tmp_path):
    """Generate mode is unaffected by Item 6a - it keeps its prompt-text "Square 1:1"
    instruction and never sets a generation config."""
    monkeypatch.setattr(generate_image_prompt, "genai", type("obj", (), {"Client": _CapturingGenaiClient}))
    monkeypatch.setattr(generate_image_prompt, "ASSET_DIR", tmp_path)

    generate_image_prompt.generate_image(_blueprint(), "AD_GENERATE", edit_mode=False)
    assert _CapturingGenaiClient.last_config is None


def test_generate_image_edit_mode_missing_reference_falls_back_and_warns(monkeypatch, tmp_path):
    """competitor_image_bytes=None (or unreadable) in edit mode must not fail the draft -
    it OMITS image_config entirely (not a forced "1:1" - a live probe, 2026-08-04, showed
    that forces the wrong shape while omitting it lets the model infer the reference's own
    ratio) and records a pipeline_warning instead."""
    from src import dedupe
    monkeypatch.setattr(generate_image_prompt, "genai", type("obj", (), {"Client": _CapturingGenaiClient}))
    monkeypatch.setattr(generate_image_prompt, "ASSET_DIR", tmp_path)
    warnings = []
    monkeypatch.setattr(dedupe, "init_pipeline_warnings", lambda: None)
    monkeypatch.setattr(dedupe, "record_warning", lambda kind, detail: warnings.append((kind, detail)))

    dest = generate_image_prompt.generate_image(
        _blueprint(), "AD_ASPECT_FALLBACK", edit_mode=True, competitor_image_bytes=None,
    )
    assert dest is not None  # the draft still succeeds
    assert _CapturingGenaiClient.last_config is None  # no image_config forced onto the call
    assert len(warnings) == 1
    kind, detail = warnings[0]
    assert kind == "edit_mode_aspect_ratio_fallback"
    assert "AD_ASPECT_FALLBACK" in detail


# ---- Step 2, Part 3 (2026-08-02): urgency/CTA text must not survive with offer_text
# empty - a live run rendered "Grab Before They're Gone!" as a button ----

def test_edit_mode_instruction_offer_text_absent_bans_urgency_and_cta():
    instruction = generate_image_prompt._edit_mode_instruction(offer_text=None)
    assert "none of the following may survive anywhere in the image" in instruction
    assert "Reproduce no urgency wording, tiling, code, or button shape" in instruction


def test_edit_mode_instruction_offer_text_given_states_exact_wording_only():
    instruction = generate_image_prompt._edit_mode_instruction(offer_text="20% off this week")
    assert "replace ONLY its wording with: 20% off this week" in instruction
    assert "none of the following may survive anywhere in the image" not in instruction


def test_build_image_prompt_edit_mode_forwards_offer_text_to_edit_instruction():
    prompt_no_offer = generate_image_prompt.build_image_prompt(_blueprint(), edit_mode=True)
    assert "none of the following may survive anywhere in the image" in prompt_no_offer

    prompt_with_offer = generate_image_prompt.build_image_prompt(
        _blueprint(), edit_mode=True, offer_text="free shipping this week"
    )
    assert "replace ONLY its wording with: free shipping this week" in prompt_with_offer


# ---- Item D (2026-08-05): offer substitution inverts 6d's removal - preserve the
# reference badge's position/shape/size/colour/typography exactly, replace only wording.
# A third partition of the SAME suppressing_offer condition 6c/6d already established,
# not a parallel clause: offer_text truthy already excluded the offer container from
# _suppressed_container_exception's removed set (it was never in scope to fold in). ----

def test_offer_substitution_preserves_every_named_property():
    instruction = generate_image_prompt._edit_mode_instruction(offer_text="20% off, code SUMMER20")
    for prop in ("position", "shape", "size", "colour", "typography"):
        assert prop in instruction.split("OFFER:")[1].split("EFFICACY")[0]
    assert "do not restyle, resize, or recolour the badge itself" in instruction


def test_offer_substitution_does_not_remove_the_badge_container():
    """offer_text supplied means the badge is preserved, not one of the containers
    _suppressed_container_exception's opening clause removes."""
    instruction = generate_image_prompt._edit_mode_instruction(
        offer_text="20% off", text_in_image=False
    )
    # text is suppressed (no headline given) but offer is NOT - opening should name only
    # the text container as removed, never the offer one.
    assert "any container holding text that's being suppressed this run" in instruction
    assert "any container holding an offer that's being suppressed this run" not in instruction
    assert "any container holding text or an offer" not in instruction


def test_offer_removal_unaffected_when_offer_text_empty():
    """The empty-offer_text branch (6d's removal wording) must be byte-for-byte
    unchanged by Item D - substitution only applies when offer_text is actually supplied."""
    instruction = generate_image_prompt._edit_mode_instruction(offer_text=None)
    assert "none of the following may survive anywhere in the image" in instruction
    assert "position, shape, size, colour, and typography" not in instruction


def test_build_image_prompt_edit_mode_offer_substitution_reaches_the_prompt():
    prompt = generate_image_prompt.build_image_prompt(
        _blueprint(), edit_mode=True, offer_text="Buy 2 Get 1 Free"
    )
    assert "position, shape, size, colour, and typography EXACTLY as shown" in prompt
    assert "replace ONLY its wording with: Buy 2 Get 1 Free" in prompt


# ---- Item 6d (2026-08-04): strengthen the offer ban - "SUMMER SALE" survived as a tiled
# background because the old ban read as covering a single badge only ----

def test_offer_ban_covers_every_named_category():
    """A refactor can't silently drop a category - each of these must appear somewhere in
    the offer-absent instruction. Mirrors test_rule_9_covers_every_kind_of_brand_mark's
    shape for a different guardrail."""
    instruction = generate_image_prompt._edit_mode_instruction(offer_text=None)
    for category in (
        "percentage or amount off", "a price", "promo or discount code",
        "scarcity or stock-count claim", "limited-time or urgency wording",
        "free-shipping offer", "sale wallpaper", "tiled or repeated promotional pattern",
    ):
        assert category in instruction


def test_offer_ban_covers_every_named_location():
    instruction = generate_image_prompt._edit_mode_instruction(offer_text=None)
    for location in ("badge", "banner", "background", "watermark", "product's own label"):
        assert location in instruction


def test_offer_ban_states_container_removal_per_6c():
    """Iterates the shared _SUPPRESSIBLE_CONTAINER_TYPES constant, not a hardcoded literal
    - test_suppression_exception_names_every_container_type is the one that pins the
    constant itself against a literal; this one just confirms the OFFER clause draws from
    the same shared list, so it deliberately follows the constant if it ever changes."""
    instruction = generate_image_prompt._edit_mode_instruction(offer_text=None)
    for container in generate_image_prompt._SUPPRESSIBLE_CONTAINER_TYPES:
        assert container in instruction
    assert "that container is removed entirely" in instruction
    assert "never left behind empty" in instruction


def test_offer_ban_covers_tiled_background_not_just_a_single_badge():
    """The exact failure this item fixes: "SUMMER SALE" tiled across the whole
    background, which a badge-only ban never named."""
    instruction = generate_image_prompt._edit_mode_instruction(offer_text=None)
    assert "not just in a single discrete badge" in instruction
    assert "the whole pattern is replaced with clean background" in instruction


def test_build_image_prompt_edit_mode_forwards_offer_ban_coverage():
    prompt = generate_image_prompt.build_image_prompt(_blueprint(), edit_mode=True)
    for category in ("scarcity or stock-count claim", "sale wallpaper", "promo or discount code"):
        assert category in prompt


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


# ---- Item 6b (2026-08-04): name the substance colour, don't point at it ----

def test_substance_recolour_clause_names_colour_when_set():
    clause = generate_image_prompt._substance_recolour_clause("bright golden-amber oil")
    assert "recolour and re-texture it to our product's actual colour and texture - " \
           "bright golden-amber oil - never the reference's own product substance" in clause
    assert "a clear serum drip must become our bright golden-amber oil, not stay clear" in clause
    assert "match OUR product's actual colour and texture," not in clause  # generic phrase fully replaced
    assert "a product-derived substance is the product, even when it has left the bottle" in clause


def test_substance_recolour_clause_falls_back_to_generic_when_unset():
    """None (the default - nothing in products.substance_colour) reproduces the exact
    original wording verbatim, including its own hardcoded "golden-amber oil" example -
    not a regression, just this function's only behaviour before the parameter existed."""
    assert (generate_image_prompt._substance_recolour_clause(None)
            == generate_image_prompt._substance_recolour_clause())
    clause = generate_image_prompt._substance_recolour_clause(None)
    assert "recolour and re-texture it to match OUR product's actual colour and texture," in clause
    assert "a clear serum drip must become our golden-amber oil, not stay clear" in clause


def test_substance_recolour_clause_empty_string_same_as_none():
    assert (generate_image_prompt._substance_recolour_clause("")
            == generate_image_prompt._substance_recolour_clause(None))


def test_edit_mode_instruction_forwards_substance_colour():
    instruction = generate_image_prompt._edit_mode_instruction(substance_colour="bright golden-amber oil")
    assert "bright golden-amber oil" in instruction


def test_build_image_prompt_edit_mode_names_substance_colour_from_product():
    product = {"name": "Besque Magic Body Oil", "substance_colour": "bright golden-amber oil"}
    prompt = generate_image_prompt.build_image_prompt(_blueprint(), product=product, edit_mode=True)
    assert "recolour and re-texture it to our product's actual colour and texture - " \
           "bright golden-amber oil -" in prompt


def test_build_image_prompt_edit_mode_omits_colour_phrase_when_substance_colour_unset():
    """A product with no substance_colour (or no product at all) must not have one
    invented - falls back to the exact old generic wording."""
    product_no_colour = {"name": "Besque Shower Oil"}  # no substance_colour key at all
    prompt = generate_image_prompt.build_image_prompt(_blueprint(), product=product_no_colour, edit_mode=True)
    assert "recolour and re-texture it to match OUR product's actual colour and texture," in prompt

    prompt_no_product = generate_image_prompt.build_image_prompt(_blueprint(), edit_mode=True)
    assert "recolour and re-texture it to match OUR product's actual colour and texture," in prompt_no_product


# ---- Item 6c (2026-08-04): remove suppressed containers, not just their contents - ONE
# integrated instruction with reproduce-faithfully, same class as item 5's retheme_colours ----

def test_suppression_exception_states_one_partition_with_full_preservation():
    """The exception must live in the SAME paragraph as the "carries over EXACTLY... in
    any way" claim - not a separate clause elsewhere a reader could take as contradicting
    it. Mirrors test_retheme_colours_on_states_one_integrated_instruction's shape."""
    instruction = generate_image_prompt._edit_mode_instruction(text_in_image=False)
    assert "carry over EXACTLY as shot in the reference" in instruction
    assert "in any way." in instruction
    assert "The ONE exception to full geometry preservation" in instruction
    assert "is removed entirely, not preserved empty" in instruction
    # Both must appear in the SAME opening paragraph, before the product-substitution text.
    carries_over_pos = instruction.index("carry over EXACTLY")
    exception_pos = instruction.index("The ONE exception")
    product_pos = instruction.index("Changing ONLY the product")
    assert carries_over_pos < exception_pos < product_pos


def test_suppression_exception_names_every_container_type():
    """Deliberately a hardcoded literal, NOT generate_image_prompt._SUPPRESSIBLE_CONTAINER_TYPES
    - this is the one test that pins the shared constant itself against something outside
    the source file, so deleting an entry from the constant can't silently take every
    container-list test down with it."""
    instruction = generate_image_prompt._edit_mode_instruction(text_in_image=False)
    for container in ("badge", "pill", "oval", "button", "banner", "ribbon", "starburst"):
        assert container in instruction


def test_suppression_exception_absent_when_neither_text_nor_offer_is_suppressed():
    """No suppression happening AT ALL - a headline is shown AND a real offer is given -
    opening must be byte-for-byte what it was before Item 6c/6d, the same additive-only
    pattern as item 5. Both must be supplied: offer_text defaults to None (suppressed) -
    supplying only headline still leaves the offer exception active (see
    test_suppression_exception_present_when_only_offer_is_suppressed), which is exactly
    the gap found and closed while building 6d."""
    instruction = generate_image_prompt._edit_mode_instruction(
        text_in_image=True, headline="Firmer Skin By Friday", offer_text="free shipping this week"
    )
    assert "The ONE exception to full geometry preservation" not in instruction
    assert "any container that held the suppressed text" not in instruction


def test_suppression_exception_present_when_only_offer_is_suppressed():
    """The gap found during 6d: a headline IS shown (no text suppression) but no offer was
    given (offer suppression still active) - the exception must still fire, naming the
    offer specifically, not text."""
    instruction = generate_image_prompt._edit_mode_instruction(
        text_in_image=True, headline="Firmer Skin By Friday", offer_text=None,
    )
    assert "The ONE exception to full geometry preservation" in instruction
    assert "any container holding an offer that's being suppressed this run" in instruction
    assert "any container holding text that's being suppressed this run" not in instruction


def test_suppression_exception_present_when_only_text_is_suppressed():
    instruction = generate_image_prompt._edit_mode_instruction(
        text_in_image=False, offer_text="free shipping this week",
    )
    assert "The ONE exception to full geometry preservation" in instruction
    assert "any container holding text that's being suppressed this run" in instruction
    assert "any container holding an offer that's being suppressed this run" not in instruction


def test_suppression_exception_names_both_when_both_suppressed():
    instruction = generate_image_prompt._edit_mode_instruction(text_in_image=False, offer_text=None)
    assert "any container holding text or an offer that's being suppressed this run" in instruction


def test_suppression_exception_present_by_default():
    """Default call (text_in_image=False) is the common suppression case - must include
    the exception without the caller having to opt in."""
    instruction = generate_image_prompt._edit_mode_instruction()
    assert "The ONE exception to full geometry preservation" in instruction


def test_suppression_exception_present_regardless_of_retheme_colours():
    """Item 6c is independent of Item 5's toggle - the exception belongs to the
    reproduce-faithfully instruction in BOTH its retheme_colours states."""
    on = generate_image_prompt._edit_mode_instruction(text_in_image=False, retheme_colours=True)
    off = generate_image_prompt._edit_mode_instruction(text_in_image=False, retheme_colours=False)
    assert "The ONE exception to full geometry preservation" in on
    assert "The ONE exception to full geometry preservation" in off


def test_text_clause_removes_container_not_just_contents():
    instruction = generate_image_prompt._edit_mode_instruction(text_in_image=False)
    assert "any container that held the suppressed text" in instruction
    assert "the container shape itself does not survive" in instruction
    assert "clean background continuous with its immediate surroundings" in instruction
    assert "no empty outline, box, or shape left behind" in instruction


def test_build_image_prompt_edit_mode_forwards_suppression_exception():
    prompt = generate_image_prompt.build_image_prompt(_blueprint(), edit_mode=True)
    assert "The ONE exception to full geometry preservation" in prompt
    assert "the container shape itself does not survive" in prompt


def test_build_image_prompt_edit_mode_neither_suppressed_omits_suppression_exception():
    prompt = generate_image_prompt.build_image_prompt(
        _blueprint(), edit_mode=True, text_in_image=True, headline="Firmer Skin By Friday",
        offer_text="free shipping this week",
    )
    assert "The ONE exception to full geometry preservation" not in prompt


def test_build_image_prompt_edit_mode_text_shown_but_offer_absent_keeps_exception():
    """Regression guard for the gap closed while building 6d: text_in_image=True alone
    must NOT be read as "no suppression" - offer_text still defaults to unsuppressed-offer
    unless explicitly given."""
    prompt = generate_image_prompt.build_image_prompt(
        _blueprint(), edit_mode=True, text_in_image=True, headline="Firmer Skin By Friday"
    )
    assert "The ONE exception to full geometry preservation" in prompt


def test_build_image_prompt_non_edit_mode_unaffected_by_suppression_exception():
    """Item 6c is edit-mode-only (per the CLAUDE.md note: these four corrections all sit
    on the image path where prompt-only guardrails are least reliable, specifically in
    edit mode where a real photograph's containers exist to be removed) - generate mode
    has no reference photo containers to remove, so it must be untouched."""
    prompt = generate_image_prompt.build_image_prompt(_blueprint())
    assert "The ONE exception to full geometry preservation" not in prompt


# ---- Item 2 (2026-08-05): resolve_effective_include_product - the single source
# build_image_prompt and pipeline.process_ad both call, so there's one derivation to keep
# in sync rather than two ----

def test_resolve_effective_include_product_forces_false_when_reference_has_no_product():
    bp = {"layout_detail": {"product_count": 0}}
    effective, reference_has_product = generate_image_prompt.resolve_effective_include_product(
        bp, include_product=True, edit_mode=True
    )
    assert effective is False
    assert reference_has_product is False


def test_resolve_effective_include_product_forces_false_when_not_product_category():
    bp = {"product_category": {"category": "not_product"}}
    effective, reference_has_product = generate_image_prompt.resolve_effective_include_product(
        bp, include_product=True, edit_mode=True
    )
    assert effective is False
    assert reference_has_product is False


def test_resolve_effective_include_product_true_when_reference_has_product():
    bp = {"layout_detail": {"product_count": 1}}
    effective, reference_has_product = generate_image_prompt.resolve_effective_include_product(
        bp, include_product=True, edit_mode=True
    )
    assert effective is True
    assert reference_has_product is True


def test_resolve_effective_include_product_operator_false_never_widened():
    """include_product=False (operator's own toggle) must stay False even when the
    reference clearly has a product - reference_has_product only ever narrows, never
    widens, the operator's own choice."""
    bp = {"layout_detail": {"product_count": 2}}
    effective, reference_has_product = generate_image_prompt.resolve_effective_include_product(
        bp, include_product=False, edit_mode=True
    )
    assert effective is False
    assert reference_has_product is True  # the reference itself still has a product


def test_resolve_effective_include_product_noop_outside_edit_mode():
    """Outside edit_mode this must be a no-op regardless of the blueprint - there's no
    reference photograph being edited, so nothing to check the blueprint against."""
    bp = {"layout_detail": {"product_count": 0}, "product_category": {"category": "not_product"}}
    effective, reference_has_product = generate_image_prompt.resolve_effective_include_product(
        bp, include_product=True, edit_mode=False
    )
    assert effective is True
    assert reference_has_product is True


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
        competitor_image_bytes=_png_bytes(),
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


# ---- Item C (2026-08-05): TEXT inherits the reference's own typography/colour instead
# of mapping creative_format to a Besque typeface - reverses Prompt 4 Item 5's
# TYPOGRAPHY_GUIDANCE, which is now removed entirely (this was its only caller). ----

def test_text_branch_inherits_typography_and_colour_from_reference():
    instruction = generate_image_prompt._edit_mode_instruction(
        text_in_image=True, headline="Firmer Skin By Friday"
    )
    assert "weight, casing, and text colour" in instruction
    assert "INHERITED from the reference" in instruction
    assert "the wording is the ONLY thing that changes" in instruction
    # The old Besque-own-typeface wording must be gone.
    assert "Besque's OWN typeface" not in instruction
    assert "never the reference's own font" not in instruction


def test_text_branch_no_longer_takes_a_creative_format_kwarg():
    """creative_format was only ever threaded here for the now-removed TYPOGRAPHY_GUIDANCE
    lookup - passing it must fail loudly (TypeError), not be silently ignored."""
    import pytest
    with pytest.raises(TypeError):
        generate_image_prompt._edit_mode_instruction(text_in_image=True, headline="H",
                                                       creative_format="product_hero")


def test_typography_guidance_removed_entirely():
    assert not hasattr(generate_image_prompt, "TYPOGRAPHY_GUIDANCE")
    assert not hasattr(generate_image_prompt, "DEFAULT_TYPOGRAPHY_GUIDANCE")


def test_typography_guidance_absent_when_no_text_rendered():
    """The TEXT-inheritance clause only applies when text is actually being rendered -
    checked within the TEXT: section specifically, since B's palette-remap sentence also
    legitimately mentions "INHERITED from the reference" (pointing at this same clause)
    regardless of whether text is being rendered this run."""
    instruction = generate_image_prompt._edit_mode_instruction(text_in_image=False)
    text_section = instruction.split("TEXT:")[1].split("OFFER:")[0]
    assert "the wording is the ONLY thing that changes" not in text_section


def test_build_image_prompt_edit_mode_text_inheritance_reaches_the_prompt():
    bp = _blueprint()
    bp["creative_format"] = "founder_story"
    prompt = generate_image_prompt.build_image_prompt(bp, edit_mode=True, text_in_image=True, headline="H")
    assert "INHERITED from the reference" in prompt


# ---- Item B (2026-08-05): retheme_colours' palette remap must never contradict Item C's
# text-colour inheritance or Item D's offer-badge colour preservation - all four
# edit_mode x retheme_colours combinations. ----

def test_retheme_on_edit_mode_on_excludes_text_and_offer_from_palette_remap():
    instruction = generate_image_prompt._edit_mode_instruction(retheme_colours=True)
    assert "NEVER reaches text/typography" in instruction
    assert "a substituted offer/price badge's own colour" in instruction
    assert "INHERITED" in instruction  # points at TEXT's own inheritance wording
    assert "preserved exactly, not re-themed" in instruction


def test_retheme_off_edit_mode_on_has_no_exclusion_clause():
    """retheme_colours=False already reproduces every colour including text/badges - no
    remap happens at all, so there is nothing for an exclusion clause to guard against."""
    instruction = generate_image_prompt._edit_mode_instruction(retheme_colours=False)
    assert "NEVER reaches text/typography" not in instruction


def test_build_image_prompt_edit_mode_on_retheme_on_reaches_prompt():
    prompt = generate_image_prompt.build_image_prompt(_blueprint(), edit_mode=True, retheme_colours=True)
    assert "NEVER reaches text/typography" in prompt


def test_build_image_prompt_edit_mode_on_retheme_off_reaches_prompt():
    prompt = generate_image_prompt.build_image_prompt(_blueprint(), edit_mode=True, retheme_colours=False)
    assert "NEVER reaches text/typography" not in prompt
    assert "colour palette" in prompt  # reverts to the original reproduce-list wording


def test_build_image_prompt_edit_mode_off_retheme_on_unaffected():
    """retheme_colours only matters in edit_mode - the non-edit-mode template path must
    never mention the edit-mode-only exclusion wording regardless of retheme_colours."""
    prompt = generate_image_prompt.build_image_prompt(_blueprint(), edit_mode=False, retheme_colours=True)
    assert "NEVER reaches text/typography" not in prompt


def test_build_image_prompt_edit_mode_off_retheme_off_unaffected():
    prompt = generate_image_prompt.build_image_prompt(_blueprint(), edit_mode=False, retheme_colours=False)
    assert "NEVER reaches text/typography" not in prompt


# ---- generate_image(): palette fetched from brand_settings only when it will be used ----

def test_generate_image_fetches_palette_when_edit_mode_and_retheme_on(monkeypatch, tmp_path):
    from src import dedupe
    monkeypatch.setattr(generate_image_prompt, "genai", type("obj", (), {"Client": _CapturingGenaiClient}))
    monkeypatch.setattr(generate_image_prompt, "ASSET_DIR", tmp_path)
    calls = []
    monkeypatch.setattr(dedupe, "get_brand_settings", lambda: calls.append(1) or {"palette": "custom test palette"})
    # No competitor_image_bytes supplied here (not what this test is about) - Item 6a's
    # aspect-ratio fallback would otherwise hit the real pipeline_warnings table.
    monkeypatch.setattr(dedupe, "init_pipeline_warnings", lambda: None)
    monkeypatch.setattr(dedupe, "record_warning", lambda *a, **k: None)

    generate_image_prompt.generate_image(_blueprint(), "AD_PALETTE", edit_mode=True, retheme_colours=True)
    assert calls == [1]
    assert "custom test palette" in generate_image_prompt.generate_image.last_prompt


def test_generate_image_does_not_fetch_palette_when_retheme_off(monkeypatch, tmp_path):
    from src import dedupe
    monkeypatch.setattr(generate_image_prompt, "genai", type("obj", (), {"Client": _CapturingGenaiClient}))
    monkeypatch.setattr(generate_image_prompt, "ASSET_DIR", tmp_path)
    calls = []
    monkeypatch.setattr(dedupe, "get_brand_settings", lambda: calls.append(1) or {"palette": "should not be used"})
    monkeypatch.setattr(dedupe, "init_pipeline_warnings", lambda: None)
    monkeypatch.setattr(dedupe, "record_warning", lambda *a, **k: None)

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


def test_register_clause_absent_when_no_style():
    assert generate_image_prompt._register_clause(None) == ""
    assert generate_image_prompt._register_clause("") == ""


def test_register_clause_uses_production_style_guidance():
    instruction = generate_image_prompt._register_clause("illustrated")
    assert "not a photograph" in instruction
    assert "hand-drawn bottle inside a photographic frame" in instruction


def test_edit_mode_instruction_style_reaches_register_clause():
    instruction = generate_image_prompt._edit_mode_instruction(style="ugc_native")
    assert "REGISTER:" in instruction
    assert "phone" in instruction.lower()


def test_build_image_prompt_edit_mode_uses_reference_style_by_default():
    bp = _blueprint()
    bp["production_style"] = {"style": "illustrated"}
    prompt = generate_image_prompt.build_image_prompt(bp, edit_mode=True)
    assert "not a photograph" in prompt


def test_build_image_prompt_edit_mode_operator_realism_overrides_reference_style():
    bp = _blueprint()
    bp["production_style"] = {"style": "illustrated"}
    prompt = generate_image_prompt.build_image_prompt(bp, edit_mode=True, realism="ugc_native")
    assert "phone" in prompt.lower()
    assert "not a photograph" not in prompt


def test_build_image_prompt_generate_mode_unaffected_by_realism_param():
    bp = _blueprint()
    assert (generate_image_prompt.build_image_prompt(bp)
            == generate_image_prompt.build_image_prompt(bp, realism="ugc_native"))
