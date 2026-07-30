"""Tests for the Claude prompt-writer pass (Part 5). _build_user_prompt is pure (no
network) and tested directly; write_creative_description's network call is monkeypatched
via generate_image_prompt_writer.anthropic.Anthropic."""
import json
from src import generate_image_prompt_writer as writer


def test_build_user_prompt_includes_angle_notes():
    """angles.notes is the operator's per-angle guidance channel for exactly this pass -
    it must be consumed, not left unread (that's the whole reason the column exists)."""
    angle = {"name": "Crepey Skin", "notes": "always shoot in warm, late-afternoon light"}
    prompt = writer._build_user_prompt({}, angle=angle)
    assert "always shoot in warm, late-afternoon light" in prompt


def test_build_user_prompt_includes_body_area_and_offer_text():
    """Confirms body_area and offer_text are actually consumed here - they thread
    correctly all the way from the run strip but nothing read them before this pass."""
    prompt = writer._build_user_prompt({}, angle={"name": "Bruising"},
                                        body_area="knees", offer_text="20% off launch week")
    assert "knees" in prompt
    assert "20% off launch week" in prompt


def test_build_user_prompt_never_reads_angle_body_area():
    """Body area varies every run and is NOT fixed per angle (team confirmed) - the writer
    must use only the per-run body_area argument, never angle.get("body_area"), even when
    the angle dict happens to carry a (suggestion-only) body_area of its own."""
    angle = {"name": "Crepey Skin", "body_area": "elbow and forearm"}
    prompt_no_run_value = writer._build_user_prompt({}, angle=angle, body_area=None)
    assert "elbow and forearm" not in prompt_no_run_value

    prompt_with_run_value = writer._build_user_prompt({}, angle=angle, body_area="knees")
    assert "knees" in prompt_with_run_value
    assert "elbow and forearm" not in prompt_with_run_value


def test_build_user_prompt_includes_product_visual_description():
    product = {"visual_description": "amber glass bottle, gold pump top"}
    prompt = writer._build_user_prompt({}, product=product)
    assert "amber glass bottle, gold pump top" in prompt


def test_build_user_prompt_reflects_reference_image_count():
    with_refs = writer._build_user_prompt({}, reference_image_count=3)
    without_refs = writer._build_user_prompt({}, reference_image_count=0)
    assert "3 reference photo" in with_refs
    assert "No reference photos" in without_refs


def test_build_user_prompt_includes_realism():
    prompt = writer._build_user_prompt({}, realism="ugc_native")
    assert "ugc_native" in prompt


# ---- Regression guards for the real incident: writer described "two Besque Magic amber
# glass bottles" (rule 7 permits one) and an invented headline (rule 6 forbade all text) ----

def test_build_user_prompt_never_reads_visual_subject():
    """visual.subject is where the vision step puts identity-carrying descriptions of the
    COMPETITOR's model AND product (e.g. product count) - handing it to the writer is
    exactly how "two bottles" leaked in. Must never appear in the prompt text, even though
    the full blueprint dict (which contains it) is passed into this function."""
    bp = {"visual": {"subject": "two amber glass bottles side by side, blonde model in dark bikini",
                      "layout": "clean centered composition"}}
    prompt = writer._build_user_prompt(bp)
    assert "two amber glass bottles" not in prompt
    assert "blonde model" not in prompt
    assert "bikini" not in prompt
    # layout (a different, legitimately-passed field) still gets through
    assert "clean centered composition" in prompt


def test_build_user_prompt_include_product_true_forces_exactly_one():
    prompt = writer._build_user_prompt({}, include_product=True)
    assert "EXACTLY ONE Besque" in prompt
    assert "never two" in prompt


def test_build_user_prompt_include_product_false_forbids_any_product():
    prompt = writer._build_user_prompt({}, include_product=False)
    assert "PRODUCTLESS" in prompt
    assert "EXACTLY ONE Besque" not in prompt


def test_build_user_prompt_text_in_image_false_reserves_negative_space_never_typography():
    prompt = writer._build_user_prompt({}, text_in_image=False)
    assert "RESERVED NEGATIVE SPACE" in prompt
    assert "NO typography" in prompt


def test_build_user_prompt_text_in_image_true_names_exact_supplied_headline():
    prompt = writer._build_user_prompt({}, text_in_image=True, headline="Firmer Skin By Friday",
                                        subtext="7 cold-pressed oils")
    assert 'the headline "Firmer Skin By Friday"' in prompt
    assert 'the supporting text "7 cold-pressed oils"' in prompt
    assert "RESERVED NEGATIVE SPACE" not in prompt


def test_build_user_prompt_text_in_image_true_without_headline_falls_back_to_reserved_space():
    """text_in_image=True but no headline available (e.g. copy generation produced none) -
    nothing is confirmed to render, so this must NOT invite Claude to invent one."""
    prompt = writer._build_user_prompt({}, text_in_image=True, headline=None)
    assert "RESERVED NEGATIVE SPACE" in prompt


def test_write_creative_description_forwards_mode_flags_to_user_prompt(monkeypatch):
    """write_creative_description must actually pass text_in_image/include_product/
    headline/subtext through to _build_user_prompt, not just accept them as unused params."""
    captured = {}
    real_build = writer._build_user_prompt

    def spy(*args, **kwargs):
        captured.update(kwargs)
        return real_build(*args, **kwargs)

    monkeypatch.setattr(writer, "_build_user_prompt", spy)
    payload = json.dumps({"creative_description": "x"})
    monkeypatch.setattr(writer.anthropic, "Anthropic", _fake_anthropic(response_text=payload))

    writer.write_creative_description({}, angle={"name": "Crepey Skin"}, text_in_image=True,
                                      include_product=False, headline="H", subtext="S")

    assert captured["text_in_image"] is True
    assert captured["include_product"] is False
    assert captured["headline"] == "H"
    assert captured["subtext"] == "S"


def _fake_anthropic(response_text=None, raises=None):
    class FakeMessages:
        def create(self, **kwargs):
            if raises:
                raise raises
            return type("obj", (), {"content": [type("obj", (), {"text": response_text})()]})()

    class FakeClient:
        def __init__(self, *a, **k):
            self.messages = FakeMessages()

    return FakeClient


def test_write_creative_description_returns_none_on_exception(monkeypatch):
    """The fallback path: if the writer raises for any reason, callers must get None back
    (never an exception) so build_image_prompt's template-assembly path still runs."""
    monkeypatch.setattr(writer.anthropic, "Anthropic", _fake_anthropic(raises=RuntimeError("boom")))
    result = writer.write_creative_description({}, angle={"name": "Crepey Skin"})
    assert result is None


def test_write_creative_description_returns_none_on_unparseable_output(monkeypatch):
    monkeypatch.setattr(writer.anthropic, "Anthropic", _fake_anthropic(response_text="not json at all"))
    result = writer.write_creative_description({}, angle={"name": "Crepey Skin"})
    assert result is None


def test_write_creative_description_returns_none_when_key_missing(monkeypatch):
    """Valid JSON, but not shaped as {"creative_description": "..."} - must not crash or
    return something falsy-but-truthy like an empty string; must return None."""
    payload = json.dumps({"something_else": "x"})
    monkeypatch.setattr(writer.anthropic, "Anthropic", _fake_anthropic(response_text=payload))
    result = writer.write_creative_description({}, angle={"name": "Crepey Skin"})
    assert result is None


def test_write_creative_description_parses_valid_json(monkeypatch):
    payload = json.dumps({"creative_description": "A serene bathroom scene, warm afternoon light."})
    monkeypatch.setattr(writer.anthropic, "Anthropic", _fake_anthropic(response_text=payload))
    result = writer.write_creative_description({}, angle={"name": "Crepey Skin"})
    assert result == "A serene bathroom scene, warm afternoon light."


def test_write_creative_description_strips_json_fence(monkeypatch):
    """generate_copy's parser once failed on unstripped ```json fences - that's why
    extract_json exists. Confirm this module actually uses it, not a hand-rolled parse."""
    fenced = "```json\n" + json.dumps({"creative_description": "Fenced scene."}) + "\n```"
    monkeypatch.setattr(writer.anthropic, "Anthropic", _fake_anthropic(response_text=fenced))
    result = writer.write_creative_description({}, angle={"name": "Crepey Skin"})
    assert result == "Fenced scene."
