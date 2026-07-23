"""Regeneration step (image prompt): turn a blueprint's visual into an image-gen prompt."""
import os

IMAGE_MODEL = os.getenv("IMAGE_MODEL", "placeholder-image-model")


def build_image_prompt(blueprint: dict, product: dict = None) -> str:
    """Construct a Besque-adapted image generation prompt from the blueprint's visual notes."""
    visual = blueprint.get("visual", {})
    layout = visual.get("layout", "clean centered composition")
    subject = visual.get("subject", "skincare product")
    palette = visual.get("palette_mood", "warm, natural tones")
    text_placement = visual.get("text_placement", "minimal")

    if product:
        product_desc = (
            f"The featured product is {product.get('name', 'a Besque product')}: {product.get('description', '')} "
            f"If any label or ingredient text appears on the product, it must show ONLY these real ingredients: "
            f"{product.get('ingredients', '')}. Key claim: {product.get('hero_claim', '')}. "
            f"Never invent ingredients or label text not listed here. "
        )
    else:
        product_desc = "(a natural botanical body oil in an elegant bottle). "
    prompt = (
        BRAND_RULES +
        f"A premium skincare advertisement image for Besque, a natural body-oil brand for women 40+. "
        f"Composition: {layout}. Subject: {subject}, reimagined with a Besque product. "
        + product_desc +
        f"Palette and mood: {palette}. Text placement: {text_placement}. "
        f"Square 1:1 aspect ratio composition. "
        f"Style: clean, editorial, aspirational, natural light, no competitor branding, "
        f"no text overlays baked into the image."
    )
    return prompt

# ---- Live single-pass image generation (nano banana via Gemini API) ----
from google import genai
from pathlib import Path

ASSET_DIR = Path(os.getenv("ASSET_DIR", "assets"))

BRAND_RULES = (
    "STRICT RULES - NEVER VIOLATE: "
    "1) Any Besque bottle label must show ONLY the exact product name provided, nothing else. "
    "2) NEVER copy the competitor's product name, brand name, claims, or any label text onto the Besque product. "
    "3) NEVER invent ingredients, percentages, or product names. "
    "4) If no product name is provided, the bottle shows only the word 'Besque'. "
    "5) The product is always a body OIL in a glass bottle unless stated otherwise - never a cream, jar, or tub. "
    "6) NEVER render any price, discount percentage, or offer text (e.g. '50% OFF', '$29.99') unless it is explicitly provided in the product info. Do NOT copy prices or offers from the competitor ad. "
)


def generate_image(blueprint, ad_id, product=None, reference_bytes=None):
    """Single-pass image generation from the blueprint. One image, no iteration.
    Saves to assets/<ad_id>_draft.png and returns the path. Returns None on failure."""
    prompt = build_image_prompt(blueprint, product=product)
    try:
        client = genai.Client(vertexai=True, project="besque-martech", location="global")
        if reference_bytes:
            from google.genai import types as genai_types
            contents = [
                genai_types.Part.from_bytes(data=reference_bytes, mime_type="image/png"),
                "REFERENCE PRODUCT PHOTO ABOVE: this is the EXACT Besque product. Reproduce this bottle, its label, and its design faithfully in the ad - do not redesign, relabel, or alter it. " + prompt,
            ]
        else:
            contents = prompt
        response = client.models.generate_content(
            model="gemini-3.1-flash-image",
            contents=contents,
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
        try:
            from google.cloud import storage
            bucket_name = os.getenv("ASSET_BUCKET", "besque-ad-intel-assets")
            blob = storage.Client().bucket(bucket_name).blob(f"{ad_id}_draft.png")
            blob.upload_from_string(image_bytes, content_type="image/png")
        except Exception as e:
            print(f"Bucket upload failed (non-fatal): {e}")
        return str(dest)
    except Exception as e:
        import traceback
        print(f"[DEBUG generate_image] ad_id={ad_id} failed: {type(e).__name__}: {e}")
        traceback.print_exc()
        return None


def edit_image(current_image_bytes, instruction, ad_id, aspect="1:1"):
    """Edit an existing draft image with a natural-language instruction via nano banana.
    Saves/uploads the result under the same key and returns it. Returns None on failure."""
    from google.genai import types as genai_types
    prompt = (
        BRAND_RULES +
        f"Edit this Besque skincare advertisement image. Instruction: {instruction}. "
        f"Keep it a premium, editorial skincare ad. Output aspect ratio: {aspect}. "
        f"Do not add any text, ingredients, or claims that are not already present."
    )
    try:
        client = genai.Client(vertexai=True, project="besque-martech", location="global")
        response = client.models.generate_content(
            model="gemini-3.1-flash-image",
            contents=[
                genai_types.Part.from_bytes(data=current_image_bytes, mime_type="image/png"),
                prompt,
            ],
            config=genai_types.GenerateContentConfig(
                image_config=genai_types.ImageConfig(aspect_ratio=aspect),
            ),
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
        try:
            from google.cloud import storage
            bucket_name = os.getenv("ASSET_BUCKET", "besque-ad-intel-assets")
            blob = storage.Client().bucket(bucket_name).blob(f"{ad_id}_draft.png")
            blob.upload_from_string(image_bytes, content_type="image/png")
        except Exception as e:
            print(f"Bucket upload failed (non-fatal): {e}")
        return str(dest)
    except Exception:
        import traceback
        traceback.print_exc()
        return None
