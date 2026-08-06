"""Regeneration step (image prompt): turn a blueprint's visual into an image-gen prompt."""
import io
import math
import os
from PIL import Image
from src import assets, generate_image_prompt_writer
from src.compliance_rules import COMPLIANCE_RULES

IMAGE_MODEL = os.getenv("IMAGE_MODEL", "placeholder-image-model")


def resolve_effective_include_product(blueprint, include_product, edit_mode):
    """The single source for whether a Besque product is actually being substituted in -
    Item 2 (2026-08-05), extracted from build_image_prompt's own inline computation so
    pipeline.process_ad can learn the same answer instead of independently re-deriving it
    (exactly the two-independent-derivations shape that let rule 6 and the output critic
    drift apart on text_in_image - see effective_authorised_text above).

    In edit_mode, the reference ad governs whether there's a product to substitute at all:
    layout_detail.product_count==0 or product_category.category=="not_product" (both from
    deconstruct.py's blueprint schema) force this False even when the operator explicitly
    asked for a product (include_product=True), since there's nothing in the reference to
    substitute. Outside edit_mode this is a no-op (reference_has_product stays True,
    effective_include_product == include_product exactly).

    Returns (effective_include_product, reference_has_product) - the caller needs
    reference_has_product too, since include_product and reference_has_product
    independently select one of three explanations for a productless outcome (operator
    disabled it / nothing to substitute / both), the same distinction
    _edit_mode_instruction's docstring already establishes.

    Deliberately a plain function, not a build_image_prompt return value (~55 existing
    call sites treat that function's return as a bare string) and not a module-level
    attribute like generate_image.last_prompt (the exact kind of hidden coupling that
    made the text_in_image bug possible - state one function sets and another reads, with
    nothing in either signature saying so)."""
    reference_has_product = True
    if edit_mode:
        layout_detail_bp = (blueprint or {}).get("layout_detail") or {}
        product_category_bp = ((blueprint or {}).get("product_category") or {}).get("category")
        reference_has_product = not (product_category_bp == "not_product"
                                      or layout_detail_bp.get("product_count") == 0)
    return include_product and reference_has_product, reference_has_product


def build_image_prompt(blueprint: dict, product: dict = None, include_product: bool = True,
                        text_in_image: bool = False, headline: str = None, subtext: str = None,
                        creative_description: str = None, edit_mode: bool = False,
                        offer_text: str = None, operator_instruction: str = None,
                        retheme_colours: bool = True, brand_palette: str = None,
                        realism: str = None, critic_feedback: list = None) -> str:
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
    ever saw it.

    substance_colour (Item 6b) is read straight from product.get("substance_colour") here
    in the edit_mode branch only - it's already part of the product dict already passed
    in, the same "read it directly, don't add a parameter" reasoning that used to apply to
    creative_format here too, before Item C (2026-08-05) removed that read entirely - the
    TEXT branch now inherits the reference's own typography rather than mapping
    creative_format to a Besque typeface, so there's nothing left to read it for. See
    _substance_recolour_clause for what happens when substance_colour is unset."""
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

    effective_include_product, reference_has_product = resolve_effective_include_product(
        blueprint, include_product, edit_mode
    )

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
            _critic_feedback_clause(critic_feedback) +
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
                                   substance_colour=(product or {}).get("substance_colour"),
                                   style=(realism or "").strip() or prod_style,
                                   typography_zones=blueprint.get("typography_zones")) +
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
            _critic_feedback_clause(critic_feedback) +
            creative_description.strip() + " "
            + product_clause
            + _bottle_fixed_clause() + _register_lighting_only_clause() +
            f"Square 1:1 aspect ratio composition. " +
            closing
        )
    else:
        prompt = (
            brand_rules(include_product=include_product, text_in_image=text_in_image,
                        headline=headline, subtext=subtext) +
            _operator_instruction_clause(operator_instruction) +
            _critic_feedback_clause(critic_feedback) +
            f"A premium skincare advertisement image for Besque, a natural body-oil brand for women 40+. "
            f"Composition and setting: {layout}. (If this implies a person, render them per compliance "
            f"rule C1 - a generic, non-identifiable model, never the specific individual described.) "
            + product_clause +
            f"Palette and mood: {palette}. Text placement: {text_placement}. "
            f"Square 1:1 aspect ratio composition. "
            + generate_image_prompt_writer.STYLE_GUIDANCE.get(prod_style, DEFAULT_STYLE_GUIDANCE)
            + _bottle_fixed_clause() + _register_lighting_only_clause() +
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


MAX_SUBTEXT_WORDS = 12


def _cap_subtext(subtext):
    """Hard cap on the in-image supporting line at MAX_SUBTEXT_WORDS - mechanical, not
    just prompted, since generate_copy.py's own "under ~12 words" instruction to the
    copy step is a soft constraint the model doesn't always obey. A live failure: an
    overlong image_subtext (a mechanism paragraph, ingredient bullets, a CTA sentence)
    reached rule 6 verbatim, and rule 6 has no length limit of its own - it renders
    whatever text it's told is authorised. Truncates on a word boundary; never adds
    an ellipsis or other invented text that could itself render as content."""
    if not subtext:
        return subtext
    words = subtext.split()
    return subtext if len(words) <= MAX_SUBTEXT_WORDS else " ".join(words[:MAX_SUBTEXT_WORDS])


def effective_authorised_text(text_in_image, headline=None, subtext=None):
    """The (headline, subtext) actually authorised to render in-scene, given text_in_image -
    the single source for a condition (text_in_image and headline) that used to be
    re-derived independently at three sites (rule 6 here, _edit_mode_instruction's TEXT
    branch, and - the live bug this closes - the output critic's authorised-text line,
    which pipeline.process_ad computed with no text_in_image check at all). A False flag,
    or a True flag with no headline actually supplied, returns (None, None) - nothing
    confirmed to render, so nothing is authorised, matching rule 6's own fallback. Every
    caller that needs to know "what text is the model/critic actually told is allowed"
    must call this, not re-check text_in_image/headline itself - see
    test_rule6_and_critic_authorised_text_never_contradict, which exists because two
    independent truthy checks drifted apart in exactly this way.

    subtext is passed through _cap_subtext here - the ONE place both rule 6 and
    _edit_mode_instruction's TEXT branch get their authorised text from, so a
    generation-time cap can never be bypassed by one caller and not the other."""
    if text_in_image and headline:
        return headline, _cap_subtext(subtext)
    return None, None


def _rule6_text_policy(text_in_image=False, headline=None, subtext=None):
    """Rule 6, TEXT POLICY. Default (text_in_image=False) is the original blanket-ban
    wording, verbatim. When text_in_image is True AND a headline was actually supplied,
    the ban is replaced with a named allow-list of exactly that headline/subtext - never a
    generic "headline is now OK" opening, so nothing beyond the approved copy can slip in.
    A True flag with no headline supplied falls back to the default (nothing confirmed to
    render, so nothing is permitted)."""
    headline, subtext = effective_authorised_text(text_in_image, headline, subtext)
    if headline:
        permitted = f"the headline \"{headline}\""
        if subtext:
            permitted += f" and the supporting text \"{subtext}\""
        return (
            f"6) TEXT POLICY (STRICT, TEXT-IN-IMAGE MODE): the ONLY text permitted anywhere "
            f"in the image is {permitted}, rendered as in-scene typography, plus the Besque "
            f"product's own printed label. This is the ENTIRE text budget for this image - "
            f"no ingredient list, mechanism or benefit paragraph, additional body copy, or "
            f"CTA sentence may ALSO be rendered, even if such text exists in the product "
            f"description, generated copy, or reference ad. NEVER render any price, discount, "
            f"percentage, offer, badge, sticker, sticky note, extra caption, tagline, "
            f"watermark, or extra logo, whether copied from the competitor ad or invented. "
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


def _critic_feedback_clause(critic_feedback=None):
    """The corrective-retry loop (2026-08-05): output_critic.check_draft's findings from
    the PREVIOUS attempt at this exact (ad_id, angle_id), fed back as explicit corrections
    for a single regeneration - see pipeline.process_ad's MAX_IMAGE_ATTEMPTS loop. Fixed
    position, same reasoning as _operator_instruction_clause: right after brand_rules() +
    operator instruction, before whatever supplies the scene text, so a correction can
    never be read as competing with the rules it exists to enforce - it IS those rules,
    restated against what the model actually did wrong last time.

    critic_feedback is a list of short strings (typically "{category}: {description}" per
    finding) - never the raw findings dicts, so this stays a plain string-formatting
    concern with no knowledge of output_critic's JSON shape. Returns "" when empty/None,
    same convention as every other optional clause here - a first-attempt generation (no
    prior findings) produces byte-for-byte the same prompt as before this existed."""
    if not critic_feedback:
        return ""
    bullets = " ".join(f"({i}) {item}" for i, item in enumerate(critic_feedback, start=1))
    return (
        f"CORRECTIONS FROM THE PREVIOUS ATTEMPT (STRICT, overrides anything above except "
        f"the rules and compliance text above it): a reviewer inspected the last generation "
        f"of this exact image and found it violated the following - every one of these is a "
        f"real defect in what was actually rendered, not a hypothetical, and every one must "
        f"be fixed this time, none may repeat in any form: {bullets} "
    )


def _substance_recolour_clause(substance_colour=None):
    """Item 6b (2026-08-04): a product-derived substance in frame (a drip, pour, pool,
    droplet, smear, texture swatch, or a smear on skin) must take OUR product's real
    colour, not the reference's. substance_colour is products.substance_colour - when set,
    it's NAMED explicitly (e.g. "bright golden-amber oil"), replacing the old generic
    "match OUR product's actual colour and texture" wording entirely, since pointing at a
    colour ("match the product") is strictly weaker than naming it. When unset (None or
    empty), this reproduces the exact original wording verbatim, including its own
    hardcoded "golden-amber oil" example - not a regression, since parsing
    visual_description for a real value was explicitly ruled out (prose, not reliably
    parseable) and inventing one here would be exactly the fabrication compliance rule C3
    (never invent product facts) forbids."""
    if substance_colour:
        return (
            "Any substance in frame that ORIGINATES FROM THE PRODUCT - a drip, pour, pool, "
            "droplet, smear, texture swatch, or a smear on skin - is part of the product, "
            "not the scene: preserve its position, volume, and motion exactly, but "
            f"recolour and re-texture it to our product's actual colour and texture - "
            f"{substance_colour} - never the reference's own product substance (e.g. a "
            f"clear serum drip must become our {substance_colour}, not stay clear). "
            "\"Preserve everything except the product\" means this too - a product-derived "
            "substance is the product, even when it has left the bottle. "
        )
    return (
        "Any substance in frame that ORIGINATES FROM THE PRODUCT - a drip, pour, pool, "
        "droplet, smear, texture swatch, or a smear on skin - is part of the product, "
        "not the scene: preserve its position, volume, and motion exactly, but "
        "recolour and re-texture it to match OUR product's actual colour and texture, "
        "never the reference's own product substance (e.g. a clear serum drip must "
        "become our golden-amber oil, not stay clear). \"Preserve everything except the "
        "product\" means this too - a product-derived substance is the product, even "
        "when it has left the bottle. "
    )


# Item 6d (2026-08-04): the container-type list was typed out three times (here, the TEXT
# clause, and the OFFER clause) - a shared constant so a refactor adding/removing a type
# only has to change one place. Deliberately NOT iterated by every coverage test below
# (see test_suppression_exception_names_every_container_type) - a test that derives its
# expectation from this same constant would silently follow it if an entry were ever
# deleted here, and still pass; at least one test keeps the names as a hardcoded literal
# so the constant itself stays pinned to something outside the source file.
_SUPPRESSIBLE_CONTAINER_TYPES = ("badge", "pill", "oval", "button", "banner", "ribbon", "starburst")


def _container_list_phrase():
    return ", ".join(_SUPPRESSIBLE_CONTAINER_TYPES[:-1]) + ", or " + _SUPPRESSIBLE_CONTAINER_TYPES[-1]


# Item 6c/6d (2026-08-04): stated as ONE partition of the reproduce-faithfully instruction,
# same class as item 5's retheme_colours integration - "geometry carries over EXACTLY...
# in any way" would directly contradict a later instruction to remove a container if the
# two were left as separate, unrelated clauses. This is the exception clause folded into
# the SAME opening paragraph that makes the full-preservation claim, naming it as the one
# thing full preservation doesn't cover, rather than a competing statement appearing only
# later in TEXT/OFFER. Real failure this fixes: a draft rendered an empty green "Don't
# Miss Out!" oval with no text in it, and six empty callout bubbles - the container
# survived because nothing ever said it shouldn't.
#
# Covers BOTH suppressed-text containers (6c) and suppressed-offer containers (6d) -
# initially 6c only covered text_in_image/headline, but an offer is independently
# suppressible (offer_text falsy) regardless of whether a headline is shown, so a
# text_in_image=True + offer_text=None run had opening claim UNQUALIFIED full geometry
# preservation while the OFFER clause below still removed a container - the exact
# contradiction 6c was built to prevent, just for a different suppressed category. Found
# during 6d, not by a live run.
#
# Item D (2026-08-05): a THIRD partition of the same suppressing_offer condition, not a
# new mechanism - offer_text truthy already made suppressing_offer False here (the offer
# container was never in the removed set), so the exception clause above already reads
# correctly for substitution: only the OFFER clause below needed strengthening, from
# naming just "shape and position" to naming position/shape/size/colour/typography
# explicitly, matching TEXT's own inheritance wording (Item C) - the badge is preserved
# exactly, only its wording changes, same as a headline is preserved exactly, only its
# wording changes.
#
# Item E (2026-08-06, PART B2): a THIRD suppressible category, not just a third partition
# of an existing one - an efficacy-claim badge (e.g. a "+61% more supple skin" roundel) is
# its own container, structurally distinct from both TEXT and OFFER (a reference's own
# layout_detail.zone_positions can list "efficacy badge mid-left" and "offer + CTA banner
# bottom-right" as two separate zones). The EFFICACY CLAIMS clause below already bans the
# WORDING unconditionally (no approved_claims threading to images exists, so this is
# always suppressed in edit mode, never toggled) - but nothing removed the badge SHAPE
# itself, the exact empty-container failure 6c/6d already closed for TEXT/OFFER,
# recurring for a third, structurally distinct category. Found by tracing a real
# reference's blueprint against the assembled prompt, not by a live run.
#
# Generalized to any subset of the three categories rather than an if/elif chain, since a
# fourth category later would otherwise mean enumerating 2**4-1 combinations by hand.
def _suppressed_container_exception(suppressing_text, suppressing_offer, suppressing_efficacy):
    active = []
    if suppressing_text:
        active.append("text")
    if suppressing_offer:
        active.append("an offer")
    if suppressing_efficacy:
        active.append("an efficacy-claim badge")
    if not active:
        return ""
    if len(active) == 1:
        joined = active[0]
    elif len(active) == 2:
        joined = f"{active[0]} or {active[1]}"
    else:
        joined = f"{active[0]}, {active[1]}, or {active[2]}"
    removed = f"any container holding {joined} that's being suppressed this run"
    return (
        f"The ONE exception to full geometry preservation: {removed} - "
        f"{_container_list_phrase()}, or a tiled promotional background pattern - is "
        f"removed entirely, not preserved empty; see TEXT/OFFER/EFFICACY CLAIMS below for "
        f"exactly which elements this covers and how the freed area is healed. "
    )


def _typography_zones_clause(typography_zones):
    """PART B3b (2026-08-06): per-zone typographic TREATMENT, not just per-zone content.
    The TEXT clause below already says "same size, position, weight, casing, and text
    colour" as ONE blanket instruction covering whichever zone gets the headline/subtext -
    fine when the reference has one typographic level, but a real reference with four
    distinct levels (a large serif headline, a gold small-caps accent line with a pipe
    divider, small sans body copy, a CTA button label) produced a draft with only two
    (serif headline, plain white body) - nothing named the other two levels explicitly, so
    Gemini defaulted to applying one style everywhere.

    Content substitution - which words land in which zone, and whether a zone survives at
    all - is governed elsewhere (rule 6, TEXT, OFFER, the container-removal exception
    above); this clause states HOW each zone that DOES survive is dressed, so distinct
    levels can't collapse into one by default. blueprint.typography_zones is optional
    (schema addition, 2026-08-06) - blueprints without it (every one deconstructed before
    this existed) produce "" here, same as every other optional clause in this module."""
    if not typography_zones:
        return ""
    lines = []
    for z in typography_zones:
        parts = [
            f"{z.get('typeface_class') or '?'} typeface", f"{z.get('weight') or '?'} weight",
            f"{z.get('case') or '?'} case", f"{z.get('letter_spacing') or '?'} letter-spacing",
            f"colour {z.get('colour') or '?'}", f"{z.get('size_relative') or '?'} relative to the frame",
        ]
        deco = z.get("decorative_elements") or []
        if deco:
            parts.append("with " + ", ".join(deco))
        lines.append(f"- {z.get('zone') or 'unnamed zone'}: {', '.join(parts)}, "
                     f"{z.get('line_count') if z.get('line_count') is not None else '?'} line(s)")
    zone_list = " ".join(lines)
    return (
        f"TYPOGRAPHIC LEVELS (STRICT): the reference has {len(typography_zones)} distinct "
        f"typographic level(s) below, each with its OWN treatment - reproduce every one of "
        f"them exactly as described, never collapsing two into one and never rendering "
        f"every zone in the same style. Whatever wording each zone actually receives is "
        f"governed by the rules above (TEXT/OFFER) - this only states HOW that zone is "
        f"dressed, for whichever zones survive: {zone_list} "
    )


def _bottle_fixed_clause():
    return (
        "The Besque bottle's geometry, proportions, and label text/layout are FIXED - "
        "never subject to re-theming, style adaptation, or creative variation, and never "
        "changed unless the operator's instruction explicitly names the bottle. "
    )


def _register_lighting_only_clause():
    return (
        "Only the bottle's lighting, grading, and finish adapt to match the rendering "
        "register - lit like a phone photo in a UGC frame, like a studio product shot in a "
        "studio frame, rendered in that illustration's own style in an illustrated frame - "
        "always the same bottle, same shape, same label. Never a hand-drawn bottle inside a "
        "photographic frame, never a photographic bottle inside an illustrated frame. "
    )


def _register_clause(style):
    """Edit-mode only - this is _edit_mode_instruction's single caller, appended straight
    onto `opening` (Chunk 13 follow-up). generate_image_prompt_writer.STYLE_GUIDANCE is
    written for two different jobs depending on mode: in generate mode (no reference) it
    directs the writer freely; here, the reference image already IS the register, so the
    same vocabulary may only describe HOW to render what's already in frame (grain,
    lighting quality, linework, label handling) - never introduce a framing, lighting, or
    composition choice the reference doesn't show. That exception is stated FIRST, folded
    into this same clause before the vocabulary itself, rather than appended after it -
    same shape as _suppressed_container_exception (6c/6d): one clause that never
    contradicts `opening`'s reproduce-faithfully instruction, not two that could be read
    as competing. If the vocabulary below and the reference ever disagree, the reference
    wins - stated explicitly so a phrase like "casual and unposed" or "propped on a
    surface" is read as texture, not as license to re-stage the shot."""
    if not style:
        return ""
    guidance = generate_image_prompt_writer.STYLE_GUIDANCE.get(style, DEFAULT_STYLE_GUIDANCE)
    return (
        f"REGISTER: the reference image is already shot in the {style} register - match "
        f"its own rendering exactly. Use the vocabulary below only to describe how to "
        f"render what the reference already shows; never let it introduce a framing, "
        f"lighting, or composition choice the reference doesn't have - composition and "
        f"framing are governed entirely by the reproduce-faithfully instruction above, and "
        f"if this vocabulary ever conflicts with what the reference actually shows, "
        f"faithful reproduction wins. {guidance} "
        + _bottle_fixed_clause()
        + _register_lighting_only_clause()
    )


def _edit_mode_instruction(text_in_image=False, headline=None, subtext=None, offer_text=None,
                            include_product=True, reference_has_product=True,
                            retheme_colours=True, palette=None,
                            substance_colour=None, style=None, typography_zones=None):
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
    stated exception, and the faithful-clone behaviour already validated in production.

    substance_colour (Item 6b, 2026-08-04) is products.substance_colour, forwarded by
    build_image_prompt from the product dict - see _substance_recolour_clause, which does
    the actual branching. Naming the real colour ("bright golden-amber oil") replaces the
    old generic "match OUR product's actual colour and texture" wording entirely rather
    than living alongside it, since pointing at a colour is strictly weaker than naming it,
    not a complement to it. None (unset on the product) reproduces the exact old wording,
    including its own hardcoded "golden-amber oil" example - not a regression, since that
    was always this function's only behaviour before this parameter existed.

    Item 6c/6d (2026-08-04): suppressing_text (text_in_image/headline, the same condition
    TEXT already branches on) and suppressing_offer (offer_text falsy, the same condition
    OFFER already branches on) together gate _suppressed_container_exception(...) into
    `opening` - the ONE exception to that same paragraph's "carries over EXACTLY... in any
    way" claim, stated in the SAME place as that claim rather than as a separate TEXT/OFFER
    -only clause a reader could read as contradicting it. Both conditions feed the SAME
    exception (not two separate ones) because a run can suppress either, both, or neither
    independently - text_in_image=True with offer_text unset must still except the offer
    container, exactly the gap a text_in_image-only check left open. Neither suppressed ->
    opening is unaffected, byte-for-byte identical to before this item, the same
    additive-only pattern retheme_colours/substance_colour already established.

    Item B (2026-08-05): retheme_colours' palette remap contradicted TEXT (Item C - text
    colour is inherited from the reference) and OFFER (Item D - a substituted badge keeps
    its own reference colour exactly) whenever both were in effect at once. Folded into the
    SAME sentence that states the remap, not a competing clause after it: the remap still
    covers background/props/wardrobe/surfaces exactly as before, but now says outright that
    it never reaches text/typography or a substituted offer badge's own colour - those are
    governed by TEXT/OFFER below. retheme_colours=False is unaffected (that branch already
    reproduces every colour including text/badges, so there was never a contradiction to
    guard against there).

    Item C (2026-08-05): the TEXT branch below now INHERITS the reference's own typography
    (weight, casing, placement) and text colour, replacing ONLY the wording - a deliberate
    reversal of Prompt 4 Item 5's "map creative_format to Besque's OWN typeface, never the
    reference's own font" (TYPOGRAPHY_GUIDANCE), which was this function's only caller of
    that map. Live use showed the output ignoring the reference's text styling entirely, and
    the team's answer for this function going forward is inheritance, not substitution - so
    the `creative_format` parameter (only ever threaded here for that lookup) and
    TYPOGRAPHY_GUIDANCE/DEFAULT_TYPOGRAPHY_GUIDANCE are removed as dead code, not left
    unused. Must not contradict Item B: text colour is explicitly named as INHERITED, never
    re-themed - see B's palette-remap sentence, which now says the same thing from the
    other side."""
    suppressing_text = not (text_in_image and headline)
    suppressing_offer = not offer_text
    # Always True: the EFFICACY CLAIMS clause below bans efficacy-claim wording
    # unconditionally (no approved_claims threading to images exists), so an
    # efficacy-claim badge is always suppressed in edit mode - never toggled, unlike
    # suppressing_text/suppressing_offer above. Named explicitly rather than inlined as a
    # literal True at the call site, so this reads as a real category rather than a
    # magic constant.
    suppressing_efficacy = True
    exception_clause = _suppressed_container_exception(suppressing_text, suppressing_offer,
                                                         suppressing_efficacy)
    if retheme_colours:
        effective_palette = palette or "terracotta, maroon, gold, cream"
        opening = (
            "EDIT MODE: the FIRST attached image is the competitor's own advertisement. "
            "This is a single instruction with two parts, not two competing ones: "
            "geometry is preserved, colour is substituted. Composition, layout, camera "
            "angle, spacing, lighting direction, contrast relationships, tonal "
            "hierarchy, and text placement all carry over EXACTLY as shot in the "
            "reference - do not change the framing, angle, spacing, or structure in any "
            "way. "
            + exception_clause +
            f"At the same time, every hue in the scene (background, props, "
            f"wardrobe, surfaces) re-maps to Besque's palette: {effective_palette} - "
            f"overriding the reference's own colours entirely. This colour substitution "
            f"NEVER reaches text/typography (see TEXT below - its colour is INHERITED "
            f"from the reference, not re-themed) or a substituted offer/price badge's own "
            f"colour (see OFFER below - its colour is preserved exactly, not re-themed): "
            f"both keep the reference's own colour untouched by this palette remap. "
        )
    else:
        opening = (
            "EDIT MODE: the FIRST attached image is the competitor's own advertisement. "
            "Reproduce its composition, background, camera angle, lighting, colour "
            "palette, text placement, and overall layout as closely as possible. "
            + exception_clause
        )
    opening += _register_clause(style)

    if include_product and reference_has_product:
        base = opening + (
            "Changing ONLY the product. Remove the competitor's product entirely and "
            "place the Besque product (shown in the reference photo(s) that follow, if "
            "any) in its position, at its scale, with its lighting, matching the "
            "original shot as faithfully as possible. "
            + _substance_recolour_clause(substance_colour) +
            "Everything else in the scene stays exactly as it "
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
    eff_headline, eff_subtext = effective_authorised_text(text_in_image, headline, subtext)
    if eff_headline:
        permitted = f'the headline "{eff_headline}"'
        if eff_subtext:
            permitted += f' and the supporting text "{eff_subtext}"'
        base += (
            f"TEXT: preserve the reference image's text zones EXACTLY as they appear - "
            f"same size, position, weight, casing, and text colour - and replace ONLY "
            f"the wording with {permitted}, same layout, our words. Typography (typeface "
            f"weight, casing, placement) and text colour are INHERITED from the "
            f"reference exactly as shown, never re-themed or restyled to a different "
            f"look - the wording is the ONLY thing that changes. This is the ENTIRE text "
            f"budget for this image - no ingredient list, mechanism or benefit paragraph, "
            f"additional body copy, or CTA sentence may ALSO be rendered, even if such "
            f"text exists in the product description, generated copy, or reference ad. "
            f"The competitor's brand name, product name, and claims must NEVER survive "
            f"into the output, even inside this inherited styling. "
        )
    else:
        base += (
            f"TEXT: any container that held the suppressed text - {_container_list_phrase()} "
            f"- is removed ENTIRELY along with its wording, not just emptied: the "
            f"container shape itself does not survive. Fill the freed area with clean "
            f"background continuous with its immediate surroundings (matching colour, "
            f"texture, and lighting), in the SAME position the container occupied in the "
            f"source image - no empty outline, box, or shape left behind, and no text, "
            f"headline, or competitor wording rendered there either. That space will be "
            f"filled later as a separate HTML overlay. "
        )
    base += _typography_zones_clause(typography_zones)
    if offer_text:
        base += (
            f"OFFER: if the reference shows an offer, discount, price, or CTA badge, "
            f"preserve its position, shape, size, colour, and typography EXACTLY as "
            f"shown in the reference - and replace ONLY its wording with: {offer_text}. "
            f"Do not invent a different number, percentage, or term; do not restyle, "
            f"resize, or recolour the badge itself. "
        )
    else:
        # Item 6d (2026-08-04): enumerated explicitly after "SUMMER SALE" survived as a
        # tiled background - the old wording ("no urgency phrasing, discount, price, or
        # CTA button text") read as covering a single discrete badge/button only, not a
        # full-background pattern, and named no scarcity/stock-count/promo-code category
        # at all. Locations (badge, banner, background, watermark, product label) and the
        # per-6c container-removal principle are stated together with the category list so
        # this reads as one ban, not a badge-only one a full-background case falls outside.
        base += (
            "OFFER: no offer was supplied for this run - none of the following may "
            "survive anywhere in the image, even if the reference shows one: a "
            "percentage or amount off, a price, a promo or discount code, a scarcity or "
            "stock-count claim (e.g. 'only 100 left', 'selling fast'), limited-time or "
            "urgency wording, a free-shipping offer, or sale wallpaper - a tiled or "
            "repeated promotional pattern covering part or all of the background. This "
            "ban applies wherever any of it appears - badge, banner, background, "
            "watermark, or the product's own label - not just in a single discrete "
            f"badge. Where it sits inside a container ({_container_list_phrase()}), that "
            f"container is removed entirely, per the exception stated above - never left "
            f"behind empty. Where it takes the form of a tiled background pattern, the "
            f"whole pattern is replaced with clean background, not just the wording "
            f"within it. Reproduce no urgency wording, tiling, code, or button shape from "
            f"the reference. "
        )
    base += (
        "EFFICACY CLAIMS: describe NO quantified efficacy claim of any kind - no "
        "percentage improvement (e.g. '+25% more moisturised'), no ratio ('3x more "
        "effective', 'twice as fast'), and no timescale ('in just 7 days') - even if the "
        "reference shows one. None has been approved for this run. "
    )
    return base

# Used when production_style is absent/null/unknown — preserves the previous hardcoded look.
DEFAULT_STYLE_GUIDANCE = "Style: clean, editorial, aspirational, natural light. "

# TYPOGRAPHY_GUIDANCE (Prompt 4, Item 5: map creative_format to a Besque typeface style
# rather than copying the reference's own font) was removed 2026-08-05 (Item C) - its only
# caller, _edit_mode_instruction's TEXT branch, now inherits the reference's own typography
# and text colour instead, per the team's live-use finding that edit mode's output was
# ignoring the reference's text styling entirely.


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
                    retheme_colours=True, critic_feedback=None):
    """Single-pass image generation from the blueprint. One image, no iteration.
    Saves to assets/<stem>_draft.png (stem = ad_id, or ad_id+angle if angle_slug is given)
    and returns the path. Returns None on failure. include_product/text_in_image/headline/
    subtext are forwarded to build_image_prompt/brand_rules - defaults reproduce today's
    behaviour exactly.

    critic_feedback (2026-08-05, the corrective-retry loop): a list of short strings from
    a PRIOR call's output_critic.check_draft findings, forwarded straight to
    build_image_prompt's _critic_feedback_clause. None (the default) reproduces today's
    prompt byte-for-byte - only pipeline.process_ad's retry (attempt 2 of
    MAX_IMAGE_ATTEMPTS, and only when attempt 1 came back HIGH-confidence) ever passes
    this. Same "the caller decides, this just forwards" pattern as offer_text/realism.

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
                                 retheme_colours=retheme_colours, brand_palette=brand_palette,
                                 realism=realism, critic_feedback=critic_feedback)
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


def _current_draft_bytes(ad_id, angle_slug=None):
    """Read the CURRENT draft image bytes for (ad_id, angle_slug) - local disk first
    (ASSET_DIR), then the GCS bucket - or None if there is no current draft at all
    (e.g. a first-ever generation). Mirrors the lookup dashboard.py's api_edit_image
    already does inline for the same purpose; extracted here so
    version_current_draft below has exactly one source for "what's there right now"."""
    stem = _draft_stem(ad_id, angle_slug)
    local = ASSET_DIR / f"{stem}_draft.png"
    if local.exists():
        return local.read_bytes()
    try:
        from google.cloud import storage
        blob = storage.Client().bucket(assets.asset_bucket_name()).blob(f"{stem}_draft.png")
        if blob.exists():
            return blob.download_as_bytes()
    except Exception:
        pass
    return None


def _version_prompt_name(version_name):
    """{stem}_draft_v{n}.png -> {stem}_draft_v{n}.prompt.txt - the sidecar that records
    the EXACT prompt that produced that version's PNG, so a later restore can pair the
    two correctly instead of guessing."""
    return version_name[:-len(".png")] + ".prompt.txt"


def _write_version_prompt(version_name, prompt):
    """Best-effort sidecar write, mirroring the PNG's own local+bucket write pattern.
    Never raises - a missing sidecar just means this version can't be restored later
    (read_version_prompt returns None), not that versioning the image itself failed."""
    if not prompt:
        return
    prompt_name = _version_prompt_name(version_name)
    try:
        ASSET_DIR.mkdir(exist_ok=True)
        with open(ASSET_DIR / prompt_name, "w", encoding="utf-8") as f:
            f.write(prompt)
    except Exception as e:
        print(f"[_write_version_prompt] {prompt_name} local write failed (non-fatal): {e}")
    try:
        from google.cloud import storage
        storage.Client().bucket(assets.asset_bucket_name()).blob(prompt_name).upload_from_string(
            prompt, content_type="text/plain")
    except Exception as e:
        print(f"[_write_version_prompt] {prompt_name} bucket upload failed (non-fatal): {e}")


def read_version_prompt(ad_id, version_n, angle_slug=None):
    """The EXACT prompt that produced version_n's PNG (local disk first, then bucket),
    or None if no sidecar was ever written for it - e.g. every version that existed
    before this feature. Callers must treat None as "cannot be restored", never
    substitute the current prompt or any other guess."""
    stem = _draft_stem(ad_id, angle_slug)
    prompt_name = f"{stem}_draft_v{version_n}.prompt.txt"
    local = ASSET_DIR / prompt_name
    if local.exists():
        return local.read_text(encoding="utf-8")
    try:
        from google.cloud import storage
        blob = storage.Client().bucket(assets.asset_bucket_name()).blob(prompt_name)
        if blob.exists():
            return blob.download_as_bytes().decode("utf-8")
    except Exception:
        pass
    return None


def read_version_bytes(ad_id, version_n, angle_slug=None):
    """version_n's PNG bytes (local disk first, then bucket), or None if that version
    doesn't exist there. Mirrors _current_draft_bytes's own lookup, for an arbitrary
    past version instead of the current draft."""
    stem = _draft_stem(ad_id, angle_slug)
    version_name = f"{stem}_draft_v{version_n}.png"
    local = ASSET_DIR / version_name
    if local.exists():
        return local.read_bytes()
    try:
        from google.cloud import storage
        blob = storage.Client().bucket(assets.asset_bucket_name()).blob(version_name)
        if blob.exists():
            return blob.download_as_bytes()
    except Exception:
        pass
    return None


def list_draft_versions(ad_id, angle_slug=None):
    """Every {stem}_draft_v{n}.png that exists on local disk, ascending by n, each with
    whether it has a recoverable prompt sidecar. Does not include the current draft -
    callers that need "current" too (dashboard.py) add it themselves, since its prompt
    lives in the artifacts DB row, not a sidecar file this module has no DB access to
    read. Local-disk only (unlike _current_draft_bytes/read_version_prompt, which also
    check the bucket) - listing is for the UI's own browser session, which only ever
    runs against whichever storage backend this process is actually using; Cloud Run's
    ephemeral local disk means this naturally returns nothing post-restart there, same
    as any other local-disk-only read in this module already does for listing purposes."""
    stem = _draft_stem(ad_id, angle_slug)
    prefix = f"{stem}_draft_v"
    versions = []
    if ASSET_DIR.exists():
        for p in ASSET_DIR.iterdir():
            if p.name.startswith(prefix) and p.suffix == ".png":
                tail = p.stem[len(prefix):]
                if tail.isdigit():
                    n = int(tail)
                    versions.append({
                        "version": n,
                        "has_prompt": (ASSET_DIR / f"{prefix}{n}.prompt.txt").exists(),
                    })
    versions.sort(key=lambda v: v["version"])
    return versions


def overwrite_current_draft(ad_id, image_bytes, angle_slug=None):
    """Write image_bytes as the new {stem}_draft.png, local + bucket - used by restore
    to make a prior version the current draft again. Caller must version the outgoing
    draft (version_current_draft) BEFORE calling this, same ordering contract as every
    other overwrite-the-current-draft path in this module."""
    stem = _draft_stem(ad_id, angle_slug)
    ASSET_DIR.mkdir(exist_ok=True)
    dest = ASSET_DIR / f"{stem}_draft.png"
    with open(dest, "wb") as f:
        f.write(image_bytes)
    try:
        from google.cloud import storage
        storage.Client().bucket(assets.asset_bucket_name()).blob(f"{stem}_draft.png").upload_from_string(
            image_bytes, content_type="image/png")
    except Exception as e:
        print(f"[overwrite_current_draft] ad_id={ad_id} bucket upload failed (non-fatal): {e}")
    return str(dest)


def version_current_draft(ad_id, angle_slug=None, current_prompt=None):
    """Preserve the CURRENT draft (if any) as {stem}_draft_v{n}.png - both locally and
    in the bucket - before something is about to overwrite {stem}_draft.png with new
    content. This is edit_image's own versioning scheme (same _next_draft_version
    numbering, same {stem}_draft_v{n}.png naming), extracted so a deliberate
    regenerate-from-scratch (pipeline.process_ad, Chunk 5 Item 7c) can reuse it
    instead of inventing a second one. Returns the version filename, or None if
    there was no current draft to preserve (nothing to version on a first-ever
    generation).

    current_prompt, if given, is the CURRENT artifact's own image_prompt (the caller's
    to fetch from the DB - this module has no artifact access) - written alongside the
    PNG as a sidecar (see _write_version_prompt) so this exact version can be restored
    later. None (the default, matching every caller before version navigation existed)
    versions the PNG only, exactly as before."""
    current = _current_draft_bytes(ad_id, angle_slug)
    if current is None:
        return None
    stem = _draft_stem(ad_id, angle_slug)
    ASSET_DIR.mkdir(exist_ok=True)
    version_name = f"{stem}_draft_v{_next_draft_version(ad_id, angle_slug)}.png"
    with open(ASSET_DIR / version_name, "wb") as f:
        f.write(current)
    try:
        from google.cloud import storage
        storage.Client().bucket(assets.asset_bucket_name()).blob(version_name).upload_from_string(
            current, content_type="image/png")
    except Exception as e:
        print(f"[version_current_draft] ad_id={ad_id} bucket upload failed (non-fatal): {e}")
    _write_version_prompt(version_name, current_prompt)
    return version_name


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


def _edit_preserve_clause(instruction):
    return (
        "This is a TARGETED EDIT to the attached image, not a new composition. Preserve "
        "EVERY element of the attached image EXACTLY as it appears - layout, typography, "
        "wording, colours, product, bottle, background, lighting, existing text - except "
        "for what the instruction below explicitly names. Change ONLY what the instruction "
        f"names; nothing else may move, resize, recolour, reword, or be added or removed. "
        f"Instruction: {instruction}"
    )


def edit_image(current_image_bytes, instruction, ad_id, aspect="1:1", angle_slug=None,
                text_in_image=False, headline=None, subtext=None, current_prompt=None):
    """Edit an existing draft image via nano banana; versions the outgoing draft and
    returns the new path, or None on failure. text_in_image/headline/subtext are accepted
    for call-site compatibility only - no longer used in the prompt. current_prompt, if
    given, is the CURRENT artifact's own image_prompt - written as a sidecar alongside
    the versioned-out PNG (see version_current_draft) so this version can be restored
    later; None skips the sidecar, same as every caller before version navigation existed."""
    from google.genai import types as genai_types
    stem = _draft_stem(ad_id, angle_slug)
    prompt = (
        COMPLIANCE_RULES +
        _bottle_fixed_clause() +
        _edit_preserve_clause(instruction) +
        f" Output aspect ratio: {aspect}."
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
        _write_version_prompt(version_name, current_prompt)
        edit_image.last_prompt = prompt
        return str(dest)
    except Exception:
        import traceback
        traceback.print_exc()
        return None


def _regenerate_delta_clause(instruction):
    return (
        " REGENERATE: the prompt above is the EXACT prompt that produced the attached "
        "image - apply ONLY the following instruction as a targeted change to it; every "
        "other element it describes (composition, text, colours, product) still applies "
        f"exactly as already stated. Instruction: {instruction}"
    )


def regenerate_from_stored_prompt(current_image_bytes, stored_prompt, instruction, ad_id, angle_slug=None):
    """Regenerate a draft by applying `instruction` as a delta to `stored_prompt` (the
    exact prompt that produced current_image_bytes), never a fresh rebuild from current
    form state. Aspect ratio is derived from current_image_bytes itself, never a
    parameter. Caller must version the outgoing draft before calling this - it only
    overwrites. Returns the new draft path, or None on failure."""
    from google.genai import types as genai_types
    stem = _draft_stem(ad_id, angle_slug)
    prompt = stored_prompt.strip() + _regenerate_delta_clause(instruction)
    try:
        client = genai.Client(vertexai=True, project="besque-martech", location="global")
        aspect_ratio = derive_aspect_ratio(current_image_bytes)
        generation_config = None
        if aspect_ratio is not None:
            generation_config = genai_types.GenerateContentConfig(
                image_config=genai_types.ImageConfig(aspect_ratio=aspect_ratio)
            )
        call_kwargs = {
            "model": "gemini-3.1-flash-image",
            "contents": [
                genai_types.Part.from_bytes(data=current_image_bytes, mime_type="image/png"),
                prompt,
            ],
        }
        if generation_config is not None:
            call_kwargs["config"] = generation_config
        response = client.models.generate_content(**call_kwargs)
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
            storage.Client().bucket(assets.asset_bucket_name()).blob(f"{stem}_draft.png").upload_from_string(
                image_bytes, content_type="image/png")
        except Exception as e:
            print(f"Bucket upload failed (non-fatal): {e}")
        regenerate_from_stored_prompt.last_prompt = prompt
        return str(dest)
    except Exception:
        import traceback
        traceback.print_exc()
        return None
