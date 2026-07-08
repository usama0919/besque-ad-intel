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