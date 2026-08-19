"""Tests for src/asset_prep - the cutout edge-contamination fix
(decontaminate_cutout_edge, D1) and the baked-in shadow strip
(find_widest_row/strip_below_row, same day, second finding). Pure PIL, no
network/GCS - the real asset upload is a one-off script, not exercised here."""
from PIL import Image

from src.asset_prep import (
    decontaminate_cutout_edge, find_widest_row, strip_below_row,
)


def _solid_with_blended_ring(fg=(200, 100, 50), bg=(255, 255, 255), size=20, alpha_at_ring=128):
    """A small synthetic RGBA image: a fully-opaque fg-coloured core, surrounded by a
    ring of pixels that are a REAL alpha-blend of fg and bg at `alpha_at_ring` (i.e.
    exactly what a naive matting tool would produce), surrounded by fully-transparent
    bg-coloured pixels - the same three-zone shape the real cutout's edge has."""
    img = Image.new("RGBA", (size, size), (*bg, 0))
    px = img.load()
    af = alpha_at_ring / 255.0
    blended = tuple(round(fg[i] * af + bg[i] * (1 - af)) for i in range(3))
    for y in range(size):
        for x in range(size):
            if 4 <= x < size - 4 and 4 <= y < size - 4:
                px[x, y] = (*fg, 255)
            elif 2 <= x < size - 2 and 2 <= y < size - 2:
                px[x, y] = (*blended, alpha_at_ring)
            else:
                px[x, y] = (*bg, 0)
    return img, blended


def test_decontaminate_recovers_true_colour_at_partial_alpha():
    fg = (200, 100, 50)
    img, blended = _solid_with_blended_ring(fg=fg, alpha_at_ring=128)
    fixed = decontaminate_cutout_edge(img, bg=(255, 255, 255), erode_px=0)
    r, g, b, a = fixed.getpixel((2, 10))  # a ring pixel
    assert a == 128, "decontamination must never change the alpha value itself"
    # Recovered colour should land close to the TRUE fg colour, not the stored blend.
    for observed, true in zip((r, g, b), fg):
        assert abs(observed - true) <= 2, f"expected ~{true}, got {observed} (blended was {blended})"


def test_decontaminate_leaves_fully_opaque_pixels_unchanged():
    img, _ = _solid_with_blended_ring()
    fixed = decontaminate_cutout_edge(img, erode_px=0)
    assert fixed.getpixel((10, 10)) == img.getpixel((10, 10))


def test_decontaminate_leaves_fully_transparent_pixels_unchanged():
    img, _ = _solid_with_blended_ring()
    fixed = decontaminate_cutout_edge(img, erode_px=0)
    assert fixed.getpixel((0, 0)) == img.getpixel((0, 0))


def test_decontaminate_never_mutates_the_caller_supplied_image():
    img, _ = _solid_with_blended_ring()
    before = img.getpixel((2, 10))
    decontaminate_cutout_edge(img, erode_px=0)
    assert img.getpixel((2, 10)) == before


def test_erode_px_zero_skips_erosion_entirely():
    img, _ = _solid_with_blended_ring()
    fixed_no_erode = decontaminate_cutout_edge(img, erode_px=0)
    # Alpha channel shape (which pixels are >0) must be identical to the source's own
    # footprint - no shrinkage at all.
    for x in range(img.size[0]):
        for y in range(img.size[1]):
            assert (fixed_no_erode.getpixel((x, y))[3] > 0) == (img.getpixel((x, y))[3] > 0)


def test_erode_px_one_shrinks_the_opaque_footprint_by_one_pixel():
    size = 20
    img = Image.new("RGBA", (size, size), (255, 255, 255, 0))
    px = img.load()
    for y in range(5, 15):
        for x in range(5, 15):
            px[x, y] = (200, 100, 50, 255)
    fixed = decontaminate_cutout_edge(img, erode_px=1)
    # The outermost ring of the 10x10 opaque square (x=5 or x=14, y=5 or y=14) must now
    # be eroded to alpha=0; the interior (e.g. x=9,y=9) must remain fully opaque.
    assert fixed.getpixel((5, 9))[3] == 0
    assert fixed.getpixel((9, 9))[3] == 255


def test_erosion_does_not_alter_surviving_pixels_colour():
    size = 20
    img = Image.new("RGBA", (size, size), (255, 255, 255, 0))
    px = img.load()
    for y in range(5, 15):
        for x in range(5, 15):
            px[x, y] = (200, 100, 50, 255)
    fixed = decontaminate_cutout_edge(img, erode_px=1)
    r, g, b, a = fixed.getpixel((9, 9))
    assert (r, g, b) == (200, 100, 50)


# ---- find_widest_row / strip_below_row: the baked-in shadow strip ----

def _bottle_with_shadow_tail(width=20, height=40, base_row=25, shadow_rows=10):
    """A rectangle (the 'bottle', rows 0..base_row, constant width) followed by a
    triangular 'shadow' tail (rows base_row+1..base_row+shadow_rows) that gets
    STRICTLY NARROWER each row - the same shape the real cutout's own footprint
    profile showed (width peaks at the true base, then monotonically shrinks)."""
    size_w, size_h = width + 20, height
    img = Image.new("RGBA", (size_w, size_h), (255, 255, 255, 0))
    px = img.load()
    cx = size_w // 2
    for y in range(0, base_row + 1):
        for x in range(cx - width // 2, cx + width // 2):
            px[x, y] = (200, 100, 50, 255)
    for i, y in enumerate(range(base_row + 1, min(size_h, base_row + 1 + shadow_rows))):
        shadow_half = max(0, width // 2 - (i + 1) * 2)
        for x in range(cx - shadow_half, cx + shadow_half):
            px[x, y] = (120, 120, 120, 180)
    return img, base_row


def test_find_widest_row_returns_a_row_within_the_true_object_not_the_shadow():
    img, base_row = _bottle_with_shadow_tail()
    widest = find_widest_row(img)
    assert widest is not None
    assert widest <= base_row, (
        f"widest row {widest} fell inside the shadow tail (base was at {base_row}) - "
        f"the shadow must never be wider than the object's own true base"
    )


def _bottle_with_a_wider_feature_up_top(width=20, height=60, wide_feature_rows=(2, 6),
                                         wide_feature_extra=30, base_row=45, shadow_rows=10):
    """Reproduces the REAL bug found live against the actual cutout: an unscoped
    find_widest_row picked row 39 near the very TOP, because the pump's own
    horizontal lever spout is wider than the base. This fixture adds a second,
    wider-than-the-base feature near the top (like the lever) - a whole-image
    search must be fooled by it (proving the bug is real), while a y_start-scoped
    search of just the base region must not be."""
    img, real_base_row = _bottle_with_shadow_tail(
        width=width, height=height, base_row=base_row, shadow_rows=shadow_rows)
    px = img.load()
    cx = img.size[0] // 2
    for y in range(*wide_feature_rows):
        for x in range(cx - (width // 2 + wide_feature_extra), cx + (width // 2 + wide_feature_extra)):
            if 0 <= x < img.size[0]:
                px[x, y] = (10, 10, 10, 255)
    return img, real_base_row


def test_find_widest_row_unscoped_is_fooled_by_a_wider_feature_elsewhere():
    """Documents the real failure mode, not just the fix - an unscoped search over
    the WHOLE image finds the wider top feature, not the true base."""
    img, real_base_row = _bottle_with_a_wider_feature_up_top()
    widest = find_widest_row(img)
    assert widest != real_base_row
    assert widest < real_base_row


def test_find_widest_row_scoped_to_the_base_region_ignores_the_wider_top_feature():
    img, real_base_row = _bottle_with_a_wider_feature_up_top()
    widest = find_widest_row(img, y_start=real_base_row - 10)
    assert widest is not None
    assert widest <= real_base_row


def test_find_widest_row_none_when_image_is_fully_transparent():
    img = Image.new("RGBA", (10, 10), (255, 255, 255, 0))
    assert find_widest_row(img) is None


def test_strip_below_row_removes_the_shadow_tail_entirely():
    img, base_row = _bottle_with_shadow_tail()
    stripped = strip_below_row(img, base_row)
    w, h = img.size
    for y in range(base_row + 1, h):
        for x in range(w):
            assert stripped.getpixel((x, y))[3] == 0, f"pixel ({x},{y}) still has alpha after stripping"


def test_strip_below_row_never_touches_pixels_at_or_above_cutoff():
    img, base_row = _bottle_with_shadow_tail()
    stripped = strip_below_row(img, base_row)
    for y in range(0, base_row + 1):
        for x in range(img.size[0]):
            assert stripped.getpixel((x, y)) == img.getpixel((x, y))


def test_strip_below_row_preserves_canvas_dimensions():
    img, base_row = _bottle_with_shadow_tail()
    stripped = strip_below_row(img, base_row)
    assert stripped.size == img.size


def test_strip_below_row_never_mutates_the_caller_supplied_image():
    img, base_row = _bottle_with_shadow_tail()
    before = img.getpixel((img.size[0] // 2, base_row + 3))
    strip_below_row(img, base_row)
    assert img.getpixel((img.size[0] // 2, base_row + 3)) == before


def test_strip_below_row_respects_margin():
    img, base_row = _bottle_with_shadow_tail()
    stripped = strip_below_row(img, base_row, margin=2)
    # rows base_row+1 and base_row+2 (within the margin) must survive unstripped
    cx = img.size[0] // 2
    assert stripped.getpixel((cx, base_row + 1))[3] > 0
    assert stripped.getpixel((cx, base_row + 2))[3] > 0
    # something further out must still be stripped
    assert stripped.getpixel((cx, base_row + 6))[3] == 0
