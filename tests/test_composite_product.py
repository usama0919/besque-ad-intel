"""Tests for Route B compositing (2026-08-17): pasting the real product cutout into a
generated draft instead of asking Gemini to draw the bottle, scoped to placements Pillow
can do convincingly. No network, no GCS, no Gemini call - _fetch_product_cutout_bytes is
never exercised here; composite_product/_composite_gate both take real bytes/a real
blueprint dict directly."""
import io

from PIL import Image

from src import generate_image_prompt


def _base_png_bytes(width, height, colour):
    """A fully opaque base 'generated draft' - deliberately a mid-grey (luminance ~76)
    so it matches pure red's own luminance (255*0.299 ~= 76). This neutralises
    _match_brightness_conservative for the aspect-ratio test below (factor lands within
    the <2% no-op band), so the pasted cutout's colour survives compositing unshifted -
    the test is about GEOMETRY (does the pasted region keep the cutout's own aspect
    ratio), not about the brightness-matching step, which has its own dedicated tests."""
    buf = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buf, format="PNG")
    return buf.getvalue()


def _cutout_png_bytes(width, height, colour):
    buf = io.BytesIO()
    Image.new("RGBA", (width, height), (*colour, 255)).save(buf, format="PNG")
    return buf.getvalue()


def _find_colour_bbox(img, colour, tolerance=8):
    """Scans an RGB image for the pixel bounding box of anything matching `colour`
    within `tolerance` per channel - measures where composite_product ACTUALLY pasted
    the cutout, independent of composite_product's own internal arithmetic."""
    img = img.convert("RGB")
    w, h = img.size
    px = img.load()
    min_x, min_y, max_x, max_y = w, h, -1, -1
    for y in range(h):
        for x in range(w):
            r, g, b = px[x, y]
            if (abs(r - colour[0]) <= tolerance and abs(g - colour[1]) <= tolerance
                    and abs(b - colour[2]) <= tolerance):
                if x < min_x:
                    min_x = x
                if y < min_y:
                    min_y = y
                if x > max_x:
                    max_x = x
                if y > max_y:
                    max_y = y
    if max_x < 0:
        return None
    return (min_x, min_y, max_x + 1, max_y + 1)


# ---- composite_product: aspect ratio preservation (item 1) ----

def test_composite_product_preserves_cutout_aspect_ratio_under_non_matching_bbox():
    base_colour = (76, 76, 76)  # matches pure red's own luminance - see _base_png_bytes
    cutout_colour = (255, 0, 0)
    cutout_w, cutout_h = 40, 124  # ratio 0.3226 - close to the real 503:1562 cutout
    base_bytes = _base_png_bytes(400, 400, base_colour)
    cutout_bytes = _cutout_png_bytes(cutout_w, cutout_h, cutout_colour)
    # A deliberately WIDE bbox (ratio far from the cutout's own tall, narrow shape) -
    # 280x100px on a 400x400 base - proves the paste does NOT stretch to match this.
    bbox = [0.1, 0.1, 0.7, 0.25]

    result_bytes = generate_image_prompt.composite_product(base_bytes, cutout_bytes, bbox)
    result = Image.open(io.BytesIO(result_bytes))

    pasted_bbox = _find_colour_bbox(result, cutout_colour)
    assert pasted_bbox is not None, "no cutout-coloured pixels found in the result"
    px0, py0, px1, py1 = pasted_bbox
    pasted_w, pasted_h = px1 - px0, py1 - py0
    pasted_ratio = pasted_w / pasted_h
    cutout_ratio = cutout_w / cutout_h
    bbox_ratio = (0.7 * 400) / (0.25 * 400)

    assert abs(pasted_ratio - cutout_ratio) < 0.03, (
        f"pasted region ratio {pasted_ratio:.3f} does not match the cutout's own "
        f"ratio {cutout_ratio:.3f}"
    )
    assert abs(pasted_ratio - bbox_ratio) > 0.5, (
        "pasted region ratio suspiciously close to the bbox's own ratio - looks stretched"
    )


def test_composite_product_anchors_to_bbox_bottom_and_centres_horizontally():
    base_colour = (76, 76, 76)
    # Luminance ~76 (130*0.587 ~= 76) to match base_colour's own - same reasoning as
    # the red used above, so _match_brightness_conservative is a no-op and the pasted
    # pixels keep exactly this colour for the bbox scan below.
    cutout_colour = (0, 130, 0)
    cutout_w, cutout_h = 30, 90
    base_bytes = _base_png_bytes(400, 400, base_colour)
    cutout_bytes = _cutout_png_bytes(cutout_w, cutout_h, cutout_colour)
    bbox = [0.2, 0.1, 0.4, 0.6]  # box: x[80,240] y[40,280]

    result = Image.open(io.BytesIO(
        generate_image_prompt.composite_product(base_bytes, cutout_bytes, bbox)))
    px0, py0, px1, py1 = _find_colour_bbox(result, cutout_colour)

    box_x0, box_y0, box_w, box_h = 80, 40, 160, 240
    # bottom-anchored: the pasted region's bottom edge sits at (or within a couple of
    # px of) the bbox's own bottom edge, never floating mid-box.
    assert abs(py1 - (box_y0 + box_h)) <= 2
    # horizontally centred within the bbox
    pasted_cx = (px0 + px1) / 2
    box_cx = box_x0 + box_w / 2
    assert abs(pasted_cx - box_cx) <= 2


# ---- _composite_gate: every gate, both directions (items 2-4) ----

def _clean_blueprint(**overrides):
    bp = {
        "objects": [
            {"object_id": "obj_01", "kind": "product", "description": "amber body oil bottle",
             "bbox": [0.3, 0.4, 0.2, 0.35], "ownership": "competitor_branded",
             "carries_brand_mark": True, "role": "hero", "persuasive_function": "hero product",
             "disposition": "substitute"},
        ],
        "background": {"surface": "marble counter", "colour": "white",
                       "light": "soft warm light from upper-left"},
    }
    bp.update(overrides)
    return bp


def test_composite_gate_accepts_the_clean_case():
    proceed, reason, obj = generate_image_prompt._composite_gate(_clean_blueprint())
    assert proceed is True
    assert reason == "ok"
    assert obj["object_id"] == "obj_01"


def test_composite_gate_rejects_when_include_product_false():
    proceed, reason, obj = generate_image_prompt._composite_gate(
        _clean_blueprint(), include_product=False)
    assert proceed is False
    assert "include_product" in reason
    assert obj is None


def test_composite_gate_rejects_when_no_product_object():
    bp = _clean_blueprint(objects=[])
    proceed, reason, obj = generate_image_prompt._composite_gate(bp)
    assert proceed is False
    assert "found 0" in reason
    assert obj is None


def test_composite_gate_rejects_when_multiple_substitute_products():
    bp = _clean_blueprint()
    bp["objects"].append({**bp["objects"][0], "object_id": "obj_02"})
    proceed, reason, obj = generate_image_prompt._composite_gate(bp)
    assert proceed is False
    assert "found 2" in reason


def test_composite_gate_rejects_when_disposition_is_not_substitute():
    bp = _clean_blueprint()
    bp["objects"][0]["disposition"] = "keep"
    proceed, reason, obj = generate_image_prompt._composite_gate(bp)
    assert proceed is False
    assert "found 0" in reason


def test_composite_gate_rejects_when_bbox_missing():
    bp = _clean_blueprint()
    del bp["objects"][0]["bbox"]
    proceed, reason, obj = generate_image_prompt._composite_gate(bp)
    assert proceed is False
    assert "no usable" in reason


def test_composite_gate_rejects_when_bbox_malformed():
    bp = _clean_blueprint()
    bp["objects"][0]["bbox"] = [0.1, 0.2]
    proceed, reason, obj = generate_image_prompt._composite_gate(bp)
    assert proceed is False
    assert "no usable" in reason


def test_composite_gate_rejects_when_description_reads_as_held():
    bp = _clean_blueprint()
    bp["objects"][0]["description"] = "amber bottle held in a hand"
    proceed, reason, obj = generate_image_prompt._composite_gate(bp)
    assert proceed is False
    assert "held/gripped" in reason


def test_composite_gate_rejects_when_a_prop_serves_the_product():
    bp = _clean_blueprint()
    bp["objects"].append({
        "object_id": "obj_hand", "kind": "prop", "description": "a hand",
        "ownership": "generic", "carries_brand_mark": False, "role": "supporting_prop",
        "persuasive_function": "holds the product", "disposition": "keep",
        "serves_object_id": "obj_01",
    })
    proceed, reason, obj = generate_image_prompt._composite_gate(bp)
    assert proceed is False
    assert "obj_hand" in reason
    assert "held or staged" in reason


def test_composite_gate_does_not_reject_when_a_text_object_serves_the_product():
    # serves_object_id from a kind=="text" object (e.g. a callout naming the product)
    # is NOT a physical holding relationship - only "prop" triggers the holder check.
    bp = _clean_blueprint()
    bp["objects"].append({
        "object_id": "obj_callout", "kind": "text", "text_purpose": "product_callout",
        "description": "callout naming the product", "ownership": "generic",
        "carries_brand_mark": False, "role": "secondary",
        "persuasive_function": "names the product", "disposition": "substitute",
        "serves_object_id": "obj_01",
    })
    proceed, reason, obj = generate_image_prompt._composite_gate(bp)
    assert proceed is True


def test_composite_gate_rejects_when_only_the_person_object_mentions_holding():
    """Held-product gate fix (2026-08-19), confirmed live on ad 1252553972969618: the
    product's own description and every prop were clean, but the PERSON object's
    description read "...holding the product beside her face" - the gate previously
    only inspected the product's own description and kind=="prop" objects naming it
    via serves_object_id, so this passed through and Route B pasted a free-standing
    bottle that ended up floating over the hand in the generated draft."""
    bp = _clean_blueprint()
    bp["objects"].append({
        "object_id": "obj_person", "kind": "person",
        "description": "Young woman with blonde wavy hair, holding the product beside her face",
        "ownership": "person", "carries_brand_mark": False, "role": "hero",
        "persuasive_function": "demonstrates the product", "disposition": "substitute",
    })
    proceed, reason, obj = generate_image_prompt._composite_gate(bp)
    assert proceed is False
    assert "obj_person" in reason
    assert "held/gripped" in reason


def test_composite_gate_does_not_reject_when_person_description_has_no_grip_language():
    """Control for the test above - an ordinary person description with no grip-shaped
    language must not trip the new broadened check."""
    bp = _clean_blueprint()
    bp["objects"].append({
        "object_id": "obj_person", "kind": "person",
        "description": "Young woman with blonde wavy hair, smiling at the camera",
        "ownership": "person", "carries_brand_mark": False, "role": "hero",
        "persuasive_function": "demonstrates the product", "disposition": "substitute",
    })
    proceed, reason, obj = generate_image_prompt._composite_gate(bp)
    assert proceed is True


def test_composite_gate_rejects_when_lighting_is_hard():
    bp = _clean_blueprint(background={"surface": "sand", "colour": "beige",
                                       "light": "harsh direct sunlight casting hard shadows"})
    proceed, reason, obj = generate_image_prompt._composite_gate(bp)
    assert proceed is False
    assert "hard/directional" in reason


def test_composite_gate_accepts_soft_light_language():
    bp = _clean_blueprint(background={"surface": "linen", "colour": "cream",
                                       "light": "soft diffuse light, no visible shadow direction"})
    proceed, reason, obj = generate_image_prompt._composite_gate(bp)
    assert proceed is True


def test_composite_gate_accepts_missing_light_field():
    # No light phrase recorded at all - absence of hard-light keywords is the gate
    # condition, never a requirement that the phrase explicitly says "soft".
    bp = _clean_blueprint(background={"surface": "linen", "colour": "cream"})
    proceed, reason, obj = generate_image_prompt._composite_gate(bp)
    assert proceed is True


# ---- build_image_prompt: geometry/identity suppressed ONLY when compositing proceeds
# (item 6) ----

_PRODUCT = {
    "visual_description": "Clear glass bottle, terracotta label, black pump.",
    "substance_colour": "golden-amber",
}

_GEOMETRY_MARKER = "4.33"  # from _bottle_geometry_clause's own hardcoded proportions
_IDENTITY_MARKER = "Clear glass bottle, terracotta label, black pump."
_INTEGRATION_MARKER = "PARTICIPATING OBJECT"
_INTEGRATION_COMPOSITING_MARKER = "COMPOSITING MODE"  # 2026-08-19 double-bottle fix
_GEOMETRY_SOURCE_MARKER = "BOTTLE GEOMETRY SOURCE"


def _bp_for_prompt():
    return {
        "visual": {"layout": "flat lay", "subject": "", "palette_mood": "warm",
                   "text_placement": "lower"},
        "background": {"surface": "marble", "colour": "white", "light": "soft light"},
        "production_style": {"style": "high_spec"},
    }


def test_build_image_prompt_keeps_geometry_and_identity_by_default_edit_mode():
    prompt = generate_image_prompt.build_image_prompt(
        _bp_for_prompt(), product=_PRODUCT, edit_mode=True, include_product=True)
    assert _GEOMETRY_MARKER in prompt
    assert _IDENTITY_MARKER in prompt
    assert _INTEGRATION_MARKER in prompt
    assert _GEOMETRY_SOURCE_MARKER in prompt


def test_build_image_prompt_suppresses_geometry_and_identity_when_compositing_edit_mode():
    prompt = generate_image_prompt.build_image_prompt(
        _bp_for_prompt(), product=_PRODUCT, edit_mode=True, include_product=True,
        suppress_bottle_identity=True,
    )
    assert _GEOMETRY_MARKER not in prompt
    assert _IDENTITY_MARKER not in prompt
    assert _GEOMETRY_SOURCE_MARKER not in prompt
    # UPDATED 2026-08-19 (double-bottle fix, ad 2767866756880226 confirmed live): the
    # integration clause is never SUPPRESSED (it still fires), but its WORDING
    # switches to the compositing-mode branch - the old "PARTICIPATING OBJECT...
    # held/applied/resting" wording asked Gemini to draw a bottle, which produced a
    # real second bottle behind the correctly-pasted cutout. The old marker must now
    # be ABSENT and the new one present - see test_bottle_integration_compositing.py
    # for the dedicated test suite this fix landed with.
    assert _INTEGRATION_MARKER not in prompt
    assert _INTEGRATION_COMPOSITING_MARKER in prompt


def test_build_image_prompt_suppresses_geometry_and_identity_when_compositing_writer_branch():
    prompt = generate_image_prompt.build_image_prompt(
        _bp_for_prompt(), product=_PRODUCT, include_product=True,
        creative_description="A styled flat-lay scene.",
        suppress_bottle_identity=True,
    )
    assert _GEOMETRY_MARKER not in prompt
    assert _IDENTITY_MARKER not in prompt
    # UPDATED 2026-08-19 (double-bottle fix) - see the edit_mode test above for why.
    assert _INTEGRATION_MARKER not in prompt
    assert _INTEGRATION_COMPOSITING_MARKER in prompt


def test_build_image_prompt_keeps_geometry_and_identity_writer_branch_by_default():
    prompt = generate_image_prompt.build_image_prompt(
        _bp_for_prompt(), product=_PRODUCT, include_product=True,
        creative_description="A styled flat-lay scene.",
    )
    assert _GEOMETRY_MARKER in prompt
    assert _IDENTITY_MARKER in prompt


def test_build_image_prompt_suppresses_geometry_and_identity_when_compositing_template_branch():
    prompt = generate_image_prompt.build_image_prompt(
        _bp_for_prompt(), product=_PRODUCT, include_product=True,
        suppress_bottle_identity=True,
    )
    assert _GEOMETRY_MARKER not in prompt
    assert _IDENTITY_MARKER not in prompt
    # UPDATED 2026-08-19 (double-bottle fix) - see the edit_mode test above for why.
    assert _INTEGRATION_MARKER not in prompt
    assert _INTEGRATION_COMPOSITING_MARKER in prompt


def test_build_image_prompt_keeps_geometry_and_identity_template_branch_by_default():
    prompt = generate_image_prompt.build_image_prompt(
        _bp_for_prompt(), product=_PRODUCT, include_product=True,
    )
    assert _GEOMETRY_MARKER in prompt
    assert _IDENTITY_MARKER in prompt


def test_build_image_prompt_suppression_is_a_no_op_when_no_product_included():
    # include_product=False already suppresses the whole bottle-clause block via
    # effective_include_product - suppress_bottle_identity must not raise or change
    # anything else about that existing behaviour.
    prompt_default = generate_image_prompt.build_image_prompt(
        _bp_for_prompt(), product=_PRODUCT, edit_mode=True, include_product=False,
    )
    prompt_suppressed = generate_image_prompt.build_image_prompt(
        _bp_for_prompt(), product=_PRODUCT, edit_mode=True, include_product=False,
        suppress_bottle_identity=True,
    )
    assert prompt_default == prompt_suppressed


# ---- _match_brightness_conservative / _draw_contact_shadow: minimal sanity (items 4-5) ----

def test_match_brightness_conservative_skips_when_already_close():
    scene = Image.new("RGB", (50, 50), (76, 76, 76))
    cutout = Image.new("RGBA", (10, 10), (76, 76, 76, 255))
    result = generate_image_prompt._match_brightness_conservative(cutout, scene)
    assert result.getpixel((0, 0)) == cutout.getpixel((0, 0))


def test_match_brightness_conservative_clamped_band():
    # A very bright scene against a very dark cutout - the correction must stay inside
    # the conservative [0.85, 1.15] clamp, never fully correcting to the scene's mean.
    scene = Image.new("RGB", (50, 50), (250, 250, 250))
    cutout = Image.new("RGBA", (10, 10), (10, 10, 10, 255))
    result = generate_image_prompt._match_brightness_conservative(cutout, scene)
    r, g, b, a = result.getpixel((0, 0))
    # 10 * 1.15 = 11.5 - even at the top of the clamp band, nowhere near 250.
    assert r <= 12 and g <= 12 and b <= 12
    assert a == 255


def test_draw_contact_shadow_darkens_pixels_below_the_paste_footprint():
    base = Image.new("RGBA", (100, 100), (200, 200, 200, 255))
    before = base.getpixel((50, 79))
    generate_image_prompt._draw_contact_shadow(base, paste_x=30, paste_y=20, cutout_w=40, cutout_h=60)
    after = base.getpixel((50, 79))
    assert after != before
    assert sum(after[:3]) < sum(before[:3])  # darker, never brighter
