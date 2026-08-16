"""Dynamic Edit System, Step 4: post-edit drift check.

Compares v1 (source) and v2 (result) pixel-for-pixel. Two methods, chosen per target:

1. ZONE (headline/subtext/cta/person_face/badge, when a position is actually
   recorded; product, when layout_detail.zone_positions names a product-shaped
   phrase - see _product_zone_position, added 2026-08-16 for the bottle-realism edit
   control): split into the EXPECTED-CHANGE region (from text_purpose.placement/
   structural_zones.position/face_present.location/layout_detail.zone_positions) vs
   everything else - % changed inside vs outside, same method as the Part A manual
   verification (2026-08-14, artifact 1251->1252): PIL ImageChops.difference()
   .convert("L"), thresholded.

2. CONTAINMENT (2026-08-14, closing the coverage gap): for prop/person_body/offer/
   banner, and for product/any zone-eligible target whose position wasn't recorded
   this time, there is no structural position to compare against, but skipping
   entirely left the most drift-prone class (product edits: tonight's floating
   bottle, retained competitor jar, wrong scale) with NO check at all. Instead: find
   every connected component of changed pixels and measure what fraction lies
   OUTSIDE the single LARGEST one. One big contiguous blob (a bottle repositioned/
   rescaled across a large area - still ONE coherent edit) reads as contained even
   though it's large; change scattered across multiple disconnected regions (the
   actual signature of hallucinated drift - Gemini altering unrelated parts of the
   frame) reads as uncontained regardless of how small each scattered patch is. A raw
   bbox-of-all-changed-pixels-vs-frame-fraction approach was considered and rejected:
   it can't tell "one large legitimate edit" from "scattered small ones" at all,
   since both can produce an equally large bbox - it would false-flag exactly the
   large, contained product edits this fix exists to stop skipping.

   NOTE this method's real limitation, confirmed live 2026-08-16: it answers "is the
   changed region spatially coherent," not "did the change land in the right place" -
   a single contiguous change relocated to the WRONG part of the frame still reads as
   fully contained. This is why product now prefers the ZONE method above whenever a
   real position is available; containment remains its only check when none is.

Only lighting/background/typography still skip outright (SKIP_TARGETS) - these
legitimately affect the whole frame by nature, so there is no "region" concept for
them at all, not merely a missing one.
"""
import io
import re
from PIL import Image, ImageChops

# Part A measured 0.076% outside-zone change on a clean, correctly-contained edit
# (artifact 1251 -> 1252) - this threshold gives ~13x headroom above that measurement
# before a ZONE-method result is flagged. A module constant, not inlined, so it can be
# tuned in one place as more real edits are measured.
DRIFT_OUTSIDE_ZONE_THRESHOLD_PCT = 1.0

# Calibrated 2026-08-14 against two real edits (artifact 1249->1253, a product
# reposition/rescale; artifact 1250->1254, a prop removal). Both are visually clean
# (confirmed by direct inspection) but the RAW containment measurement (no size filter)
# came back at 28.33% and 17.10% respectively - already past the original 15%
# estimate, on edits with nothing actually wrong. Diagnosis: 17,814 and 3,775 connected
# components respectively, the overwhelming majority 1-4px specks - ambient
# regeneration noise on textured surfaces (water sparkle/caustics), not real secondary
# edits. MIN_COMPONENT_SIZE_PX filters these out before the scatter fraction is
# computed. At that filter, the SAME two clean edits measure 9.36% and 2.81% - the
# threshold below has ~2.7x headroom over the higher of these two, deliberately less
# than the zone threshold's ~13x (Part A's headline edit measured 0.076% with almost no
# genuine secondary structure at all; these two containment edits have real, legitimate
# secondary changes - e.g. re-rendered label sub-regions on the moved bottle, water
# distortion at the edited object's edge - sitting just outside the single largest
# connected blob, so the "clean" baseline itself is inherently noisier here).
MIN_COMPONENT_SIZE_PX = 50

# See MIN_COMPONENT_SIZE_PX's own comment for the two measurements this was set from
# (9.36% and 2.81%, both with the filter applied). A different question from
# DRIFT_OUTSIDE_ZONE_THRESHOLD_PCT (that one measures % outside a DECLARED region; this
# measures % outside the largest CONNECTED blob) - kept as a separate constant
# deliberately, not reused, since the two may need to diverge further as more real
# product/prop/person_body edits are measured.
CONTAINMENT_SCATTER_THRESHOLD_PCT = 25.0

# Luma-of-difference threshold (0-255) above which a pixel counts as "changed" -
# identical value and method to the Part A manual diff
# (ImageChops.difference(...).convert("L"), threshold 30).
PIXEL_DIFF_THRESHOLD = 30

# The only targets with no region concept at all - whole-frame effects by nature, not
# merely missing a recorded position. Every other target either has a zone (when a
# position was recorded) or falls through to the containment check below.
SKIP_TARGETS = frozenset({"lighting", "background", "typography"})

# Position strings in this codebase's blueprints are qualitative ("top-centre", "lower-
# center offer banner"), never coordinates - bands are deliberately wide, and padded
# further below, so a slightly-off bucket doesn't itself cause a false drift flag.
_VERTICAL_BANDS = {
    "top": (0.0, 0.32), "upper": (0.0, 0.32),
    "bottom": (0.68, 1.0), "lower": (0.68, 1.0),
    "mid": (0.30, 0.70), "middle": (0.30, 0.70), "centre": (0.30, 0.70), "center": (0.30, 0.70),
}
_HORIZONTAL_BANDS = {
    "left": (0.0, 0.40), "right": (0.60, 1.0),
    "centre": (0.20, 0.80), "center": (0.20, 0.80),
}
_ZONE_PAD_FRACTION = 0.12

# Mirrors edit_capability._HEADLINE_SHAPED_PURPOSES - kept as a separate constant
# rather than importing it, since this module must stay independently testable and
# the two lists describe the same schema enum for two different reasons (control
# derivation there, expected-region lookup here).
_HEADLINE_SHAPED_PURPOSES = {"problem_hook", "efficacy_claim", "product_description"}


def _text_purpose_placement(blueprint, purposes):
    for tp in blueprint.get("text_purpose") or []:
        if (tp or {}).get("purpose") in purposes:
            return tp.get("placement")
    return None


def _structural_zone_position(blueprint, zone_type):
    for z in blueprint.get("structural_zones") or []:
        if (z or {}).get("zone_type") == zone_type:
            return z.get("position")
    return None


_PRODUCT_POSITION_PATTERN = re.compile(r"\b(?:product|bottle)\b", re.IGNORECASE)


def _product_zone_position(blueprint):
    """The 'product ...'-shaped phrase from layout_detail.zone_positions, if one
    exists - e.g. "product mid-frame" (src/deconstruct.py's own classifier prompt
    names this exact example). This is a real, deconstruct-time recorded fact, the
    same qualitative-position-string contract every other ZONE-eligible target here
    already relies on (text_purpose.placement/structural_zones.position/
    face_present.location) - never a guessed or invented box.

    2026-08-16: added because the CONTAINMENT fallback this target previously always
    used answers a different question - "is the changed region spatially coherent"
    (one connected blob passes, however large, wherever it sits) - not "did the
    change actually land on the product region." A realism edit that cleanly
    relocated its one contiguous change to the WRONG part of the frame would pass
    containment outright. Falls back to containment (returns None here) only when
    zone_positions has nothing product-shaped recorded - never fabricates one."""
    for phrase in (blueprint.get("layout_detail") or {}).get("zone_positions") or []:
        if _PRODUCT_POSITION_PATTERN.search(phrase or ""):
            return phrase
    return None


def _position_for_target(descriptor, blueprint):
    """The free-text position string this target's zone is recorded at, or None if
    none is recorded. None no longer means "skip" (2026-08-14) - only SKIP_TARGETS
    skip outright; every other target with no position here falls through to the
    containment check in check_drift, never a fabricated zone."""
    target = descriptor.get("target")
    if target == "headline":
        return _text_purpose_placement(blueprint, _HEADLINE_SHAPED_PURPOSES)
    if target == "subtext":
        return (_structural_zone_position(blueprint, "sub_line")
                or _structural_zone_position(blueprint, "body_copy")
                or _text_purpose_placement(blueprint, _HEADLINE_SHAPED_PURPOSES))
    if target == "cta":
        return (_structural_zone_position(blueprint, "cta")
                or _text_purpose_placement(blueprint, {"cta"}))
    if target == "person_face":
        return (blueprint.get("face_present") or {}).get("location")
    if target == "badge":
        return _structural_zone_position(blueprint, "badge")
    if target == "product":
        return _product_zone_position(blueprint)
    # prop, person_body, offer, banner: no position field exists for these anywhere
    # in schema/blueprint.schema.json - always falls through to containment.
    return None


def _parse_position_to_bbox(position, width, height):
    """Best-effort keyword parse of a free-text position string into a pixel bounding
    box (x0, y0, x1, y1). Falls back to the full frame if no directional keyword is
    found at all - better to under-flag on an unparsed string than mis-flag on a
    guessed box with no basis in the text."""
    if not position:
        return (0, 0, width, height)
    text = position.lower()
    v_band = next((band for kw, band in _VERTICAL_BANDS.items() if kw in text), None)
    h_band = next((band for kw, band in _HORIZONTAL_BANDS.items() if kw in text), None)
    if v_band is None and h_band is None:
        return (0, 0, width, height)
    v_band = v_band or (0.0, 1.0)
    h_band = h_band or (0.0, 1.0)
    pad = _ZONE_PAD_FRACTION
    y0 = max(0.0, v_band[0] - pad) * height
    y1 = min(1.0, v_band[1] + pad) * height
    x0 = max(0.0, h_band[0] - pad) * width
    x1 = min(1.0, h_band[1] + pad) * width
    return (int(x0), int(y0), int(x1), int(y1))


def expected_change_region(descriptor, blueprint, width, height):
    """Pixel bbox (x0, y0, x1, y1) for the edited target's zone, or None if this
    target has no recorded position - callers use None to select the containment
    check instead (see check_drift), never as "skip"."""
    position = _position_for_target(descriptor, blueprint or {})
    if not position:
        return None
    return _parse_position_to_bbox(position, width, height)


def _changed_mask(source_bytes, result_bytes):
    """A single-channel 0/255 PIL image: 255 where source and result differ by more
    than PIXEL_DIFF_THRESHOLD, same method as Part A."""
    a = Image.open(io.BytesIO(source_bytes)).convert("RGB")
    b = Image.open(io.BytesIO(result_bytes)).convert("RGB")
    if a.size != b.size:
        b = b.resize(a.size)
    diff = ImageChops.difference(a, b).convert("L")
    return diff.point(lambda p: 255 if p > PIXEL_DIFF_THRESHOLD else 0)


def _pixel_diff_stats(source_bytes, result_bytes, bbox):
    """ZONE method: % of pixels changed inside bbox vs outside it, each computed
    against that region's OWN pixel count (matches Part A's own reporting)."""
    mask = _changed_mask(source_bytes, result_bytes)
    W, H = mask.size
    px = mask.load()
    x0, y0, x1, y1 = bbox
    x0, x1 = max(0, x0), min(W, x1)
    y0, y1 = max(0, y0), min(H, y1)
    inside_total = max(0, x1 - x0) * max(0, y1 - y0)
    outside_total = (W * H) - inside_total
    inside_changed = 0
    outside_changed = 0
    for y in range(H):
        in_v = y0 <= y < y1
        for x in range(W):
            if px[x, y]:
                if in_v and x0 <= x < x1:
                    inside_changed += 1
                else:
                    outside_changed += 1
    inside_pct = 100.0 * inside_changed / inside_total if inside_total else 0.0
    outside_pct = 100.0 * outside_changed / outside_total if outside_total else 0.0
    return inside_pct, outside_pct


def _containment_scatter_stats(source_bytes, result_bytes):
    """CONTAINMENT method: 4-connected-component labelling (union-find) over the
    changed-pixel mask. Returns (scatter_pct, bbox).

    Components smaller than MIN_COMPONENT_SIZE_PX are dropped BEFORE scatter_pct is
    computed - excluded from both the numerator and the denominator, never counted as
    either "contained" or "scattered". Found live (2026-08-14, artifact 1249->1253):
    without this filter, thousands of 1-4px components from ambient regeneration noise
    on textured surfaces (water sparkle/caustics) alone pushed scatter_pct to 28% on a
    visually clean edit. scatter_pct is then the % of the SURVIVING changed pixels
    lying outside the single LARGEST surviving component. bbox is the bounding box of
    every changed pixel regardless of the filter (informational, not used for the
    verdict itself - unlike the zone method, a large bbox alone is not the signal here).

    Union-find is keyed by changed pixels only (a dict, not a full W*H array), so cost
    scales with how much actually changed, not the frame size - the same reasoning
    that keeps the zone method's pixel loop practical without numpy."""
    mask = _changed_mask(source_bytes, result_bytes)
    W, H = mask.size
    px = mask.load()
    parent = {}

    def find(i):
        root = i
        while parent[root] != root:
            root = parent[root]
        while parent[i] != root:
            parent[i], i = root, parent[i]
        return root

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    idx_of = {}
    min_x = min_y = None
    max_x = max_y = None
    for y in range(H):
        for x in range(W):
            if px[x, y]:
                idx = y * W + x
                parent[idx] = idx
                idx_of[(x, y)] = idx
                if min_x is None or x < min_x:
                    min_x = x
                if max_x is None or x > max_x:
                    max_x = x
                if min_y is None or y < min_y:
                    min_y = y
                if max_y is None or y > max_y:
                    max_y = y

    if not idx_of:
        return 0.0, None

    for (x, y), idx in idx_of.items():
        left = idx_of.get((x - 1, y))
        if left is not None:
            union(idx, left)
        top = idx_of.get((x, y - 1))
        if top is not None:
            union(idx, top)

    counts = {}
    for idx in idx_of.values():
        root = find(idx)
        counts[root] = counts.get(root, 0) + 1
    bbox = (min_x, min_y, max_x + 1, max_y + 1)

    kept_sizes = [size for size in counts.values() if size >= MIN_COMPONENT_SIZE_PX]
    total_kept = sum(kept_sizes)
    if not total_kept:
        # Every component was smaller than the noise floor - nothing meaningful
        # changed at all, which is not drift by definition.
        return 0.0, bbox
    largest = max(kept_sizes)
    scatter_pct = 100.0 * (total_kept - largest) / total_kept
    return scatter_pct, bbox


def check_drift(source_bytes, result_bytes, descriptor, blueprint):
    """Returns {"method", "checked", "drift_flag", "inside_pct", "outside_pct",
    "scatter_pct", "bbox"}.

    method="skip" (SKIP_TARGETS only: lighting/background/typography) - checked=False,
    drift_flag always False, every percentage None. Not a weaker pass - there is
    genuinely no region concept for these targets.

    method="zone" - a position was recorded for this target; inside_pct/outside_pct
    populated, scatter_pct None; drift_flag = outside_pct > DRIFT_OUTSIDE_ZONE_THRESHOLD_PCT.

    method="containment" - no position recorded (product/prop/person_body/offer/
    banner, or any zone-eligible target lacking one this time); scatter_pct populated,
    inside_pct/outside_pct None; drift_flag = scatter_pct > CONTAINMENT_SCATTER_THRESHOLD_PCT.
    bbox is the changed-pixel bounding box (informational only - see
    _containment_scatter_stats's own docstring for why it isn't the verdict signal)."""
    target = descriptor.get("target")
    if target in SKIP_TARGETS:
        return {"method": "skip", "checked": False, "drift_flag": False,
                "inside_pct": None, "outside_pct": None, "scatter_pct": None, "bbox": None}

    a = Image.open(io.BytesIO(source_bytes)).convert("RGB")
    width, height = a.size
    bbox = expected_change_region(descriptor, blueprint or {}, width, height)
    if bbox is not None:
        inside_pct, outside_pct = _pixel_diff_stats(source_bytes, result_bytes, bbox)
        drift_flag = outside_pct > DRIFT_OUTSIDE_ZONE_THRESHOLD_PCT
        return {"method": "zone", "checked": True, "drift_flag": drift_flag,
                "inside_pct": inside_pct, "outside_pct": outside_pct,
                "scatter_pct": None, "bbox": bbox}

    scatter_pct, changed_bbox = _containment_scatter_stats(source_bytes, result_bytes)
    drift_flag = scatter_pct > CONTAINMENT_SCATTER_THRESHOLD_PCT
    return {"method": "containment", "checked": True, "drift_flag": drift_flag,
            "inside_pct": None, "outside_pct": None, "scatter_pct": scatter_pct,
            "bbox": changed_bbox}
