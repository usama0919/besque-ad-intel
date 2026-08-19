"""One-time asset preparation tooling (2026-08-19, D1) - NOT part of the live
generation path. Nothing in pipeline.py/generate_image_prompt.py imports this module
at runtime; it exists so the fix applied to product_assets/besque_magic_body_oil_
cutout.png is a real, testable function rather than throwaway arithmetic in a script,
and so a future re-export of the same asset (or a different product's cutout) can be
put through the identical, verified transform again.

The defect (found via direct pixel inspection, not assumed): at the cutout's edges,
RGB channels carry white-background bleed even at HIGH alpha (e.g. (230,230,230,220),
(199,199,199,255)) - true amber only appears a few pixels further in. This is a
non-premultiplied-alpha background-removal artifact: partially-transparent edge
pixels store a blend of the true foreground colour and the (white) matte background,
proportional to how transparent they were, rather than pure foreground colour with
alpha alone carrying transparency. composite_product's own paste (generate_image_
prompt.py) uses the cutout's alpha as its own mask - it faithfully reproduces
whatever colour is stored there, so a contaminated edge composites as a visible
light/white matte line no matter how carefully the paste itself is done.

find_widest_row/strip_below_row (2026-08-19, second finding the same day): what
looked like more edge contamination near the cutout's base turned out, on direct
visual inspection (rendered against white AND black backgrounds - it showed as the
same grey smudge against both, proof it's real semi-transparent content, not
background bleed), to be a SEPARATE baked-in drop-shadow/reflection graphic riding
along with the bottle. generate_image_prompt.composite_product already draws its own
scene-matched contact shadow (_draw_contact_shadow) immediately before pasting - a
second, fixed shadow baked into the asset itself can never match an arbitrary
generated scene's own light direction, and has been layering under every composite
regardless. find_widest_row locates the bottle's true base by a purely geometric
signal (the row with the widest opaque footprint - the point where the object
actually contacts its surface; a shadow/reflection beneath that can only ever be
narrower going further down, never wider, so the widest row cannot itself be part of
a shadow). strip_below_row then zeroes alpha for every row past that point, WITHOUT
cropping the canvas - the bottle's own silhouette above and at the cutoff is never
touched, so its proportions (and the 4.33 constant _bottle_geometry_clause states,
derived from this same asset) are unaffected by construction, not just by
intention."""
from PIL import Image, ImageFilter


def decontaminate_cutout_edge(img, bg=(255, 255, 255), erode_px=1):
    """Two steps, applied in order:

    1. Colour decontamination: every pixel with 0 < alpha < 255 has its background
       contribution removed via the standard formula
           true_c = (observed_c - (1 - alpha/255) * bg_c) / (alpha/255)
       clamped to [0, 255] per channel. Fully transparent (alpha==0) and fully
       opaque (alpha==255) pixels are left untouched - there is nothing to remove at
       alpha==0 (the pixel is discarded at composite time regardless) and no
       background contribution to remove at alpha==255 (the formula's own (1-af)
       term is already zero there, so leaving these pixels alone changes nothing;
       it's stated as a separate branch only to avoid a division that would just
       return the input unchanged).

    2. `erode_px` rounds of `ImageFilter.MinFilter(3)` applied to the alpha channel
       ONLY (colour channels untouched) - each round shrinks the opaque region
       inward by one pixel, cutting off the residual fringe ring where alpha is
       high enough (e.g. 220-252) that step 1's correction is small (the (1-af)
       term is itself small there) even though the stored colour is still visibly
       off. Combined with step 1 rather than instead of it: step 1 fixes the
       correctable blended-edge pixels, step 2 discards the few that aren't fully
       correctable this way, rather than leaving them in at a slightly-wrong colour.

    Returns a NEW image; never mutates the caller's own image (same discipline as
    generate_image_prompt._match_brightness_conservative)."""
    img = img.convert("RGBA")
    w, h = img.size
    src_px = img.load()
    out = Image.new("RGBA", (w, h))
    dst_px = out.load()
    bg_r, bg_g, bg_b = bg

    for y in range(h):
        for x in range(w):
            r, g, b, a = src_px[x, y]
            if 0 < a < 255:
                af = a / 255.0
                nr = (r - (1 - af) * bg_r) / af
                ng = (g - (1 - af) * bg_g) / af
                nb = (b - (1 - af) * bg_b) / af
                dst_px[x, y] = (
                    max(0, min(255, round(nr))),
                    max(0, min(255, round(ng))),
                    max(0, min(255, round(nb))),
                    a,
                )
            else:
                dst_px[x, y] = (r, g, b, a)

    if erode_px:
        alpha_channel = out.split()[3]
        for _ in range(erode_px):
            alpha_channel = alpha_channel.filter(ImageFilter.MinFilter(3))
        out.putalpha(alpha_channel)

    return out


def find_widest_row(img, alpha_threshold=30, y_start=0, y_end=None):
    """Return the y-coordinate of the row with the widest opaque-ish footprint
    (alpha > alpha_threshold) WITHIN [y_start, y_end), or None if no pixel in that
    range exceeds the threshold.

    The geometric argument ("going further down from the point of contact, only a
    shadow/reflection can exist, and it can only get NARROWER moving away from the
    object, never wider") is only valid LOCALLY, within the base/shadow region - NOT
    as a whole-image search. Confirmed live on the real cutout: an unscoped search
    found row 39 (near the very TOP) as the global widest row, because the pump's
    own horizontal lever spout (_bottle_geometry_clause: "overhanging the body's
    left edge by 0.38 body-widths") is wider than the base - a real second
    wide-and-then-narrowing feature the whole-image assumption doesn't account for.
    The caller must scope y_start/y_end to the region where the base/shadow
    transition is actually expected (e.g. the bottom fraction of the image) - this
    function does not guess that region itself.

    alpha_threshold=30 (not 0) so a few stray barely-transparent pixels can't shift
    the result - the same kind of deliberate non-zero threshold this codebase
    already uses elsewhere for a similar reason (e.g. _bboxes_overlap requires a
    strictly positive area, not merely non-zero)."""
    img = img.convert("RGBA")
    w, h = img.size
    if y_end is None:
        y_end = h
    px = img.load()
    best_y, best_width = None, -1
    for y in range(max(0, y_start), min(h, y_end)):
        min_x = max_x = None
        for x in range(w):
            if px[x, y][3] > alpha_threshold:
                if min_x is None:
                    min_x = x
                max_x = x
        if min_x is not None:
            width = max_x - min_x
            if width > best_width:
                best_width = width
                best_y = y
    return best_y


def strip_below_row(img, cutoff_y, margin=0):
    """Zero the alpha channel for every row strictly below `cutoff_y + margin` -
    removes baked-in content beneath the object's true base (a shadow/reflection)
    WITHOUT cropping the canvas, so image dimensions are unchanged and every pixel
    at or above the cutoff - the object's own silhouette - is untouched byte-for-
    byte. Colour channels below the cutoff are left as-is (irrelevant once alpha is
    0; composite_product's paste already ignores fully-transparent pixels via its
    own alpha-as-mask paste). Returns a new image; never mutates the caller's own."""
    img = img.convert("RGBA")
    w, h = img.size
    out = img.copy()
    px = out.load()
    start = cutoff_y + margin + 1
    for y in range(max(0, start), h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a != 0:
                px[x, y] = (r, g, b, 0)
    return out
