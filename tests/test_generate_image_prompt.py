"""Tests for the image-prompt generator (no image API call)."""
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


def test_prompt_includes_visual_details():
    prompt = generate_image_prompt.build_image_prompt(_blueprint())
    assert "warm golden tones" in prompt
    assert "portrait, subject centered" in prompt


def test_prompt_mentions_besque_and_avoids_competitor():
    prompt = generate_image_prompt.build_image_prompt(_blueprint())
    assert "Besque" in prompt
    assert "no competitor branding" in prompt


def test_prompt_handles_missing_visual_gracefully():
    prompt = generate_image_prompt.build_image_prompt({})
    assert isinstance(prompt, str)
    assert len(prompt) > 20


def test_prompt_includes_compliance_rules():
    prompt = generate_image_prompt.build_image_prompt(_blueprint())
    assert "C1. NO REAL PEOPLE" in prompt
    assert "C6. NO SEXUALIZED FRAMING" in prompt
    # Existing rules 6/7 must still be present, unmodified, not replaced by the new rules.
    assert "TEXT POLICY (STRICT)" in prompt
    assert "PRODUCT POLICY (STRICT)" in prompt


def test_prompt_never_leaks_visual_subject():
    """Regression guard for the Rule C1 tension: visual.subject is where the vision step
    puts identity-carrying descriptions of the competitor's model (see deconstruct.py real
    data) - it must never reach the image-generation prompt verbatim."""
    bp = _blueprint()
    bp["visual"]["subject"] = "Blonde athletic woman 40+ in dark bikini, visibly muscular physique"
    prompt = generate_image_prompt.build_image_prompt(bp)
    assert "Blonde athletic woman" not in prompt
    assert "bikini" not in prompt


def test_prompt_has_defensive_clause_near_layout():
    """The layout field IS forwarded into the prompt, so the compliance override for
    whatever it might imply about a person must sit right next to it, not just be
    stated once somewhere earlier in a long prompt."""
    prompt = generate_image_prompt.build_image_prompt(_blueprint())
    layout_pos = prompt.index("portrait, subject centered")
    nearby = prompt[layout_pos:layout_pos + 300]
    assert "generic, non-identifiable model" in nearby