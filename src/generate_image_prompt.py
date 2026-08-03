"""Regeneration step (image prompt): turn a blueprint's visual into an image-gen prompt."""
import io
import math
import os
from PIL import Image
from src import assets, generate_image_prompt_writer
from src.compliance_rules import COMPLIANCE_RULES

IMAGE_MODEL = os.getenv("IMAGE_MODEL", "placeholder-image-model")


def build_image_prompt(blueprint: dict, product: dict = None, include_product: bool = True,
                        text_in_image: bool = False, headline: str = None, subtext: str = None,
                        creative_description: str = None, edit_mode: bool = False,
                        offer_text: str = None, operator_instruction: str = None,
                        retheme_colours: bool = True, brand_palette: str = None) -> str:
    """Construct a Besque-adapted image generation prompt from the blueprint's visual notes.
    include_product=True, text_in_image=False, creative_description=None, edit_mode=False,
    offer_text=None, operator_instruction=None (today's defaults) reproduce the prior
    output exactly for a given blueprint/product - none of these are a rewrite of the
    default path.

    creative_description, if given, is the Claude prompt-writer's output
    (generate_image_prompt_writer.write_creative_description) - it REPLACES the
    template-assembled scene/composition/palette/production-style text (the writer's job
    is composition, mood, and how the angle is expressed), but brand_rules()/compliance and
    the product's factual description (product_clause, below) are still always assembled
    mechanically regardless of what the writer returned - the writer never controls the
    guardrails.

    edit_mode=True takes priority over creative_description: the competitor's own ad image
    (passed separately to generate_image as an input Part) IS the creative brief, so neither
    the writer nor the template scene/layout/palette text is used - see
    _edit_mode_instruction. product_clause/closing still always apply, same as every other
    branch. Aspect ratio is NOT stated in edit-mode prompt text (Item 6a) - it's derived
    from the reference image itself and set on the generation config instead, see
    generate_image/derive_aspect_ratio; generate mode keeps its explicit "Square 1:1"
    prompt-text instruction unchanged. offer_text is only consumed here in edit_mode (see
    _edit_mode_instruction's OFFER branch); the non-edit-mode/writer path already gets
    offer_text via generate_image's separate call into write_creative_description.

    operator_instruction (Step 2) is inserted in a FIXED position in every branch:
    immediately after brand_rules() (rules 1-9 + compliance C1-C6), before whatever
    supplies the scene text (creative_description / _edit_mode_instruction / the template
    below) - see _operator_instruction_clause. It steers the scene; it can never grant a
    permission those rules forbid, since it appears strictly below them and states that
    boundary explicitly.

    retheme_colours/brand_palette (Prompt 4, Item 5) are only consumed in edit_mode (see
    _edit_mode_instruction) - palette substitution only makes sense when reproducing an
    actual reference photograph. Named brand_palette, not palette, because `palette`
    below is ALREADY a local variable (visual.get("palette_mood", ...)) used by the
    non-edit-mode template branch - a real bug this parameter's first draft had, caught
    by test_build_image_prompt_edit_mode_forwards_retheme_and_palette: the template's
    local assignment silently shadowed the caller's value before _edit_mode_instruction
    ever saw it. blueprint.get("creative_format") is read directly here for the TEXT
    branch's typeface guidance (TYPOGRAPHY_GUIDANCE), not threaded as a separate
    parameter since it's already part of the blueprint passed in."""
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

    # Step 2, Part 4: in edit mode the reference ad governs whether there's a product to
    # substitute at all - "add a Besque product" only makes sense when the reference
    # actually shows one. layout_detail.product_count==0 or product_category=="not_product"
    # (both from deconstruct.py's blueprint schema) are the two available signals; either
    # forces effective_include_product False even if the operator's own toggle was True,
    # since there is nothing in the reference to substitute. Outside edit_mode this is a
    # no-op (reference_has_product stays True, effective_include_product == include_product).
    effective_include_product = include_product
    reference_has_product = True
    if edit_mode:
        layout_detail_bp = (blueprint or {}).get("layout_detail") or {}
        product_category_bp = ((blueprint or {}).get("product_category") or {}).get("category")
        reference_has_product = not (product_category_bp == "not_product"
                                      or layout_detail_bp.get("product_count") == 0)
        effective_include_product = include_product and reference_has_product

    if effective_include_product:
        if product:
            visual_desc = product.get("visual_description", "")
            product_desc = (
                f"The featured product is {product.get('name', 'a Besque product')}: {product.get('description', '')} "
                + (f"Its fixed visual appearance: {visual_desc}. " if visual_desc else "")
                + f"These are the ONLY real ingredients allowed to appear if the product's OWN "
                f"printed label is legible in the shot: {product.get('ingredients', '')}. This "
                f"ingredient list exists SOLELY to constrain what the product's own label may "
                f"say - it is not a list of scene elements. NEVER render any ingredient name as "
                f"a separate floating callout, badge, sticker, or piece of scene text anywhere "
                f"else in the image, even if the reference ad's own layout used ingredient "
                f"callouts. Key claim: {product.get('hero_claim', '')}. Never invent ingredients "
                f"or label text not listed here. "
            )
        else:
            product_desc = "(a natural botanical body oil in an elegant bottle). "
        product_clause = (
            f"Place the Besque product described below as the subject "
            f"within this setting; do not render the competitor's product. "
            + product_desc
        )
    else:
        product_clause = (
            "This is a deliberately productless, educational/illustrative image - do not "
            "place any Besque product, bottle, or branding anywhere in this setting. "
        )

    if text_in_image:
        closing = (
            "Render exactly the headline and supporting text specified in rule 6 above as "
            "in-scene typography - do not leave space for a separate overlay, and do not add "
            "any other text; no competitor branding anywhere."
        )
    else:
        label_clause = ("only the Besque product's own label may appear" if effective_include_product
                         else "no text of any kind may appear")
        closing = (
            f"Keep the base image completely free of overlaid marketing text — {label_clause} "
            f"— and leave clean, uncluttered negative space where headline and offer text will "
            f"be added later as a separate HTML overlay; no competitor branding anywhere."
        )

    if edit_mode:
        # The competitor's own ad image (attached separately by generate_image) IS the
        # brief - takes priority over creative_description, which is never generated in
        # this mode anyway (see generate_image). product_clause/closing still always
        # follow, same guardrail-always-appended pattern as every other branch. No aspect
        # ratio line here (Item 6a) - edit mode derives it from the reference image and
        # sets it on the generation config in generate_image, not in prompt text.
        prompt = (
            brand_rules(include_product=effective_include_product, text_in_image=text_in_image,
                        headline=headline, subtext=subtext, edit_mode=True) +
            _operator_instruction_clause(operator_instruction) +
            # include_product here is the RAW operator toggle, not effective_include_product -
            # the two booleans (include_product, reference_has_product) independently select
            # one of three distinct explanations (substitute / nothing-to-substitute /
            # operator-disabled), but the ADD-vs-DON'T-ADD outcome they produce always
            # matches effective_include_product exactly (same combining logic), so this can
            # never contradict product_clause below, which IS built from the effective value.
            _edit_mode_instruction(text_in_image=text_in_image, headline=headline, subtext=subtext,
                                   offer_text=offer_text, include_product=include_product,
                                   reference_has_product=reference_has_product,
                                   retheme_colours=retheme_colours, palette=brand_palette,
                                   creative_format=blueprint.get("creative_format")) +
            product_clause +
            closing
        )
    elif creative_description:
        # The writer's job (scene/setting, subject, product placement, text styling,
        # palette, realism) replaces the template-assembled equivalent below - but
        # product_clause (the product's factual visual_description/ingredients guardrail)
        # and the mechanical aspect-ratio/closing lines still always follow it, regardless
        # of what the writer wrote. The writer never controls the guardrails.
        prompt = (
            brand_rules(include_product=include_product, text_in_image=text_in_image,
                        headline=headline, subtext=subtext) +
            _operator_instruction_clause(operator_instruction) +
            creative_description.strip() + " "
            + product_clause +
            f"Square 1:1 aspect ratio composition. " +
            closing
        )
    else:
        prompt = (
            brand_rules(include_product=include_product, text_in_image=text_in_image,
                        headline=headline, subtext=subtext) +
            _operator_instruction_clause(operator_instruction) +
            f"A premium skincare advertisement image for Besque, a natural body-oil brand for women 40+. "
            f"Composition and setting: {layout}. (If this implies a person, render them per compliance "
            f"rule C1 - a generic, non-identifiable model, never the specific individual described.) "
            + product_clause +
            f"Palette and mood: {palette}. Text placement: {text_placement}. "
            f"Square 1:1 aspect ratio composition. "
            + PRODUCTION_STYLE_GUIDANCE.get(prod_style, DEFAULT_STYLE_GUIDANCE) +
            closing
        )
    return prompt

# ---- Live single-pass image generation (nano banana via Gemini API) ----
from google import genai
from pathlib import Path

ASSET_DIR = Path(os.getenv("ASSET_DIR", "assets"))

# Rules 1-5 never change. Rules 6/7 have a conditional branch each (text_in_image,
# include_product); rule 8 is new and unconditional. Kept as module-level pieces, not one
# flat string, so brand_rules() can compose them per-call while
# test_brand_rules_default_reproduces_prior_rules_verbatim proves the default call still
# produces the exact old BRAND_RULES text through rule 7.
_RULES_1_TO_5 = (
    "STRICT RULES - NEVER VIOLATE: "
    "1) Any Besque bottle label must show ONLY the exact product name provided, nothing else. "
    "2) NEVER copy the competitor's product name, brand name, claims, or any label text onto the Besque product. "
    "3) NEVER invent ingredients, percentages, or product names. "
    "4) If no product name is provided, the bottle shows only the word 'Besque'. "
    "5) The product is always a body OIL in a glass bottle unless stated otherwise - never a cream, jar, or tub. "
)


def _rule6_text_policy(text_in_image=False, headline=None, subtext=None):
    """Rule 6, TEXT POLICY. Default (text_in_image=False) is the original blanket-ban
    wording, verbatim. When text_in_image is True AND a headline was actually supplied,
    the ban is replaced with a named allow-list of exactly that headline/subtext - never a
    generic "headline is now OK" opening, so nothing beyond the approved copy can slip in.
    A True flag with no headline supplied falls back to the default (nothing confirmed to
    render, so nothing is permitted)."""
    if text_in_image and headline:
        permitted = f"the headline \"{headline}\""
        if subtext:
            permitted += f" and the supporting text \"{subtext}\""
        return (
            f"6) TEXT POLICY (STRICT, TEXT-IN-IMAGE MODE): the ONLY text permitted anywhere "
            f"in the image is {permitted}, rendered as in-scene typography, plus the Besque "
            f"product's own printed label. NEVER render any price, discount, percentage, "
            f"offer, badge, sticker, sticky note, extra caption, tagline, watermark, or extra "
            f"logo, whether copied from the competitor ad or invented. "
        )
    return (
        "6) TEXT POLICY (STRICT): the Besque product's own printed label — exactly as shown on "
        "the reference product photo — is the ONLY text permitted anywhere in the image. NEVER "
        "render any headline, price, discount, percentage, offer, badge, sticker, sticky note, "
        "caption, tagline, watermark, or extra logo, whether copied from the competitor ad or "
        "invented. "
    )


def _rule7_product_policy(include_product=True):
    """Rule 7, PRODUCT POLICY. Default (include_product=True) is the original wording,
    verbatim. include_product=False relaxes it entirely into a productless mode for
    educational/illustrative ads (e.g. glp1) - no Besque bottle, label, or branding at
    all, rather than the default's "exactly one bottle" framing."""
    if include_product:
        return (
            "7) PRODUCT POLICY (STRICT): the single product in the reference product photo is "
            "the ONLY product permitted anywhere in the image — exactly one bottle, and it is "
            "that one. If no reference product photo is supplied, exactly one Besque bottle "
            "matching the product description is permitted. A multi-product range, collection, "
            "bundle, gift set or line-up in the source ad is a layout to borrow, not an "
            "inventory to reproduce: keep its composition, lighting and mood, collapse it to a "
            "single-product composition, and leave the freed area as clean negative space. "
            "NEVER add a second bottle, a variant, a size sibling, a refill, a carton, a box, or "
            "any further SKU, whether copied from the competitor ad or invented. "
        )
    return (
        "7) PRODUCT POLICY (STRICT, PRODUCTLESS MODE): this is a deliberately productless, "
        "educational/illustrative image - no product is being sold or shown. NO Besque bottle, "
        "product, label, or branding of any kind may appear anywhere in the image, whether "
        "copied from the competitor ad or invented. Do not render any bottle, jar, tube, or "
        "packaging, Besque or otherwise. "
    )


# New rule, unconditional. Regression guard for a real failure: a blueprint's layout
# description (composition/framing instructions like "stacked headline over product shot")
# got read as literal content and the words "Stacked HeadLine" were rendered as visible
# typography in the image.
_RULE_8_LAYOUT_IS_COMPOSITION = (
    "8) LAYOUT DESCRIPTORS ARE COMPOSITION, NOT TEXT (STRICT): any layout, subject, or framing "
    "description supplied above (words like 'stacked', 'headline', 'split-screen', 'grid', "
    "'banner') is an instruction about how visual elements are arranged in the frame ONLY. "
    "NEVER render a layout or composition descriptor's own words as literal visible typography "
    "in the image - the words 'headline', 'stacked', or similar must never themselves appear "
    "as text on the image. The only text permitted anywhere is governed by rule 6 above. "
)

# New rule, EDIT MODE only. In edit mode Gemini is handed the competitor's own ad as an
# image Part, not just a text description of it - it contains their real product,
# packaging, logo, and brand name in frame. Rules 6/7 above still decide exactly what text
# and product may appear, but they now have to survive a real photograph being edited
# rather than a from-scratch generation, so this states explicitly that none of the
# source image's own branding may carry through.
_RULE_9_SOURCE_IMAGE_IS_THE_COMPETITORS_AD = (
    "9) SOURCE IMAGE IS THE COMPETITOR'S OWN AD (STRICT, EDIT MODE): the attached reference "
    "image being reproduced is the competitor's own advertisement - it contains their real "
    "product, packaging, logo, and brand name. Every brand mark belonging to the "
    "competitor - logo, emblem, watermark, roundel, badge, seal, or any other brand mark, "
    "wherever it sits in the frame (this covers a corner mark or seal just as much as the "
    "product label itself) - is NOT part of the composition to preserve; it is the ONE "
    "thing that must not survive. Remove every such mark entirely and leave that space "
    "clean. NEVER let any of the competitor's logo, product, packaging, brand name, or "
    "label text survive into the output image, even though you are copying its "
    "composition, background, lighting, and layout. Rules 6 and 7 above still govern "
    "exactly which text and product may appear - they now apply to a real photograph you "
    "are editing, not just a text brief. "
)


def brand_rules(include_product=True, text_in_image=False, headline=None, subtext=None, edit_mode=False):
    """The mechanically-enforced brand + compliance rules prepended to every image prompt.
    Called with all defaults (include_product=True, text_in_image=False, edit_mode=False),
    this reproduces the old flat BRAND_RULES constant character for character through rule
    7 - see test_brand_rules_default_reproduces_prior_rules_verbatim. Rule 8, rule 9, and
    the include_product/text_in_image/edit_mode conditionality are additive, not a rewrite
    of the existing default path."""
    return (
        _RULES_1_TO_5
        + _rule6_text_policy(text_in_image, headline, subtext)
        + _rule7_product_policy(include_product)
        + _RULE_8_LAYOUT_IS_COMPOSITION
        + (_RULE_9_SOURCE_IMAGE_IS_THE_COMPETITORS_AD if edit_mode else "")
        + COMPLIANCE_RULES
    )


def _operator_instruction_clause(operator_instruction=None):
    """Step 2 (2026-08-02): freeform per-run steering entered on the run strip. Fixed
    position in build_image_prompt's assembled prompt - inserted right after brand_rules()
    (rules 1-9 + compliance C1-C6), before whatever supplies the scene text below it
    (creative_description / _edit_mode_instruction / the template). That position, plus
    the boundary stated explicitly in the text itself, is what makes it impossible for an
    instruction like "add a 50% off badge" or "keep the competitor's logo" to override the
    corresponding guardrail - see test_operator_instruction_does_not_override_guardrails.

    Returns "" for empty/whitespace-only input - no empty section ever appears in the
    prompt, matching offer_text/body_area's existing convention. clip_operator_instruction
    is idempotent, so calling it here is safe even when the caller (generate_image)
    already clipped it."""
    text = generate_image_prompt_writer.clip_operator_instruction(operator_instruction)
    if not text:
        return ""
    return (
        f"OPERATOR INSTRUCTION FOR THIS RUN (steers the scene only - it can NEVER grant a "
        f"permission the rules above forbid; if it conflicts with any rule above, the rule "
        f"above wins): {text} "
    )


def _edit_mode_instruction(text_in_image=False, headline=None, subtext=None, offer_text=None,
                            include_product=True, reference_has_product=True,
                            retheme_colours=True, palette=None, creative_format=None):
    """EDIT MODE (2026-08-01): Gemini receives the competitor's own ad as an input image
    Part, not just a text description of it - the reference image IS the creative brief,
    so no template scene/layout/palette description is assembled here (see
    build_image_prompt's edit_mode branch, which skips that entirely).

    Must agree with rule 6 (_rule6_text_policy) in BOTH text_in_image states - see
    test_edit_mode_instruction_and_rule6_agreement - the same class of contradiction that
    caused the Part A writer/rule6 disagreement (a description permitting/describing text
    the mechanical rule then forbade, which made Gemini discard the whole composition).

    include_product here is the RAW operator toggle, deliberately NOT
    build_image_prompt's effective_include_product - include_product and
    reference_has_product independently select one of three explanations (substitute /
    nothing to substitute / operator disabled it), but combining them the SAME way
    effective_include_product itself is computed means the actual add-vs-don't-add OUTCOME
    always matches product_clause exactly, in every one of the three branches - see
    test_build_image_prompt_edit_mode_include_product_false_unaffected_by_reference_has_product.
    Passing the already-narrowed effective value instead would collapse "operator wanted a
    product but the reference had none" into "operator disabled it", losing exactly the
    distinction Part 4 exists to state.

    reference_has_product=False (Step 2, Part 4) only changes WHICH sentence explains an
    outcome where no product is added: the reference ad itself has no product in frame
    (blueprint.layout_detail.product_count==0 or product_category=="not_product"), so the
    wording says there's nothing to substitute rather than a generic productless-mode
    sentence - distinguishing "the operator asked for no product" from "the source had
    none to substitute" without changing the actual (non-contradictory) outcome.

    retheme_colours=True (Prompt 4, Item 5, default) states palette substitution as ONE
    instruction integrated with the reproduce-faithfully instruction, not two that could
    read as competing - "geometry is preserved, colour is substituted" is a single
    sentence naming both halves, never a separate clause that could be read as
    contradicting "reproduce faithfully". retheme_colours=False reverts to the exact
    original wording (colour palette folded into the reproduce list) - the doc's own
    stated exception, and the faithful-clone behaviour already validated in production."""
    if retheme_colours:
        effective_palette = palette or "terracotta, maroon, gold, cream"
        opening = (
            "EDIT MODE: the FIRST attached image is the competitor's own advertisement. "
            "This is a single instruction with two parts, not two competing ones: "
            "geometry is preserved, colour is substituted. Composition, layout, camera "
            "angle, spacing, lighting direction, contrast relationships, tonal "
            "hierarchy, and text placement all carry over EXACTLY as shot in the "
            "reference - do not change the framing, angle, spacing, or structure in any "
            f"way. At the same time, every hue in the scene (background, props, "
            f"wardrobe, surfaces) re-maps to Besque's palette: {effective_palette} - "
            f"overriding the reference's own colours entirely. "
        )
    else:
        opening = (
            "EDIT MODE: the FIRST attached image is the competitor's own advertisement. "
            "Reproduce its composition, background, camera angle, lighting, colour "
            "palette, text placement, and overall layout as closely as possible. "
        )

    if include_product and reference_has_product:
        base = opening + (
            "Changing ONLY the product. Remove the competitor's product entirely and "
            "place the Besque product (shown in the reference photo(s) that follow, if "
            "any) in its position, at its scale, with its lighting, matching the "
            "original shot as faithfully as possible. "
            "Any substance in frame that ORIGINATES FROM THE PRODUCT - a drip, pour, pool, "
            "droplet, smear, texture swatch, or a smear on skin - is part of the product, "
            "not the scene: preserve its position, volume, and motion exactly, but "
            "recolour and re-texture it to match OUR product's actual colour and texture, "
            "never the reference's own product substance (e.g. a clear serum drip must "
            "become our golden-amber oil, not stay clear). \"Preserve everything except the "
            "product\" means this too - a product-derived substance is the product, even "
            "when it has left the bottle. Everything else in the scene stays exactly as it "
            "appears in the source image. "
        )
    elif include_product and not reference_has_product:
        base = opening + (
            "The reference image has NO product in frame - do NOT add a Besque product, "
            "bottle, or packaging anywhere in the scene; there is nothing to substitute "
            "here. Everything else in the scene stays exactly as it appears in the "
            "source image. "
        )
    else:
        base = opening + (
            "This is a deliberately productless edit - do NOT add any Besque product, "
            "bottle, or packaging anywhere in the scene. Everything else in the scene "
            "stays exactly as it appears in the source image. "
        )
    if text_in_image and headline:
        permitted = f'the headline "{headline}"'
        if subtext:
            permitted += f' and the supporting text "{subtext}"'
        typo_guidance = TYPOGRAPHY_GUIDANCE.get(creative_format, DEFAULT_TYPOGRAPHY_GUIDANCE)
        base += (
            f"TEXT: preserve the reference image's text zones, size, and position "
            f"EXACTLY as they appear - but replace the wording with {permitted} only, "
            f"same layout, our words. Use Besque's OWN typeface style here, never the "
            f"reference's own font: {typo_guidance}. The competitor's brand name, "
            f"product name, and claims must NEVER survive into the output, even inside "
            f"this replacement typeface. "
        )
    else:
        base += (
            "TEXT: leave the reference image's text zones as clean, empty space in the "
            "SAME positions they occupy in the source image - do not render any text, "
            "headline, or competitor wording there; that space will be filled later as a "
            "separate HTML overlay. "
        )
    if offer_text:
        base += (
            f"OFFER: if the reference shows an offer, discount, price, or CTA button, "
            f"reproduce its shape and position but with ONLY this exact wording: "
            f"{offer_text}. Do not invent a different number, percentage, or term. "
        )
    else:
        base += (
            "OFFER: no offer was supplied for this run - no urgency phrasing, discount, "
            "price, or CTA button text may appear anywhere in the image, even if the "
            "reference has one. Reproduce any such button's shape and position as clean "
            "empty space or neutral wording only, never the competitor's urgency wording. "
        )
    base += (
        "EFFICACY CLAIMS: describe NO quantified efficacy claim of any kind - no "
        "percentage improvement (e.g. '+25% more moisturised'), no ratio ('3x more "
        "effective', 'twice as fast'), and no timescale ('in just 7 days') - even if the "
        "reference shows one. None has been approved for this run. "
    )
    return base

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

# Per-creative-format typeface guidance (Prompt 4, Item 5): map the FORMAT to a Besque
# typeface style rather than copying the reference's own font - "map creative_format to a
# typeface style rather than copying the reference's". Direct-response formats get clean
# sans-serif for scannability/urgency; premium/editorial formats get elegant serif;
# testimonial_review gets a handwritten-marker feel (a real customer's own note), the
# whiteboard/diagram-style annotation register the spec names.
TYPOGRAPHY_GUIDANCE = {
    "before_after": "clean, bold sans-serif - direct-response clarity and urgency",
    "problem_solution": "clean, bold sans-serif - direct-response clarity and urgency",
    "offer_led": "clean, bold sans-serif - direct-response clarity and urgency",
    "comparison": "clean, bold sans-serif - direct-response clarity",
    "listicle_tips": "clean sans-serif - easy to scan, direct-response clarity",
    "product_hero": "elegant serif - premium, editorial, aspirational",
    "founder_story": "elegant serif - premium, editorial, aspirational",
    "ingredient_focus": "elegant serif - premium, editorial, aspirational",
    "lifestyle_scene": "elegant serif - premium, editorial, aspirational",
    "text_led_editorial": "elegant serif - premium, editorial, aspirational",
    "testimonial_review": "handwritten marker - informal and personal, like a real customer's own note",
}
# Same coverage guarantee as PRODUCTION_STYLE_GUIDANCE above - a schema addition to
# creative_format can't silently fall through to DEFAULT_TYPOGRAPHY_GUIDANCE unnoticed.
from src import validator as _validator
assert set(_validator.creative_formats()) <= set(TYPOGRAPHY_GUIDANCE), (
    "TYPOGRAPHY_GUIDANCE is missing an entry for one of validator.creative_formats()"
)
del _validator

# Used when creative_format is absent/null/unrecognised.
DEFAULT_TYPOGRAPHY_GUIDANCE = "clean sans-serif - versatile, legible default"


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


def _sniff_mime_type(data):
    """Magic-byte sniff, same fallback-to-jpeg logic as deconstruct.py's
    _b64_from_bytes/_load_image_b64_v2 - duplicated rather than imported since it's a
    one-line lookup and deconstruct.py already duplicates it twice itself."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:4] == b"GIF8":
        return "image/gif"
    return "image/jpeg"


# Item 6a (2026-08-04): edit mode must clone the reference ad's own shape, not a hardcoded
# square - a portrait reference cloned to a hardcoded 1:1 isn't a clone. google.genai.types.
# ImageConfig.aspect_ratio's own field description only lists "1:1", "2:3", "3:2", "3:4",
# "4:3", "9:16", "16:9", "21:9" - but that description is stale documentation, not a
# client-side constraint (the field is a plain untyped str). "4:5"/"5:4" are added here on
# the strength of a live probe against the real gemini-3.1-flash-image model (not this
# model's first-party docs, which only confirm 4:5/5:4 for the separate Lite variant, nor
# the SDK docstring, which doesn't mention them at all): a real 1080x1350 (exactly 4:5)
# competitor reference, edited with aspect_ratio="4:5" explicitly, succeeded with no error
# and returned a 928x1152 (~4:5) image - see scratchpad probe results, 2026-08-04. 23.8% of
# this project's own stored competitor references (24/101) sit at ~4:5 and previously
# mis-snapped to 3:4 under the old 8-value table.
SUPPORTED_ASPECT_RATIOS = {
    "1:1": 1.0,
    "2:3": 2 / 3,
    "3:2": 3 / 2,
    "3:4": 3 / 4,
    "4:3": 4 / 3,
    "4:5": 4 / 5,
    "5:4": 5 / 4,
    "9:16": 9 / 16,
    "16:9": 16 / 9,
    "21:9": 21 / 9,
}


def derive_aspect_ratio(reference_bytes):
    """The SUPPORTED_ASPECT_RATIOS key nearest reference_bytes' own width:height, compared
    on a log scale (so a ratio and its reciprocal, e.g. 2:1 vs 1:2, aren't treated as
    equidistant from 1:1 the way raw subtraction would). Returns None - never raises -
    when reference_bytes is falsy or Pillow can't read it (corrupt, truncated, or
    zero-dimension). generate_image treats None as "omit image_config entirely, don't
    force a ratio" plus a pipeline_warning, not a failed draft - a live probe (2026-08-04)
    confirmed gemini-3.1-flash-image infers and preserves the ATTACHED reference image's
    own aspect ratio when no image_config is given at all, which is only relevant when
    reference_bytes exists but Pillow specifically couldn't decode it (still attached as
    the input Part regardless of whether this function could read it) - so omitting the
    config is a strictly better fallback than forcing "1:1", which is guaranteed wrong for
    exactly the portrait/landscape refs this function exists to get right."""
    if not reference_bytes:
        return None
    try:
        with Image.open(io.BytesIO(reference_bytes)) as img:
            width, height = img.size
    except Exception:
        return None
    if not width or not height:
        return None
    target = width / height
    return min(
        SUPPORTED_ASPECT_RATIOS,
        key=lambda name: abs(math.log(target) - math.log(SUPPORTED_ASPECT_RATIOS[name])),
    )


def generate_image(blueprint, ad_id, product=None, reference_images=None, angle_slug=None,
                    include_product=True, text_in_image=False, headline=None, subtext=None,
                    messaging_angle=None, realism=None, body_area=None, offer_text=None,
                    edit_mode=False, competitor_image_bytes=None, operator_instruction=None,
                    retheme_colours=True):
    """Single-pass image generation from the blueprint. One image, no iteration.
    Saves to assets/<stem>_draft.png (stem = ad_id, or ad_id+angle if angle_slug is given)
    and returns the path. Returns None on failure. include_product/text_in_image/headline/
    subtext are forwarded to build_image_prompt/brand_rules - defaults reproduce today's
    behaviour exactly.

    messaging_angle (resolved angle dict) gates the Claude prompt-writer pass: only runs
    when an angle is selected AND edit_mode is off, matching every other angle-driven
    behaviour in this pipeline. realism/body_area/offer_text are handed to the writer as
    creative context - this is the first (and only) place any of the three are actually
    consumed when the writer runs; if it doesn't (no angle, or edit_mode), they have no
    effect on the image side. The writer's failure mode is silent-by-design:
    write_creative_description never raises, it returns None, and build_image_prompt's
    creative_description=None branch is exactly today's template assembly - so a writer
    failure degrades to the pre-Part-5 prompt, never to nothing.

    edit_mode=True (competitor_image_bytes given) skips the writer entirely - the reference
    IS the brief, a text creative_description would just fight it - and attaches
    competitor_image_bytes to Gemini as an input Part ahead of the product reference
    photos, clearly distinguished in the framing text: one is the ad to reproduce, the
    others are the Besque product to substitute in. Defaults (edit_mode=False,
    competitor_image_bytes=None) reproduce today's generate-only behaviour exactly.
    offer_text is ALSO forwarded to build_image_prompt now (not just the writer) so edit
    mode's _edit_mode_instruction OFFER branch actually receives it - edit mode skips the
    writer, so build_image_prompt is the only path it has left to reach.

    operator_instruction (Step 2) is clipped ONCE here (clip_operator_instruction is
    idempotent, so downstream re-clipping is harmless) and forwarded to BOTH the writer
    (as inspiration-tier guidance) and build_image_prompt (as the mechanical, fixed-position
    clause - see _operator_instruction_clause) so it reaches the model whether or not the
    writer actually runs.

    retheme_colours (Prompt 4, Item 5) defaults to True - the palette itself is DATA, read
    from dedupe.get_brand_settings() here (not a hardcoded string), so a future correction
    made in the UI takes effect immediately. Only fetched when it will actually be used
    (edit_mode) - no DB read on the far more common non-edit-mode path.

    Aspect ratio (Item 6a) is edit_mode-only too: derive_aspect_ratio(competitor_image_bytes)
    snaps the reference ad's own width:height to the nearest ratio Vertex's ImageConfig
    actually supports, and that ratio is set on the generate_content call's config - not
    stated in prompt text (see build_image_prompt's edit_mode branch, which no longer
    mentions an aspect ratio at all). If competitor_image_bytes is missing or unreadable,
    this OMITS image_config from the call entirely (not a forced "1:1") and records a
    pipeline_warning rather than failing the draft - a live probe (2026-08-04) confirmed
    the model infers and preserves the attached reference image's own aspect ratio with no
    image_config at all, which is only reachable here when the bytes exist but Pillow
    couldn't decode them (still attached as the input Part either way); forcing "1:1"
    would have been guaranteed-wrong for a portrait/landscape reference in exactly that
    case. Generate mode is unaffected - it keeps stating "Square 1:1" in prompt text only,
    no config passed."""
    operator_instruction = generate_image_prompt_writer.clip_operator_instruction(operator_instruction)
    creative_description = None
    if messaging_angle and not edit_mode:
        creative_description = generate_image_prompt_writer.write_creative_description(
            blueprint, product=product, angle=messaging_angle, realism=realism,
            body_area=body_area, offer_text=offer_text,
            reference_image_count=len(reference_images or []),
            text_in_image=text_in_image, include_product=include_product,
            headline=headline, subtext=subtext, operator_instruction=operator_instruction,
        )
    brand_palette = None
    if edit_mode and retheme_colours:
        from src import dedupe as _dedupe
        brand_palette = _dedupe.get_brand_settings().get("palette")
    prompt = build_image_prompt(blueprint, product=product, include_product=include_product,
                                 text_in_image=text_in_image, headline=headline, subtext=subtext,
                                 creative_description=creative_description, edit_mode=edit_mode,
                                 offer_text=offer_text, operator_instruction=operator_instruction,
                                 retheme_colours=retheme_colours, brand_palette=brand_palette)
    stem = _draft_stem(ad_id, angle_slug)
    try:
        client = genai.Client(vertexai=True, project="besque-martech", location="global")
        from google.genai import types as genai_types
        # Defense in depth for rule 7's productless mode: the prompt already says no
        # product may appear, but don't also hand the model reference photos of one.
        reference_images = (reference_images or []) if include_product else []
        competitor_part = None
        if edit_mode and competitor_image_bytes:
            competitor_part = genai_types.Part.from_bytes(
                data=competitor_image_bytes, mime_type=_sniff_mime_type(competitor_image_bytes)
            )
        if reference_images or competitor_part is not None:
            image_parts = []
            framing = ""
            if competitor_part is not None:
                image_parts.append(competitor_part)
                framing += (
                    "FIRST IMAGE ABOVE: the competitor's own advertisement - this is THE "
                    "AD TO REPRODUCE (its composition, background, camera angle, lighting, "
                    "palette, text placement, and layout). Its product, packaging, logo, "
                    "and brand name must NOT survive - see the instructions below. "
                )
            if reference_images:
                image_parts += [genai_types.Part.from_bytes(data=img, mime_type="image/png")
                                for img in reference_images]
                framing += _reference_framing(len(reference_images))
            contents = image_parts + [framing + prompt]
        else:
            contents = prompt

        # Item 6a (2026-08-04): edit mode sets aspect ratio on the generation config,
        # derived from the reference ad's own shape - see derive_aspect_ratio - instead of
        # a hardcoded "Square 1:1" prompt-text line (removed from build_image_prompt's
        # edit_mode branch). Generate mode is untouched: it keeps stating its aspect ratio
        # in prompt text only, no config passed here.
        #
        # aspect_ratio is None (bytes missing, or present but Pillow couldn't decode them)
        # -> generation_config stays None, i.e. image_config is OMITTED from the call
        # entirely, NOT forced to "1:1". A live probe (2026-08-04, scratchpad
        # aspect_ratio_probe.py) confirmed this is the better fallback: with no
        # image_config at all, gemini-3.1-flash-image inferred and preserved a real
        # 1080x1350 (0.8000) reference's own ratio almost exactly (922x1152, 0.8003) -
        # forcing "1:1" would have been guaranteed-wrong here, the exact failure mode this
        # item exists to fix.
        generation_config = None
        if edit_mode:
            aspect_ratio = derive_aspect_ratio(competitor_image_bytes)
            if aspect_ratio is None:
                from src import dedupe as _dedupe
                _dedupe.init_pipeline_warnings()
                _dedupe.record_warning(
                    "edit_mode_aspect_ratio_fallback",
                    f"ad_id={ad_id}: could not derive aspect ratio from the reference "
                    f"image (missing or unreadable); omitting image_config so the model "
                    f"infers aspect ratio from the attached reference image itself.",
                )
            else:
                generation_config = genai_types.GenerateContentConfig(
                    image_config=genai_types.ImageConfig(aspect_ratio=aspect_ratio)
                )

        import time as _time
        response = None
        for _attempt in range(3):
            try:
                call_kwargs = {"model": "gemini-3.1-flash-image", "contents": contents}
                if generation_config is not None:
                    call_kwargs["config"] = generation_config
                response = client.models.generate_content(**call_kwargs)
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


def _edit_text_clause(text_in_image=False):
    """The text-policy clause appended to an edit-image prompt. Mirrors _rule6_text_policy's
    two branches - extracted as its own function so it's directly testable without
    mocking genai.Client, the same way brand_rules' helpers are."""
    if text_in_image:
        return (
            "Render exactly the headline and supporting text specified in rule 6 above as "
            "in-scene typography - do not leave space for a separate overlay, and do not add "
            "any other text; no competitor branding anywhere. "
        )
    return (
        "Keep the edited image completely free of overlaid marketing text — only the Besque "
        "product's own label may appear, exactly as it appears in the image being edited — and "
        "leave clean, uncluttered negative space where headline and offer text will be added "
        "later as a separate HTML overlay; no competitor branding anywhere. "
    )


def edit_image(current_image_bytes, instruction, ad_id, aspect="1:1", angle_slug=None,
                text_in_image=False, headline=None, subtext=None):
    """Edit an existing draft image with a natural-language instruction via nano banana.
    Versions the outgoing draft to {stem}_draft_v{n}.png (stem = ad_id, or ad_id+angle),
    then saves/uploads the result under the same stem's key and returns it. Returns None
    on failure.

    text_in_image/headline/subtext restore the ORIGINAL generation's rule-6 mode for this
    edit - without them, editing a text-in-image draft would silently fall back to
    brand_rules()'s defaults (no text permitted) while the closing instruction still told
    the model to keep the base free of text, directly contradicting a headline that's
    already baked into the image being edited. Callers should read these back from the
    artifact row (angle_id + text_in_image + generated_copy), never ask the operator to
    re-specify. include_product is NOT restorable here - artifacts has no column for it,
    so an edited productless (e.g. glp1) draft still uses rule 7's default (include_product
    assumed True); this is a known gap, not a silent choice.

    Edit Mode's angle_slug param exists so an angle-variant draft can't be
    versioned/overwritten under the wrong (ad_id-only) key if this function is called."""
    from google.genai import types as genai_types
    stem = _draft_stem(ad_id, angle_slug)
    prompt = (
        brand_rules(text_in_image=text_in_image, headline=headline, subtext=subtext) +
        f"Edit this Besque skincare advertisement image. Instruction: {instruction}. "
        f"Keep it a premium, editorial skincare ad. Output aspect ratio: {aspect}. "
        + _edit_text_clause(text_in_image) +
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
