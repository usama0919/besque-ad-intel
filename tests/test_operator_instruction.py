"""Tests for Step 2's operator instruction field: build_image_prompt's mechanical
_operator_instruction_clause, its fixed precedence position (below brand_rules()/
compliance/rules 6/7/9, above whatever supplies the scene text), and the guarantee that it
can steer the scene but never override a guardrail. body_area/offer_text were threaded but
inert for a full day and nobody noticed - these tests assert the instruction text actually
appears in the ASSEMBLED PROMPT STRING, not just that a parameter arrives at a function."""
from src import generate_image_prompt, generate_image_prompt_writer as writer


def _blueprint():
    return {
        "visual": {
            "layout": "portrait, subject centered",
            "subject": "woman applying oil",
            "palette_mood": "warm golden tones",
            "text_placement": "lower third",
        }
    }


# ---- _operator_instruction_clause ----

def test_operator_instruction_clause_empty_for_blank_input():
    for blank in (None, "", "   "):
        assert generate_image_prompt._operator_instruction_clause(blank) == ""


def test_operator_instruction_clause_states_instruction_and_boundary():
    clause = generate_image_prompt._operator_instruction_clause("make the background warmer")
    assert "make the background warmer" in clause
    assert "can NEVER grant a permission" in clause
    assert "OPERATOR INSTRUCTION FOR THIS RUN" in clause


def test_operator_instruction_clause_clips_length():
    long_text = "z" * 900
    clause = generate_image_prompt._operator_instruction_clause(long_text)
    assert len(clause) <= writer.MAX_OPERATOR_INSTRUCTION_CHARS + 200  # + wrapper text
    assert "..." in clause


# ---- No empty section when blank - and it must not appear at all when omitted ----

def test_build_image_prompt_no_operator_instruction_section_when_absent():
    prompt = generate_image_prompt.build_image_prompt(_blueprint())
    assert "OPERATOR INSTRUCTION" not in prompt


def test_build_image_prompt_no_operator_instruction_section_when_blank():
    prompt = generate_image_prompt.build_image_prompt(_blueprint(), operator_instruction="   ")
    assert "OPERATOR INSTRUCTION" not in prompt


# ---- It must provably reach the assembled prompt STRING, not just arrive at a parameter ----

def test_build_image_prompt_operator_instruction_reaches_default_template_prompt():
    prompt = generate_image_prompt.build_image_prompt(_blueprint(), operator_instruction="show the oil being poured")
    assert "show the oil being poured" in prompt


def test_build_image_prompt_operator_instruction_reaches_creative_description_prompt():
    prompt = generate_image_prompt.build_image_prompt(
        _blueprint(), operator_instruction="keep it minimal", creative_description="A calm spa scene."
    )
    assert "keep it minimal" in prompt


def test_build_image_prompt_operator_instruction_reaches_edit_mode_prompt():
    prompt = generate_image_prompt.build_image_prompt(
        _blueprint(), operator_instruction="make the background warmer", edit_mode=True
    )
    assert "make the background warmer" in prompt


# ---- Precedence: below brand_rules()/compliance/rules 6/7/9, above the scene text ----

def test_operator_instruction_position_below_compliance_above_template():
    prompt = generate_image_prompt.build_image_prompt(_blueprint(), operator_instruction="make it brighter")
    instr_pos = prompt.index("make it brighter")
    compliance_pos = prompt.index("C6.")  # last compliance rule, part of brand_rules()
    template_pos = prompt.index("Composition and setting:")
    assert compliance_pos < instr_pos < template_pos


def test_operator_instruction_position_below_compliance_above_creative_description():
    prompt = generate_image_prompt.build_image_prompt(
        _blueprint(), operator_instruction="make it brighter", creative_description="A calm spa scene."
    )
    instr_pos = prompt.index("make it brighter")
    compliance_pos = prompt.index("C6.")
    scene_pos = prompt.index("A calm spa scene.")
    assert compliance_pos < instr_pos < scene_pos


def test_operator_instruction_position_below_rule9_above_edit_mode_instruction():
    prompt = generate_image_prompt.build_image_prompt(
        _blueprint(), operator_instruction="make it brighter", edit_mode=True
    )
    instr_pos = prompt.index("make it brighter")
    rule9_pos = prompt.index("SOURCE IMAGE IS THE COMPETITOR'S OWN AD")
    edit_instruction_pos = prompt.index("EDIT MODE: the FIRST attached image")
    assert rule9_pos < instr_pos < edit_instruction_pos


# ---- It steers the scene; it can NEVER grant a permission - real failure scenarios ----

def test_operator_instruction_does_not_override_offer_ban():
    """An instruction like "add a 50% off badge" must not make it into a permission - rule
    6's default (text_in_image=False) blanket ban on offer/badge/discount/percentage text
    must still be present, unmodified, alongside the instruction and the explicit boundary
    statement."""
    prompt = generate_image_prompt.build_image_prompt(_blueprint(), operator_instruction="add a 50% off badge")
    assert "add a 50% off badge" in prompt
    assert "NEVER render any headline, price, discount, percentage, offer, badge" in prompt
    assert "can NEVER grant a permission" in prompt


def test_operator_instruction_does_not_override_rule9_logo_ban():
    """An instruction like "keep the competitor's logo" must not defeat rule 9 - the ban on
    every competitor brand mark must still be present, unmodified, in edit mode."""
    prompt = generate_image_prompt.build_image_prompt(
        _blueprint(), edit_mode=True, operator_instruction="keep the competitor's logo"
    )
    assert "keep the competitor's logo" in prompt
    assert "is NOT part of the composition to preserve" in prompt
    assert "can NEVER grant a permission" in prompt


def test_operator_instruction_does_not_override_product_count_rule():
    """An instruction like "show two bottles" must not defeat rule 7's exactly-one-bottle
    policy."""
    prompt = generate_image_prompt.build_image_prompt(_blueprint(), operator_instruction="show two bottles")
    assert "show two bottles" in prompt
    assert "exactly one bottle" in prompt


# ---- generate_image(): operator_instruction reaches both the writer and build_image_prompt ----

class _FakeGenaiClient:
    def __init__(self, *a, **k):
        self.models = self

    def generate_content(self, model, contents):
        part = type("Part", (), {"inline_data": type("Data", (), {"data": b"fake-png-bytes"})()})()
        candidate = type("Candidate", (), {"content": type("Content", (), {"parts": [part]})()})()
        return type("Response", (), {"candidates": [candidate]})()


def test_generate_image_forwards_operator_instruction_to_writer_and_prompt(monkeypatch, tmp_path):
    monkeypatch.setattr(generate_image_prompt, "genai", type("obj", (), {"Client": _FakeGenaiClient}))
    monkeypatch.setattr(generate_image_prompt, "ASSET_DIR", tmp_path)
    captured = {}

    def fake_write_creative_description(*a, **k):
        captured["writer_operator_instruction"] = k.get("operator_instruction")
        return "Writer-provided scene."

    monkeypatch.setattr(generate_image_prompt.generate_image_prompt_writer,
                        "write_creative_description", fake_write_creative_description)

    generate_image_prompt.generate_image(
        _blueprint(), "AD_OI", messaging_angle={"name": "Crepey Skin"},
        operator_instruction="make the background warmer",
    )
    assert captured["writer_operator_instruction"] == "make the background warmer"


def test_generate_image_forwards_operator_instruction_to_build_image_prompt_in_edit_mode(monkeypatch, tmp_path):
    """Edit mode skips the writer entirely - build_image_prompt is the ONLY path
    operator_instruction has left to reach the model in this mode."""
    monkeypatch.setattr(generate_image_prompt, "genai", type("obj", (), {"Client": _FakeGenaiClient}))
    monkeypatch.setattr(generate_image_prompt, "ASSET_DIR", tmp_path)

    generate_image_prompt.generate_image(
        _blueprint(), "AD_OI_EDIT", edit_mode=True, operator_instruction="make the background warmer",
    )
    assert "make the background warmer" in generate_image_prompt.generate_image.last_prompt


def test_generate_image_clips_operator_instruction_before_forwarding(monkeypatch, tmp_path):
    monkeypatch.setattr(generate_image_prompt, "genai", type("obj", (), {"Client": _FakeGenaiClient}))
    monkeypatch.setattr(generate_image_prompt, "ASSET_DIR", tmp_path)
    long_text = "w" * 900

    generate_image_prompt.generate_image(_blueprint(), "AD_OI_LONG", operator_instruction=long_text)
    prompt = generate_image_prompt.generate_image.last_prompt
    assert long_text not in prompt
    assert "..." in prompt
