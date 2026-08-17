"""Unit tests for src/drift_check.py - Dynamic Edit System, Step 4. Synthetic PIL
images only, no real drafts, no network, no DB - the check itself is a pure function
of two byte strings plus a descriptor/blueprint."""
import io
from PIL import Image
from src.drift_check import (
    check_drift, expected_change_region, _parse_position_to_bbox,
    DRIFT_OUTSIDE_ZONE_THRESHOLD_PCT, CONTAINMENT_SCATTER_THRESHOLD_PCT, SKIP_TARGETS,
    MIN_COMPONENT_SIZE_PX,
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
    "objects": [{"object_id": "obj_01", "kind": "text", "text_purpose": "headline",
                 "description": "headline", "bbox": [0.2, 0.0, 0.6, 0.32]}],
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
    # BLUEPRINT_WITH_TOP_HEADLINE has no layout_detail.zone_positions at all, so
    # "product" correctly falls back to None here too (see test_product_zone_*
    # below for when zone_positions DOES name a product phrase) - prop/person_body/
    # offer/banner have no comparable field anywhere in the schema at all.
    # expected_change_region returning None is read by check_drift as "use
    # containment", never "skip" (that's the coverage gap the 2026-08-14 fix closed).
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


def test_ambient_noise_specks_below_min_size_do_not_trigger_drift():
    # Reproduces the live finding (2026-08-14, artifact 1249->1253): a real clean edit
    # measured 28.33% scatter_pct with no size filter, purely from thousands of 1-4px
    # ambient regeneration specks (water sparkle/caustic noise) - NOT real drift. Here:
    # one real, contained edit (a 30x30=900px patch) plus ~200 scattered 2x2=4px
    # specks - each well below MIN_COMPONENT_SIZE_PX (50) - covering the rest of the
    # frame. The specks must not count toward scatter_pct at all.
    assert 4 < MIN_COMPONENT_SIZE_PX  # sanity: the specks below are genuinely "tiny"
    img = Image.new("RGB", (W, H), (10, 10, 10))
    for y in range(60, 90):
        for x in range(60, 90):
            img.putpixel((x, y), (240, 240, 240))
    for i in range(200):
        sx = (i * 37) % (W - 2)
        sy = (i * 53) % (H - 2)
        if 60 <= sx <= 90 and 60 <= sy <= 90:
            continue  # skip specks that would land inside/merge with the real patch
        for yy in range(sy, sy + 2):
            for xx in range(sx, sx + 2):
                img.putpixel((xx, yy), (230, 230, 230))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    v1 = _png_bytes((10, 10, 10))
    v2 = buf.getvalue()
    result = check_drift(v1, v2, PRODUCT_DESCRIPTOR, {})
    assert result["method"] == "containment"
    assert result["drift_flag"] is False
    assert result["scatter_pct"] < CONTAINMENT_SCATTER_THRESHOLD_PCT


def test_person_face_zone_derived_from_face_present_location():
    blueprint = {"face_present": {"has_face": True, "location": "lower-right of frame"}}
    descriptor = {"target": "person_face", "attribute": "age", "label": "Person - Age"}
    region = expected_change_region(descriptor, blueprint, W, H)
    assert region is not None
    x0, y0, x1, y1 = region
    assert x0 > W * 0.4  # right-biased
    assert y0 > H * 0.4  # bottom-biased


# ---- Product ZONE derivation from layout_detail.zone_positions (2026-08-16, for the
# bottle-realism edit control): a real recorded position, when one exists, beats the
# containment fallback - containment alone answers "is the change one coherent blob,"
# not "did it land on the product" ----

def test_product_zone_derived_from_layout_detail_zone_positions():
    blueprint = {"layout_detail": {"zone_positions": [
        "headline top-center", "product mid-frame", "CTA bottom-full-width",
    ]}}
    descriptor = {"target": "product", "attribute": "realism", "label": "Product - Realism"}
    region = expected_change_region(descriptor, blueprint, W, H)
    assert region is not None


def test_product_zone_absent_when_zone_positions_has_no_product_phrase():
    blueprint = {"layout_detail": {"zone_positions": ["headline top-center", "CTA bottom-full-width"]}}
    descriptor = {"target": "product", "attribute": "realism", "label": "Product - Realism"}
    assert expected_change_region(descriptor, blueprint, W, H) is None


def test_product_zone_matches_bottle_phrasing_too():
    blueprint = {"layout_detail": {"zone_positions": ["bottle lower-left"]}}
    descriptor = {"target": "product", "attribute": "realism", "label": "Product - Realism"}
    assert expected_change_region(descriptor, blueprint, W, H) is not None


def test_realism_edit_uses_zone_method_when_zone_positions_names_product():
    blueprint = {"layout_detail": {"zone_positions": ["product top-centre"]}}
    descriptor = {"target": "product", "attribute": "realism", "label": "Product - Realism"}
    v1 = _png_bytes((10, 10, 10))
    # change confined to the top-centre zone the phrase points at
    v2 = _png_bytes((10, 10, 10), patch=(60, 10, 140, 60, (250, 250, 250)))
    result = check_drift(v1, v2, descriptor, blueprint)
    assert result["method"] == "zone"
    assert result["drift_flag"] is False


# ---- headline/subtext/cta ZONE derivation from a real blueprint.objects[].bbox
# (2026-08-17 rewire): replaces the deleted top-level text_purpose/structural_zones
# arrays - these targets no longer route through a keyword-parsed position string at
# all, they resolve straight from the matching object's own bbox. ----

def _text_object(purpose, bbox, object_id="obj_01"):
    return {"object_id": object_id, "kind": "text", "text_purpose": purpose,
            "description": purpose, "bbox": bbox}


def test_subtext_zone_derived_from_object_bbox():
    blueprint = {"objects": [_text_object("subtext", [0.1, 0.7, 0.8, 0.15])]}
    descriptor = {"target": "subtext", "attribute": "text", "label": "Subtext"}
    region = expected_change_region(descriptor, blueprint, W, H)
    assert region is not None
    x0, y0, x1, y1 = region
    assert y0 > H * 0.5  # lower-half biased, matching the bbox's own y


def test_cta_zone_derived_from_object_bbox():
    blueprint = {"objects": [_text_object("cta", [0.3, 0.85, 0.4, 0.1])]}
    descriptor = {"target": "cta", "attribute": "text", "label": "CTA"}
    region = expected_change_region(descriptor, blueprint, W, H)
    assert region is not None


def test_subtext_zone_absent_when_no_matching_object():
    blueprint = {"objects": [_text_object("headline", [0.1, 0.0, 0.8, 0.2])]}
    descriptor = {"target": "subtext", "attribute": "text", "label": "Subtext"}
    assert expected_change_region(descriptor, blueprint, W, H) is None


def test_headline_zone_object_with_no_bbox_falls_through():
    blueprint = {"objects": [{"object_id": "obj_01", "kind": "text",
                              "text_purpose": "headline", "description": "headline"}]}
    descriptor = {"target": "headline", "attribute": "text", "label": "Headline"}
    assert expected_change_region(descriptor, blueprint, W, H) is None


def test_badge_target_has_no_position_field_falls_through_to_containment():
    """badge no longer has ANY structured position field post-refactor (the old
    structural_zones "badge" zone_type is gone, and there is no per-object field
    identifying which graphic-kind object is the badge) - must fall through to
    containment, never a fabricated zone."""
    blueprint = {"objects": [{"object_id": "obj_01", "kind": "graphic",
                              "description": "NEW badge", "bbox": [0.7, 0.0, 0.2, 0.1]}]}
    descriptor = {"target": "badge", "attribute": "corner_badge", "label": "Corner badge"}
    assert expected_change_region(descriptor, blueprint, W, H) is None


def test_realism_edit_flags_a_single_contiguous_change_in_the_wrong_place():
    """The exact gap containment alone misses: ONE coherent blob of change - which
    containment would pass outright, however large - sitting entirely OUTSIDE the
    recorded product zone. A relocated/wrong-region realism edit must be flagged,
    not silently accepted because the change happened to be spatially contained."""
    blueprint = {"layout_detail": {"zone_positions": ["product top-centre"]}}
    descriptor = {"target": "product", "attribute": "realism", "label": "Product - Realism"}
    v1 = _png_bytes((10, 10, 10))
    # one single, fully contiguous patch - but in the BOTTOM half, nowhere near the
    # recorded top-centre product zone.
    v2 = _png_bytes((10, 10, 10), patch=(40, 150, 160, 195, (240, 240, 240)))
    zone_result = check_drift(v1, v2, descriptor, blueprint)
    assert zone_result["method"] == "zone"
    assert zone_result["drift_flag"] is True

    # Confirms the premise: the SAME pixels pass outright under containment alone
    # (no blueprint zone_positions at all) - this is the predicate gap being closed.
    containment_result = check_drift(v1, v2, descriptor, {})
    assert containment_result["method"] == "containment"
    assert containment_result["drift_flag"] is False


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


# ---- Stage 4 (2026-08-17): object-removal drift zone - the object's OWN bbox padded
# by OBJECT_REMOVAL_MARGIN_FRACTION, never the product zone ----

OBJECT_BLUEPRINT = {"objects": [
    {"object_id": "obj_02", "kind": "prop", "description": "wooden tray",
     "bbox": [0.25, 0.25, 0.25, 0.25]},
]}
OBJECT_DESCRIPTOR = {"target": "object", "attribute": "obj_02", "label": "wooden tray"}


def test_object_removal_uses_removal_zone_method():
    v1 = _png_bytes((10, 10, 10))
    # change confined inside the padded object bbox: object is [50,50]-[100,100] on a
    # 200x200 frame; with a 20% margin that's roughly [40,40]-[110,110].
    v2 = _png_bytes((10, 10, 10), patch=(55, 55, 95, 95, (240, 240, 240)))
    result = check_drift(v1, v2, OBJECT_DESCRIPTOR, OBJECT_BLUEPRINT)
    assert result["method"] == "removal_zone"
    assert result["drift_flag"] is False


def test_object_removal_flags_change_outside_the_padded_bbox():
    v1 = _png_bytes((10, 10, 10))
    # a large change entirely in the opposite corner of the frame - well outside even
    # a padded [40,40]-[110,110] zone.
    v2 = _png_bytes((10, 10, 10), patch=(150, 150, 200, 200, (240, 240, 240)))
    result = check_drift(v1, v2, OBJECT_DESCRIPTOR, OBJECT_BLUEPRINT)
    assert result["method"] == "removal_zone"
    assert result["drift_flag"] is True


def test_object_removal_falls_back_to_containment_when_object_id_not_found():
    v1 = _png_bytes((10, 10, 10))
    v2 = _png_bytes((10, 10, 10), patch=(40, 40, 160, 160, (240, 240, 240)))
    missing_descriptor = {"target": "object", "attribute": "obj_99", "label": "?"}
    result = check_drift(v1, v2, missing_descriptor, OBJECT_BLUEPRINT)
    assert result["method"] == "containment"
