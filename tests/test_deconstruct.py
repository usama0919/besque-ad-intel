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
        "background": {"surface": "plain backdrop", "colour": "white", "light": "soft even light"},
        "cta": "Shop Now",
        "destination_url": "https://example.com",
        "objects": [
            {"object_id": "obj_01", "kind": "person", "description": "woman applying oil",
             "bbox": [0.2, 0.1, 0.6, 0.8], "colours": [], "ownership": "person",
             "role": "hero", "carries_brand_mark": False,
             "persuasive_function": "demonstrates the product", "disposition": "substitute"},
        ],
        "production_style": {"style": "ugc", "confidence": "high", "signals": ["handheld framing"]},
        "body_area_shown": "none",
        "face_present": {"has_face": False, "prominence": "none", "location": ""},
        "semantic_split": {"is_split": False, "split_axis": None, "left_or_before": "", "right_or_after": ""},
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


# ---- Bottle shape language filter (2026-08-16) - wired into deconstruct_from_response
# itself, so every real deconstruct call gets it, not just a caller that remembers to ----

def test_deconstruct_from_response_strips_bottle_shape_language():
    payload = json.loads(_fake_claude_json())
    payload["visual"]["subject"] = "a tall cylindrical amber bottle"
    payload["product_category"] = {"category": "body_oil", "confidence": "high",
                                    "signals": ["cylindrical applicator wand", "clear glass"]}
    payload["layout_detail"] = {"product_count": 1,
                                 "zone_positions": ["tall pump collar bottle mid-frame", "CTA bottom"]}
    bp = deconstruct.deconstruct_from_response(json.dumps(payload))
    assert bp["visual"]["subject"] == ""
    assert bp["product_category"]["signals"] == ["clear glass"]
    assert bp["layout_detail"]["zone_positions"] == ["CTA bottom"]


def test_deconstruct_from_response_leaves_clean_blueprint_unchanged():
    raw = _fake_claude_json()
    bp = deconstruct.deconstruct_from_response(raw)
    assert bp["visual"]["subject"] == "woman"


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


# ---- typography_zones/scene_elements/structural_zones prompt sections DELETED
# 2026-08-17: blueprint.objects replaces all of them (see schema/blueprint.schema.json,
# deconstruct.py's BLUEPRINT_PROMPT, generate_image_prompt._objects_clause). The tests
# that used to assert those sections' exact wording (typography_zones format-safety,
# "OMIT it entirely" zone-absence wording, depicts_competitor_category classifier
# wording, scene_elements colour-neutrality wording) tested prompt text that no longer
# exists - removed rather than left permanently failing. See test_objects_schema.py for
# the new `objects` array's own coverage (resolve_disposition, the closure sentence,
# legacy-blueprint read safety).

def test_build_prompt_instructs_objects_never_folded_into_background():
    """The direct replacement for the old scene_elements colour-neutrality tests above:
    the new `objects` section's own explicit instruction that anything with an
    identity is an object row, never background prose."""
    prompt = deconstruct.build_prompt("AD1", "PageX", "2026-01-01")
    assert "objects" in prompt
    assert "never folded into this field's prose" in prompt
    assert "MULTIPLE INSTANCES OF THE SAME PRODUCT ARE SEPARATE ROWS" in prompt


def test_build_prompt_background_reduced_to_surface_colour_light():
    prompt = deconstruct.build_prompt("AD1", "PageX", "2026-01-01")
    assert '"surface"' in prompt
    assert '"colour"' in prompt
    assert '"light"' in prompt


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


def test_deconstruct_image_missing_objects_retries_once_then_raises(monkeypatch):
    """objects is required (schema/blueprint.schema.json, 2026-08-17). A response that
    omits it fails schema validation, not JSON parsing, so it should retry once with the
    validator's own message appended as a correction instruction, not the JSON-escaping
    nudge. If the retry still comes back without objects, raise - and the vision call
    must have been made exactly twice, never a third time."""
    payload = json.loads(_fake_claude_json())
    del payload["objects"]
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


# ---- 2026-08-13: transient API-error retry - a network timeout/connection error or a
# 429/5xx from Anthropic must retry with backoff, capped, and never be confused with
# the parse/validation retry above. Two ads were lost live to
# anthropic.APITimeoutError ("Request timed out or interrupted") propagating straight
# out of deconstruct_image with no retry at all. ----

import httpx
import anthropic as _anthropic_module


def _api_status_error(cls, status_code, message="error"):
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    resp = httpx.Response(status_code, request=req, json={"error": {"type": "x", "message": message}})
    return cls(message, response=resp, body={})


def _timeout_error():
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return _anthropic_module.APITimeoutError(request=req)


def test_is_transient_anthropic_error_true_for_timeout_and_connection():
    assert deconstruct._is_transient_anthropic_error(_timeout_error()) is True
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    assert deconstruct._is_transient_anthropic_error(
        _anthropic_module.APIConnectionError(request=req)) is True


def test_is_transient_anthropic_error_true_for_429_and_5xx():
    assert deconstruct._is_transient_anthropic_error(
        _api_status_error(_anthropic_module.RateLimitError, 429)) is True
    assert deconstruct._is_transient_anthropic_error(
        _api_status_error(_anthropic_module.InternalServerError, 500)) is True
    assert deconstruct._is_transient_anthropic_error(
        _api_status_error(_anthropic_module.OverloadedError, 529)) is True


def test_is_transient_anthropic_error_false_for_auth_and_bad_request():
    assert deconstruct._is_transient_anthropic_error(
        _api_status_error(_anthropic_module.AuthenticationError, 401)) is False
    assert deconstruct._is_transient_anthropic_error(
        _api_status_error(_anthropic_module.BadRequestError, 400)) is False
    assert deconstruct._is_transient_anthropic_error(
        _api_status_error(_anthropic_module.PermissionDeniedError, 403)) is False


def test_is_transient_anthropic_error_false_for_plain_exception():
    assert deconstruct._is_transient_anthropic_error(ValueError("not an API error")) is False


def _fake_message(text):
    return type("obj", (), {"content": [type("obj", (), {"text": text})()]})()


def test_call_claude_with_transient_retry_succeeds_after_two_timeouts(monkeypatch):
    monkeypatch.setattr(deconstruct.time, "sleep", lambda s: None)
    calls = {"n": 0}

    class FakeMessages:
        def create(self, **kwargs):
            calls["n"] += 1
            if calls["n"] < 3:
                raise _timeout_error()
            return _fake_message("ok")

    result = deconstruct._call_claude_with_transient_retry(
        type("obj", (), {"messages": FakeMessages()})(), {}, ad_id="AD1",
    )
    assert result.content[0].text == "ok"
    assert calls["n"] == 3


def test_call_claude_with_transient_retry_raises_after_exhausting_cap(monkeypatch):
    sleeps = []
    monkeypatch.setattr(deconstruct.time, "sleep", lambda s: sleeps.append(s))
    calls = {"n": 0}

    class FakeMessages:
        def create(self, **kwargs):
            calls["n"] += 1
            raise _timeout_error()

    with pytest.raises(_anthropic_module.APITimeoutError):
        deconstruct._call_claude_with_transient_retry(
            type("obj", (), {"messages": FakeMessages()})(), {}, ad_id="AD1",
        )
    assert calls["n"] == deconstruct._MAX_TRANSIENT_ATTEMPTS
    # exponential backoff: 2s, 4s, 8s (one fewer sleep than attempts - no sleep after
    # the final, exhausted attempt)
    assert sleeps == [2.0, 4.0, 8.0]


def test_call_claude_with_transient_retry_does_not_retry_non_transient(monkeypatch):
    monkeypatch.setattr(deconstruct.time, "sleep", lambda s: (_ for _ in ()).throw(AssertionError("must not sleep")))
    calls = {"n": 0}

    class FakeMessages:
        def create(self, **kwargs):
            calls["n"] += 1
            raise _api_status_error(_anthropic_module.AuthenticationError, 401)

    with pytest.raises(_anthropic_module.AuthenticationError):
        deconstruct._call_claude_with_transient_retry(
            type("obj", (), {"messages": FakeMessages()})(), {}, ad_id="AD1",
        )
    assert calls["n"] == 1  # failed fast, no retry


def test_call_claude_with_transient_retry_logs_attempt_and_error_class(monkeypatch, caplog):
    monkeypatch.setattr(deconstruct.time, "sleep", lambda s: None)
    calls = {"n": 0}

    class FakeMessages:
        def create(self, **kwargs):
            calls["n"] += 1
            if calls["n"] < 2:
                raise _timeout_error()
            return _fake_message("ok")

    with caplog.at_level("WARNING", logger="deconstruct"):
        deconstruct._call_claude_with_transient_retry(
            type("obj", (), {"messages": FakeMessages()})(), {}, ad_id="AD1",
        )
    messages = [r.getMessage() for r in caplog.records]
    assert any("AD1" in m and "1/4" in m and "APITimeoutError" in m for m in messages)


def test_deconstruct_image_retries_transient_timeout_then_succeeds(monkeypatch):
    """End to end: a timeout on the FIRST vision call must not fail the ad or consume
    a parse/validation attempt - it retries transiently and the ad completes normally."""
    monkeypatch.setattr(deconstruct.time, "sleep", lambda s: None)
    calls = {"n": 0}

    class FakeMessages:
        def create(self, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise _timeout_error()
            return _fake_message(_fake_claude_json())

    class FakeClient:
        def __init__(self, *a, **k):
            self.messages = FakeMessages()

    monkeypatch.setattr(deconstruct.anthropic, "Anthropic", FakeClient)

    bp = deconstruct.deconstruct_image(
        image_bytes=b"\x89PNG\r\n\x1a\nfakepngbytes",
        ad_id="AD1", source_page="PageX", captured_at="2026-01-01",
    )
    assert bp["ad_id"] == "AD123"
    assert calls["n"] == 2  # one transient retry, zero parse/validation retries


def test_deconstruct_image_transient_exhaustion_propagates_without_touching_validation_retry(monkeypatch):
    """A transient failure that exhausts its own budget must raise directly - never
    fall through to the parse/validation except blocks (which would pointlessly retry
    with a JSON-escaping nudge that has nothing to do with a timeout)."""
    monkeypatch.setattr(deconstruct.time, "sleep", lambda s: None)
    calls = {"n": 0}

    class FakeMessages:
        def create(self, **kwargs):
            calls["n"] += 1
            raise _timeout_error()

    class FakeClient:
        def __init__(self, *a, **k):
            self.messages = FakeMessages()

    monkeypatch.setattr(deconstruct.anthropic, "Anthropic", FakeClient)

    with pytest.raises(_anthropic_module.APITimeoutError):
        deconstruct.deconstruct_image(
            image_bytes=b"\x89PNG\r\n\x1a\nfakepngbytes",
            ad_id="AD1", source_page="PageX", captured_at="2026-01-01",
        )
    # exactly _MAX_TRANSIENT_ATTEMPTS raw calls - the parse/validation loop's own
    # attempt=2 (a DIFFERENT nudge) was never reached at all.
    assert calls["n"] == deconstruct._MAX_TRANSIENT_ATTEMPTS