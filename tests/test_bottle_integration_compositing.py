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


# ---- Route B double-bottle fix, part 2 (2026-08-18). CONFIRMED LIVE, artifacts 1352/
# 1353: _bottle_integration_clause (the fix above) was already correctly suppressed, but
# THREE other sites in the same prompt still independently instructed or assumed a
# rendered bottle: _substitute_object_line's product branch ("place the Besque product
# here instead, at bbox [...]"), _edit_mode_instruction's own substitute-branch placement
# text ("place the Besque product... in its position"/"draw the Besque product
# NATIVELY..."), and product_clause's material-realism paragraph (meniscus/refraction/
# specular-highlight/label-wrap language). Gemini drew its own bottle per these
# instructions while Route B separately pasted the real cutout - two bottles, different
# sizes and colours, confirmed by direct visual inspection of the drafted image. Fixed by
# threading suppress_bottle_identity to all three, and removing the dangling "BOTTLE
# IDENTITY and BOTTLE GEOMETRY clauses above" references those sites made to clauses that
# no longer exist in the prompt when suppressed. The substance-recolour clause was
# checked and deliberately left untouched - it governs a substance that has LEFT the
# bottle (a drip/pour/smear elsewhere in the scene), a separate element Route B's cutout
# paste never covers, so it remains correct regardless of whether the bottle itself is
# drawn or pasted. ----

# "Draw the bottle" markers that must NEVER appear anywhere in a suppressed prompt -
# gathered directly from the real, unsuppressed instruction text each fixed site used to
# emit unconditionally (see the sites named above), not invented for this test.
_DRAW_THE_BOTTLE_MARKERS = (
    "place the Besque product here instead",       # _substitute_object_line, product branch
    "place a Besque bottle here",                  # _substitute_object_line, multi-instance branch
    "place the Besque product (shown in the reference photo(s) that follow",  # _edit_mode_instruction, photographic substitute
    "draw the Besque product NATIVELY",            # _edit_mode_instruction, illustrated substitute
    "a visible liquid volume with a distinct surface line (a meniscus)",  # _BOTTLE_MATERIAL_REALISM_CLAUSE
    "real specular highlights and reflections",    # _BOTTLE_MATERIAL_REALISM_CLAUSE
    "The label wraps the bottle's own curve",       # _BOTTLE_MATERIAL_REALISM_CLAUSE
)

# Dangling references: named clauses that do not exist anywhere in a suppressed prompt -
# nothing may point at them by name once they're gone.
_DANGLING_REFERENCE_MARKERS = (
    "BOTTLE IDENTITY and BOTTLE GEOMETRY clauses above",
    "BOTTLE GEOMETRY clause above",
    "this is what the Besque bottle IS",  # _bottle_identity_clause's own opening phrase
)


def _blueprint_with_substitute_product():
    """Mirrors the real double-bottle ad's shape (artifact 1352): one objects[] entry,
    kind=="product", disposition=="substitute", a real 4-element bbox - exactly what
    _composite_gate requires and what _substitute_object_line/_edit_mode_instruction
    both react to independently."""
    bp = load_blueprint_fixture("sample_hero_with_offer")
    bp = dict(bp)
    bp["objects"] = [
        {
            "object_id": "obj_02",
            "kind": "product",
            "description": "competitor eye serum tube, slim black squeeze tube",
            "bbox": [0.1, 0.2, 0.45, 0.75],
            "disposition": "substitute",
            "ownership": "competitor_branded",
            "carries_brand_mark": True,
            "role": "hero",
        },
    ]
    return bp


def _product():
    return {
        "name": "Magic Body Oil",
        "visual_description": "a tall cylindrical bottle with a gold collar and black pump",
        "substance_colour": "golden-amber oil",
    }


def test_suppressed_prompt_contains_zero_draw_the_bottle_instructions_edit_mode_photographic():
    bp = _blueprint_with_substitute_product()
    prompt = gip.build_image_prompt(
        bp, product=_product(), include_product=True, edit_mode=True, realism="ugc",
        suppress_bottle_identity=True,
    )
    assert "COMPOSITING MODE" in prompt  # sanity: suppression actually engaged
    for marker in _DRAW_THE_BOTTLE_MARKERS:
        assert marker not in prompt, f"draw-the-bottle instruction survived: {marker!r}"
    for marker in _DANGLING_REFERENCE_MARKERS:
        assert marker not in prompt, f"dangling reference to a suppressed clause: {marker!r}"


def test_suppressed_prompt_contains_zero_draw_the_bottle_instructions_edit_mode_illustrated():
    bp = _blueprint_with_substitute_product()
    prompt = gip.build_image_prompt(
        bp, product=_product(), include_product=True, edit_mode=True, realism="illustrated",
        suppress_bottle_identity=True,
    )
    assert "COMPOSITING MODE" in prompt
    for marker in _DRAW_THE_BOTTLE_MARKERS:
        assert marker not in prompt, f"draw-the-bottle instruction survived: {marker!r}"
    for marker in _DANGLING_REFERENCE_MARKERS:
        assert marker not in prompt, f"dangling reference to a suppressed clause: {marker!r}"


def test_suppressed_prompt_contains_zero_draw_the_bottle_instructions_template_branch():
    """Non-edit-mode branch - _edit_mode_instruction never runs here, but product_clause's
    material-realism paragraph is shared code and must be suppressed here too."""
    bp = _blueprint_with_substitute_product()
    prompt = gip.build_image_prompt(
        bp, product=_product(), include_product=True, edit_mode=False, realism=None,
        suppress_bottle_identity=True,
    )
    assert "COMPOSITING MODE" in prompt
    for marker in _DRAW_THE_BOTTLE_MARKERS:
        assert marker not in prompt, f"draw-the-bottle instruction survived: {marker!r}"
    for marker in _DANGLING_REFERENCE_MARKERS:
        assert marker not in prompt, f"dangling reference to a suppressed clause: {marker!r}"


def test_unsuppressed_prompt_still_contains_the_draw_instructions_control():
    """Control for the three tests above: proves they aren't vacuously passing (e.g. from
    a typo'd marker or an unrelated code path never reached) - the SAME setup with
    suppress_bottle_identity=False must still show at least one real draw-the-bottle
    instruction, exactly reproducing pre-fix behaviour."""
    bp = _blueprint_with_substitute_product()
    prompt = gip.build_image_prompt(
        bp, product=_product(), include_product=True, edit_mode=True, realism="ugc",
        suppress_bottle_identity=False,
    )
    assert "COMPOSITING MODE" not in prompt
    assert "place the Besque product (shown in the reference photo(s) that follow" in prompt
    assert "place the Besque product here instead" in prompt
    assert "a visible liquid volume with a distinct surface line (a meniscus)" in prompt


def test_suppressed_object_line_leaves_no_identity_dangling_reference_unit():
    """Unit-level companion to the prompt-level tests above, isolating
    _substitute_object_line's own product branch directly."""
    obj = {"object_id": "obj_02", "kind": "product", "bbox": [0.1, 0.2, 0.45, 0.75],
           "description": "competitor eye serum tube"}
    line = gip._substitute_object_line(
        obj, "product", None, "competitor eye serum tube", {}, suppress_bottle_identity=True,
    )
    assert "BOTTLE IDENTITY" not in line
    assert "BOTTLE GEOMETRY" not in line
    assert "place the Besque product here instead" not in line
    assert "PASTED here after generation" in line


def test_unsuppressed_object_line_unchanged_byte_for_byte():
    obj = {"object_id": "obj_02", "kind": "product", "bbox": [0.1, 0.2, 0.45, 0.75],
           "description": "competitor eye serum tube"}
    line = gip._substitute_object_line(
        obj, "product", None, "competitor eye serum tube", {}, suppress_bottle_identity=False,
    )
    assert line == (
        "SUBSTITUTE: this position held a competitor product (\"competitor eye serum "
        "tube\") - place the Besque product here instead, at bbox [0.1, 0.2, 0.45, 0.75] "
        "(this object's own recorded position and scale). Its identity (shape, "
        "proportions, colours, label) comes ONLY from the BOTTLE IDENTITY and BOTTLE "
        "GEOMETRY clauses above, never from this object's own colours or description."
    )
