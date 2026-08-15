"""Bottle-realism-only targeted edit control (2026-08-16).

ONE pre-authored delta sentence per realism value, sent to Gemini verbatim alongside
the v1 draft image - never assembled at request time from blueprint/field text. Every
other targeted edit in this codebase (generate_image_prompt.build_targeted_edit_
instruction) builds its instruction dynamically from the descriptor + blueprint; this
control deliberately does not, because the whole point is a fixed, reviewed sentence
with no live-field surface for brand/identity text to leak through - the same
"structural fix, not another prompt clause" lesson CLAUDE.md's guardrails note already
draws for other leaks in this codebase.

Four values only - ugc_native, high_spec, hybrid, illustrated - a fixed picker set for
this control specifically. Deliberately independent of whatever
schema/blueprint.schema.json's production_style.style enum currently allows (as of
2026-08-16 that enum is the tighter ["ugc", "high_spec", "illustrated"], with older
artifact rows still carrying "ugc_native"/"high_spec_studio"/"hybrid" from before the
2026-08-11 rename) - a stored value that doesn't match one of these four exactly is the
edit_capability/modal's job to surface as a "current: <value>" chip, never this module's
job to normalise or guess.

Each delta, by construction:
- names the render register for the BOTTLE ONLY, never the whole scene
- states the label's content/wording/icons/proportions/position are unchanged
- states everything else in the image is unchanged
- never restates label colour, typeface, or wordmark text - bottle identity is
  products.visual_description's job; a delta only ever changes HOW the bottle is
  rendered, never confirms or re-describes WHAT it is.
"""

REALISM_VALUES = ("ugc_native", "high_spec", "hybrid", "illustrated")

# Shared tail every delta ends with - the two required "unchanged" clauses, worded
# identically across all four so no value gets a weaker guarantee than another.
_UNCHANGED_CLAUSE = (
    "The label's content, wording, icons, proportions, and position on the bottle stay "
    "exactly as they already appear - completely unchanged. Every other element in the "
    "image - layout, every other object, all text, colours, background, and lighting - "
    "stays exactly as it already appears, completely unchanged."
)

REALISM_DELTAS = {
    "ugc_native": (
        "Re-render the product bottle only, and nothing else, in an unpolished, "
        "phone-camera-realistic photographic register: natural imperfections, casual "
        "handheld framing, available light, and authentic amateur photo quality - never "
        "a flat illustration and never a polished studio photograph. "
        + _UNCHANGED_CLAUSE
    ),
    "high_spec": (
        "Re-render the product bottle only, and nothing else, in a polished, "
        "professionally lit studio-photograph register: crisp real-world detail, clean "
        "specular highlights, and refined studio lighting - never a flat illustration "
        "and never a casual amateur snapshot. "
        + _UNCHANGED_CLAUSE
    ),
    "hybrid": (
        "Re-render the product bottle only, and nothing else, in a hybrid register: a "
        "photographically rendered bottle that sits naturally within illustrated or "
        "graphic surrounding artwork, blending real photographic detail with the "
        "scene's own graphic treatment - never a fully flat illustration and never a "
        "fully isolated studio photograph. "
        + _UNCHANGED_CLAUSE
    ),
    "illustrated": (
        "Re-render the product bottle only, and nothing else, in a flat illustrated "
        "register: hand-drawn or vector artwork matching this scene's own line weight "
        "and shading - never a photograph and never a photorealistic render composited "
        "into the drawing. "
        + _UNCHANGED_CLAUSE
    ),
}


def get_delta(value):
    """The pre-authored delta sentence for `value`, or None if `value` isn't exactly
    one of REALISM_VALUES. Callers must fail closed on None - never fall back to a
    guessed, assembled, or default instruction."""
    return REALISM_DELTAS.get((value or "").strip())
