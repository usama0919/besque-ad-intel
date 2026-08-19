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
light/white matte line no matter how carefully the paste itself is done."""
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
