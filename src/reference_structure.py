"""Pre-generation gate: reject a reference with no transferable structure
(2026-08-20).

If a reference ad is essentially wall-to-wall product with nothing else in the
frame, or every object in it exists only in service of the product, there is
nothing to actually CLONE - substituting Besque's own bottle in would just produce
"a Besque bottle filling most of the frame," not a real ad structure (a headline, a
layout, a composition worth reproducing). Two independent, purely structural
signals decide this - no message-level or claim analysis, matching this session's
own detection-only scope elsewhere in the objects model:

1. The union of every kind=="product" object's own bbox covers more than
   _PRODUCT_COVERAGE_REJECT_THRESHOLD of the frame.
2. Every top-level object is itself kind=="product", or is a kind=="text" object
   whose serves_object_id or part_of names a product object - i.e. nothing in the
   blueprint exists independently of the product(s) themselves.

Either condition alone is sufficient to reject; a reference can fail one, both, or
neither. Mirrors content_safety.hard_block_reason's own contract exactly (a pure
function returning a reason string or None, no side effects, no DB access) so
pipeline.py's own record_warning/log/mark_seen/skip dance is identical for both
gates - the caller decides what happens on rejection, this module only decides
whether to."""

# Calibrated against the live case this fix was written for (ad 1693447485074085,
# see tests/test_reference_structure.py for the exact figure) - not an arbitrary
# round number chosen with no reference point, though still a threshold pending
# more real cases to check it against.
_PRODUCT_COVERAGE_REJECT_THRESHOLD = 0.60


def _valid_bbox(bbox):
    return (
        isinstance(bbox, (list, tuple)) and len(bbox) == 4
        and all(isinstance(v, (int, float)) for v in bbox)
    )


def product_bbox_coverage(objects):
    """Exact union area of every kind=="product" object's own bbox, as a fraction
    of the full frame (bboxes are already fractional [0, 1] - schema/blueprint.
    schema.json's own convention). Computed via coordinate compression + a
    per-vertical-strip y-interval merge, not a grid approximation - a 60% threshold
    deserves an exact figure, not one sensitive to an arbitrary grid resolution.
    Overlapping product bboxes (e.g. a same_product_as-linked pair, or two
    genuinely distinct products) are counted ONCE where they overlap, never
    double-counted - that is the entire reason this is a union, not a sum.
    Malformed or missing bboxes are skipped, never raise. Returns 0.0 when there
    are no kind=="product" objects, or none with a usable bbox."""
    rects = []
    for obj in objects or []:
        if not isinstance(obj, dict) or obj.get("kind") != "product":
            continue
        bbox = obj.get("bbox")
        if not _valid_bbox(bbox):
            continue
        x, y, w, h = bbox
        if w <= 0 or h <= 0:
            continue
        rects.append((x, y, x + w, y + h))
    if not rects:
        return 0.0
    xs = sorted({r[0] for r in rects} | {r[2] for r in rects})
    total = 0.0
    for i in range(len(xs) - 1):
        x0, x1 = xs[i], xs[i + 1]
        strip_w = x1 - x0
        if strip_w <= 0:
            continue
        mid = (x0 + x1) / 2.0
        # Every rectangle spanning this strip is constant between two consecutive
        # distinct x-coordinates, so a single point-in-strip test at the midpoint
        # correctly identifies strip membership for the whole strip.
        intervals = sorted((r[1], r[3]) for r in rects if r[0] <= mid < r[2])
        merged_h = 0.0
        cur_start = cur_end = None
        for y0, y1 in intervals:
            if cur_start is None:
                cur_start, cur_end = y0, y1
            elif y0 <= cur_end:
                cur_end = max(cur_end, y1)
            else:
                merged_h += cur_end - cur_start
                cur_start, cur_end = y0, y1
        if cur_start is not None:
            merged_h += cur_end - cur_start
        total += strip_w * merged_h
    return total


def _every_object_is_product_or_serves_one(objects):
    """True when every entry in `objects` is either kind=="product", or a
    kind=="text" object whose serves_object_id or part_of names one of THIS
    blueprint's own product object_ids - i.e. nothing exists independently of the
    product(s). Scoped to kind=="text" only, per the task's own literal wording -
    a non-text, non-product object (a prop, a person, a graphic) that happens to
    serve/belong to a product still counts as independent structure, since it is
    a physical thing in the scene beyond the product itself, not just a caption
    naming it. An object missing object_id, or a blueprint with zero product
    objects at all, cannot satisfy this (there is nothing for a text object's
    serves_object_id/part_of to legitimately point at)."""
    product_ids = {
        obj.get("object_id") for obj in objects
        if obj.get("kind") == "product" and obj.get("object_id")
    }
    if not product_ids:
        return False
    for obj in objects:
        if obj.get("kind") == "product":
            continue
        if obj.get("kind") == "text" and (
            obj.get("serves_object_id") in product_ids or obj.get("part_of") in product_ids
        ):
            continue
        return False
    return True


def unusable_reference_reason(blueprint):
    """Return (reason: str|None, coverage: float) - reason is None when the
    reference has usable transferable structure, else a human-readable rejection
    reason. coverage (product_bbox_coverage's own return value) is ALWAYS computed
    and returned regardless of outcome, so a caller can log/report the figure even
    when rejection came from the other condition, or neither condition fired."""
    blueprint = blueprint or {}
    objects = [o for o in (blueprint.get("objects") or []) if isinstance(o, dict)]
    coverage = product_bbox_coverage(objects)
    if coverage > _PRODUCT_COVERAGE_REJECT_THRESHOLD:
        return (
            f"product bbox coverage {coverage:.1%} exceeds the "
            f"{_PRODUCT_COVERAGE_REJECT_THRESHOLD:.0%} threshold - the reference is "
            f"essentially wall-to-wall product with no other structure to clone",
            coverage,
        )
    if objects and _every_object_is_product_or_serves_one(objects):
        return (
            "every object in the reference is a product, or a text object that "
            "only serves a product - nothing exists independently of the "
            "product(s) themselves to clone",
            coverage,
        )
    return None, coverage
