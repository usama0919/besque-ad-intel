"""Deconstruction step: send an ad image to Claude and get a structured blueprint."""
import os
import json
import base64
from pathlib import Path

from src import validator

# Model + key are read from env so the real key plugs in at kickoff.
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")

BLUEPRINT_PROMPT = """You are an expert ad analyst. Analyse the attached advertising image and return a JSON creative blueprint. Return ONLY valid JSON, no preamble or markdown.

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
"""


def build_prompt(ad_id, source_page, captured_at, destination_url=""):
    return BLUEPRINT_PROMPT.format(
        ad_id=ad_id,
        source_page=source_page,
        captured_at=captured_at,
        destination_url=destination_url,
    )


def parse_blueprint(raw_text: str) -> dict:
    """Parse Claude's text response into a blueprint dict. Strips markdown fences if present."""
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    return json.loads(cleaned.strip())


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


def deconstruct_image(image_bytes, ad_id, source_page, captured_at, destination_url=""):
    """Send one ad image to Claude vision and return a validated blueprint dict.
    Makes ONE API call. Raises if the response fails schema validation."""
    b64, media_type = _b64_from_bytes(image_bytes)
    prompt = build_prompt(ad_id, source_page, captured_at, destination_url)

    client = anthropic.Anthropic(timeout=60.0, max_retries=1)  # reads ANTHROPIC_API_KEY from env
    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}},
                {"type": "text", "text": prompt},
            ],
        }],
    )
    raw_text = message.content[0].text
    return deconstruct_from_response(raw_text)
