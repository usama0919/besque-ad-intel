"""Tests for the corrective-retry loop's prompt-side half (2026-08-05): build_image_prompt's
mechanical _critic_feedback_clause, its fixed precedence position (same slot as
_operator_instruction_clause - below brand_rules()/compliance/rules 6/7/9, above whatever
supplies the scene text), and that it reaches generate_image in every mode. The retry
control-flow itself (pipeline.process_ad's MAX_IMAGE_ATTEMPTS loop) is tested in
tests/test_pipeline.py."""
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


# ---- _critic_feedback_clause ----

def test_critic_feedback_clause_empty_for_blank_input():
    for blank in (None, [], ""):
        assert generate_image_prompt._critic_feedback_clause(blank) == ""


def test_critic_feedback_clause_states_each_finding_and_boundary():
    clause = generate_image_prompt._critic_feedback_clause([
        "Unauthorised text: headline read the competitor's product name",
        "Wrong product category: a mist was rendered instead of the authorised body oil",
    ])
    assert "Unauthorised text: headline read the competitor's product name" in clause
    assert "Wrong product category: a mist was rendered instead of the authorised body oil" in clause
    assert "CORRECTIONS FROM THE PREVIOUS ATTEMPT" in clause
    assert "must be fixed this time, none may repeat" in clause


# ---- No empty section when absent - and it must not appear at all when omitted ----

def test_build_image_prompt_no_critic_feedback_section_when_absent():
    prompt = generate_image_prompt.build_image_prompt(_blueprint())
    assert "CORRECTIONS FROM THE PREVIOUS ATTEMPT" not in prompt


# ---- It must provably reach the assembled prompt STRING in every branch ----

def test_build_image_prompt_critic_feedback_reaches_default_template_prompt():
    prompt = generate_image_prompt.build_image_prompt(
        _blueprint(), critic_feedback=["Wrong product category: rendered a mist"]
    )
    assert "Wrong product category: rendered a mist" in prompt


def test_build_image_prompt_critic_feedback_reaches_creative_description_prompt():
    prompt = generate_image_prompt.build_image_prompt(
        _blueprint(), critic_feedback=["CTA button survived verbatim"],
        creative_description="A calm spa scene.",
    )
    assert "CTA button survived verbatim" in prompt


def test_build_image_prompt_critic_feedback_reaches_edit_mode_prompt():
    prompt = generate_image_prompt.build_image_prompt(
        _blueprint(), critic_feedback=["Competitor brand name rendered on the label"], edit_mode=True,
    )
    assert "Competitor brand name rendered on the label" in prompt


# ---- Precedence: below brand_rules()/compliance/rules 6/7/9, above the scene text - same
# slot as operator_instruction, right after it ----

def test_critic_feedback_position_below_compliance_above_template():
    prompt = generate_image_prompt.build_image_prompt(
        _blueprint(), critic_feedback=["headline mismatch"]
    )
    feedback_pos = prompt.index("headline mismatch")
    compliance_pos = prompt.index("C6.")
    template_pos = prompt.index("Composition and setting:")
    assert compliance_pos < feedback_pos < template_pos


def test_critic_feedback_position_below_rule9_above_edit_mode_instruction():
    prompt = generate_image_prompt.build_image_prompt(
        _blueprint(), critic_feedback=["headline mismatch"], edit_mode=True,
    )
    feedback_pos = prompt.index("headline mismatch")
    rule9_pos = prompt.index("SOURCE IMAGE IS THE COMPETITOR'S OWN AD")
    edit_instruction_pos = prompt.index("EDIT MODE: the FIRST attached image")
    assert rule9_pos < feedback_pos < edit_instruction_pos


def test_critic_feedback_sits_after_operator_instruction():
    prompt = generate_image_prompt.build_image_prompt(
        _blueprint(), operator_instruction="make it brighter", critic_feedback=["headline mismatch"],
    )
    instr_pos = prompt.index("make it brighter")
    feedback_pos = prompt.index("headline mismatch")
    assert instr_pos < feedback_pos


# ---- generate_image(): critic_feedback reaches the assembled prompt in every mode ----

class _FakeGenaiClient:
    def __init__(self, *a, **k):
        self.models = self

    def generate_content(self, model, contents, **kwargs):
        part = type("Part", (), {"inline_data": type("Data", (), {"data": b"fake-png-bytes"})()})()
        candidate = type("Candidate", (), {"content": type("Content", (), {"parts": [part]})()})()
        return type("Response", (), {"candidates": [candidate]})()


def test_generate_image_forwards_critic_feedback_to_prompt(monkeypatch, tmp_path):
    monkeypatch.setattr(generate_image_prompt, "genai", type("obj", (), {"Client": _FakeGenaiClient}))
    monkeypatch.setattr(generate_image_prompt, "ASSET_DIR", tmp_path)

    generate_image_prompt.generate_image(
        _blueprint(), "AD_CF", critic_feedback=["Wrong product category: rendered a mist"],
    )
    assert "Wrong product category: rendered a mist" in generate_image_prompt.generate_image.last_prompt


def test_generate_image_forwards_critic_feedback_to_prompt_in_edit_mode(monkeypatch, tmp_path):
    monkeypatch.setattr(generate_image_prompt, "genai", type("obj", (), {"Client": _FakeGenaiClient}))
    monkeypatch.setattr(generate_image_prompt, "ASSET_DIR", tmp_path)

    # A minimal valid 1x1 PNG - real bytes so derive_aspect_ratio succeeds without ever
    # touching the DB fallback-warning path (edit_mode's missing-reference branch does).
    fake_png = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
                b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0"
                b"\x00\x00\x03\x01\x01\x00\x18\xdd\x8d\xb0\x00\x00\x00\x00IEND\xaeB`\x82")
    generate_image_prompt.generate_image(
        _blueprint(), "AD_CF_EDIT", edit_mode=True, retheme_colours=False,
        competitor_image_bytes=fake_png,
        critic_feedback=["Competitor CTA button survived verbatim"],
    )
    assert "Competitor CTA button survived verbatim" in generate_image_prompt.generate_image.last_prompt


def test_generate_image_no_critic_feedback_reproduces_identical_prompt(monkeypatch, tmp_path):
    """Omitting critic_feedback (every pre-existing caller) must produce byte-for-byte the
    same prompt as before this parameter existed."""
    monkeypatch.setattr(generate_image_prompt, "genai", type("obj", (), {"Client": _FakeGenaiClient}))
    monkeypatch.setattr(generate_image_prompt, "ASSET_DIR", tmp_path)

    generate_image_prompt.generate_image(_blueprint(), "AD_CF_NONE")
    prompt_without = generate_image_prompt.generate_image.last_prompt
    generate_image_prompt.generate_image(_blueprint(), "AD_CF_NONE_2", critic_feedback=None)
    prompt_with_none = generate_image_prompt.generate_image.last_prompt
    assert prompt_without == prompt_with_none
    assert "CORRECTIONS FROM THE PREVIOUS ATTEMPT" not in prompt_without
