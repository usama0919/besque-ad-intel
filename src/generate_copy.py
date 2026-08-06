import logging
import os
import json

from src import json_response
from src.compliance_rules import COMPLIANCE_RULES

CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")

# Propagates to the root handler configured in pipeline.py, so these lines appear
# inline with the rest of the run log rather than on a separate stream.
log = logging.getLogger("generate_copy")

COPY_PROMPT = """You are a senior copywriter for Besque, a natural skincare brand for women 40+. Using the creative blueprint below, write Besque-adapted ad copy, following the voice, claim, and compliance rules given below.

Return ONLY valid JSON, no preamble or markdown, with exactly these fields:
- headline (string)
- primary_text (string)
- cta (string)
- image_subtext (string): ONE short line suitable for rendering directly ONTO the image itself - under about 12 words, NOT the full primary_text body copy. Empty string "" if no short line is appropriate for this ad.

Rules:
- Do NOT mention or reference any competitor brand name.
- Do NOT copy competitor wording verbatim; adapt the angle, not the words.
- Keep claims within the APPROVED CLAIMS section below. Where it says none are supplied, stay strictly within the PRODUCT facts.
- A section reading "None supplied." means that material genuinely does not exist. Write the copy from what IS given; do not ask for the missing material and do not decline.

{compliance_rules}

BRAND VOICE GUIDE:
{brand_voice}

APPROVED CLAIMS:
{approved_claims}

APPROVED TESTIMONIALS:
{approved_testimonials}

PRODUCT (use ONLY these facts, never invent claims or ingredients):
{product_info}

OFFER (per-run operator input - governs what offer language, if any, may appear; overrides anything the CREATIVE BLUEPRINT below shows the competitor ad had):
{offer_clause}

LANGUAGE: Write the copy in the SAME language as the competitor ad shown in the blueprint. If the blueprint's text is in Italian, write Italian; if German, German; and so on. Default to English only if the source language is unclear.

CREATIVE BLUEPRINT:
{blueprint}
"""


NO_BRAND_VOICE = (
    "None supplied. Write in a warm, plain-spoken, non-hyperbolic voice suited to a "
    "natural skincare brand for women 40+."
)
NO_APPROVED_CLAIMS = (
    "None supplied. Make claims using ONLY the PRODUCT facts below - do not introduce "
    "efficacy, clinical or timescale claims those facts do not support."
)
NO_APPROVED_TESTIMONIALS = (
    "None supplied. Do not invent, quote, or imply any customer testimonial, review, "
    "star rating, or first-person endorsement - per compliance rule C2 above."
)
NO_PRODUCT = (
    "None supplied. Refer to a Besque natural body oil in general terms only; do not "
    "invent a product name, ingredients, percentages or numeric results."
)
NO_OFFER_TEXT = (
    "No offer has been supplied for this run. headline/primary_text/image_subtext must "
    "contain NO discount, percentage, price, sale, or urgency/scarcity mechanic of any "
    "kind (e.g. \"50% off\", \"while stock lasts\", \"today only\") even if the CREATIVE "
    "BLUEPRINT below shows the competitor ad had one - that offer belongs to the "
    "competitor, not Besque. A real incident produced \"50% off - ONLY while stock "
    "lasts\" this way, lifted from the competitor's own clearance sale."
)

PRODUCT_FACT_KEYS = ("name", "description", "ingredients", "hero_claim")


def _offer_clause(offer_text=""):
    """STRICT offer instruction, mirroring the image prompt-writer's own offer rule
    (generate_image_prompt_writer._build_user_prompt) so the same operator input governs
    both copy and image generation identically: exact wording only when supplied, an
    absolute ban on any offer/discount/urgency language when it isn't."""
    if offer_text:
        return (
            f"An offer has been supplied for this run: {offer_text}. Use ONLY this exact "
            f"offer/wording where an offer is mentioned - do not invent a different "
            f"number, percentage, or term, and do not add a second offer."
        )
    return NO_OFFER_TEXT


def _product_facts(product):
    """Only the four fields the copywriter needs - keeps id, image_key and category
    out of the prompt. Empty fields are omitted rather than sent as blanks."""
    if not product:
        return NO_PRODUCT
    facts = {k: product.get(k) for k in PRODUCT_FACT_KEYS
             if str(product.get(k) or "").strip()}
    return json.dumps(facts, indent=2) if facts else NO_PRODUCT


# zone_types that carry per-panel copy in a multi-panel comparison layout (structural_zones,
# 2026-08-06 schema addition) - cta is deliberately excluded, a comparison ad has one CTA,
# not one per panel.
_PANEL_COPY_ZONE_TYPES = ("sub_line", "body_copy")


def comparison_panels(blueprint):
    """The structural_zones entries (2026-08-06 schema) that make this reference a
    multi-panel comparison, e.g. a two-panel before/after joke - TWO OR MORE sub_line/
    body_copy zones, each with its own position and its own detail describing what that
    specific panel shows. Returns [] for every ordinary single-panel reference (the
    overwhelming majority) - callers must treat an empty list as "not a comparison",
    never as a comparison with zero panels.

    Detected here, not left to Claude to notice buried in the raw blueprint JSON - a real
    live failure (2026-08-06, Grüns GLP-1 two-panel joke: problem panel left, outcome panel
    right) had the model return byte-identical text in both panels, because nothing ever
    told it a SECOND, DISTINCT piece of copy was expected at all."""
    zones = blueprint.get("structural_zones") or []
    panels = [z for z in zones if z.get("zone_type") in _PANEL_COPY_ZONE_TYPES]
    return panels if len(panels) >= 2 else []


def _panel_copy_clause(panels):
    """Instruction text appended when comparison_panels() finds a real multi-panel layout -
    lists each panel's own position/detail so Claude writes DISTINCT copy matching what
    THAT specific panel shows, rather than reusing one phrase for every panel. Position
    strings are echoed back VERBATIM from the blueprint (not rephrased) so
    generate_image_prompt._structural_zones_clause can match panel_copy entries back to
    the exact structural_zones entry they belong to by position string."""
    panel_lines = "\n".join(
        f'  - position "{p.get("position", "")}": the reference shows - {p.get("detail", "")}'
        for p in panels
    )
    return (
        "\n\nMULTI-PANEL COMPARISON (STRICT): this reference has "
        f"{len(panels)} distinct text panels, not one - each panel shows something "
        f"DIFFERENT (e.g. a problem statement in one panel, the outcome in another), per "
        f"the panel-specific detail below:\n{panel_lines}\n"
        "Also return an ADDITIONAL field, panel_copy: a list of EXACTLY "
        f"{len(panels)} objects, one per panel above, each "
        '{"position": "<the exact position string from the list above, verbatim>", '
        '"text": "<a short line of Besque copy matching what THAT panel specifically '
        'shows>"}. Every panel must get genuinely different wording matching its own '
        "described content - never repeat the same phrase across panels, and never let "
        "one panel's text describe what a DIFFERENT panel shows."
    )


def build_copy_prompt(blueprint, brand_voice="", approved_claims="", product=None,
                       approved_testimonials="", compliance_feedback=None, offer_text=""):
    """compliance_feedback is the list of issue strings from a prior failed
    check_compliance call - only passed on a retry, so it's appended as an explicit
    revision instruction rather than a template placeholder that's usually empty.

    offer_text is the per-run operator input (dashboard run-strip control, threaded
    exactly like realism/body_area) - never sourced from blueprint.offer, which is the
    competitor's own offer, not an authorized Besque one.

    panel_copy (2026-08-06) is appended the same additive way as compliance_feedback below
    - present only when comparison_panels(blueprint) finds a real multi-panel layout, so
    every ordinary single-panel blueprint sees byte-for-byte the same prompt as before this
    existed."""
    prompt = COPY_PROMPT.format(
        brand_voice=brand_voice or NO_BRAND_VOICE,
        approved_claims=approved_claims or NO_APPROVED_CLAIMS,
        approved_testimonials=approved_testimonials or NO_APPROVED_TESTIMONIALS,
        compliance_rules=COMPLIANCE_RULES,
        blueprint=json.dumps(blueprint, indent=2),
        product_info=_product_facts(product),
        offer_clause=_offer_clause(offer_text),
    )
    panels = comparison_panels(blueprint)
    if panels:
        prompt += _panel_copy_clause(panels)
    if compliance_feedback:
        issues_text = "\n".join(f"- {issue}" for issue in compliance_feedback)
        prompt += (
            "\n\nREVISION REQUIRED: your previous attempt was rejected for the following "
            "compliance issue(s). Fix these specific problems while keeping everything else "
            "on-brief:\n" + issues_text + "\n"
        )
    return prompt


def parse_copy(raw_text):
    """Parse Claude's text response into a copy dict. Tolerates markdown fences
    and surrounding prose - see json_response.extract_json."""
    return json_response.extract_json(raw_text)


REQUIRED_COPY_FIELDS = {"headline", "primary_text", "cta"}


def validate_copy(copy):
    missing = REQUIRED_COPY_FIELDS - copy.keys()
    if missing:
        raise ValueError("Copy missing required fields: " + str(missing))


def copy_from_response(raw_text):
    copy = parse_copy(raw_text)
    validate_copy(copy)
    return copy


# ---- Live Claude copy call (wired at kickoff) ----
import anthropic


JSON_ONLY_SYSTEM = (
    "You return only raw JSON. Your entire response must begin with { and end with } - "
    "no markdown fences, no ```json marker, no preamble, no commentary, no trailing text. "
    "Keep primary_text concise enough that the JSON object always completes."
)

# (max_tokens, system) per attempt. The retry widens the token budget AND adds the
# JSON-only system prompt, because truncation and fenced/prose output both surface as
# the same "Expecting value: line 1 column 1 (char 0)" - so fix both at once.
# Deliberately not src.retry.with_retry: that repeats one callable unchanged, and
# these two attempts differ.
_COPY_ATTEMPTS = (
    (3072, None),
    (8192, JSON_ONLY_SYSTEM),
)


def _log_parse_failure(attempt, total, max_tokens, message, raw_text, exc):
    """Record exactly what came back, so an intermittent failure stays diagnosable
    from the run log even when the retry goes on to succeed."""
    usage = getattr(message, "usage", None)
    log.error("copy parse failed (attempt %s/%s): %s: %s",
              attempt, total, type(exc).__name__, exc)
    log.error("stop_reason=%r output_tokens=%s input_tokens=%s max_tokens=%s content_blocks=%s",
              getattr(message, "stop_reason", "?"),
              getattr(usage, "output_tokens", "?"),
              getattr(usage, "input_tokens", "?"),
              max_tokens,
              len(getattr(message, "content", None) or []))
    log.error("raw_text len=%s chars", len(raw_text))
    log.error("raw_text repr (first 2000): %r", raw_text[:2000])
    if len(raw_text) > 2000:
        log.error("raw_text repr (last 500): %r", raw_text[-500:])


def generate_copy_live(blueprint, brand_voice="", approved_claims="", product=None,
                        approved_testimonials="", compliance_feedback=None, offer_text=""):
    """Send a blueprint to Claude and return validated Besque-adapted copy.

    Normally ONE API call. If the response cannot be parsed into the required fields,
    retries ONCE with a larger max_tokens and a JSON-only system prompt. Raises if the
    retry fails too. (compliance_feedback is a separate, outer retry driven by
    pipeline.py's fail-soft loop after a compliance check - not this JSON-parsing retry.)
    """
    prompt = build_copy_prompt(blueprint, brand_voice, approved_claims, product=product,
                                approved_testimonials=approved_testimonials,
                                compliance_feedback=compliance_feedback, offer_text=offer_text)
    client = anthropic.Anthropic(timeout=60.0, max_retries=1)  # reads ANTHROPIC_API_KEY from env

    total = len(_COPY_ATTEMPTS)
    for attempt, (max_tokens, system) in enumerate(_COPY_ATTEMPTS, 1):
        # Set per attempt so the prompt the dashboard shows is the one that produced the copy.
        generate_copy_live.last_prompt = prompt if not system else f"[system] {system}\n\n{prompt}"
        kwargs = {
            "model": CLAUDE_MODEL,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        message = client.messages.create(**kwargs)
        raw_text = ""
        try:
            raw_text = message.content[0].text if message.content else ""
            return copy_from_response(raw_text)
        except Exception as e:
            _log_parse_failure(attempt, total, max_tokens, message, raw_text, e)
            if attempt == total:
                raise
            log.warning("retrying copy generation: max_tokens %s -> %s, adding JSON-only system prompt",
                        max_tokens, _COPY_ATTEMPTS[attempt][0])
