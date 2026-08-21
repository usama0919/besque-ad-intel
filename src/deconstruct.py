"""Deconstruction step: send an ad image to Claude and get a structured blueprint."""
import os
import json
import base64
import logging
import re
import time
from pathlib import Path

from src import compliance, json_response, validator

# Model + key are read from env so the real key plugs in at kickoff.
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")

log = logging.getLogger("deconstruct")

BLUEPRINT_PROMPT = """You are an image decomposition system. Your first job is to decompose the attached advertising image into its constituent visual elements - every product, person, text block, logo, prop, and graphic - as a structured object inventory. Your second job is to record the ad's structural and strategic facts around that inventory. Return a single JSON creative blueprint, ONLY valid JSON, no preamble or markdown.

Scraped ad copy, if supplied as a separate text block, is the source of truth for the headline and offer where it conflicts with what is legible in the image. It is NEVER a source for objects: every entry in objects[] must correspond to something visibly present in the attached image itself - if the scraped copy contains a testimonial, CTA, or other text that is not actually rendered in the image's own pixels, that text must NOT appear in objects[] at all, no matter how prominently it features in the scraped copy.

The JSON must have exactly these fields:
- ad_id (string): use the value "{ad_id}"
- source_page (string): use the value "{source_page}"
- captured_at (string): use the value "{captured_at}"
- objects (array): REQUIRED - this key must ALWAYS be present, and must NEVER be empty - every ad has at least one object (at minimum, the product or the headline). ONE ENTRY PER VISUALLY DISTINCT THING in the reference ad - every product, every person, every discrete text block (headline, sub-line, body copy, CTA button, badge, price, disclaimer, testimonial quote - each is its OWN entry, never bundled), every logo/wordmark, every prop, every surface the product or a person interacts with directly (not the general background - see `background` below for the environment itself), and every other graphic element (an icon, a badge shape, a decorative flourish). MULTIPLE INSTANCES OF THE SAME PRODUCT ARE SEPARATE ROWS, each with its own object_id - two bottles in frame is two entries, not one entry noting a count. Anything with an identity - anything a viewer could point at and name - is an object row; it is NEVER acceptable to fold an identifiable thing into `background` prose instead of listing it here. An incomplete inventory here means that object gets silently reproduced unchanged (if omitted) or invented from nothing (if the model believes the scene is otherwise empty) when this reference is cloned - list everything visible, not just the obviously important pieces. Legible text printed ON a non-text object's own surface (a brand name on a product label, a logo on packaging, a caption baked into a graphic) is NOT folded into that object's `description` prose either - record it via that object's own `text_content` field below, so it can be judged for disposition exactly like any other text. Each entry:
    {{
      "object_id": stable string identifier, "obj_01", "obj_02", ... "obj_NN", assigned in the order you list them, never reused within this blueprint,
      "kind": one of product/person/text/logo/prop/surface/graphic - product = a sellable item in its packaging; person = a human figure (the whole figure, not per-body-part); text = one discrete text block; logo = a brand mark/wordmark distinct from a product's own printed label; prop = a physical object that isn't the product, a surface, or a person; surface = a distinct physical surface the product/a person rests on or touches (a tray, a towel, a countertop the bottle sits on specifically - not the general environment); graphic = a non-text graphic element (an icon, a badge shape, a decorative flourish),
      "description": what it plainly is, e.g. "amber glass body-oil bottle with pump", "customer testimonial quote with 5-star rating", "brand wordmark top-left" - structure and content only, describe what it IS, not its colour (colour goes in `colours` below),
      "appearance": OPTIONAL, mainly for a competitor-owned/brand-marked product/logo/prop object - generic, non-identifying physical characteristics ONLY: shape, material, generic colour language, texture, e.g. "a cylindrical glass bottle with a matte label and a pump top". MUST NEVER include a brand name, product name, or any other text that would identify WHICH product or company this is - that belongs in `description` above, never here. This exists so a competitor object's generic physical form can safely inform Besque's own composition without ever transferring its identity - omit entirely for an object where this distinction doesn't apply (most text, most generic props),
      "bbox": [x, y, w, h], the object's bounding box as fractions of the FULL IMAGE (0.0 to 1.0), x/y measured from the top-left corner, w/h as fractions of image width/height - your best estimate of where this object sits and how much of the frame it occupies,
      "colours": array of this object's OWN colours only, e.g. ["amber", "gold"] - never the scene's overall palette (that's `visual.palette_mood`) and never another object's colour,
      "ownership": one of competitor_branded/generic/besque/person - competitor_branded = this specific item visibly belongs to or names the advertiser/competitor (their product, their logo, their packaging, their own on-image copy); generic = register-neutral, could belong to any brand's ad (a hand, a towel, a plain surface, unbranded scenery); besque = do not use this value at deconstruct time, it does not apply to a competitor reference; person = a human figure, handled by its own substitution rules rather than the brand-ownership question,
      "role": one of hero/secondary/supporting_prop/environment - hero = the main subject the ad is built around; secondary = a supporting element with real presence (a sub-line, a second product); supporting_prop = minor set-dressing; environment = part of the setting rather than a foregrounded thing,
      "carries_brand_mark": true/false - true if this object itself visibly shows a reproducible brand mark, logo, or wordmark (even if its `kind` isn't "logo" - e.g. a product bottle with the competitor's name printed on its own label carries a brand mark too), false otherwise,
      "brand": OPTIONAL - the specific brand/company name this object visibly belongs to or names, e.g. "GlowCo", "The Body Firm" - the advertiser's own brand, OR a different, third-party brand if one appears (e.g. a rival product shown in a comparison ad). A single clean name only, nothing else - this is a SEPARATE, structured value from `description` (which may also mention the name as part of normal prose) and from `appearance` (which must NEVER contain a brand or product name at all - see that field's own instruction above). Omit entirely when ownership is generic/besque/person, or when no specific brand name is actually legible or attributable in the image - never guess or infer a brand from generic visual cues alone,
      "persuasive_function": what this object exists to DO in the ad's argument, in one short phrase, e.g. "the hero product being sold", "proves social validation via a real customer's words", "names the advertiser",
      "disposition": one of substitute/keep/drop - your best judgement of whether this object should be replaced with a Besque equivalent, kept as-is, or removed entirely when this reference is cloned for Besque. This is a STARTING POINT only - a separate mechanical check overrides this for any competitor-owned or brand-marked object regardless of what you choose here, so judge honestly rather than trying to predict the override.
      "required_in_output": OPTIONAL, boolean - being listed here means you OBSERVED this object in the source photo, never by itself an instruction that it must survive into the output. Set to false when this object is incidental, not load-bearing to the ad's own composition or argument (a stray hand mid-gesture not central to the shot, a loose hair strand, background clutter that happened to be in frame) - true (or omit) for the ordinary case, everything that IS structurally part of the composition. Only ever meaningful when disposition is "keep" - never set this to influence a "substitute" or "drop" object, those are already fully governed by disposition. NEVER set this to false for a kind=="product" object under any circumstances - the Besque product always belongs in the output regardless of what this field says, and a mechanical check ignores this field entirely for that kind, so setting it there would have no effect besides being wrong.
      "represents_product_substance": OPTIONAL, boolean - meaningful only for kind prop/surface/graphic. Set to true when this object's OWN material or texture is a STYLED VISUAL STAND-IN for the featured product's own substance or consistency - e.g. a swirl, pool, or dollop of cream/gel/liquid arranged as flat-lay set-dressing beside or beneath the product to show what it feels/looks like, NOT literally a drip or smear that visibly left the SAME bottle shown in this frame. Omit or false for ordinary set-dressing (a marble surface, a flower, a ribbon, a towel) that has nothing to do with the product's own substance - the common case.
      "text_purpose": REQUIRED when kind is "text", omit entirely for every other kind. One of headline/subtext/cta/offer/certification/testimonial/price_anchor/award/disclaimer/product_callout/other - the JOB this specific text block does, not its wording:
        headline = the ad's main hook/attention line.
        subtext = a supporting line or body copy beneath the headline.
        cta = a call-to-action button label or link text.
        offer = a discount, promo code, scarcity/stock-count claim, or urgency wording (e.g. "20% OFF", "Only 100 left").
        certification = a badge or line naming a certification/standard the product holds (e.g. "Vegan", "Cruelty Free", "Dermatologist Tested").
        testimonial = a customer quote or review, with or without a star rating or name. Distinguish which KIND via the `social_proof_kind` field below - a single customer's own words is a different thing from an aggregate review-count/star-average bar, and they need different treatment downstream.
        price_anchor = a shown price, or a was/now price comparison.
        award = an award, "as seen in" press mention, editorial accolade, or third-party endorsement line (e.g. "by THE BODY FIRM").
        disclaimer = legal, regulatory, or medical fine print, or an asterisked footnote.
        product_callout = a short benefit or property label pointing at the product, distinct from the headline (e.g. an icon + "Fast-Absorbing").
        other = any other discrete text block that genuinely fits none of the above - use sparingly, only when no other value honestly applies.
      "serves_object_id": OPTIONAL, applies only when `kind` is "text" or "prop" - the object_id of a DIFFERENT object THIS one exists only to support, e.g. a hand whose entire visible role is holding a specific product bottle records that product's object_id here; a caption or arrow pointing specifically at one product records that product's object_id. null (or omit) for every object that stands on its own, which is the common case - most props and text blocks serve no other single object and must NOT be forced to name one. Never point at yourself, and only ever name an object_id you have already assigned earlier in this SAME list - assign object_ids in an order that makes this possible (the product/person before anything that serves it).
      "same_product_as": OPTIONAL, applies only when `kind` is "product" - when this ad shows MORE THAN ONE product object, decide whether they are the SAME product differing only in size or format (e.g. a standard-size and a jumbo-size bottle of the identical product line) or GENUINELY DIFFERENT products. If this product is the SAME product as an EARLIER product object_id in this list, record that earlier object's id here. null (or omit) when this product is visually distinct from every other product object in the scene (the common single-product case), or when this IS the first/earliest instance of a repeated product - never name yourself, and only ever name an object_id you have already assigned earlier in this SAME list. Get this right: instances sharing a same_product_as chain all get replaced with Besque's product; products left unlinked (each genuinely distinct) result in only ONE surviving in the output, with the others removed entirely - so mislabelling two different products as "the same" wrongly keeps a second one, and mislabelling two sizes of one product as "different" wrongly deletes one that should have survived. NEVER use this field for a component, attachment, detached piece, container, or accessory of another object (a lid, cap, box, carton, dropper, applicator, spoon, refill pouch, sleeve) - that is a DIFFERENT relationship, see `part_of` below. A detached lid leaning against its own tin is NOT "the same product, different size" - it is a PART of the tin, and recording it as same_product_as wrongly authorises a second full product instance where the ad shows only one.
      "part_of": OPTIONAL, applies to any kind but most commonly "product" or "prop" - the object_id of an EARLIER object in this SAME list that THIS object is a physical component, attachment, detached piece, container, or accessory OF: a lid, cap, box, carton, dropper, applicator, spoon, refill pouch, or sleeve belonging to a product elsewhere in this list. This object has no independent existence in the composition apart from the object it belongs to - it is never a second free-standing instance (that is same_product_as's job, above, and the two must never be confused). null (or omit) when this object is not a component of anything else, which is the common case - never name yourself, and only ever name an object_id you have already assigned earlier in this SAME list.
      "social_proof_kind": REQUIRED when text_purpose is "testimonial", omit entirely otherwise - one of "single_quote" (one customer's own words, with or without a name/rating attached) or "aggregate" (a review-count/star-average summary with no single customer's words, e.g. "Rated 4.8 by 12,000 customers", "Trustpilot - Over 30,000 - 5 stars"). An aggregate figure is NEVER Besque's to show (no approved aggregate exists) and is always removed downstream regardless of what you record here - this field exists so that removal is judged by what the zone actually IS, never guessed from its text_purpose alone.
      "typography": REQUIRED when `kind` is "text", omit entirely for every other kind. Also omit it for text that is the competitor's own on-pack branding (a brand wordmark, a product name, an on-pack descriptor line) and for any award or disclaimer text - these are never reproduced, so their typographic treatment is not needed. Otherwise: {{ "typeface_class": serif/sans/script, "weight": e.g. bold/light/regular, "case": upper/title/sentence, "letter_spacing": tight/normal/wide, "colour": this text's OWN colour - distinct from the scene's overall palette_mood, e.g. "gold" or "white", "size_relative": e.g. large/medium/small relative to the frame, "decorative_elements": array of short phrases for anything attached (a pipe divider, a rule, an underline, a bullet mark) or [] if none, "line_count": number of lines this text actually occupies }}. A reference commonly has THREE OR FOUR distinct typographic levels across its several text objects (e.g. a large serif headline, a small-caps accent line with wide letter-spacing, small body copy, a button label) - give each its own accurate treatment, never the same values copied across every text object just because one was easy to read.
      "styling": REQUIRED when text_purpose is "testimonial", omit entirely otherwise - how the testimonial is visually PRESENTED, e.g. "quote marks, no card", "5-star rating above the quote inside a white rounded card", "speech-bubble shape with a small avatar circle". This is CONTAINER presentation only, never content - the actual quote/attribution rendered downstream never comes from here.
      "text_content": OPTIONAL, applies to ANY object regardless of `kind` - if this object's OWN rendered pixels contain legible text (a brand wordmark printed on a product's label, a logo on packaging, a caption baked into a background graphic, a slogan printed on a prop), record ONE ENTRY HERE PER DISTINCT LEGIBLE TEXT BLOCK on that object - never fold it into `description` prose instead. This is separate from and in addition to a genuine standalone kind=="text" object elsewhere in this list: use `text_content` specifically for text that is physically PART OF this non-text (or text) object's own surface, not a separate discrete text block floating in the scene. Getting this right matters - text baked into a product photo, a prop, or a graphic that never becomes its own object here is text no downstream check ever looks at again; it renders into the Besque draft completely unchanged, brand name and all. Array of: {{ "object_id": stable id, e.g. "obj_04_txt_01" (never reuse an id already used elsewhere in this blueprint), "content": the literal text AS IT LITERALLY APPEARS, verbatim - this exists so a human/mechanical check can see what was detected, it must NEVER be treated as content to render, "bbox": [x, y, w, h] of just this text within the full image, same convention as every other bbox in this schema, "text_purpose": same enum as a top-level text object's text_purpose (headline/subtext/cta/offer/certification/testimonial/price_anchor/award/disclaimer/product_callout/other) - a baked-in brand wordmark or logo is almost always "other" unless it genuinely fits a more specific purpose, "ownership": same enum as a top-level object's ownership (competitor_branded/generic/besque/person), "carries_brand_mark": true/false, same meaning as the top-level field, "disposition": your best-judgement starting point (substitute/keep/drop), same caveat as the top-level field - a separate mechanical check overrides this for anything competitor-owned or brand-marked regardless of what you choose here }}. Omit entirely (or an empty array) when this object has no legible baked-in text of its own, which is the common case.
    }}
- format (string): one of testimonial_card, product_hero, editorial, offer_led, or another short descriptor
- hook (object): {{ "type": one of question/bold_claim/problem_agitate/social_proof/other, "headline_structure": short description }}
- angle (string): the core persuasive angle
- awareness_stage (string): one of unaware, problem, solution, product, most_aware
- claims (array): any of efficacy, sensory, ingredient, social_proof, offer
- visual (object): {{ "layout": ..., "subject": ..., "palette_mood": ..., "text_placement": ... }}
- background (object): REQUIRED - this key must ALWAYS be present. {{ "surface": what the scene is set on or against, e.g. "marble countertop", "sandy beach", "plain white studio backdrop", "colour": the background's OWN dominant colour, distinct from any object's colour, "light": one short phrase describing the scene's overall lighting character, e.g. "soft warm light from upper-left" or "flat even studio light, no visible shadow direction" }}. This is the ENVIRONMENT only - surface, colour, and light. Nothing with its own identity belongs here: a product, a person, a prop, a piece of text, a logo - ANYTHING a viewer could point at and name as a distinct thing - is an OBJECT (see `objects` above), never folded into this field's prose. If you find yourself describing what something IS rather than what the space around it looks like, it belongs in `objects`, not here.
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
- typography (object): {{ "headline_face": typeface style e.g. serif/sans/script, "headline_weight": e.g. bold/light/regular, "hierarchy_levels": array of short phrases describing each distinct text tier top to bottom (e.g. ["large bold serif headline", "medium sans subhead", "small CTA button label"]), "case_treatment": e.g. "all caps headline, sentence case body" }} - general prose about the ad's typography; per-zone text detail belongs in `objects` (kind "text", listed first above), not here.
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

# Stat-claim restoration (2026-08-19, audit-confirmed gap: 6b82f60 deleted
# STAT_CLAIM_PATTERNS/_is_stat_shaped_zone with no replacement - nothing in the
# text_purpose enum covers a numeric/percentage/ratio/timescale efficacy badge, e.g.
# a "+61% more supple skin" roundel. Recovered verbatim from git history
# (6b82f60~1:src/generate_image_prompt.py) rather than reinvented, per the standing
# instruction to restore from history not guess a new implementation. Reuses
# compliance.py's own numeric-claim patterns, same as the original - matches the
# SHAPE of a stat claim generally (any percentage, any "N out of M", any "Nx more/
# faster/better", any "in N days/weeks/hours"), never a list of known values, same
# principle _TEXT_PURPOSE_ALWAYS_DROP already uses for award names via keyword match.
STAT_CLAIM_PATTERNS = (
    compliance.NUMERIC_CLAIM_PATTERN, compliance.RATIO_CLAIM_PATTERN, compliance.TIMESCALE_CLAIM_PATTERN,
    # DURATION_CLAIM_PATTERN (2026-08-20, Part D): a bare competitor-story duration
    # ("12 years") - see that pattern's own docstring in compliance.py.
    compliance.DURATION_CLAIM_PATTERN,
)

# Only these two purposes are checked - deliberately NOT headline/subtext/cta/offer/
# certification/testimonial/price_anchor/award/disclaimer, matching the ORIGINAL
# (pre-6b82f60) scope exactly: that code only ever ran _is_stat_shaped_zone against
# product_callout zones. A stat-shaped headline/subtext still can't leak a fabricated
# claim - its wording is governed entirely by rule 6/TIER 1 angle language elsewhere,
# never copied from the reference - so forcing its SLOT to drop here would only
# delete a headline position that should still exist for Besque's own (non-stat)
# wording to occupy, a regression this restoration must not introduce.
_STAT_SHAPE_CHECKED_PURPOSES = ("product_callout", "other", None)


def _is_stat_shaped_text(obj):
    """True when this text object's own wording reads as a numeric/percentage/
    ratio/timescale/duration efficacy claim - Besque did not run whatever study
    produced THAT number, has no evidence for a competitor's own customer-story
    duration, and has no Besque counterpart to substitute with; putting our
    product name or a generated benefit line in it isn't a substitution, it's
    noise wearing the shape of one.

    2026-08-20 fix (Part D of the text-layer completion task): reads via
    _prohibited_claim_text (description + persuasive_function for a top-level
    object, `content` for a text_content sub-object), not description/
    persuasive_function alone - a sub-object has no description/persuasive_
    function at all, so this was silently checking two always-empty fields for
    every sub-object before this fix, exactly the same dispatch mistake
    resolve_disposition's own kind=="text" check already made once (see that
    function's own 2026-08-20 note). Confirmed live: a sub-object's own `content`
    can legitimately carry a competitor duration claim (e.g. "12 years") that
    this check must catch on sub-objects, not only top-level objects."""
    return any(p.search(_prohibited_claim_text(obj)) for p in STAT_CLAIM_PATTERNS)


def _served_object_needs_drop(obj, served_object_disposition):
    """True when `obj` names a `serves_object_id` AND the object it serves has
    resolved to "substitute" or "drop" - the mechanical re-evaluation Problem 1
    (2026-08-17) requires: a hand/prop/caption existing only to hold or point at
    another object has no independent reason to survive once what it served is gone
    (drop) or replaced with a structurally different Besque equivalent (substitute) -
    "the hand carries over unchanged" (observed five times, also with hair) is exactly
    what trusting the model's own "keep" guess here produces, the same "prompt-only
    rules do not reliably bind" failure class this codebase has hit repeatedly (see
    CLAUDE.md's guardrails note). served_object_disposition is None when obj has no
    serves_object_id, or the caller has not resolved one - never guessed here."""
    return bool(obj.get("serves_object_id")) and served_object_disposition in ("substitute", "drop")


def _resolve_text_disposition(obj, context, is_branded, served_object_disposition=None):
    """The text_purpose-driven half of resolve_disposition, below - kept as its own
    function so the branded/non-branded call sites can both reach it without
    duplicating the purpose map. `is_branded` is passed in (never recomputed here) so
    this can never drift from the same ownership/carries_brand_mark check every other
    kind already uses.

    Missing/unrecognised text_purpose (a legacy object predating this field, or a
    genuinely malformed one) falls back to the object's own model-assigned disposition,
    same as any other kind - back-compat for the ~300 existing rows this schema
    addition does not retroactively touch, never a guessed purpose. served_object_
    disposition (2026-08-17, Problem 1) only ever reaches this final fallback - every
    other text_purpose is already deterministic on its own terms (a headline substitutes
    regardless of what it "serves"; an unrecognised/"other" purpose is the one case with
    nothing else deciding its fate, e.g. a caption or arrow pointing at a product).

    Stat-shaped check (2026-08-19 restoration) runs BEFORE _TEXT_PURPOSE_ALWAYS_SUBSTITUTE
    deliberately - product_callout is unconditionally in that set, so a stat-shaped
    callout must be intercepted here or it would never reach this check at all."""
    purpose = obj.get("text_purpose")
    if purpose in _TEXT_PURPOSE_ALWAYS_DROP:
        return "drop"
    if purpose in _STAT_SHAPE_CHECKED_PURPOSES and _is_stat_shaped_text(obj):
        return "drop"
    if purpose in _TEXT_PURPOSE_ALWAYS_SUBSTITUTE:
        return "substitute"
    gate_key = _TEXT_PURPOSE_CONTEXT_GATED.get(purpose)
    if gate_key is not None:
        return "substitute" if context.get(gate_key) else "drop"
    # purpose == "other", or no purpose recorded at all.
    if is_branded:
        return "drop"
    if _served_object_needs_drop(obj, served_object_disposition):
        return "drop"
    return obj.get("disposition")


def _prohibited_claim_text(obj):
    """The text of `obj` worth checking against compliance.PROHIBITED_CLAIM_PATTERNS -
    `content` for a text_content sub-object (the literal on-image string it records,
    schema/blueprint.schema.json), else `description` + `persuasive_function` for a
    top-level object (same two-field convention _is_stat_shaped_text already uses
    for the identical "which field carries a top-level object's own wording" question).
    A sub-object always has `content`; a top-level object never does - checking for
    its presence is exactly as reliable a discriminator as kind=="text" is for a
    top-level object, and avoids the same dispatch mistake resolve_disposition's own
    kind=="text" check already made once (see this function's own 2026-08-20 fix
    note below) - never checked twice or missed by relying on `kind` alone."""
    if "content" in obj:
        return obj.get("content") or ""
    return f"{obj.get('description') or ''} {obj.get('persuasive_function') or ''}"


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
def resolve_disposition(obj, context=None, served_object_disposition=None,
                         part_of_parent_disposition=None):
    """Mechanical override of one object's `disposition` - never trusts the model's own
    guess for a competitor-owned or brand-marked object, or for a text object whose
    text_purpose mechanically determines the answer (2026-08-17, restoring the
    per-zone-type rules the deleted structural_zones/_structural_zones_clause used to
    encode - see the objects-array refactor commit for what replaced them).

    part_of_parent_disposition (2026-08-20, tin+lid product-count generalisation):
    the ALREADY-RESOLVED disposition of the object THIS object's own `part_of` names,
    if any - passed in by the caller (never looked up here, same pure-function
    discipline as served_object_disposition). An object naming `part_of` is a
    component/attachment/detached piece of another object (a lid, a cap, a dropper)
    with no independent existence in the composition - it can NEVER independently
    resolve to "substitute" (that would render it as its own second product instance,
    exactly the artifact-1400 tin+lid bug this field exists to prevent) or "keep" once
    its parent is being substituted (a Besque bottle has no separate lid to keep
    alongside it). Checked FIRST, before every other rule in this function including
    kind=="person"/is_branded, since a component's fate is never independently decided
    by its own kind or branding once a parent relationship is recorded - only by what
    happens to the parent. When the parent resolves to "substitute", the component
    always resolves to "drop". When the parent's disposition is "keep" or "drop", the
    component inherits that same value. When the caller doesn't know the parent's
    disposition yet (None, the default), the component conservatively resolves to
    "drop" - never substitute, never a "keep" independent of a parent whose own fate
    isn't known. An object with no `part_of` at all is entirely unaffected, regardless
    of what this parameter is passed as.

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

    For kind == "person" (2026-08-17): ALWAYS "substitute", unconditionally - checked
    before the ownership/branding logic below, never after, and never gated on
    `ownership` or `carries_brand_mark` at all. A person in a competitor's reference ad
    is that competitor's model by definition - there is no case where reproducing her
    is correct, so this can never fall through to "keep" the way the generic passthrough
    below used to let it. This is the fix for a live failure: a competitor's model
    object resolved to "keep" (the model's own prompt-time guess, trusted unchanged by
    the old passthrough), and rule 10's prompt-only age/skin-texture requirements
    (grey/silver hair, visible facial lines, mature skin texture) could not bind against
    a "keep" disposition sitting closer to the point of use in the assembled prompt -
    the same "prompt-only rules do not reliably bind" failure class this codebase has
    hit repeatedly (see CLAUDE.md's guardrails note). The fix is mechanical and
    structural, same as every other case in this function, not a stronger version of
    rule 10's own wording. PERSON's own substitution instructions (identity/pose/age)
    are unaffected by this - this function only decides SUBSTITUTE vs. KEEP vs. DROP,
    never how a substitution is carried out.

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

    served_object_disposition (2026-08-17, Problem 1 - "inherited objects the ad does
    not need"): the ALREADY-RESOLVED disposition of the object THIS object's own
    `serves_object_id` points at, if any - passed in by the caller (never looked up
    here; this function stays a pure function of its own arguments, same discipline
    `context` already established). blueprint.objects is a flat list with no
    relationships beyond this one optional pointer, so nothing else could tell this
    function "the product this hand holds is being replaced" - see
    _served_object_needs_drop, checked in the final passthrough below and inside
    _resolve_text_disposition's own "other"/no-purpose branch. A hand/prop/caption
    that serves nothing (serves_object_id is null, the common case) is entirely
    unaffected regardless of what this parameter is passed as.

    Every other object (kind not text/person, ownership in generic/besque, not
    brand-mark-carrying, not a competitor-argument prop, not serving an object that
    substituted or dropped) passes through with whatever disposition the model
    assigned, completely unchanged - this function only ever narrows an object AWAY
    from "keep", never invents a "keep" the model didn't already choose, and never
    touches an object that was never a compliance risk to begin with. `person` is no
    longer part of this passthrough group (see above) - it is the ONE kind this
    function narrows away from "keep" unconditionally, regardless of whether it was
    ever a "risk" by the ownership/branding tests the rest of this function uses."""
    context = context or {}
    ownership = obj.get("ownership")
    carries_brand_mark = bool(obj.get("carries_brand_mark"))
    is_branded = ownership == "competitor_branded" or carries_brand_mark

    # Prohibited claim phrases (2026-08-20, Part A) - checked FIRST, before every
    # other rule including part_of, exactly the same structural position as the
    # part_of check immediately below: Besque makes no efficacy, authority, or
    # certification claim, so an object carrying one of these phrases can never
    # resolve to "keep" or "substitute" for ANY reason - not because it's a
    # component of something being substituted, not because it's otherwise
    # generic/unbranded. See compliance.PROHIBITED_CLAIM_PATTERNS' own docstring
    # for the live case (ad 1357229623024367, "Clinically Proven"/"Dermatologist
    # Developed") and _prohibited_claim_text for which field is checked.
    if compliance.prohibited_claim_match(_prohibited_claim_text(obj)):
        return "drop"

    if obj.get("part_of"):
        if part_of_parent_disposition == "substitute":
            return "drop"
        return part_of_parent_disposition or "drop"

    # kind=="text" OR text_purpose present (2026-08-20, empty-container fix
    # follow-on): a text_content sub-object (schema/blueprint.schema.json) has no
    # `kind` field at all - it doesn't need one, its own text_purpose already says
    # what it is - but this dispatch previously checked kind=="text" only, so every
    # sub-object silently fell through to the generic (non-text) branches below
    # regardless of its own text_purpose. Branded sub-objects still resolved
    # correctly by accident (both paths agree "drop" for anything branded), which
    # is why this went unnoticed until a context-gated purpose (offer/
    # certification/testimonial) on a sub-object needed its own gate actually
    # checked - the empty-container fix's own dual-resolution claim (a sub-object
    # re-resolving "substitute" once real context exists) depends on this dispatch
    # being correct. text_purpose is required on every text_content entry (schema),
    # so checking for its presence is exactly as reliable as kind=="text" is for a
    # top-level object, never a guess.
    if obj.get("kind") == "text" or obj.get("text_purpose") is not None:
        return _resolve_text_disposition(obj, context, is_branded, served_object_disposition)

    if obj.get("kind") == "person":
        return "substitute"

    if is_branded:
        if obj.get("kind") == "product":
            return "substitute"
        return "drop"
    if _is_competitor_argument_prop(obj):
        return "drop"
    if _served_object_needs_drop(obj, served_object_disposition):
        return "drop"
    return obj.get("disposition")


def _resolve_object_dispositions(blueprint):
    """Runs resolve_disposition over every entry in blueprint["objects"], overwriting
    each object's disposition in place (on a copy - the caller's own blueprint dict is
    never mutated, same discipline as strip_bottle_shape_language). Returns the updated
    blueprint. Safe to call on a blueprint with no `objects` key or an empty list -
    returns the blueprint unchanged in that case; schema validation is what actually
    guarantees `objects` is present and non-empty by the time this runs in the real
    pipeline (deconstruct_from_response calls this AFTER validation).

    TWO PASSES (2026-08-17, Problem 1 - serves_object_id): pass 1 resolves every object
    exactly as before, with no knowledge of serves_object_id - this is also what pass 2
    reads AS the served object's disposition for anything that names one, since
    resolve_disposition is a pure function of one object plus its OWN served-object
    input, never a whole-blueprint traversal. Pass 2 re-resolves only objects that
    actually name a serves_object_id, passing pass 1's value for whatever they serve.
    Deliberately single-hop: if A serves B and B serves C, pass 2 corrects B against C
    correctly, but A is re-resolved against B's PASS-1 value (which does not yet know
    about C) - a chain longer than one hop is not handled generically here, since
    nothing in this schema or any observed failure has needed one; recorded as a known
    scoping limit, not a silent gap.

    part_of (2026-08-20, tin+lid product-count generalisation) gets the SAME two-pass
    treatment as serves_object_id, for the same reason: a component's fate depends on
    its parent's resolved disposition, which pass 1 doesn't know about the component
    relationship at all. Pass 1's own value for a component (called with no part_of_
    parent_disposition) is a safe placeholder ("drop", resolve_disposition's own
    conservative default) - pass 2 overrides it with the parent's real pass-1 value.
    Single-hop, same documented scoping limit as serves_object_id above.

    text_content sub-objects (2026-08-20, DETECTION ONLY): every object's own
    `text_content` array (legible text baked into that object's own pixels,
    regardless of `kind` - see schema/blueprint.schema.json) is run through
    resolve_disposition here too, via _resolve_text_content_dispositions, with the
    SAME no-context (None) call resolve_disposition already gets for every top-level
    object at this stage - no run-specific offer/certification/testimonial exists
    yet at deconstruct time, exactly the same reason top-level context-gated
    purposes resolve provisionally here and get RE-resolved at generation time (see
    generate_image_prompt._objects_clause). This is the fix for a live, confirmed
    leak: competitor copy baked into a non-text object never became its own
    kind=='text' object, so resolve_disposition was never consulted on it at all.

    Empty-container override (2026-08-20): a "keep" object whose text_content is
    non-empty and resolves ENTIRELY to "drop" is force-dropped too, rather than
    surviving as a container with nothing left inside it - see this function's
    own inline comment for the live numbered-list case this closes. EXTENDED
    (2026-08-20, Part C) to also count top-level kind=="text" objects that serve
    THIS object via serves_object_id (rather than nesting as a text_content
    child) - the live "empty pink sticky note" case, ad 1746884313351902: the
    sticky (a kind=="graphic" object with NO text_content at all) had its own
    offer text recorded as a SEPARATE object naming the sticky via
    serves_object_id, a shape the text_content-only version of this rule never
    looked at. Every occurrence records a pipeline_warnings row
    ("empty_container_dropped" at THIS resolution point specifically - generate_
    image_prompt._objects_clause uses the distinct "empty_container_dropped_at_
    generation" kind, so a failure in one resolution point is diagnosable
    without auditing the other).

    Prohibited claim phrases (2026-08-20, Part A): resolve_disposition itself
    already forces "drop" for a matching object or text_content sub-object (see
    compliance.PROHIBITED_CLAIM_PATTERNS) - this function additionally records a
    "prohibited_claim_dropped" warning, independently attributable from an
    ordinary drop.

    Linked-text disposition alignment (2026-08-20, Part B): after every object's
    OWN disposition is resolved, a final pass (align_linked_text_dispositions)
    realigns any linked text-object group (serves_object_id links, or objects
    sharing a part_of/serves_object_id parent) to their strictest shared
    disposition - a label and its evidence must always share the same fate.
    Records "linked_text_disposition_aligned" only when a group genuinely
    disagreed and needed realignment."""
    objects = blueprint.get("objects")
    if not isinstance(objects, list):
        return blueprint
    dict_objects = [o for o in objects if isinstance(o, dict)]
    ad_id = blueprint.get("ad_id")
    pass1 = {
        obj.get("object_id"): resolve_disposition(obj)
        for obj in dict_objects
    }
    # Reverse index for Part C: object_id -> [kind=="text" objects whose
    # serves_object_id names it] - a container's own associated text may be
    # linked this way instead of nested as a text_content child.
    served_by = {}
    for obj in dict_objects:
        if obj.get("kind") != "text":
            continue
        served_id = obj.get("serves_object_id")
        if served_id:
            served_by.setdefault(served_id, []).append(obj)

    blueprint = dict(blueprint)
    resolved_objects = []
    for obj in objects:
        if not isinstance(obj, dict):
            resolved_objects.append(obj)
            continue
        served_id = obj.get("serves_object_id")
        served_disposition = pass1.get(served_id) if served_id else None
        part_of_id = obj.get("part_of")
        part_of_disposition = pass1.get(part_of_id) if part_of_id else None
        disposition = resolve_disposition(
            obj, served_object_disposition=served_disposition,
            part_of_parent_disposition=part_of_disposition,
        )
        resolved_obj = {**obj, "disposition": disposition}

        if compliance.prohibited_claim_match(_prohibited_claim_text(obj)):
            _record_deconstruct_warning(
                ad_id, "prohibited_claim_dropped",
                f"object {obj.get('object_id', '?')!r} force-dropped - its own "
                f"wording matches a prohibited efficacy/authority/certification "
                f"claim phrase, never approvable regardless of context.",
            )

        resolved_sub_content = None
        if obj.get("text_content"):
            resolved_sub_content = _resolve_text_content_dispositions(obj["text_content"])
            resolved_obj["text_content"] = resolved_sub_content
            for sub in resolved_sub_content:
                if isinstance(sub, dict) and compliance.prohibited_claim_match(_prohibited_claim_text(sub)):
                    _record_deconstruct_warning(
                        ad_id, "prohibited_claim_dropped",
                        f"text_content sub-object {sub.get('object_id', '?')!r} (on "
                        f"object {obj.get('object_id', '?')!r}) force-dropped - its "
                        f"own wording matches a prohibited efficacy/authority/"
                        f"certification claim phrase.",
                    )

        # Empty-container override: nested text_content children AND/OR
        # serves_object_id-linked text objects (Part C) form ONE combined set of
        # "this container's own textual content" - see this function's own
        # docstring for the exact live case each shape closes.
        combined_child_dispositions = []
        if resolved_sub_content:
            combined_child_dispositions.extend(
                s.get("disposition") for s in resolved_sub_content if isinstance(s, dict)
            )
        serving_texts = served_by.get(obj.get("object_id")) or []
        if serving_texts:
            # pass1 values only (context-free, single-hop) - same documented
            # scoping limit as served_disposition/part_of_disposition above: a
            # serving text object's OWN further relations are not chased here.
            combined_child_dispositions.extend(
                pass1.get(o.get("object_id")) for o in serving_texts
            )
        if resolved_obj["disposition"] == "keep" and combined_child_dispositions and all(
            d == "drop" for d in combined_child_dispositions
        ):
            resolved_obj["disposition"] = "drop"
            _record_deconstruct_warning(
                ad_id, "empty_container_dropped",
                f"object {obj.get('object_id', '?')!r} force-dropped - all "
                f"{len(combined_child_dispositions)} of its own text_content "
                f"sub-object(s) and/or serves_object_id-linked text object(s) "
                f"resolved to 'drop'; rendering it as 'keep' would have produced "
                f"an empty container.",
            )
        resolved_objects.append(resolved_obj)

    # Part B: align linked text-object groups to their strictest shared value.
    disposition_map = {
        o.get("object_id"): o.get("disposition")
        for o in resolved_objects if isinstance(o, dict) and o.get("object_id")
    }
    aligned_map, changed_groups = align_linked_text_dispositions(dict_objects, disposition_map)
    for group_ids, resolved_value in changed_groups:
        _record_deconstruct_warning(
            ad_id, "linked_text_disposition_aligned",
            f"objects {list(group_ids)} disagreed on disposition; aligned to the "
            f"stricter value {resolved_value!r}.",
        )
    if aligned_map:
        resolved_objects = [
            {**o, "disposition": aligned_map[o.get("object_id")]}
            if isinstance(o, dict) and o.get("object_id") in aligned_map and
            aligned_map[o.get("object_id")] != o.get("disposition")
            else o
            for o in resolved_objects
        ]

    blueprint["objects"] = resolved_objects
    return blueprint


def _resolve_text_content_dispositions(text_content, context=None):
    """Runs resolve_disposition over every entry in one object's `text_content` array
    (legible text baked into that object's own rendered pixels - see schema/
    blueprint.schema.json's own docstring for the live leak this closes), returning a
    NEW list - never mutates the caller's own list or its dict entries. Each
    sub-object is treated exactly like a top-level kind=='text' object:
    resolve_disposition dispatches on its own text_purpose/ownership/
    carries_brand_mark fields via the SAME _resolve_text_disposition path, so a
    sub-object with carries_brand_mark true or ownership 'competitor_branded' can
    never resolve to 'keep' - identical enforcement to the top-level case, not a
    weaker or parallel mechanism. context (None at deconstruct time, the real run
    context at generation time - see generate_image_prompt._objects_clause) is
    forwarded unchanged; sub-objects have no serves_object_id/part_of relational
    fields of their own (out of scope - DETECTION ONLY, see the schema field's own
    docstring), so no two-pass handling is needed here."""
    return [
        {**sub, "disposition": resolve_disposition(sub, context)} if isinstance(sub, dict) else sub
        for sub in (text_content or [])
    ]


def resolve_product_group_dispositions(objects):
    """The three-voices product-count fix (2026-08-18, live failure: the OSEA "You'll
    Wish You Went Jumbo" reference - two competitor-branded product objects, both
    individually resolved to "substitute" by resolve_disposition, with nothing
    coordinating them - rendered as two byte-identical SUBSTITUTE bullets in SCENE
    OBJECTS while rule 7 elsewhere in the same prompt unconditionally forbade a second
    bottle. The critic reported neither bottle was ever replaced.

    resolve_disposition decides ONE object's fate in isolation and correctly resolves
    every competitor-branded/brand-marked product object to "substitute" on its own
    terms - that per-object answer was never wrong, it was just never coordinated
    across MULTIPLE product objects in the same scene. This function is that
    coordination, run fresh over the WHOLE objects list rather than stored on the
    blueprint at deconstruct time (unlike resolve_disposition's own answer): the rule
    it applies needs no run-specific context (no offer_text/certifications/
    testimonial), but it does need every product object visible at once, which a
    per-object pure function structurally cannot see.

    THE RULE:
    - Multiple product objects that are the SAME product differing only in size or
      format (linked via same_product_as - see schema/blueprint.schema.json and
      BLUEPRINT_PROMPT above) are multiple INSTANCES of the one authorised Besque
      bottle - ALL of them substitute, matching the reference's own count and layout.
      This is the OSEA case: standard-size and jumbo-size are the same product.
    - Product objects that name no same_product_as and are not named BY one are each
      a GENUINELY DIFFERENT product - when more than one such distinct product (or
      distinct product-group) exists, exactly ONE substitutes (the hero-role one, or
      the first-listed when none is marked hero) and every other one DROPS, so the
      freed space closes into the composition rather than being left empty (see
      _objects_clause's ABSENT wording) instead of a second competitor product
      surviving untouched.

    An object whose disposition has already been explicitly forced to "drop" (e.g. an
    operator's object-removal edit - see generate_image_prompt.blueprint_with_object_
    dropped) is excluded from consideration entirely, never re-admitted into a group
    or counted - a manual removal is a stronger, later decision than this mechanism's
    own grouping judgement.

    Returns {object_id: "substitute"|"drop"} for every kind=="product" object that is
    competitor_branded or carries_brand_mark and not already drop-forced - the same
    "is_branded" predicate resolve_disposition already uses to decide a product
    substitutes at all. A single such object (the common case) always returns
    "substitute" and is never treated as ambiguous. Returns {} when there are none -
    callers fall back to whatever resolve_disposition already produced per-object,
    unaffected by this function existing."""
    objects = [obj for obj in (objects or []) if isinstance(obj, dict)]
    product_objects = [
        obj for obj in objects
        if obj.get("kind") == "product"
        and obj.get("disposition") != "drop"
        and (obj.get("ownership") == "competitor_branded" or bool(obj.get("carries_brand_mark")))
    ]
    if len(product_objects) <= 1:
        return {obj["object_id"]: "substitute" for obj in product_objects if obj.get("object_id")}

    by_id = {obj.get("object_id"): obj for obj in product_objects}

    def root_of(object_id):
        seen = set()
        current = object_id
        while True:
            obj = by_id.get(current)
            same_as = (obj or {}).get("same_product_as")
            if not same_as or same_as not in by_id or same_as in seen:
                return current
            seen.add(current)
            current = same_as

    groups = {}
    for obj in product_objects:
        root = root_of(obj.get("object_id"))
        groups.setdefault(root, []).append(obj)

    if len(groups) <= 1:
        return {obj["object_id"]: "substitute" for obj in product_objects if obj.get("object_id")}

    # More than one distinct product - exactly one group wins outright, never a guess
    # from colour/size/description text. Prefer a hero-role group; otherwise the
    # first-listed group (object list order) - a deterministic, code-level tiebreak,
    # not a model judgement call.
    def group_sort_key(root):
        members = groups[root]
        has_hero = any(m.get("role") == "hero" for m in members)
        first_index = min(product_objects.index(m) for m in members)
        return (0 if has_hero else 1, first_index)

    winning_root = min(groups, key=group_sort_key)
    result = {}
    for root, members in groups.items():
        disposition = "substitute" if root == winning_root else "drop"
        for member in members:
            object_id = member.get("object_id")
            if object_id:
                result[object_id] = disposition
    return result


def resolve_testimonial_dispositions(objects, context=None):
    """Duplicate-testimonial guard, restored 2026-08-19 (audit-confirmed gap, CONFIRMED
    LIVE: a real draft rendered the identical review, "Nice and smooth... - Margaret
    P.", in two separate boxes). The deleted test was test_structural_zones_clause_
    testimonial_renders_exactly_once_across_two_zones - the old mechanism was a local
    `testimonial_placed` boolean inside _structural_zones_clause (6b82f60~1:src/
    generate_image_prompt.py), set True the first time a social_proof/single_quote
    zone actually substituted; every zone reached afterward fell to the else branch
    and was force-removed regardless of its own kind. _substitute_object_line's
    testimonial branch has NO equivalent state today - two independent kind=="text",
    text_purpose=="testimonial" objects each independently pass _resolve_text_
    disposition's context-gate check and BOTH resolve "substitute", because that
    check (like resolve_product_group_dispositions' predecessor bug) only ever looks
    at ONE object at a time. This function is the same fix shape as resolve_product_
    group_dispositions: coordinate across the WHOLE objects list, not per-object.

    Same session also restores structural_zones[].social_proof_kind's aggregate_bar
    vs single_quote distinction (Task 3) as objects[].social_proof_kind ("aggregate"
    vs "single_quote", default/absent treated as single_quote for back-compat with
    every blueprint predating this field) - an aggregate-shaped object (a review-
    count/star-average bar, e.g. "Rated 4.8 by 12,000 customers") is NEVER eligible
    to win the one substitute slot, exactly matching the old code's own social_proof_
    kind branch: only "single_quote" could ever substitute, anything else (including
    "unspecified kind") always removed. Besque has no approved aggregate figure to
    substitute an aggregate bar WITH (see CLAUDE.md: "A published review-count/
    average is HELD pending Harry") - this is a compliance backstop, not just a
    dedup convenience.

    context carries the same {"testimonial": {...}} shape resolve_disposition's own
    context-gated purposes already use - context=None/no real testimonial supplied
    means EVERY testimonial-purposed object drops (nothing to substitute with),
    identical to the pre-existing single-object behaviour for this case.

    Deliberately does NOT filter on the object's own stored `disposition` the way
    resolve_product_group_dispositions filters out an already-drop-forced product:
    for a context-gated purpose, the STORED value is "drop" by default for every
    testimonial object regardless of any operator action (deconstruct time always
    runs with context=None, per resolve_disposition's own dual-resolution design) -
    it carries no information distinguishing "deconstruct's context-free default"
    from "an operator's manual object-removal edit." resolve_disposition's own
    context-gated branch already had this exact same limitation for the single-
    testimonial case before this function existed (it recomputes from context alone,
    never consulting the stored field either) - this function preserves that,
    unchanged, while fixing the actual bug in scope: coordination ACROSS objects.

    Returns {object_id: "substitute"|"drop"} for every kind=="text", text_purpose==
    "testimonial" object. At most ONE entry is ever "substitute" - the first-listed
    object that is not aggregate-shaped, when a real testimonial was actually
    supplied this run. Returns {} when there are no testimonial-purposed objects at
    all - callers fall back to whatever resolve_disposition already produces
    per-object, unaffected by this function existing."""
    objects = [obj for obj in (objects or []) if isinstance(obj, dict)]
    context = context or {}
    testimonial_objects = [
        obj for obj in objects
        if obj.get("kind") == "text" and obj.get("text_purpose") == "testimonial"
    ]
    if not testimonial_objects:
        return {}

    has_real_testimonial = bool((context.get("testimonial") or {}).get("quote"))
    result = {}
    winner_assigned = False
    for obj in testimonial_objects:
        object_id = obj.get("object_id")
        if not object_id:
            continue
        is_aggregate = obj.get("social_proof_kind") == "aggregate"
        if not is_aggregate and has_real_testimonial and not winner_assigned:
            result[object_id] = "substitute"
            winner_assigned = True
        else:
            result[object_id] = "drop"
    return result


# LINKED-TEXT DISPOSITION ALIGNMENT (2026-08-20, Part B of the text-layer
# completion task): live evidence, ad 1357229623024367-shaped - a competitor
# bullet split into a LABEL ("Clinically Proven") and its EVIDENCE ("95% saw
# results by week 6") as two separate objects. The evidence's own number tripped
# the stat-shape check and dropped; the label had no number in it at all and
# independently resolved to substitute (or, after Part A, drop only if it itself
# matches a prohibited phrase - a label that happens to say something else
# entirely would still survive alone). Per-object resolution is correct on each
# object's OWN terms - the bug is that a label and its evidence are not
# independent claims, they are ONE claim split across two objects, and
# resolve_disposition has no way to see that from either object alone (same
# "needs the whole list" structural gap resolve_product_group_dispositions/
# resolve_testimonial_dispositions already exist to close for their own cases).
_DISPOSITION_STRICTNESS = {"drop": 0, "substitute": 1, "keep": 2}


def _text_object_link_groups(objects):
    """Union-find grouping of kind=="text" objects that are linked - directly via
    serves_object_id/part_of naming ANOTHER text object, or indirectly by sharing
    the same non-null part_of value or the same non-null serves_object_id value
    (siblings under one bullet/row/icon, e.g. a label and its evidence both
    serving the same icon object). Only kind=="text" objects are ever grouped -
    a prop or icon that a text object serves is the thing being LABELLED, not a
    second claim needing disposition alignment; grouping it in would conflate
    this mechanism with the unrelated part_of/serves_object_id inheritance rules
    resolve_disposition already has. Returns a list of groups (each a list of
    object_ids), one entry per group with 2 or more members - a lone, unlinked
    text object is never included."""
    text_ids = {
        obj.get("object_id") for obj in objects
        if isinstance(obj, dict) and obj.get("kind") == "text" and obj.get("object_id")
    }
    by_id = {obj.get("object_id"): obj for obj in objects if isinstance(obj, dict)}
    parent = {oid: oid for oid in text_ids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        if a not in parent or b not in parent:
            return
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for oid in text_ids:
        obj = by_id[oid]
        served = obj.get("serves_object_id")
        if served in text_ids:
            union(oid, served)
        part_of_id = obj.get("part_of")
        if part_of_id in text_ids:
            union(oid, part_of_id)

    by_shared_target = {}
    for oid in text_ids:
        obj = by_id[oid]
        for key in ("part_of", "serves_object_id"):
            target = obj.get(key)
            if target:
                by_shared_target.setdefault(target, []).append(oid)
    for siblings in by_shared_target.values():
        for other in siblings[1:]:
            union(siblings[0], other)

    groups = {}
    for oid in text_ids:
        groups.setdefault(find(oid), []).append(oid)
    return [members for members in groups.values() if len(members) > 1]


def align_linked_text_dispositions(objects, dispositions):
    """Realigns linked text-object groups (see _text_object_link_groups) to their
    STRICTEST shared disposition (drop > substitute > keep) whenever they
    disagree - a label and its evidence must always share the same fate; a
    competitor claim does not survive just because only its proof was replaced.

    dispositions: {object_id: disposition}, the ALREADY per-object-resolved map
    (from either resolution point - deconstruct time or generate_image_prompt.
    _objects_clause) - this function only re-groups and re-strictens, it never
    computes an object's OWN disposition from scratch.

    Returns (updated_dispositions, changed_groups) - updated_dispositions is a
    NEW dict (the input is never mutated), changed_groups is a list of
    (member_object_ids, resolved_disposition) for exactly the groups that
    actually disagreed and needed realignment - already-unanimous groups are
    left untouched and never appear here, so a caller using this to decide
    whether to record_warning only ever warns on a REAL disagreement."""
    updated = dict(dispositions)
    changed = []
    for group in _text_object_link_groups(objects):
        values = {updated.get(oid) for oid in group}
        values.discard(None)
        if len(values) <= 1:
            continue
        strictest = min(values, key=lambda v: _DISPOSITION_STRICTNESS.get(v, 99))
        for oid in group:
            updated[oid] = strictest
        changed.append((tuple(group), strictest))
    return updated, changed


# HALLUCINATED TEXT OBJECT FILTER (2026-08-19): deconstruct_image (below) attaches the
# ad's scraped Facebook caption to the SAME API call as the image (see the "Scraped ad
# copy" paragraph in BLUEPRINT_PROMPT above), stated as the source of truth for headline
# and offer wording where it conflicts with the image - but until this session nothing in
# that prompt said objects[] may describe ONLY what is visibly present in the attached
# image, as distinct from that scraped copy block. Confirmed live on two artifacts (1377,
# 1386, structural shapes reproduced in tests/test_hallucinated_text_objects.py): the
# model folded the scraped caption's testimonial/CTA text into objects[] entries with a
# defaulted full-frame bbox ([0.0, 0.0, 1.0, 1.0]), while the SAME blueprint's own
# layout_detail.text_zone/legibility_notes correctly recorded "no text is overlaid on the
# image itself." The BLUEPRINT_PROMPT wording above is fixed alongside this to reduce
# recurrence at the source, but per this file's own standing lesson ("prompt-only rules
# do not bind on the image path" - CLAUDE.md) that wording is not the enforcement; this
# function is.
FULLFRAME_TEXT_BBOX_AREA_THRESHOLD = 0.9

_NO_IN_IMAGE_TEXT_SIGNAL_RE = re.compile(
    r"no in-image text|no text is overlaid|none in-image|external to (the )?image|"
    r"external ad (body )?text|external ad copy|external, not in-image|"
    r"delivered as external|not (baked|overlaid) (into|on) the image|all copy is (external|delivered)",
    re.IGNORECASE,
)


def _text_object_bbox_area(bbox):
    if not (isinstance(bbox, (list, tuple)) and len(bbox) == 4):
        return None
    try:
        _, _, w, h = bbox
        return float(w) * float(h)
    except (TypeError, ValueError):
        return None


def blueprint_signals_no_in_image_text(blueprint):
    """True when layout_detail.text_zone or legibility_notes states, in free text, that
    no text is baked into the image itself (e.g. "external to image", "no text is
    overlaid on the image itself"). Keyword-matched against free text the model writes,
    the same fragility tradeoff as every other free-text signal this codebase already
    keys behaviour off (e.g. output_critic's testimonial-shaped-category match) - a false
    negative (wording this doesn't recognise) leaves the contradiction check silent for
    that one blueprint; both real instances found so far (1377, 1386) use near-identical
    phrasing to each other, which is why this is one signal, not the only one -
    drop_hallucinated_text_objects's bbox check below is independent of this."""
    blueprint = blueprint or {}
    layout_detail = blueprint.get("layout_detail") or {}
    text_zone = str(layout_detail.get("text_zone") or "")
    legibility_notes = str(blueprint.get("legibility_notes") or "")
    return bool(_NO_IN_IMAGE_TEXT_SIGNAL_RE.search(f"{text_zone} || {legibility_notes}"))


def drop_hallucinated_text_objects(blueprint):
    """Filters blueprint.objects[] down to the objects eligible to reach
    build_image_prompt, dropping any kind=="text" object matching EITHER of two
    INDEPENDENT signals that it describes text from the ad's external scraped caption
    copy rather than something visibly rendered in the reference image:

    1. full_frame_bbox: the object's own bbox covers >= FULLFRAME_TEXT_BBOX_AREA_THRESHOLD
       of the frame - a real in-image text block (a headline, a CTA button, a testimonial
       card) occupies a bounded region; a hallucinated one, with no real pixels to anchor
       to, was observed defaulting to the whole frame ([0.0, 0.0, 1.0, 1.0]) on both known
       instances.
    2. contradicts_no_in_image_text: blueprint_signals_no_in_image_text(blueprint) is
       True - the SAME blueprint's own layout_detail.text_zone/legibility_notes state
       that no text is baked into the image at all, directly contradicting the existence
       of ANY kind=="text" object. When this fires, EVERY kind=="text" object in the
       blueprint is dropped, not just the one(s) also caught by the bbox check - a
       blueprint-level "no in-image text exists" statement makes every one of them
       suspect, not only the most obviously oversized one.

    Deliberately two independent checks, not one merged condition: a future edit to
    either one must not silently leave the other as the only cover. Both known real
    instances happen to trip both signals at once, but there is no guarantee a future
    hallucinated object will always be full-frame AND have a contradicting text_zone -
    e.g. a hallucinated headline placed at a plausible, non-full-frame bbox despite
    text_zone correctly saying "external" is exactly the case signal 2 exists to still
    catch without signal 1.

    Returns (kept_objects, dropped) - kept_objects is a NEW list (the input list/dicts
    are never mutated), dropped is a list of {"object_id", "description", "reasons"}
    dicts, one per removed object, reasons a list containing "full_frame_bbox" and/or
    "contradicts_no_in_image_text". Pure function, no logging/DB access - a caller with
    DB access (generate_image, pipeline._regenerate_existing_draft) calls this itself to
    record_warning on a non-empty `dropped`; build_image_prompt also calls this directly
    so the invariant ("a hallucinated text object never reaches the built prompt") holds
    unconditionally regardless of whether a caller remembers to check - meaning this runs
    twice on the normal generate path. That's cheap (a list scan, no API call), the same
    recompute-fresh-never-trust-a-cached-call tradeoff already made by
    resolve_testimonial_dispositions/resolve_product_group_dispositions above."""
    objects = [obj for obj in ((blueprint or {}).get("objects") or []) if isinstance(obj, dict)]
    no_text_signal = blueprint_signals_no_in_image_text(blueprint)
    kept, dropped = [], []
    for obj in objects:
        if obj.get("kind") != "text":
            kept.append(obj)
            continue
        reasons = []
        area = _text_object_bbox_area(obj.get("bbox"))
        if area is not None and area >= FULLFRAME_TEXT_BBOX_AREA_THRESHOLD:
            reasons.append("full_frame_bbox")
        if no_text_signal:
            reasons.append("contradicts_no_in_image_text")
        if reasons:
            dropped.append({
                "object_id": obj.get("object_id"),
                "description": obj.get("description"),
                "reasons": reasons,
            })
        else:
            kept.append(obj)
    return kept, dropped


def _assert_no_competitor_branded_object_kept(blueprint):
    """Defence-in-depth self-check, not the primary enforcement mechanism -
    resolve_disposition's own logic already guarantees this invariant by construction
    (it never returns "keep" for a competitor_branded or brand-mark-carrying object), so
    this should never actually fire in practice. It exists anyway because "prompt-only
    rules have never bound on the image path" (this file's own standing lesson) applies
    exactly as much to a bug in OUR OWN enforcement code as to a model instruction - if
    resolve_disposition is ever changed in a way that reintroduces this exact gap, this
    raises loudly at deconstruct time rather than letting a competitor-branded object
    quietly reach image generation with disposition="keep".

    2026-08-19: names the object's `brand` (per-object brand identity field), when
    present, in the raised message - purely a clearer log line for whoever reads this
    if it ever actually fires; brand plays no role in the check itself (that stays
    ownership/carries_brand_mark, unchanged) and this is not a new enforcement path."""
    for obj in blueprint.get("objects") or []:
        if not isinstance(obj, dict):
            continue
        is_branded = obj.get("ownership") == "competitor_branded" or bool(obj.get("carries_brand_mark"))
        if is_branded and obj.get("disposition") == "keep":
            brand = obj.get("brand")
            brand_note = f" (brand={brand!r})" if brand else ""
            raise BlueprintValidationError(
                f"Object {obj.get('object_id', '?')!r}{brand_note} is competitor-branded but "
                f"resolved to disposition='keep' - this must never happen; resolve_disposition "
                f"has a bug.",
                "competitor_branded object resolved to keep",
            )
        # text_content sub-objects (2026-08-20, DETECTION ONLY): the SAME check,
        # extended to the new leak vector this task closes - a brand mark baked into
        # a non-text object's own pixels must never survive as 'keep' either.
        for sub in obj.get("text_content") or []:
            if not isinstance(sub, dict):
                continue
            sub_branded = sub.get("ownership") == "competitor_branded" or bool(sub.get("carries_brand_mark"))
            if sub_branded and sub.get("disposition") == "keep":
                raise BlueprintValidationError(
                    f"Text sub-object {sub.get('object_id', '?')!r} on object "
                    f"{obj.get('object_id', '?')!r} is competitor-branded but resolved to "
                    f"disposition='keep' - this must never happen; resolve_disposition has a bug.",
                    "competitor_branded text sub-object resolved to keep",
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

# TRUNCATION RETRY (2026-08-20): a THIRD independent failure class, distinct from both
# budgets above - four of five ads failed deconstruct in one day, all deterministic
# (the same two ad_ids failed identically at 10:32/10:34 and again at 11:27/11:29, not
# transient), traced to stop_reason=='max_tokens' at output_tokens=4096, dying mid-JSON
# around obj_20/obj_21 (char ~12,300-12,900). A truncated response is NOT malformed JSON
# - it is genuinely incomplete - so JSON_ESCAPE_SYSTEM's retry (which only nudges quote
# escaping) can never fix it; routing a truncation through that retry burns the ONE
# parse/validation attempt on a fix that cannot possibly work. Detected explicitly via
# message.stop_reason, BEFORE deconstruct_image's own parse/validation try/except ever
# runs, and given its OWN small retry budget (escalate max_tokens once, never touch
# `system` or _MAX_DECONSTRUCT_ATTEMPTS' counter) - same reasoning as
# _call_claude_with_transient_retry's own separate budget for a different failure class.
_MAX_TRUNCATION_ATTEMPTS = 2
# Base ceiling raised 4096 -> 16384 (Part B added ~200-350 tokens of extra fields on top
# of an already-migrated per-object schema; a dense ad's objects[] array - see the
# KilgourMD field-count/token-cost report in this fix's commit message - is what's
# actually driving length, not any single top-level field). Escalated ceiling is used
# ONLY on a stop_reason=='max_tokens' retry, doubled again. Neither number is empirically
# measured against Anthropic's own hard output-token ceiling for this model - a stopgap,
# per this same fix's own report on why bounding the OUTPUT (fewer fields per non-hero
# object) is the durable direction, not a bigger number.
_DECONSTRUCT_MAX_TOKENS = 16384
_DECONSTRUCT_MAX_TOKENS_ESCALATED = 32768
# 60s -> 180s: proportional to the 4x max_tokens raise above (4096 -> 16384) - the two
# live APITimeoutError failures were on ads of the same density as the two max_tokens
# truncations, so the same underlying "this response takes longer to generate than a
# smaller one" cause is the likely explanation for both symptoms, not two unrelated
# bugs. Not empirically measured against a real dense-ad generation time either -
# raised proportionally to the token increase as the same reasoned-not-measured margin
# the existing max_tokens comment already admits to.
_DECONSTRUCT_TIMEOUT_SECONDS = 180.0


class DeconstructTruncatedError(RuntimeError):
    """Raised by _fetch_deconstruct_message when Claude's deconstruct response is cut
    off by max_tokens on every truncation-retry attempt, up to and including
    _DECONSTRUCT_MAX_TOKENS_ESCALATED - the objects inventory for this ad does not fit
    even the escalated ceiling. Never raised for a malformed-but-complete response
    (that's BlueprintValidationError or a plain JSON parse error, handled by
    deconstruct_image's own retry loop) - this is specifically "the response never
    finished," which no JSON-escaping or schema-correction nudge can fix."""


def _record_deconstruct_warning(ad_id, kind, detail):
    """pipeline_warnings row for a deconstruct failure that would otherwise be an
    ERROR-only log line - process_ad's own outer except (src/pipeline.py) catches
    every deconstruct_image exception and returns "failed" with nothing else recorded,
    so an ad silently vanishes from a batch with no signal anywhere the dashboard
    reads from. Lazy-imported so the common case (deconstruct never fails) never
    touches the DB, matching this codebase's established pattern for a rare-path-only
    DB write (see generate_image_prompt.py's own hallucinated-text-object warning)."""
    from src import dedupe as _dedupe
    _dedupe.init_pipeline_warnings()
    _dedupe.record_warning(kind, f"Ad {ad_id}: {detail}")

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


def _fetch_deconstruct_message(client, base_kwargs, ad_id):
    """Get one Claude response for deconstruct, retrying ONLY on stop_reason==
    'max_tokens' (a truncated response), with its own small budget
    (_MAX_TRUNCATION_ATTEMPTS) independent of _MAX_TRANSIENT_ATTEMPTS (network
    failures) and _MAX_DECONSTRUCT_ATTEMPTS (parse/validation failures) - three
    separate failure classes, three separate budgets, never sharing a counter or a
    `system` prompt mutation with each other (see this module's own truncation-retry
    comment above for why routing a truncation through either of the other two
    cannot succeed).

    base_kwargs must NOT include "max_tokens" - this function sets it, starting at
    _DECONSTRUCT_MAX_TOKENS and escalating to _DECONSTRUCT_MAX_TOKENS_ESCALATED on a
    truncation retry. Every attempt still goes through
    _call_claude_with_transient_retry, so a network failure on the SAME call is still
    handled by that separate mechanism.

    Raises DeconstructTruncatedError (after recording a pipeline_warnings row) if
    every attempt truncates. Returns the message unchanged (whatever its stop_reason)
    the first time one attempt does NOT truncate - a non-'max_tokens' stop_reason is
    deconstruct_image's own problem to parse/validate, not this function's."""
    max_tokens = _DECONSTRUCT_MAX_TOKENS
    for attempt in range(1, _MAX_TRUNCATION_ATTEMPTS + 1):
        kwargs = dict(base_kwargs, max_tokens=max_tokens)
        message = _call_claude_with_transient_retry(client, kwargs, ad_id)
        if getattr(message, "stop_reason", None) != "max_tokens":
            return message
        raw_text = message.content[0].text if message.content else ""
        usage = getattr(message, "usage", None)
        log.error(
            "deconstruct truncated for ad %s (attempt %s/%s): stop_reason='max_tokens' "
            "max_tokens=%s output_tokens=%s input_tokens=%s raw_text len=%s chars, "
            "tail: %r",
            ad_id, attempt, _MAX_TRUNCATION_ATTEMPTS, max_tokens,
            getattr(usage, "output_tokens", "?"), getattr(usage, "input_tokens", "?"),
            len(raw_text), raw_text[-300:],
        )
        if attempt == _MAX_TRUNCATION_ATTEMPTS:
            _record_deconstruct_warning(
                ad_id, "deconstruct_truncated",
                f"response truncated (stop_reason='max_tokens') on every attempt up to "
                f"max_tokens={max_tokens} - the objects inventory did not fit even the "
                f"escalated ceiling. output_tokens={getattr(usage, 'output_tokens', '?')}, "
                f"raw_text len={len(raw_text)} chars. Not retried further - the response "
                f"is genuinely incomplete, not malformed, so no further retry of this "
                f"shape can succeed.",
            )
            raise DeconstructTruncatedError(
                f"Ad {ad_id}: deconstruct response truncated (stop_reason='max_tokens') "
                f"on every attempt up to max_tokens={max_tokens}"
            )
        log.warning(
            "deconstruct ad %s: retrying with escalated max_tokens=%s after truncation "
            "at max_tokens=%s",
            ad_id, _DECONSTRUCT_MAX_TOKENS_ESCALATED, max_tokens,
        )
        max_tokens = _DECONSTRUCT_MAX_TOKENS_ESCALATED


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

    The raw API call itself goes through _fetch_deconstruct_message on every one of the
    attempts above, which in turn goes through _call_claude_with_transient_retry - TWO
    further separate, independently-capped retries beneath the parse/validation one
    here: a truncation retry (stop_reason=='max_tokens', escalates max_tokens, see
    _fetch_deconstruct_message's own docstring) and a network-failure retry (timeout/
    connection error, or a 429/5xx from Anthropic's own infrastructure, see
    _call_claude_with_transient_retry's own docstring). Neither budget interacts with
    the parse/validation one here or with each other: each failure is retried silently
    within its own layer and never reaches a caller's except block at all unless its
    own budget is exhausted, at which point it propagates out of that layer exactly as
    an unretried failure of that kind did before it existed - never treated as one of
    the OTHER two failure classes, and never consuming one of their attempts. On
    exhaustion of ANY of the three budgets, a pipeline_warnings row is recorded naming
    the ad and the specific failure mode (deconstruct_truncated or deconstruct_failed)
    before the exception propagates - an ad that fails deconstruct no longer vanishes
    from a batch with only an ERROR-level log line."""
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
    # timeout=_DECONSTRUCT_TIMEOUT_SECONDS (2026-08-20, was 60.0 - see that constant's
    # own comment for why raised proportionally to the max_tokens increase below).
    client = anthropic.Anthropic(timeout=_DECONSTRUCT_TIMEOUT_SECONDS, max_retries=0)  # reads ANTHROPIC_API_KEY from env
    total = _MAX_DECONSTRUCT_ATTEMPTS
    system = None
    for attempt in range(1, total + 1):
        kwargs = {
            "model": CLAUDE_MODEL,
            "temperature": DECONSTRUCT_TEMPERATURE,
            "messages": [{"role": "user", "content": content}],
        }
        if system:
            kwargs["system"] = system
        # max_tokens is set INSIDE _fetch_deconstruct_message (starting at
        # _DECONSTRUCT_MAX_TOKENS, escalating only on its own truncation-specific
        # retry) - never passed here, so this loop's parse/validation retry can never
        # accidentally reset it back down after an escalation.
        message = _fetch_deconstruct_message(client, kwargs, ad_id)
        raw_text = ""
        try:
            raw_text = message.content[0].text if message.content else ""
            return deconstruct_from_response(raw_text)
        except BlueprintValidationError as e:
            log.error("deconstruct schema validation failed for ad %s (attempt %s/%s): %s",
                      ad_id, attempt, total, e.validation_error)
            if attempt == total:
                _record_deconstruct_warning(
                    ad_id, "deconstruct_failed",
                    f"schema validation failed on every attempt ({total}): "
                    f"{e.validation_error}",
                )
                raise
            log.warning("retrying deconstruct for ad %s with the validation error appended "
                        "as a correction instruction", ad_id)
            system = _validation_retry_system(e.validation_error)
        except Exception as e:
            _log_parse_failure(attempt, total, message, raw_text, e)
            if attempt == total:
                _record_deconstruct_warning(
                    ad_id, "deconstruct_failed",
                    f"response could not be parsed as JSON on every attempt ({total}): "
                    f"{type(e).__name__}: {e}",
                )
                raise
            log.warning("retrying deconstruct for ad %s with a JSON-escaping system prompt nudge", ad_id)
            system = JSON_ESCAPE_SYSTEM
