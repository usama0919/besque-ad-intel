"""Deconstruction step: send an ad image to Claude and get a structured blueprint."""
import os
import json
import base64
from pathlib import Path

from src import json_response, validator

# Model + key are read from env so the real key plugs in at kickoff.
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")

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
- cta (string): the call to action
- destination_url (string): use the value "{destination_url}"
- headline_verbatim (string): the exact main headline text in the image, or "" if none
- offer (object): {{ "type": ..., "value": ..., "mechanic": ... }} or null if no offer
- social_proof (object): {{ "type": ..., "owner": ... }} — owner is the brand/body the proof belongs to, or null
- layout_detail (object): {{ "text_zone": ..., "product_count": number, "background_type": ..., "zone_positions": array of short phrases locating each element top to bottom (e.g. ["headline top-center", "product mid-frame", "CTA bottom-full-width"]), "has_bottom_banner": true/false, "has_corner_badge": true/false, "frame_division": short description of how the frame splits (e.g. "three stacked horizontal bands" or "single uninterrupted gradient ground, no hard divisions") }}
- legibility_notes (string): whether in-image text is readable at feed size
- body_area_shown (string): if a human subject appears in the image, name the specific body region shown or emphasised (e.g. "legs", "arms", "torso", "hands", "neck and décolletage"); if NO human subject appears at all (e.g. a product-only shot, an illustration/diagram, or text-only creative), use exactly "none". This is read downstream to decide whether a per-run body-area instruction may be applied to this reference at all - do not guess a body part onto a productless or human-less image.
- creative_objective (string): the ad's primary strategic goal in one short phrase, e.g. "drive urgency around a limited-time offer" or "build trust via a testimonial"
- target_audience (string): who this ad is speaking to, in one short phrase, e.g. "women 40+ concerned about skin texture and firmness"
- typography (object): {{ "headline_face": typeface style e.g. serif/sans/script, "headline_weight": e.g. bold/light/regular, "hierarchy_levels": array of short phrases describing each distinct text tier top to bottom (e.g. ["large bold serif headline", "medium sans subhead", "small CTA button label"]), "case_treatment": e.g. "all caps headline, sentence case body" }}
- typography_zones (array): one entry PER DISTINCT TEXT ZONE in the image (brand logo, headline, sub-copy, offer/CTA, badge text - every zone that carries its own visible text, not just the headline). This is PER-ZONE detail; the `typography`/`hierarchy_levels` fields above describe the ad's typography in general prose - this field must actually enumerate each zone so the treatment isn't lost. Each entry: {{ "zone": short label matching a zone_positions phrase above (e.g. "headline upper-right"), "typeface_class": serif/sans/script, "weight": e.g. bold/light/regular, "case": upper/title/sentence, "letter_spacing": tight/normal/wide, "colour": this zone's OWN text colour - distinct from the scene's overall palette_mood, e.g. "gold" or "white", "size_relative": e.g. large/medium/small relative to the frame, "decorative_elements": array of short phrases for anything attached to the zone (a pipe divider, a rule, an underline, a bullet mark) or [] if none, "line_count": number of lines this zone actually occupies }}. A reference commonly has THREE OR FOUR distinct typographic levels (e.g. a large serif headline, a small-caps accent line with wide letter-spacing, small body copy, a button label) - give each its own entry; never collapse two visually distinct levels into one, and never omit a zone just because its text is short.
- structural_zones (array): every occurrence of these NINE structural zone types. Described by what each type IS STRUCTURALLY, never by which brand happens to be in front of you right now - this must generalise to any ad, any category, any layout. Zero, one, or several entries of the same zone_type are all valid: if a type doesn't appear at all, it simply has no entries; if an ad has two badges, return two entries with zone_type "badge". An ad with none of these nine zones returns an empty array - do not force a fit. If a zone_type is NOT present, OMIT it entirely - do not add an entry for it just to say it is absent (e.g. never write a sub_line entry whose detail says "no explicit sub-line"). An entry in this array means the zone EXISTS; anything downstream that reads this array will treat every entry as real and try to act on it, so a placeholder entry describing an absence would be read as a zone that is actually there. Each entry: {{ "zone_type": one of brand_wordmark/sub_line/body_copy/cta/price_anchor/product_callout/badge/social_proof/disclaimer, "position": short phrase locating it (e.g. "top-center", "bottom-right banner"), "container": one of none/oval/rect/banner/ribbon/other - the shape holding it, "detail": a short structural description specific to what this zone_type needs (see below), "social_proof_kind": one of aggregate_bar/single_quote - ONLY set when zone_type is social_proof, omit or null otherwise }}.
    brand_wordmark = the advertiser's own logo or name mark, distinct from a product's own printed label if a product is shown
    sub_line = a short accent line below the headline (e.g. a tagline, a small-caps line with wide letter-spacing) - a DISTINCT in-scene text zone from the headline itself, not a second name for it
    body_copy = a paragraph or multi-line block of supporting text rendered IN the image itself - NEVER the scraped ad_text/primary_text supplied separately as a text block above; that is off-image Facebook copy, not part of the visual, and must never be reported here
    cta = a call-to-action button or label rendered in the image as its own zone (the ad's own `cta` field above names the ACTION being asked for; this names the zone/container it appears in, if any)
    price_anchor = a price shown as its own graphic element - detail should say whether it's a single price or a struck-through original/new pair (e.g. "single price, £29" or "struck-through pair: £200 then £29")
    product_callout = a small card or panel calling out a specific product/variant (e.g. a "New Scent" card) - detail should say exactly what it carries: a name, a descriptor line, a colour swatch, a thumbnail image, any combination of these
    badge = a discrete graphic badge, seal, or roundel - detail should name its actual content (e.g. "reads NEW", "star rating icon", "%-off roundel", "award/certification seal")
    social_proof = a testimonial or aggregate-review element rendered in the image - social_proof_kind distinguishes an AGGREGATE BAR (a review count + star average, e.g. "Trustpilot · Over 30,000 · ★★★★★") from a SINGLE QUOTE (one customer's words plus attribution), since these need different treatment downstream; set social_proof_kind for every social_proof entry, never leave it unset when this zone_type is used
    disclaimer = fine-print or footnote text (e.g. "*T&Cs apply", a small legal line)
- production_style (object): {{ "style": one of {production_style_options}, "confidence": high/medium/low, "signals": array of short phrases justifying the choice }}
    ugc_native = phone-camera framing, natural/available light, real hands or skin, imperfect staging
    high_spec_studio = controlled premium lighting, deliberate composition, macro texture, editorial typography
    hybrid = studio-grade product quality inside casual framing (e.g. hero-lit product on a real countertop, or polished product with handwritten annotation). Only choose hybrid when both are genuinely present — do not use it as a hedge when uncertain.
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


def deconstruct_from_response(raw_text: str) -> dict:
    """Parse and validate a blueprint from a raw model response. Raises if invalid."""
    blueprint = parse_blueprint(raw_text)
    err = validator.validation_error(blueprint)
    if err:
        raise ValueError(f"Blueprint failed schema validation: {err}")
    return blueprint

# ---- Live Claude vision call (wired at kickoff) ----
import base64
import mimetypes
import anthropic


def _load_image_b64_v2(image_path):
    with open(image_path, "rb") as f:
        data = f.read()
    media_type = ("image/png" if data[:8]==b"\x89PNG\r\n\x1a\n" else "image/webp" if data[:4]==b"RIFF" and data[8:12]==b"WEBP" else "image/gif" if data[:4]==b"GIF8" else "image/jpeg")
    return base64.standard_b64encode(data).decode("utf-8"), media_type


def _b64_from_bytes(data):
    media_type = ("image/png" if data[:8]==b"\x89PNG\r\n\x1a\n" else "image/webp" if data[:4]==b"RIFF" and data[8:12]==b"WEBP" else "image/gif" if data[:4]==b"GIF8" else "image/jpeg")
    return base64.standard_b64encode(data).decode("utf-8"), media_type


def deconstruct_image(image_bytes, ad_id, source_page, captured_at, destination_url="", ad_text="", cta=""):
    """Send one ad image to Claude vision and return a validated blueprint dict.
    Makes ONE API call. Raises if the response fails schema validation."""
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

    client = anthropic.Anthropic(timeout=60.0, max_retries=1)  # reads ANTHROPIC_API_KEY from env
    message = client.messages.create(
        model=CLAUDE_MODEL,
        # Part B added creative_objective/target_audience/typography (4 sub-fields) and
        # expanded layout_detail (4 more sub-fields, one an array) on top of the existing
        # ~15-field blueprint - estimated +200-350 tokens for the fuller JSON response.
        # 3072 -> 4096 is a reasoned safety margin, NOT an empirically measured fix (no
        # real ad image / API call was run to confirm truncation in this change). If a
        # blueprint response is later seen truncated (a JSON parse failure, or
        # message.stop_reason == "max_tokens"), raise further or add a retry ladder
        # mirroring generate_copy.py's (3072, None)/(8192, JSON_ONLY_SYSTEM) pattern.
        max_tokens=4096,
        messages=[{
            "role": "user",
            "content": content,
        }],
    )
    raw_text = message.content[0].text
    return deconstruct_from_response(raw_text)
