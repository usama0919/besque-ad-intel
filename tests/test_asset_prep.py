"""Tests for src/asset_prep.decontaminate_cutout_edge (D1, 2026-08-19) - the cutout
edge-contamination fix. Pure PIL, no network/GCS - the real asset upload is a
one-off script, not exercised here."""
from PIL import Image

from src.asset_prep import decontaminate_cutout_edge


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
