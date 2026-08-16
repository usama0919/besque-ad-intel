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
    """Rule 6 is the single source of the literal quoted headline/subtext (2026-08-13
    fix) - the edit-mode TEXT branch references that authorisation instead of re-quoting
    the string, which was producing 2-3 literal occurrences of the same text in one
    assembled prompt (rule 6, this TEXT branch, and structural zones' sub_line/body_copy
    fallback)."""
    headline = "Firmer Skin By Friday"
    subtext = "7 cold-pressed oils"
    instruction = generate_image_prompt._edit_mode_instruction(
        text_in_image=True, headline=headline, subtext=subtext
    )
    rule6 = generate_image_prompt._rule6_text_policy(text_in_image=True, headline=headline, subtext=subtext)

    assert f'"{headline}"' not in instruction
    assert f'"{subtext}"' not in instruction
    assert "already authorised above" in instruction
    assert f'"{headline}"' in rule6
    assert f'"{subtext}"' in rule6
    assert "preserve the reference image's text zones" in instruction
    assert "RESERVED NEGATIVE SPACE" not in instruction
    assert "NEVER render any headline" not in rule6


def test_build_image_prompt_edit_mode_headline_and_subtext_appear_exactly_once():
    """2026-08-13 fix: the authorised headline/subtext must be stated by rule 6 alone -
    _edit_mode_instruction's TEXT branch previously re-quoted the identical string,
    producing 2 literal occurrences of each in one assembled prompt (3 with an
    overlapping structural zone - see the test directly below)."""
    headline = "Firmer Skin By Friday"
    subtext = "7 cold-pressed oils"
    prompt = generate_image_prompt.build_image_prompt(
        _blueprint(), edit_mode=True, text_in_image=True, headline=headline, subtext=subtext,
    )
    assert prompt.count(f'"{headline}"') == 1
    assert prompt.count(f'"{subtext}"') == 1


def test_build_image_prompt_edit_mode_headline_and_subtext_exactly_once_with_overlapping_zone():
    """The overlapping-zone case: a sub_line structural zone with no distinct panel_copy
    entry previously fell back to re-quoting the same subtext a third time
    (_structural_zones_clause's sub_line/body_copy branch) - it now references rule 6's
    authorisation instead, so the count stays 1 even here."""
    headline = "Firmer Skin By Friday"
    subtext = "7 cold-pressed oils"
    bp = _blueprint()
    bp["structural_zones"] = [{"zone_type": "sub_line", "position": "top-center", "container": "none"}]
    prompt = generate_image_prompt.build_image_prompt(
        bp, edit_mode=True, text_in_image=True, headline=headline, subtext=subtext,
    )
    assert prompt.count(f'"{headline}"') == 1
    assert prompt.count(f'"{subtext}"') == 1


def test_edit_mode_instruction_text_branch_states_entire_text_budget():
    instruction = generate_image_prompt._edit_mode_instruction(
        text_in_image=True, headline="Headline", subtext="Short line."
    )
    text_section = instruction.split("TEXT:")[1].split("OFFER:")[0]
    assert "ENTIRE text budget for this image" in text_section
    assert "ingredient list" in text_section


def test_effective_authorised_text_caps_overlong_subtext():
    """The cap lives in effective_authorised_text, the single mechanical source both
    rule 6 and _edit_mode_instruction derive eff_subtext from - moved here from asserting
    on _edit_mode_instruction's own output (2026-08-13), since that function no longer
    re-quotes the literal subtext at all, only rule 6 does."""
    long_text = " ".join(f"word{i}" for i in range(30))
    _, capped_subtext = generate_image_prompt.effective_authorised_text(
        True, headline="Headline", subtext=long_text
    )
    assert "word11" in capped_subtext
    assert "word12" not in capped_subtext

    rule6 = generate_image_prompt._rule6_text_policy(text_in_image=True, headline="Headline", subtext=long_text)
    assert "word11" in rule6
    assert "word12" not in rule6


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


# ---- Item 1 (2026-08-12): OCCLUDE_PERSON, off by default, switchable via env var only ----

def test_occlusion_disabled_by_default(monkeypatch):
    monkeypatch.delenv("OCCLUDE_PERSON", raising=False)
    assert generate_image_prompt._occlusion_enabled() is False


def test_occlusion_enabled_via_env_var(monkeypatch):
    monkeypatch.setenv("OCCLUDE_PERSON", "1")
    assert generate_image_prompt._occlusion_enabled() is True
    monkeypatch.setenv("OCCLUDE_PERSON", "true")
    assert generate_image_prompt._occlusion_enabled() is True
    monkeypatch.setenv("OCCLUDE_PERSON", "0")
    assert generate_image_prompt._occlusion_enabled() is False


def test_occlude_person_region_noop_when_no_face():
    original = _png_bytes()
    result, occluded = generate_image_prompt._occlude_person_region(original, {"has_face": False})
    assert occluded is False
    assert result == original


def test_occlude_person_region_preserves_dimensions():
    original = _png_bytes(width=600, height=900)
    result, occluded = generate_image_prompt._occlude_person_region(
        original, {"has_face": True, "location": "upper-centre of frame"}
    )
    assert occluded is True
    assert result != original
    img = Image.open(io.BytesIO(result))
    assert img.size == (600, 900)


def test_occlude_person_region_fails_soft_on_corrupt_bytes():
    result, occluded = generate_image_prompt._occlude_person_region(b"not an image", {"has_face": True})
    assert occluded is False
    assert result == b"not an image"


def test_generate_image_occlusion_off_by_default_leaves_bytes_unchanged(monkeypatch, tmp_path):
    monkeypatch.setattr(generate_image_prompt, "genai", type("obj", (), {"Client": _CapturingGenaiClient}))
    monkeypatch.setattr(generate_image_prompt, "ASSET_DIR", tmp_path)
    monkeypatch.delenv("OCCLUDE_PERSON", raising=False)
    competitor_bytes = _png_bytes()
    bp = _blueprint()
    bp["face_present"] = {"has_face": True, "prominence": "primary", "location": "centre"}

    generate_image_prompt.generate_image(
        bp, "AD_OCCLUDE_OFF", edit_mode=True, competitor_image_bytes=competitor_bytes,
        retheme_colours=False,
    )
    contents = _CapturingGenaiClient.last_contents
    assert contents[0].inline_data.data == competitor_bytes
    assert "OCCLUSION NOTICE" not in contents[-1]


def test_generate_image_occlusion_on_blocks_person_and_states_notice(monkeypatch, tmp_path):
    monkeypatch.setattr(generate_image_prompt, "genai", type("obj", (), {"Client": _CapturingGenaiClient}))
    monkeypatch.setattr(generate_image_prompt, "ASSET_DIR", tmp_path)
    monkeypatch.setenv("OCCLUDE_PERSON", "1")
    competitor_bytes = _png_bytes()
    bp = _blueprint()
    bp["face_present"] = {"has_face": True, "prominence": "incidental", "location": "centre"}

    generate_image_prompt.generate_image(
        bp, "AD_OCCLUDE_ON", edit_mode=True, competitor_image_bytes=competitor_bytes,
        retheme_colours=False,
    )
    contents = _CapturingGenaiClient.last_contents
    assert contents[0].inline_data.data != competitor_bytes
    img = Image.open(io.BytesIO(contents[0].inline_data.data))
    assert img.size == (100, 100)  # _png_bytes() default - dimensions unchanged
    assert "OCCLUSION NOTICE" in contents[-1]
    assert "fill that region with a new" in contents[-1].lower()
    assert "must never be reproduced, outlined, or left in the output as a grey block" in contents[-1]


def test_generate_image_occlusion_on_but_no_face_present_leaves_bytes_unchanged(monkeypatch, tmp_path):
    """OCCLUDE_PERSON on is necessary but not sufficient - a reference with no face at
    all must not be touched."""
    monkeypatch.setattr(generate_image_prompt, "genai", type("obj", (), {"Client": _CapturingGenaiClient}))
    monkeypatch.setattr(generate_image_prompt, "ASSET_DIR", tmp_path)
    monkeypatch.setenv("OCCLUDE_PERSON", "1")
    competitor_bytes = _png_bytes()
    bp = _blueprint()
    bp["face_present"] = {"has_face": False, "location": ""}

    generate_image_prompt.generate_image(
        bp, "AD_OCCLUDE_NO_FACE", edit_mode=True, competitor_image_bytes=competitor_bytes,
        retheme_colours=False,
    )
    contents = _CapturingGenaiClient.last_contents
    assert contents[0].inline_data.data == competitor_bytes
    assert "OCCLUSION NOTICE" not in contents[-1]


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


def test_generate_image_illustrated_style_keeps_product_reference_images(monkeypatch, tmp_path):
    """REVERSED 2026-08-14 (live evidence: four generations of the same product produced
    four different bottles - no pump, taller pumpless, squat with pump, one correct - the
    illustrated-register runs being exactly the ones with no real photo to anchor identity
    to, matching pipeline.fetch_reference_images' own comment that visual_description text
    alone "reliably gets pump direction and proportions wrong").

    The 2026-08-06 Grüns GLP-1 leak was the reference photo's PHOTOGRAPHIC REGISTER
    bleeding into the drawing, never its IDENTITY facts (colour, label, proportions) -
    dropping the photo entirely overcorrected by withholding identity along with register.
    realism="illustrated" must now KEEP the product photos attached, same as every other
    style - _edit_mode_instruction's own illustrated branch is what now keeps the DRAWING
    native/never-photorealistic, regardless of what's attached."""
    monkeypatch.setattr(generate_image_prompt, "genai", type("obj", (), {"Client": _CapturingGenaiClient}))
    monkeypatch.setattr(generate_image_prompt, "ASSET_DIR", tmp_path)
    competitor_bytes = _png_bytes()

    generate_image_prompt.generate_image(
        _blueprint(), "AD_ILLUSTRATED", edit_mode=True, realism="illustrated",
        retheme_colours=False,
        competitor_image_bytes=competitor_bytes,
        reference_images=[b"product-photo-1", b"product-photo-2"],
    )
    contents = _CapturingGenaiClient.last_contents
    assert contents[0].inline_data.data == competitor_bytes
    assert [p.inline_data.data for p in contents[1:3]] == [b"product-photo-1", b"product-photo-2"]
    assert "REFERENCE PRODUCT PHOTOS ABOVE" in contents[-1]


def test_generate_image_photographic_style_keeps_product_reference_images(monkeypatch, tmp_path):
    """Every style is unaffected by realism/production_style - product reference photos
    are always attached exactly the same way (see the illustrated test above for why
    illustrated is no longer a special case)."""
    monkeypatch.setattr(generate_image_prompt, "genai", type("obj", (), {"Client": _CapturingGenaiClient}))
    monkeypatch.setattr(generate_image_prompt, "ASSET_DIR", tmp_path)
    competitor_bytes = _png_bytes()

    generate_image_prompt.generate_image(
        _blueprint(), "AD_PHOTOGRAPHIC", edit_mode=True, realism="high_spec_studio",
        retheme_colours=False,
        competitor_image_bytes=competitor_bytes,
        reference_images=[b"product-photo-1", b"product-photo-2"],
    )
    contents = _CapturingGenaiClient.last_contents
    assert [p.inline_data.data for p in contents[1:3]] == [b"product-photo-1", b"product-photo-2"]


# ---- apply_targeted_edit: reference_images (2026-08-15, product-realism edit control)
# - optional, attached only when a caller passes them; every other targeted edit
# (placement/text/background/etc.) omits this and is byte-for-byte unaffected ----

def test_apply_targeted_edit_attaches_no_reference_images_by_default(monkeypatch):
    monkeypatch.setattr(generate_image_prompt, "genai", type("obj", (), {"Client": _CapturingGenaiClient}))
    generate_image_prompt.apply_targeted_edit(_png_bytes(), "Change the headline to X.")
    contents = _CapturingGenaiClient.last_contents
    assert len(contents) == 2  # draft image + instruction text, nothing else
    assert contents[0].inline_data.data == _png_bytes()


def test_apply_targeted_edit_attaches_reference_images_when_given(monkeypatch):
    monkeypatch.setattr(generate_image_prompt, "genai", type("obj", (), {"Client": _CapturingGenaiClient}))
    draft_bytes = _png_bytes()
    generate_image_prompt.apply_targeted_edit(
        draft_bytes, "Re-render rendering treatment only.",
        reference_images=[b"product-photo-1", b"product-photo-2"],
    )
    contents = _CapturingGenaiClient.last_contents
    assert contents[0].inline_data.data == draft_bytes
    assert [p.inline_data.data for p in contents[1:3]] == [b"product-photo-1", b"product-photo-2"]
    framing_and_instruction = contents[-1]
    assert "FIRST IMAGE ABOVE: the current draft to edit" in framing_and_instruction
    assert "REFERENCE PRODUCT PHOTOS ABOVE" in framing_and_instruction
    assert "Re-render rendering treatment only." in framing_and_instruction


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


# ---- Item 6a, REINSTATED 2026-08-07: aspect_ratio IS forced onto the generation config
# in edit mode, derived per-reference via derive_aspect_ratio. Briefly removed the same
# day (a correctly-derived "1:1" on ad 3170893503111146 still produced 1.79:1) - reinstated
# after measuring the SAME reference (ad 893032677180797) produce a close match on one
# omitted-ratio run (0.5581 vs 0.5625) and a badly wrong one on another (0.322 vs 0.5625) -
# omitting is nondeterministic, not a reliable inference, so explicit forcing is the safer
# default despite its own one documented failure. ----

def test_generate_image_edit_mode_sets_aspect_ratio_config_from_reference(monkeypatch, tmp_path):
    monkeypatch.setattr(generate_image_prompt, "genai", type("obj", (), {"Client": _CapturingGenaiClient}))
    monkeypatch.setattr(generate_image_prompt, "ASSET_DIR", tmp_path)

    generate_image_prompt.generate_image(
        _blueprint(), "AD_ASPECT", edit_mode=True, competitor_image_bytes=_png_bytes(1080, 1920),
    )
    config = _CapturingGenaiClient.last_config
    assert config is not None
    assert config.image_config.aspect_ratio == "9:16"
    assert config.image_config.image_size == "2K"


def test_generate_image_generate_mode_sets_image_size_but_no_aspect_ratio(monkeypatch, tmp_path):
    """Generate mode keeps its prompt-text "Square 1:1" instruction and never sets
    aspect_ratio on the config (Item 6a is edit-mode-only) - but as of 2026-08-06,
    image_size is an independent knob and must be set here too: every call before this fix
    ran at Gemini's unset "1K" default (confirmed live - a 1080x1920 reference produced a
    768x1376 draft, under Meta's 1080x1350 minimum for a 4:5 feed image), regardless of
    edit_mode."""
    monkeypatch.setattr(generate_image_prompt, "genai", type("obj", (), {"Client": _CapturingGenaiClient}))
    monkeypatch.setattr(generate_image_prompt, "ASSET_DIR", tmp_path)

    generate_image_prompt.generate_image(_blueprint(), "AD_GENERATE", edit_mode=False)
    config = _CapturingGenaiClient.last_config
    assert config is not None
    assert config.image_config.image_size == "2K"
    assert config.image_config.aspect_ratio is None


def test_generate_image_edit_mode_missing_reference_falls_back_and_warns(monkeypatch, tmp_path):
    """competitor_image_bytes=None (or unreadable) in edit mode must not fail the draft -
    it OMITS aspect_ratio (not a forced "1:1" - forcing the wrong shape is worse than
    omitting when there's genuinely nothing to derive from) and records a pipeline_warning
    instead. image_size is still set - that's an independent knob from aspect_ratio, so the
    fallback must not lose it too."""
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
    config = _CapturingGenaiClient.last_config
    assert config is not None
    assert config.image_config.aspect_ratio is None  # not forced onto the call
    assert config.image_config.image_size == "2K"
    assert len(warnings) == 1
    kind, detail = warnings[0]
    assert kind == "edit_mode_aspect_ratio_fallback"
    assert "AD_ASPECT_FALLBACK" in detail
    assert warnings == []


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
    # text is suppressed (no headline given) and efficacy is ALWAYS suppressed (Item E,
    # 2026-08-06), but offer is NOT - opening should name text + efficacy as removed,
    # never the offer container.
    assert "any container holding text or an efficacy-claim badge that's being suppressed this run" in instruction
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


# ---- Step 2, Part 4 (2026-08-02); REVERSED 2026-08-07 (reference usability gate
# reversal, see CLAUDE.md) - a reference with no product is still a usable scene, so the
# product is now ADDED, never suppressed ----

def test_edit_mode_instruction_reference_has_no_product_adds_one():
    instruction = generate_image_prompt._edit_mode_instruction(reference_has_product=False)
    assert "there is nothing to" in instruction
    assert "ADD the Besque product" in instruction
    assert "do NOT add a Besque product" not in instruction
    assert "Remove the competitor's product entirely and place the Besque product" not in instruction


def test_edit_mode_instruction_reference_has_product_default_substitutes():
    instruction = generate_image_prompt._edit_mode_instruction()
    assert "Remove the competitor's product entirely and place the Besque product" in instruction


def test_build_image_prompt_edit_mode_product_count_zero_adds_product():
    """layout_detail.product_count==0 (deconstruct.py's schema) means the reference ad has
    no product in frame - REVERSED 2026-08-07: the operator's own include_product=True
    toggle now ADDS one into the scene (derived from its own composition), rather than
    being suppressed just because the source had nothing to substitute into."""
    bp = _blueprint()
    bp["layout_detail"] = {"product_count": 0}
    prompt = generate_image_prompt.build_image_prompt(bp, edit_mode=True, include_product=True)
    assert "ADD the Besque product" in prompt
    assert "This is a deliberately productless, educational/illustrative image" not in prompt
    assert "Remove the competitor's product entirely and place the Besque product" not in prompt


def test_build_image_prompt_edit_mode_not_product_category_adds_product():
    bp = _blueprint()
    bp["product_category"] = {"category": "not_product"}
    prompt = generate_image_prompt.build_image_prompt(bp, edit_mode=True, include_product=True)
    assert "ADD the Besque product" in prompt
    assert "This is a deliberately productless, educational/illustrative image" not in prompt


def test_build_image_prompt_edit_mode_product_present_still_substitutes():
    """Regression guard: the new reference_has_product logic must not accidentally
    suppress the normal substitution case when the reference DOES have a product.

    Only product_clause's PLACEMENT sentence is deliberately ABSENT here (2026-08-14
    fix, live incident ad 2390171264812593) - _edit_mode_instruction's own
    substitution branch already placed the product, so also telling Gemini to place
    one asked for a SECOND, independent product. The DESCRIPTIVE facts (ingredients,
    hero claim, bottle material realism) are NOT part of that placement sentence and
    must still reach the prompt regardless - Gemini still needs to know what the
    substituted product looks like. See
    test_build_image_prompt_edit_mode_does_not_duplicate_product_when_substituted for
    the dedicated regression test."""
    bp = _blueprint()
    bp["layout_detail"] = {"product_count": 1}
    product = {"name": "Magic Body Oil", "description": "seven cold-pressed oils",
               "ingredients": "almond; rosehip", "hero_claim": "Visibly firms"}
    prompt = generate_image_prompt.build_image_prompt(bp, product=product, edit_mode=True, include_product=True)
    assert "Remove the competitor's product entirely and place the Besque product" in prompt
    assert "Place the Besque product described below as the subject" not in prompt
    assert "almond; rosehip" in prompt
    assert generate_image_prompt._BOTTLE_MATERIAL_REALISM_CLAUSE in prompt


def test_build_image_prompt_edit_mode_does_not_duplicate_product_when_substituted():
    """Direct regression test for the live incident (ad 2390171264812593, 2026-08-14):
    an illustrated reference already showing a product-shaped element was correctly
    substituted with a Besque bottle in a character's hand, but a SEPARATE
    photorealistic bottle was also composited into the frame - two products, one of
    them in the wrong render medium. Root cause: product_clause's PLACEMENT sentence
    (built purely from effective_include_product - intent, not evidence of scene
    state) was unconditionally appended after _edit_mode_instruction regardless of
    whether substitution already placed a product. Multi-product-count wording is
    included in product_clause too (bp sets product_count=2 here) to prove its
    placement sentence - not just the single-count one - is suppressed, while the
    descriptive facts (ingredients) still survive either way."""
    bp = _blueprint()
    bp["layout_detail"] = {"product_count": 2}
    bp["production_style"] = {"style": "illustrated", "confidence": "high", "signals": []}
    product = {"name": "Magic Body Oil", "description": "seven cold-pressed oils",
               "ingredients": "almond; rosehip", "hero_claim": ""}
    prompt = generate_image_prompt.build_image_prompt(bp, product=product, edit_mode=True, include_product=True)
    # The illustrated register's own substitution wording (draw NATIVELY, not "place ...
    # shown in the reference photo(s)" - that phrasing is photographic-only).
    assert "Remove the competitor's product entirely and draw the Besque product NATIVELY" in prompt
    assert "Place the Besque product described below as the subject" not in prompt
    assert "The reference shows 2 products together" not in prompt
    assert "almond; rosehip" in prompt


def test_build_image_prompt_edit_mode_illustrated_no_product_adds_natively():
    """The illustrated-register ADD branch (2026-08-07, reference usability gate
    reversal): no product in frame, illustrated style - the bottle is drawn NATIVELY into
    the scene's own visual language, same drawing constraints as the substitute-
    illustrated branch, never a photographic composite and never suppressed."""
    bp = _blueprint()
    bp["layout_detail"] = {"product_count": 0}
    bp["production_style"] = {"style": "illustrated"}
    prompt = generate_image_prompt.build_image_prompt(bp, edit_mode=True, include_product=True)
    assert "ADD the Besque product NATIVELY" in prompt
    assert "never a photograph or photorealistic render composited into the drawing" in prompt
    assert "This is a deliberately productless, educational/illustrative image" not in prompt


def test_edit_mode_instruction_add_product_placement_derived_from_composition():
    """Placement in the ADD branch must be DERIVED from the reference's own observed
    layout/composition - never a fixed or default position, and never the same wording
    regardless of what the reference shows."""
    instruction = generate_image_prompt._edit_mode_instruction(
        reference_has_product=False,
        layout_detail={"frame_division": "three stacked horizontal bands",
                        "zone_positions": ["headline top-center", "CTA bottom-full-width"]},
        visual={"layout": "clean centered composition"},
    )
    assert "OBSERVED SCENE COMPOSITION" in instruction
    assert "three stacked horizontal bands" in instruction
    assert "headline top-center" in instruction

    no_facts = generate_image_prompt._edit_mode_instruction(reference_has_product=False)
    assert "OBSERVED SCENE COMPOSITION" not in no_facts
    assert "never a fixed or default position" in no_facts


def test_scene_composition_facts_empty_when_nothing_extracted():
    assert generate_image_prompt._scene_composition_facts(None, None) == ""
    assert generate_image_prompt._scene_composition_facts({}, {}) == ""


def test_scene_composition_facts_reports_observed_fields_only():
    facts = generate_image_prompt._scene_composition_facts(
        {"frame_division": "single uninterrupted gradient ground", "background_type": "studio backdrop"},
        {"layout": "product hero, centered"},
    )
    assert "single uninterrupted gradient ground" in facts
    assert "studio backdrop" in facts
    assert "product hero, centered" in facts


# ---- reference_has_text_zone (2026-08-07, reference usability gate reversal): the
# text-side analogue of reference_has_product - independent, a reference can have a
# product but no text zone, text but no product, both, or neither ----

def test_reference_has_text_zone_true_for_headline():
    assert generate_image_prompt.reference_has_text_zone({"headline_verbatim": "Feel confident again"}) is True


def test_reference_has_text_zone_true_for_text_bearing_structural_zone():
    bp = {"structural_zones": [{"zone_type": "body_copy", "position": "mid", "container": "none", "detail": "x"}]}
    assert generate_image_prompt.reference_has_text_zone(bp) is True


def test_reference_has_text_zone_false_when_neither():
    bp = {"structural_zones": [{"zone_type": "badge", "position": "top", "container": "oval", "detail": "NEW"}]}
    assert generate_image_prompt.reference_has_text_zone(bp) is False
    assert generate_image_prompt.reference_has_text_zone({}) is False


# ---- OFFER_BADGE_KEYWORDS word-boundary matching (2026-08-11): plain substring matching
# let "off" match inside "official"/"offering", "sale" inside "salesperson", "price" inside
# "pricing" - none of these are actually offer-shaped. ----

def test_is_offer_shaped_zone_true_for_real_offer_keywords():
    assert generate_image_prompt._is_offer_shaped_zone("reads 'SAVE 16%'") is True
    assert generate_image_prompt._is_offer_shaped_zone("20% off first order") is True
    assert generate_image_prompt._is_offer_shaped_zone("summer sale") is True
    assert generate_image_prompt._is_offer_shaped_zone("best price guarantee") is True
    assert generate_image_prompt._is_offer_shaped_zone("promo code inside") is True
    assert generate_image_prompt._is_offer_shaped_zone("discount badge") is True
    assert generate_image_prompt._is_offer_shaped_zone("deal of the day") is True


def test_is_offer_shaped_zone_false_for_word_boundary_false_positives():
    """The exact false-positive class plain substring matching produced - "off"/"sale"/
    "price" appearing INSIDE an unrelated word, never as a word of its own."""
    assert generate_image_prompt._is_offer_shaped_zone("official product seal") is False
    assert generate_image_prompt._is_offer_shaped_zone("special offering this month") is False
    assert generate_image_prompt._is_offer_shaped_zone("salesperson recommended") is False
    assert generate_image_prompt._is_offer_shaped_zone("pricing details inside") is False
    assert generate_image_prompt._is_offer_shaped_zone("coffee break gift set") is False


def test_is_offer_shaped_zone_false_for_empty_or_none():
    assert generate_image_prompt._is_offer_shaped_zone("") is False
    assert generate_image_prompt._is_offer_shaped_zone(None) is False


def test_is_offer_shaped_zone_percent_sign_still_matches_without_word_boundary():
    """% is never part of a word, so it deliberately keeps plain substring matching -
    confirms the % branch of the pattern wasn't broken by adding \\b to the others."""
    assert generate_image_prompt._is_offer_shaped_zone("94%") is True


# ---- reference_has_offer_zone (2026-08-11, clone mode): whole-blueprint analogue of
# _is_offer_shaped_zone (which only tests one zone's own detail string) - True when a
# price_anchor is present (inherently offer-shaped, no keyword check needed) or a badge
# whose detail reads as offer/discount-shaped, same OFFER_BADGE_KEYWORDS
# _structural_zones_clause itself uses to decide substitute-vs-remove ----

def test_reference_has_offer_zone_true_for_price_anchor():
    bp = {"structural_zones": [{"zone_type": "price_anchor", "position": "top", "container": "none", "detail": "was $60, now $45"}]}
    assert generate_image_prompt.reference_has_offer_zone(bp) is True


def test_reference_has_offer_zone_true_for_offer_shaped_badge():
    bp = {"structural_zones": [{"zone_type": "badge", "position": "top", "container": "oval", "detail": "reads 'SAVE 16%'"}]}
    assert generate_image_prompt.reference_has_offer_zone(bp) is True


def test_reference_has_offer_zone_false_for_non_offer_badge():
    """A badge exists, but its detail doesn't read as offer-shaped (e.g. a plain "NEW"
    flag or a star rating) - must not be treated as an offer zone."""
    bp = {"structural_zones": [{"zone_type": "badge", "position": "top", "container": "oval", "detail": "reads NEW"}]}
    assert generate_image_prompt.reference_has_offer_zone(bp) is False


def test_reference_has_offer_zone_false_when_no_structural_zones():
    assert generate_image_prompt.reference_has_offer_zone({}) is False
    assert generate_image_prompt.reference_has_offer_zone(None) is False
    assert generate_image_prompt.reference_has_offer_zone({"structural_zones": []}) is False


def test_reference_has_offer_zone_false_for_unrelated_zone_types():
    bp = {"structural_zones": [{"zone_type": "brand_wordmark", "position": "top", "container": "none", "detail": "OSEA"},
                                {"zone_type": "sub_line", "position": "mid", "container": "none", "detail": "tagline"}]}
    assert generate_image_prompt.reference_has_offer_zone(bp) is False


# ---- OFFER prose clause structural gate (2026-08-11): a "20% OFF" badge rendered on a
# draft whose reference had no offer-shaped zone at all - the clause was gated on
# offer_text's own truthiness alone, asking Gemini to judge VISUALLY whether an offer was
# shown. When clone_mode is on, offer_text is only effectively present if
# reference_has_offer_zone actually says so - never Gemini's own call. clone_mode=False
# (the default) is unaffected. ----

_NO_OFFER_ZONE_BLUEPRINT = {"structural_zones": [
    {"zone_type": "brand_wordmark", "position": "top", "container": "none", "detail": "OSEA"},
]}
_HAS_OFFER_ZONE_BLUEPRINT = {"structural_zones": [
    {"zone_type": "price_anchor", "position": "top", "container": "none", "detail": "was $60, now $45"},
]}


def test_edit_mode_offer_clause_unaffected_when_clone_mode_off():
    """clone_mode=False (the default, omitted here) - offer_text's own truthiness is the
    only gate, byte-for-byte today's behaviour, even with no offer-shaped zone at all."""
    prompt = generate_image_prompt.build_image_prompt(
        _NO_OFFER_ZONE_BLUEPRINT, edit_mode=True, offer_text="20% off",
    )
    assert 'replace ONLY its wording with: 20% off' in prompt
    assert "no offer was supplied for this run" not in prompt


def test_edit_mode_offer_clause_suppressed_when_clone_mode_on_and_no_offer_zone():
    """The actual live incident this closes: clone_mode on, offer_text set on the run
    strip, reference has no offer-shaped zone - the OFFER clause must ban, not ask
    Gemini to invent one."""
    prompt = generate_image_prompt.build_image_prompt(
        _NO_OFFER_ZONE_BLUEPRINT, edit_mode=True, offer_text="20% off", clone_mode=True,
    )
    assert 'replace ONLY its wording with: 20% off' not in prompt
    assert "no offer was supplied for this run" in prompt


def test_edit_mode_offer_clause_still_fires_when_clone_mode_on_and_offer_zone_present():
    """clone_mode must not suppress a real, structurally-detected offer zone - only the
    invented-from-nothing case."""
    prompt = generate_image_prompt.build_image_prompt(
        _HAS_OFFER_ZONE_BLUEPRINT, edit_mode=True, offer_text="20% off", clone_mode=True,
    )
    assert 'replace ONLY its wording with: 20% off' in prompt
    assert "no offer was supplied for this run" not in prompt


def test_edit_mode_offer_clause_and_suppression_exception_never_disagree():
    """suppressing_offer (governs the container-removal exception wording elsewhere in
    the prompt) and the OFFER clause itself must key off the SAME effective value - the
    exact demand-and-forbid shape that produced artifact 1136's fabricated testimonials,
    on a different input, if they were ever allowed to disagree."""
    prompt = generate_image_prompt.build_image_prompt(
        _NO_OFFER_ZONE_BLUEPRINT, edit_mode=True, offer_text="20% off", clone_mode=True,
        text_in_image=True, headline="Feel Confident Again",
    )
    assert "no offer was supplied for this run" in prompt
    assert "an offer" in prompt  # from the container-removal exception's own active list
    assert 'replace ONLY its wording with: 20% off' not in prompt


def test_edit_mode_instruction_text_added_into_negative_space_when_no_reference_text_zone():
    """TEXT branch ADD path: an authorised headline with NO existing reference text zone
    to substitute into must be placed newly, in clean negative space derived from the
    reference's own composition - never the "preserve the reference's text zones
    exactly" wording, which is vacuous/wrong when no such zone exists."""
    instruction = generate_image_prompt._edit_mode_instruction(
        text_in_image=True, headline="Feel confident again", reference_has_text_zone=False,
    )
    assert "the reference has no existing text zone to substitute into" in instruction
    assert "clean negative space" in instruction
    assert "preserve the reference image's text zones EXACTLY as they appear" not in instruction


def test_edit_mode_instruction_text_substituted_when_reference_has_text_zone():
    """Regression guard: the default (reference_has_text_zone=True) must stay
    byte-for-byte the original substitute wording."""
    instruction = generate_image_prompt._edit_mode_instruction(
        text_in_image=True, headline="Feel confident again",
    )
    assert "preserve the reference image's text zones EXACTLY as they appear" in instruction
    assert "the reference has no existing text zone to substitute into" not in instruction


def test_build_image_prompt_product_count_above_one_still_yields_single_product_instruction():
    """REVERSED 2026-08-12 (live failure, product_count=5): the old version of this test
    asserted "place 2 of the Besque product" - a real run showed that instruction directly
    contradicting rule 7 ("exactly one bottle... NEVER add a second... whether copied from
    the competitor ad or invented") in the SAME prompt. The critic flagged it HIGH and the
    actual output rendered 8 bottles. Rule 7 wins unconditionally now: a reference-derived
    count above 1 must still yield an instruction for exactly ONE Besque product, with the
    composition adapting around it rather than multiplying to match the reference."""
    bp = _blueprint()
    bp["layout_detail"] = {"product_count": 2}
    product = {"name": "Magic Body Oil", "description": "seven cold-pressed oils",
               "ingredients": "almond; rosehip", "hero_claim": "Visibly firms"}
    prompt = generate_image_prompt.build_image_prompt(bp, product=product, include_product=True)
    assert "place 2 of the Besque product" not in prompt
    assert "place 5 of the Besque product" not in prompt
    assert "renders as exactly ONE bottle" in prompt
    assert "rule 7 above permits exactly one" in prompt
    assert "Adapt the COMPOSITION around that single bottle" in prompt
    assert "never shrink the single bottle to read like part of a missing set" in prompt


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


# ---- 2026-08-12 15:13 sweep: the oil inside the bottle read flat and uniform - a
# GENERIC, behavioural clause (no hardcoded colour/material - those come from
# product.visual_description and the reference photos), distinct from
# _substance_recolour_clause above (which governs substance that has LEFT the
# bottle, not the oil still inside it). ----

def test_bottle_material_realism_clause_covers_liquid_glass_pump_and_label():
    clause = generate_image_prompt._BOTTLE_MATERIAL_REALISM_CLAUSE
    assert "meniscus" in clause
    assert "translucency" in clause and "viscosity" in clause
    assert "refracts and reflects" in clause
    assert "separate, unrelated studio lighting" in clause
    assert "specular highlight" in clause
    assert "wraps the bottle's own curve" in clause


def test_build_image_prompt_includes_bottle_material_realism_when_product_shown():
    prompt = generate_image_prompt.build_image_prompt(_blueprint(), edit_mode=True)
    assert generate_image_prompt._BOTTLE_MATERIAL_REALISM_CLAUSE in prompt


def test_build_image_prompt_includes_bottle_material_realism_in_generate_mode_too():
    """Not edit-mode-specific - the flat-template and writer paths share the same
    product_clause, so this must reach both, not just the edit-mode branch."""
    prompt = generate_image_prompt.build_image_prompt(_blueprint())
    assert generate_image_prompt._BOTTLE_MATERIAL_REALISM_CLAUSE in prompt


def test_build_image_prompt_omits_bottle_material_realism_when_productless():
    prompt = generate_image_prompt.build_image_prompt(_blueprint(), edit_mode=True, include_product=False)
    assert generate_image_prompt._BOTTLE_MATERIAL_REALISM_CLAUSE not in prompt


# ---- 2026-08-14: the material realism clause is style-aware - the photoreal clause's
# meniscus/refraction/specular-highlight language directly contradicted an illustrated
# register's own "never photorealistic" instruction elsewhere in the same prompt (same
# class as the 12 Aug contradictions) ----

def test_build_image_prompt_illustrated_uses_illustrated_material_clause_not_photoreal():
    bp = _blueprint()
    bp["production_style"] = {"style": "illustrated", "confidence": "high", "signals": []}
    prompt = generate_image_prompt.build_image_prompt(bp, edit_mode=True)
    assert generate_image_prompt._BOTTLE_MATERIAL_REALISM_CLAUSE_ILLUSTRATED in prompt
    assert generate_image_prompt._BOTTLE_MATERIAL_REALISM_CLAUSE not in prompt


def test_build_image_prompt_photoreal_uses_photoreal_material_clause_not_illustrated():
    bp = _blueprint()
    bp["production_style"] = {"style": "high_spec_studio", "confidence": "high", "signals": []}
    prompt = generate_image_prompt.build_image_prompt(bp, edit_mode=True)
    assert generate_image_prompt._BOTTLE_MATERIAL_REALISM_CLAUSE in prompt
    assert generate_image_prompt._BOTTLE_MATERIAL_REALISM_CLAUSE_ILLUSTRATED not in prompt


def test_bottle_material_realism_clause_illustrated_never_demands_photorealistic_liquid():
    """The illustrated variant explicitly BANS the photoreal clause's photographic
    physical-realism demands (never asserts them as a requirement) - a flat, drawn
    oil/glass/hardware is explicitly CORRECT in this register, never a failure to fix."""
    clause = generate_image_prompt._BOTTLE_MATERIAL_REALISM_CLAUSE_ILLUSTRATED
    assert "never a photorealistic meniscus" in clause
    assert "refracts and reflects" not in clause  # the photoreal clause's own DEMAND wording
    assert "never glass refraction, never specular highlights" in clause
    assert "FLAT" in clause
    # Content-fidelity guarantees carry over unchanged from the photoreal clause.
    assert "never a change to what the label actually contains" in clause
    assert "BESQUE wordmark and the product name at minimum" in clause


# ---- 2026-08-13: small-scale label legibility - a genuine gap, not a duplicate of the
# curved-wrap sentence (that's geometry/decal-vs-wrapped; this is information density
# at small render size). Added to the SAME clause, not a competing second one, and
# explicitly reconciled with _bottle_fixed_clause's "label text/layout are FIXED". ----

def test_bottle_material_realism_clause_covers_small_scale_label_simplification():
    clause = generate_image_prompt._BOTTLE_MATERIAL_REALISM_CLAUSE
    assert "small fraction of the frame" in clause
    assert "BESQUE wordmark and the product name at minimum" in clause
    assert "dropping certification icons, border/rule detail, and fine print" in clause


def test_bottle_material_realism_clause_small_scale_addition_never_changes_real_content():
    """Must be explicit that this is a rendering simplification, not a content change -
    or it would read as contradicting _bottle_fixed_clause's "label text/layout are
    FIXED"."""
    clause = generate_image_prompt._BOTTLE_MATERIAL_REALISM_CLAUSE
    assert "never a change to what the label actually contains" in clause
    assert "full label" in clause and "still applies whenever the bottle is rendered large" in clause


def test_bottle_material_realism_clause_still_states_curved_wrap_fidelity_unchanged():
    """The pre-existing wrap-fidelity sentence must survive untouched alongside the new
    size-driven addition - this is an extension, not a replacement."""
    clause = generate_image_prompt._BOTTLE_MATERIAL_REALISM_CLAUSE
    assert "wraps the bottle's own curve" in clause
    assert "flat decal pasted" in clause


def test_bottle_material_realism_clause_never_invents_a_colour_or_material():
    """Deliberately generic - naming a specific colour/material not present in
    product.visual_description would be an invented product fact (compliance C3)."""
    clause = generate_image_prompt._BOTTLE_MATERIAL_REALISM_CLAUSE
    for invented in ("amber", "red label", "black and gold", "terracotta"):
        assert invented not in clause.lower()


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
    """The exception must live in the SAME paragraph as the "carries over CLOSELY... "
    claim - not a separate clause elsewhere a reader could take as contradicting it.
    Mirrors test_retheme_colours_on_states_one_integrated_instruction's shape.
    2026-08-12: "carry over EXACTLY" became "carry over CLOSELY" (item 4's 5-8%
    background-variation allowance) - the structural claim under test (same paragraph,
    before product-substitution text) is unchanged."""
    instruction = generate_image_prompt._edit_mode_instruction(text_in_image=False)
    assert "carry over CLOSELY as shot in the reference" in instruction
    assert "never a different composition" in instruction
    assert "The ONE exception to full geometry preservation" in instruction
    assert "is removed entirely, not preserved empty" in instruction
    # Both must appear in the SAME opening paragraph, before the product-substitution text.
    carries_over_pos = instruction.index("carry over CLOSELY")
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


def test_suppression_exception_names_every_container_type_for_efficacy_only_case():
    """Item E (2026-08-06, PART B2): the SAME container-type list must apply to the new
    third case too, not just text/offer - proven with text and offer BOTH unsuppressed
    (a real headline shown, a real offer supplied), so efficacy is the ONLY active
    category and this test can't accidentally pass via the text or offer wording instead.
    Hardcoded literal, NOT _SUPPRESSIBLE_CONTAINER_TYPES - same reasoning as
    test_suppression_exception_names_every_container_type above: a test that derives its
    expectation from the constant under test passes vacuously if an entry is ever deleted
    from it."""
    instruction = generate_image_prompt._edit_mode_instruction(
        text_in_image=True, headline="Firmer Skin By Friday", offer_text="free shipping this week",
    )
    assert "any container holding an efficacy-claim badge that's being suppressed this run" in instruction
    for container in ("badge", "pill", "oval", "button", "banner", "ribbon", "starburst"):
        assert container in instruction


def test_suppression_exception_present_but_efficacy_only_when_neither_text_nor_offer_is_suppressed():
    """A headline is shown AND a real offer is given - text/offer suppression are both
    OFF - but Item E (2026-08-06) made efficacy-claim-badge suppression unconditional (no
    approved_claims threading to images exists, so it's never toggled), so the exception
    is never fully absent any more - only text/offer's OWN naming drops out. Renamed from
    "_absent_..." (its pre-Item-E name, which is no longer true) rather than left
    misleading."""
    instruction = generate_image_prompt._edit_mode_instruction(
        text_in_image=True, headline="Firmer Skin By Friday", offer_text="free shipping this week"
    )
    assert "The ONE exception to full geometry preservation" in instruction
    assert "any container holding an efficacy-claim badge that's being suppressed this run" in instruction
    assert "any container that held the suppressed text" not in instruction


def test_suppression_exception_present_when_only_offer_is_suppressed():
    """The gap found during 6d: a headline IS shown (no text suppression) but no offer was
    given (offer suppression still active) - the exception must still fire, naming the
    offer and efficacy (Item E, always-on), never text."""
    instruction = generate_image_prompt._edit_mode_instruction(
        text_in_image=True, headline="Firmer Skin By Friday", offer_text=None,
    )
    assert "The ONE exception to full geometry preservation" in instruction
    assert "any container holding an offer or an efficacy-claim badge that's being suppressed this run" in instruction
    assert "any container holding text" not in instruction


def test_suppression_exception_present_when_only_text_is_suppressed():
    instruction = generate_image_prompt._edit_mode_instruction(
        text_in_image=False, offer_text="free shipping this week",
    )
    assert "The ONE exception to full geometry preservation" in instruction
    assert "any container holding text or an efficacy-claim badge that's being suppressed this run" in instruction
    assert "any container holding an offer" not in instruction


def test_suppression_exception_names_all_three_when_text_offer_and_efficacy_all_suppressed():
    instruction = generate_image_prompt._edit_mode_instruction(text_in_image=False, offer_text=None)
    assert ("any container holding text, an offer, or an efficacy-claim badge that's being "
            "suppressed this run") in instruction


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


def test_build_image_prompt_edit_mode_text_and_offer_unsuppressed_still_has_efficacy_exception():
    """Renamed (was "..._neither_suppressed_omits_suppression_exception") - Item E
    (2026-08-06) made efficacy-claim-badge suppression unconditional, so even with text
    and offer both unsuppressed, the exception still fires for efficacy alone."""
    prompt = generate_image_prompt.build_image_prompt(
        _blueprint(), edit_mode=True, text_in_image=True, headline="Firmer Skin By Friday",
        offer_text="free shipping this week",
    )
    assert "any container holding an efficacy-claim badge that's being suppressed this run" in prompt


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

def test_resolve_effective_include_product_never_forces_false_when_reference_has_no_product():
    """REVERSED 2026-08-07 (reference usability gate reversal, see CLAUDE.md): a
    productless reference no longer forces effective_include_product off - it is always
    exactly the operator's own toggle now. reference_has_product is still returned, but
    only to select ADD-vs-SUBSTITUTE wording downstream, never the boolean outcome."""
    bp = {"layout_detail": {"product_count": 0}}
    effective, reference_has_product = generate_image_prompt.resolve_effective_include_product(
        bp, include_product=True, edit_mode=True
    )
    assert effective is True
    assert reference_has_product is False


def test_resolve_effective_include_product_never_forces_false_when_not_product_category():
    bp = {"product_category": {"category": "not_product"}}
    effective, reference_has_product = generate_image_prompt.resolve_effective_include_product(
        bp, include_product=True, edit_mode=True
    )
    assert effective is True
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
    # 2026-08-12: "geometry is preserved" gained a small-variation allowance (item 4) -
    # no longer the bare "geometry is preserved, colour is substituted" phrase, but still
    # ONE integrated instruction, not two competing ones.
    assert "geometry is preserved" in instruction
    assert "colour is substituted" in instruction
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
    assert "geometry is preserved" in prompt
    assert "colour is substituted" in prompt


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


def test_register_clause_uses_style_guidance():
    instruction = generate_image_prompt._register_clause("illustrated")
    assert "Pixar" in instruction
    assert "RECOGNISABLE by silhouette" in instruction
    assert "hand-drawn bottle inside a photographic frame" in instruction


def test_register_clause_illustrated_label_never_says_photorealistic():
    """2026-08-06, Grüns GLP-1 leak: STYLE_GUIDANCE's illustrated entry used to say the
    label keeps "photorealistic label detail", which directly contradicted the
    bottle-rendering-matches-scene clause in the same prompt and produced a photorealistic
    bottle composited into an otherwise hand-drawn scene. The label must stay accurate and
    legible, but rendered in the scene's own illustrated visual language, never
    photorealistic."""
    instruction = generate_image_prompt._register_clause("illustrated")
    assert "photorealistic label detail" not in instruction
    assert "illustrated visual language" in instruction


def test_register_clause_illustrated_drops_secondary_label_legibility_demand():
    """Same live incident (2026-08-06): the old wording demanded the WHOLE label stay
    legible ("angled so it reads clearly") - the critic correctly flagged sub-lines/cert
    icons as illegible at illustrated scale, confirming that demand can't survive in this
    register. Only the product NAME needs to stay legible; secondary label content
    (sub-lines, cert icons, fine print) explicitly does not."""
    instruction = generate_image_prompt._register_clause("illustrated")
    assert "does NOT need to stay legible" in instruction
    assert "sub-lines, certification icons, fine print" in instruction


def test_register_clause_states_faithful_reproduction_wins_over_style_vocabulary():
    # Chunk 13 follow-up: the register vocabulary must never read as license to
    # re-stage the reference's own composition/framing/lighting - the exception is
    # folded into this same clause, not appended as a separate contradicting one.
    instruction = generate_image_prompt._register_clause("ugc_native")
    assert "faithful reproduction wins" in instruction
    assert "reproduce-faithfully instruction above" in instruction


def test_edit_mode_instruction_style_reaches_register_clause():
    instruction = generate_image_prompt._edit_mode_instruction(style="ugc")
    assert "REGISTER:" in instruction
    assert "phone" in instruction.lower()


# ---- style=="illustrated": the product-substitution sentence describes the bottle
# drawn natively, using any attached reference photo for IDENTITY only, never as a
# rendering-style reference (2026-08-06, Grüns GLP-1 leak; REVERSED 2026-08-14 - photos
# are attached again for this style, see generate_image's own docstring for why) ----

def test_edit_mode_instruction_illustrated_never_points_at_photo_for_rendering_style():
    """The old photographic-substitute wording ("shown in the reference photo(s) that
    follow... matching the original shot's composition") must not appear for this style
    - that phrasing asks Gemini to match the PHOTO's own look, which is exactly the
    2026-08-06 leak. The instruction must instead say the photo (if any) is identity-only
    and that native, non-photorealistic drawing applies regardless of the photo."""
    instruction = generate_image_prompt._edit_mode_instruction(style="illustrated")
    assert "shown in the reference photo(s) that follow" not in instruction
    assert "matching the original shot's composition" not in instruction
    assert "never as a rendering-style reference" in instruction.lower()
    assert "regardless" in instruction.lower()
    # The genuine no-photo fallback wording must still exist for a product with zero
    # configured reference images - not removed, only no longer the unconditional case.
    # No longer names "silhouette" here (2026-08-16): shape is always fixed by
    # _bottle_geometry_clause, with or without a photo - only colour/name are ever
    # derived from "no photo attached".
    assert "work from colour and" in instruction.lower()


def test_edit_mode_instruction_illustrated_names_product_by_name():
    instruction = generate_image_prompt._edit_mode_instruction(
        style="illustrated", product_name="Magic Body Oil",
    )
    assert '"Magic Body Oil"' in instruction


def test_edit_mode_instruction_illustrated_falls_back_to_besque_name():
    """No product_name given - same fallback rule 4 already states for the unbranded
    case, not a silent gap."""
    instruction = generate_image_prompt._edit_mode_instruction(style="illustrated")
    assert '"Besque"' in instruction


def test_edit_mode_instruction_illustrated_drops_secondary_legibility_demand():
    instruction = generate_image_prompt._edit_mode_instruction(style="illustrated")
    assert "does not need to be legible" in instruction


def test_edit_mode_instruction_illustrated_drawn_natively_never_photorealistic():
    instruction = generate_image_prompt._edit_mode_instruction(style="illustrated")
    assert "NATIVELY" in instruction
    assert "never a photograph or photorealistic render composited" in instruction


def test_edit_mode_instruction_photographic_style_unaffected_by_illustrated_branch():
    """Every non-illustrated style keeps pointing at the reference photo exactly as
    before - this is an illustrated-only change."""
    instruction = generate_image_prompt._edit_mode_instruction(style="high_spec_studio")
    assert "shown in the reference photo(s) that follow" in instruction
    assert "NATIVELY" not in instruction


def test_edit_mode_instruction_illustrated_still_recolours_substance():
    """The illustrated branch must not silently drop the substance-recolour clause the
    photographic branch already carries."""
    instruction = generate_image_prompt._edit_mode_instruction(
        style="illustrated", substance_colour="bright golden-amber oil",
    )
    assert "bright golden-amber oil" in instruction


def test_build_image_prompt_edit_mode_uses_reference_style_by_default():
    bp = _blueprint()
    bp["production_style"] = {"style": "illustrated"}
    prompt = generate_image_prompt.build_image_prompt(bp, edit_mode=True)
    assert "Pixar" in prompt


def test_build_image_prompt_edit_mode_operator_realism_overrides_reference_style():
    bp = _blueprint()
    bp["production_style"] = {"style": "illustrated"}
    prompt = generate_image_prompt.build_image_prompt(bp, edit_mode=True, realism="ugc")
    assert "phone" in prompt.lower()
    assert "Pixar" not in prompt


def test_build_image_prompt_generate_mode_unaffected_by_realism_param():
    # NOTE: confirmed pre-existing failure at HEAD (before any 2026-08-11 work), unrelated
    # to the production_style enum rename - realism demonstrably DOES reach the
    # STYLE_GUIDANCE lookup in the flat-template branch (build_image_prompt.py's `else`
    # branch), contradicting this test's own name/assertion. Left as-is (still
    # "ugc_native", the literal it had at HEAD) rather than silently "fixed" into passing
    # - not in scope for this task, flagging for a separate fix.
    bp = _blueprint()
    assert (generate_image_prompt.build_image_prompt(bp)
            == generate_image_prompt.build_image_prompt(bp, realism="ugc_native"))


# ---- PART B3b (2026-08-06): per-zone typographic TREATMENT, not just per-zone content -
# a real reference with 4 distinct typographic levels produced a draft with only 2, because
# nothing named the other two levels explicitly and Gemini defaulted to one style everywhere.

def _zone(**overrides):
    z = {"zone": "headline upper-right", "typeface_class": "serif", "weight": "bold",
         "case": "title", "letter_spacing": "normal", "colour": "white",
         "size_relative": "large", "decorative_elements": [], "line_count": 2}
    z.update(overrides)
    return z


def test_typography_zones_clause_empty_for_blank_input():
    for blank in (None, []):
        assert generate_image_prompt._typography_zones_clause(blank) == ""


def test_typography_zones_clause_states_every_field_per_zone():
    clause = generate_image_prompt._typography_zones_clause([
        _zone(zone="headline upper-right", typeface_class="serif", weight="bold",
              case="title", letter_spacing="normal", colour="white", size_relative="large",
              decorative_elements=[], line_count=2),
        _zone(zone="ingredient sub-copy mid-right", typeface_class="sans", weight="light",
              case="sentence", letter_spacing="wide", colour="gold", size_relative="small",
              decorative_elements=["pipe divider between clauses"], line_count=3),
    ])
    assert "TYPOGRAPHIC LEVELS" in clause
    assert "2 distinct typographic level(s)" in clause
    assert "never collapsing two into one" in clause
    # First zone
    assert "headline upper-right" in clause
    assert "serif typeface" in clause
    assert "bold weight" in clause
    assert "title case" in clause
    assert "normal letter-spacing" in clause
    assert "colour white" in clause
    assert "large relative to the frame" in clause
    assert "2 line(s)" in clause
    # Second zone - distinct treatment, not collapsed into the first's
    assert "ingredient sub-copy mid-right" in clause
    assert "sans typeface" in clause
    assert "wide letter-spacing" in clause
    assert "colour gold" in clause
    assert "pipe divider between clauses" in clause
    assert "3 line(s)" in clause


def test_typography_zones_clause_never_raises_on_missing_fields():
    """A partially-filled zone (an older blueprint, or a field Claude omitted) must
    degrade to "?" placeholders, never crash the prompt assembly."""
    clause = generate_image_prompt._typography_zones_clause([{"zone": "headline"}])
    assert "headline" in clause
    assert "? typeface" in clause
    assert "? line(s)" in clause


def test_edit_mode_instruction_forwards_typography_zones():
    instruction = generate_image_prompt._edit_mode_instruction(
        typography_zones=[_zone(zone="CTA button", typeface_class="sans")]
    )
    assert "TYPOGRAPHIC LEVELS" in instruction
    assert "CTA button" in instruction


def test_edit_mode_instruction_no_typography_zones_section_when_absent():
    instruction = generate_image_prompt._edit_mode_instruction()
    assert "TYPOGRAPHIC LEVELS" not in instruction


def test_build_image_prompt_edit_mode_reads_typography_zones_from_blueprint():
    bp = _blueprint()
    bp["typography_zones"] = [_zone(zone="offer banner bottom-right")]
    prompt = generate_image_prompt.build_image_prompt(bp, edit_mode=True)
    assert "TYPOGRAPHIC LEVELS" in prompt
    assert "offer banner bottom-right" in prompt


def test_build_image_prompt_generate_mode_unaffected_by_typography_zones():
    """typography_zones is edit-mode only (per _edit_mode_instruction) - the flat template/
    writer paths never read it, so a blueprint carrying it must produce byte-for-byte the
    same generate-mode prompt as one without it."""
    bp_plain = _blueprint()
    bp_with_zones = _blueprint()
    bp_with_zones["typography_zones"] = [_zone()]
    assert (generate_image_prompt.build_image_prompt(bp_plain)
            == generate_image_prompt.build_image_prompt(bp_with_zones))


# ---- structural_zones generator wiring (2026-08-06, generalised 2026-08-07) - every zone
# type now has a real substitute-or-remove decision: brand_wordmark (always), sub_line/
# body_copy/cta (only when text_in_image supplies real content, else removed), badge/
# price_anchor/product_callout (substituted when a genuine Besque counterpart is supplied
# this call - offer_text/certifications/product_name respectively - else removed),
# disclaimer (always removed, no exception), social_proof (untouched, no instruction
# either way). ----

def _szone(zone_type, **overrides):
    z = {"zone_type": zone_type, "position": "top-center", "container": "none"}
    z.update(overrides)
    return z


def test_structural_zones_clause_empty_for_blank_input():
    clause, substituted = generate_image_prompt._structural_zones_clause(None)
    assert clause == "" and substituted == set()
    clause, substituted = generate_image_prompt._structural_zones_clause([])
    assert clause == "" and substituted == set()


def test_structural_zones_clause_brand_wordmark_always_substituted():
    """The single biggest visible gap in every draft so far - never removed, never
    gated on text_in_image, unlike everything else this function handles."""
    clause, substituted = generate_image_prompt._structural_zones_clause(
        [_szone("brand_wordmark", position="top-center", container="oval")]
    )
    assert "brand_wordmark" in substituted
    assert "replace its content with BESQUE" in clause
    assert "never removed" in clause
    assert "top-center" in clause and "oval" in clause


def test_structural_zones_clause_sub_line_and_body_copy_substituted_when_text_supplied():
    """No panel_copy - both zones hit the zone_copy_text fallback, which references rule
    6's authorisation rather than re-quoting the literal subtext (2026-08-13 fix): the
    old re-quote here was a third literal occurrence of the same string, alongside rule 6
    and _edit_mode_instruction's own TEXT branch."""
    clause, substituted = generate_image_prompt._structural_zones_clause(
        [_szone("sub_line"), _szone("body_copy")], zone_copy_text="7 cold-pressed oils.",
    )
    assert substituted == {"sub_line", "body_copy"}
    assert "7 cold-pressed oils." not in clause
    assert clause.count("already authorised above") == 2
    assert "matching the reference's own line count" in clause


def test_structural_zones_clause_panel_copy_routes_distinct_text_by_position():
    """2026-08-06, Grüns GLP-1 leak: a two-panel before/after joke rendered the SAME
    headline text in both panels, because every sub_line zone got the SAME zone_copy_text
    regardless of position. panel_copy must route each zone to ITS OWN text by exact
    position match."""
    clause, substituted = generate_image_prompt._structural_zones_clause(
        [_szone("sub_line", position="upper-left-mid"), _szone("sub_line", position="upper-right-mid")],
        zone_copy_text="fallback text - must not appear when panel_copy covers both",
        panel_copy=[
            {"position": "upper-left-mid", "text": "Skin feeling looser?"},
            {"position": "upper-right-mid", "text": "Give it what it needs."},
        ],
    )
    assert substituted == {"sub_line"}
    assert '"Skin feeling looser?"' in clause
    assert '"Give it what it needs."' in clause
    assert "fallback text" not in clause
    assert clause.count("upper-left-mid") == 1
    assert clause.count("upper-right-mid") == 1


def test_structural_zones_clause_panel_copy_falls_back_when_position_uncovered():
    """panel_copy given but missing an entry for one zone's position - that zone falls
    back to referencing rule 6's authorisation (2026-08-13: not re-quoting the literal
    zone_copy_text, which duplicated the string already stated by rule 6) rather than
    being silently dropped."""
    clause, substituted = generate_image_prompt._structural_zones_clause(
        [_szone("sub_line", position="upper-left-mid"), _szone("sub_line", position="upper-right-mid")],
        zone_copy_text="shared fallback",
        panel_copy=[{"position": "upper-left-mid", "text": "Skin feeling looser?"}],
    )
    assert substituted == {"sub_line"}
    assert '"Skin feeling looser?"' in clause
    assert "shared fallback" not in clause
    assert "already authorised above" in clause


def test_structural_zones_clause_panel_copy_absent_reproduces_shared_text_behaviour():
    """No panel_copy at all - byte-for-byte the same single-shared-text behaviour as
    before this feature existed."""
    with_panel_copy_absent = generate_image_prompt._structural_zones_clause(
        [_szone("sub_line"), _szone("body_copy")], zone_copy_text="7 cold-pressed oils.",
    )
    with_panel_copy_none = generate_image_prompt._structural_zones_clause(
        [_szone("sub_line"), _szone("body_copy")], zone_copy_text="7 cold-pressed oils.", panel_copy=None,
    )
    assert with_panel_copy_absent == with_panel_copy_none


def test_structural_zones_clause_panel_copy_malformed_entries_ignored():
    """A malformed panel_copy entry (missing position/text, or not even a dict) must
    never raise - it's silently skipped and the zone falls back to referencing rule 6's
    authorisation (2026-08-13: not the literal zone_copy_text string)."""
    clause, substituted = generate_image_prompt._structural_zones_clause(
        [_szone("sub_line", position="upper-left-mid")],
        zone_copy_text="fallback",
        panel_copy=["not-a-dict", {"position": "upper-left-mid"}, {"text": "no position"}, {}],
    )
    assert substituted == {"sub_line"}
    assert "already authorised above" in clause
    assert "fallback" not in clause


def test_structural_zones_clause_sub_line_and_body_copy_removed_when_no_text():
    """No Besque text for this run (text_in_image off, or no copy) - never leave the
    reference's own sub-line/body-copy sitting there untouched."""
    clause, substituted = generate_image_prompt._structural_zones_clause(
        [_szone("sub_line"), _szone("body_copy")], zone_copy_text=None,
    )
    assert substituted == set()
    assert "STRUCTURAL ZONES - REMOVE" in clause
    assert "sub_line" in clause and "body_copy" in clause
    assert "STRUCTURAL ZONES - SUBSTITUTE" not in clause


def test_structural_zones_clause_cta_substituted_when_text_supplied():
    clause, substituted = generate_image_prompt._structural_zones_clause(
        [_szone("cta", position="bottom-center", container="rect")], cta_text="Shop Besque Magic Body Oil",
    )
    assert substituted == {"cta"}
    assert "Shop Besque Magic Body Oil" in clause
    assert "same button shape and position" in clause


def test_structural_zones_clause_cta_removed_when_no_text():
    clause, substituted = generate_image_prompt._structural_zones_clause(
        [_szone("cta")], cta_text=None,
    )
    assert substituted == set()
    assert "STRUCTURAL ZONES - REMOVE" in clause


def test_structural_zones_clause_removes_badge_price_callout_with_no_counterpart_supplied():
    """No offer_text/certifications/product_name supplied this call - every one of these
    falls to REMOVAL, same outcome as before this generalised, but now because no
    counterpart was GIVEN, not because none could ever exist. disclaimer has no exception
    either way."""
    no_counterpart = ["badge", "disclaimer", "price_anchor", "product_callout"]
    clause, substituted = generate_image_prompt._structural_zones_clause(
        [_szone(zt) for zt in no_counterpart],
        zone_copy_text="irrelevant", cta_text="irrelevant",  # even with text available
    )
    assert substituted == set()
    for zt in no_counterpart:
        assert zt in clause
    assert "STRUCTURAL ZONES - SUBSTITUTE" not in clause


def test_structural_zones_clause_badge_award_shaped_always_removes():
    """An award/editorial/endorsement badge has no Besque counterpart - removed even when
    certifications AND offer_text are both supplied, since award-shape wins the check
    order unconditionally."""
    clause, substituted = generate_image_prompt._structural_zones_clause(
        [_szone("badge", detail="Allure Best of Beauty Award Winner")],
        certifications=["Vegan", "Cruelty Free", "100% Natural"], offer_text="20% off",
    )
    assert substituted == set()
    assert "STRUCTURAL ZONES - SUBSTITUTE" not in clause
    assert "badge" in clause


def test_structural_zones_clause_badge_offer_shaped_substitutes_with_offer_text():
    clause, substituted = generate_image_prompt._structural_zones_clause(
        [_szone("badge", detail="reads 'SAVE 16%'")], offer_text="20% off your first order",
    )
    assert substituted == {"badge"}
    assert "20% off your first order" in clause
    assert "offer/discount badge" in clause


def test_structural_zones_clause_badge_offer_shaped_removes_when_no_offer_text():
    clause, substituted = generate_image_prompt._structural_zones_clause(
        [_szone("badge", detail="reads 'SAVE 16%'")], offer_text=None,
    )
    assert substituted == set()
    assert "STRUCTURAL ZONES - SUBSTITUTE" not in clause


def test_structural_zones_clause_badge_offer_shaped_removes_when_offer_text_empty_string():
    """offer_text="" (as opposed to None) must still fall to removal, not be treated as a
    supplied value - same empty/NULL/empty-list guard the field-driven substitutions share."""
    clause, substituted = generate_image_prompt._structural_zones_clause(
        [_szone("badge", detail="reads 'SAVE 16%'")], offer_text="",
    )
    assert substituted == set()
    assert "STRUCTURAL ZONES - SUBSTITUTE" not in clause
    assert "offer/discount badge" not in clause
    assert "STRUCTURAL ZONES - REMOVE" in clause
    assert "badge" in clause


def test_structural_zones_clause_badge_cert_shaped_substitutes_with_certifications():
    clause, substituted = generate_image_prompt._structural_zones_clause(
        [_szone("badge", detail="USDA Organic certification seal")],
        certifications=["Vegan", "Cruelty Free", "100% Natural"],
    )
    assert substituted == {"badge"}
    assert "Vegan, Cruelty Free, 100% Natural" in clause


def test_structural_zones_clause_badge_cert_shaped_removes_when_certifications_empty_list():
    """certifications=[] (as opposed to None) must still fall to removal - an empty list
    is not a supplied counterpart."""
    clause, substituted = generate_image_prompt._structural_zones_clause(
        [_szone("badge", detail="USDA Organic certification seal")], certifications=[],
    )
    assert substituted == set()
    assert "STRUCTURAL ZONES - SUBSTITUTE" not in clause
    assert "certification badge" not in clause
    assert "STRUCTURAL ZONES - REMOVE" in clause
    assert "badge" in clause


def test_structural_zones_clause_price_anchor_substitutes_with_offer_text():
    clause, substituted = generate_image_prompt._structural_zones_clause(
        [_szone("price_anchor", detail="was $60, now $45")], offer_text="20% off",
    )
    assert substituted == {"price_anchor"}
    assert "20% off" in clause
    assert "never the competitor's own price" in clause


def test_structural_zones_clause_price_anchor_removes_when_offer_text_empty_string():
    """offer_text="" must still fall to removal, not be treated as a supplied value."""
    clause, substituted = generate_image_prompt._structural_zones_clause(
        [_szone("price_anchor", detail="was $60, now $45")], offer_text="",
    )
    assert substituted == set()
    assert "STRUCTURAL ZONES - SUBSTITUTE" not in clause
    assert "never the competitor's own price" not in clause
    assert "STRUCTURAL ZONES - REMOVE" in clause
    assert "price_anchor" in clause


def test_structural_zones_clause_product_callout_substitutes_with_distinct_callout_copy():
    """REVERSED 2026-08-12 (Item 2): product_callout no longer takes the bare
    product_name - confirmed live, ad 1576971893931336, four distinct reference callouts
    all rendered the identical "Besque Magic Body Oil". Now keyed by THIS zone's own
    position into callout_copy (generate_copy.py's per-zone panel_copy, ungated by
    text_in_image)."""
    clause, substituted = generate_image_prompt._structural_zones_clause(
        [_szone("product_callout", detail="reads 'New Scent'")],
        callout_copy=[{"position": "top-center", "text": "Fast-Absorbing"}],
    )
    assert substituted == {"product_callout"}
    assert "Fast-Absorbing" in clause
    assert "never the product name" in clause


def test_structural_zones_clause_product_callout_removes_when_no_callout_copy():
    """No callout_copy entry for this zone's position - removed, not filled with a
    repeated fallback string (the actual bug this reverses)."""
    clause, substituted = generate_image_prompt._structural_zones_clause(
        [_szone("product_callout", detail="reads 'New Scent'")], callout_copy=None,
    )
    assert substituted == set()
    assert "STRUCTURAL ZONES - SUBSTITUTE" not in clause
    assert "STRUCTURAL ZONES - REMOVE" in clause
    assert "product_callout" in clause


def test_structural_zones_clause_product_callout_multiple_zones_get_distinct_copy():
    """The actual regression test for the reported bug: 3+ callout zones, each getting
    its OWN distinct string, never the same text repeated across zones."""
    zones = [
        {"zone_type": "product_callout", "position": "mid-left", "container": "rect", "detail": "flame icon, thermogenic"},
        {"zone_type": "product_callout", "position": "mid-left-lower", "container": "rect", "detail": "lightning icon, energy"},
        {"zone_type": "product_callout", "position": "mid-right", "container": "rect", "detail": "silhouette icon, tightening"},
        {"zone_type": "product_callout", "position": "mid-right-lower", "container": "rect", "detail": "nail icon, nail and hair"},
    ]
    callout_copy = [
        {"position": "mid-left", "text": "Melts Fat Fast"},
        {"position": "mid-left-lower", "text": "All-Day Energy"},
        {"position": "mid-right", "text": "Visibly Tighter Skin"},
        # mid-right-lower deliberately omitted
    ]
    clause, substituted = generate_image_prompt._structural_zones_clause(
        zones, callout_copy=callout_copy,
    )
    assert substituted == {"product_callout"}
    assert "Melts Fat Fast" in clause
    assert "All-Day Energy" in clause
    assert "Visibly Tighter Skin" in clause
    # every substituted string must be genuinely distinct - none repeated
    texts = ["Melts Fat Fast", "All-Day Energy", "Visibly Tighter Skin"]
    assert len(set(texts)) == 3
    # the 4th zone (no callout copy) must be REMOVED, not duplicated with any other text
    assert "mid-right-lower" in clause
    assert "STRUCTURAL ZONES - REMOVE" in clause


# ---- _is_stat_shaped_zone (2026-08-11): shape-based, not a list of known numbers - reuses
# compliance.py's own NUMERIC_CLAIM_PATTERN/RATIO_CLAIM_PATTERN/TIMESCALE_CLAIM_PATTERN so a
# stat-shaped detail is detected by its FORM (any percentage, any "N out of M", any "Nx
# more/faster", any "in N days/weeks/hours"), never a specific value from any real ad. ----

def test_is_stat_shaped_zone_true_for_percentage():
    assert generate_image_prompt._is_stat_shaped_zone("94% saw visible results") is True


def test_is_stat_shaped_zone_true_for_ratio_claim():
    assert generate_image_prompt._is_stat_shaped_zone("9 out of 10 customers agree") is True
    assert generate_image_prompt._is_stat_shaped_zone("3x faster absorption") is True


def test_is_stat_shaped_zone_true_for_timescale_claim():
    assert generate_image_prompt._is_stat_shaped_zone("results in just 7 days") is True


def test_is_stat_shaped_zone_false_for_non_stat_control():
    """A bottle size is a number too, but it's not a stat/efficacy claim shape - a
    control case proving this isn't just "does the string contain a digit"."""
    assert generate_image_prompt._is_stat_shaped_zone("reads 8 fl oz / 240ml") is False
    assert generate_image_prompt._is_stat_shaped_zone("New Scent card - Coconut Vanilla") is False
    assert generate_image_prompt._is_stat_shaped_zone("") is False
    assert generate_image_prompt._is_stat_shaped_zone(None) is False


def test_structural_zones_clause_product_callout_removes_when_stat_shaped_even_with_callout_copy():
    """A callout whose reference content is a statistic has no Besque counterpart - a
    benefit phrase must NOT be substituted in, even though callout_copy supplies one for
    this exact position and would normally win."""
    clause, substituted = generate_image_prompt._structural_zones_clause(
        [_szone("product_callout", detail="91% saw visibly firmer skin in 4 weeks")],
        callout_copy=[{"position": "top-center", "text": "Visibly Firmer Skin"}],
    )
    assert substituted == set()
    assert "STRUCTURAL ZONES - SUBSTITUTE" not in clause
    assert "Visibly Firmer Skin" not in clause
    assert "STRUCTURAL ZONES - REMOVE" in clause
    assert "product_callout" in clause


def test_structural_zones_clause_social_proof_aggregate_bar_always_removed():
    """2026-08-06, fabricated-testimonials fix: an aggregate_bar (review count/star
    average) has no approved figure to substitute (held pending Harry - see CLAUDE.md),
    so it must always be REMOVED, never left for the general reproduce-faithfully
    instruction to govern - that was the actual bug (Gemini invented a plausible count)."""
    clause, substituted = generate_image_prompt._structural_zones_clause(
        [_szone("social_proof", social_proof_kind="aggregate_bar")],
        zone_copy_text="text", cta_text="cta",
    )
    assert "STRUCTURAL ZONES - REMOVE, SOCIAL PROOF" in clause
    assert "NEVER invent a customer quote" in clause
    assert substituted == set()


def test_structural_zones_clause_social_proof_single_quote_removed_without_real_review():
    """No real review supplied - the zone must be removed, never left as an invitation
    for Gemini to invent one (the exact live bug this fix closes)."""
    clause, substituted = generate_image_prompt._structural_zones_clause(
        [_szone("social_proof", social_proof_kind="single_quote")],
        testimonial=None,
    )
    assert "STRUCTURAL ZONES - REMOVE, SOCIAL PROOF" in clause
    assert substituted == set()


def test_structural_zones_clause_social_proof_single_quote_substituted_with_real_review():
    clause, substituted = generate_image_prompt._structural_zones_clause(
        [_szone("social_proof", social_proof_kind="single_quote", position="lower-third")],
        testimonial={"quote": "This oil changed my skin.", "attribution": "Jane D."},
    )
    assert "STRUCTURAL ZONES - SUBSTITUTE" in clause
    assert '"This oil changed my skin."' in clause
    assert "Jane D." in clause
    assert "REAL customer review" in clause
    assert "rendered EXACTLY as given, never reworded" in clause
    assert "social_proof" in substituted


def test_structural_zones_clause_social_proof_missing_attribution_falls_back():
    clause, substituted = generate_image_prompt._structural_zones_clause(
        [_szone("social_proof", social_proof_kind="single_quote")],
        testimonial={"quote": "Great oil.", "attribution": ""},
    )
    assert "a verified customer" in clause
    assert "social_proof" in substituted


def test_structural_zones_clause_social_proof_unrecognised_kind_removed():
    """An unrecognised/missing social_proof_kind must be conservative (REMOVE), never
    fall through to no-instruction-at-all, which is what let the original bug happen."""
    clause, substituted = generate_image_prompt._structural_zones_clause(
        [_szone("social_proof", social_proof_kind=None)],
        testimonial={"quote": "Great oil.", "attribution": "Jane D."},
    )
    assert "STRUCTURAL ZONES - REMOVE, SOCIAL PROOF" in clause
    assert "unspecified kind" in clause
    assert substituted == set()


# ---- testimonial_zones consumption (2026-08-13, Item 1): PLACEMENT/STYLING only, never
# content - select_testimonial_review stays the sole content source ----

def test_structural_zones_clause_testimonial_zones_adds_styling_detail():
    clause, substituted = generate_image_prompt._structural_zones_clause(
        [_szone("social_proof", social_proof_kind="single_quote", position="lower-third")],
        testimonial={"quote": "This oil changed my skin.", "attribution": "Jane D."},
        testimonial_zones=[{
            "text_verbatim": "competitor's own fabricated quote",
            "attribution": "Some Competitor Customer",
            "placement": "bottom-center white card",
            "styling": "Avatar thumbnail top-left, reaction bar below quote",
        }],
    )
    assert "Avatar thumbnail top-left, reaction bar below quote" in clause
    assert "Position it at bottom-center white card" in clause
    # content still comes ONLY from `testimonial`, never testimonial_zones
    assert "competitor's own fabricated quote" not in clause
    assert "Some Competitor Customer" not in clause
    assert '"This oil changed my skin."' in clause
    assert "Jane D." in clause
    assert "social_proof" in substituted


def test_structural_zones_clause_testimonial_zones_absent_unaffected():
    """No testimonial_zones given (every pre-existing caller) - byte-for-byte the same
    substitution as before this item existed, just without the extra styling detail."""
    clause, substituted = generate_image_prompt._structural_zones_clause(
        [_szone("social_proof", social_proof_kind="single_quote", position="lower-third")],
        testimonial={"quote": "This oil changed my skin.", "attribution": "Jane D."},
        testimonial_zones=None,
    )
    assert '"This oil changed my skin."' in clause
    assert "Jane D." in clause
    assert "Match this reference's own styling" not in clause


def test_structural_zones_clause_testimonial_zones_matched_ordinally_not_by_string():
    """Two social_proof/single_quote zones, two testimonial_zones entries - matched by
    ORDER encountered, not by comparing position/placement strings (deliberately
    independently-worded free-text fields, never guaranteed to match textually)."""
    zones = [
        _szone("social_proof", social_proof_kind="single_quote", position="upper-third"),
        _szone("social_proof", social_proof_kind="single_quote", position="lower-third"),
    ]
    testimonial_zones = [
        {"styling": "FIRST card styling", "placement": "top area"},
        {"styling": "SECOND card styling", "placement": "bottom area"},
    ]
    clause, substituted = generate_image_prompt._structural_zones_clause(
        zones, testimonial=None, testimonial_zones=testimonial_zones,
    )
    # both zones removed (no real review), but styling detail is never used on removal -
    # this just confirms ordinal indexing doesn't crash/misbehave when zones outnumber
    # or match testimonial_zones one-to-one, regardless of substitute-vs-remove outcome.
    assert substituted == set()


# ---- 2026-08-12 evening: TESTIMONIAL RENDERING TWICE regression - a reference with
# TWO social_proof/single_quote zones (e.g. a speech-bubble quote AND a caption block
# reiterating it) got the SAME real review substituted into BOTH, with no cap on how
# many qualifying zones the loop would fill. Only ONE real review exists per run -
# it renders in the FIRST zone; any further zone falls to REMOVE, same as "no real
# review at all", rather than repeating the identical quote+attribution a second time. ----

def test_structural_zones_clause_testimonial_renders_exactly_once_across_two_zones():
    zones = [
        _szone("social_proof", social_proof_kind="single_quote", position="mid-frame"),
        _szone("social_proof", social_proof_kind="single_quote", position="lower-third"),
    ]
    clause, substituted = generate_image_prompt._structural_zones_clause(
        zones, testimonial={"quote": "This oil changed my skin.", "attribution": "sally p."},
    )
    assert clause.count("This oil changed my skin.") == 1
    assert clause.count("sally p.") == 1
    assert "STRUCTURAL ZONES - SUBSTITUTE" in clause
    assert "STRUCTURAL ZONES - REMOVE, SOCIAL PROOF" in clause
    assert substituted == {"social_proof"}


def test_structural_zones_clause_testimonial_placement_respects_styling_only_for_first_zone():
    """The styling/placement detail from testimonial_zones must still attach only to
    the ONE zone that actually gets the real review - not to the removed duplicate."""
    zones = [
        _szone("social_proof", social_proof_kind="single_quote", position="mid-frame"),
        _szone("social_proof", social_proof_kind="single_quote", position="lower-third"),
    ]
    clause, substituted = generate_image_prompt._structural_zones_clause(
        zones, testimonial={"quote": "This oil changed my skin.", "attribution": "sally p."},
        testimonial_zones=[{"styling": "Avatar top-left", "placement": "mid-frame card"}],
    )
    assert clause.count("Avatar top-left") == 1
    assert substituted == {"social_proof"}


def test_structural_zones_clause_handles_several_of_the_same_type():
    clause, substituted = generate_image_prompt._structural_zones_clause(
        [_szone("badge", position="top-left"), _szone("badge", position="top-right")]
    )
    assert clause.count("badge") == 2
    assert "top-left" in clause and "top-right" in clause


def test_edit_mode_instruction_forwards_structural_zones_and_cta_text():
    instruction = generate_image_prompt._edit_mode_instruction(
        text_in_image=True, headline="H", subtext="S",
        structural_zones=[_szone("brand_wordmark")], cta_text="Shop Now",
    )
    assert "STRUCTURAL ZONES - SUBSTITUTE" in instruction
    assert "replace its content with BESQUE" in instruction


def test_edit_mode_instruction_forwards_panel_copy_end_to_end():
    instruction = generate_image_prompt._edit_mode_instruction(
        text_in_image=True, headline="H", subtext="shared fallback",
        structural_zones=[
            _szone("sub_line", position="upper-left-mid"),
            _szone("sub_line", position="upper-right-mid"),
        ],
        panel_copy=[
            {"position": "upper-left-mid", "text": "Skin feeling looser?"},
            {"position": "upper-right-mid", "text": "Give it what it needs."},
        ],
    )
    assert '"Skin feeling looser?"' in instruction
    assert '"Give it what it needs."' in instruction


def test_edit_mode_instruction_panel_copy_suppressed_when_text_in_image_off():
    """Same gating rule as zone_copy_text/cta_text - panel_copy must never leak into the
    prompt when the operator asked for no baked-in text this run; those zones fall to
    removal instead."""
    instruction = generate_image_prompt._edit_mode_instruction(
        text_in_image=False,
        structural_zones=[_szone("sub_line", position="upper-left-mid")],
        panel_copy=[{"position": "upper-left-mid", "text": "Skin feeling looser?"}],
    )
    assert "Skin feeling looser?" not in instruction


def test_edit_mode_instruction_forwards_testimonial_end_to_end():
    instruction = generate_image_prompt._edit_mode_instruction(
        text_in_image=True, headline="H",
        structural_zones=[_szone("social_proof", social_proof_kind="single_quote")],
        testimonial={"quote": "This oil changed my skin.", "attribution": "Jane D."},
    )
    assert '"This oil changed my skin."' in instruction
    assert "Jane D." in instruction


def test_edit_mode_instruction_testimonial_not_suppressed_when_text_in_image_off():
    """REVERSED 2026-08-13 (Item 1 of the testimonial/callout/garbling task): testimonial
    used to be gated by text_in_image the SAME way zone_copy_text/cta_text/panel_copy
    are - this test used to assert the review was REMOVED here. That gate was a category
    error: text_in_image decides whether headline/subtext bake into the image or render
    as a separate HTML overlay; a testimonial CARD (avatar, name, quote, reaction bar) is
    not headline text competing for that slot, it's a self-contained visual element, the
    same category as badge/price_anchor/certifications (none of which are gated by
    text_in_image either). Confirmed live: ad 1354698976158962 had a real review ready
    (select_testimonial_review found one) and a genuine social_proof/single_quote zone,
    but text_in_image=False on that run silently dropped it anyway - the zone was never
    short of content, it was gated on the wrong flag. The ONLY gate a testimonial zone
    needs is "no real review -> remove, never invent", tested separately below."""
    instruction = generate_image_prompt._edit_mode_instruction(
        text_in_image=False,
        structural_zones=[_szone("social_proof", social_proof_kind="single_quote")],
        testimonial={"quote": "This oil changed my skin.", "attribution": "Jane D."},
    )
    assert "This oil changed my skin." in instruction
    assert "Jane D." in instruction
    assert "STRUCTURAL ZONES - REMOVE, SOCIAL PROOF" not in instruction


def test_edit_mode_instruction_gates_zone_copy_and_cta_on_text_in_image():
    """text_in_image=False must suppress zone_copy_text/cta_text the SAME way it already
    suppresses headline/subtext - sub_line/body_copy/cta fall to removal, never showing
    the reference's own words just because text_in_image happened to be off."""
    instruction = generate_image_prompt._edit_mode_instruction(
        text_in_image=False, headline="H", subtext="S",
        structural_zones=[_szone("sub_line"), _szone("cta")], cta_text="Shop Now",
    )
    assert "STRUCTURAL ZONES - SUBSTITUTE" not in instruction
    assert "STRUCTURAL ZONES - REMOVE" in instruction


def test_edit_mode_instruction_text_budget_unchanged_when_no_structural_substitution():
    """Byte-for-byte the original wording when structural_zones is absent or doesn't
    substitute anything - every existing blueprint, and every reference without
    sub_line/body_copy/cta, must see no change here at all."""
    with_zones = generate_image_prompt._edit_mode_instruction(
        text_in_image=True, headline="H", subtext="S",
        structural_zones=[_szone("badge")],  # present, but only removes - never substitutes
    )
    without_zones = generate_image_prompt._edit_mode_instruction(
        text_in_image=True, headline="H", subtext="S", structural_zones=None,
    )
    assert "no ingredient list, mechanism or benefit paragraph, additional body copy, or CTA sentence may ALSO be rendered" in with_zones
    assert "no ingredient list, mechanism or benefit paragraph, additional body copy, or CTA sentence may ALSO be rendered" in without_zones


def test_edit_mode_instruction_text_budget_relaxes_only_for_substituted_categories():
    instruction = generate_image_prompt._edit_mode_instruction(
        text_in_image=True, headline="H", subtext="S",
        structural_zones=[_szone("cta")], cta_text="Shop Now",
    )
    assert "no ingredient list or mechanism/benefit paragraph may ALSO be rendered" in instruction
    assert "beyond what STRUCTURAL ZONES below explicitly authorises" in instruction
    assert "additional body copy, or CTA sentence may ALSO be rendered" not in instruction


def test_build_image_prompt_edit_mode_reads_structural_zones_and_cta_from_blueprint():
    bp = _blueprint()
    bp["structural_zones"] = [_szone("brand_wordmark")]
    prompt = generate_image_prompt.build_image_prompt(bp, edit_mode=True, cta_text="Shop Now")
    assert "replace its content with BESQUE" in prompt


def test_build_image_prompt_generate_mode_unaffected_by_structural_zones():
    bp_plain = _blueprint()
    bp_with_zones = _blueprint()
    bp_with_zones["structural_zones"] = [_szone("brand_wordmark")]
    assert (generate_image_prompt.build_image_prompt(bp_plain, cta_text="Shop Now")
            == generate_image_prompt.build_image_prompt(bp_with_zones, cta_text="Shop Now"))


# ---- Item 4 (2026-08-12): background variation - 5-8% variation from the REFERENCE,
# phrased as a partition of the reproduce-faithfully instruction, never a competing one ----

def test_edit_mode_background_variation_states_percentage_and_scope():
    instruction = generate_image_prompt._edit_mode_instruction()
    assert "5-8%" in instruction
    assert "camera angle, framing, background detail, or prop arrangement" in instruction
    assert "never a different composition" in instruction


def test_edit_mode_background_variation_present_with_retheme_off_too():
    """Both opening branches (retheme_colours True/False) must carry the variation
    allowance - it's about geometry, not colour."""
    instruction = generate_image_prompt._edit_mode_instruction(retheme_colours=False)
    assert "5-8%" in instruction


def test_edit_mode_background_variation_not_a_competing_instruction():
    """Must read as a PARTITION of the SAME single instruction, not a separate clause
    that a reader (or the model) could interpret as fighting the reproduce-faithfully
    claim - the same shape as the colour re-theming clause."""
    instruction = generate_image_prompt._edit_mode_instruction(retheme_colours=True)
    assert "not two competing ones" in instruction
    variation_pos = instruction.index("5-8%")
    two_parts_pos = instruction.index("not two competing ones")
    assert two_parts_pos < variation_pos


# ---- Item 6 (2026-08-12): semantic_split consumption - before/after semantics are
# INTERNAL to the image (half vs half), a different axis from item 4's output-vs-reference
# variation ----

def test_semantic_split_clause_empty_when_not_split():
    assert generate_image_prompt._semantic_split_clause(None) == ""
    assert generate_image_prompt._semantic_split_clause({"is_split": False}) == ""


def test_semantic_split_clause_states_same_subject_pose_camera_lighting():
    clause = generate_image_prompt._semantic_split_clause({
        "is_split": True, "split_axis": "vertical",
        "left_or_before": "dry, crepey skin with visible fine lines",
        "right_or_after": "smooth, hydrated skin with visible firmness",
    })
    assert "BEFORE/AFTER SEMANTICS" in clause
    assert "SAME subject" in clause and "SAME pose" in clause
    assert "SAME camera angle" in clause and "SAME lighting" in clause
    assert "dry, crepey skin with visible fine lines" in clause
    assert "smooth, hydrated skin with visible firmness" in clause
    assert "VISIBLE IMPROVEMENT" in clause


def test_semantic_split_clause_states_it_is_a_different_axis_from_background_variation():
    """Must say so explicitly, per instruction, so the two can never be read as competing."""
    clause = generate_image_prompt._semantic_split_clause({"is_split": True, "split_axis": "horizontal"})
    assert "DIFFERENT axis" in clause
    assert "REFERENCE photograph" in clause


def test_build_image_prompt_semantic_split_reaches_flat_template_and_writer_branches():
    """Not edit-mode-only - internal half-vs-half consistency matters regardless of
    which branch assembles the scene text."""
    bp = _blueprint()
    bp["semantic_split"] = {"is_split": True, "split_axis": "vertical",
                             "left_or_before": "before state", "right_or_after": "after state"}
    flat_prompt = generate_image_prompt.build_image_prompt(bp)
    assert "before state" in flat_prompt and "after state" in flat_prompt


# ---- Item 8 (2026-08-12): face_present consumption - face-to-body substitution ----

def test_face_present_absent_uses_existing_person_clause():
    instruction = generate_image_prompt._edit_mode_instruction(face_present=None)
    assert "PERSON:" in instruction
    assert "PERSON -> BODY AREA" not in instruction


def test_face_present_incidental_uses_existing_person_clause():
    instruction = generate_image_prompt._edit_mode_instruction(
        face_present={"has_face": True, "prominence": "incidental", "location": "background"}
    )
    assert "PERSON:" in instruction
    assert "PERSON -> BODY AREA" not in instruction


def test_face_present_primary_replaces_person_clause_with_body_area():
    instruction = generate_image_prompt._edit_mode_instruction(
        face_present={"has_face": True, "prominence": "primary", "location": "centre-frame close-up"}
    )
    assert "PERSON -> BODY AREA" in instruction
    assert "arm, neck, stomach, or legs" in instruction
    assert "NOT a crop" in instruction and "NOT a face-swap" in instruction
    # The general REPRODUCE/SUBSTITUTE PERSON clause must not ALSO be present - one
    # instruction, not two competing ones about the same subject.
    assert "PERSON: if a person appears" not in instruction


def test_face_present_primary_still_defers_to_rules_10_and_11():
    instruction = generate_image_prompt._edit_mode_instruction(
        face_present={"has_face": True, "prominence": "primary", "location": "centre"}
    )
    assert "Rules 10/11 above" in instruction


def test_build_image_prompt_edit_mode_reads_face_present_from_blueprint():
    bp = _blueprint()
    bp["face_present"] = {"has_face": True, "prominence": "primary", "location": "centre"}
    prompt = generate_image_prompt.build_image_prompt(bp, edit_mode=True)
    assert "PERSON -> BODY AREA" in prompt


# ---- Item 9 (2026-08-12): text duplication - one unified canvas, highest risk in
# split-screen/before-after/multi-panel layouts ----

def test_canvas_unity_clause_present_when_text_substituted_into_existing_zone():
    instruction = generate_image_prompt._edit_mode_instruction(
        text_in_image=True, headline="Firmer skin, naturally",
    )
    assert "The output canvas is ONE unified space" in instruction
    assert "EXACTLY ONCE across the entire frame" in instruction
    assert "never once per panel or half" in instruction


def test_canvas_unity_clause_present_when_text_added_to_negative_space():
    instruction = generate_image_prompt._edit_mode_instruction(
        text_in_image=True, headline="Firmer skin, naturally", reference_has_text_zone=False,
    )
    assert "The output canvas is ONE unified space" in instruction


def test_canvas_unity_clause_absent_when_text_suppressed():
    """Nothing to duplicate when text itself is off - must not appear as dead weight."""
    instruction = generate_image_prompt._edit_mode_instruction(text_in_image=False)
    assert "The output canvas is ONE unified space" not in instruction
