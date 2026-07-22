import os
import json

CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")

COPY_PROMPT = """You are a senior copywriter for Besque, a natural skincare brand for women 40+. Using the creative blueprint below, write Besque-adapted ad copy. Match the brand voice guide and use only approved claims.

Return ONLY valid JSON, no preamble or markdown, with exactly these fields:
- headline (string)
- primary_text (string)
- cta (string)

Rules:
- Do NOT mention or reference any competitor brand name.
- Do NOT copy competitor wording verbatim; adapt the angle, not the words.
- Keep claims within the approved list.

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


def build_copy_prompt(blueprint, brand_voice="", approved_claims="", product=None):
    return COPY_PROMPT.format(
        brand_voice=brand_voice or "(brand voice guide provided at kickoff)",
        approved_claims=approved_claims or "(approved claims list provided at kickoff)",
        blueprint=json.dumps(blueprint, indent=2),
        product_info=json.dumps(product, indent=2) if product else "(no specific product selected)",
    )


def parse_copy(raw_text):
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    return json.loads(cleaned.strip())


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


def generate_copy_live(blueprint, brand_voice="", approved_claims="", product=None):
    """Send a blueprint to Claude and return validated Besque-adapted copy.
    Makes ONE API call. Raises if the response is missing required fields."""
    prompt = build_copy_prompt(blueprint, brand_voice, approved_claims, product=product)

    client = anthropic.Anthropic(timeout=60.0, max_retries=1)  # reads ANTHROPIC_API_KEY from env
    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    raw_text = message.content[0].text
    return copy_from_response(raw_text)
