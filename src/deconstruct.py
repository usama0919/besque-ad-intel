"""Deconstruction step: send an ad image to Claude and get a structured blueprint."""
import os
import json
import base64
import logging
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
- visual (object): {{ "layout": ..., "subject": ..., "palette_mood": ..., "text_placement": ..., "scene_lighting": {{ "light_direction": where the dominant light source actually falls in THIS image relative to the camera, e.g. "upper-left, slightly behind camera" or "overhead, direct", "hardness": "hard" (a distinct, sharp-edged shadow) or "soft" (a diffuse, low-contrast shadow), "colour_temperature": e.g. "warm/golden", "neutral daylight", "cool/blue-tinted fluorescent", "shadow_behaviour": where shadows actually fall and how strong they are, "grain": e.g. "clean, no visible grain" or "visible phone-camera noise/grain", "depth_of_field": e.g. "shallow, background softly blurred" or "deep, background in focus" }} - these six fields are OBSERVATIONS of what THIS reference image's own lighting actually looks like, exactly as a photographer describing an existing photo would, never a style label and never a description of what the lighting SHOULD be. }}
- cta (string): the call to action
- destination_url (string): use the value "{destination_url}"
- headline_verbatim (string): the exact main headline text in the image, or "" if none
- offer (object): {{ "type": ..., "value": ..., "mechanic": ... }} or null if no offer
- social_proof (object): {{ "type": ..., "owner": ... }} — owner is the brand/body the proof belongs to, or null
- layout_detail (object): {{ "text_zone": ..., "product_count": number, "background_type": ..., "zone_positions": array of short phrases locating each element top to bottom (e.g. ["headline top-center", "product mid-frame", "CTA bottom-full-width"]), "has_bottom_banner": true/false, "has_corner_badge": true/false, "frame_division": short description of how the frame splits (e.g. "three stacked horizontal bands" or "single uninterrupted gradient ground, no hard divisions") }}
- legibility_notes (string): whether in-image text is readable at feed size
- body_area_shown (string): REQUIRED - this key must ALWAYS be present in your JSON output. if a human subject appears in the image, name the specific body region shown or emphasised (e.g. "legs", "arms", "torso", "hands", "neck and décolletage"); if NO human subject appears at all (e.g. a product-only shot, an illustration/diagram, or text-only creative), use exactly "none". This is read downstream to decide whether a per-run body-area instruction may be applied to this reference at all - do not guess a body part onto a productless or human-less image.
- face_present (object): REQUIRED - this key must ALWAYS be present. {{ "has_face": true/false, "prominence": one of primary/incidental/none, "location": free-text description of where in the frame the face sits (e.g. "centre-frame, close-up, looking directly at camera") }}. has_face is false and prominence is exactly "none" when no face is visible anywhere in the image - location is "" in that case, never a guess at where a face would be. prominence "primary" means the face is the compositional focus (e.g. a close-up beauty shot); "incidental" means a person is visible but the face is not what the ad is about (e.g. a wide lifestyle shot, a person shown from behind or cropped above the shoulders, a hands-only shot with a face barely visible in the background).
- creative_objective (string): the ad's primary strategic goal in one short phrase, e.g. "drive urgency around a limited-time offer" or "build trust via a testimonial"
- target_audience (string): who this ad is speaking to, in one short phrase, e.g. "women 40+ concerned about skin texture and firmness"
- typography (object): {{ "headline_face": typeface style e.g. serif/sans/script, "headline_weight": e.g. bold/light/regular, "hierarchy_levels": array of short phrases describing each distinct text tier top to bottom (e.g. ["large bold serif headline", "medium sans subhead", "small CTA button label"]), "case_treatment": e.g. "all caps headline, sentence case body" }}
- typography_zones (array): one entry PER DISTINCT TEXT ZONE in the image (brand logo, headline, sub-copy, offer/CTA, badge text - every zone that carries its own visible text, not just the headline). This is PER-ZONE detail; the `typography`/`hierarchy_levels` fields above describe the ad's typography in general prose - this field must actually enumerate each zone so the treatment isn't lost. Each entry: {{ "zone": short label matching a zone_positions phrase above (e.g. "headline upper-right"), "typeface_class": serif/sans/script, "weight": e.g. bold/light/regular, "case": upper/title/sentence, "letter_spacing": tight/normal/wide, "colour": this zone's OWN text colour - distinct from the scene's overall palette_mood, e.g. "gold" or "white", "size_relative": e.g. large/medium/small relative to the frame, "decorative_elements": array of short phrases for anything attached to the zone (a pipe divider, a rule, an underline, a bullet mark) or [] if none, "line_count": number of lines this zone actually occupies }}. A reference commonly has THREE OR FOUR distinct typographic levels (e.g. a large serif headline, a small-caps accent line with wide letter-spacing, small body copy, a button label) - give each its own entry; never collapse two visually distinct levels into one, and never omit a zone just because its text is short.
- structural_zones (array): REQUIRED - this key must ALWAYS be present in your JSON output, never omitted, regardless of how many zones the ad actually has. Every occurrence of these NINE structural zone types. Described by what each type IS STRUCTURALLY, never by which brand happens to be in front of you right now - this must generalise to any ad, any category, any layout. Zero, one, or several entries of the same zone_type are all valid: if a type doesn't appear at all, it simply has no entries; if an ad has two badges, return two entries with zone_type "badge". An ad with none of these nine zones returns structural_zones as an explicit empty array [] - the key is still present, it is simply empty; do not force a fit and do not drop the key itself. If a zone_type is NOT present, OMIT it entirely - do not add an entry for it just to say it is absent (e.g. never write a sub_line entry whose detail says "no explicit sub-line"). An entry in this array means the zone EXISTS; anything downstream that reads this array will treat every entry as real and try to act on it, so a placeholder entry describing an absence would be read as a zone that is actually there. Each entry: {{ "zone_type": one of brand_wordmark/sub_line/body_copy/cta/price_anchor/product_callout/badge/social_proof/disclaimer, "position": short phrase locating it (e.g. "top-center", "bottom-right banner"), "container": one of none/oval/rect/banner/ribbon/other - the shape holding it, "detail": a short structural description specific to what this zone_type needs (see below), "social_proof_kind": one of aggregate_bar/single_quote - ONLY set when zone_type is social_proof, omit or null otherwise }}.
    brand_wordmark = the advertiser's own logo or name mark, distinct from a product's own printed label if a product is shown
    sub_line = a short accent line below the headline (e.g. a tagline, a small-caps line with wide letter-spacing) - a DISTINCT in-scene text zone from the headline itself, not a second name for it
    body_copy = a paragraph or multi-line block of supporting text rendered IN the image itself - NEVER the scraped ad_text/primary_text supplied separately as a text block above; that is off-image Facebook copy, not part of the visual, and must never be reported here
    cta = a call-to-action button or label rendered in the image as its own zone (the ad's own `cta` field above names the ACTION being asked for; this names the zone/container it appears in, if any)
    price_anchor = a price shown as its own graphic element - detail should say whether it's a single price or a struck-through original/new pair, and must transcribe the exact currency symbol and amount shown in the reference - never assume, convert, or default to any one currency or region's format
    product_callout = a small card or panel calling out a specific product/variant (e.g. a "New Scent" card) - detail should say exactly what it carries: a name, a descriptor line, a colour swatch, a thumbnail image, any combination of these
    badge = a discrete graphic badge, seal, or roundel - detail should name its actual content (e.g. "reads NEW", "star rating icon", "%-off roundel", "award/certification seal")
    social_proof = a testimonial or aggregate-review element rendered in the image - social_proof_kind distinguishes an AGGREGATE BAR (a review count + star average, e.g. "Trustpilot · Over 30,000 · ★★★★★") from a SINGLE QUOTE (one customer's words plus attribution), since these need different treatment downstream; set social_proof_kind for every social_proof entry, never leave it unset when this zone_type is used
    disclaimer = fine-print or footnote text (e.g. "*T&Cs apply", a small legal line)
- scene_elements (array): REQUIRED - this key must ALWAYS be present, [] when there is genuinely nothing beyond the product itself to inventory. Every element in the scene OTHER THAN the product: hands, skin, props, background objects, secondary figures, surfaces the product rests on - anything visually present that a faithful reproduction of this reference would need to include. Each entry: {{ "element": short noun phrase describing WHAT the element is and its STRUCTURE only - NEVER its colour (e.g. "wooden bathroom shelf", NOT "honey-toned wooden shelf"; "background gradient", NOT "lavender-purple to salmon-pink gradient background"; "a second person's hand", NOT "a pale hand"; "a folded white towel" is WRONG for this reason - write "a folded towel" instead). Colour is never part of an element's identity here: palette substitution governs colour separately downstream, and a colour word baked into this phrase would read as demanding that exact colour survive - describe the STRUCTURE (what it is, its shape/material/role) and let colour be decided elsewhere, "role": what it is doing in the scene (e.g. "product rests on it", "applying the product to skin", "softly blurred in the background"), "essential": true if omitting it would change what the ad is communicating, false if it is incidental set-dressing, "depicts_competitor_category": true if this element EXISTS TO MAKE THE COMPETITOR'S ARGUMENT - a prop, symbol, metaphor, or diagram element that carries or illustrates the ad's persuasive claim, even when it is not a literal depiction of the competitor's product category itself. Judge by FUNCTION in the argument, not just literal resemblance: a chain-and-padlock illustrating "locked" fat the product claims to unlock counts exactly as much as a steak or a spoon of powder for a protein supplement, a strand of hair for a haircare product, or an eye-diagram for an eye treatment - a metaphor or symbolic prop is not exempt just because it is not literally the product category. EXCLUDED, ALWAYS false regardless of role: any human figure, face, or body part - even one that is central to the ad's argument or is itself the metaphor (e.g. a distressed human character dramatising the problem). A person is handled entirely by the separate person-substitution path (face_present/PERSON below) - never by this field, with no exception. false otherwise for anything register-neutral that would suit any brand's ad (a hand, a towel, a shelf, a background wall, generic scenery) - a hand/body part is false for two independent reasons here (register-neutral AND a body part), never a case where the two tests could disagree. REQUIRED on every entry - this key must ALWAYS be present, never omitted, even when the answer is false. This is a DIFFERENT question from essential: an element can be essential AND register-neutral (keep it, unchanged), essential AND competitor-specific (keep the ROLE, substitute the content), or incidental either way. Applies in EVERY register, not only illustrated/drawn scenes - a photographic or 3D-rendered prop that exists to make the competitor's argument (a chain, a padlock, a donut, a weight-loss label) needs this exactly as much as a drawn one; how it gets substituted (natively in the reference's own style vs. photorealistically) is a separate, register-dependent decision made downstream of this flag, not a reason to withhold it here. }}. An incomplete inventory here means those elements get silently dropped when this reference is cloned - list everything visible, not just the obviously important pieces.
- testimonial_zones (array): REQUIRED - this key must ALWAYS be present, [] when the ad carries no testimonial. Every customer-testimonial-shaped text element in the image - distinct from structural_zones' social_proof entries above, which record WHERE/HOW it is contained; this records the actual testimonial CONTENT. Each entry: {{ "text_verbatim": the exact testimonial text as it appears in the image, "attribution": the name/initial/handle it is attributed to, verbatim, or "" if unattributed, "placement": short phrase locating it (e.g. "bottom-left card"), "styling": how it is visually presented (e.g. "quote marks, no card", "5-star rating above the quote inside a white rounded card") }}.
- text_purpose (array): REQUIRED - this key must ALWAYS be present, [] when the image carries no text at all. One entry per distinct text block in the image - this classifies EVERY text block by function, at a finer grain than the fields above. Each entry: {{ "text_verbatim": the exact text, "purpose": one of offer/testimonial/efficacy_claim/problem_hook/product_description/cta/other, "placement": short phrase locating it }}. purpose describes what the text is DOING - the job it performs in the ad's argument - never what it literally says; this is what drives what the REPLACEMENT copy must accomplish when this reference is cloned, so classify by function even when the wording itself doesn't obviously announce its purpose (e.g. a rhetorical question is still a problem_hook, not "other").
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


def deconstruct_from_response(raw_text: str) -> dict:
    """Parse and validate a blueprint from a raw model response. Raises if invalid."""
    blueprint = parse_blueprint(raw_text)
    err = validator.validation_error(blueprint)
    if err:
        raise BlueprintValidationError(f"Blueprint failed schema validation: {err}", err)
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
