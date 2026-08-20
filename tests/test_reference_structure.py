"""Pre-generation gate: reject a reference with no transferable structure
(2026-08-20). Two independent conditions, either sufficient to reject:

1. The union of every kind=="product" object's own bbox covers more than 60% of
   the frame.
2. Every top-level object is kind=="product", or a kind=="text" object whose
   serves_object_id/part_of points at a product.

Mirrors content_safety.hard_block_reason's own contract: a pure function
returning (reason, coverage), no side effects - pipeline.py itself does the
record_warning/log/mark_seen/skip dance, identical to the existing hard-block
gate."""
import json

from src import reference_structure as rs
from src.reference_structure import unusable_reference_reason, product_bbox_coverage


def _product(object_id, bbox, **overrides):
    base = {"object_id": object_id, "kind": "product", "bbox": bbox}
    base.update(overrides)
    return base


def _text(object_id, **overrides):
    base = {"object_id": object_id, "kind": "text", "text_purpose": "other"}
    base.update(overrides)
    return base


# ---- product_bbox_coverage: exact union, not a naive sum ----

def test_coverage_single_product():
    objects = [_product("obj_01", [0.1, 0.1, 0.4, 0.4])]
    assert abs(product_bbox_coverage(objects) - 0.16) < 1e-9


def test_coverage_two_non_overlapping_products_sums():
    objects = [
        _product("obj_01", [0.0, 0.0, 0.2, 0.2]),
        _product("obj_02", [0.5, 0.5, 0.2, 0.2]),
    ]
    assert abs(product_bbox_coverage(objects) - 0.08) < 1e-9


def test_coverage_two_overlapping_products_never_double_counted():
    # Two identical, fully-overlapping product bboxes - union must equal ONE of
    # them, never twice its area (the entire reason this is a union, not a sum).
    objects = [
        _product("obj_01", [0.1, 0.1, 0.4, 0.4]),
        _product("obj_02", [0.1, 0.1, 0.4, 0.4]),
    ]
    assert abs(product_bbox_coverage(objects) - 0.16) < 1e-9


def test_coverage_partial_overlap_computed_correctly():
    # [0,0,0.6,0.5] area=0.30, [0.4,0.2,0.6,0.5] area=0.30, overlap region
    # x:[0.4,0.6] (0.2) by y:[0.2,0.5] (0.3) = 0.06. Union = 0.30+0.30-0.06 = 0.54.
    objects = [
        _product("obj_01", [0.0, 0.0, 0.6, 0.5]),
        _product("obj_02", [0.4, 0.2, 0.6, 0.5]),
    ]
    assert abs(product_bbox_coverage(objects) - 0.54) < 1e-9


def test_coverage_ignores_non_product_objects():
    objects = [
        _product("obj_01", [0.0, 0.0, 0.3, 0.3]),
        _text("obj_02", bbox=[0.0, 0.0, 1.0, 1.0]),
        {"object_id": "obj_03", "kind": "prop", "bbox": [0.0, 0.0, 1.0, 1.0]},
    ]
    assert abs(product_bbox_coverage(objects) - 0.09) < 1e-9


def test_coverage_zero_when_no_products():
    objects = [_text("obj_01"), {"object_id": "obj_02", "kind": "prop"}]
    assert product_bbox_coverage(objects) == 0.0


def test_coverage_skips_malformed_bbox():
    objects = [
        _product("obj_01", [0.0, 0.0, 0.3, 0.3]),
        _product("obj_02", None),
        _product("obj_03", [0.1, 0.1]),
        _product("obj_04", [0, 0, -1, 5]),
    ]
    assert abs(product_bbox_coverage(objects) - 0.09) < 1e-9


# ---- _every_object_is_product_or_serves_one ----

def test_every_object_all_products_true():
    objects = [_product("obj_01", [0, 0, 0.1, 0.1]), _product("obj_02", [0.5, 0.5, 0.1, 0.1])]
    assert rs._every_object_is_product_or_serves_one(objects) is True


def test_every_object_text_serving_product_true():
    objects = [
        _product("obj_01", [0, 0, 0.5, 0.5]),
        _text("obj_02", serves_object_id="obj_01"),
    ]
    assert rs._every_object_is_product_or_serves_one(objects) is True


def test_every_object_text_part_of_product_true():
    objects = [
        _product("obj_01", [0, 0, 0.5, 0.5]),
        _text("obj_02", part_of="obj_01"),
    ]
    assert rs._every_object_is_product_or_serves_one(objects) is True


def test_every_object_false_when_independent_prop_present():
    objects = [
        _product("obj_01", [0, 0, 0.5, 0.5]),
        {"object_id": "obj_02", "kind": "prop", "description": "a wooden tray"},
    ]
    assert rs._every_object_is_product_or_serves_one(objects) is False


def test_every_object_false_when_text_serves_nothing():
    objects = [
        _product("obj_01", [0, 0, 0.5, 0.5]),
        _text("obj_02"),
    ]
    assert rs._every_object_is_product_or_serves_one(objects) is False


def test_every_object_false_when_no_products_at_all():
    objects = [_text("obj_01"), _text("obj_02")]
    assert rs._every_object_is_product_or_serves_one(objects) is False


def test_every_object_non_text_serving_object_still_counts_as_independent_structure():
    """Scoped to kind=='text' only, per the task's own literal wording - a prop
    that serves/belongs to a product is still independent physical structure,
    never exempted the way a text caption is."""
    objects = [
        _product("obj_01", [0, 0, 0.5, 0.5]),
        {"object_id": "obj_02", "kind": "prop", "serves_object_id": "obj_01"},
    ]
    assert rs._every_object_is_product_or_serves_one(objects) is False


# ---- unusable_reference_reason: combined gate ----

def test_unusable_reference_reason_none_for_ordinary_reference():
    blueprint = {"objects": [
        _text("obj_01"),
        _product("obj_02", [0.1, 0.1, 0.3, 0.4]),
        {"object_id": "obj_03", "kind": "prop", "description": "a wooden tray"},
    ]}
    reason, coverage = unusable_reference_reason(blueprint)
    assert reason is None
    assert abs(coverage - 0.12) < 1e-9


def test_unusable_reference_reason_rejects_high_coverage():
    blueprint = {"objects": [
        _product("obj_01", [0.0, 0.0, 0.9, 0.9]),
        _text("obj_02"),
    ]}
    reason, coverage = unusable_reference_reason(blueprint)
    assert reason is not None
    assert "coverage" in reason
    assert coverage > 0.6


def test_unusable_reference_reason_rejects_all_product_structure():
    blueprint = {"objects": [
        _product("obj_01", [0.0, 0.0, 0.1, 0.1]),
        _text("obj_02", serves_object_id="obj_01"),
    ]}
    reason, coverage = unusable_reference_reason(blueprint)
    assert reason is not None
    assert "nothing exists independently" in reason


def test_unusable_reference_reason_empty_objects_list_not_rejected():
    reason, coverage = unusable_reference_reason({"objects": []})
    assert reason is None
    assert coverage == 0.0


def test_unusable_reference_reason_missing_blueprint_fields_does_not_raise():
    reason, coverage = unusable_reference_reason({})
    assert reason is None
    assert coverage == 0.0
    reason, coverage = unusable_reference_reason(None)
    assert reason is None
    assert coverage == 0.0
