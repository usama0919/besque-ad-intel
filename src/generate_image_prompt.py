"""Regeneration step (image prompt): turn a blueprint's visual into an image-gen prompt."""
import io
import logging
import math
import os
import re
from PIL import Image, ImageDraw
from src import assets, generate_image_prompt_writer, compliance
from src.compliance_rules import COMPLIANCE_RULES

IMAGE_MODEL = os.getenv("IMAGE_MODEL", "placeholder-image-model")

log = logging.getLogger("generate_image_prompt")


# SUBSTITUTION AS ONE RULE, props row (2026-08-07): a prop belonging to the COMPETITOR's
# own product category (an applicator diagram, an anatomical inset, a device illustration)
# has no Besque equivalent and must be removed WITH the competitor's product, never
# preserved as background composition. Proven live: a real draft kept a leftover eye-
# diagram prop (from a competitor eye-gel reference) standing next to the substituted
# Besque bottle - nothing keyed off product_category.signals/visual.subject at all, so the
# prop had no removal hook, unlike the product itself which always does.
PROP_KEYWORDS = ("diagram", "illustration", "device", "applicator", "inset",
                 "anatomical", "prop stand", "wand", "roller", "dropper tool")


def _competitor_props_clause(blueprint):
    """Scans product_category.signals and visual.subject (both already extracted by
    deconstruct.py - no new model call) for language naming a prop tied to the
    COMPETITOR's own product category, quoted back verbatim in the removal instruction so
    the model sees exactly what was flagged, never a paraphrase. Returns "" when nothing
    matches (the ordinary case for most references) - never a guessed prop.

    Edit-mode only: this reads signals about what the ATTACHED reference image literally
    shows, and only edit mode attaches that image to Gemini at all - generate mode's
    writer/template text has no equivalent literal-pixel-copy risk to guard against here."""
    blueprint = blueprint or {}
    candidates = list((blueprint.get("product_category") or {}).get("signals") or [])
    subject = (blueprint.get("visual") or {}).get("subject")
    if subject:
        candidates.append(subject)
    matches = [c for c in candidates if any(kw in c.lower() for kw in PROP_KEYWORDS)]
    if not matches:
        return ""
    quoted = "; ".join(f'"{m}"' for m in matches)
    return (
        f"PROPS (STRICT): the reference's own scene includes a prop belonging to the "
        f"COMPETITOR's product category, not Besque's - {quoted}. Remove it WITH the "
        f"competitor's product; it is not part of the composition to preserve, and must "
        f"never be redrawn, kept as a background element, or left standing next to the "
        f"substituted Besque product. "
    )


def resolve_product_count(reference_count, operator_count, default_count=1):
    """How many Besque products belong in the scene (Task F, point 6, 2026-08-07).
    Precedence: an explicit operator override for THIS run wins outright when given;
    otherwise what the REFERENCE literally shows (blueprint.layout_detail.product_count)
    is the natural baseline; a logged default (1) only when neither is available.
    Returns (resolved_count, source) - source is "operator"/"reference"/"default". A
    resolved count above 1 means render that many of the SAME Besque product (reproducing
    the reference's own composition) - never a different product invented per count, and
    never collapsed back down to one just because it's more than the ordinary case. The
    caller logs a line whenever resolved_count > 1, purely informational (what was
    derived and from where), never a warning about a limitation - there isn't one."""
    if operator_count is not None:
        return operator_count, "operator"
    if reference_count is not None and reference_count > 0:
        return int(reference_count), "reference"
    return default_count, "default"


def reference_has_product(blueprint):
    """True when the reference blueprint shows a product in frame at all -
    layout_detail.product_count != 0 and product_category.category != "not_product",
    both already extracted by deconstruct.py, never a new signal invented for this. False
    means there is nothing in the reference to SUBSTITUTE - see
    resolve_effective_include_product/_edit_mode_instruction, which now ADD the product
    into the scene in that case rather than skipping the ad (2026-08-07, reference
    usability gate reversal - a reference with no product is still a usable scene).

    Public (no leading underscore): pipeline.py's own reference-usability detection reads
    this exact derivation for its pool-badge/logging use, rather than re-implementing it
    and risking drift - one source of truth, same reasoning as effective_authorised_text
    above."""
    blueprint = blueprint or {}
    layout_detail_bp = blueprint.get("layout_detail") or {}
    product_category_bp = (blueprint.get("product_category") or {}).get("category")
    return not (product_category_bp == "not_product" or layout_detail_bp.get("product_count") == 0)


def reference_has_text_zone(blueprint):
    """True when the reference blueprint shows an EXISTING headline or another
    text-bearing structural zone (sub_line/body_copy/cta) that authorised in-image copy
    could substitute into. False means any authorised text has nothing to substitute
    into and must be ADDED into clean negative space instead (see
    _edit_mode_instruction's TEXT branch) - independent of reference_has_product; a
    reference can have a product but no text, text but no product, both, or neither.

    Public for the same reason as reference_has_product above - pipeline.py's pool-badge
    detection reads this exact derivation, never a re-implementation of it."""
    blueprint = blueprint or {}
    if (blueprint.get("headline_verbatim") or "").strip():
        return True
    text_zone_types = {"sub_line", "body_copy", "cta"}
    return any(
        (z or {}).get("zone_type") in text_zone_types
        for z in (blueprint.get("structural_zones") or [])
    )


def _structural_zones_have_offer(structural_zones):
    """Core of reference_has_offer_zone below, operating on an already-extracted
    structural_zones list rather than a full blueprint - lets _edit_mode_instruction
    (2026-08-11) reuse the identical check on the structural_zones it already receives as
    its own parameter, without needing the whole blueprint threaded to it separately."""
    for z in (structural_zones or []):
        zt = (z or {}).get("zone_type")
        if zt == "price_anchor":
            return True
        if zt == "badge" and _is_offer_shaped_zone(z.get("detail")):
            return True
    return False


def reference_has_offer_zone(blueprint):
    """True when the reference blueprint shows a structural zone that actually CARRIES an
    offer - a price_anchor (a price shown as its own graphic element is inherently
    offer-shaped, no keyword check needed) or a badge whose own detail reads as
    offer/discount-shaped (see _is_offer_shaped_zone/OFFER_BADGE_KEYWORDS below - same
    keyword set _structural_zones_clause already uses to decide whether a badge
    substitutes with offer_text or gets removed, so this can never disagree with what
    actually renders).

    Added 2026-08-11 for clone mode (pipeline.py) - deciding whether THIS ad's per-ad
    config should carry the operator's offer_text at all, before build_image_prompt ever
    runs, rather than only gating it once inside _structural_zones_clause's own per-zone
    branch. Same public/no-leading-underscore convention as reference_has_product/
    reference_has_text_zone above - pipeline.py reads this directly, no reimplementation.

    Thin wrapper over _structural_zones_have_offer (added same day) - kept as the public
    blueprint-taking entry point so pipeline.py's existing call sites are unaffected."""
    return _structural_zones_have_offer((blueprint or {}).get("structural_zones") or [])


def resolve_effective_include_product(blueprint, include_product, edit_mode):
    """The single source for whether a Besque product is actually being placed in the
    scene - Item 2 (2026-08-05), extracted from build_image_prompt's own inline
    computation so pipeline.process_ad can learn the same answer instead of independently
    re-deriving it (exactly the two-independent-derivations shape that let rule 6 and the
    output critic drift apart on text_in_image - see effective_authorised_text above).

    REVERSED 2026-08-07 (reference usability gate reversal): effective_include_product is
    now ALWAYS exactly the operator's include_product toggle - a reference with no product
    in frame no longer forces this False. This must generalise across every reference
    shape, not special-case any one ad: a productless reference is still a usable scene,
    it just means the product gets ADDED rather than substituted (see
    _edit_mode_instruction). reference_has_product (reference_has_product() above) is
    still computed and returned - callers use it ONLY to choose ADD vs SUBSTITUTE wording,
    never to override the operator's explicit choice again.

    Returns (effective_include_product, reference_has_product) - the caller needs
    reference_has_product too, to choose which explanation/wording applies, the same
    distinction _edit_mode_instruction's docstring establishes.

    Deliberately a plain function, not a build_image_prompt return value (~55 existing
    call sites treat that function's return as a bare string) and not a module-level
    attribute like generate_image.last_prompt (the exact kind of hidden coupling that
    made the text_in_image bug possible - state one function sets and another reads, with
    nothing in either signature saying so)."""
    ref_has_product = reference_has_product(blueprint) if edit_mode else True
    return include_product, ref_has_product


def build_image_prompt(blueprint: dict, product: dict = None, include_product: bool = True,
                        text_in_image: bool = False, headline: str = None, subtext: str = None,
                        creative_description: str = None, edit_mode: bool = False,
                        offer_text: str = None, operator_instruction: str = None,
                        retheme_colours: bool = True, brand_palette: str = None,
                        realism: str = None, critic_feedback: list = None,
                        cta_text: str = None, panel_copy: list = None,
                        testimonial: dict = None, product_count: int = None,
                        clone_mode: bool = False) -> str:
    """Construct a Besque-adapted image generation prompt from the blueprint's visual notes.
    include_product=True, text_in_image=False, creative_description=None, edit_mode=False,
    offer_text=None, operator_instruction=None (today's defaults) reproduce the prior
    output exactly for a given blueprint/product - none of these are a rewrite of the
    default path.

    clone_mode (2026-08-11): forwarded only to _edit_mode_instruction's own OFFER clause
    (edit_mode branch) - see that function's docstring. False (the default) reproduces
    today's exact prompt; offer_text's own truthiness is the only gate either way.

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
    branch. Aspect ratio is NOT stated in edit-mode prompt text - it's derived from the
    reference image itself and set explicitly on the generation config instead (see
    generate_image/derive_aspect_ratio, reinstated 2026-08-07 after omitting it proved
    nondeterministic, not just imprecise); generate mode keeps its explicit "Square 1:1"
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
    _substance_recolour_clause for what happens when substance_colour is unset.

    product_count (Task F, point 6, 2026-08-07; corrected same day): resolved via
    resolve_product_count against blueprint.layout_detail.product_count (the reference's
    own observed count, never hardcoded) - see resolve_product_count for precedence. A
    resolved count above 1 renders that many of the SAME Besque product, reproducing the
    reference's own composition - this is NOT "faking a pair": the only thing actually
    banned is inventing a DIFFERENT product/SKU/variant, never matching how many of the
    one real product appear. An earlier version of this clause instead forced exactly ONE
    bottle whenever count was above 1 (reasoning that a genuinely distinct second SKU
    can't be sourced from a single visual_description) - that reasoning conflated "a
    different SKU" with "more than one of the same SKU," and directly caused two real
    two-product references to both render with only one bottle."""
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
    scene_lighting = visual.get("scene_lighting") or {}
    prod_style = (blueprint.get("production_style") or {}).get("style", "")

    layout_detail_bp = blueprint.get("layout_detail") or {}
    effective_include_product, reference_has_product = resolve_effective_include_product(
        blueprint, include_product, edit_mode
    )
    # reference_has_text_zone (2026-08-07, reference usability gate reversal): the
    # text-side analogue of reference_has_product, only meaningful in edit_mode (outside
    # it there's no reference zone to substitute into or add alongside at all - generate
    # mode always "adds" from scratch, see build_image_prompt's own docstring).
    ref_has_text_zone = reference_has_text_zone(blueprint) if edit_mode else True
    reference_product_count = layout_detail_bp.get("product_count")
    resolved_product_count, product_count_source = resolve_product_count(
        reference_product_count, product_count
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
        product_desc += _BOTTLE_MATERIAL_REALISM_CLAUSE
        if resolved_product_count and resolved_product_count > 1:
            # REVERSED 2026-08-12 (live failure, ad with product_count=5): rule 7 above
            # already states "exactly one bottle... NEVER add a second bottle... whether
            # copied from the competitor ad or invented" - unconditionally, every branch.
            # The clause below used to instruct "place {N} of the Besque product", which
            # directly contradicted rule 7 in the SAME prompt - two authorities, same
            # subject, opposite answers. The critic caught it as a HIGH violation and the
            # actual output rendered 8 bottles. This is not a wording tweak, it is a
            # structural fix: this branch can no longer construct a sentence asking for
            # more than one Besque product - there is no code path left that does. What
            # DOES still vary by count is the COMPOSITION instruction: the reference's
            # multi-product layout (spacing, framing, negative space sized for several
            # items) needs to be adapted around a single bottle, not reproduced with empty
            # slots or a shrunken bottle standing in for a missing set. Kept as its own
            # branch (rather than collapsing into the ==1 case below) only because that
            # composition-adaptation instruction has nothing to say when the reference
            # never had multiple products to adapt away from.
            log.info(
                "product_count resolved to %s (source=%s) - reference shows multiple "
                "products, but exactly ONE Besque product renders per rule 7; composition "
                "adapts around the single bottle instead of reproducing the count",
                resolved_product_count, product_count_source,
            )
            product_clause = (
                f"The reference shows {resolved_product_count} products together, but "
                f"Besque's product renders as exactly ONE bottle - rule 7 above permits "
                f"exactly one, with no exception for what the reference happens to show. "
                f"Adapt the COMPOSITION around that single bottle: keep the reference's "
                f"setting, lighting, and framing, but resize, recentre, or rebalance the "
                f"layout so the one bottle occupies the space naturally - never leave empty "
                f"slots where the reference's other products were, and never shrink the "
                f"single bottle to read like part of a missing set. Do not render the "
                f"competitor's product. "
                + product_desc
            )
        else:
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
            _scene_elements_clause(blueprint.get("scene_elements")) +
            _semantic_split_clause(blueprint.get("semantic_split")) +
            # include_product here is the RAW operator toggle - identical to
            # effective_include_product since the 2026-08-07 reference usability gate
            # reversal (reference_has_product no longer forces effective_include_product
            # off; see resolve_effective_include_product). reference_has_product/
            # reference_has_text_zone independently select SUBSTITUTE-vs-ADD wording per
            # element, never the boolean outcome itself - product_clause below is built
            # from the same effective_include_product, so this can never contradict it.
            _edit_mode_instruction(text_in_image=text_in_image, headline=headline, subtext=subtext,
                                   offer_text=offer_text, include_product=include_product,
                                   reference_has_product=reference_has_product,
                                   reference_has_text_zone=ref_has_text_zone,
                                   layout_detail=layout_detail_bp, visual=visual,
                                   retheme_colours=retheme_colours, palette=brand_palette,
                                   substance_colour=(product or {}).get("substance_colour"),
                                   style=(realism or "").strip() or prod_style,
                                   scene_lighting=scene_lighting,
                                   typography_zones=blueprint.get("typography_zones"),
                                   structural_zones=blueprint.get("structural_zones"),
                                   cta_text=cta_text, product_name=(product or {}).get("name"),
                                   panel_copy=panel_copy, testimonial=testimonial,
                                   certifications=(product or {}).get("certifications"),
                                   competitor_props_clause=_competitor_props_clause(blueprint),
                                   face_present=blueprint.get("face_present"),
                                   testimonial_zones=blueprint.get("testimonial_zones"),
                                   clone_mode=clone_mode) +
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
            _scene_elements_clause(blueprint.get("scene_elements")) +
            _semantic_split_clause(blueprint.get("semantic_split")) +
            creative_description.strip() + " "
            + product_clause
            + _bottle_fixed_clause() + _bottle_register_clause(scene_lighting) +
            f"Square 1:1 aspect ratio composition. " +
            closing
        )
    else:
        prompt = (
            brand_rules(include_product=include_product, text_in_image=text_in_image,
                        headline=headline, subtext=subtext) +
            _operator_instruction_clause(operator_instruction) +
            _critic_feedback_clause(critic_feedback) +
            _scene_elements_clause(blueprint.get("scene_elements")) +
            _semantic_split_clause(blueprint.get("semantic_split")) +
            f"A premium skincare advertisement image for Besque, a natural body-oil brand for women 40+. "
            f"Composition and setting: {layout}. (If this implies a person, render them per compliance "
            f"rule C1 - a generic, non-identifiable model, never the specific individual described - "
            f"and per rule 10 above, reading 45-60 years old with visibly age-appropriate skin.) "
            + product_clause +
            f"Palette and mood: {palette}. Text placement: {text_placement}. "
            f"Square 1:1 aspect ratio composition. "
            + generate_image_prompt_writer.STYLE_GUIDANCE.get(
                (realism or "").strip() or prod_style, DEFAULT_STYLE_GUIDANCE)
            + _bottle_fixed_clause() + _bottle_register_clause(scene_lighting) +
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


_RULE_10_SUBJECT_AGE = (
    "10) SUBJECT AGE (STRICT, BRAND-LEVEL, EVERY GENERATION PATH, OVERRIDES ANY OTHER "
    "AGE/APPEARANCE INSTRUCTION ANYWHERE IN THIS PROMPT): Besque's audience is women 40+. "
    "Any human subject appearing anywhere in the output image must read as 45-60 years "
    "old - visibly midlife, never youthful. Concretely: visible fine lines around the eyes "
    "and mouth, some natural skin laxity at the jawline/neck, real tone and texture "
    "variation across the skin, hair that may show natural greying or a mature styling - "
    "never smooth, poreless, airbrushed, or a visibly younger face with 'mature' framing "
    "bolted on. Competitor reference ads typically show a model in their 20s-30s: "
    "rendering that age, or anything read as under 45, is the exact failure this rule "
    "exists to catch, confirmed live (ad 1986367985280315 shipped a ~30-year-old subject "
    "unflagged). This rule wins over ANY other instruction in this prompt that describes "
    "matching, reproducing, or preserving the reference subject's age or appearance - "
    "including a REPRODUCE/SUBSTITUTE partition elsewhere that covers pose, framing, or "
    "skin-condition presentation: age is never one of the reproduced/matched attributes, "
    "on any path, with no exception. "
)

_RULE_11_SKIN_TEXTURE_REALISM = (
    "11) SKIN TEXTURE REALISM (STRICT, BRAND-LEVEL, EVERY GENERATION PATH): wherever "
    "loose, crepey, or aged skin is depicted, it must read as real, photographed human "
    "skin - irregular wrinkle patterns (never a repeating or symmetrical texture), "
    "uneven tone and pigmentation, and a real, uneven light response across the skin's "
    "surface. Uniform texture, poreless smoothness, or a visibly AI-smoothed/plastic look "
    "is a failure, independently of whether the subject's apparent AGE (rule 10 above) is "
    "otherwise correct - age-appropriate NUMBER and age-appropriate SKIN TEXTURE are two "
    "separate requirements; satisfying one does not excuse failing the other. Applies to "
    "BOTH halves of a before/after composition (see the BEFORE/AFTER SEMANTICS "
    "instruction below, where one is used), not only the side depicting the concern - the "
    "after side's improvement must still read as real skin, never a smoothed/idealised "
    "render standing in for 'better.' "
)


def brand_rules(include_product=True, text_in_image=False, headline=None, subtext=None, edit_mode=False):
    """The mechanically-enforced brand + compliance rules prepended to every image prompt.
    Called with all defaults (include_product=True, text_in_image=False, edit_mode=False),
    this reproduces the old flat BRAND_RULES constant character for character through rule
    7 - see test_brand_rules_default_reproduces_prior_rules_verbatim. Rule 8, rule 9, rule
    10, and the include_product/text_in_image/edit_mode conditionality are additive, not a
    rewrite of the existing default path.

    Rule 10 (SUBJECT AGE, 2026-08-11) and rule 11 (SKIN TEXTURE REALISM, 2026-08-12) are
    both unconditional - unlike rule 9, which is edit-mode-only, these must fire on every
    path (flat template, writer/creative_description, and edit mode) since the problems
    they guard against are not specific to edit mode; they happen wherever a person/skin
    appears in the output. Positioned after rule 9 rather than renumbering it, so edit
    mode reads 9, 10, 11 and every other path reads 8, 10, 11 (a numbering gap, not a
    numbering error) rather than disturbing rule 9's own existing number."""
    return (
        _RULES_1_TO_5
        + _rule6_text_policy(text_in_image, headline, subtext)
        + _rule7_product_policy(include_product)
        + _RULE_8_LAYOUT_IS_COMPOSITION
        + (_RULE_9_SOURCE_IMAGE_IS_THE_COMPETITORS_AD if edit_mode else "")
        + _RULE_10_SUBJECT_AGE
        + _RULE_11_SKIN_TEXTURE_REALISM
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


def _scene_elements_clause(scene_elements=None):
    """Item 9 consumption (2026-08-11): deconstruct's scene_elements inventory
    (schema/blueprint.schema.json), injected as a positive MUST-INCLUDE list - only
    entries flagged essential=true, each named individually with its own role, never a
    blanket "keep everything" instruction that would just restate the reproduce-
    faithfully language already above it in edit mode, and would have nothing to attach
    to at all in generate mode. Phrased as inclusion, not prohibition, per instruction:
    this tells the model what must be IN the output, not what to avoid.

    Fixed position, same reasoning as _operator_instruction_clause/_critic_feedback_
    clause: called identically in all three build_image_prompt branches (edit mode,
    writer/creative_description, flat template), right after critic feedback and before
    whatever supplies the scene text - so a reference's essential elements are never lost
    to whichever branch happens to fire. Returns "" when scene_elements is empty or has
    nothing flagged essential, so a blueprint from before this field existed produces
    byte-for-byte the same prompt as before this existed."""
    essential = [e for e in (scene_elements or []) if e.get("essential")]
    if not essential:
        return ""
    bullets = " ".join(
        f"({i}) {e.get('element', '')} - {e.get('role', '')}"
        for i, e in enumerate(essential, start=1)
    )
    return (
        f"SCENE ELEMENTS TO INCLUDE (STRICT): the reference's own composition depends on "
        f"the following elements - each one MUST appear in the output, named individually "
        f"with the role it plays here, not merely implied by the setting or left to chance: "
        f"{bullets} "
    )


def _semantic_split_clause(semantic_split=None):
    """Item 6 consumption (2026-08-12): deconstruct's semantic_split field
    (schema/blueprint.schema.json). When a reference is a before/after, side-by-side, or
    split-screen composition, the two halves of the OUTPUT must hold the same subject,
    pose, camera angle, and lighting - only the skin condition differs between them. One
    side shows the concern clearly and realistically; the other shows VISIBLE IMPROVEMENT
    in that SAME concern - rendering the same condition on both sides is a failure, since
    the contrast between the two is the entire point of the format.

    INTERNAL to the image (half vs half) - explicitly NOT the same axis as the
    background-variation allowance elsewhere in this prompt (item 4/edit mode's opening
    instruction), which governs how much the OUTPUT may vary from the REFERENCE
    photograph. Stated explicitly, by name, so the two can never be read as competing:
    one governs output-vs-reference, this one governs half-vs-half within the output.

    Fixed position, same reasoning as _scene_elements_clause: called identically in all
    three build_image_prompt branches. Returns "" when is_split is false/absent, so a
    blueprint with no split, or one from before this field existed, produces byte-for-byte
    the same prompt as before this existed."""
    semantic_split = semantic_split or {}
    if not semantic_split.get("is_split"):
        return ""
    axis = semantic_split.get("split_axis") or "vertical"
    before = (semantic_split.get("left_or_before") or "").strip() or "the reference's own before/concern side, as shown"
    after = (semantic_split.get("right_or_after") or "").strip() or "a visibly improved version of that same concern"
    return (
        f"BEFORE/AFTER SEMANTICS (STRICT, INTERNAL TO THE IMAGE - this is a DIFFERENT "
        f"axis from the background-variation allowance elsewhere in this prompt, which "
        f"governs how much the output may vary from the REFERENCE photograph, never how "
        f"the two halves below relate to EACH OTHER): this reference is a {axis} split "
        f"composition. Both halves MUST show the SAME subject, the SAME pose, the SAME "
        f"camera angle, and the SAME lighting - the only thing that may differ between "
        f"them is the skin condition itself. One side shows the concern clearly and "
        f"realistically: {before}. The other side shows VISIBLE IMPROVEMENT in that SAME "
        f"concern, on the SAME subject: {after}. Rendering the same skin condition on "
        f"both sides is a failure - the contrast between them is the entire point of "
        f"this format. "
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


# BOTTLE MATERIAL REALISM (2026-08-12 15:13 sweep): distinct from _substance_recolour_
# clause above (which governs a substance that has LEFT the bottle - a drip, pour, or
# smear elsewhere in the scene) - this is the oil AS SEEN THROUGH THE GLASS, still
# inside the bottle. Live finding: the oil reads flat and uniform, with no liquid
# behaviour at all. Deliberately generic/behavioural rather than naming a specific
# colour or material here (unlike _substance_recolour_clause, which names
# substance_colour when known) - a specific colour/material claim not present in
# product.visual_description would be an invented product fact (compliance C3); real
# glass/pump colour and finish are already carried by visual_description and the
# product's own reference photos, this only states HOW whatever those are should
# render as a real, physical object rather than a flat graphic. Works WITH
# output_critic's existing PRODUCT REGISTER MISMATCH finding (a studio-lit bottle
# composited into an ambient scene) rather than against it - refraction/reflection are
# an extension of that same "match the scene's own light, never a separate studio
# light" principle (see _bottle_register_clause), applied to a transparent object
# specifically, not a second, competing lighting instruction.
_BOTTLE_MATERIAL_REALISM_CLAUSE = (
    "The oil inside the bottle must read as a REAL LIQUID, not a flat, uniform fill: "
    "render a visible liquid volume with a distinct surface line (a meniscus) where "
    "the oil meets the air above it - never full to the cap, and never a solid block "
    "of colour with no surface at all. Its translucency and viscosity should read as "
    "real liquid - light passes through it, and it visibly settles/pools under gravity "
    "- never opaque, matte, or plastic-looking. The glass itself refracts and reflects "
    "THIS SCENE's own lighting (see the bottle's lighting instruction elsewhere in "
    "this prompt) - never the separate, unrelated studio lighting the product's own "
    "reference photo(s) happen to have been shot under; a glass bottle with no depth, "
    "refraction, or reflection reads as a flat sticker, which is a failure. Pump or cap "
    "hardware renders with correct material properties for what it actually is (metal, "
    "plastic, or a mix, per the product's own visual appearance) - real specular "
    "highlights and reflections consistent with this scene's lighting, never a flat, "
    "single-tone shape. The label wraps the bottle's own curve as a real printed label "
    "would - legible, without warping, stretching, or reading as a flat decal pasted "
    "over a curved surface. "
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


# Every zone_type in deconstruct.py's schema now has a real substitute-or-remove branch
# below - there is no longer a catch-all "no Besque value exists for this type" set.
# disclaimer is the one deliberate exception: it always removes, in its own branch below,
# because its removal carries an extra rule the others don't (any pointing asterisk/
# footnote marker must go with it - 2026-08-06, Grüns GLP-1 leak).

# SUBSTITUTION AS ONE RULE, badge/pill/price/callout rows (2026-08-07, generalised same
# day after two real two-bottle references both lost their offer pill and their award tag
# entirely): whatever tag, badge, pill, price block, or callout the REFERENCE happens to
# contain is reproduced IN PLACE - same position, shape, size, typography, read straight
# off that zone's own `position`/`container`/`detail` fields, never a hardcoded position or
# layout - filled with a genuine Besque counterpart, or removed and healed if none exists.
# Never a silent vanish, never a floating element appearing somewhere the reference didn't
# have one (see the OFFER prose clause below, which this doesn't replace - it's a fallback
# for a reference where structural_zones didn't capture the container explicitly).
#
# A badge's own `detail` decides which counterpart, if any, applies - checked in this
# order, first match wins:
#   1. AWARD_BADGE_KEYWORDS (award/editorial/third-party endorsement, e.g. "Allure Best of
#      Beauty Award Winner") - NO Besque counterpart, ALWAYS removed. Checked first so it
#      wins any ambiguity - never substitute a cert icon for an award Besque hasn't won.
#   2. OFFER_BADGE_KEYWORDS (a discount/price/promo badge, e.g. "%-off roundel") - the
#      operator's own offer_text is the counterpart, substituted ONLY when offer_text was
#      actually supplied this run; no offer_text means no authorised Besque offer exists,
#      so it removes, same as the OFFER prose clause's own container rule.
#   3. CERT_BADGE_KEYWORDS (a certification/seal) - Besque's own real certifications are
#      the counterpart, substituted ONLY when certifications is non-empty.
#   4. anything else (a star rating, a "NEW" flag, an unrecognised badge) - removed, no
#      guessed counterpart.
AWARD_BADGE_KEYWORDS = ("award", "winner", "best of", "editor", "reader's choice",
                        "as seen in", "featured in", "recommended by")

OFFER_BADGE_KEYWORDS = ("%", "off", "save", "discount", "sale", "deal", "promo", "price")

# Word-boundary matching (2026-08-11) - plain substring matching on OFFER_BADGE_KEYWORDS
# let "off" match inside "official"/"offering", "sale" inside "salesperson", etc. \b only
# makes sense around actual word characters, so "%" (never part of a word) keeps plain
# substring matching - it can't have this false-positive problem in the first place.
_OFFER_BADGE_PATTERN = re.compile(
    "|".join(kw if kw == "%" else rf"\b{re.escape(kw)}\b" for kw in OFFER_BADGE_KEYWORDS),
    re.IGNORECASE,
)

CERT_BADGE_KEYWORDS = ("certif", "seal", "organic", "cruelty", "vegan", "natural",
                       "eco", "dermat", "accredit", "approved", "clinically tested")


def _is_award_shaped_badge(detail):
    detail_lower = (detail or "").lower()
    return any(kw in detail_lower for kw in AWARD_BADGE_KEYWORDS)


def _is_offer_shaped_zone(detail):
    return bool(_OFFER_BADGE_PATTERN.search(detail or ""))


def _is_cert_shaped_badge(detail):
    detail_lower = (detail or "").lower()
    return any(kw in detail_lower for kw in CERT_BADGE_KEYWORDS)


# Stat-shaped zone detection (2026-08-11, product_callout): a callout whose OWN reference
# content is a numeric/percentage/ratio/timescale claim ("94% saw visible results", "9 out
# of 10 customers agree", "3x faster", "results in just 7 days") has no Besque counterpart
# to substitute - Besque did not run the study that produced THAT number, so putting our
# product name in a container shaped for someone else's statistic isn't a substitution,
# it's noise wearing the shape of one. Deliberately reuses compliance.py's own numeric-
# claim patterns rather than a new regex written for this - NUMERIC_CLAIM_PATTERN/
# RATIO_CLAIM_PATTERN/TIMESCALE_CLAIM_PATTERN already detect the SHAPE of a stat claim
# generally (any percentage, any "N out of M", any "Nx more/faster/better", any "in N
# days/weeks/hours"), never a specific number - same "match the shape, not a list of known
# values" principle _is_award_shaped_badge already uses for award names.
STAT_CLAIM_PATTERNS = (
    compliance.NUMERIC_CLAIM_PATTERN, compliance.RATIO_CLAIM_PATTERN, compliance.TIMESCALE_CLAIM_PATTERN,
)


def _is_stat_shaped_zone(detail):
    detail_text = detail or ""
    return any(p.search(detail_text) for p in STAT_CLAIM_PATTERNS)


def _structural_zones_clause(structural_zones, zone_copy_text=None, cta_text=None, panel_copy=None,
                              testimonial=None, certifications=None, offer_text=None,
                              testimonial_zones=None, callout_copy=None):
    """Wires blueprint.structural_zones (2026-08-06 schema addition) into edit mode - every
    zone type gets a real substitute-or-remove decision now, read from what THIS reference
    actually shows, never a hardcoded position or layout:
    - brand_wordmark: ALWAYS substituted with BESQUE, same position/container, never
      removed - its absence is the single biggest reason a clone reads as unbranded next
      to the reference, so this doesn't wait on text_in_image the way the others do.
    - sub_line / body_copy: substituted with text matching THIS zone specifically -
      panel_copy (generate_copy.text_zone_targets' per-zone output) wins by exact
      position-string match when given; zone_copy_text is the fallback, used for every
      such zone when panel_copy is absent or doesn't cover this position. Callers must
      pass None for both (not the raw subtext) when text_in_image is off - otherwise these
      fall to REMOVAL, same as the container-removal exception already does for
      headline/subtext - never left showing the reference's own words.
    - cta: substituted with cta_text when supplied, same button shape/position - same
      None-when-suppressed rule as above.
    - badge (2026-08-07, SUBSTITUTION AS ONE RULE, generalised same day): checked in order
      - award/editorial/endorsement (see _is_award_shaped_badge) ALWAYS removes, no Besque
      counterpart exists; offer/discount-shaped (see _is_offer_shaped_zone) substitutes
      with offer_text when supplied, else removes; certification-shaped (see
      _is_cert_shaped_badge) substitutes with certifications when non-empty, else removes;
      anything else (a star rating, a "NEW" flag) removes. Never a guessed counterpart.
    - price_anchor (2026-08-07, generalised): substituted with offer_text in the SAME
      position/shape/size when supplied - a price zone IS an offer zone, whatever its own
      currency/amount said, that's the competitor's price, not Besque's. Removed when
      offer_text is falsy, same as any other suppressed-offer container.
    - product_callout (REVERSED 2026-08-12, Item 2 - was substituted with the bare
      product_name, unconditionally, on EVERY callout zone found; confirmed live, ad
      1576971893931336: four callout zones with four genuinely distinct reference
      details - "Flame & Burns"/thermogenic, "Energy", "Tight Skin", "Nail & Hair" - all
      four rendered the identical string "Besque Magic Body Oil", because nothing ever
      gave each zone its OWN copy). Now substituted with callout_copy_by_position's
      entry for THIS zone's own position (see callout_copy below) - a genuine Besque
      benefit or property per zone, never the product name repeated across several
      callouts. A zone with no matching callout copy is REMOVED, not filled with a
      repeated fallback string - this is the actual fix, not a smaller duplicate.
      Checked FIRST, same precedence as award-shaped badges (2026-08-11): a stat-shaped
      callout (see _is_stat_shaped_zone) ALWAYS removes regardless of callout copy - a
      callout whose reference content is a numeric/percentage/ratio/timescale claim has
      no Besque counterpart; repeating a benefit phrase in that container isn't a
      substitution either.
    - disclaimer: always REMOVED, same as above, but a legal/regulatory/medical disclaimer
      belonging to the reference brand is never Besque's for ANY product - the removal
      instruction also explicitly takes any pointing asterisk/footnote marker with it, so
      no dangling reference is left behind (2026-08-06, Grüns GLP-1 leak).
    - social_proof (2026-08-06, fabricated-testimonials fix): a single_quote zone is
      substituted with `testimonial` ONLY when a real one was supplied (a REAL customer
      review, selected from dedupe.get_reviews_for_product - see
      pipeline.select_testimonial_review) - rendered verbatim, never reworded or invented.
      No real review available, or an aggregate_bar (review-count/star-average - held
      pending approval, see CLAUDE.md), or any other/unrecognised social_proof_kind: ALWAYS
      REMOVED, never left for the general reproduce-faithfully instruction to govern -
      that was the actual bug (Gemini invented a plausible-sounding customer quote to fill
      a testimonial-shaped space it had nothing real to put there).

    Returns (clause_text, substituted_zone_types) - the second value lets the TEXT
    clause's "entire text budget" ban adjust itself so it never bans a category this
    function is simultaneously authorising, the same class of writer/rule6 contradiction
    this codebase has already hit more than once.

    panel_copy (2026-08-06, Grüns GLP-1 leak: a two-panel before/after joke rendered the
    SAME headline text in both panels; generalised 2026-08-11 to ANY count of sub_line/
    body_copy zones, not just 2+) is a list of {"position", "text"} dicts, one per zone,
    keyed by the EXACT position string generate_copy.text_zone_targets echoed back from
    this same structural_zones list - see generate_copy._text_zone_copy_clause. Before
    this existed, every sub_line/body_copy zone got the SAME zone_copy_text regardless of
    how many there were or what each one's own detail described; this routes each zone to
    its OWN text instead, by position, and falls back to zone_copy_text only for a
    position panel_copy doesn't cover (or when panel_copy is absent entirely - every
    existing single-panel blueprint sees byte-for-byte the same behaviour as before).

    testimonial, when given, is {"quote": str, "attribution": str} - a single real review,
    already selected and length-filtered by the caller (pipeline.py); this function never
    picks or rewrites it, only decides whether to render it here or remove the zone.

    testimonial_zones (Item 1, 2026-08-12 - deconstruct.py's schema field, previously
    unread anywhere) supplies PLACEMENT/STYLING detail ONLY for the social_proof
    substitution above - never content. `testimonial` (select_testimonial_review's real
    review) stays the sole content source; testimonial_zones' own text_verbatim/
    attribution (the COMPETITOR's testimonial content) is never read here at all. Matched
    to a social_proof/single_quote zone by ORDINAL position among such zones encountered
    in structural_zones, not by string-matching testimonial_zones.placement against
    structural_zones.position - those are two independently-worded free-text fields, and
    the common case is exactly one testimonial per ad, where ordinal matching is exact
    and a string-similarity match would only add a way to guess wrong. None (absent, or
    exhausted before this zone) reproduces the byte-for-byte prior behaviour - the
    zone's own container/position still govern, just without the extra styling detail.

    callout_copy (Item 2, 2026-08-12) is the SAME shape as panel_copy - a list of
    {"position", "text"} dicts, generate_copy.text_zone_targets/_text_zone_copy_clause
    now covering product_callout zones too (_PANEL_COPY_ZONE_TYPES) - but passed as its
    OWN parameter, deliberately NOT merged into panel_copy: panel_copy is gated by
    text_in_image at the caller (_edit_mode_instruction), because sub_line/body_copy
    zones are genuinely part of the same in-image TEXT BUDGET as headline/subtext (rule
    6's own wording already lists "additional body copy" in that budget). A
    product_callout is a self-contained visual element with an icon and a short label -
    the same category as badge/price_anchor/certifications above, none of which are
    gated by text_in_image - so callout_copy is passed UNGATED, and product_callout
    keeps working exactly like badge/price_anchor do regardless of the operator's
    text_in_image choice."""
    substituted_zone_types = set()
    testimonial_zones = testimonial_zones or []
    testimonial_zone_index = 0
    if not structural_zones:
        return "", substituted_zone_types

    panel_copy_by_position = {
        p.get("position"): p.get("text") for p in (panel_copy or [])
        if isinstance(p, dict) and p.get("position") and p.get("text")
    }
    callout_copy_by_position = {
        p.get("position"): p.get("text") for p in (callout_copy or [])
        if isinstance(p, dict) and p.get("position") and p.get("text")
    }

    substitute_lines = []
    remove_lines = []
    disclaimer_lines = []
    social_proof_remove_lines = []
    for z in structural_zones:
        zt = z.get("zone_type")
        pos = z.get("position") or "its shown position"
        container = z.get("container") or "none"
        if zt == "brand_wordmark":
            substitute_lines.append(
                f"- brand_wordmark at {pos} (container: {container}): replace its content "
                f"with BESQUE - same position, same container shape, never removed."
            )
            substituted_zone_types.add(zt)
        elif zt in ("sub_line", "body_copy"):
            panel_text = panel_copy_by_position.get(z.get("position"))
            if panel_text:
                substitute_lines.append(
                    f"- {zt} at {pos} (container: {container}): replace its wording with "
                    f"\"{panel_text}\" - same position, same container, matching the "
                    f"reference's own line count for this zone where possible, our words only."
                )
                substituted_zone_types.add(zt)
            elif zone_copy_text:
                # No distinct panel copy for this position - it would otherwise fall
                # back to re-quoting the same subtext already stated by rule 6's TEXT
                # POLICY and by _edit_mode_instruction's own TEXT branch, producing a
                # third literal occurrence of the identical string (2026-08-13 live
                # finding). Reference that authorisation instead of re-quoting it.
                substitute_lines.append(
                    f"- {zt} at {pos} (container: {container}): replace its wording with "
                    f"the same supporting text already authorised above (TEXT POLICY) - "
                    f"same position, same container, matching the reference's own line "
                    f"count for this zone where possible, never a different string."
                )
                substituted_zone_types.add(zt)
            else:
                remove_lines.append(f"- {zt} at {pos} (container: {container})")
        elif zt == "cta":
            if cta_text:
                substitute_lines.append(
                    f"- cta at {pos} (container: {container}): replace its label with "
                    f"\"{cta_text}\" - same button shape and position, our words only."
                )
                substituted_zone_types.add(zt)
            else:
                remove_lines.append(f"- cta at {pos} (container: {container})")
        elif zt == "badge":
            detail = z.get("detail") or ""
            # Checked in order - first match wins, never a guessed counterpart:
            # award/editorial (no Besque counterpart, ALWAYS removes) > offer/discount
            # (operator's offer_text) > certification (Besque's real certifications) >
            # anything else (removed).
            if _is_award_shaped_badge(detail):
                remove_lines.append(f"- badge at {pos} (container: {container})")
            elif offer_text and _is_offer_shaped_zone(detail):
                substitute_lines.append(
                    f"- badge at {pos} (container: {container}): this reads as an "
                    f"offer/discount badge (\"{detail}\") - replace its content with "
                    f"this run's authorised offer: \"{offer_text}\" - same shape and "
                    f"position, never a different number or term."
                )
                substituted_zone_types.add(zt)
            elif certifications and _is_cert_shaped_badge(detail):
                cert_list = ", ".join(certifications)
                substitute_lines.append(
                    f"- badge at {pos} (container: {container}): this reads as a "
                    f"certification badge (\"{detail}\") - replace its content with "
                    f"Besque's own real certifications: {cert_list} - same shape and "
                    f"position, never a certification Besque doesn't actually hold."
                )
                substituted_zone_types.add(zt)
            else:
                remove_lines.append(f"- badge at {pos} (container: {container})")
        elif zt == "price_anchor":
            if offer_text:
                substitute_lines.append(
                    f"- price_anchor at {pos} (container: {container}): replace its "
                    f"content with this run's authorised offer: \"{offer_text}\" - same "
                    f"position, shape, and size, never the competitor's own price/amount."
                )
                substituted_zone_types.add(zt)
            else:
                remove_lines.append(f"- price_anchor at {pos} (container: {container})")
        elif zt == "product_callout":
            detail = z.get("detail") or ""
            # Checked first, same reasoning as award-shaped badges above: a stat-shaped
            # callout has no Besque counterpart at all - a benefit phrase is not a
            # substitute for someone else's numeric claim, so this wins regardless of
            # whether callout copy is available.
            if _is_stat_shaped_zone(detail):
                remove_lines.append(f"- product_callout at {pos} (container: {container})")
            else:
                # REVERSED 2026-08-12 (Item 2): was `elif product_name:` - substituted
                # the bare product name into EVERY callout zone found, unconditionally.
                # Confirmed live: 4 distinct reference callouts (thermogenic/energy/
                # skin-tightening/nail-and-hair) all rendered the identical "Besque
                # Magic Body Oil". Now keyed by THIS zone's own position into
                # callout_copy_by_position (generate_copy.py's per-zone panel_copy,
                # ungated by text_in_image - see this function's own docstring) - a
                # zone with no matching callout copy is REMOVED, never filled with a
                # repeated fallback string.
                text_for_zone = callout_copy_by_position.get(z.get("position"))
                if text_for_zone:
                    substitute_lines.append(
                        f"- product_callout at {pos} (container: {container}): replace "
                        f"its content with \"{text_for_zone}\" - a genuine Besque "
                        f"benefit or property specific to THIS callout, same position "
                        f"and shape, never the product name and never the same text "
                        f"as any other callout in this image."
                    )
                    substituted_zone_types.add(zt)
                else:
                    remove_lines.append(f"- product_callout at {pos} (container: {container})")
        elif zt == "disclaimer":
            disclaimer_lines.append(f"- disclaimer at {pos} (container: {container})")
        elif zt == "social_proof":
            kind = z.get("social_proof_kind")
            if kind == "single_quote" and testimonial and testimonial.get("quote"):
                attribution = testimonial.get("attribution") or "a verified customer"
                # testimonial_zones supplies PLACEMENT/STYLING only (see this function's
                # own docstring) - matched by ordinal position among social_proof/
                # single_quote zones, never by string-matching two independently-worded
                # free-text fields. Exhausted/absent leaves styling_instruction empty -
                # byte-for-byte the prior wording, just without the extra detail.
                styling_zone = (testimonial_zones[testimonial_zone_index]
                                if testimonial_zone_index < len(testimonial_zones) else None)
                testimonial_zone_index += 1
                styling_instruction = ""
                if styling_zone and styling_zone.get("styling"):
                    styling_instruction = (
                        f" Match this reference's own styling for the card/container "
                        f"itself (never its wording, which comes only from the real "
                        f"review above): {styling_zone['styling']}"
                    )
                    if styling_zone.get("placement"):
                        styling_instruction += f" Position it at {styling_zone['placement']}."
                substitute_lines.append(
                    f"- social_proof (single_quote) at {pos} (container: {container}): "
                    f"replace with this REAL customer review, rendered EXACTLY as given, "
                    f"never reworded, shortened, or invented: \"{testimonial['quote']}\" "
                    f"— attributed to {attribution}. No star rating, age, or timeframe "
                    f"unless the review text itself states one."
                    + styling_instruction
                )
                substituted_zone_types.add(zt)
            else:
                social_proof_remove_lines.append(
                    f"- social_proof ({kind or 'unspecified kind'}) at {pos} (container: {container})"
                )
        # else: an unrecognised zone_type - no instruction either way.

    parts = []
    if substitute_lines:
        parts.append(
            "STRUCTURAL ZONES - SUBSTITUTE (STRICT): the following zones are kept exactly "
            "as positioned, their content replaced with ours, never the reference's own "
            "words and never invented: " + " ".join(substitute_lines)
        )
    if remove_lines:
        parts.append(
            "STRUCTURAL ZONES - REMOVE (STRICT): no Besque value exists for these zones "
            "yet - each container is removed entirely, not left as an empty shape, and "
            "the composition rebalanced into the freed space: " + " ".join(remove_lines)
        )
    if disclaimer_lines:
        parts.append(
            "STRUCTURAL ZONES - REMOVE, DISCLAIMER (STRICT): a legal, regulatory, or medical "
            "disclaimer belonging to the reference brand is NEVER Besque's, whatever product "
            "is being advertised and whatever the reference sells - remove the container "
            "entirely, not left as an empty shape. Any asterisk or footnote marker elsewhere "
            "in the frame (on a headline, subtext, or badge) that points to this disclaimer "
            "must be removed with it - a dangling asterisk with no referent left behind is "
            "its own defect, just as bad as the disclaimer text itself: "
            + " ".join(disclaimer_lines)
        )
    if social_proof_remove_lines:
        parts.append(
            "STRUCTURAL ZONES - REMOVE, SOCIAL PROOF (STRICT): no real, approved customer "
            "review or review count/rating exists for this run - remove these containers "
            "entirely, not left as an empty shape. NEVER invent a customer quote, name, "
            "star rating, or review count to fill this space - a fabricated testimonial is "
            "a compliance violation, not a stylistic choice: "
            + " ".join(social_proof_remove_lines)
        )
    clause = (" ".join(parts) + " ") if parts else ""
    return clause, substituted_zone_types


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


def _scene_lighting_facts(scene_lighting):
    """Turn deconstruct.py's observed visual.scene_lighting object into a facts sentence -
    OBSERVATIONS of this specific reference's own lighting (direction, hardness, colour
    temperature, shadow behaviour, grain, depth of field), never a style label. Returns ""
    when nothing was extracted (a pre-migration blueprint, or the model omitted the field
    this run) - callers fall back to the generic register-matching wording in that case;
    this function never guesses a value to fill the gap."""
    scene_lighting = scene_lighting or {}
    facts = []
    if scene_lighting.get("light_direction"):
        facts.append(f"light falls from {scene_lighting['light_direction']}")
    if scene_lighting.get("hardness"):
        facts.append(f"shadows are {scene_lighting['hardness']}")
    if scene_lighting.get("shadow_behaviour"):
        facts.append(f"shadow behaviour: {scene_lighting['shadow_behaviour']}")
    if scene_lighting.get("colour_temperature"):
        facts.append(f"colour temperature: {scene_lighting['colour_temperature']}")
    if scene_lighting.get("grain"):
        facts.append(f"grain/texture: {scene_lighting['grain']}")
    if scene_lighting.get("depth_of_field"):
        facts.append(f"depth of field: {scene_lighting['depth_of_field']}")
    if not facts:
        return ""
    return "OBSERVED SCENE LIGHTING (facts about this reference, not a style label): " + "; ".join(facts) + ". "


def _scene_composition_facts(layout_detail=None, visual=None):
    """Turn deconstruct.py's OBSERVED layout_detail/visual fields into a facts sentence
    describing the reference's EXISTING composition - same "observe, never guess"
    contract _scene_lighting_facts already established, just for placement instead of
    light. Used ONLY when ADDING a new element (product or text) to a scene that has
    nothing of that kind to substitute into (2026-08-07, reference usability gate
    reversal): the new element's position/scale must be derived from what THIS reference
    actually shows - its layout, how the frame divides, and where its existing elements
    already sit - never a fixed or default position, and never the same regardless of
    which reference this runs against. Returns "" when nothing was extracted (a
    pre-migration blueprint, or the model omitted these fields this run) - callers fall
    back to a generic composition-aware instruction in that case, never a guessed
    position."""
    layout_detail = layout_detail or {}
    visual = visual or {}
    facts = []
    if visual.get("layout"):
        facts.append(f"overall layout: {visual['layout']}")
    if layout_detail.get("frame_division"):
        facts.append(f"frame divides as: {layout_detail['frame_division']}")
    if layout_detail.get("zone_positions"):
        facts.append("existing elements sit at: " + "; ".join(layout_detail["zone_positions"]))
    if layout_detail.get("background_type"):
        facts.append(f"background: {layout_detail['background_type']}")
    if not facts:
        return ""
    return ("OBSERVED SCENE COMPOSITION (facts about THIS reference's existing layout, "
            "never a fixed position): " + "; ".join(facts) + ". ")


def _bottle_register_clause(scene_lighting):
    """Replaces the generic 'match the rendering register' instruction with concrete
    observed facts about THIS reference's own lighting, whenever deconstruct.py extracted
    them - a wording-only "match the style" instruction has already failed three times on
    this exact bottle-register bug (see CLAUDE.md's guardrails note), so this states what
    the scene's lighting actually IS rather than asking the model to infer it. Falls back
    to _register_lighting_only_clause()'s original generic wording only when scene_lighting
    is entirely empty (nothing to state facts about) - never a silent guess."""
    facts = _scene_lighting_facts(scene_lighting)
    if not facts:
        return _register_lighting_only_clause()
    return (
        facts +
        "The bottle's lighting, shadow, grain, and depth of field must match these "
        "observed facts about THIS scene EXACTLY - never the separate, unrelated studio "
        "lighting the product's own reference photo(s) happen to have been shot under. "
        "Geometry, proportions, and label stay exactly as stated above regardless. "
    )


def _register_clause(style, scene_lighting=None):
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
        + _bottle_register_clause(scene_lighting)
    )


def _non_carryover_exceptions_clause():
    """The 'everything else in the scene carries over exactly' family of clauses below
    (one per product branch) all need the SAME exceptions, in the SAME wording, so they
    can never drift out of sync with each other the way PERSON almost did (2026-08-10).

    AUDIT, 2026-08-12 15:13 live sweep: two separate drafts proved this catch-all was
    winning over rule 9 - a competitor's "by THE BODY FIRM" tagline survived directly
    beneath the substituted BESQUE logo, and a competitor's product jar survived in
    frame beside the substituted Besque bottle. Rule 9 (brand_rules) already says both
    must never survive, but it is stated further from the point of use than THIS
    clause, which was still saying the opposite ("everything else... carries over...
    exactly", with nothing here naming either exception) right where the model decides
    what to keep. A THIRD exception is added the same session for the composition-
    adaptation case (a prop/float/holder sized for the reference's own differently-
    shaped product, or a second product the reference shows) - the same catch-all was
    also silently re-asserting "carries over exactly" against the product-count
    composition-adaptation instruction stated in the product branches above.

    Called fresh each time rather than cached as a module constant so a future
    exception can be added without hunting for five call sites to update by hand -
    there is exactly one place this text is written."""
    return (
        "EXCEPT THE PERSON (see PERSON below, which governs pose, body position, and "
        "wardrobe separately), EXCEPT any competitor brand mark - logo, wordmark, "
        "tagline, or \"by X\" endorsement line, wherever it sits in frame - or the "
        "competitor's own product or packaging anywhere in the scene, including a "
        "SECOND product beyond the one being substituted, added, or removed above (see "
        "rule 9 above, which governs both and wins over this carry-over instruction no "
        "matter how broadly \"everything else\" might otherwise be read), and EXCEPT "
        "the scale of any prop, holder, float, or opening sized for the reference's own "
        "product - that adapts to fit the substituted Besque bottle's real, fixed "
        "proportions instead, never the reverse (see the product instruction above)"
    )


def _edit_mode_instruction(text_in_image=False, headline=None, subtext=None, offer_text=None,
                            include_product=True, reference_has_product=True,
                            reference_has_text_zone=True, layout_detail=None, visual=None,
                            retheme_colours=True, palette=None,
                            substance_colour=None, style=None, scene_lighting=None,
                            typography_zones=None,
                            structural_zones=None, cta_text=None, product_name=None,
                            panel_copy=None, testimonial=None, certifications=None,
                            competitor_props_clause="", face_present=None,
                            testimonial_zones=None, clone_mode=False):
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

    reference_has_product=False (Step 2, Part 4; REVERSED 2026-08-07, reference usability
    gate reversal) now changes WHICH ACTION is taken, not just which sentence explains a
    no-op: the reference ad itself has no product in frame
    (blueprint.layout_detail.product_count==0 or product_category=="not_product"), but
    include_product=True still means a Besque product belongs in the output - there is
    just nothing to SUBSTITUTE, so it is ADDED into the scene instead, at a position and
    scale DERIVED from layout_detail/visual (see _scene_composition_facts), never a fixed
    or default placement and never skipped. This must generalise identically across every
    reference shape - no ad_id/competitor/page special-casing anywhere in this function.

    reference_has_text_zone (2026-08-07, same reversal) is the text-side analogue,
    independent of reference_has_product - a reference can have a product but no
    headline/text zone, text but no product, both, or neither. False means an authorised
    headline/subtext has no existing zone to substitute into, so it is ADDED into clean
    negative space instead (see the TEXT branch below) - never suppressed just because
    the reference itself had nothing there.

    layout_detail/visual (2026-08-07) are the raw blueprint sub-objects, forwarded only so
    _scene_composition_facts can derive ADD placement from them - never read for anything
    else here.

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
    other side.

    style=="illustrated" (2026-08-06, Grüns GLP-1 leak - the cause was structural, not
    textual): the include_product branch below stops pointing at "the reference photo(s)
    that follow" for this style, because generate_image() no longer attaches any this run
    (see its own docstring) - passing a photographic reference AND demanding faithful
    substitution is exactly what made Gemini render a photograph and composite it into a
    drawing. Instead the bottle is described briefly in the scene's own visual language:
    recognisable by silhouette, colour, and product_name, drawn natively flat rather than
    substituted from a photo. product_name falls back to "Besque" when not given, the same
    fallback rule 4 already states for the unbranded case. Every other style is
    byte-for-byte unaffected - reference photos are still attached and still demanded for
    every photographic register.

    PERSON (2026-08-10): the person in a competitor's ad is their licensed model or a real
    customer, not a Besque asset - reproducing their actual likeness is a rights
    violation, not a fidelity setting. C1 (compliance_rules.py) already says this once,
    globally, in the shared compliance block; it was losing to the opening's own "carry
    over EXACTLY"/"geometry is preserved" language and the five "everything else stays
    exactly as it appears" catch-all lines below, none of which named the person either
    way. Fixed the same way colour/text/offer already are: a new named PERSON row in
    this same enumerated partition (added once, unconditionally, after the product
    branches - see below), PLUS carving the person out of all five "everything else"
    catch-all lines so they no longer contradict it. Without the catch-all edit the new
    clause would just be demanded and forbidden in the same prompt - the exact shape
    that produced artifact 1136's fabricated testimonials.

    AUDIT, item 3 (2026-08-12): the PERSON row's original REPRODUCE side listed pose,
    body position, and wardrobe silhouette alongside pure camera mechanics - reproducing
    most of the person's identity while calling the OTHER half (face/hair) a
    "substitution." Confirmed live (ad 1859386398364761: same face, clothing, and pose
    all survived). Rewritten so REPRODUCE covers only camera/scene mechanics (framing,
    crop, camera angle, distance, lighting, compositional position); pose, body
    position, and wardrobe moved to SUBSTITUTE. The retheme_colours opening (both
    branches) also used to imply the person's own pose/wardrobe carried over as part of
    "geometry preserved"/"overall layout reproduced" - both now say explicitly that this
    does not extend to the person, deferring entirely to PERSON below."""
    suppressing_text = not (text_in_image and headline)
    # Clone mode (2026-08-11): the OFFER clause below used to trust offer_text's own
    # truthiness alone and ask Gemini to judge VISUALLY whether the reference shows an
    # offer, discount, price, or CTA badge - exactly the "prompt asks the model to decide"
    # pattern this codebase has repeatedly found unreliable (see the top of CLAUDE.md).
    # On a reference with no real offer-shaped zone, Gemini invented one - a live "20%
    # OFF" badge with nothing in the reference to substitute into. When clone_mode is on,
    # offer_text is only EFFECTIVELY present here if this reference actually has a real
    # offer-shaped zone (_structural_zones_have_offer - the SAME structural check
    # reference_has_offer_zone/pipeline.py's own upstream suppression already use, not a
    # second independent judgment that could disagree with it) - never Gemini's own
    # visual call. Computed ONCE, here, so suppressing_offer (governs the container-
    # removal exception wording below) and the OFFER clause itself can never disagree
    # about whether an offer is actually being rendered this call - the exact demand-and-
    # forbid shape that produced artifact 1136's fabricated testimonials, on a different
    # input. clone_mode=False (the default) is unaffected: offer_text's own truthiness is
    # the only gate, byte-for-byte today's behaviour. _structural_zones_clause's own
    # badge/price_anchor substitution (elsewhere in this function) is NOT changed to use
    # this - it already only ever acts on a zone that genuinely exists in structural_zones,
    # so it never had this vulnerability; only the prose OFFER clause asked Gemini to look
    # for something that might not be there at all.
    effective_offer_text = (
        offer_text if (not clone_mode or _structural_zones_have_offer(structural_zones))
        else None
    )
    suppressing_offer = not effective_offer_text
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
            "geometry is preserved with a small, natural amount of variation - not a "
            "mechanically exact clone - and colour is substituted. Composition, layout, "
            "camera angle, spacing, lighting direction, contrast relationships, tonal "
            "hierarchy, and text placement all carry over CLOSELY as shot in the "
            "reference: camera angle, framing, background detail, or prop arrangement "
            "may shift by roughly 5-8%, the same way two real photographs of the same "
            "real scene, taken moments apart, are never pixel-identical. This is a "
            "small natural variation only, never a different composition - the overall "
            "structure and which non-person elements appear must still carry over from "
            "the reference. This does NOT extend to the person - see PERSON below, "
            "which governs pose, body position, and wardrobe separately and overrides "
            "anything about the person implied here - NOR to any competitor brand mark "
            "(logo, wordmark, tagline, or \"by X\" endorsement line) or the "
            "competitor's own product/packaging anywhere in frame, including a SECOND "
            "product the reference shows - see rule 9 above, which governs those and "
            "overrides this carry-over instruction entirely, regardless of how broadly "
            "\"which non-person elements appear\" might otherwise be read. "
            + exception_clause +
            f"At the same time, every hue in the scene (background, props, "
            f"surfaces) re-maps to Besque's palette: {effective_palette} - "
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
            "palette, text placement, and overall layout closely, with a small, "
            "natural 5-8% variation allowed in camera angle, framing, background "
            "detail, or prop arrangement - not a mechanically exact clone, but never a "
            "different composition either. This does NOT extend to the person - see "
            "PERSON below, which governs pose, body position, and wardrobe separately "
            "and overrides anything about the person implied here - NOR to any "
            "competitor brand mark (logo, wordmark, tagline, or \"by X\" endorsement "
            "line) or the competitor's own product/packaging anywhere in frame, "
            "including a SECOND product the reference shows - see rule 9 above, which "
            "governs those and overrides this reproduce instruction entirely. "
            + exception_clause
        )
    opening += _register_clause(style, scene_lighting)
    opening += competitor_props_clause or ""

    if include_product and reference_has_product and style == "illustrated":
        name = product_name or "Besque"
        base = opening + (
            "Changing ONLY the product. Remove the competitor's product entirely and draw "
            f"the Besque product NATIVELY in this scene's own illustrated visual language - "
            f"flat, matching the surrounding artwork's own line weight and shading, never a "
            f"photograph or photorealistic render composited into the drawing. No product "
            f"reference photo is attached this run, on purpose: work from silhouette, "
            f"colour, and the label name alone - \"{name}\". Secondary label content "
            f"(sub-lines, certification icons, fine print) does not need to be legible at "
            f"this scale in this style; name and colour accuracy matter, secondary-text "
            f"legibility does not. If the reference shows more than one distinct "
            f"product, account for each explicitly rather than silently dropping any "
            f"of them - substitute a Besque item where a genuine substitution makes "
            f"sense, or deliberately remove one with no Besque equivalent and adjust "
            f"the surrounding composition so the scene makes sense without it. "
            + _substance_recolour_clause(substance_colour) +
            f"Everything else in the scene - {_non_carryover_exceptions_clause()} - carries over "
            "from the source image exactly as the reproduce-faithfully instruction above states (never a stricter 'exactly' reintroduced here, including its small natural variation allowance). "
        )
    elif include_product and reference_has_product:
        lighting_facts = _scene_lighting_facts(scene_lighting)
        # "with its lighting" (the old wording here) was ambiguous between "the scene's
        # lighting" and "the product reference photo's own separate studio lighting" - the
        # two contradict whenever the reference photo is a clean studio/cutout shot dropped
        # into a UGC or non-studio scene. State the scene's own observed lighting facts
        # explicitly instead, so there's nothing left to infer; falls back to a
        # register-level instruction (never the reference photo's own lighting) only when
        # no scene_lighting was extracted at all.
        lighting_instruction = lighting_facts or (
            "Light the substituted product to match THIS SCENE's own lighting register - "
            "never the separate, unrelated lighting the product's reference photo(s) "
            "happen to have been shot under. "
        )
        base = opening + (
            "Changing ONLY the product. Remove the competitor's product entirely and "
            "place the Besque product (shown in the reference photo(s) that follow, if "
            "any) in its position, matching the original shot's composition as "
            "faithfully as possible. The bottle's own geometry and proportions are "
            "FIXED (see the bottle-fixed instruction above) - if the reference's own "
            "product sits inside, on, or against a prop, holder, float, or opening "
            "sized for ITS shape, that PROP is what adapts: resize or reshape it to "
            "properly fit the Besque bottle's real proportions, never the reverse "
            "(never stretch, squeeze, or shrink the bottle to fit a prop sized for a "
            "differently-shaped product). If the reference shows more than one "
            "distinct product, account for each explicitly rather than silently "
            "dropping any of them - substitute a Besque item where a genuine "
            "substitution makes sense, or deliberately remove one with no Besque "
            "equivalent and adjust the surrounding composition (props, spacing, "
            "balance) so the scene makes sense without it. " + lighting_instruction
            + _substance_recolour_clause(substance_colour) +
            f"Everything else in the scene - {_non_carryover_exceptions_clause()} - carries over "
            "from the source image exactly as the reproduce-faithfully instruction above states (never a stricter 'exactly' reintroduced here, including its small natural variation allowance). "
        )
    elif include_product and not reference_has_product and style == "illustrated":
        # ADD, illustrated register (2026-08-07, reference usability gate reversal): the
        # reference has no product to substitute, but include_product=True still means
        # one belongs in the output - added natively into the scene's own illustrated
        # visual language, same drawing constraints as the substitute-illustrated branch
        # above (no reference photo attached, work from silhouette/colour/name alone),
        # just with no competitor product to remove first. Placement is DERIVED from this
        # reference's own observed composition, never a fixed position.
        name = product_name or "Besque"
        composition_facts = _scene_composition_facts(layout_detail, visual)
        placement_instruction = composition_facts or (
            "Integrate it naturally into this scene's own existing composition and open "
            "space, at a scale consistent with the surrounding elements - never a fixed "
            "or default position invented independently of what this scene shows. "
        )
        base = opening + (
            "The reference image has NO product in frame - there is nothing to "
            "substitute, so instead ADD the Besque product NATIVELY into this scene's "
            f"own illustrated visual language: flat, matching the surrounding artwork's "
            f"own line weight and shading, never a photograph or photorealistic render "
            f"composited into the drawing. No product reference photo is attached this "
            f"run, on purpose: work from silhouette, colour, and the label name alone - "
            f"\"{name}\". " + placement_instruction +
            "Secondary label content (sub-lines, certification icons, fine print) does "
            "not need to be legible at this scale in this style; name and colour "
            "accuracy matter, secondary-text legibility does not. "
            # No _substance_recolour_clause here, deliberately: that instruction only
            # makes sense when a product-derived substance is ALREADY in frame, which
            # correlates with the reference already having a product - a reference with
            # none almost certainly has no such substance to recolour either, so this
            # would be dead weight text (same reasoning the substitute branches' use of
            # it doesn't need to restate).
            f"Everything else in the scene - {_non_carryover_exceptions_clause()} - carries over "
            "from the source image exactly as the reproduce-faithfully instruction above states (never a stricter 'exactly' reintroduced here, including its small natural variation allowance), aside from this addition. "
        )
    elif include_product and not reference_has_product:
        # ADD, photographic register (2026-08-07, reference usability gate reversal):
        # same "nothing to substitute, so add instead" logic as the illustrated branch
        # above, for every other production style. Placement/scale is DERIVED from this
        # reference's own observed composition (_scene_composition_facts) - never a fixed
        # or default position, and lighting still comes from the scene's own observed
        # facts exactly as the substitute branch above already does.
        lighting_facts = _scene_lighting_facts(scene_lighting)
        lighting_instruction = lighting_facts or (
            "Light it to match THIS SCENE's own lighting register - never the separate, "
            "unrelated lighting the product's reference photo(s) happen to have been "
            "shot under. "
        )
        composition_facts = _scene_composition_facts(layout_detail, visual)
        placement_instruction = composition_facts or (
            "Integrate it naturally into this scene's own existing composition and open "
            "space, at a scale and depth consistent with the surrounding elements - "
            "never a fixed or default position invented independently of what this "
            "scene shows. "
        )
        base = opening + (
            "The reference image has NO product in frame - there is nothing to "
            "substitute, so instead ADD the Besque product (shown in the reference "
            "photo(s) that follow, if any) newly into this scene. " + placement_instruction
            + lighting_instruction
            # No _substance_recolour_clause here either - see the illustrated ADD
            # branch's own comment above for why.
            + f"Everything else in the scene - {_non_carryover_exceptions_clause()} - carries over "
            "from the source image exactly as the reproduce-faithfully instruction above states (never a stricter 'exactly' reintroduced here, including its small natural variation allowance), aside from this addition. "
        )
    else:
        base = opening + (
            "This is a deliberately productless edit - do NOT add any Besque product, "
            f"bottle, or packaging anywhere in the scene. Everything else in the scene - "
            f"{_non_carryover_exceptions_clause()} - carries over from the source image "
            "exactly as the reproduce-faithfully instruction above states (never a "
            "stricter \"exactly\" reintroduced here, including its small natural "
            "variation allowance). "
        )

    # PERSON (2026-08-10): unconditional, appended once regardless of which product
    # branch fired above - person substitution is independent of product SUBSTITUTE vs
    # ADD vs productless, same reasoning TEXT/OFFER/EFFICACY CLAIMS below already use for
    # being appended once rather than duplicated per branch. Placed right after the
    # product branches and before TEXT, so the enumerated partition reads in scene order:
    # composition (opening) -> product -> person -> text -> offer -> efficacy claims.
    #
    # face_present (Item 8, 2026-08-12): face-to-body substitution. When the reference's
    # own face_present says has_face AND prominence=="primary" (deconstruct.py's schema),
    # the usual REPRODUCE-pose/SUBSTITUTE-identity split below is REPLACED, not
    # supplemented, by a face-to-body instruction - having both present at once would be
    # exactly the "demand and forbid the same element" contradiction shape this session
    # has repeatedly found (see item 1/product_count above). Absent/incidental/none falls
    # through to the existing clause unchanged, so a blueprint with no face_present field,
    # or one where the face isn't the compositional focus, sees byte-for-byte the same
    # prompt as before this item existed. Prompt-level only: no actual pixel masking/
    # cropping happens here - that structural work is a separate, later task.
    face_present = face_present or {}
    face_is_primary = bool(face_present.get("has_face")) and face_present.get("prominence") == "primary"
    if face_is_primary:
        base += (
            "PERSON -> BODY AREA (STRICT, face_present.prominence=primary - REPLACES the "
            "usual PERSON reproduce/substitute instruction, not additional to it): the "
            "reference's human subject is FACE-PRIMARY - the compositional focus is the "
            "face itself, not incidental. Rather than substituting a different generic "
            "face onto the same framing, the subject in frame becomes a BODY AREA "
            "instead - arm, neck, stomach, or legs, chosen to match the skin concern "
            "this reference actually addresses - never the face or head. This is NOT a "
            "crop of the existing composition and NOT a face-swap: preserve the "
            "composition, lighting, emotional tone, and overall structure of the scene "
            "exactly as the reference shows (subject to the small variation allowance "
            "above), but the chosen body area occupies where the person's face/upper "
            "body was, at a scale and framing that reads as a deliberately composed "
            "shot of that body area - never an obviously cropped or awkwardly "
            "substituted fragment. Rules 10/11 above (age-appropriate skin texture) "
            "still apply fully to this body area. This is prompt-level guidance only; "
            "no structural masking or compositing happens here. "
        )
    else:
        base += (
            # AUDIT (item 3, 2026-08-12): "REPRODUCE exactly as shown: pose, body
            # position, ... wardrobe silhouette..." used to sit on the REPRODUCE side -
            # telling the model to keep the reference's own pose, body position, and
            # clothing silhouette, then separately telling it to swap only the face/hair.
            # That is reproducing most of the person's identity while calling it
            # "substitution" - confirmed live (ad 1859386398364761: same face, same
            # clothing, same pose survived). REPRODUCE is now scoped to pure camera/scene
            # mechanics only; pose, body position, and wardrobe moved to SUBSTITUTE,
            # alongside face/hair/identity - the person is substituted, not partially
            # preserved.
            "PERSON: if a person appears anywhere in the reference image, this is one "
            "instruction with two parts, not two competing ones, the same shape as the "
            "colour instruction above. REPRODUCE only the camera/scene mechanics: "
            "framing, crop, camera angle, distance, lighting on the subject, and where "
            "in the composition the person sits. SUBSTITUTE the person themselves: "
            "face, hair, pose, body position, wardrobe/clothing, and every other "
            "identifying or appearance-defining feature must belong to a different, "
            "generic, non-identifiable Besque subject - never the reference's own "
            "individual's face, hair, pose, or clothing, even partially or "
            "approximately. Match ONLY the skin-condition presentation shown in the "
            "reference - that presentation is the ad's argument - but never the same "
            "face, hair, pose, clothing, or identity. AGE IS THE ONE EXCEPTION TO "
            "'MATCH THE REFERENCE': do NOT match the reference's own apparent age bracket, "
            "even though skin-condition presentation is otherwise matched - rule 10 above "
            "(SUBJECT AGE) governs the substituted model's age instead, unconditionally, "
            "regardless of what age the reference's own model happens to be. A previous "
            "version of this clause said to match the reference's age bracket - that directly "
            "contradicted rule 10 and is why it did not reliably bind; there is no 'match the "
            "reference's age' instruction anywhere in this prompt any more. The person in a "
            "competitor's ad is their licensed model or a real customer, not a Besque asset; "
            "reproducing their actual likeness - face, pose, or clothing included - is a "
            "rights violation, not a fidelity choice. This is compliance rule C1 above, made "
            "specific at the point of use for this reference. "
        )

    eff_headline, eff_subtext = effective_authorised_text(text_in_image, headline, subtext)
    # structural_zones' sub_line/body_copy/cta substitution must never be offered when
    # text_in_image itself is off - same gating headline/subtext already use, so these
    # zones fall to REMOVAL below instead of showing the reference's own words when the
    # operator asked for no baked-in text this run.
    zone_copy_text = eff_subtext if text_in_image else None
    zone_cta_text = cta_text if text_in_image else None
    zone_panel_copy = panel_copy if text_in_image else None
    # testimonial is deliberately NOT gated by text_in_image (UNGATED 2026-08-12, Item 1
    # - was `testimonial if text_in_image else None`, matching sub_line/body_copy/cta).
    # That gate was a category error: text_in_image decides whether HEADLINE/SUBTEXT
    # bake into the image pixels or render as a separate HTML overlay - a testimonial
    # CARD (avatar, name, quote, reaction bar) is not headline text competing for that
    # same overlay slot, it's a self-contained visual element, the same category as
    # badge/price_anchor/product_callout/certifications below, none of which are gated
    # by text_in_image either. Confirmed live: ad 1354698976158962 had a real review
    # ready to substitute (select_testimonial_review found one) and a genuine
    # social_proof/single_quote zone, but text_in_image=False on that run silently
    # dropped it anyway - the zone was never actually short of content, it was gated on
    # the wrong flag. The "no real review -> remove, never invent" rule inside
    # _structural_zones_clause is the ONLY gate the testimonial zone needs.
    zone_testimonial = testimonial
    # certifications is deliberately NOT gated by text_in_image - a cert badge is a
    # brand/product graphic element baked into the base image regardless of whether
    # headline/subtext render separately as an HTML overlay, the same treatment
    # brand_wordmark already gets (also unconditional), not sub_line/cta's treatment.
    structural_clause, substituted_zone_types = _structural_zones_clause(
        structural_zones, zone_copy_text=zone_copy_text, cta_text=zone_cta_text,
        panel_copy=zone_panel_copy, testimonial=zone_testimonial, certifications=certifications,
        offer_text=offer_text, testimonial_zones=testimonial_zones,
        # callout_copy (Item 2, 2026-08-12): the RAW panel_copy, NOT zone_panel_copy -
        # product_callout is a self-contained visual element (icon + label), the same
        # category as badge/price_anchor/certifications above, none of which are gated
        # by text_in_image - see _structural_zones_clause's own docstring.
        callout_copy=panel_copy,
    )
    if eff_headline:
        # The exact headline/subtext wording is stated ONCE, by rule 6's TEXT POLICY
        # above - this function must reference that authorisation, never re-quote the
        # string itself, or the same text ends up stated twice (2026-08-13 live finding:
        # the literal string appeared 2-3x in one assembled prompt across rule 6, this
        # TEXT branch, and STRUCTURAL ZONES' sub_line/body_copy fallback).
        # The "entire text budget" ban must never contradict STRUCTURAL ZONES below by
        # banning a category that clause is simultaneously authorising (2026-08-06) - the
        # same writer/rule6 contradiction shape this codebase has already hit more than
        # once. Default (no structural_zones substitution active) is BYTE-FOR-BYTE the
        # original wording - every blueprint without structural_zones, and every one
        # where none of sub_line/body_copy/cta apply, sees no change here at all.
        if substituted_zone_types & {"sub_line", "body_copy", "cta"}:
            budget_ban = (
                "no ingredient list or mechanism/benefit paragraph may ALSO be rendered "
                "beyond what STRUCTURAL ZONES below explicitly authorises"
            )
        else:
            budget_ban = (
                "no ingredient list, mechanism or benefit paragraph, additional body "
                "copy, or CTA sentence may ALSO be rendered"
            )
        # Item 9 (2026-08-12): the highest-risk failure is split-screen/before-after/
        # multi-panel layouts each receiving their OWN copy of the same headline/subtext -
        # stated once here, ahead of both text sub-branches below, since duplication risk
        # applies to substituting into an existing zone and adding into negative space
        # equally. Not gated on semantic_split specifically: a multi-panel layout that
        # deconstruct didn't classify as a before/after split (e.g. typography_zones with
        # several headline-shaped entries) has the identical risk.
        canvas_unity_clause = (
            "The output canvas is ONE unified space, even when the composition shows a "
            "split-screen, before/after, or multi-panel layout (see BEFORE/AFTER "
            "SEMANTICS above, where one applies) - the headline, the supporting text, "
            "and any authorised review text each render EXACTLY ONCE across the entire "
            "frame, never once per panel or half. "
        )
        if reference_has_text_zone:
            base += (
                canvas_unity_clause +
                f"TEXT: preserve the reference image's text zones EXACTLY as they appear - "
                f"same size, position, weight, casing, and text colour - and replace ONLY "
                f"the wording with the headline/supporting text already authorised above "
                f"(TEXT POLICY) - never a different string, same layout, our words. Typography (typeface "
                f"weight, casing, placement) and text colour are INHERITED from the "
                f"reference exactly as shown, never re-themed or restyled to a different "
                f"look - the wording is the ONLY thing that changes. This is the ENTIRE text "
                f"budget for this image - {budget_ban}, even if such text exists in the "
                f"product description, generated copy, or reference ad. "
                f"The competitor's brand name, product name, and claims must NEVER survive "
                f"into the output, even inside this inherited styling. "
            )
        else:
            # ADD, no existing text zone to substitute into (2026-08-07, reference
            # usability gate reversal): "preserve the reference's text zones exactly"
            # would be vacuous/wrong when none exist - instead place the authorised copy
            # newly into clean negative space, positioned per this reference's OWN
            # observed composition, never a fixed position and never an invented
            # container/badge shape that isn't already part of the scene.
            composition_facts = _scene_composition_facts(layout_detail, visual)
            placement_instruction = composition_facts or (
                "Place it in clean open space consistent with this scene's own "
                "composition - never a fixed or default position invented "
                "independently of what this scene shows. "
            )
            base += (
                canvas_unity_clause +
                f"TEXT: the reference has no existing text zone to substitute into - "
                f"place the headline/supporting text already authorised above (TEXT "
                f"POLICY) - never a different string - newly into the scene as in-scene "
                f"typography, in clean negative space. " + placement_instruction +
                f"Do not invent a container, badge, banner, or bubble shape that isn't "
                f"already part of the scene to hold it. This is the ENTIRE text "
                f"budget for this image - {budget_ban}, even if such text exists in the "
                f"product description, generated copy, or reference ad. "
                f"The competitor's brand name, product name, and claims must NEVER "
                f"survive into the output. "
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
    base += structural_clause
    if effective_offer_text:
        base += (
            f"OFFER: if the reference shows an offer, discount, price, or CTA badge, "
            f"preserve its position, shape, size, colour, and typography EXACTLY as "
            f"shown in the reference - and replace ONLY its wording with: "
            f"{effective_offer_text}. Do not invent a different number, percentage, or "
            f"term; do not restyle, resize, or recolour the badge itself. "
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


def _occlusion_enabled():
    """OCCLUDE_PERSON env var (Item 1, 2026-08-12), read FRESH on every call - not
    cached at module-import time the way FORCE_REPROCESS is (see CLAUDE.md's own
    warning about that pattern leaving a stale value active for a whole process
    lifetime). Deliberate here: this flag exists specifically to be flipped mid manual
    test ("switchable off without a code change"), and an import-time cache would defeat
    exactly that. Off by default - prompt-only guardrails have already failed twice on
    this exact problem (PERSON identity, then SUBJECT AGE), so blocking pixels outright
    is a genuinely new, unverified mechanism, not a proven one."""
    return os.getenv("OCCLUDE_PERSON", "").strip().lower() in ("1", "true", "yes", "on")


# Coarse position-keyword -> occlusion box, as (left, top, right, bottom) fractions of
# (width, height). Derived from face_present.location's own free text (deconstruct.py's
# schema) - never a fixed per-ad box; the same generic keyword set applies to every
# reference regardless of which ad it is. Deliberately GENEROUS, not a tight bounding
# box: this codebase has no face/person segmentation library (adding one was evaluated
# and set aside earlier this session - opencv-python-headless alone is a ~60MB wheel for
# a single detector), so "occlude a wide enough region to plausibly contain the whole
# person" is what's actually achievable today. Ordered most-specific compound phrase
# first, so "upper-left" doesn't fall through to the plain "upper" bucket.
_OCCLUSION_KEYWORD_BOXES = (
    (("upper-left", "top-left"), (0.0, 0.0, 0.65, 0.65)),
    (("upper-right", "top-right"), (0.35, 0.0, 1.0, 0.65)),
    (("lower-left", "bottom-left"), (0.0, 0.35, 0.65, 1.0)),
    (("lower-right", "bottom-right"), (0.35, 0.35, 1.0, 1.0)),
    (("upper", "top"), (0.0, 0.0, 1.0, 0.65)),
    (("lower", "bottom"), (0.0, 0.35, 1.0, 1.0)),
    (("left",), (0.0, 0.0, 0.65, 1.0)),
    (("right",), (0.35, 0.0, 1.0, 1.0)),
    (("centre", "center", "middle"), (0.15, 0.0, 0.85, 1.0)),
)
# No positional keyword matched at all in face_present.location - a generous central
# vertical band, since deconstruct already confirmed has_face=true (a person is present
# SOMEWHERE in frame); never the full frame, so surrounding composition/background clues
# still reach Gemini for everything _occlude_person_region does not black out.
_DEFAULT_OCCLUSION_BOX = (0.15, 0.0, 0.85, 1.0)


def _derive_occlusion_box(location_text):
    text = (location_text or "").lower()
    for keywords, box in _OCCLUSION_KEYWORD_BOXES:
        if any(kw in text for kw in keywords):
            return box
    return _DEFAULT_OCCLUSION_BOX


def _occlude_person_region(image_bytes, face_present):
    """Item 1 (2026-08-12), gated by _occlusion_enabled() (default off): when the
    blueprint's face_present.has_face is true, at ANY prominence, block out an
    approximate person-shaped region of the COMPETITOR's reference bytes before they
    ever reach Gemini - structural, not textual, the same lever that worked for the
    illustrated-bottle leak (drop/alter the input rather than ask the model not to look
    at it). Unlike that fix, this one is UNVERIFIED: prompt-only guardrails have already
    failed twice on this exact problem, so this is a genuinely new mechanism being
    tested live, not a proven one - hence the flag and the manual test it exists for.

    Returns (bytes, occluded: bool) - the caller needs to know whether occlusion
    actually happened, to decide whether to add the "this is a blocked region, not a
    shape to reproduce" prompt clause; stating that when nothing was occluded would be
    describing something that isn't there.

    Dimensions are NEVER changed - only pixels within the derived box are overwritten -
    so derive_aspect_ratio reading these same bytes downstream sees the identical
    width:height it would see unoccluded. On ANY failure (corrupt bytes, decode error),
    returns the ORIGINAL bytes unoccluded rather than raising - a broken mask must never
    take down a generation that would otherwise have worked."""
    if not (face_present or {}).get("has_face"):
        return image_bytes, False
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img.load()
        width, height = img.size
        img = img.convert("RGB")
        left_f, top_f, right_f, bottom_f = _derive_occlusion_box(face_present.get("location"))
        box = (int(left_f * width), int(top_f * height), int(right_f * width), int(bottom_f * height))
        ImageDraw.Draw(img).rectangle(box, fill=(128, 128, 128))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        occluded_bytes = buf.getvalue()
        occluded_img = Image.open(io.BytesIO(occluded_bytes))
        if occluded_img.size != (width, height):
            raise ValueError(f"occlusion changed dimensions: {(width, height)} -> {occluded_img.size}")
        return occluded_bytes, True
    except Exception as e:
        log.warning("OCCLUDE_PERSON: failed to occlude person region (%s: %s) - using "
                    "the reference image unoccluded", type(e).__name__, e)
        return image_bytes, False


# ImageConfig.image_size defaults to "1K" whenever it's left unset, which is every call
# before 2026-08-06 - confirmed live (Grüns GLP-1 run, artifact 1083): a 1080x1920
# reference produced a 768x1376 draft, well under Meta's 1080x1350 minimum for a 4:5 feed
# image. "2K" is the next tier up (image_size also accepts "4K"); set explicitly on every
# ImageConfig this module builds, in EVERY branch including the ones that omit
# aspect_ratio entirely (the model-infers-aspect-ratio fallback) - resolution and aspect
# ratio are independent knobs, and omitting one must never mean omitting the other.
IMAGE_SIZE = "2K"

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
                    retheme_colours=True, critic_feedback=None, cta_text=None, panel_copy=None,
                    testimonial=None, product_count=None, clone_mode=False):
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

    Aspect ratio (Item 6a, edit-mode-only, briefly removed then REINSTATED 2026-08-07):
    derive_aspect_ratio(competitor_image_bytes) snaps the reference's own width:height to
    the nearest ratio Vertex's ImageConfig supports, set explicitly on the generation
    config - derived fresh per reference every call, never hardcoded. Briefly removed the
    same day on the theory that forcing is unreliable (measured live: a correctly-derived
    "1:1" was set explicitly and still came back 1.79:1) - true, but incomplete: measured
    immediately after removing it, the SAME reference produced a close match on one run
    (0.5581 vs a true 0.5625) and a badly wrong one on another (0.322 vs the same 0.5625)
    with aspect_ratio omitted both times. Omitting isn't just imprecise, it's
    nondeterministic for identical input - reinstated as the safer default: the
    attached reference still constrains output most of the time when explicit, versus
    omission leaving shape to chance entirely. Missing/unreadable bytes still fall back to
    omitting aspect_ratio (with a pipeline_warning) rather than forcing a guessed "1:1" -
    that specific fallback was never in question. Generate mode is unaffected either way -
    it never set an explicit aspect_ratio on the config, only the prompt-text "Square 1:1"
    line - there is no attached reference there to derive one from. image_size is a
    separate knob and is always set, both modes.

    Illustrated register (2026-08-06, Grüns GLP-1 leak): reference_images (the product's
    OWN photos) are dropped entirely - never attached to Gemini at all - whenever the
    effective style resolves to "illustrated", the SAME resolution build_image_prompt's
    edit_mode branch uses (realism override, else the blueprint's own detected
    production_style). Passing a photographic reference while simultaneously demanding
    faithful substitution is what produced a photorealistic bottle composited into an
    otherwise hand-drawn scene - dropping the photo and describing the bottle in the
    scene's own visual language instead (see _edit_mode_instruction's style=="illustrated"
    branch) fixes it structurally rather than with more prompt wording. Applies in every
    mode, not just edit_mode - reference_images are attached the same way regardless."""
    effective_style = (realism or "").strip() or (blueprint.get("production_style") or {}).get("style", "")
    if effective_style == "illustrated":
        reference_images = []
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
                                 realism=realism, critic_feedback=critic_feedback, cta_text=cta_text,
                                 panel_copy=panel_copy, testimonial=testimonial,
                                 product_count=product_count, clone_mode=clone_mode)
    stem = _draft_stem(ad_id, angle_slug)
    # OCCLUDE_PERSON (Item 1, 2026-08-12): applied to the IN-MEMORY bytes only, right
    # before they're attached to Gemini - never to the on-disk image_path fetch, which
    # is a separate read (assets.download_image, called from pipeline.py) this function
    # never touches. Deliberately BEFORE build_image_prompt is called above would also
    # have worked (occlusion doesn't affect any text the prompt builder produces), but
    # placing it here, right next to Part.from_bytes, keeps the "these bytes are what
    # Gemini sees" concern in one place rather than splitting it across the function.
    occluded_this_call = False
    if edit_mode and competitor_image_bytes and _occlusion_enabled():
        competitor_image_bytes, occluded_this_call = _occlude_person_region(
            competitor_image_bytes, blueprint.get("face_present")
        )
        if occluded_this_call:
            log.info("Ad %s: OCCLUDE_PERSON active - person region blocked before "
                     "attaching the reference image to Gemini", ad_id)
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
                    "brand name, and any person's actual likeness must NOT survive - see "
                    "the instructions below. "
                )
                if occluded_this_call:
                    framing += (
                        "OCCLUSION NOTICE (STRICT): a solid grey rectangle in this "
                        "attached image is a DELIBERATE BLOCK placed over the "
                        "reference's original human subject before this image was ever "
                        "sent to you - it is NOT a graphic element, badge, panel, or "
                        "shape that belongs to the original ad, and it must never be "
                        "reproduced, outlined, or left in the output as a grey block, "
                        "box, or panel. Fill that region with a NEW, generic "
                        "Besque-appropriate subject (per rule 10 above: 45-60, "
                        "age-appropriate skin) whose pose, framing, and lighting fit "
                        "naturally into the surrounding composition - never a shape "
                        "standing in for a person. "
                    )
            if reference_images:
                image_parts += [genai_types.Part.from_bytes(data=img, mime_type="image/png")
                                for img in reference_images]
                framing += _reference_framing(len(reference_images))
            contents = image_parts + [framing + prompt]
        else:
            contents = prompt

        if edit_mode:
            # Item 6a (2026-08-04), REINSTATED 2026-08-07 (a same-day removal in e27b9eb
            # was itself wrong for this case - see below). derive_aspect_ratio picks the
            # nearest supported bucket to the reference's OWN width:height, per-reference,
            # never hardcoded to any ad/shape.
            #
            # History: e27b9eb removed this, reasoning that a reference measured live had a
            # correctly-derived "1:1" explicitly set and still came back 1.79:1 - so forcing
            # was "unreliable even when derived correctly." True, but incomplete: measured
            # immediately after removing it, a SEPARATE reference (ratio 0.5625) produced
            # 0.5581 on one run and 0.322 on another, with aspect_ratio omitted both times -
            # omitting is not just imprecise, it is NONDETERMINISTIC, sometimes inferring
            # correctly and sometimes not at all, for the identical input. Explicit forcing
            # has one documented failure; omitting has both a success and a dramatic failure
            # on the exact same input. Between "usually constrains, sometimes fails" and
            # "sometimes infers, sometimes doesn't even try," the former is the safer
            # default specifically because the attached reference image still constrains it
            # most of the time - omission removes the constraint entirely and leaves shape
            # to chance.
            aspect_ratio = derive_aspect_ratio(competitor_image_bytes)
            if aspect_ratio is None:
                from src import dedupe as _dedupe
                _dedupe.init_pipeline_warnings()
                _dedupe.record_warning(
                    "edit_mode_aspect_ratio_fallback",
                    f"ad_id={ad_id}: could not derive aspect ratio from the reference "
                    f"image (missing or unreadable); omitting aspect_ratio so the model "
                    f"infers it from the attached reference image itself - image_size is "
                    f"still set, that's an independent knob.",
                )
                generation_config = genai_types.GenerateContentConfig(
                    image_config=genai_types.ImageConfig(image_size=IMAGE_SIZE)
                )
            else:
                generation_config = genai_types.GenerateContentConfig(
                    image_config=genai_types.ImageConfig(aspect_ratio=aspect_ratio, image_size=IMAGE_SIZE)
                )
        else:
            # Generate mode is UNCHANGED and NOT covered by the evidence above - there is
            # no attached reference image here to constrain shape, so image_size=2K alone
            # gives the model nothing to anchor an aspect ratio to; Meta's 1080x1350
            # minimum makes an arbitrary shape a real risk here, not just cosmetic.
            # Generate mode has never set an explicit aspect_ratio on this config (only
            # the prompt-text "Square 1:1" line, unchanged) - left exactly as it was,
            # pending its own generate-mode-specific probe before deciding whether to
            # force one or keep relying on prompt text alone.
            generation_config = genai_types.GenerateContentConfig(
                image_config=genai_types.ImageConfig(image_size=IMAGE_SIZE)
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
                image_config=genai_types.ImageConfig(aspect_ratio=aspect, image_size=IMAGE_SIZE),
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
    """2026-08-06: no longer claims the prompt above is "the EXACT prompt that produced
    the attached image" - since pipeline._regenerate_existing_draft now REBUILDS that
    prompt from current code and the artifact's stored inputs rather than replaying the
    historical prompt verbatim, that claim would usually be false (the attached image was
    produced by the OLD prompt; the one above it is a fresh rebuild). The instruction is
    still a targeted delta on top of everything else stated above."""
    return (
        " REGENERATE: the prompt above describes this image's current composition and "
        "rules - apply ONLY the following instruction as a targeted change to it; every "
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
        # Framing row, REINSTATED 2026-08-07 - see generate_image's own comment for the
        # full evidence (the same reference produced 0.5581 on one run, 0.322 on another,
        # with aspect_ratio omitted - omitting is nondeterministic, not just imprecise).
        # derive_aspect_ratio derives per-reference from current_image_bytes, never
        # hardcoded to any ad/shape.
        aspect_ratio = derive_aspect_ratio(current_image_bytes)
        if aspect_ratio is not None:
            image_config = genai_types.ImageConfig(aspect_ratio=aspect_ratio, image_size=IMAGE_SIZE)
        else:
            image_config = genai_types.ImageConfig(image_size=IMAGE_SIZE)
        generation_config = genai_types.GenerateContentConfig(image_config=image_config)
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
