"""Route B double-bottle fix (2026-08-19). CONFIRMED LIVE, ad `2767866756880226`:
_composite_gate passed "True (ok)" at 16:29, draft written 16:31 - the pasted cutout
was correct, but Gemini ALSO drew its own bottle (a taller amber bottle with a
partial label) behind and left of it. Root cause: when the gate passes,
_bottle_identity_clause/_bottle_geometry_clause are already suppressed via
suppress_bottle_identity, but _bottle_integration_clause was left unconditional -
still asking Gemini to place "a PARTICIPATING OBJECT... held, in the process of
being applied, or resting." Nothing told it not to draw a bottle at all.

Fix: _bottle_integration_clause(suppress_bottle_identity) now has two branches.
False (the default) reproduces the prior text byte-for-byte, verified below against
a literal copy of the pre-fix return value. True asks for the same scene
participation (scale, contact/grip shadow, grip conformation when a hand is
present) as an EMPTY, product-shaped space, and explicitly forbids rendering the
product's own form, label, or pump."""
from src import generate_image_prompt as gip
from tests.blueprint_fixtures import load_blueprint_fixture

# Copied verbatim from the pre-fix function body (git HEAD, src/generate_image_prompt.py
# ~line 2028) - not derived from the function under test, so this can't pass by
# comparing the code to itself.
PRE_FIX_TEXT = (
    "BOTTLE INTEGRATION (STRICT, EVERY GENERATION PATH, OVERRIDES ANY COMPOSITION-"
    "MATCHING INSTRUCTION ELSEWHERE THAT WOULD REPRODUCE A FLOATING PRODUCT SHOT): "
    "the bottle is a PARTICIPATING OBJECT in this scene, never a flat packshot "
    "pasted on top of it. It must be held, in the process of being applied, or "
    "resting on a real surface within the scene - never floating, never centred on "
    "an empty background unrelated to the composition around it - even when the "
    "reference ad itself shows the competitor's product as a floating, ungrounded "
    "packshot: that presentation is never reproduced for Besque's own bottle, "
    "regardless of how faithfully the surrounding composition is otherwise matched. "
    "Scale it consistently with whatever is nearest it - a hand, a shelf, a "
    "counter, a towel - never larger or smaller than that context would allow. A "
    "contact shadow (or, where held, a grip shadow) must be visible wherever the "
    "bottle meets a hand or surface - its absence is what makes a packshot read as "
    "pasted in. WHEN HELD: fingers wrap convincingly around the bottle's body, the "
    "wrist sits at a natural angle for that grip, and the bottle is scaled "
    "correctly to the hand holding it - never a hand posed around a bottle-shaped "
    "gap, and never a hand too large or small for the bottle it holds. WHEN THE "
    "PRODUCT IS BEING APPLIED: show the oil visibly on the skin, not only the "
    "bottle in frame. The bottle must NEVER overlap a text block or caption - if "
    "the composition would otherwise place one over the other, move or resize the "
    "bottle within the scene's own logic (per its stated scale) rather than let it "
    "cross behind or in front of rendered text. PUMP/CAP ORIENTATION follows THIS "
    "SCENE's own composition and whichever hand or surface the bottle sits on or is "
    "held by - never fixed to match the facing shown in Besque's own reference "
    "photo(s), which fix the pump's design and geometry only, never which way it "
    "points. Rotate the pump/cap to whatever facing this scene's grip or resting "
    "position actually requires. "
)


def test_default_call_unchanged_byte_for_byte():
    assert gip._bottle_integration_clause() == PRE_FIX_TEXT


def test_false_explicit_unchanged_byte_for_byte():
    assert gip._bottle_integration_clause(False) == PRE_FIX_TEXT


def test_compositing_mode_forbids_drawing_the_bottle():
    clause = gip._bottle_integration_clause(True)
    assert "NEVER draw the bottle itself" in clause
    assert "Do not render any bottle, container, packaging, label, pump, cap, or liquid" in clause
    assert "PARTICIPATING OBJECT" not in clause
    assert "It must be held, in the process of being applied, or" not in clause


def test_compositing_mode_still_requests_scale_and_contact_shadow():
    clause = gip._bottle_integration_clause(True)
    assert "scale the empty space consistently with whatever is nearest it" in clause
    assert "contact shadow" in clause
    assert "grip shadow" in clause


def test_compositing_mode_requests_grip_pose_when_hand_present():
    clause = gip._bottle_integration_clause(True)
    assert "WHEN A HAND IS PRESENT" in clause
    assert "fingers curled as if wrapped around a bottle's body" in clause
    assert "never resting open or flat as if holding nothing" in clause


def test_compositing_mode_never_leaves_space_for_another_object_to_intrude():
    clause = gip._bottle_integration_clause(True)
    assert "Never let anything else" in clause
    assert "occupy or overlap the space reserved for the bottle" in clause


def _blueprint_and_product():
    bp = load_blueprint_fixture("sample_hero_with_offer")
    product = {
        "name": "Magic Body Oil",
        "visual_description": "a tall cylindrical bottle with a gold collar and black pump",
        "substance_colour": "golden-amber oil",
    }
    return bp, product


def test_build_image_prompt_edit_mode_compositing_suppresses_participating_object_instruction():
    bp, product = _blueprint_and_product()
    prompt = gip.build_image_prompt(
        bp, product=product, include_product=True, edit_mode=True, realism=None,
        suppress_bottle_identity=True,
    )
    assert "COMPOSITING MODE" in prompt
    assert "NEVER draw the bottle itself" in prompt
    assert "It must be held, in the process of being applied, or" not in prompt
    # identity/geometry were already suppressed before this fix - confirm that
    # existing behaviour survives untouched alongside the new integration fix.
    assert "this is what the Besque bottle IS" not in prompt


def test_build_image_prompt_edit_mode_non_compositing_unchanged():
    bp, product = _blueprint_and_product()
    prompt = gip.build_image_prompt(
        bp, product=product, include_product=True, edit_mode=True, realism=None,
        suppress_bottle_identity=False,
    )
    assert "COMPOSITING MODE" not in prompt
    assert "It must be held, in the process of being applied, or" in prompt
    assert "this is what the Besque bottle IS" in prompt


def test_build_image_prompt_template_branch_compositing_suppresses_participating_object_instruction():
    """The template (non-edit-mode, no creative_description) branch has its own
    _bottle_integration_clause call site - confirm the fix reaches it too, not just
    the edit-mode branch the live bug was found on."""
    bp, product = _blueprint_and_product()
    prompt = gip.build_image_prompt(
        bp, product=product, include_product=True, edit_mode=False, realism=None,
        suppress_bottle_identity=True,
    )
    assert "COMPOSITING MODE" in prompt
    assert "It must be held, in the process of being applied, or" not in prompt


def test_build_image_prompt_creative_description_branch_compositing_suppresses_participating_object_instruction():
    """The writer/creative_description branch has its own _bottle_integration_clause
    call site too - same confirmation as the template branch above."""
    bp, product = _blueprint_and_product()
    prompt = gip.build_image_prompt(
        bp, product=product, include_product=True, edit_mode=False, realism=None,
        creative_description="A warm, sunlit bathroom counter scene.",
        suppress_bottle_identity=True,
    )
    assert "COMPOSITING MODE" in prompt
    assert "It must be held, in the process of being applied, or" not in prompt
