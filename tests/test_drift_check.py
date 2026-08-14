"""Unit tests for src/drift_check.py - Dynamic Edit System, Step 4. Synthetic PIL
images only, no real drafts, no network, no DB - the check itself is a pure function
of two byte strings plus a descriptor/blueprint."""
import io
from PIL import Image
from src.drift_check import (
    check_drift, expected_change_region, _parse_position_to_bbox,
    DRIFT_OUTSIDE_ZONE_THRESHOLD_PCT, CONTAINMENT_SCATTER_THRESHOLD_PCT, SKIP_TARGETS,
)

W, H = 200, 200


def _png_bytes(fill, patch=None):
    """A WxH solid-colour image, with an optional rectangular patch (x0,y0,x1,y1,colour)
    painted on top."""
    img = Image.new("RGB", (W, H), fill)
    if patch:
        x0, y0, x1, y1, colour = patch
        for y in range(y0, y1):
            for x in range(x0, x1):
                img.putpixel((x, y), colour)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


HEADLINE_DESCRIPTOR = {"target": "headline", "attribute": "text", "label": "Headline"}
BACKGROUND_DESCRIPTOR = {"target": "background", "attribute": "type", "label": "Background"}

BLUEPRINT_WITH_TOP_HEADLINE = {
    "text_purpose": [{"text_verbatim": "x", "purpose": "problem_hook", "placement": "top-centre"}],
}


def test_clean_edit_inside_zone_passes():
    v1 = _png_bytes((10, 10, 10))
    # change a patch inside the top band only (top-centre zone, padded ~ y in [0, 88])
    v2 = _png_bytes((10, 10, 10), patch=(60, 10, 140, 60, (250, 250, 250)))
    result = check_drift(v1, v2, HEADLINE_DESCRIPTOR, BLUEPRINT_WITH_TOP_HEADLINE)
    assert result["checked"] is True
    assert result["drift_flag"] is False
    assert result["outside_pct"] < DRIFT_OUTSIDE_ZONE_THRESHOLD_PCT
    assert result["inside_pct"] > 0


def test_synthetic_wide_change_outside_zone_fails():
    v1 = _png_bytes((10, 10, 10))
    # change a large patch in the BOTTOM half - well outside the top-centre headline zone
    v2 = _png_bytes((10, 10, 10), patch=(0, 150, 200, 200, (250, 250, 250)))
    result = check_drift(v1, v2, HEADLINE_DESCRIPTOR, BLUEPRINT_WITH_TOP_HEADLINE)
    assert result["checked"] is True
    assert result["drift_flag"] is True
    assert result["outside_pct"] > DRIFT_OUTSIDE_ZONE_THRESHOLD_PCT


def test_lighting_background_typography_still_skip_even_with_full_frame_change():
    # These are the ONLY targets that skip outright (SKIP_TARGETS) - whole-frame
    # effects by nature, not merely missing a recorded position.
    v1 = _png_bytes((10, 10, 10))
    v2 = _png_bytes((250, 250, 250))  # entire frame changed
    for target in ("lighting", "background", "typography"):
        descriptor = {"target": target, "attribute": "x", "label": target}
        result = check_drift(v1, v2, descriptor, BLUEPRINT_WITH_TOP_HEADLINE)
        assert result["method"] == "skip"
        assert result["checked"] is False
        assert result["drift_flag"] is False
        assert result["inside_pct"] is None
        assert result["outside_pct"] is None
        assert result["scatter_pct"] is None


def test_skip_targets_constant_matches_the_three_whole_frame_effects():
    assert SKIP_TARGETS == frozenset({"lighting", "background", "typography"})


def test_product_prop_person_body_offer_banner_have_no_zone_position():
    # No position field exists for these anywhere in the schema - expected_change_
    # region correctly returns None, which check_drift now reads as "use containment",
    # never "skip" (that's the coverage gap this fix closes).
    for target in ("product", "prop", "person_body", "offer", "banner"):
        descriptor = {"target": target, "attribute": "x", "label": target}
        region = expected_change_region(descriptor, BLUEPRINT_WITH_TOP_HEADLINE, W, H)
        assert region is None, f"{target} should have no derivable zone position"


# ---- Containment check: product/prop/etc. - one contained blob passes, scattered fails ----

PRODUCT_DESCRIPTOR = {"target": "product", "attribute": "placement", "label": "Product"}


def test_contained_product_swap_passes():
    # A single large, CONTIGUOUS change - e.g. a bottle repositioned/rescaled across a
    # big region. One connected blob, however large, is contained by definition.
    v1 = _png_bytes((10, 10, 10))
    v2 = _png_bytes((10, 10, 10), patch=(40, 40, 160, 160, (240, 240, 240)))
    result = check_drift(v1, v2, PRODUCT_DESCRIPTOR, {})
    assert result["method"] == "containment"
    assert result["checked"] is True
    assert result["drift_flag"] is False
    assert result["scatter_pct"] < CONTAINMENT_SCATTER_THRESHOLD_PCT
    assert result["inside_pct"] is None and result["outside_pct"] is None


def test_scattered_change_fails_containment():
    # Several small, DISCONNECTED patches spread across the frame - no single blob
    # dominates, so a large fraction of changed pixels sit outside the largest one.
    img = Image.new("RGB", (W, H), (10, 10, 10))
    for (cx, cy) in [(20, 20), (170, 20), (20, 170), (170, 170), (95, 95)]:
        for y in range(cy, cy + 8):
            for x in range(cx, cx + 8):
                if 0 <= x < W and 0 <= y < H:
                    img.putpixel((x, y), (240, 240, 240))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    v1 = _png_bytes((10, 10, 10))
    v2 = buf.getvalue()
    result = check_drift(v1, v2, PRODUCT_DESCRIPTOR, {})
    assert result["method"] == "containment"
    assert result["checked"] is True
    assert result["drift_flag"] is True
    assert result["scatter_pct"] > CONTAINMENT_SCATTER_THRESHOLD_PCT


def test_containment_no_change_at_all_is_not_drift():
    v1 = _png_bytes((10, 10, 10))
    v2 = _png_bytes((10, 10, 10))
    result = check_drift(v1, v2, PRODUCT_DESCRIPTOR, {})
    assert result["method"] == "containment"
    assert result["drift_flag"] is False
    assert result["scatter_pct"] == 0.0


def test_person_face_zone_derived_from_face_present_location():
    blueprint = {"face_present": {"has_face": True, "location": "lower-right of frame"}}
    descriptor = {"target": "person_face", "attribute": "age", "label": "Person - Age"}
    region = expected_change_region(descriptor, blueprint, W, H)
    assert region is not None
    x0, y0, x1, y1 = region
    assert x0 > W * 0.4  # right-biased
    assert y0 > H * 0.4  # bottom-biased


def test_parse_position_top_centre():
    bbox = _parse_position_to_bbox("top-centre", 1000, 1000)
    x0, y0, x1, y1 = bbox
    assert y0 == 0
    assert y1 < 500  # top band, not spanning the whole frame


def test_parse_position_unrecognized_falls_back_to_full_frame():
    bbox = _parse_position_to_bbox("somewhere odd", 1000, 1000)
    assert bbox == (0, 0, 1000, 1000)


def test_parse_position_empty_falls_back_to_full_frame():
    assert _parse_position_to_bbox("", 500, 500) == (0, 0, 500, 500)
