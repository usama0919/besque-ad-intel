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

# ---- Live single-pass image generation (Flux via Replicate) ----
import replicate
import httpx
from pathlib import Path

IMAGE_MODEL_ID = os.getenv("IMAGE_MODEL_ID", "black-forest-labs/flux-schnell")
ASSET_DIR = Path(os.getenv("ASSET_DIR", "assets"))


def generate_image(blueprint, ad_id):
    """Single-pass image generation from the blueprint. One image, no iteration.
    Saves to assets/<ad_id>_draft.png and returns the path. Returns None on failure."""
    prompt = build_image_prompt(blueprint)
    output = replicate.run(IMAGE_MODEL_ID, input={"prompt": prompt, "num_outputs": 1})
    # Flux returns a list of file-like/URL outputs
    item = output[0] if isinstance(output, list) else output
    url = str(item)

    ASSET_DIR.mkdir(exist_ok=True)
    dest = ASSET_DIR / f"{ad_id}_draft.png"
    with httpx.stream("GET", url, timeout=60, follow_redirects=True) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
    return str(dest)
