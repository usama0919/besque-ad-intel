"""Regeneration step (image prompt): turn a blueprint's visual into an image-gen prompt."""
import os

IMAGE_MODEL = os.getenv("IMAGE_MODEL", "placeholder-image-model")


def build_image_prompt(blueprint: dict) -> str:
    """Construct a Besque-adapted image generation prompt from the blueprint's visual notes."""
    visual = blueprint.get("visual", {})
    layout = visual.get("layout", "clean centered composition")
    subject = visual.get("subject", "skincare product")
    palette = visual.get("palette_mood", "warm, natural tones")
    text_placement = visual.get("text_placement", "minimal")

    prompt = (
        f"A premium skincare advertisement image for Besque, a natural body-oil brand for women 40+. "
        f"Composition: {layout}. Subject: {subject}, reimagined with a Besque product "
        f"(a natural botanical body oil in an elegant bottle). "
        f"Palette and mood: {palette}. Text placement: {text_placement}. "
        f"Style: clean, editorial, aspirational, natural light, no competitor branding, "
        f"no text overlays baked into the image."
    )
    return prompt

# ---- Live single-pass image generation (nano banana via Gemini API) ----
from google import genai
from pathlib import Path

ASSET_DIR = Path(os.getenv("ASSET_DIR", "assets"))


def generate_image(blueprint, ad_id):
    """Single-pass image generation from the blueprint. One image, no iteration.
    Saves to assets/<ad_id>_draft.png and returns the path. Returns None on failure."""
    prompt = build_image_prompt(blueprint)
    try:
        client = genai.Client(vertexai=True, project="besque-martech", location="global")
        response = client.models.generate_content(
            model="gemini-3.1-flash-image",
            contents=prompt,
        )
        image_bytes = None
        for part in response.candidates[0].content.parts:
            if part.inline_data is not None:
                image_bytes = part.inline_data.data
                break
        if image_bytes is None:
            return None

        ASSET_DIR.mkdir(exist_ok=True)
        dest = ASSET_DIR / f"{ad_id}_draft.png"
        with open(dest, "wb") as f:
            f.write(image_bytes)
        return str(dest)
    except Exception as e:
        import traceback
        print(f"[DEBUG generate_image] ad_id={ad_id} failed: {type(e).__name__}: {e}")
        traceback.print_exc()
        return None
