"""Deconstruct truncation/timeout fix (2026-08-20). Four of five ads failed
deconstruct in one live session, deterministically (the same two ad_ids failed
identically at 10:32/10:34 and again at 11:27/11:29 - not transient): every
truncation showed stop_reason='max_tokens' at output_tokens=4096, dying mid-JSON
around obj_20/obj_21 (char ~12,300-12,900); the APITimeoutError failures were on ads
of the same density, likely the same response-length problem hitting the 60s client
deadline.

Fixed: max_tokens raised 4096 -> 16384 (src.deconstruct._DECONSTRUCT_MAX_TOKENS);
stop_reason=='max_tokens' detected explicitly and given its OWN retry budget
(_fetch_deconstruct_message/_MAX_TRUNCATION_ATTEMPTS), escalating to
_DECONSTRUCT_MAX_TOKENS_ESCALATED, NEVER routed through JSON_ESCAPE_SYSTEM (a
truncated response is incomplete, not malformed - that nudge cannot fix it); client
timeout raised 60.0 -> 180.0 (_DECONSTRUCT_TIMEOUT_SECONDS); every exhausted budget
(truncation, schema validation, JSON parse) now calls dedupe.record_warning before
raising, naming the ad and the failure mode - previously an ad vanished from a batch
with only an ERROR-level log line, invisible to the dashboard's warnings feed."""
import json

import httpx
import pytest

from src import deconstruct


def _timeout_error():
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return deconstruct.anthropic.APITimeoutError(request=req)


def _fake_claude_json():
    return json.dumps({
        "ad_id": "AD123", "source_page": "CompetitorPage", "captured_at": "2026-01-01T00:00:00Z",
        "format": "testimonial_card",
        "hook": {"type": "social_proof", "headline_structure": "quote + result"},
        "angle": "confidence at any age", "awareness_stage": "solution",
        "claims": ["efficacy", "social_proof"],
        "visual": {"layout": "portrait", "subject": "woman", "palette_mood": "warm", "text_placement": "lower third"},
        "background": {"surface": "plain backdrop", "colour": "white", "light": "soft even light"},
        "cta": "Shop Now", "destination_url": "https://example.com",
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


def _mock_warnings(monkeypatch):
    from src import dedupe
    captured = []
    monkeypatch.setattr(dedupe, "init_pipeline_warnings", lambda: None)
    monkeypatch.setattr(dedupe, "record_warning",
                         lambda kind, detail: captured.append((kind, detail)))
    return captured


class _FakeMessage:
    def __init__(self, text, stop_reason="end_turn", output_tokens=100, input_tokens=500):
        self.content = [type("obj", (), {"text": text})()]
        self.stop_reason = stop_reason
        self.usage = type("obj", (), {"output_tokens": output_tokens, "input_tokens": input_tokens})()


def _fake_client(messages, captured_kwargs=None):
    """messages: list of _FakeMessage, returned in order across successive .create()
    calls (repeats the last one if more calls happen than messages supplied).
    captured_kwargs, if given, has every call's kwargs appended to it."""
    state = {"n": 0}

    class FakeMessages:
        def create(self, **kwargs):
            if captured_kwargs is not None:
                captured_kwargs.append(kwargs)
            idx = min(state["n"], len(messages) - 1)
            state["n"] += 1
            return messages[idx]

    class FakeClient:
        def __init__(self, *a, **k):
            if captured_kwargs is not None:
                captured_kwargs.append({"__client_init__": k})
            self.messages = FakeMessages()

    return FakeClient, state


# ---- max_tokens raised to 16384, timeout raised to 180.0 ----

def test_deconstruct_image_first_call_uses_16384_max_tokens(monkeypatch):
    calls = []
    FakeClient, _ = _fake_client([_FakeMessage(_fake_claude_json())], captured_kwargs=calls)
    monkeypatch.setattr(deconstruct.anthropic, "Anthropic", FakeClient)
    deconstruct.deconstruct_image(
        image_bytes=b"\x89PNG\r\n\x1a\nfakepngbytes",
        ad_id="AD1", source_page="PageX", captured_at="2026-01-01",
    )
    create_calls = [c for c in calls if "__client_init__" not in c]
    assert create_calls[0]["max_tokens"] == deconstruct._DECONSTRUCT_MAX_TOKENS == 16384


def test_deconstruct_image_client_constructed_with_raised_timeout(monkeypatch):
    calls = []
    FakeClient, _ = _fake_client([_FakeMessage(_fake_claude_json())], captured_kwargs=calls)
    monkeypatch.setattr(deconstruct.anthropic, "Anthropic", FakeClient)
    deconstruct.deconstruct_image(
        image_bytes=b"\x89PNG\r\n\x1a\nfakepngbytes",
        ad_id="AD1", source_page="PageX", captured_at="2026-01-01",
    )
    init_kwargs = [c["__client_init__"] for c in calls if "__client_init__" in c]
    assert init_kwargs[0]["timeout"] == deconstruct._DECONSTRUCT_TIMEOUT_SECONDS == 180.0


# ---- stop_reason=='max_tokens' is a distinct condition, never routed to JSON_ESCAPE_SYSTEM ----

def test_truncation_retries_with_escalated_ceiling_never_touches_system_prompt(monkeypatch):
    calls = []
    truncated = _FakeMessage("{\"objects\": [", stop_reason="max_tokens", output_tokens=4096)
    complete = _FakeMessage(_fake_claude_json(), stop_reason="end_turn")
    FakeClient, _ = _fake_client([truncated, complete], captured_kwargs=calls)
    monkeypatch.setattr(deconstruct.anthropic, "Anthropic", FakeClient)

    bp = deconstruct.deconstruct_image(
        image_bytes=b"\x89PNG\r\n\x1a\nfakepngbytes",
        ad_id="AD1", source_page="PageX", captured_at="2026-01-01",
    )
    assert bp["ad_id"] == "AD123"

    create_calls = [c for c in calls if "__client_init__" not in c]
    assert len(create_calls) == 2
    assert create_calls[0]["max_tokens"] == deconstruct._DECONSTRUCT_MAX_TOKENS
    assert create_calls[1]["max_tokens"] == deconstruct._DECONSTRUCT_MAX_TOKENS_ESCALATED
    # A truncation retry is not a JSON-escaping/validation retry - "system" must never
    # be set to JSON_ESCAPE_SYSTEM (or set at all) as a result of a truncation.
    assert "system" not in create_calls[1]


def test_truncation_exhaustion_raises_distinct_error_and_records_warning(monkeypatch):
    warnings = _mock_warnings(monkeypatch)
    calls = []
    always_truncated = _FakeMessage("{\"objects\": [", stop_reason="max_tokens", output_tokens=4096)
    FakeClient, _ = _fake_client([always_truncated], captured_kwargs=calls)
    monkeypatch.setattr(deconstruct.anthropic, "Anthropic", FakeClient)

    with pytest.raises(deconstruct.DeconstructTruncatedError):
        deconstruct.deconstruct_image(
            image_bytes=b"\x89PNG\r\n\x1a\nfakepngbytes",
            ad_id="AD_TRUNC", source_page="PageX", captured_at="2026-01-01",
        )
    create_calls = [c for c in calls if "__client_init__" not in c]
    # Exactly _MAX_TRUNCATION_ATTEMPTS calls - never falls through into the outer
    # parse/validation loop's own second attempt (that would be a 3rd+ call here).
    assert len(create_calls) == deconstruct._MAX_TRUNCATION_ATTEMPTS == 2
    assert len(warnings) == 1
    kind, detail = warnings[0]
    assert kind == "deconstruct_truncated"
    assert "AD_TRUNC" in detail
    assert "max_tokens" in detail


def test_truncation_never_confused_with_a_genuine_json_parse_failure(monkeypatch):
    """A non-truncated but genuinely malformed response (stop_reason='end_turn', bad
    JSON) must still go through the ordinary JSON-escaping retry, unaffected by the
    truncation-detection branch existing at all."""
    calls = []
    malformed = _FakeMessage("not json at all {", stop_reason="end_turn")
    FakeClient, _ = _fake_client([malformed], captured_kwargs=calls)
    monkeypatch.setattr(deconstruct.anthropic, "Anthropic", FakeClient)

    with pytest.raises(ValueError):
        deconstruct.deconstruct_image(
            image_bytes=b"\x89PNG\r\n\x1a\nfakepngbytes",
            ad_id="AD2", source_page="PageX", captured_at="2026-01-01",
        )
    create_calls = [c for c in calls if "__client_init__" not in c]
    assert len(create_calls) == deconstruct._MAX_DECONSTRUCT_ATTEMPTS == 2
    # The SECOND call (the parse-failure retry) must carry the JSON-escaping nudge -
    # proving the malformed-JSON path is untouched by the truncation fix.
    assert create_calls[1]["system"] == deconstruct.JSON_ESCAPE_SYSTEM


# ---- On exhausted retries (any of the three budgets), a pipeline_warnings row is recorded ----

def test_schema_validation_exhaustion_records_warning(monkeypatch):
    warnings = _mock_warnings(monkeypatch)
    payload = json.loads(_fake_claude_json())
    del payload["objects"]
    bad_json = json.dumps(payload)
    calls = []
    FakeClient, _ = _fake_client([_FakeMessage(bad_json)], captured_kwargs=calls)
    monkeypatch.setattr(deconstruct.anthropic, "Anthropic", FakeClient)

    with pytest.raises(ValueError):
        deconstruct.deconstruct_image(
            image_bytes=b"\x89PNG\r\n\x1a\nfakepngbytes",
            ad_id="AD_SCHEMA", source_page="PageX", captured_at="2026-01-01",
        )
    assert len(warnings) == 1
    kind, detail = warnings[0]
    assert kind == "deconstruct_failed"
    assert "AD_SCHEMA" in detail
    assert "schema validation" in detail


def test_json_parse_exhaustion_records_warning(monkeypatch):
    warnings = _mock_warnings(monkeypatch)
    calls = []
    malformed = _FakeMessage("not json at all {", stop_reason="end_turn")
    FakeClient, _ = _fake_client([malformed], captured_kwargs=calls)
    monkeypatch.setattr(deconstruct.anthropic, "Anthropic", FakeClient)

    with pytest.raises(ValueError):
        deconstruct.deconstruct_image(
            image_bytes=b"\x89PNG\r\n\x1a\nfakepngbytes",
            ad_id="AD_PARSE", source_page="PageX", captured_at="2026-01-01",
        )
    assert len(warnings) == 1
    kind, detail = warnings[0]
    assert kind == "deconstruct_failed"
    assert "AD_PARSE" in detail
    assert "JSON" in detail


def test_successful_deconstruct_records_no_warning(monkeypatch):
    warnings = _mock_warnings(monkeypatch)
    FakeClient, _ = _fake_client([_FakeMessage(_fake_claude_json())])
    monkeypatch.setattr(deconstruct.anthropic, "Anthropic", FakeClient)
    deconstruct.deconstruct_image(
        image_bytes=b"\x89PNG\r\n\x1a\nfakepngbytes",
        ad_id="AD_OK", source_page="PageX", captured_at="2026-01-01",
    )
    assert warnings == []


# ---- _fetch_deconstruct_message in isolation ----

def test_fetch_deconstruct_message_returns_immediately_when_not_truncated(monkeypatch):
    calls = []
    FakeClient, _ = _fake_client([_FakeMessage("whatever", stop_reason="end_turn")],
                                  captured_kwargs=calls)
    client = FakeClient()
    message = deconstruct._fetch_deconstruct_message(
        client, {"model": "x", "messages": []}, "AD1",
    )
    assert message.stop_reason == "end_turn"
    create_calls = [c for c in calls if "__client_init__" not in c]
    assert len(create_calls) == 1
    assert create_calls[0]["max_tokens"] == deconstruct._DECONSTRUCT_MAX_TOKENS


def test_fetch_deconstruct_message_escalates_once_then_returns_success(monkeypatch):
    calls = []
    truncated = _FakeMessage("cut off", stop_reason="max_tokens", output_tokens=16384)
    ok = _FakeMessage("fine", stop_reason="end_turn")
    FakeClient, _ = _fake_client([truncated, ok], captured_kwargs=calls)
    client = FakeClient()
    message = deconstruct._fetch_deconstruct_message(
        client, {"model": "x", "messages": []}, "AD1",
    )
    assert message.stop_reason == "end_turn"
    create_calls = [c for c in calls if "__client_init__" not in c]
    assert [c["max_tokens"] for c in create_calls] == [
        deconstruct._DECONSTRUCT_MAX_TOKENS, deconstruct._DECONSTRUCT_MAX_TOKENS_ESCALATED,
    ]


def test_fetch_deconstruct_message_raises_after_exhausting_truncation_budget(monkeypatch):
    warnings = _mock_warnings(monkeypatch)
    always_truncated = _FakeMessage("cut off", stop_reason="max_tokens", output_tokens=32768)
    FakeClient, _ = _fake_client([always_truncated])
    client = FakeClient()
    with pytest.raises(deconstruct.DeconstructTruncatedError):
        deconstruct._fetch_deconstruct_message(client, {"model": "x", "messages": []}, "AD1")
    assert len(warnings) == 1
    assert warnings[0][0] == "deconstruct_truncated"


def test_fetch_deconstruct_message_a_transient_network_failure_still_retries_independently(monkeypatch):
    """A network failure on the SAME call must still go through
    _call_claude_with_transient_retry, unaffected by the truncation wrapper existing
    around it - the two budgets stay independent."""
    monkeypatch.setattr(deconstruct.time, "sleep", lambda s: None)
    attempts = {"n": 0}

    class FlakyMessages:
        def create(self, **kwargs):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise _timeout_error()
            return _FakeMessage("fine", stop_reason="end_turn")

    class FlakyClient:
        def __init__(self):
            self.messages = FlakyMessages()

    message = deconstruct._fetch_deconstruct_message(
        FlakyClient(), {"model": "x", "messages": []}, "AD1",
    )
    assert message.stop_reason == "end_turn"
    assert attempts["n"] == 2
