"""No-product-object placement fix (2026-08-19). Confirmed live: ad 1567146038752995
(blueprint: one person object, substitute; one sofa prop, keep; zero product objects)
- the draft invented a wooden side table with a bottle on it and cropped the woman
down to legs. Same on a separate pool ad, 2026-08-19 22:24: two bottles appeared on
an invented tray. include_product=True with no kind=="product" object previously gave
Gemini no computed placement at all - it improvised furniture.

First version of the fix maximised raw unoccupied area - re-run against the real ad
above, that picked [0, 0, 0.15, 0.35]: a sliver running from the top of the frame down
to the sofa's own top edge, numerically unoccupied but visually a bottle floating
against a wall, adjacency to anything never factored into the choice. REVISED to
require the region rest directly on a real, kept support surface (a sofa, table,
counter, floor - matched by keyword against kind=="prop"/disposition=="keep" objects
only) - never the largest gap for its own sake. No supported region -> skip, never a
floating placement."""
import pytest
from src import generate_image_prompt as gip
from tests.blueprint_fixtures import load_blueprint_fixture


# ---- _is_support_surface ----

def test_is_support_surface_accepts_kept_prop_matching_keyword():
    obj = {"kind": "prop", "disposition": "keep", "description": "a vintage sage sofa"}
    assert gip._is_support_surface(obj) is True


def test_is_support_surface_rejects_wrong_kind():
    obj = {"kind": "person", "disposition": "keep", "description": "sitting on a sofa"}
    assert gip._is_support_surface(obj) is False


def test_is_support_surface_rejects_wrong_disposition():
    obj = {"kind": "prop", "disposition": "substitute", "description": "a wooden table"}
    assert gip._is_support_surface(obj) is False


def test_is_support_surface_rejects_no_keyword_match():
    obj = {"kind": "prop", "disposition": "keep", "description": "a decorative wall clock"}
    assert gip._is_support_surface(obj) is False


# ---- find_supported_placement_bbox: the real ad, and synthetic scenarios ----

def test_real_ad_1567146038752995_finds_a_supported_region_not_a_floating_sliver():
    """Regression lock, exact real data. The first (area-maximising) version of this
    fix picked [0, 0, 0.15, 0.35] here - a floating sliver from the top of the frame.
    This version must rest on the sofa's own top edge (y=0.35) instead."""
    objects = [
        {"object_id": "obj_01", "kind": "person", "disposition": "substitute",
         "bbox": [0.15, 0.05, 0.72, 0.95], "description": "Woman appearing to be in her late 50s"},
        {"object_id": "obj_02", "kind": "prop", "disposition": "keep",
         "bbox": [0.0, 0.35, 1.0, 0.65], "description": "Vintage sage-green corduroy upholstered sofa"},
    ]
    bbox = gip.find_supported_placement_bbox(objects)
    assert bbox is not None
    x, y, w, h = bbox
    assert y + h == pytest.approx(0.35)  # rests exactly on the sofa's own top edge
    assert w == pytest.approx(0.15)
    assert h == pytest.approx(0.20)
    assert x == pytest.approx(0.0)  # the sofa's left arm, clear of the person


def test_no_support_object_returns_none():
    objects = [
        {"object_id": "obj_01", "kind": "person", "disposition": "substitute",
         "bbox": [0.1, 0.1, 0.8, 0.8], "description": "a woman"},
    ]
    assert gip.find_supported_placement_bbox(objects) is None


def test_support_object_fully_covered_by_another_object_returns_none():
    """A support exists, but the ENTIRE region directly above it is occupied by
    something else - no free sub-interval survives."""
    objects = [
        {"object_id": "obj_01", "kind": "prop", "disposition": "keep",
         "bbox": [0.0, 0.7, 1.0, 0.3], "description": "a wooden table"},
        {"object_id": "obj_02", "kind": "person", "disposition": "keep",
         "bbox": [0.0, 0.0, 1.0, 0.7], "description": "a woman standing behind the table"},
    ]
    assert gip.find_supported_placement_bbox(objects) is None


def test_support_too_close_to_frame_top_is_rejected_when_below_min_height():
    """The candidate height is clipped to the support's own distance from y=0 - a
    support sitting only 0.05 from the top of the frame can't offer a 0.20-tall
    region above it, and must be rejected rather than silently returning a shorter
    (invented-height) candidate."""
    objects = [
        {"object_id": "obj_01", "kind": "prop", "disposition": "keep",
         "bbox": [0.0, 0.05, 1.0, 0.9], "description": "a long counter"},
    ]
    assert gip.find_supported_placement_bbox(objects) is None


def test_picks_the_largest_supported_candidate_among_several():
    objects = [
        {"object_id": "obj_01", "kind": "prop", "disposition": "keep",
         "bbox": [0.0, 0.5, 0.3, 0.5], "description": "a small side table"},
        {"object_id": "obj_02", "kind": "prop", "disposition": "keep",
         "bbox": [0.5, 0.5, 0.5, 0.5], "description": "a wide counter"},
    ]
    bbox = gip.find_supported_placement_bbox(objects)
    assert bbox is not None
    x, y, w, h = bbox
    # the wider counter's own footprint (0.5) beats the small table's (0.3)
    assert w == pytest.approx(0.5)
    assert x == pytest.approx(0.5)


def test_ignores_disposition_substitute_or_drop_props_as_supports():
    """Only disposition=='keep' is trusted - a prop being substituted or dropped is
    not guaranteed to survive into the final image in a form that could hold anything."""
    objects = [
        {"object_id": "obj_01", "kind": "prop", "disposition": "substitute",
         "bbox": [0.0, 0.5, 1.0, 0.5], "description": "a counter"},
    ]
    assert gip.find_supported_placement_bbox(objects) is None


# ---- resolve_no_product_placement: the decision wrapper ----

_SUPPORTED_OBJECTS = [
    {"object_id": "obj_01", "kind": "person", "disposition": "substitute",
     "bbox": [0.15, 0.05, 0.72, 0.95], "description": "a woman"},
    {"object_id": "obj_02", "kind": "prop", "disposition": "keep",
     "bbox": [0.0, 0.35, 1.0, 0.65], "description": "a sofa"},
]

_UNSUPPORTED_OBJECTS = [
    {"object_id": "obj_01", "kind": "person", "disposition": "substitute",
     "bbox": [0.0, 0.0, 1.0, 1.0], "description": "a woman filling the frame"},
]

_HAS_PRODUCT_OBJECTS = [
    {"object_id": "obj_01", "kind": "product", "disposition": "substitute",
     "bbox": [0.3, 0.4, 0.2, 0.35], "description": "a competitor bottle"},
]


def test_not_applicable_when_include_product_false():
    bp = {"objects": _UNSUPPORTED_OBJECTS}
    applies, bbox, reason = gip.resolve_no_product_placement(bp, False)
    assert applies is False
    assert bbox is None
    assert reason == "not applicable"


def test_not_applicable_when_objects_empty_or_absent():
    assert gip.resolve_no_product_placement({}, True) == (False, None, "not applicable")
    assert gip.resolve_no_product_placement({"objects": []}, True) == (False, None, "not applicable")


def test_not_applicable_when_a_product_object_exists():
    bp = {"objects": _HAS_PRODUCT_OBJECTS}
    applies, bbox, reason = gip.resolve_no_product_placement(bp, True)
    assert applies is False
    assert bbox is None


def test_applies_and_finds_bbox_when_supported_region_exists():
    bp = {"objects": _SUPPORTED_OBJECTS}
    applies, bbox, reason = gip.resolve_no_product_placement(bp, True)
    assert applies is True
    assert bbox is not None
    assert reason == "ok"


def test_applies_but_fails_when_no_supported_region_exists():
    bp = {"objects": _UNSUPPORTED_OBJECTS}
    applies, bbox, reason = gip.resolve_no_product_placement(bp, True)
    assert applies is True
    assert bbox is None
    assert "no region large enough" in reason or "no region" in reason.lower()
    assert reason != "ok" and reason != "not applicable"


# ---- _no_product_placement_clause ----

def test_clause_states_the_bbox_and_forbids_new_furniture():
    clause = gip._no_product_placement_clause([0.0, 0.15, 0.15, 0.2])
    assert "[0.0, 0.15, 0.15, 0.2]" in clause
    assert "no new furniture" in clause.lower() or "do not introduce" in clause.lower() \
        or "do not" in clause.lower() and "furniture" in clause.lower()
    assert "keeps its own position and framing" in clause or "position and framing" in clause


# ---- build_image_prompt wiring: composed only when a bbox is actually given ----

def _blueprint_and_product():
    bp = load_blueprint_fixture("sample_hero_with_offer")
    product = {
        "name": "Magic Body Oil",
        "visual_description": "a tall cylindrical bottle with a gold collar and black pump",
        "substance_colour": "golden-amber oil",
    }
    return bp, product


def test_build_image_prompt_composes_placement_clause_when_bbox_given_edit_mode():
    bp, product = _blueprint_and_product()
    prompt = gip.build_image_prompt(
        bp, product=product, include_product=True, edit_mode=True,
        no_product_placement_bbox=[0.0, 0.15, 0.15, 0.2],
    )
    assert "PRODUCT PLACEMENT (STRICT, NO PRODUCT OBJECT IN THIS REFERENCE)" in prompt
    assert "[0.0, 0.15, 0.15, 0.2]" in prompt


def test_build_image_prompt_composes_placement_clause_template_branch():
    bp, product = _blueprint_and_product()
    prompt = gip.build_image_prompt(
        bp, product=product, include_product=True, edit_mode=False,
        no_product_placement_bbox=[0.0, 0.15, 0.15, 0.2],
    )
    assert "PRODUCT PLACEMENT (STRICT, NO PRODUCT OBJECT IN THIS REFERENCE)" in prompt


def test_build_image_prompt_composes_placement_clause_creative_description_branch():
    bp, product = _blueprint_and_product()
    prompt = gip.build_image_prompt(
        bp, product=product, include_product=True, edit_mode=False,
        creative_description="A warm, sunlit bathroom counter scene.",
        no_product_placement_bbox=[0.0, 0.15, 0.15, 0.2],
    )
    assert "PRODUCT PLACEMENT (STRICT, NO PRODUCT OBJECT IN THIS REFERENCE)" in prompt


def test_build_image_prompt_omits_placement_clause_when_bbox_none_byte_identical():
    """Control: no_product_placement_bbox=None (the default) must reproduce byte-
    identical output to before this parameter existed - proven by comparing against
    an explicit call with the parameter simply omitted."""
    bp, product = _blueprint_and_product()
    with_default = gip.build_image_prompt(bp, product=product, include_product=True, edit_mode=True)
    with_explicit_none = gip.build_image_prompt(
        bp, product=product, include_product=True, edit_mode=True, no_product_placement_bbox=None,
    )
    assert with_default == with_explicit_none
    assert "PRODUCT PLACEMENT (STRICT, NO PRODUCT OBJECT IN THIS REFERENCE)" not in with_default
