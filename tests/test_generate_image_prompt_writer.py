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


# ---- Part B: creative_objective/target_audience/typography/expanded layout_detail ----

def test_build_user_prompt_includes_creative_objective_and_target_audience():
    bp = {"creative_objective": "drive urgency around a limited-time offer",
          "target_audience": "women 40+ concerned about skin texture"}
    prompt = writer._build_user_prompt(bp)
    assert "drive urgency around a limited-time offer" in prompt
    assert "women 40+ concerned about skin texture" in prompt


def test_build_user_prompt_includes_typography_styling_not_literal_text():
    bp = {"typography": {
        "headline_face": "serif", "headline_weight": "bold",
        "hierarchy_levels": ["large bold headline", "small CTA label"],
        "case_treatment": "all caps headline",
    }}
    prompt = writer._build_user_prompt(bp)
    assert "face: serif" in prompt
    assert "weight: bold" in prompt
    assert "large bold headline" in prompt
    assert "case: all caps headline" in prompt
    # framed as styling inspiration, not literal wording to quote
    assert "never quote literal text from here" in prompt


def test_build_user_prompt_includes_expanded_layout_detail():
    bp = {"layout_detail": {
        "zone_positions": ["headline top-center", "product mid-frame"],
        "has_bottom_banner": True, "has_corner_badge": True,
        "frame_division": "three stacked horizontal bands",
    }}
    prompt = writer._build_user_prompt(bp)
    assert "headline top-center" in prompt
    assert "has a full-width bottom banner" in prompt
    assert "has a corner badge" in prompt
    assert "three stacked horizontal bands" in prompt


def test_build_user_prompt_handles_missing_new_fields_gracefully():
    """138 existing artifacts have none of these fields - must not crash."""
    prompt = writer._build_user_prompt({})
    assert isinstance(prompt, str)
    assert len(prompt) > 20


# ---- Medium must match realism: a photographic (high_spec_studio) reference produced
# fully illustrated output (drawn eyes, painted skin, rendered bottle) in a real run ----

def test_realism_auto_resolves_to_blueprint_production_style():
    """realism="(auto)" reaches here as realism=None/"" - it must resolve to the
    reference ad's OWN detected production_style, never to no signal at all."""
    bp = {"production_style": {"style": "high_spec_studio"}}
    prompt = writer._build_user_prompt(bp, realism=None)
    assert "Realism / medium (STRICT" in prompt
    assert "high_spec_studio" in prompt


def test_realism_explicit_overrides_blueprint_production_style():
    """An operator-chosen realism must win over the reference ad's own detected style -
    never auto-detected when explicitly given (matches every other angle-driven control
    in this pipeline)."""
    bp = {"production_style": {"style": "high_spec_studio"}}
    prompt = writer._build_user_prompt(bp, realism="illustrated")
    assert "The medium must match illustrated exactly" in prompt


def test_realism_states_photographic_vs_drawn_taxonomy_explicitly():
    prompt = writer._build_user_prompt({}, realism="high_spec_studio")
    assert "high_spec_studio, ugc_native, and hybrid all mean a PHOTOGRAPH" in prompt
    assert "illustrated means NOT a photograph at all" in prompt


def test_realism_line_absent_when_neither_realism_nor_blueprint_style_given():
    prompt = writer._build_user_prompt({})
    assert "Realism / medium" not in prompt


# ---- Competitor offer must never leak into the image ----

def test_offer_text_given_states_exact_wording_only():
    prompt = writer._build_user_prompt({}, offer_text="20% off this week only")
    assert 'describe exactly this offer, wording, badge, or price - nothing more, nothing invented: 20% off this week only.' in prompt
    assert "describe NO offer" not in prompt


def test_offer_text_absent_forbids_any_offer_even_with_competitor_creative_objective():
    """Regression guard: with offer_text empty, a draft rendered a "20% OFF" badge lifted
    from the competitor's own offer/creative_objective. Must forbid ANY offer/badge/price
    regardless of what creative_objective (passed as "inspiration") describes."""
    bp = {"creative_objective": "drive urgency around a 20% off discount this weekend"}
    prompt = writer._build_user_prompt(bp, offer_text=None)
    assert "describe NO offer, badge, price, discount, or percentage of any kind" in prompt


# ---- Never name a product category Besque doesn't sell ----

def test_product_category_ban_always_present():
    prompt = writer._build_user_prompt({})
    assert "Besque sells a body OIL, never any other category" in prompt


def test_product_category_ban_overrides_competitor_typography_naming_wrong_category():
    """Regression guard: a draft headline read "Bye-Bye, Body Lotion" - Besque never
    sells lotion. The ban must be present even when typography.hierarchy_levels (quoted
    as "styling inspiration" from the competitor's own ad) literally names a different
    category."""
    bp = {"typography": {"hierarchy_levels": ["large bold 'Bye-Bye, Body Lotion' headline", "small CTA"]}}
    prompt = writer._build_user_prompt(bp)
    assert "Besque sells a body OIL, never any other category" in prompt
    assert "'lotion'" in prompt


# ---- Text DENSITY must match the reference, not just exact wording (2026-07-31): subtext
# carried the full ~80-word primary_text body copy against a reference that carried a
# single short headline and a name - Gemini rendered the whole paragraph into the scene. ----

def test_text_density_statement_includes_legibility_notes():
    bp = {"legibility_notes": "only the headline and a small logo are legible at feed size"}
    prompt = writer._build_user_prompt(bp)
    assert "Text DENSITY to match" in prompt
    assert "only the headline and a small logo are legible at feed size" in prompt


def test_text_density_statement_includes_layout_detail_text_zone():
    bp = {"layout_detail": {"text_zone": "single line, bottom third"}}
    prompt = writer._build_user_prompt(bp)
    assert "Text DENSITY to match" in prompt
    assert "single line, bottom third" in prompt


def test_text_density_statement_counts_typography_hierarchy_levels():
    bp = {"typography": {"hierarchy_levels": ["large bold headline", "small CTA button label"]}}
    prompt = writer._build_user_prompt(bp)
    assert "2 distinct text tier(s) in the reference" in prompt


def test_text_density_statement_warns_against_adding_a_paragraph():
    bp = {"legibility_notes": "one short headline only"}
    prompt = writer._build_user_prompt(bp)
    assert "must not add a paragraph of copy" in prompt


def test_text_density_statement_absent_when_no_density_signals_given():
    prompt = writer._build_user_prompt({})
    assert "Text DENSITY to match" not in prompt


# ---- Step 2 (2026-08-02): operator instruction field ----

def test_clip_operator_instruction_passes_short_text_through():
    assert writer.clip_operator_instruction("make the background warmer") == "make the background warmer"


def test_clip_operator_instruction_strips_whitespace():
    assert writer.clip_operator_instruction("  keep it minimal  ") == "keep it minimal"


def test_clip_operator_instruction_none_and_blank_are_empty():
    assert writer.clip_operator_instruction(None) == ""
    assert writer.clip_operator_instruction("   ") == ""


def test_clip_operator_instruction_caps_length():
    long_text = "x" * 900
    clipped = writer.clip_operator_instruction(long_text)
    assert len(clipped) <= writer.MAX_OPERATOR_INSTRUCTION_CHARS + 3  # +3 for "..."
    assert clipped.endswith("...")


def test_clip_operator_instruction_is_idempotent():
    long_text = "y" * 900
    once = writer.clip_operator_instruction(long_text)
    twice = writer.clip_operator_instruction(once)
    assert once == twice


def test_build_user_prompt_includes_operator_instruction():
    prompt = writer._build_user_prompt({}, operator_instruction="make the background warmer")
    assert "Operator instruction for this run" in prompt
    assert "make the background warmer" in prompt


def test_build_user_prompt_operator_instruction_states_strict_boundary():
    prompt = writer._build_user_prompt({}, operator_instruction="keep it minimal")
    assert "cannot override anything in the STRICT block below" in prompt


def test_build_user_prompt_omits_operator_instruction_section_when_blank():
    for blank in (None, "", "   "):
        prompt = writer._build_user_prompt({}, operator_instruction=blank)
        assert "Operator instruction for this run" not in prompt


def test_build_user_prompt_operator_instruction_appears_before_strict_block():
    """Precedence: steers the scene, sits ABOVE the STRICT overrides that follow it in the
    writer's own prompt (product category ban, offer, text-in-image, etc.) - never below,
    or it would read as itself being STRICT."""
    prompt = writer._build_user_prompt({}, operator_instruction="make the background warmer",
                                        offer_text=None)
    instr_pos = prompt.index("Operator instruction for this run")
    strict_pos = prompt.index("Product category (STRICT")
    assert instr_pos < strict_pos


def test_write_creative_description_forwards_operator_instruction(monkeypatch):
    captured = {}

    def fake_build_user_prompt(*a, **k):
        captured.update(k)
        return "prompt text"

    monkeypatch.setattr(writer, "_build_user_prompt", fake_build_user_prompt)

    class FakeMessage:
        content = [type("obj", (), {"text": json.dumps({"creative_description": "A scene."})})()]

    class FakeMessages:
        def create(self, **kwargs):
            return FakeMessage()

    class FakeClient:
        def __init__(self, *a, **k):
            self.messages = FakeMessages()

    monkeypatch.setattr(writer.anthropic, "Anthropic", FakeClient)
    writer.write_creative_description({}, operator_instruction="show the oil being poured")
    assert captured["operator_instruction"] == "show the oil being poured"


# ---- Offer-empty branch now also bans urgency phrasing / CTA button text (Step 2, Part 3) ----

def test_offer_text_absent_also_bans_urgency_phrasing_and_cta_button_text():
    """Regression guard: a live edit-mode run rendered "Grab Before They're Gone!" as a
    button with offer_text empty - the writer's own offer ban must cover this class too,
    not just discount/percentage numbers, and the original exact substring (asserted by
    test_offer_text_absent_forbids_any_offer_even_with_competitor_creative_objective) must
    survive unchanged."""
    prompt = writer._build_user_prompt({}, offer_text=None)
    assert "describe NO offer, badge, price, discount, or percentage of any kind" in prompt
    assert "urgency phrasing" in prompt
    assert "CTA button text" in prompt


# ---- Prompt 4, Item 2: efficacy claims are always banned in the image path (no
# approved_claims threading exists at this layer, so this is unconditional, not gated) ----

def test_efficacy_claims_always_banned_regardless_of_offer_text():
    for offer_text in (None, "20% off launch week"):
        prompt = writer._build_user_prompt({}, offer_text=offer_text)
        assert "describe NO quantified efficacy claim of any kind" in prompt
        assert "percentage improvement" in prompt
        assert "3x more effective" in prompt or "ratio" in prompt.lower()
        assert "in just 7 days" in prompt


def test_efficacy_claims_ban_present_even_with_include_product_false():
    prompt = writer._build_user_prompt({}, include_product=False)
    assert "describe NO quantified efficacy claim of any kind" in prompt
