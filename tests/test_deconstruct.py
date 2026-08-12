"""Tests for the deconstruction step — mocks Claude's response, no API call."""
import json
import pytest
from src import deconstruct


def _fake_claude_json():
    return json.dumps({
        "ad_id": "AD123",
        "source_page": "CompetitorPage",
        "captured_at": "2026-01-01T00:00:00Z",
        "format": "testimonial_card",
        "hook": {"type": "social_proof", "headline_structure": "quote + result"},
        "angle": "confidence at any age",
        "awareness_stage": "solution",
        "claims": ["efficacy", "social_proof"],
        "visual": {"layout": "portrait", "subject": "woman", "palette_mood": "warm", "text_placement": "lower third"},
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
    })


def test_parse_plain_json():
    raw = _fake_claude_json()
    bp = deconstruct.parse_blueprint(raw)
    assert bp["ad_id"] == "AD123"


def test_parse_strips_markdown_fences():
    raw = "```json\n" + _fake_claude_json() + "\n```"
    bp = deconstruct.parse_blueprint(raw)
    assert bp["format"] == "testimonial_card"


def test_deconstruct_valid_response_passes_schema():
    raw = _fake_claude_json()
    bp = deconstruct.deconstruct_from_response(raw)
    assert bp["awareness_stage"] == "solution"


def test_deconstruct_invalid_response_raises():
    bad = json.dumps({"ad_id": "X"})  # missing required fields
    with pytest.raises(ValueError):
        deconstruct.deconstruct_from_response(bad)


def test_build_prompt_inserts_values():
    prompt = deconstruct.build_prompt("AD1", "PageX", "2026-01-01", "https://x.com")
    assert "AD1" in prompt and "PageX" in prompt


def test_build_prompt_production_style_options_match_validator():
    """production_style_options is built from validator.production_styles(), not a
    repeated literal - so it can't drift from what validation_error() actually accepts."""
    from src import validator
    prompt = deconstruct.build_prompt("AD1", "PageX", "2026-01-01")
    for style in validator.production_styles():
        assert style in prompt
    assert "illustrated" in prompt


def test_build_prompt_instructs_naming_medical_signals_explicitly():
    """Prompt 4, Item 3: content_safety.hard_block_reason reads product_category.signals
    for medical/clinical/anatomical keywords - this only works if the classifier prompt
    actually instructs Claude to name that content explicitly there, rather than folding
    it silently into a generic "other"/"not_product" classification."""
    prompt = deconstruct.build_prompt("AD1", "PageX", "2026-01-01")
    assert "medical" in prompt.lower()
    assert "anatomical" in prompt.lower()
    assert "intimate-health" in prompt.lower() or "intimate health" in prompt.lower()
    assert "hard-block" in prompt.lower() or "hard block" in prompt.lower()


def test_build_prompt_includes_new_creative_fields_without_format_error():
    """Part B: creative_objective/target_audience/typography/expanded layout_detail were
    added to BLUEPRINT_PROMPT, which is itself run through .format() in build_prompt() -
    an unescaped literal brace in any of the new text would raise KeyError here."""
    prompt = deconstruct.build_prompt("AD1", "PageX", "2026-01-01")
    assert "creative_objective" in prompt
    assert "target_audience" in prompt
    assert "typography" in prompt
    assert "headline_face" in prompt
    assert "hierarchy_levels" in prompt
    assert "zone_positions" in prompt
    assert "has_bottom_banner" in prompt
    assert "frame_division" in prompt


def test_build_prompt_includes_typography_zones_without_format_error():
    """PART B3b (2026-08-06): typography_zones was added to BLUEPRINT_PROMPT, itself run
    through .format() in build_prompt() - same KeyError risk as the Part B fields above if
    a literal brace in the new text weren't escaped."""
    prompt = deconstruct.build_prompt("AD1", "PageX", "2026-01-01")
    assert "typography_zones" in prompt
    assert "letter_spacing" in prompt
    assert "decorative_elements" in prompt
    assert "line_count" in prompt


def test_deconstruct_response_with_typography_zones_passes_schema():
    """Optional field (2026-08-06 schema addition) - present and well-formed must validate."""
    payload = json.loads(_fake_claude_json())
    payload["typography_zones"] = [
        {"zone": "headline upper-right", "typeface_class": "serif", "weight": "bold",
         "case": "title", "letter_spacing": "normal", "colour": "white",
         "size_relative": "large", "decorative_elements": [], "line_count": 2},
    ]
    bp = deconstruct.deconstruct_from_response(json.dumps(payload))
    assert bp["typography_zones"][0]["zone"] == "headline upper-right"


def test_deconstruct_response_without_typography_zones_still_passes_schema():
    """Optional means optional - every blueprint deconstructed before this field existed
    must still validate with no typography_zones key at all."""
    raw = _fake_claude_json()
    assert "typography_zones" not in json.loads(raw)
    bp = deconstruct.deconstruct_from_response(raw)
    assert "typography_zones" not in bp


def test_build_prompt_instructs_omitting_absent_zones_never_describing_absence():
    """A real live case (CeraVe, 2026-08-06): the model returned a sub_line entry whose
    detail read 'No explicit sub-line; headline stands alone' - an entry describing its
    own absence still reads downstream as a zone that EXISTS, which the generator would
    then try to substitute into. The prompt must say explicitly: omit the zone entirely,
    never add a placeholder entry explaining that it's missing."""
    prompt = deconstruct.build_prompt("AD1", "PageX", "2026-01-01")
    assert "OMIT it entirely" in prompt
    assert "do not add an entry for it just to say it is absent" in prompt


def test_deconstruct_image_scraped_ad_copy_with_braces_does_not_raise(monkeypatch):
    """ad_text/cta are passed to Claude as a SEPARATE content block, never through
    .format() - confirmed end to end: literal { and } in scraped ad copy must not raise
    KeyError, the exact risk .format()-interpolating new fields would have introduced."""
    class FakeMessage:
        content = [type("obj", (), {"text": _fake_claude_json()})()]

    class FakeMessages:
        def create(self, **kwargs):
            return FakeMessage()

    class FakeClient:
        def __init__(self, *a, **k):
            self.messages = FakeMessages()

    monkeypatch.setattr(deconstruct.anthropic, "Anthropic", FakeClient)

    bp = deconstruct.deconstruct_image(
        image_bytes=b"\x89PNG\r\n\x1a\nfakepngbytes",
        ad_id="AD1", source_page="PageX", captured_at="2026-01-01",
        ad_text="Save 20% {today only} - don't miss it! {limited stock}",
        cta="Shop {Now}",
    )
    assert bp["ad_id"] == "AD123"


def test_deconstruct_image_missing_structural_zones_retries_once_then_raises(monkeypatch):
    """structural_zones is now required (schema/blueprint.schema.json). A response that
    omits it fails schema validation, not JSON parsing, so it should retry once with the
    validator's own message appended as a correction instruction, not the JSON-escaping
    nudge. If the retry still comes back without structural_zones, raise - and the vision
    call must have been made exactly twice, never a third time."""
    payload = json.loads(_fake_claude_json())
    del payload["structural_zones"]
    bad_json = json.dumps(payload)

    call_count = {"n": 0}

    class FakeMessage:
        def __init__(self, text):
            self.content = [type("obj", (), {"text": text})()]

    class FakeMessages:
        def create(self, **kwargs):
            call_count["n"] += 1
            return FakeMessage(bad_json)

    class FakeClient:
        def __init__(self, *a, **k):
            self.messages = FakeMessages()

    monkeypatch.setattr(deconstruct.anthropic, "Anthropic", FakeClient)

    with pytest.raises(ValueError):
        deconstruct.deconstruct_image(
            image_bytes=b"\x89PNG\r\n\x1a\nfakepngbytes",
            ad_id="AD1", source_page="PageX", captured_at="2026-01-01",
        )
    assert call_count["n"] == 2