"""Deconstruction step: send an ad image to Claude and get a structured blueprint."""
import os
import json
import base64
import logging
import re
import time
from pathlib import Path

from src import json_response, validator

# Model + key are read from env so the real key plugs in at kickoff.
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")

log = logging.getLogger("deconstruct")

BLUEPRINT_PROMPT = """You are an expert ad analyst. Analyse the attached advertising image and return a JSON creative blueprint. Return ONLY valid JSON, no preamble or markdown.

Scraped ad copy, if supplied as a separate text block, is the source of truth for the headline and offer where it conflicts with what is legible in the image.

The JSON must have exactly these fields:
- ad_id (string): use the value "{ad_id}"
- source_page (string): use the value "{source_page}"
- captured_at (string): use the value "{captured_at}"
- format (string): one of testimonial_card, product_hero, editorial, offer_led, or another short descriptor
- hook (object): {{ "type": one of question/bold_claim/problem_agitate/social_proof/other, "headline_structure": short description }}
- angle (string): the core persuasive angle
- awareness_stage (string): one of unaware, problem, solution, product, most_aware
- claims (array): any of efficacy, sensory, ingredient, social_proof, offer
- visual (object): {{ "layout": ..., "subject": ..., "palette_mood": ..., "text_placement": ... }}
- background (object): REQUIRED - this key must ALWAYS be present. {{ "surface": what the scene is set on or against, e.g. "marble countertop", "sandy beach", "plain white studio backdrop", "colour": the background's OWN dominant colour, distinct from any object's colour, "light": one short phrase describing the scene's overall lighting character, e.g. "soft warm light from upper-left" or "flat even studio light, no visible shadow direction" }}. This is the ENVIRONMENT only - surface, colour, and light. Nothing with its own identity belongs here: a product, a person, a prop, a piece of text, a logo - ANYTHING a viewer could point at and name as a distinct thing - is an OBJECT (see `objects` below), never folded into this field's prose. If you find yourself describing what something IS rather than what the space around it looks like, it belongs in `objects`, not here.
- cta (string): the call to action
- destination_url (string): use the value "{destination_url}"
- headline_verbatim (string): the exact main headline text in the image, or "" if none
- offer (object): {{ "type": ..., "value": ..., "mechanic": ... }} or null if no offer
- social_proof (object): {{ "type": ..., "owner": ... }} — owner is the brand/body the proof belongs to, or null
- layout_detail (object): {{ "text_zone": ..., "product_count": number, "zone_positions": array of short phrases locating each element top to bottom (e.g. ["headline top-center", "product mid-frame", "CTA bottom-full-width"]), "has_bottom_banner": true/false, "has_corner_badge": true/false, "frame_division": short description of how the frame splits (e.g. "three stacked horizontal bands" or "single uninterrupted gradient ground, no hard divisions") }}
- legibility_notes (string): whether in-image text is readable at feed size
- body_area_shown (string): REQUIRED - this key must ALWAYS be present in your JSON output. if a human subject appears in the image, name the specific body region shown or emphasised (e.g. "legs", "arms", "torso", "hands", "neck and décolletage"); if NO human subject appears at all (e.g. a product-only shot, an illustration/diagram, or text-only creative), use exactly "none". This is read downstream to decide whether a per-run body-area instruction may be applied to this reference at all - do not guess a body part onto a productless or human-less image.
- face_present (object): REQUIRED - this key must ALWAYS be present. {{ "has_face": true/false, "prominence": one of primary/incidental/none, "location": free-text description of where in the frame the face sits (e.g. "centre-frame, close-up, looking directly at camera") }}. has_face is false and prominence is exactly "none" when no face is visible anywhere in the image - location is "" in that case, never a guess at where a face would be. prominence "primary" means the face is the compositional focus (e.g. a close-up beauty shot); "incidental" means a person is visible but the face is not what the ad is about (e.g. a wide lifestyle shot, a person shown from behind or cropped above the shoulders, a hands-only shot with a face barely visible in the background).
- creative_objective (string): the ad's primary strategic goal in one short phrase, e.g. "drive urgency around a limited-time offer" or "build trust via a testimonial"
- target_audience (string): who this ad is speaking to, in one short phrase, e.g. "women 40+ concerned about skin texture and firmness"
- typography (object): {{ "headline_face": typeface style e.g. serif/sans/script, "headline_weight": e.g. bold/light/regular, "hierarchy_levels": array of short phrases describing each distinct text tier top to bottom (e.g. ["large bold serif headline", "medium sans subhead", "small CTA button label"]), "case_treatment": e.g. "all caps headline, sentence case body" }} - general prose about the ad's typography; per-zone text detail belongs in `objects` (kind "text") below, not here.
- objects (array): REQUIRED - this key must ALWAYS be present, and must NEVER be empty - every ad has at least one object (at minimum, the product or the headline). ONE ENTRY PER VISUALLY DISTINCT THING in the reference ad - every product, every person, every discrete text block (headline, sub-line, body copy, CTA button, badge, price, disclaimer, testimonial quote - each is its OWN entry, never bundled), every logo/wordmark, every prop, every surface the product or a person interacts with directly (not the general background - see `background` above for the environment itself), and every other graphic element (an icon, a badge shape, a decorative flourish). MULTIPLE INSTANCES OF THE SAME PRODUCT ARE SEPARATE ROWS, each with its own object_id - two bottles in frame is two entries, not one entry noting a count. Anything with an identity - anything a viewer could point at and name - is an object row; it is NEVER acceptable to fold an identifiable thing into `background` prose instead of listing it here. An incomplete inventory here means that object gets silently reproduced unchanged (if omitted) or invented from nothing (if the model believes the scene is otherwise empty) when this reference is cloned - list everything visible, not just the obviously important pieces. Each entry:
    {{
      "object_id": stable string identifier, "obj_01", "obj_02", ... "obj_NN", assigned in the order you list them, never reused within this blueprint,
      "kind": one of product/person/text/logo/prop/surface/graphic - product = a sellable item in its packaging; person = a human figure (the whole figure, not per-body-part); text = one discrete text block; logo = a brand mark/wordmark distinct from a product's own printed label; prop = a physical object that isn't the product, a surface, or a person; surface = a distinct physical surface the product/a person rests on or touches (a tray, a towel, a countertop the bottle sits on specifically - not the general environment); graphic = a non-text graphic element (an icon, a badge shape, a decorative flourish),
      "description": what it plainly is, e.g. "amber glass body-oil bottle with pump", "customer testimonial quote with 5-star rating", "brand wordmark top-left" - structure and content only, describe what it IS, not its colour (colour goes in `colours` below),
      "bbox": [x, y, w, h], the object's bounding box as fractions of the FULL IMAGE (0.0 to 1.0), x/y measured from the top-left corner, w/h as fractions of image width/height - your best estimate of where this object sits and how much of the frame it occupies,
      "colours": array of this object's OWN colours only, e.g. ["amber", "gold"] - never the scene's overall palette (that's `visual.palette_mood`) and never another object's colour,
      "ownership": one of competitor_branded/generic/besque/person - competitor_branded = this specific item visibly belongs to or names the advertiser/competitor (their product, their logo, their packaging, their own on-image copy); generic = register-neutral, could belong to any brand's ad (a hand, a towel, a plain surface, unbranded scenery); besque = do not use this value at deconstruct time, it does not apply to a competitor reference; person = a human figure, handled by its own substitution rules rather than the brand-ownership question,
      "role": one of hero/secondary/supporting_prop/environment - hero = the main subject the ad is built around; secondary = a supporting element with real presence (a sub-line, a second product); supporting_prop = minor set-dressing; environment = part of the setting rather than a foregrounded thing,
      "carries_brand_mark": true/false - true if this object itself visibly shows a reproducible brand mark, logo, or wordmark (even if its `kind` isn't "logo" - e.g. a product bottle with the competitor's name printed on its own label carries a brand mark too), false otherwise,
      "persuasive_function": what this object exists to DO in the ad's argument, in one short phrase, e.g. "the hero product being sold", "proves social validation via a real customer's words", "names the advertiser",
      "disposition": one of substitute/keep/drop - your best judgement of whether this object should be replaced with a Besque equivalent, kept as-is, or removed entirely when this reference is cloned for Besque. This is a STARTING POINT only - a separate mechanical check overrides this for any competitor-owned or brand-marked object regardless of what you choose here, so judge honestly rather than trying to predict the override.
      "text_purpose": REQUIRED when kind is "text", omit entirely for every other kind. One of headline/subtext/cta/offer/certification/testimonial/price_anchor/award/disclaimer/product_callout/other - the JOB this specific text block does, not its wording:
        headline = the ad's main hook/attention line.
        subtext = a supporting line or body copy beneath the headline.
        cta = a call-to-action button label or link text.
        offer = a discount, promo code, scarcity/stock-count claim, or urgency wording (e.g. "20% OFF", "Only 100 left").
        certification = a badge or line naming a certification/standard the product holds (e.g. "Vegan", "Cruelty Free", "Dermatologist Tested").
        testimonial = a customer quote or review, with or without a star rating or name.
        price_anchor = a shown price, or a was/now price comparison.
        award = an award, "as seen in" press mention, editorial accolade, or third-party endorsement line (e.g. "by THE BODY FIRM").
        disclaimer = legal, regulatory, or medical fine print, or an asterisked footnote.
        product_callout = a short benefit or property label pointing at the product, distinct from the headline (e.g. an icon + "Fast-Absorbing").
        other = any other discrete text block that genuinely fits none of the above - use sparingly, only when no other value honestly applies.
    }}
- semantic_split (object): REQUIRED - this key must ALWAYS be present. {{ "is_split": true/false, "split_axis": "vertical" or "horizontal" or null, "left_or_before": free text describing what that side/panel depicts, "right_or_after": free text describing what the other side/panel depicts }}. is_split is true whenever the image is visually divided into two comparable panels or halves - a before/after, a side-by-side comparison, a split-screen. When is_split is false, split_axis is null and both left_or_before and right_or_after are "". For a genuine before/after ad, the two sides MUST be described as materially DIFFERENT states (e.g. left_or_before: "dry, crepey skin with visible fine lines"; right_or_after: "smooth, hydrated skin with visible firmness") - recording both sides as showing the same condition is a failure, since the contrast between them is the entire point of the format.
- production_style (object): REQUIRED - this key must ALWAYS be present. {{ "style": one of {production_style_options}, "confidence": high/medium/low, "signals": array of short phrases justifying the choice }}
    ugc = handheld or phone-camera framing, uncontrolled or available lighting (window light, room light, natural daylight - never a lighting rig), imperfect composition (off-centre, tilted, awkwardly cropped), a domestic or non-studio setting (bathroom, bedroom, kitchen, car, outdoors), visible grain or motion blur, a selfie or arm's-length POV. These are OBSERVABLE SIGNALS in the pixels, not a vibe - two or more present means ugc.
    high_spec = controlled premium lighting, deliberate composition, macro texture, editorial typography, a studio or professionally art-directed setting. A polished studio look is NOT the default answer: default to ugc unless the image actually shows deliberate studio lighting/composition/setting. Misclassifying a genuinely UGC reference as high_spec is a KNOWN FAILURE - check for the ugc signals above FIRST, and only choose high_spec when they are absent AND real studio signals are present instead.
    illustrated = not a photograph at all - a whiteboard-style diagram, 3D render, or comic-strip/illustrated panel. Choose this when the ad is drawn or rendered rather than shot.
- creative_format (string): exactly one of testimonial_review, before_after, problem_solution, product_hero, offer_led, comparison, listicle_tips, founder_story, ingredient_focus, lifestyle_scene, text_led_editorial
    (production_style and creative_format are two independent axes — a testimonial can be UGC or studio.)
- product_category (object): {{ "category": one of body_oil/face_oil/serum/moisturiser/cleanser/haircare/supplement/firming/other/not_product, "confidence": high/medium/low, "signals": array of short phrases justifying the choice }}
    body_oil = oil intended for the body rather than the face
    face_oil = facial oil or facial treatment oil
    serum = lightweight concentrated leave-on treatment, usually water- or gel-based
    moisturiser = cream, lotion or balm whose main job is hydrating or sealing
    cleanser = wash-off face or body cleanser — gel, balm, foam or micellar
    haircare = shampoo, conditioner, scalp or hair treatment
    supplement = ingestible — capsule, powder, gummy or drink
    firming = sold primarily on tightening, lifting or firming skin
    other = a real product is being sold but none of the above categories fit
    not_product = the ad sells no product at all: tester or ambassador recruitment, brand/founder story with nothing to buy, or a store-wide sale naming no single product
    (other vs not_product: use other when there IS a product and no category fits; use not_product when nothing is being sold. Never use other as a substitute for not_product.)
    If the ad depicts medical, clinical, intimate-health, or anatomically explicit subject matter (e.g. an anatomical diagram, a medical or surgical treatment, an intimate-health condition, a before/after of a medical procedure) — classify as not_product (or other, if a real product is genuinely being sold alongside it) AND name the specific medical/clinical/anatomical nature explicitly and plainly in signals (e.g. "anatomical diagram of digestive tract", "hemorrhoid treatment demonstration", "intimate-health condition"), in addition to whatever other signals justify the category choice. This is read downstream to hard-block cloning such a reference — do not soften or omit it.
    confidence is one of high/medium/low — never a number.
    (product_category is a third independent axis, unrelated to production_style and creative_format.)
"""


def build_prompt(ad_id, source_page, captured_at, destination_url=""):
    return BLUEPRINT_PROMPT.format(
        ad_id=ad_id,
        source_page=source_page,
        captured_at=captured_at,
        destination_url=destination_url,
        # Read from the schema via validator.production_styles() rather than repeating
        # the enum as a literal here, so this prompt can't drift from what
        # validator.is_valid() actually accepts.
        production_style_options="/".join(validator.production_styles()),
    )


def parse_blueprint(raw_text: str) -> dict:
    """Parse Claude's text response into a blueprint dict. Tolerates markdown fences
    and surrounding prose - see json_response.extract_json."""
    return json_response.extract_json(raw_text)


class BlueprintValidationError(ValueError):
    """Raised by deconstruct_from_response when the parsed blueprint fails schema
    validation. Carries the raw validator message (validation_error) separately from
    the ValueError text so deconstruct_image's retry loop can quote the specific problem
    back to Claude as a correction instruction, distinct from the generic JSON-escaping
    nudge used for an unparseable response."""

    def __init__(self, message, validation_error):
        super().__init__(message)
        self.validation_error = validation_error


# BOTTLE SHAPE LANGUAGE FILTER (2026-08-16): the Besque bottle's geometry is now a single
# fixed, hardcoded fact (generate_image_prompt._bottle_geometry_clause) - no blueprint
# field may ever be allowed to compete with it by describing a product's shape or
# proportions, since blueprint describes the COMPETITOR reference ad and its own product
# geometry has repeatedly leaked into the SUBSTITUTED Besque bottle (see CLAUDE.md's
# 2026-08-15 _bottle_geometry_source_clause note: "the rendered bottle changed silhouette,
# height, width, and proportions between ads - tracking each reference ad's OWN product
# geometry instead of Besque's"). Structural fix, not a prompt clause - the standing
# lesson this whole codebase keeps re-learning: strip the language at its SOURCE (here,
# once, at deconstruct time) rather than ask every downstream prompt-assembly site not to
# use it.
#
# Scoped to exactly the three blueprint fields TRACED to actually reach assembled prompt
# text as free text (not every field that merely exists):
#   - product_category.signals[] - previously quoted VERBATIM into
#     generate_image_prompt._competitor_props_clause's removal instruction when a signal
#     also matched a PROP_KEYWORD; that clause was deleted 2026-08-17 (folded into
#     resolve_disposition/_is_competitor_argument_prop below, which matches against an
#     object's own description/persuasive_function instead) - this field no longer
#     reaches assembled prompt text at all, but the filter stays: content_safety.py's
#     hard-block check and the dashboard's blueprint display both still read it, and
#     bottle-shape language has no legitimate reason to survive in either.
#   - visual.subject - no longer read anywhere in prompt construction at all (its one
#     former reader, _competitor_props_clause, is deleted); kept filtered for the same
#     display-surface reason as product_category.signals above.
#   - layout_detail.zone_positions[] - folded into _scene_composition_facts' "OBSERVED
#     SCENE COMPOSITION" sentence (product ADD placement) and drift_check's product zone
#     bbox - both meant to be POSITION-only, per deconstruct's own field description
#     ("short phrases locating each element"), never a shape word riding along.
# scene_elements[].element/.role is deliberately NOT filtered here: that field's own
# classifier instruction already excludes the product entirely ("every element OTHER
# THAN the product"), and its usual content (props, surfaces, background objects) can
# legitimately contain words like "round" or "curved" with nothing to do with bottle
# geometry - blanket-filtering it would discard real prop detail for no traced benefit.
#
# Deliberately narrow, unambiguous geometry vocabulary - NOT generic adjectives like
# "tall"/"wide"/"narrow" that legitimately describe camera framing or a person in
# visual.subject ("a tall woman", "a wide shot") and would false-positive constantly.
# Every term here is a bottle/container-anatomy or dimension word with no ordinary
# non-geometry reading in this context.
_BOTTLE_SHAPE_KEYWORDS = (
    "silhouette", "cylindrical", "cylinder", "cylinders", "tapered", "taper",
    "hourglass", "teardrop", "bulbous", "straight-sided", "straight sided",
    "collar", "pump", "neck", "shoulder", "proportion", "proportions",
    "dimension", "dimensions", "height-to-width", "body-width", "body width",
)
_BOTTLE_SHAPE_PATTERN = re.compile(
    r"\b(?:" + "|".join(re.escape(kw) for kw in _BOTTLE_SHAPE_KEYWORDS) + r")\b",
    re.IGNORECASE,
)

# The exact field paths this filter touches - named once here so deconstruct_image's
# caller (and anyone auditing this) can report/log precisely what was filtered, rather
# than a vague "some fields."
BOTTLE_SHAPE_FILTERED_FIELDS = (
    "product_category.signals[]", "visual.subject", "layout_detail.zone_positions[]",
)


def _contains_bottle_shape_language(text):
    return bool(_BOTTLE_SHAPE_PATTERN.search(text or ""))


def strip_bottle_shape_language(blueprint):
    """Drops (never edits-in-place-and-mangles) any value in the three fields named by
    BOTTLE_SHAPE_FILTERED_FIELDS that contains bottle/container geometry language - a
    list entry is dropped whole (never partially word-scrubbed, which risks leaving a
    grammatically broken fragment); visual.subject is blanked to "" (still schema-valid -
    a required string, not a required NON-EMPTY string) rather than the key removed.
    Returns (blueprint, filtered) where filtered is a dict {field_path: [dropped
    values]} - empty dict when nothing matched, so a caller can log exactly what
    happened without guessing. Operates on a shallow copy of the blueprint and its
    mutated sub-dicts/lists only - the caller's original object is never mutated."""
    blueprint = dict(blueprint or {})
    filtered = {}

    product_category = dict(blueprint.get("product_category") or {})
    signals = list(product_category.get("signals") or [])
    kept_signals = [s for s in signals if not _contains_bottle_shape_language(s)]
    dropped_signals = [s for s in signals if _contains_bottle_shape_language(s)]
    if dropped_signals:
        product_category["signals"] = kept_signals
        blueprint["product_category"] = product_category
        filtered["product_category.signals[]"] = dropped_signals

    visual = dict(blueprint.get("visual") or {})
    subject = visual.get("subject") or ""
    if _contains_bottle_shape_language(subject):
        visual["subject"] = ""
        blueprint["visual"] = visual
        filtered["visual.subject"] = [subject]

    layout_detail = dict(blueprint.get("layout_detail") or {})
    zone_positions = list(layout_detail.get("zone_positions") or [])
    kept_zones = [z for z in zone_positions if not _contains_bottle_shape_language(z)]
    dropped_zones = [z for z in zone_positions if _contains_bottle_shape_language(z)]
    if dropped_zones:
        layout_detail["zone_positions"] = kept_zones
        blueprint["layout_detail"] = layout_detail
        filtered["layout_detail.zone_positions[]"] = dropped_zones

    return blueprint, filtered


# COMPETITOR ARGUMENT PROPS (2026-08-17, folded in from the now-deleted
# generate_image_prompt._competitor_props_clause): a prop tied to the COMPETITOR's own
# product category (an applicator diagram, an anatomical inset, a device illustration)
# has no Besque equivalent regardless of how the model scored its `ownership` - a plain
# roller or dropper tool reads as "generic" ownership (it isn't itself branded) but
# still only exists to make the competitor's own argument, so it must never survive as
# a "kept" prop. This used to be a SEPARATE clause in generate_image_prompt.py, matched
# against product_category.signals/visual.subject with no coordination against this
# function's own ownership-based override - two independent systems could disagree
# about the same prop's fate. Folded into resolve_disposition instead, matched against
# the object's own description/persuasive_function, so there is exactly one mechanism
# deciding every object's fate, never two.
_PROP_KEYWORDS = ("diagram", "illustration", "device", "applicator", "inset",
                   "anatomical", "prop stand", "wand", "roller", "dropper tool")


def _is_competitor_argument_prop(obj):
    if obj.get("kind") != "prop":
        return False
    text = f"{obj.get('description') or ''} {obj.get('persuasive_function') or ''}".lower()
    return any(kw in text for kw in _PROP_KEYWORDS)


# TEXT_PURPOSE DISPOSITION MAP (2026-08-17): mirrors the per-zone-type substitution
# rules the deleted generate_image_prompt._structural_zones_clause used to encode (see
# git history immediately before the objects-array refactor) - restored here as the
# mechanical disposition decision, one purpose at a time. Every value here is either
# unconditional (award/disclaimer always drop; product_callout/headline/subtext/cta
# always substitute - Besque always has a name/generated-copy line to put there) or
# gated on a real, supplied Besque value in `context` (offer/price_anchor/
# certification/testimonial: substitute only when the run actually authorised one,
# drop otherwise - never left for Gemini to invent a number, cert, or quote it wasn't
# given).
_TEXT_PURPOSE_ALWAYS_DROP = {"award", "disclaimer"}
_TEXT_PURPOSE_ALWAYS_SUBSTITUTE = {"headline", "subtext", "cta", "product_callout"}
_TEXT_PURPOSE_CONTEXT_GATED = {
    "offer": "offer_text", "price_anchor": "offer_text",
    "certification": "certifications", "testimonial": "testimonial",
}


def _resolve_text_disposition(obj, context, is_branded):
    """The text_purpose-driven half of resolve_disposition, below - kept as its own
    function so the branded/non-branded call sites can both reach it without
    duplicating the purpose map. `is_branded` is passed in (never recomputed here) so
    this can never drift from the same ownership/carries_brand_mark check every other
    kind already uses.

    Missing/unrecognised text_purpose (a legacy object predating this field, or a
    genuinely malformed one) falls back to the object's own model-assigned disposition,
    same as any other kind - back-compat for the ~300 existing rows this schema
    addition does not retroactively touch, never a guessed purpose."""
    purpose = obj.get("text_purpose")
    if purpose in _TEXT_PURPOSE_ALWAYS_DROP:
        return "drop"
    if purpose in _TEXT_PURPOSE_ALWAYS_SUBSTITUTE:
        return "substitute"
    gate_key = _TEXT_PURPOSE_CONTEXT_GATED.get(purpose)
    if gate_key is not None:
        return "substitute" if context.get(gate_key) else "drop"
    # purpose == "other", or no purpose recorded at all.
    if is_branded:
        return "drop"
    return obj.get("disposition")


# OBJECT DISPOSITION ENFORCEMENT (2026-08-17): mechanical, not prompt-only. The vision
# prompt above already asks the model to judge substitute/keep/drop for itself ("a
# STARTING POINT only - a separate mechanical check overrides this... judge honestly
# rather than trying to predict the override") - but per this codebase's own repeated,
# proven finding (see CLAUDE.md's guardrails note at the top of this repo: "the model
# does not reliably obey a text instruction about what NOT to render"), a prompt telling
# the model what its own disposition field SHOULD resolve to has no better track record
# than any other prompt-only rule. resolve_disposition is the function that actually
# decides, run over every object AFTER the vision call, unconditionally overriding
# whatever the model wrote.
def resolve_disposition(obj, context=None):
    """Mechanical override of one object's `disposition` - never trusts the model's own
    guess for a competitor-owned or brand-marked object, or for a text object whose
    text_purpose mechanically determines the answer (2026-08-17, restoring the
    per-zone-type rules the deleted structural_zones/_structural_zones_clause used to
    encode - see the objects-array refactor commit for what replaced them).

    context (2026-08-17) carries the Besque-side facts this run actually has available
    to substitute WITH - {"offer_text": str|None, "certifications": list|None,
    "testimonial": dict|None}. Deliberately passed in by the caller, never read from a
    DB here: this function stays a pure, unit-testable function of its two arguments,
    same discipline the original version already established for `obj` alone. None
    (the default) is treated as "nothing supplied" - every context-gated purpose
    (offer/price_anchor/certification/testimonial) resolves to "drop" in that case,
    which is exactly correct for the ONE call site that has no run-specific context yet
    (deconstruct_from_response, at blueprint-creation time, before any operator has
    chosen a per-run offer/testimonial) and gets RE-RESOLVED with the real context at
    generation time by generate_image_prompt._objects_clause - see that function's own
    docstring for why a single deconstruct-time resolution can never be the final
    answer for these three purposes.

    ownership == "competitor_branded" (this specific item visibly belongs to the
    competitor - their product, their logo, their own packaging or on-image copy) OR
    carries_brand_mark == True (this object shows a reproducible brand mark regardless
    of its `kind`) can NEVER resolve to "keep": a competitor's own branded content
    surviving into a Besque draft is exactly the compliance/trademark exposure this
    codebase's rule 9 (brand_rules) already treats as the single most-proven failure
    class.

    For kind == "text", ownership/carries_brand_mark alone no longer decide substitute
    vs. drop - text_purpose does (see _resolve_text_disposition), and every purpose in
    _TEXT_PURPOSE_ALWAYS_SUBSTITUTE/_TEXT_PURPOSE_CONTEXT_GATED already never resolves
    to "keep" by construction, so branding can never smuggle a "keep" through those. The
    one purpose that COULD still fall through to "keep" ("other", or no purpose
    recorded) is explicitly forced to "drop" here when the object is branded - "ownership
    rules still win, whatever the text_purpose says."

    For every other kind: a product-kind object SUBSTITUTES - Besque has a real product
    to put in its place. Every other kind (a competitor's logo, a competitor-branded
    prop, a block of the competitor's own on-image copy) DROPS - there is no Besque
    equivalent for a logo or a rival's own sentence to substitute in, only remove. A
    prop tied to the competitor's own product-category argument drops too, even when
    its ownership reads as "generic" - see _is_competitor_argument_prop.

    "maps to a Besque product" (the task's own phrasing for the substitute condition)
    is resolved here as `kind == "product"` - this pipeline runs against exactly one
    product category at a time (the operator's selected product for the run), so any
    product-kind object in a same-category reference is assumed substitutable; there is
    no per-object category-matching signal in this schema to check more precisely than
    that.

    Every other object (ownership in generic/besque/person, not brand-mark-carrying,
    not a competitor-argument prop) passes through with whatever disposition the model
    assigned, completely unchanged - this function only ever narrows an object AWAY
    from "keep", never invents a "keep" the model didn't already choose, and never
    touches an object that was never a compliance risk to begin with."""
    context = context or {}
    ownership = obj.get("ownership")
    carries_brand_mark = bool(obj.get("carries_brand_mark"))
    is_branded = ownership == "competitor_branded" or carries_brand_mark

    if obj.get("kind") == "text":
        return _resolve_text_disposition(obj, context, is_branded)

    if is_branded:
        if obj.get("kind") == "product":
            return "substitute"
        return "drop"
    if _is_competitor_argument_prop(obj):
        return "drop"
    return obj.get("disposition")


def _resolve_object_dispositions(blueprint):
    """Runs resolve_disposition over every entry in blueprint["objects"], overwriting
    each object's disposition in place (on a copy - the caller's own blueprint dict is
    never mutated, same discipline as strip_bottle_shape_language). Returns the updated
    blueprint. Safe to call on a blueprint with no `objects` key or an empty list -
    returns the blueprint unchanged in that case; schema validation is what actually
    guarantees `objects` is present and non-empty by the time this runs in the real
    pipeline (deconstruct_from_response calls this AFTER validation)."""
    objects = blueprint.get("objects")
    if not isinstance(objects, list):
        return blueprint
    blueprint = dict(blueprint)
    blueprint["objects"] = [
        {**obj, "disposition": resolve_disposition(obj)} if isinstance(obj, dict) else obj
        for obj in objects
    ]
    return blueprint


def _assert_no_competitor_branded_object_kept(blueprint):
    """Defence-in-depth self-check, not the primary enforcement mechanism -
    resolve_disposition's own logic already guarantees this invariant by construction
    (it never returns "keep" for a competitor_branded or brand-mark-carrying object), so
    this should never actually fire in practice. It exists anyway because "prompt-only
    rules have never bound on the image path" (this file's own standing lesson) applies
    exactly as much to a bug in OUR OWN enforcement code as to a model instruction - if
    resolve_disposition is ever changed in a way that reintroduces this exact gap, this
    raises loudly at deconstruct time rather than letting a competitor-branded object
    quietly reach image generation with disposition="keep"."""
    for obj in blueprint.get("objects") or []:
        if not isinstance(obj, dict):
            continue
        is_branded = obj.get("ownership") == "competitor_branded" or bool(obj.get("carries_brand_mark"))
        if is_branded and obj.get("disposition") == "keep":
            raise BlueprintValidationError(
                f"Object {obj.get('object_id', '?')!r} is competitor-branded but resolved "
                f"to disposition='keep' - this must never happen; resolve_disposition has "
                f"a bug.",
                "competitor_branded object resolved to keep",
            )


def deconstruct_from_response(raw_text: str) -> dict:
    """Parse and validate a blueprint from a raw model response. Raises if invalid.

    Bottle-shape language is stripped AFTER validation (2026-08-16, strip_bottle_shape_
    language) - schema validity is checked against what Claude actually returned, never
    against a version this function has already edited, and the strip only ever
    narrows a list or blanks one string, never invalidates a blueprint that was
    already valid. Any field actually filtered is logged by name and value so a
    stripped ad stays diagnosable from the run log, the same discipline this codebase
    already applies to every other silently-defaulted field.

    Object disposition is resolved AFTER that (2026-08-17, resolve_disposition) - order
    doesn't matter between the two (shape-stripping touches product_category.signals/
    visual.subject/layout_detail.zone_positions; disposition resolution touches
    objects[].disposition; the two never touch the same field), kept sequential rather
    than merged into one pass so each stays independently testable. Raises if the
    post-resolution invariant is somehow violated (see
    _assert_no_competitor_branded_object_kept) - this should never actually trigger."""
    blueprint = parse_blueprint(raw_text)
    err = validator.validation_error(blueprint)
    if err:
        raise BlueprintValidationError(f"Blueprint failed schema validation: {err}", err)
    blueprint, filtered = strip_bottle_shape_language(blueprint)
    if filtered:
        log.info("deconstruct: stripped bottle-shape language for ad %s: %s",
                  blueprint.get("ad_id", "?"), filtered)
    blueprint = _resolve_object_dispositions(blueprint)
    _assert_no_competitor_branded_object_kept(blueprint)
    return blueprint

# ---- Live Claude vision call (wired at kickoff) ----
import base64
import mimetypes
import anthropic


# TRANSIENT API-ERROR RETRY (2026-08-13): a completely different failure class from the
# parse/validation retry below (_MAX_DECONSTRUCT_ATTEMPTS) - that one handles a response
# we DID get back but that fails to parse or fails schema validation; this one handles
# never getting a response at all (a network timeout/dropped connection) or Anthropic's
# own infrastructure being temporarily unable to serve the request (429/5xx). Two ads
# lost live today to "Request timed out or interrupted" (anthropic.APITimeoutError's own
# exact message) - deconstruct_image's only retry loop was for parse/validation, so a
# timeout propagated straight out of client.messages.create() (which sits BEFORE that
# loop's try/except even starts) and failed the ad with nothing salvaged.
#
# Deliberately a SEPARATE, flat retry wrapped around ONLY the raw API call, never folded
# into the parse/validation loop's own attempt counter or system-prompt state - a
# transient failure has nothing to do with JSON escaping or schema correction, so it
# must never consume one of THOSE two precious attempts or trigger the escaping nudge.
# The two budgets are independent and both individually capped (2 outer x 4 inner = 8
# calls in the genuine worst case, a small bounded number, not an unbounded multiply) -
# see _call_claude_with_transient_retry's own docstring for exactly where it sits.
_MAX_TRANSIENT_ATTEMPTS = 4
_TRANSIENT_BACKOFF_BASE_SECONDS = 2.0

# 429 (RateLimitError) and every 5xx (InternalServerError/ServiceUnavailableError/
# OverloadedError/DeadlineExceededError, or any other 5xx the SDK doesn't name
# specifically) mean "the request itself was fine, try again" - never a reason to
# change what we're sending. Checked via status_code, not by enumerating every named
# subclass, so a 5xx status the SDK hasn't given its own class name to (or ever adds
# later) is still caught correctly.
def _is_transient_anthropic_error(exc):
    """True for a network-layer failure (timeout, dropped connection -
    anthropic.APIConnectionError and its subclass APITimeoutError) or a 429/5xx status
    from the API itself. False for anything else - auth (401), malformed/oversized
    request (400/413/422), permission (403), not found (404), conflict (409), or any
    other 4xx (where a content-policy rejection would also surface) - those need a
    DIFFERENT request, not a retry, and must fail fast exactly as they do today."""
    if isinstance(exc, anthropic.APIConnectionError):
        return True
    if isinstance(exc, anthropic.APIStatusError):
        return exc.status_code == 429 or exc.status_code >= 500
    return False


def _call_claude_with_transient_retry(client, kwargs, ad_id):
    """Call client.messages.create(**kwargs), retrying ONLY on a transient failure
    (see _is_transient_anthropic_error) with exponential backoff (2s, 4s, 8s, ...) up
    to _MAX_TRANSIENT_ATTEMPTS total tries. A non-transient error raises immediately on
    the very first attempt, byte-for-byte the behaviour before this existed. Logs the
    attempt number and the exception's own class name on every transient failure, so
    the run log always shows whether a retry happened and why - not just that the call
    eventually succeeded or ultimately failed."""
    wait = _TRANSIENT_BACKOFF_BASE_SECONDS
    for attempt in range(1, _MAX_TRANSIENT_ATTEMPTS + 1):
        try:
            return client.messages.create(**kwargs)
        except Exception as e:
            if not _is_transient_anthropic_error(e):
                raise
            log.warning(
                "deconstruct transient API error for ad %s (attempt %s/%s): %s: %s",
                ad_id, attempt, _MAX_TRANSIENT_ATTEMPTS, type(e).__name__, e,
            )
            if attempt == _MAX_TRANSIENT_ATTEMPTS:
                raise
            time.sleep(wait)
            wait *= 2


def _load_image_b64_v2(image_path):
    with open(image_path, "rb") as f:
        data = f.read()
    media_type = ("image/png" if data[:8]==b"\x89PNG\r\n\x1a\n" else "image/webp" if data[:4]==b"RIFF" and data[8:12]==b"WEBP" else "image/gif" if data[:4]==b"GIF8" else "image/jpeg")
    return base64.standard_b64encode(data).decode("utf-8"), media_type


def _b64_from_bytes(data):
    media_type = ("image/png" if data[:8]==b"\x89PNG\r\n\x1a\n" else "image/webp" if data[:4]==b"RIFF" and data[8:12]==b"WEBP" else "image/gif" if data[:4]==b"GIF8" else "image/jpeg")
    return base64.standard_b64encode(data).decode("utf-8"), media_type


# Nudge for the retry attempt only - added after ad 1319813143652844 failed with
# "Expecting ',' delimiter: line 21 column 26", traced to an unescaped literal quote
# inside a captured verbatim field (headline_verbatim/typography_zones text lifted
# straight from the ad image, which can itself contain quote marks). Not sent on
# attempt 1 so today's byte-for-byte behaviour is unchanged when nothing goes wrong.
JSON_ESCAPE_SYSTEM = (
    "Return ONLY valid JSON, no markdown fences, no preamble. Any literal double-quote "
    "character that appears WITHIN a string value - e.g. a headline or on-image text "
    "captured verbatim that itself contains a quote mark - must be escaped as \\\" so the "
    "JSON remains parseable. Never leave a bare unescaped \" inside a string value."
)

# Nudge for the retry attempt only - added when structural_zones became a required
# field (see schema/blueprint.schema.json). A response that parses as JSON but fails
# schema validation (e.g. structural_zones missing) is a different failure from
# malformed JSON, so it gets the validator's own message quoted back as a correction
# instruction, not the escaping nudge above - see deconstruct_image's retry loop.
def _validation_retry_system(err_message):
    return (
        "Your previous response was valid JSON but failed schema validation: "
        f"{err_message} Correct this specific problem and return the full corrected "
        "JSON blueprint again. Return ONLY valid JSON, no markdown fences, no preamble."
    )


# Total vision-call attempts for one ad: the original call plus exactly one retry,
# whichever failure mode triggers it (parse or schema validation). Mirrors
# generate_copy.py's _COPY_ATTEMPTS shape - one retry, never a loop.
_MAX_DECONSTRUCT_ATTEMPTS = 2

# Claude's API default temperature is 1.0 - fine for creative copy, wrong for this call.
# deconstruct_image is an OBSERVATION task (what does this specific image actually show),
# not a creative one - four single-ad runs of the identical reference produced four
# different headlines, layouts, and bottle treatments with nothing but sampling variance
# to blame (no code-level state leak - see CLAUDE.md's 2026-08-10 batch-degradation note
# for the parallel investigation that already ruled that out for pipeline.py itself).
# Low, not zero: some legitimate phrasing latitude in free-text fields (signals, detail
# strings) is fine and not worth fighting; the STRUCTURE and FACTS extracted from the same
# pixels should not be a coin flip.
DECONSTRUCT_TEMPERATURE = 0.2


def _log_parse_failure(attempt, total, message, raw_text, exc):
    """Record exactly what came back, so an intermittent failure stays diagnosable from
    the run log even when the retry goes on to succeed - mirrors
    generate_copy._log_parse_failure."""
    usage = getattr(message, "usage", None)
    log.error("deconstruct parse failed (attempt %s/%s): %s: %s",
              attempt, total, type(exc).__name__, exc)
    log.error("stop_reason=%r output_tokens=%s input_tokens=%s content_blocks=%s",
              getattr(message, "stop_reason", "?"),
              getattr(usage, "output_tokens", "?"),
              getattr(usage, "input_tokens", "?"),
              len(getattr(message, "content", None) or []))
    log.error("raw_text len=%s chars", len(raw_text))
    log.error("raw_text repr (first 2000): %r", raw_text[:2000])
    if len(raw_text) > 2000:
        log.error("raw_text repr (last 500): %r", raw_text[-500:])


def deconstruct_image(image_bytes, ad_id, source_page, captured_at, destination_url="", ad_text="", cta=""):
    """Send one ad image to Claude vision and return a validated blueprint dict.

    Normally ONE API call. If the response cannot be parsed as JSON, retries ONCE with a
    system-prompt nudge about JSON string escaping (see JSON_ESCAPE_SYSTEM). If the
    response parses but fails schema validation (e.g. structural_zones missing - now a
    required field), retries ONCE instead with the validator's own message appended as a
    correction instruction (see _validation_retry_system) - a different problem gets a
    different nudge, not the JSON-escaping one. Either way, raises if the retry fails
    too, logging ad_id plus the raw response or the validation message on every failed
    attempt so an unfixable ad stays diagnosable from the run log. One retry, never a
    loop.

    The raw API call itself (client.messages.create) goes through
    _call_claude_with_transient_retry on every one of the attempts above - a SEPARATE,
    independently-capped retry for a network timeout/connection error or a 429/5xx from
    Anthropic's own infrastructure (see that function's own docstring). That budget
    never interacts with the parse/validation one above: a transient failure is retried
    silently within that helper and never reaches the except blocks below at all unless
    its own budget is exhausted, at which point it propagates straight out of this
    function exactly as an unretried transient error did before this existed - it is
    never treated as a parse/validation failure, and never consumes one of THOSE two
    attempts."""
    b64, media_type = _b64_from_bytes(image_bytes)
    prompt = build_prompt(ad_id, source_page, captured_at, destination_url)

    content = [{"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}}]
    copy_parts = []
    if ad_text:
        copy_parts.append(f"Scraped ad copy (verbatim from the source page):\n{ad_text}")
    if cta:
        copy_parts.append(f"CTA button: {cta}")
    if copy_parts:
        content.append({"type": "text", "text": "\n\n".join(copy_parts)})
    content.append({"type": "text", "text": prompt})

    # max_retries=0 (was 1): the SDK's own hidden retry covers the same transient
    # failures _call_claude_with_transient_retry now handles explicitly - leaving both
    # active would silently compound (the SDK retrying inside every one of OUR retries,
    # invisible to our own logging and to _MAX_TRANSIENT_ATTEMPTS' own cap). Exactly one
    # mechanism now owns transient retry, and it's the one that logs and is tested.
    client = anthropic.Anthropic(timeout=60.0, max_retries=0)  # reads ANTHROPIC_API_KEY from env
    total = _MAX_DECONSTRUCT_ATTEMPTS
    system = None
    for attempt in range(1, total + 1):
        kwargs = {
            "model": CLAUDE_MODEL,
            # Part B added creative_objective/target_audience/typography (4 sub-fields) and
            # expanded layout_detail (4 more sub-fields, one an array) on top of the existing
            # ~15-field blueprint - estimated +200-350 tokens for the fuller JSON response.
            # 3072 -> 4096 is a reasoned safety margin, NOT an empirically measured fix.
            "max_tokens": 4096,
            "temperature": DECONSTRUCT_TEMPERATURE,
            "messages": [{"role": "user", "content": content}],
        }
        if system:
            kwargs["system"] = system
        message = _call_claude_with_transient_retry(client, kwargs, ad_id)
        raw_text = ""
        try:
            raw_text = message.content[0].text if message.content else ""
            return deconstruct_from_response(raw_text)
        except BlueprintValidationError as e:
            log.error("deconstruct schema validation failed for ad %s (attempt %s/%s): %s",
                      ad_id, attempt, total, e.validation_error)
            if attempt == total:
                raise
            log.warning("retrying deconstruct for ad %s with the validation error appended "
                        "as a correction instruction", ad_id)
            system = _validation_retry_system(e.validation_error)
        except Exception as e:
            _log_parse_failure(attempt, total, message, raw_text, e)
            if attempt == total:
                raise
            log.warning("retrying deconstruct for ad %s with a JSON-escaping system prompt nudge", ad_id)
            system = JSON_ESCAPE_SYSTEM
