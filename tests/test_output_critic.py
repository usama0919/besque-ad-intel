"""Tests for the output critic (Prompt 4, Item 1) - a SAFETY control, not a quality
feature. Every guardrail on the image path up to this point is prompt-only; this module
is the only thing that inspects what Gemini actually produced. check_draft must never
raise (a failure must never lose a draft or fail a run), and must drop low-confidence
findings (a critic that flags everything becomes noise)."""
import json
from src import output_critic as critic


def test_build_user_prompt_includes_brand_rules_text():
    prompt = critic._build_user_prompt("STRICT RULES - NEVER VIOLATE: some rule text")
    assert "STRICT RULES - NEVER VIOLATE: some rule text" in prompt


def test_build_user_prompt_states_authorised_headline():
    prompt = critic._build_user_prompt("rules", headline="Firmer Skin By Friday", subtext="7 oils")
    assert "Firmer Skin By Friday" in prompt
    assert "7 oils" in prompt


def test_build_user_prompt_states_no_text_authorised_when_headline_absent():
    prompt = critic._build_user_prompt("rules", headline=None)
    assert "NONE - no text was authorised for this image" in prompt


def test_build_user_prompt_states_authorised_offer():
    prompt = critic._build_user_prompt("rules", offer_text="20% off launch week")
    assert "20% off launch week" in prompt


def test_build_user_prompt_states_no_offer_authorised_when_absent():
    prompt = critic._build_user_prompt("rules", offer_text=None)
    assert "NONE - no offer was authorised for this image" in prompt


def test_build_user_prompt_states_product_presence():
    prompt = critic._build_user_prompt("rules", include_product=True)
    assert "exactly one Besque product" in prompt
    prompt2 = critic._build_user_prompt("rules", include_product=False)
    assert "NONE - this was a deliberately productless image" in prompt2


# ---- The prompt-fixed instruction: proven-shipped categories default to high confidence ----

def test_critic_system_states_high_confidence_defaults_for_shipped_categories():
    for cat in ("unauthorised offer", "scarcity claim", "promo code", "efficacy claim", "testimonial"):
        assert cat in critic.CRITIC_SYSTEM


def test_critic_system_lists_every_required_check():
    checks = ["competitor logo", "competitor brand or product name", "unauthorised offer",
              "quantified efficacy claim", "testimonial", "more than one product",
              "product-derived substance", "empty graphic container", "garbled or illegible",
              "text rendered when none was authorised"]
    for c in checks:
        assert c in critic.CRITIC_SYSTEM


# ---- check_draft: never raises, filters confidence, parses JSON ----

class _FakeMessage:
    def __init__(self, text):
        self.content = [type("obj", (), {"text": text})()]


class _FakeMessages:
    def __init__(self, response_text):
        self._text = response_text

    def create(self, **kwargs):
        return _FakeMessage(self._text)


class _FakeClient:
    def __init__(self, response_text):
        self.messages = _FakeMessages(response_text)


def test_check_draft_returns_high_and_medium_only(monkeypatch):
    response = json.dumps({"violations": [
        {"category": "testimonial", "description": "fabricated quote", "confidence": "high"},
        {"category": "logo", "description": "faint mark, might be shadow", "confidence": "low"},
        {"category": "offer", "description": "possible price text", "confidence": "medium"},
    ]})
    monkeypatch.setattr(critic.anthropic, "Anthropic", lambda *a, **k: _FakeClient(response))

    findings = critic.check_draft(b"\x89PNG\r\n\x1a\nfake", "rules")
    assert len(findings) == 2
    assert {f["confidence"] for f in findings} == {"high", "medium"}
    assert not any(f["confidence"] == "low" for f in findings)


def test_check_draft_returns_empty_list_when_no_violations(monkeypatch):
    monkeypatch.setattr(critic.anthropic, "Anthropic",
                        lambda *a, **k: _FakeClient(json.dumps({"violations": []})))
    assert critic.check_draft(b"fake-bytes", "rules") == []


def test_check_draft_returns_none_on_api_error(monkeypatch):
    class _BoomClient:
        def __init__(self, *a, **k):
            raise RuntimeError("API unavailable")

    monkeypatch.setattr(critic.anthropic, "Anthropic", _BoomClient)
    assert critic.check_draft(b"fake-bytes", "rules") is None


def test_check_draft_returns_none_on_unparseable_response(monkeypatch):
    monkeypatch.setattr(critic.anthropic, "Anthropic",
                        lambda *a, **k: _FakeClient("not valid json at all"))
    assert critic.check_draft(b"fake-bytes", "rules") is None


def test_check_draft_never_raises_even_on_malformed_violation_entries(monkeypatch):
    """A violation entry missing "confidence" must not crash the filter - it's simply
    dropped (not high/medium), never a hard failure."""
    response = json.dumps({"violations": [{"category": "x", "description": "y"}]})
    monkeypatch.setattr(critic.anthropic, "Anthropic", lambda *a, **k: _FakeClient(response))
    findings = critic.check_draft(b"fake-bytes", "rules")
    assert findings == []
