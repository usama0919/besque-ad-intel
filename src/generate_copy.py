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

CREATIVE BLUEPRINT:
{blueprint}
"""


def build_copy_prompt(blueprint, brand_voice="", approved_claims=""):
    return COPY_PROMPT.format(
        brand_voice=brand_voice or "(brand voice guide provided at kickoff)",
        approved_claims=approved_claims or "(approved claims list provided at kickoff)",
        blueprint=json.dumps(blueprint, indent=2),
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
