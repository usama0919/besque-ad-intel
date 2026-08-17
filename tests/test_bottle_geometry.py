"""Tests for the fixed bottle-geometry clause (2026-08-16): src/generate_image_prompt.py
_bottle_geometry_clause() is a single hardcoded constant composed into every generate-
path prompt, never the realism-only targeted edit path, with no competing geometry
statement surviving anywhere else in prompt construction, and no blueprint-sourced free
text carrying bottle-shape language into the assembled prompt in the first place."""
from src import generate_image_prompt, deconstruct, realism_deltas

EXPECTED_GEOMETRY_CLAUSE = (
    "Bottle geometry is fixed and identical in every render. Total height is 4.33 "
    "times the glass body width. The glass body is a straight-sided cylinder with "
    "parallel walls and no taper, occupying the lower 2.85 body-widths. Above it a "
    "short shoulder of 0.21 body-widths meets a polished gold collar 0.75 "
    "body-widths wide and 0.63 body-widths tall. Above the collar sits a black "
    "pump: a stem 0.43 body-widths wide and a horizontal lever spout overhanging "
    "the body's left edge by 0.38 body-widths. These proportions never vary with "
    "scene, crop, style, realism, bottle count, or how large the bottle appears "
    "in frame."
)


def _blueprint(**overrides):
    bp = {
        "visual": {"layout": "clean centered", "subject": "amber bottle on marble",
                   "palette_mood": "warm", "text_placement": "lower third",
                   "scene_lighting": {}},
        "layout_detail": {"product_count": 1, "zone_positions": ["product mid-frame"]},
        "production_style": {"style": "high_spec"},
        "structural_zones": [], "text_purpose": [], "scene_elements": [],
        "face_present": {"has_face": False},
    }
    bp.update(overrides)
    return bp


# ---- Item 1: the clause itself is a hardcoded constant, no arguments ----

def test_clause_takes_no_arguments_and_is_byte_identical_across_calls():
    a = generate_image_prompt._bottle_geometry_clause()
    b = generate_image_prompt._bottle_geometry_clause()
    assert a == b == EXPECTED_GEOMETRY_CLAUSE


# ---- Item 2: composed into every generate-path prompt (all three build_image_prompt
# branches), byte-identical regardless of realism value, scene/style, or product count ----

REALISM_VALUES_TO_CHECK = ("ugc_native", "high_spec", "hybrid", "illustrated", None)
PRODUCT_COUNTS_TO_CHECK = (0, 1, 2, 5)


def test_geometry_clause_identical_across_realism_values_in_template_branch():
    for realism in REALISM_VALUES_TO_CHECK:
        prompt = generate_image_prompt.build_image_prompt(_blueprint(), realism=realism)
        assert EXPECTED_GEOMETRY_CLAUSE in prompt, f"missing for realism={realism!r}"


def test_geometry_clause_identical_across_realism_values_in_writer_branch():
    for realism in REALISM_VALUES_TO_CHECK:
        prompt = generate_image_prompt.build_image_prompt(
            _blueprint(), realism=realism, creative_description="A styled scene.",
        )
        assert EXPECTED_GEOMETRY_CLAUSE in prompt, f"missing for realism={realism!r}"


def test_geometry_clause_identical_across_realism_values_in_edit_mode_branch():
    for realism in REALISM_VALUES_TO_CHECK:
        prompt = generate_image_prompt.build_image_prompt(
            _blueprint(), realism=realism, edit_mode=True,
        )
        assert EXPECTED_GEOMETRY_CLAUSE in prompt, f"missing for realism={realism!r}"


def test_geometry_clause_identical_across_product_counts():
    for count in PRODUCT_COUNTS_TO_CHECK:
        bp = _blueprint(layout_detail={"product_count": count, "zone_positions": []})
        prompt = generate_image_prompt.build_image_prompt(bp, product_count=count)
        assert EXPECTED_GEOMETRY_CLAUSE in prompt, f"missing for product_count={count!r}"


def test_geometry_clause_identical_across_scene_types():
    scenes = [
        {"layout": "flat lay on white background", "subject": "product only",
         "palette_mood": "clean", "text_placement": "none", "scene_lighting": {}},
        {"layout": "lifestyle bathroom scene", "subject": "woman applying oil",
         "palette_mood": "warm golden", "text_placement": "lower third",
         "scene_lighting": {"light_direction": "upper-left"}},
        {"layout": "editorial split-screen before/after", "subject": "two panels",
         "palette_mood": "contrast", "text_placement": "top", "scene_lighting": {}},
    ]
    for visual in scenes:
        prompt = generate_image_prompt.build_image_prompt(_blueprint(visual=visual))
        assert EXPECTED_GEOMETRY_CLAUSE in prompt


def test_geometry_clause_absent_when_product_excluded():
    # Gated on effective_include_product, same as _bottle_identity_clause/_bottle_
    # integration_clause siblings - a deliberately productless run must never assert
    # bottle geometry facts about a bottle that isn't in the scene at all.
    prompt = generate_image_prompt.build_image_prompt(_blueprint(), include_product=False)
    assert EXPECTED_GEOMETRY_CLAUSE not in prompt


# ---- Item 3: no competing geometry statement survives anywhere else in prompt
# construction - the specific phrases that used to independently describe shape ----

_DELETED_COMPETING_PHRASES = (
    "not its silhouette or body shape",
    "not its height-to-width ratio or proportions",
    "not its neck, shoulder, or base",
    "not its pump or collar hardware design",
    "not its label's shape, placement, border, or content",
    "colours, proportions, and hardware",
    "work from silhouette, colour",
)


def test_no_deleted_competing_geometry_phrase_survives_in_any_branch():
    for edit_mode, creative_description in ((True, None), (False, None), (False, "A scene.")):
        prompt = generate_image_prompt.build_image_prompt(
            _blueprint(), edit_mode=edit_mode, creative_description=creative_description,
        )
        for phrase in _DELETED_COMPETING_PHRASES:
            assert phrase not in prompt, f"deleted phrase {phrase!r} resurfaced (edit_mode={edit_mode})"


def test_bottle_geometry_source_clause_no_longer_restates_categories():
    clause = generate_image_prompt._bottle_geometry_source_clause()
    for phrase in _DELETED_COMPETING_PHRASES:
        assert phrase not in clause


# ---- Item 2 (negative): the realism-only targeted edit path never carries the clause -
# it sends only its own pre-authored delta, nothing else ----

def test_realism_edit_deltas_contain_no_geometry_clause_or_its_facts():
    distinctive_facts = ("4.33", "2.85", "0.75 body-widths wide", "0.63 body-widths tall",
                         "0.43 body-widths wide", "0.38 body-widths")
    for value in realism_deltas.REALISM_VALUES:
        delta = realism_deltas.REALISM_DELTAS[value]
        assert EXPECTED_GEOMETRY_CLAUSE not in delta
        for fact in distinctive_facts:
            assert fact not in delta, f"{value!r} delta leaked geometry fact {fact!r}"


# ---- Item 4: blueprint-sourced free text reaching the prompt carries no bottle-shape
# terms - integration test of deconstruct's stripping feeding straight into
# build_image_prompt's edit-mode branch. product_category.signals/visual.subject no
# longer reach assembled prompt text at all (their one former reader,
# generate_image_prompt._competitor_props_clause, was deleted 2026-08-17), so this
# assertion holds even more strongly now than when it was written - kept as a
# regression guard in case a future clause starts reading them again. ----

def test_blueprint_shape_language_stripped_before_reaching_the_prompt():
    raw_blueprint = _blueprint(
        product_category={"category": "body_oil", "confidence": "high",
                           "signals": ["cylindrical applicator wand", "clear packaging"]},
        visual={"layout": "flat lay", "subject": "a tall cylindrical amber bottle",
                "palette_mood": "clean", "text_placement": "none", "scene_lighting": {}},
        layout_detail={"product_count": 1,
                       "zone_positions": ["tall pump collar bottle mid-frame", "CTA bottom"]},
    )
    cleaned_blueprint, filtered = deconstruct.strip_bottle_shape_language(raw_blueprint)

    # Reported filtering: exactly the fields traced to reach prompt construction.
    assert filtered["product_category.signals[]"] == ["cylindrical applicator wand"]
    assert filtered["visual.subject"] == ["a tall cylindrical amber bottle"]
    assert filtered["layout_detail.zone_positions[]"] == ["tall pump collar bottle mid-frame"]
    # Arrangement-only content survives untouched.
    assert cleaned_blueprint["product_category"]["signals"] == ["clear packaging"]
    assert cleaned_blueprint["visual"]["subject"] == ""
    assert cleaned_blueprint["layout_detail"]["zone_positions"] == ["CTA bottom"]

    prompt = generate_image_prompt.build_image_prompt(cleaned_blueprint, edit_mode=True)
    for leaked in ("cylindrical applicator wand", "tall cylindrical amber bottle",
                   "tall pump collar bottle mid-frame"):
        assert leaked not in prompt


def test_strip_bottle_shape_language_is_a_no_op_on_clean_arrangement_only_fields():
    bp = _blueprint(
        product_category={"category": "body_oil", "confidence": "high",
                           "signals": ["amber glass jar", "gold accents"]},
        layout_detail={"product_count": 2, "zone_positions": ["product mid-frame", "CTA bottom"]},
    )
    cleaned, filtered = deconstruct.strip_bottle_shape_language(bp)
    assert filtered == {}
    assert cleaned["product_category"]["signals"] == ["amber glass jar", "gold accents"]
    assert cleaned["layout_detail"]["zone_positions"] == ["product mid-frame", "CTA bottom"]


def test_strip_bottle_shape_language_never_mutates_the_original_blueprint():
    bp = _blueprint(
        product_category={"category": "body_oil", "confidence": "high",
                           "signals": ["cylindrical applicator wand"]},
    )
    original_signals = list(bp["product_category"]["signals"])
    deconstruct.strip_bottle_shape_language(bp)
    assert bp["product_category"]["signals"] == original_signals
