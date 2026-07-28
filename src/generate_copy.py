import logging
import os
import json

from src import json_response

CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")

# Propagates to the root handler configured in pipeline.py, so these lines appear
# inline with the rest of the run log rather than on a separate stream.
log = logging.getLogger("generate_copy")

COPY_PROMPT = """You are a senior copywriter for Besque, a natural skincare brand for women 40+. Using the creative blueprint below, write Besque-adapted ad copy, following the voice and claim rules given below.

Return ONLY valid JSON, no preamble or markdown, with exactly these fields:
- headline (string)
- primary_text (string)
- cta (string)

Rules:
- Do NOT mention or reference any competitor brand name.
- Do NOT copy competitor wording verbatim; adapt the angle, not the words.
- Keep claims within the APPROVED CLAIMS section below. Where it says none are supplied, stay strictly within the PRODUCT facts.
- A section reading "None supplied." means that material genuinely does not exist. Write the copy from what IS given; do not ask for the missing material and do not decline.

BRAND VOICE GUIDE:
{brand_voice}

APPROVED CLAIMS:
{approved_claims}

PRODUCT (use ONLY these facts, never invent claims or ingredients):
{product_info}

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
NO_PRODUCT = (
    "None supplied. Refer to a Besque natural body oil in general terms only; do not "
    "invent a product name, ingredients, percentages or numeric results."
)

PRODUCT_FACT_KEYS = ("name", "description", "ingredients", "hero_claim")


def _product_facts(product):
    """Only the four fields the copywriter needs - keeps id, image_key and category
    out of the prompt. Empty fields are omitted rather than sent as blanks."""
    if not product:
        return NO_PRODUCT
    facts = {k: product.get(k) for k in PRODUCT_FACT_KEYS
             if str(product.get(k) or "").strip()}
    return json.dumps(facts, indent=2) if facts else NO_PRODUCT


def build_copy_prompt(blueprint, brand_voice="", approved_claims="", product=None):
    return COPY_PROMPT.format(
        brand_voice=brand_voice or NO_BRAND_VOICE,
        approved_claims=approved_claims or NO_APPROVED_CLAIMS,
        blueprint=json.dumps(blueprint, indent=2),
        product_info=_product_facts(product),
    )


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


def generate_copy_live(blueprint, brand_voice="", approved_claims="", product=None):
    """Send a blueprint to Claude and return validated Besque-adapted copy.

    Normally ONE API call. If the response cannot be parsed into the required fields,
    retries ONCE with a larger max_tokens and a JSON-only system prompt. Raises if the
    retry fails too.
    """
    prompt = build_copy_prompt(blueprint, brand_voice, approved_claims, product=product)
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
