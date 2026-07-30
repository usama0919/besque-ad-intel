"""Regeneration step (image prompt): turn a blueprint's visual into an image-gen prompt."""
import os
from src import assets
from src.compliance_rules import COMPLIANCE_RULES

IMAGE_MODEL = os.getenv("IMAGE_MODEL", "placeholder-image-model")


def build_image_prompt(blueprint: dict, product: dict = None) -> str:
    """Construct a Besque-adapted image generation prompt from the blueprint's visual notes."""
    visual = blueprint.get("visual", {})
    # visual.subject is deliberately NOT read here. In practice it's where the vision
    # deconstruct step puts rich, identity-carrying descriptions of the competitor ad's
    # model (hair colour, build, pose, clothing - e.g. "Blonde athletic woman 40+ in dark
    # bikini..."). Wiring it into this prompt would hand that description straight to the
    # image model. If a future change needs `subject` for better composition, it must
    # come with an explicit compliance override alongside it, not instead of one.
    layout = visual.get("layout", "clean centered composition")
    palette = visual.get("palette_mood", "warm, natural tones")
    text_placement = visual.get("text_placement", "minimal")
    prod_style = (blueprint.get("production_style") or {}).get("style", "")

    if product:
        visual_desc = product.get("visual_description", "")
        product_desc = (
            f"The featured product is {product.get('name', 'a Besque product')}: {product.get('description', '')} "
            + (f"Its fixed visual appearance: {visual_desc}. " if visual_desc else "")
            + f"If any label or ingredient text appears on the product, it must show ONLY these real ingredients: "
            f"{product.get('ingredients', '')}. Key claim: {product.get('hero_claim', '')}. "
            f"Never invent ingredients or label text not listed here. "
        )
    else:
        product_desc = "(a natural botanical body oil in an elegant bottle). "
    prompt = (
        BRAND_RULES +
        f"A premium skincare advertisement image for Besque, a natural body-oil brand for women 40+. "
        f"Composition and setting: {layout}. (If this implies a person, render them per compliance "
        f"rule C1 - a generic, non-identifiable model, never the specific individual described.) "
        f"Place the Besque product described below as the subject "
        f"within this setting; do not render the competitor's product. "
        + product_desc +
        f"Palette and mood: {palette}. Text placement: {text_placement}. "
        f"Square 1:1 aspect ratio composition. "
        + PRODUCTION_STYLE_GUIDANCE.get(prod_style, DEFAULT_STYLE_GUIDANCE) +
        f"Keep the base image completely free of overlaid marketing text — only the Besque product's "
        f"own label may appear — and leave clean, uncluttered negative space where headline and offer "
        f"text will be added later as a separate HTML overlay; no competitor branding anywhere."
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
    "6) TEXT POLICY (STRICT): the Besque product's own printed label — exactly as shown on the reference product photo — is the ONLY text permitted anywhere in the image. NEVER render any headline, price, discount, percentage, offer, badge, sticker, sticky note, caption, tagline, watermark, or extra logo, whether copied from the competitor ad or invented. "
    "7) PRODUCT POLICY (STRICT): the single product in the reference product photo is the ONLY product permitted anywhere in the image — exactly one bottle, and it is that one. If no reference product photo is supplied, exactly one Besque bottle matching the product description is permitted. A multi-product range, collection, bundle, gift set or line-up in the source ad is a layout to borrow, not an inventory to reproduce: keep its composition, lighting and mood, collapse it to a single-product composition, and leave the freed area as clean negative space. NEVER add a second bottle, a variant, a size sibling, a refill, a carton, a box, or any further SKU, whether copied from the competitor ad or invented. "
) + COMPLIANCE_RULES

# Per-production-style guidance, keyed by blueprint.production_style.style. Swapped in as a
# single Style clause so ugc_native does not fight the studio-look wording it replaces.
PRODUCTION_STYLE_GUIDANCE = {
    "ugc_native": (
        "Style: authentic user-generated content look — shot on a phone, natural available light, "
        "casual real-life setting, slightly imperfect candid framing, relatable not polished. "
        "The Besque product itself must stay sharp, in focus, and clearly lit by the available "
        "light — never blurred, backlit, or lost in shadow. "
    ),
    "high_spec_studio": (
        "Style: high-spec studio production — controlled premium lighting, deliberate composition, "
        "crisp macro texture, editorial and aspirational. "
    ),
    "hybrid": (
        "Style: studio-quality product rendering inside a casual, real-world setting — the product "
        "is hero-lit with deliberate studio-grade lighting and polished, while the surrounding "
        "scene feels natural and lived-in. "
    ),
    "illustrated": (
        "Style: not a photograph - a whiteboard-style diagram, clean 3D render, or comic-strip/"
        "illustrated panel, flat or lightly shaded colour, clear linework, diagrammatic labelling "
        "where relevant. No photographic lighting, no camera grain, no realistic skin or material "
        "texture - this is drawn or rendered, not shot. "
    ),
}
# Every real production_style value must have an explicit entry above - assert it here so a
# schema addition can't silently fall through to DEFAULT_STYLE_GUIDANCE unnoticed (that
# fallback is only for blueprint.production_style being absent/null, not for a recognized
# style someone forgot to add guidance for).
from src import validator as _validator
assert set(_validator.production_styles()) <= set(PRODUCTION_STYLE_GUIDANCE), (
    "PRODUCTION_STYLE_GUIDANCE is missing an entry for one of validator.production_styles()"
)
del _validator

# Used when production_style is absent/null/unknown — preserves the previous hardcoded look.
DEFAULT_STYLE_GUIDANCE = "Style: clean, editorial, aspirational, natural light. "


def _reference_framing(count):
    """Framing text placed after the reference image block(s). Explicit about multiple
    photos being the SAME bottle from different angles, not a product range - BRAND_RULES
    rule 7 already collapses multi-product layouts to one product, and without this an
    ad-gen model handed 2-4 images can easily misread them as separate SKUs to feature."""
    if count <= 1:
        return ("REFERENCE PRODUCT PHOTO ABOVE: this is the EXACT Besque product. Reproduce "
                "this bottle, its label, and its design faithfully in the ad - do not redesign, "
                "relabel, or alter it. ")
    return (
        f"REFERENCE PRODUCT PHOTOS ABOVE ({count} images): these are ALL PHOTOS OF THE SAME "
        f"SINGLE BOTTLE, shown from different angles/sides - NOT multiple products, NOT a "
        f"product range or bundle. Reproduce this one bottle, its label, and its design "
        f"faithfully in the ad, exactly one bottle in the final image, do not redesign, "
        f"relabel, or alter it. "
    )


def _draft_stem(ad_id, angle_slug=None):
    """Filename/blob-key stem for one (ad_id, angle) draft. angle_slug=None reproduces the
    pre-angle stem exactly (just ad_id) - existing draft paths on disk/bucket are untouched.
    A given angle_slug produces a distinct stem, because generate_image/edit_image key their
    output PNG and bucket blob by this stem alone: without it, a second angle's draft would
    silently overwrite the first angle's PNG at the same {ad_id}_draft.png path even though
    their DB rows are correctly distinct."""
    return ad_id if not angle_slug else f"{ad_id}__{angle_slug}"


def generate_image(blueprint, ad_id, product=None, reference_images=None, angle_slug=None):
    """Single-pass image generation from the blueprint. One image, no iteration.
    Saves to assets/<stem>_draft.png (stem = ad_id, or ad_id+angle if angle_slug is given)
    and returns the path. Returns None on failure."""
    prompt = build_image_prompt(blueprint, product=product)
    stem = _draft_stem(ad_id, angle_slug)
    try:
        client = genai.Client(vertexai=True, project="besque-martech", location="global")
        reference_images = reference_images or []
        if reference_images:
            from google.genai import types as genai_types
            contents = (
                [genai_types.Part.from_bytes(data=img, mime_type="image/png") for img in reference_images]
                + [_reference_framing(len(reference_images)) + prompt]
            )
        else:
            contents = prompt
        import time as _time
        response = None
        for _attempt in range(3):
            try:
                response = client.models.generate_content(
                    model="gemini-3.1-flash-image",
                    contents=contents,
                )
                break
            except Exception as _e:
                if "429" in str(_e) and _attempt < 2:
                    _time.sleep(20 * (_attempt + 1))
                    continue
                raise
        image_bytes = None
        for part in response.candidates[0].content.parts:
            if part.inline_data is not None:
                image_bytes = part.inline_data.data
                break
        if image_bytes is None:
            return None

        ASSET_DIR.mkdir(exist_ok=True)
        dest = ASSET_DIR / f"{stem}_draft.png"
        with open(dest, "wb") as f:
            f.write(image_bytes)
        try:
            from google.cloud import storage
            bucket_name = assets.asset_bucket_name()
            blob = storage.Client().bucket(bucket_name).blob(f"{stem}_draft.png")
            blob.upload_from_string(image_bytes, content_type="image/png")
        except Exception as e:
            print(f"Bucket upload failed (non-fatal): {e}")
        generate_image.last_prompt = prompt
        return str(dest)
    except Exception as e:
        import traceback
        print(f"[DEBUG generate_image] ad_id={ad_id} failed: {type(e).__name__}: {e}")
        traceback.print_exc()
        return None


def _next_draft_version(ad_id, angle_slug=None):
    """Next free n for {stem}_draft_v{n}.png (1-based), stem = _draft_stem(ad_id, angle_slug).
    Uses a prefix scan rather than glob() so an ad_id containing glob metacharacters can't
    skew the match."""
    prefix = f"{_draft_stem(ad_id, angle_slug)}_draft_v"
    n = 0
    if ASSET_DIR.exists():
        for p in ASSET_DIR.iterdir():
            if p.name.startswith(prefix) and p.suffix == ".png":
                tail = p.stem[len(prefix):]
                if tail.isdigit():
                    n = max(n, int(tail))
    return n + 1


def edit_image(current_image_bytes, instruction, ad_id, aspect="1:1", angle_slug=None):
    """Edit an existing draft image with a natural-language instruction via nano banana.
    Versions the outgoing draft to {stem}_draft_v{n}.png (stem = ad_id, or ad_id+angle),
    then saves/uploads the result under the same stem's key and returns it. Returns None
    on failure. Edit Mode itself is unbuilt - this parameter only exists so an angle-variant
    draft can't be versioned/overwritten under the wrong (ad_id-only) key if this function
    is ever called against one."""
    from google.genai import types as genai_types
    stem = _draft_stem(ad_id, angle_slug)
    prompt = (
        BRAND_RULES +
        f"Edit this Besque skincare advertisement image. Instruction: {instruction}. "
        f"Keep it a premium, editorial skincare ad. Output aspect ratio: {aspect}. "
        f"Keep the edited image completely free of overlaid marketing text — only the Besque "
        f"product's own label may appear, exactly as it appears in the image being edited — and "
        f"leave clean, uncluttered negative space where headline and offer text will be added "
        f"later as a separate HTML overlay; no competitor branding anywhere. "
        f"Do not add or alter ingredients, percentages, or claims."
    )
    try:
        client = genai.Client(vertexai=True, project="besque-martech", location="global")
        print(f"[edit_image] ad_id={ad_id} aspect={aspect} prompt:\n{prompt}")
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
        dest = ASSET_DIR / f"{stem}_draft.png"
        # Preserve the pre-edit draft before overwriting. current_image_bytes is that draft
        # whether the caller read it from disk or the bucket, so this works cache-cold too.
        # Deliberately fatal: if the previous version cannot be preserved we do not
        # overwrite it, since the whole point is that edits stay reversible.
        version_name = f"{stem}_draft_v{_next_draft_version(ad_id, angle_slug)}.png"
        try:
            with open(ASSET_DIR / version_name, "wb") as f:
                f.write(current_image_bytes)
        except Exception as e:
            print(f"[edit_image] ad_id={ad_id} aborted: could not version previous draft: {e}")
            return None
        with open(dest, "wb") as f:
            f.write(image_bytes)
        try:
            from google.cloud import storage
            bucket = storage.Client().bucket(assets.asset_bucket_name())
            # Mirror the version alongside the new draft; local assets are ephemeral on Cloud Run.
            bucket.blob(version_name).upload_from_string(current_image_bytes, content_type="image/png")
            bucket.blob(f"{stem}_draft.png").upload_from_string(image_bytes, content_type="image/png")
        except Exception as e:
            print(f"Bucket upload failed (non-fatal): {e}")
        edit_image.last_prompt = prompt
        return str(dest)
    except Exception:
        import traceback
        traceback.print_exc()
        return None
