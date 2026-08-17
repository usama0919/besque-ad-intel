"""Regeneration step (image prompt): turn a blueprint's visual into an image-gen prompt."""
import io
import logging
import math
import os
import re
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageStat
from src import assets, deconstruct, generate_image_prompt_writer
from src.compliance_rules import COMPLIANCE_RULES

IMAGE_MODEL = os.getenv("IMAGE_MODEL", "placeholder-image-model")

log = logging.getLogger("generate_image_prompt")


# _competitor_props_clause / PROP_KEYWORDS DELETED 2026-08-17: this used to scan
# product_category.signals/visual.subject for a prop tied to the competitor's own
# product category (an applicator diagram, an anatomical inset) and emit its own
# separate removal instruction - a second, uncoordinated mechanism deciding the same
# object's fate that resolve_disposition already owns per-object (a real prop is now
# always its own `objects` row, with a real ownership/disposition). Folded into
# deconstruct.resolve_disposition instead (see _is_competitor_argument_prop there) -
# one mechanism now decides every object's fate, never two that could disagree about
# the same prop. See the handover report for this session for why this was deleted
# rather than repointed at objects: the objects model already covers this case more
# precisely (a real bounding box, a real ownership judgement) than a keyword scan over
# two loosely-related free-text fields ever did.


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


def resolve_authorised_product_count(objects):
    """The number of Besque bottles this scene authorises, computed in CODE from the
    objects inventory - never left for a prose instruction to arbitrate (2026-08-18,
    the OSEA two-bottle failure: rule 7's unconditional "exactly one" and two
    independently-substituting SCENE OBJECTS product bullets disagreed in the same
    prompt, and the model resolved the contradiction by substituting neither).

    Defers entirely to deconstruct.resolve_product_group_dispositions for the actual
    grouping/winner decision (same product differing only in size/format = every
    instance substitutes; genuinely different products = exactly one substitutes,
    the rest drop) - this function is just "how many of those survived as
    substitute." Every clause that mentions bottle count - rule 7
    (_rule7_product_policy), _edit_mode_instruction, product_clause's >1 branch -
    must read THIS SAME value, so they can never disagree with each other again.

    Returns None when there are no competitor-branded/brand-marked product objects
    to reason about at all (a legacy blueprint predating the objects model, or a
    genuinely productless reference) - callers fall back to the pre-existing
    resolve_product_count(reference_count=layout_detail.product_count, ...) path in
    that case, so nothing regresses for a blueprint this mechanism doesn't cover."""
    dispositions = deconstruct.resolve_product_group_dispositions(objects)
    if not dispositions:
        return None
    return sum(1 for v in dispositions.values() if v == "substitute")


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
    detection reads this exact derivation, never a re-implementation of it.

    REWIRED 2026-08-17: structural_zones no longer exists (schema/blueprint.schema.json -
    blueprint.objects replaces it, see _objects_clause). A "text-bearing zone" is now any
    kind=="text" object whose text_purpose is headline/subtext/cta - the same three
    purposes the deleted structural_zones' sub_line/body_copy/cta zone_types used to mean,
    named more precisely now that text_purpose classifies by JOB rather than a generic
    sub_line/body_copy split."""
    blueprint = blueprint or {}
    if (blueprint.get("headline_verbatim") or "").strip():
        return True
    return _objects_have_text_purpose(blueprint.get("objects"), _TEXT_PURPOSE_ZONE_TYPES)


# _TEXT_PURPOSE_ZONE_TYPES/_TEXT_PURPOSE_OFFER_TYPES/_objects_have_text_purpose
# (2026-08-17): the objects-array replacement for the deleted _structural_zones_have_offer/
# OFFER_BADGE_KEYWORDS keyword-matching - text_purpose already classifies a text object as
# "offer"/"price_anchor" mechanically (see deconstruct.py's BLUEPRINT_PROMPT), so there is
# no longer any free-text detail to guess an offer shape from; the classification IS the
# signal, not a keyword proxy for it.
_TEXT_PURPOSE_ZONE_TYPES = ("headline", "subtext", "cta")
_TEXT_PURPOSE_OFFER_TYPES = ("offer", "price_anchor")


def _objects_have_text_purpose(objects, purposes):
    return any(
        (obj or {}).get("kind") == "text" and (obj or {}).get("text_purpose") in purposes
        for obj in (objects or [])
    )


def reference_has_offer_zone(blueprint):
    """True when the reference blueprint shows a text object that actually CARRIES an
    offer - text_purpose "offer" or "price_anchor" (see deconstruct.py's BLUEPRINT_PROMPT
    for what each means).

    Added 2026-08-11 for clone mode (pipeline.py) - deciding whether THIS ad's per-ad
    config should carry the operator's offer_text at all, before build_image_prompt ever
    runs. Same public/no-leading-underscore convention as reference_has_product/
    reference_has_text_zone above - pipeline.py reads this directly, no reimplementation.

    REWIRED 2026-08-17: structural_zones/_structural_zones_have_offer/
    OFFER_BADGE_KEYWORDS no longer exist - see _objects_have_text_purpose above."""
    return _objects_have_text_purpose((blueprint or {}).get("objects"), _TEXT_PURPOSE_OFFER_TYPES)


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
                        clone_mode: bool = False, object_copy: list = None,
                        suppress_bottle_identity: bool = False) -> str:
    """Construct a Besque-adapted image generation prompt from the blueprint's visual notes.

    suppress_bottle_identity (2026-08-17, Route B compositing): True when generate_image's
    own _composite_gate has ALREADY decided this run will paste the real product cutout
    in after Gemini returns, rather than let Gemini draw the bottle. Drops
    _bottle_geometry_clause/_bottle_identity_clause (and, in edit mode only,
    _bottle_geometry_source_clause, which otherwise dangles a reference to "the BOTTLE
    GEOMETRY clause above" that would no longer exist in this same prompt) from every
    branch below - asking Gemini to draw a detailed, identity-correct bottle in the
    exact region a real cutout is about to be pasted over is pure waste at best, and a
    visible double-bottle/conflicting-render risk at worst. _bottle_integration_clause
    is NOT suppressed - it governs scene composition (grounding, scale, no text
    overlap) around wherever the product goes, which still matters whether that space
    ends up drawn by Gemini or pasted in afterward. False (the default) reproduces
    today's prompt byte-for-byte - no caller that doesn't know about compositing sees
    any change.
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
    # REWIRED 2026-08-17: visual.scene_lighting (six sub-fields) no longer exists
    # (schema/blueprint.schema.json - the objects-array refactor collapsed it, together
    # with layout_detail.background_type, into one top-level `background` object:
    # {"surface", "colour", "light"}). `background.light` is the sole surviving lighting
    # fact - one free-text phrase, not six structured sub-fields - see
    # _scene_lighting_facts/_bottle_register_clause for what that loses.
    background = blueprint.get("background") or {}
    prod_style = (blueprint.get("production_style") or {}).get("style", "")
    # Computed once, reused everywhere "which register is this" is needed (the three
    # style= call sites below, and _illustrated_elements_clause) - the same operator-
    # override-else-reference-observed precedence, never duplicated as three separate
    # inline expressions that could drift.
    resolved_style = (realism or "").strip() or prod_style

    layout_detail_bp = blueprint.get("layout_detail") or {}
    effective_include_product, reference_has_product = resolve_effective_include_product(
        blueprint, include_product, edit_mode
    )
    # reference_has_text_zone (2026-08-07, reference usability gate reversal): the
    # text-side analogue of reference_has_product, only meaningful in edit_mode (outside
    # it there's no reference zone to substitute into or add alongside at all - generate
    # mode always "adds" from scratch, see build_image_prompt's own docstring).
    ref_has_text_zone = reference_has_text_zone(blueprint) if edit_mode else True
    # 2026-08-18 (three-voices product-count fix): the code-computed, per-object-aware
    # count (resolve_authorised_product_count, deferring to deconstruct.resolve_
    # product_group_dispositions) REPLACES the bare layout_detail.product_count number
    # as the "reference" tier whenever there are competitor-branded/brand-marked
    # product objects to reason about - resolve_product_count's own operator>reference>
    # default precedence is otherwise unchanged, so an explicit per-run product_count
    # override still wins outright. Falls back to the bare number (old behaviour,
    # unchanged) only for a blueprint resolve_authorised_product_count can't reason
    # about at all - a legacy row predating the objects model, or a genuinely
    # productless reference.
    authorised_from_objects = resolve_authorised_product_count(blueprint.get("objects"))
    reference_product_count = (
        authorised_from_objects if authorised_from_objects is not None
        else layout_detail_bp.get("product_count")
    )
    resolved_product_count, product_count_source = resolve_product_count(
        reference_product_count, product_count
    )
    if product_count_source == "reference" and authorised_from_objects is not None:
        product_count_source = "objects"
    # rule_authorised_product_count (2026-08-18): what rule 7 (_rule7_product_policy,
    # via brand_rules) and _edit_mode_instruction are told is authorised - ONLY the
    # "objects" source (deconstruct.resolve_product_group_dispositions actually
    # confirmed a same_product_as chain) ever gets to state >1 here; every other
    # source (operator override, the bare layout_detail number, or the default) has
    # no evidence about same-vs-different products, so both of those clauses see 1,
    # exactly the pre-existing 2026-08-12 "collapse to one" behaviour for anything
    # this fix doesn't cover. Kept as a SEPARATE variable from resolved_product_count
    # (which product_clause's own >1 branch still reads directly, gating on
    # product_count_source itself) so there is one unambiguous value each clause
    # reads for "how many bottles may I claim are authorised."
    rule_authorised_product_count = (
        resolved_product_count if product_count_source == "objects" else 1
    )

    if effective_include_product:
        if product:
            # visual_description is deliberately NOT restated here (2026-08-13) -
            # _bottle_identity_clause already states it, earlier, with STRICT/rule-level
            # weight; repeating it here was the exact "identity described exactly once,
            # generically" gap that clause was built to fix, not a second, redundant
            # confirmation worth keeping.
            hero_claim = (product.get("hero_claim") or "").strip()
            product_desc = (
                f"The featured product is {product.get('name', 'a Besque product')}: {product.get('description', '')} "
                f"These are the ONLY real ingredients allowed to appear if the product's OWN "
                f"printed label is legible in the shot: {product.get('ingredients', '')}. This "
                f"ingredient list exists SOLELY to constrain what the product's own label may "
                f"say - it is not a list of scene elements. NEVER render any ingredient name as "
                f"a separate floating callout, badge, sticker, or piece of scene text anywhere "
                f"else in the image, even if the reference ad's own layout used ingredient "
                f"callouts. "
                # Key claim omitted entirely when unset (2026-08-13) - hero_claim is
                # currently blank pending Harry's real approved_claims; rendering "Key
                # claim: . " asserted an empty statement rather than saying nothing.
                + (f"Key claim: {hero_claim}. " if hero_claim else "")
                + f"Never invent ingredients or label text not listed here. "
            )
        else:
            product_desc = "(a natural botanical body oil in an elegant bottle). "
        # Style-aware (2026-08-14) - see _BOTTLE_MATERIAL_REALISM_CLAUSE_ILLUSTRATED's
        # own comment for why: the photoreal clause's meniscus/refraction/specular-
        # highlight language directly contradicts an illustrated register's own
        # "never photorealistic" instruction elsewhere in the same prompt.
        product_desc += (
            _BOTTLE_MATERIAL_REALISM_CLAUSE_ILLUSTRATED if resolved_style == "illustrated"
            else _BOTTLE_MATERIAL_REALISM_CLAUSE
        )
        # Skip only the PLACEMENT sentence below (never product_desc above - the
        # identity/ingredients/material-realism facts Gemini needs regardless of
        # add-vs-substitute) when edit mode's own substitution already placed the
        # product into an existing reference zone - see _edit_mode_instruction's
        # reference_has_product SUBSTITUTE branches, which already state their own
        # placement/composition instructions (including their own "account for more
        # than one distinct product" handling). Fail-closed like the product edit
        # control's element_provenance predicate (edit_capability._product_control): a
        # positive signal the product is ALREADY accounted for suppresses only the
        # placement instruction, never the description. Live evidence (ad
        # 2390171264812593): an illustrated reference's correctly-substituted bottle
        # (in a character's hand) was joined by a SECOND, standalone photorealistic
        # bottle composited at the bottom of the frame from this placement sentence -
        # product_clause was built purely from effective_include_product (intent), with
        # no awareness that a product had already been placed by substitution.
        already_placed_by_substitution = edit_mode and reference_has_product
        # GROUNDED (2026-08-18, three-voices product-count fix): resolved_product_count
        # is only trustworthy as "N genuine instances of one product" when it came from
        # resolve_authorised_product_count - i.e. deconstruct.resolve_product_group_
        # dispositions actually inspected the objects inventory and confirmed a
        # same_product_as chain. A count sourced from the bare layout_detail.
        # product_count number (source="reference", no objects model available) or an
        # operator override (source="operator") or the default carries NO evidence
        # about same-vs-different products - collapsing to one remains the only safe
        # default for those, exactly the pre-existing 2026-08-12 behaviour, UNCHANGED
        # here (see test_build_image_prompt_product_count_above_one_still_yields_
        # single_product_instruction, which locks this in for a legacy/no-objects
        # blueprint). Only the "objects" source gets the new >1 treatment.
        product_count_is_grounded = product_count_source == "objects"
        if resolved_product_count and resolved_product_count > 1 and product_count_is_grounded:
            # This branch states the count as a FACT, agreeing with rule 7
            # (_rule7_product_policy, same resolved_product_count) and _edit_mode_
            # instruction (same value, passed as authorised_product_count) rather than
            # contradicting them - the exact three-way disagreement this fix closes.
            # Deliberately NEVER suppressed by already_placed_by_substitution: unlike the
            # ==1 branch's placement sentence below (which duplicates edit mode's own
            # substitution and caused a real second-bottle bug), this sentence states a
            # COUNT only, never a placement instruction of its own - safe to always
            # include, and the task that introduced it explicitly requires it survive in
            # edit mode.
            log.info(
                "product_count resolved to %s (source=%s) - %d Besque bottle(s) "
                "authorised; exact position of each is governed by the SCENE OBJECTS "
                "inventory",
                resolved_product_count, product_count_source, resolved_product_count,
            )
            placement = (
                f"This scene authorises exactly {resolved_product_count} Besque bottles - "
                f"computed from the reference's own product objects (multiple instances of "
                f"the SAME product differing only in size or format), matching the "
                f"reference's own count. Render exactly that many, each the same real "
                f"Besque product at its own size/scale - never a different SKU per "
                f"instance, never fewer than this count, never more. WHICH reference "
                f"position each one occupies is governed entirely by the SCENE OBJECTS "
                f"inventory below; this sentence states the count only, never a placement "
                f"instruction of its own. "
            )
            product_clause = placement + product_desc
        elif resolved_product_count and resolved_product_count > 1:
            # UNCHANGED since 2026-08-12 (live failure, ad with product_count=5): rule 7
            # above states "exactly one bottle... NEVER add a second bottle... whether
            # copied from the competitor ad or invented" unconditionally whenever this
            # count isn't grounded in objects-model evidence (see product_count_is_
            # grounded above) - so this branch must not construct a sentence asking for
            # more than one Besque product either; there is no way to tell same-vs-
            # different product without the objects model, so collapsing remains the
            # only safe default. What DOES still vary by count is the COMPOSITION
            # instruction: the reference's multi-product layout (spacing, framing,
            # negative space sized for several items) needs to be adapted around a
            # single bottle, not reproduced with empty slots or a shrunken bottle
            # standing in for a missing set.
            log.info(
                "product_count resolved to %s (source=%s, ungrounded) - reference shows "
                "multiple products, but exactly ONE Besque product renders per rule 7; "
                "composition adapts around the single bottle instead of reproducing the "
                "count",
                resolved_product_count, product_count_source,
            )
            placement = (
                f"The reference shows {resolved_product_count} products together, but "
                f"Besque's product renders as exactly ONE bottle - rule 7 above permits "
                f"exactly one, with no exception for what the reference happens to show. "
                f"Adapt the COMPOSITION around that single bottle: keep the reference's "
                f"setting, lighting, and framing, but resize, recentre, or rebalance the "
                f"layout so the one bottle occupies the space naturally - never leave empty "
                f"slots where the reference's other products were, and never shrink the "
                f"single bottle to read like part of a missing set. Do not render the "
                f"competitor's product. "
            )
            product_clause = ("" if already_placed_by_substitution else placement) + product_desc
        else:
            placement = (
                f"Place the Besque product described below as the subject "
                f"within this setting; do not render the competitor's product. "
            )
            product_clause = ("" if already_placed_by_substitution else placement) + product_desc
    else:
        product_clause = (
            "This is a deliberately productless, educational/illustrative image - do not "
            "place any Besque product, bottle, or branding anywhere in this setting. "
        )

    # objects_context (2026-08-17): the real Besque-side values THIS run has to
    # substitute a text-purposed object with - see _objects_clause's own docstring.
    # Built once here, reused identically across all three branches below, so an offer/
    # certification/testimonial/cta object is judged against the SAME facts regardless
    # of which branch renders the prompt.
    #
    # object_copy_by_id (2026-08-17, second restoration): keyed by object_id, not the
    # object's own kind/purpose - generate_copy.text_objects_needing_copy/
    # _object_copy_clause generate ONE line per text object with no recognised
    # text_purpose ("other"/absent); this is the root-cause fix for a live failure where
    # every such object (four Instagram DM bubbles, in the reported case) received the
    # IDENTICAL generic fallback line in _substitute_object_line, because nothing
    # differentiated one object_id's substitution from another's.
    object_copy_by_id = {
        entry.get("object_id"): entry.get("text")
        for entry in (object_copy or [])
        if isinstance(entry, dict) and entry.get("object_id") and entry.get("text")
    }
    objects_context = {
        "offer_text": offer_text,
        "certifications": (product or {}).get("certifications"),
        "testimonial": testimonial,
        "cta_text": cta_text,
        "product_name": (product or {}).get("name"),
        "object_copy_by_id": object_copy_by_id,
    }

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
                        headline=headline, subtext=subtext, edit_mode=True,
                        authorised_product_count=rule_authorised_product_count) +
            # _bottle_geometry_source_clause is edit-mode-only (2026-08-15) - this is the
            # ONE branch where a competitor reference image AND Besque's own product
            # reference photos are both attached at once, so it's the only place a
            # source-attribution statement between them is meaningful. _bottle_geometry_
            # clause (2026-08-16) is the single hardcoded source of truth those source-
            # attribution facts now defer to, rather than restating shape categories.
            #
            # suppress_bottle_identity (2026-08-17): drops identity/geometry AND the
            # source-attribution clause (which would otherwise dangle a reference to
            # "the BOTTLE GEOMETRY clause above" that isn't in this prompt) - integration
            # stays, since scene composition around the product still applies either way.
            ((("" if suppress_bottle_identity else _bottle_identity_clause(product) + _bottle_geometry_clause())
              + _bottle_integration_clause(suppress_bottle_identity)
              + ("" if suppress_bottle_identity else _bottle_geometry_source_clause()))
             if effective_include_product else "") +
            _operator_instruction_clause(operator_instruction) +
            _critic_feedback_clause(critic_feedback) +
            _objects_clause(blueprint.get("objects"), objects_context, ad_id=blueprint.get("ad_id")) +
            # Per-zone typography restoration (2026-08-17) - edit-mode only, matching
            # the deleted _typography_zones_clause's own original scope exactly (the
            # reference IS the creative brief in this branch, so typographic
            # differentiation is only ever meaningful here, same as before this was
            # deleted). Never called in the other two branches below.
            _object_typography_clause(blueprint.get("objects"), objects_context) +
            _semantic_split_clause(blueprint.get("semantic_split")) +
            # include_product here is the RAW operator toggle - identical to
            # effective_include_product since the 2026-08-07 reference usability gate
            # reversal (reference_has_product no longer forces effective_include_product
            # off; see resolve_effective_include_product). reference_has_product/
            # reference_has_text_zone independently select SUBSTITUTE-vs-ADD wording per
            # element, never the boolean outcome itself.
            _edit_mode_instruction(text_in_image=text_in_image, headline=headline, subtext=subtext,
                                   offer_text=offer_text, include_product=include_product,
                                   reference_has_product=reference_has_product,
                                   reference_has_text_zone=ref_has_text_zone,
                                   layout_detail=layout_detail_bp, visual=visual,
                                   retheme_colours=retheme_colours, palette=brand_palette,
                                   substance_colour=(product or {}).get("substance_colour"),
                                   style=resolved_style,
                                   background=background,
                                   cta_text=cta_text, product_name=(product or {}).get("name"),
                                   panel_copy=panel_copy, testimonial=testimonial,
                                   certifications=(product or {}).get("certifications"),
                                   objects=blueprint.get("objects"),
                                   face_present=blueprint.get("face_present"),
                                   clone_mode=clone_mode,
                                   authorised_product_count=rule_authorised_product_count) +
            # product_clause already omits its own PLACEMENT sentence when
            # already_placed_by_substitution (edit_mode and reference_has_product) is
            # true - see where product_clause is built above - so it is always safe to
            # append here unconditionally; it still carries product_desc (identity/
            # ingredients/material-realism facts) even when the placement sentence is
            # suppressed.
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
                        headline=headline, subtext=subtext,
                        authorised_product_count=rule_authorised_product_count) +
            # suppress_bottle_identity (2026-08-17): drops identity/geometry when Route
            # B compositing will paste the real cutout in after generation - integration
            # stays, scene composition around the product still applies either way.
            ((("" if suppress_bottle_identity else _bottle_identity_clause(product) + _bottle_geometry_clause())
              + _bottle_integration_clause(suppress_bottle_identity))
             if effective_include_product else "") +
            _operator_instruction_clause(operator_instruction) +
            _critic_feedback_clause(critic_feedback) +
            _objects_clause(blueprint.get("objects"), objects_context, ad_id=blueprint.get("ad_id")) +
            _semantic_split_clause(blueprint.get("semantic_split")) +
            creative_description.strip() + " "
            + product_clause
            + _bottle_fixed_clause() + _bottle_register_clause(background, resolved_style) +
            f"Square 1:1 aspect ratio composition. " +
            closing
        )
    else:
        prompt = (
            brand_rules(include_product=include_product, text_in_image=text_in_image,
                        headline=headline, subtext=subtext,
                        authorised_product_count=rule_authorised_product_count) +
            # suppress_bottle_identity (2026-08-17): drops identity/geometry when Route
            # B compositing will paste the real cutout in after generation - integration
            # stays, scene composition around the product still applies either way.
            ((("" if suppress_bottle_identity else _bottle_identity_clause(product) + _bottle_geometry_clause())
              + _bottle_integration_clause(suppress_bottle_identity))
             if effective_include_product else "") +
            _operator_instruction_clause(operator_instruction) +
            _critic_feedback_clause(critic_feedback) +
            _objects_clause(blueprint.get("objects"), objects_context, ad_id=blueprint.get("ad_id")) +
            _semantic_split_clause(blueprint.get("semantic_split")) +
            f"A premium skincare advertisement image for Besque, a natural body-oil brand for women 40+. "
            f"Composition and setting: {layout}. (If this implies a person, render them per compliance "
            f"rule C1 - a generic, non-identifiable model, never the specific individual described - "
            f"and per rule 10 above, reading 45-60 years old with visibly age-appropriate skin.) "
            + product_clause +
            f"Palette and mood: {palette}. Text placement: {text_placement}. "
            f"Square 1:1 aspect ratio composition. "
            + generate_image_prompt_writer.STYLE_GUIDANCE.get(
                resolved_style, DEFAULT_STYLE_GUIDANCE)
            + _bottle_fixed_clause() + _bottle_register_clause(background, resolved_style) +
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


def _rule7_product_policy(include_product=True, authorised_product_count=1):
    """Rule 7, PRODUCT POLICY. Default (include_product=True, authorised_product_count=1)
    is the original wording, verbatim. include_product=False relaxes it entirely into a
    productless mode for educational/illustrative ads (e.g. glp1) - no Besque bottle,
    label, or branding at all, rather than the default's "exactly one bottle" framing.

    authorised_product_count (2026-08-18, three-voices product-count fix): the SAME
    value resolve_authorised_product_count computed from the objects inventory -
    passed in by build_image_prompt via brand_rules(), never recomputed here. This
    rule used to state "exactly one bottle... NEVER add a second" unconditionally,
    which directly contradicted a blueprint with two competitor product objects both
    genuinely marked substitute (the OSEA "You'll Wish You Went Jumbo" reference -
    two instances of the SAME product differing only in size). Rule 7 now states
    WHATEVER count was actually authorised - still exactly one bottle for the
    overwhelmingly common single-product case (byte-identical to the prior wording
    when authorised_product_count<=1, see test_rule7_default_count_matches_prior_
    wording_verbatim), but a genuine >1 for the same-product-multiple-instances case,
    agreeing with product_clause's >1 branch and _edit_mode_instruction's own stated
    count instead of forbidding what SCENE OBJECTS is simultaneously demanding."""
    if include_product and authorised_product_count and authorised_product_count > 1:
        return (
            f"7) PRODUCT POLICY (STRICT): exactly {authorised_product_count} Besque bottles "
            f"are authorised in this scene - computed from the reference's own objects "
            f"inventory (multiple instances of the SAME product differing only in size or "
            f"format), never from prose, and never overridden by anything else in this "
            f"prompt. Render exactly that many, each the SAME real Besque product at its "
            f"own size/scale matching the reference's own layout - never a different SKU "
            f"invented per instance, never fewer than this count, never more. A "
            f"competitor's own product must not survive anywhere in the image. See the "
            f"SCENE OBJECTS inventory below for exactly which position each authorised "
            f"bottle occupies. "
        )
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
    "AGE/APPEARANCE INSTRUCTION ANYWHERE IN THIS PROMPT): any human subject appearing "
    "anywhere in the output image MUST show ALL three of the following - these visible "
    "features are the PRIMARY, MANDATORY specification of age, not optional styling cues: "
    "(1) GREY OR SILVER HAIR, or hair with visible natural greying through it - never a "
    "uniform, fully-pigmented youthful brown, black, red, or blonde with no grey present "
    "at all; (2) VISIBLE FACIAL LINES around the eyes, mouth, and forehead - not smoothed "
    "away; (3) MATURE SKIN TEXTURE - some natural laxity at the jawline/neck, real tone "
    "and texture variation across the skin - never smooth, poreless, or airbrushed. A "
    "numeric age alone does not reliably produce these in the output pixels, so they are "
    "specified directly and are each individually required, not satisfied by hitting only "
    "one or two. As a SECONDARY anchor, not the headline of this rule: the subject should "
    "additionally read as 45-60 years old. Besque's audience is women 40+; anything read "
    "as under 45 - OR a subject with fully-pigmented, ungreyed hair and smooth, unlined "
    "skin regardless of the stated numeric age - is the exact failure this rule exists to "
    "catch. This rule wins over ANY other instruction in this prompt that describes "
    "matching, reproducing, or preserving the reference subject's age or appearance - "
    "including a REPRODUCE/SUBSTITUTE partition elsewhere that covers pose, framing, or "
    "skin-condition presentation: age is never one of the reproduced/matched attributes, "
    "on any path, with no exception. This independence applies SPECIFICALLY to hair "
    "colour and skin texture, not only to the numeric bracket: competitor reference ads "
    "typically show a model in their 20s-30s with full, ungreyed hair and smooth skin - "
    "even when the reference's own model has that appearance, the substituted subject's "
    "hair and skin must still show the three required features above; the reference "
    "model's own hair colour or skin smoothness is never a reason to render less grey "
    "hair, fewer lines, or smoother skin than this rule requires. "
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


def brand_rules(include_product=True, text_in_image=False, headline=None, subtext=None,
                 edit_mode=False, authorised_product_count=1):
    """The mechanically-enforced brand + compliance rules prepended to every image prompt.
    Called with all defaults (include_product=True, text_in_image=False, edit_mode=False,
    authorised_product_count=1), this reproduces the old flat BRAND_RULES constant
    character for character through rule 7 - see
    test_brand_rules_default_reproduces_prior_rules_verbatim. Rule 8, rule 9, rule 10,
    and the include_product/text_in_image/edit_mode conditionality are additive, not a
    rewrite of the existing default path.

    authorised_product_count (2026-08-18, three-voices product-count fix): forwarded
    straight to _rule7_product_policy - see that function's own docstring. build_image_
    prompt passes the SAME resolve_authorised_product_count value here as it passes to
    _edit_mode_instruction and uses in product_clause, so all three can never disagree.

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
        + _rule7_product_policy(include_product, authorised_product_count)
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


_OBJECT_CLOSURE_SENTENCE = (
    "The scene contains these objects and no others. Do not add any object, "
    "body part, hair, hand, garment or prop that is not listed above."
)


def _testimonial_styling_instruction(obj):
    """Testimonial styling restoration (2026-08-17): restores testimonial_zones[].
    styling (card shape, star-rating presence, quote-mark treatment), deleted by
    6b82f60 with no replacement - content was restored in a9b1e9f
    (_substitute_object_line's testimonial branch, above), styling was not, so a
    correctly-substituted testimonial rendered in a generic container instead of
    matching the reference's own card/rating treatment.

    Recovered from git history (6b82f60~1:src/generate_image_prompt.py's
    testimonial_zones styling_instruction, including the account-chrome carve-out
    added 2026-08-13 for rule C9) rather than reinvented - the carve-out is NOT
    optional to drop: "match this reference's own styling for the card" previously
    read as license to reproduce the reference's own avatar/handle/display name
    verbatim, a real live leak this exact wording closed. Dropped the old
    "Position it at {placement}" sub-sentence - the objects model already gives this
    object its own bbox, so a second, separately-worded position statement would be
    the same "closer-instruction contradicts an earlier one" shape this codebase has
    hit repeatedly, not a restoration worth reintroducing.

    Returns "" when obj has no `styling` (a pre-existing blueprint predating this
    field, or a testimonial object the model didn't classify) - byte-identical
    output to before this field existed, same additive convention as every other
    field added to the objects model this session."""
    styling = (obj or {}).get("styling")
    if not styling:
        return ""
    return (
        f" Match this reference's own styling for the card/container itself (never "
        f"its wording, which comes only from the real review above): {styling} This "
        f"styling covers LAYOUT ONLY (card shape, avatar placement, handle text "
        f"position, verified tick, follow button) - it is NEVER license to reproduce "
        f"WHOSE account this is. If the reference shows an avatar, @handle, "
        f"username, or display name, that account identity is never the reference's "
        f"own: render Besque's own account identity if one is supplied above, or "
        f"remove it entirely (compliance rule C9) - never the competitor's or any "
        f"other real account's identity. Any face rendered inside that avatar is a "
        f"depicted person like any other in this image, never decorative UI "
        f"furniture exempt from anything: it is bound by compliance rule C1 (never a "
        f"real individual's likeness, always a generic non-identifiable stand-in) "
        f"and rule 10 (must read 45-60, never younger) exactly the same as this ad's "
        f"primary subject."
    )


def _object_typography_clause(objects, context=None):
    """Per-zone typography restoration (2026-08-17): restores the deleted top-level
    typography_zones array and its consumer _typography_zones_clause (both GONE,
    deleted by 6b82f60 with no replacement), reimplemented on the objects model per
    the operator's explicit instruction - text objects already carry bbox and
    text_purpose, so per-object `typography` (schema/blueprint.schema.json) replaces
    a separate array matched by a free-text position string.

    Recovered framing from git history (6b82f60~1) almost verbatim - only the
    per-zone label changed (an object's own `description`, not a position-matched
    `zone` string) and the listed set now comes from actual object disposition
    rather than being unfiltered: only objects that will actually appear in the
    output (disposition "substitute" or "keep") are listed - dressing an object
    that's being removed is meaningless, and the old code's own closing sentence
    ("for whichever zones survive") already claimed this filtering without actually
    doing it; this restoration does what that sentence said.

    Returns "" when no kind=="text" object carries a `typography` field - byte-
    identical output for every blueprint predating this field, same as every other
    additive field this session."""
    context = context or {}
    lines = []
    for obj in objects or []:
        obj = obj or {}
        if obj.get("kind") != "text":
            continue
        # Resolved fresh via deconstruct.resolve_disposition, never the raw stored
        # field - a context-gated purpose (offer/price_anchor/certification/
        # testimonial) can be stored "drop" at deconstruct time and still resolve
        # "substitute" here with this run's real context, same dual-resolution
        # discipline every other per-object caller in this module already follows.
        if deconstruct.resolve_disposition(obj, context) == "drop":
            continue
        typography = obj.get("typography")
        if typography is None:
            continue
        parts = [
            f"{typography.get('typeface_class') or '?'} typeface",
            f"{typography.get('weight') or '?'} weight",
            f"{typography.get('case') or '?'} case",
            f"{typography.get('letter_spacing') or '?'} letter-spacing",
            f"colour {typography.get('colour') or '?'}",
            f"{typography.get('size_relative') or '?'} relative to the frame",
        ]
        deco = typography.get("decorative_elements") or []
        if deco:
            parts.append("with " + ", ".join(deco))
        label = (obj.get("description") or obj.get("object_id") or "unnamed object").strip()
        line_count = typography.get("line_count")
        lines.append(
            f"- {label}: {', '.join(parts)}, "
            f"{line_count if line_count is not None else '?'} line(s)"
        )
    if not lines:
        return ""
    zone_list = " ".join(lines)
    return (
        f"TYPOGRAPHIC LEVELS (STRICT): the reference has {len(lines)} distinct "
        f"typographic level(s) below, each with its OWN treatment - reproduce every "
        f"one of them exactly as described, never collapsing two into one and never "
        f"rendering every level in the same style. Whatever wording each object "
        f"actually receives is governed by the SCENE OBJECTS inventory and rules "
        f"above - this only states HOW that object is dressed, for whichever "
        f"objects survive: {zone_list} "
    )


def _substitute_object_line(obj, kind, text_purpose, description, context, product_instance_count=1):
    """The SUBSTITUTE line for one object whose (re-)resolved disposition is
    "substitute" - dispatches on text_purpose (2026-08-17 restoration of the deleted
    _structural_zones_clause's per-zone-type rules) for a kind=="text" object with a
    recognised purpose, kind=="product" for the bottle (deferred to BOTTLE IDENTITY/
    GEOMETRY, unchanged from before this restoration), and a per-object copy lookup for
    everything else that reaches the fallback below (2026-08-17, second restoration -
    root-cause fix for a live failure: a four-Instagram-DM-bubble reference produced a
    draft where all four bubbles carried the IDENTICAL generated sentence, because this
    fallback previously supplied no concrete content at all - "replace with Besque's own
    equivalent content", nothing distinguishing one object_id from another. Every one of
    those four bubbles is a kind=="text" object with text_purpose "other" - no other
    recognised purpose reaches this fallback for a text object; a non-text kind being
    substituted (a prop/logo/graphic) still gets the original generic wording, since
    generate_copy.text_objects_needing_copy only ever generates copy for kind=="text").

    context supplies the actual Besque-side values to substitute WITH - see
    _objects_clause's own docstring for its shape. A purpose whose value is missing
    this run (no cta_text produced, e.g.) renders as an explicit removal instead of a
    substitution with nothing to put there - never an empty or invented value.
    object_copy_by_id (this object's own generated line, keyed by object_id, built by
    build_image_prompt from generate_copy_live's `object_copy` field) is looked up by
    THIS object's own object_id, never a shared value - see the fallback branch below.

    product_instance_count (2026-08-18, three-voices product-count fix): how many
    kind=="product" objects TOTAL resolved to "substitute" this call (see
    _objects_clause's own product_group_dispositions) - passed so this function can
    tell whether THIS object is the sole authorised bottle or one of several genuine
    instances of the same product. Fixes a real bug: two competitor product objects
    both marked substitute previously produced BYTE-IDENTICAL SUBSTITUTE lines with
    no bbox, no description, and no awareness of each other (the OSEA "standard vs
    jumbo" reference) - the model had no way to tell these apart and substituted
    neither. Every product line now names ITS OWN bbox/description, so two different
    positions in the objects inventory are never worded identically again."""
    if kind == "product":
        bbox = obj.get("bbox")
        position_fact = (
            f"at bbox {list(bbox)} (this object's own recorded position and scale)"
            if isinstance(bbox, (list, tuple)) and len(bbox) == 4
            else "at this object's own recorded position and scale"
        )
        if product_instance_count and product_instance_count > 1:
            return (
                f"SUBSTITUTE: this position held one instance of a competitor product "
                f"the reference shows {product_instance_count} times, differing only in "
                f"size or format (\"{description}\") - place a Besque bottle here "
                f"instead, {position_fact}. Every other instance of this same product "
                f"in this SCENE OBJECTS list substitutes too, each at its own recorded "
                f"position - together they reproduce the reference's own "
                f"{product_instance_count}-instance layout; this is not inventing an "
                f"extra bottle, it is matching what the reference itself shows. Its "
                f"identity (shape, proportions, colours, label) comes ONLY from the "
                f"BOTTLE IDENTITY and BOTTLE GEOMETRY clauses above, never from this "
                f"object's own colours or description."
            )
        return (
            f"SUBSTITUTE: this position held a competitor product (\"{description}\") - "
            f"place the Besque product here instead, {position_fact}. Its "
            "identity (shape, proportions, colours, label) comes ONLY from the "
            "BOTTLE IDENTITY and BOTTLE GEOMETRY clauses above, never from this "
            "object's own colours or description."
        )
    if text_purpose in ("offer", "price_anchor"):
        offer_text = context.get("offer_text")
        return (
            f"SUBSTITUTE: this reads as the reference's offer/price element "
            f"(\"{description}\") - replace its content with this run's authorised "
            f"offer: \"{offer_text}\" - same position, shape, and size, never a "
            f"different number, percentage, or term, and never the competitor's own "
            f"price or amount."
        )
    if text_purpose == "certification":
        cert_list = ", ".join(context.get("certifications") or [])
        return (
            f"SUBSTITUTE: this reads as a certification element (\"{description}\") - "
            f"replace its content with Besque's own real certifications: {cert_list} - "
            f"never a certification Besque doesn't actually hold."
        )
    if text_purpose == "testimonial":
        testimonial = context.get("testimonial") or {}
        quote = testimonial.get("quote", "")
        attribution = testimonial.get("attribution") or "a verified customer"
        return (
            f"SUBSTITUTE: this reads as a customer testimonial (\"{description}\") - "
            f"replace with this REAL customer review, rendered EXACTLY as given, never "
            f"reworded, shortened, or invented: \"{quote}\" — attributed to "
            f"{attribution}. No star rating, age, or timeframe unless the review text "
            f"itself states one."
            + _testimonial_styling_instruction(obj)
        )
    if text_purpose == "product_callout":
        product_name = context.get("product_name") or "Besque"
        return (
            f"SUBSTITUTE: this reads as a product callout (\"{description}\") - "
            f"replace its content with the Besque product name, \"{product_name}\" - "
            f"never a benefit claim not already authorised elsewhere in this prompt."
        )
    if text_purpose in ("headline", "subtext"):
        return (
            f"SUBSTITUTE: this is the ad's {text_purpose} text (\"{description}\") - "
            f"its exact wording is governed entirely by the TEXT POLICY (rule 6) and "
            f"the TEXT instruction elsewhere in this prompt, never restated or "
            f"reinvented here."
        )
    if text_purpose == "cta":
        cta_text = context.get("cta_text")
        if cta_text:
            return (
                f"SUBSTITUTE: this is the ad's call-to-action (\"{description}\") - "
                f"replace its label with \"{cta_text}\" - same position and shape, our "
                f"words only, never the reference's own label."
            )
        return (
            f"ABSENT: no call-to-action wording was authorised for this run - the "
            f"{description} that appeared here is REMOVED, not left as an empty "
            f"button; close the space naturally with the surrounding surface."
        )
    object_copy_text = (context.get("object_copy_by_id") or {}).get(obj.get("object_id"))
    if object_copy_text:
        return (
            f"SUBSTITUTE: replace this {kind or 'object'} "
            f"(\"{description}\") with this run's generated Besque copy for THIS "
            f"specific object: \"{object_copy_text}\" - same position, our words only, "
            f"never the reference's own wording and never a different object's text."
        )
    return (
        f"SUBSTITUTE: replace this {kind or 'object'} "
        f"(\"{description}\") with Besque's own equivalent content, in the "
        f"same position - aligned with this ad's authorised copy/offer "
        f"elsewhere in this prompt where relevant, never inventing a new claim."
    )


def _objects_clause(objects=None, context=None, ad_id=None):
    """2026-08-17: REPLACES _scene_elements_clause/_illustrated_elements_clause -
    deconstruct.py no longer produces scene_elements at all (schema/blueprint.schema.json),
    every visually distinct thing in the reference is now one entry in blueprint.objects,
    each carrying its own disposition (substitute/keep/drop) already mechanically resolved
    by deconstruct.resolve_disposition BEFORE this function ever sees it - this function
    never re-judges ownership/brand-marking itself for a non-text object, it only phrases
    whatever disposition is already on the object.

    One line per object, grouped by disposition into a single STRICT clause, followed
    UNCONDITIONALLY by _OBJECT_CLOSURE_SENTENCE verbatim, last - the direct fix for
    generation adding objects the reference never had (a floating hair wisp, extra
    faces): previously nothing in this prompt ever stated the object list was COMPLETE,
    only what to include/substitute, which left "is there anything else I could add"
    open by omission.

    context (2026-08-17, restoring the per-zone-type rules the deleted
    _structural_zones_clause used to encode) is {"offer_text", "certifications",
    "testimonial", "cta_text", "product_name", "object_copy_by_id"} - the real
    Besque-side values THIS run actually has to substitute with. None (the default) is
    treated as "nothing supplied", same as deconstruct.resolve_disposition's own
    default. object_copy_by_id (2026-08-17, second restoration) is a dict of
    {object_id: generated_text} - generate_copy.text_objects_needing_copy's per-object
    restoration for text objects with no recognised text_purpose ("other"/absent), the
    root-cause fix for a live failure where every such object received the identical
    generic fallback line - see _substitute_object_line's own docstring.

    - keep: reproduced exactly as shown, unchanged - the analogue of the old
      MUST-INCLUDE list, but for every object the resolved disposition says to keep,
      not only ones flagged essential.
    - substitute: dispatched by kind/text_purpose - see _substitute_object_line.
    - drop: named as ABSENT, not silently omitted - the object's own description is
      quoted so the model knows exactly what must not appear.

    A kind=="text" object with a real text_purpose has its disposition RE-RESOLVED here
    via deconstruct.resolve_disposition(obj, context), never trusted from the object's
    own stored `disposition` field - that field was resolved once at deconstruct time,
    with NO run-specific context (no operator has chosen an offer/testimonial for a run
    that doesn't exist yet at deconstruct time), so an offer/certification/testimonial
    purpose stored as "drop" must be re-checked against what THIS run actually
    authorised before it's trusted. A text object with no text_purpose at all (a legacy
    blueprint predating this field) falls back to its stored disposition unchanged -
    back-compat, never re-resolved against a purpose that doesn't exist.

    serves_object_id (2026-08-17, Problem 1) gets the SAME two-pass treatment, for the
    same reason: an object serving a context-gated text object (an offer badge's own
    prop stand, say) can only know that object's FINAL disposition once this run's real
    context is applied, not the context-free value deconstruct time stored. A first
    pass resolves every object exactly as the single-object case above already did;
    a second pass re-resolves ONLY objects naming a serves_object_id, feeding pass 1's
    value for whatever they serve into deconstruct.resolve_disposition's own
    served_object_disposition parameter. Single-hop only, same documented scoping limit
    as deconstruct._resolve_object_dispositions.

    Returns "" when objects is empty/absent (a legacy blueprint predating this schema,
    or a blueprint with a genuinely empty list, which schema validation should never
    actually allow through in practice) - never a fabricated object list.

    2026-08-17: this early return used to be silent - a legacy blueprint's prompt
    skipped the entire objects model (no substitute/keep/drop lines, no closure
    sentence, resolve_disposition never called) with nothing in any log naming why.
    Logged at ERROR, not warning/info: every other guardrail this codebase has (rules
    1-9, compliance C1-C9) still assembles into the prompt regardless of this return,
    so a caller reading the log has no other signal that the SCENE OBJECTS mechanism
    specifically was skipped for this call - ad_id is passed by the caller (read from
    blueprint.get("ad_id") at each of build_image_prompt's three call sites) since this
    function only ever received the bare objects array before this, never the whole
    blueprint or an id to name in a log line. None (a caller that doesn't pass it, e.g.
    an existing test) still logs, just without naming which ad."""
    objects = objects or []
    if not objects:
        log.error(
            "Ad %s: SCENE OBJECTS clause skipped - blueprint has no 'objects' key "
            "(or an empty one). The objects model (substitute/keep/drop per element, "
            "the closure sentence, resolve_disposition) did not reach this prompt.",
            ad_id,
        )
        return ""
    context = context or {}
    # product_group_dispositions (2026-08-18, three-voices product-count fix): resolved
    # FRESH here, every call, never trusted from the stored per-object `disposition`
    # field - see deconstruct.resolve_product_group_dispositions's own docstring for why
    # (same-product-multiple-instances vs genuinely-different-products needs the WHOLE
    # objects list at once, which a per-object stored value can never encode). Overrides
    # first_pass's answer for exactly the kind=="product" objects it covers; every other
    # object (including a product not competitor-branded/brand-marked, the rare case
    # this doesn't cover) keeps its existing resolution path, unchanged.
    product_group_dispositions = deconstruct.resolve_product_group_dispositions(objects)
    product_instance_count = sum(1 for v in product_group_dispositions.values() if v == "substitute")
    # testimonial_dispositions (2026-08-19, duplicate-testimonial guard restoration):
    # same reasoning as product_group_dispositions immediately above - resolved FRESH
    # here, every call, coordinating across the WHOLE objects list so at most one
    # testimonial-purposed object ever resolves to "substitute" - see deconstruct.
    # resolve_testimonial_dispositions's own docstring for the live bug this fixes
    # (the identical review rendering in two boxes).
    testimonial_dispositions = deconstruct.resolve_testimonial_dispositions(objects, context)
    first_pass = {}
    for obj in objects:
        obj = obj or {}
        kind = obj.get("kind")
        object_id = obj.get("object_id")
        text_purpose = obj.get("text_purpose") if kind == "text" else None
        if kind == "product" and object_id in product_group_dispositions:
            first_pass[object_id] = product_group_dispositions[object_id]
        elif text_purpose == "testimonial" and object_id in testimonial_dispositions:
            first_pass[object_id] = testimonial_dispositions[object_id]
        elif text_purpose:
            first_pass[object_id] = deconstruct.resolve_disposition(obj, context)
        else:
            first_pass[object_id] = obj.get("disposition")
    lines = []
    for obj in objects:
        obj = obj or {}
        kind = obj.get("kind")
        text_purpose = obj.get("text_purpose") if kind == "text" else None
        served_id = obj.get("serves_object_id")
        object_id = obj.get("object_id")
        if kind == "product" and object_id in product_group_dispositions:
            disposition = product_group_dispositions[object_id]
        elif text_purpose == "testimonial" and object_id in testimonial_dispositions:
            disposition = testimonial_dispositions[object_id]
        elif served_id:
            disposition = deconstruct.resolve_disposition(
                obj, context, served_object_disposition=first_pass.get(served_id))
        else:
            disposition = first_pass.get(object_id)
        description = (obj.get("description") or obj.get("object_id") or "object").strip()
        role = obj.get("role") or ""
        if disposition == "keep":
            lines.append(f"KEEP: {description} ({role}) - reproduce exactly as shown, unchanged.")
        elif disposition == "substitute":
            lines.append(_substitute_object_line(
                obj, kind, text_purpose, description, context,
                product_instance_count=product_instance_count if kind == "product" else 1,
            ))
        elif disposition == "drop":
            lines.append(
                f"ABSENT: the {description} that appeared here is REMOVED - it must not "
                f"appear anywhere in the output; close the space naturally with the "
                f"surrounding surface and lighting."
            )
    if not lines:
        log.error(
            "Ad %s: SCENE OBJECTS clause skipped - blueprint has a non-empty 'objects' "
            "list but every entry resolved to a disposition other than keep/substitute/"
            "drop (or resolve_disposition returned something unexpected). No object line "
            "was built for any of %d object(s); the closure sentence was also dropped.",
            ad_id, len(objects),
        )
        return ""
    bullets = " ".join(f"({i}) {line}" for i, line in enumerate(lines, start=1))
    return (
        f"SCENE OBJECTS (STRICT, COMPLETE INVENTORY): {bullets} {_OBJECT_CLOSURE_SENTENCE} "
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
#
# SMALL-SCALE LABEL LEGIBILITY (2026-08-13): added, not restated - checked first
# whether the wrap-fidelity sentence below ("legible, without warping...") already
# covered this, and it doesn't. That sentence is about GEOMETRY (does the label read
# as wrapped onto the glass vs a flat decal); the live bug is about INFORMATION DENSITY
# at small render size (a hero-sized bottle's label is correct, the same label shrunk
# to a small in-scene bottle garbles) - a label that wraps perfectly can still garble
# if it's asked to render the full wordmark+certs+fine print in too few pixels. No
# existing instruction addressed render-size at all, so this isn't a contradiction to
# remove, it's a genuine gap. Deliberately scoped as a SIZE-DRIVEN RENDERING
# simplification, never a content change, so it does not compete with
# _bottle_fixed_clause's "label text/layout are FIXED" - that governs what the real
# label IS; this governs what's legible to actually render at a given size, and says so
# explicitly to head off exactly that reading.
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
    "over a curved surface. When the bottle occupies only a small fraction of the "
    "frame, simplify what the label shows to what will actually render legibly at "
    "that size - the BESQUE wordmark and the product name at minimum, dropping "
    "certification icons, border/rule detail, and fine print below the size where "
    "they would legibly render. This is a size-driven simplification of what is SHOWN "
    "on a small render, never an invented label or a different one, and never a "
    "change to what the label actually contains - the full label (wordmark, name, "
    "certifications, fine print) still applies whenever the bottle is rendered large "
    "enough to show it legibly. "
)

# Illustrated-register equivalent (2026-08-14): _BOTTLE_MATERIAL_REALISM_CLAUSE above
# demands photorealistic physical properties - a meniscus, glass refraction, specular
# highlights - unconditionally, in every style, which directly contradicts
# _edit_mode_instruction's own illustrated branch ("draw the Besque product NATIVELY...
# never a photograph or photorealistic render composited into the drawing"). Same
# prompt, opposite instructions about the same bottle - same contradiction class as the
# 12 Aug findings and the product/background/lighting preservation-list fix earlier the
# same day. Identity facts (label content, colours, proportions - stated once, in
# _bottle_identity_clause, explicitly style-invariant) are UNCHANGED by this split; only
# HOW those facts render changes here - flat fill instead of a meniscus, flat colour
# instead of refraction, flat shading instead of specular highlights, matching the
# surrounding artwork's own line weight rather than photographic material behaviour.
# The small-scale label simplification and "never a content change" statements are kept
# verbatim from the photoreal clause - simplification-at-small-size and content-fixed
# are style-independent facts, not photorealism-specific ones.
_BOTTLE_MATERIAL_REALISM_CLAUSE_ILLUSTRATED = (
    "The oil inside the bottle renders FLAT, in this scene's own illustrated visual "
    "language: a single flat fill colour (or the scene's own flat/cel-shading "
    "convention) suggesting the oil's level and hue - never a photorealistic meniscus, "
    "never translucency or light passing through the liquid, never a rendered surface "
    "highlight on the liquid itself; those are photographic-register effects and do not "
    "belong in a hand-drawn scene. The glass and any pump/cap hardware render as FLAT "
    "SHAPES matching the surrounding artwork's own line weight and shading - never "
    "glass refraction, never specular highlights, never reflective material rendering; "
    "a flat, drawn bottle is CORRECT here, not a failure to fix. The label wraps the "
    "bottle's own drawn silhouette legibly, in the scene's own line/colour treatment - "
    "never a flat decal floating disconnected from the bottle's shape, and never "
    "rendered with photographic material properties. When the bottle occupies only a "
    "small fraction of the frame, simplify what the label shows to what will actually "
    "render legibly at that size - the BESQUE wordmark and the product name at "
    "minimum, dropping certification icons, border/rule detail, and fine print below "
    "the size where they would legibly render. This is a size-driven simplification of "
    "what is SHOWN on a small render, never an invented label or a different one, and "
    "never a change to what the label actually contains - the full label (wordmark, "
    "name, certifications, fine print) still applies whenever the bottle is rendered "
    "large enough to show it legibly. "
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


# OFFER_BADGE_KEYWORDS/_OFFER_BADGE_PATTERN/_is_offer_shaped_zone DELETED 2026-08-17:
# this keyword-matching over a zone's free-text `detail` existed only to GUESS whether a
# structural_zones badge was offer-shaped, for _structural_zones_have_offer/
# reference_has_offer_zone above. text_purpose now classifies a text object as "offer"/
# "price_anchor" directly at deconstruct time (see BLUEPRINT_PROMPT), so there is no
# longer any free text to guess a shape from - _objects_have_text_purpose above reads the
# classification itself. _is_award_shaped_badge/_is_cert_shaped_badge/_is_stat_shaped_zone
# and the per-zone-type badge substitution logic that used to consume this alongside them
# were deleted 2026-08-17 with _structural_zones_clause (schema/blueprint.schema.json no
# longer has structural_zones at all; blueprint.objects replaces it, see _objects_clause).

# _structural_zones_clause and _typography_zones_clause DELETED 2026-08-17: their sole
# input fields (structural_zones, typography_zones) no longer exist in
# schema/blueprint.schema.json - blueprint.objects replaces both (see _objects_clause
# above). The zone-type-specific substitution business logic they carried (offer/badge/
# certification/price/testimonial/disclaimer, each substituted or removed by its own
# rule, reproduced with a real customer review or Besque's own real certifications) has
# NO equivalent in the objects model - a real, deliberate loss of capability, not an
# oversight. See the handover report for this session.

def _bottle_fixed_clause():
    return (
        "The Besque bottle's geometry, proportions, and label text/layout are FIXED - "
        "never subject to re-theming, style adaptation, or creative variation, and never "
        "changed unless the operator's instruction explicitly names the bottle. "
    )


PRODUCT_CUTOUT_GCS_KEY = "product_assets/besque_magic_body_oil_cutout.png"

# Fetched at most ONCE per process (the file is a static asset, never changes at
# runtime) - cached even on failure, never retried within the same process. Without
# this, a stretch of expired ADC/network trouble costs a real multi-second GCS round
# trip (or auth failure) on EVERY single generate_image call, not just the first -
# measured live at ~4-5s per attempt. This mirrors the existing operational practice
# for ADC issues in this codebase (CLAUDE.md: "Re-auth *and* restart") - a stale
# None cached here clears on the next process restart, same as any other ADC problem.
_product_cutout_cache_populated = False
_product_cutout_bytes_cache = None


def _fetch_product_cutout_bytes():
    """Fetch the Besque Magic Body Oil product cutout (a background-removed packshot)
    from the asset bucket - fails OPEN, returning None on any error (missing blob, auth
    failure, network), the same non-fatal contract pipeline.fetch_reference_images'
    own per-key try/except already uses for the product's other reference photos. An
    optional extra reference image is never worth failing an otherwise-working
    generation over. Attached only on the generate path (generate_image), never the
    realism-only targeted edit path, and only for non-illustrated runs - see
    generate_image's own gating. Result cached process-wide - see the cache
    variables' own comment above for why."""
    global _product_cutout_cache_populated, _product_cutout_bytes_cache
    if _product_cutout_cache_populated:
        return _product_cutout_bytes_cache
    try:
        from google.cloud import storage as _storage
        blob = _storage.Client().bucket(assets.asset_bucket_name()).blob(PRODUCT_CUTOUT_GCS_KEY)
        _product_cutout_bytes_cache = blob.download_as_bytes() if blob.exists() else None
    except Exception as e:
        log.warning("could not fetch product cutout %s: %s", PRODUCT_CUTOUT_GCS_KEY, e)
        _product_cutout_bytes_cache = None
    finally:
        _product_cutout_cache_populated = True
    return _product_cutout_bytes_cache


# ---- Route B compositing (2026-08-17): paste the REAL product cutout into the
# generated draft instead of asking Gemini to draw the bottle, for the narrow set of
# placements Pillow can do convincingly. Confirmed root cause (the realism diagnostic):
# every bottle-description clause is already textually invariant, and Gemini still
# rendered a wrong pump colour, an invented label, and a wrong silhouette - this is not
# a prompt-wording gap, it is the fourth confirmed case in this codebase of a prompt-
# only instruction not binding on the image path. The fix is structural: stop asking
# Gemini to draw the bottle at all when the placement qualifies, and place the real
# pixels there instead. ----

# Grip-shaped language in the PRODUCT object's own description - checked because
# deconstruct.py sometimes folds "held by a hand" into the product's description
# itself rather than (or in addition to) a separate prop object with serves_object_id.
_GRIP_DESCRIPTION_KEYWORDS = (
    "held", "holding", "grip", "gripped", "grasp", "grasped", "clutch", "clutched",
    "in hand", "in-hand", "in a hand", "fingers wrapped", "palm",
)

# background.light keywords that mean "hard or strongly directional" - a flat pasted
# cutout has no cast shadow/highlight matching a specific hard light direction, and
# reads as pasted-on immediately under this kind of lighting. Absence of these words is
# the gate condition, never a requirement that the phrase explicitly says "soft".
_HARD_LIGHT_KEYWORDS = (
    "hard", "harsh", "direct sun", "direct sunlight", "strong direct", "dramatic",
    "spotlight", "high-contrast", "high contrast", "strongly directional",
    "harsh shadow", "hard shadow", "intense light", "glaring",
)


def _composite_gate(blueprint, include_product=True):
    """Decides whether composite_product will run for THIS generation - evaluated
    BEFORE build_image_prompt (see generate_image's own ordering) so
    _bottle_geometry_clause/_bottle_identity_clause can be suppressed from the SAME
    build exactly when compositing will actually happen. Every gate below is a
    structural, blueprint-level fact - none require the generated image itself, which
    is what makes evaluating this before the Gemini call possible at all.

    Returns (proceed: bool, reason: str, product_object: dict|None). product_object is
    the single qualifying objects[] entry when proceed is True, else None - the caller
    reuses it directly for the bbox rather than re-deriving it a second time. Any
    failing gate returns proceed=False immediately; reason names exactly which one, for
    the caller to log at INFO - never a silent skip.

    Gates (every one must pass):
    1. Exactly one objects[] entry with kind=="product" and disposition=="substitute",
       and it has a usable (4-element) bbox - Pillow needs one unambiguous placement,
       never a guess between several candidates or a fallback when none exists.
    2. Not held/gripped: no kind=="prop" object whose serves_object_id names this
       product (serves_object_id is only ever populated on text/prop objects per
       schema/blueprint.schema.json, never "person" - a hand is always tracked as a
       prop, so this is the complete structural check for "something is holding it"),
       AND the product's own description carries no grip-shaped language. A held
       bottle needs a grip shadow following finger contours and a scale relationship
       to a hand that a flat pasted cutout cannot produce convincingly (see the Phase 1
       diagnostic's Pillow-feasibility assessment).
    3. background.light does not read as hard or strongly directional (see
       _HARD_LIGHT_KEYWORDS) - a pasted cutout has no cast shadow/highlight matching a
       specific hard light direction.

    Deliberately does NOT gate on production_style/realism (e.g. "illustrated") - not
    in the task's own three named gates, so not added here; flagged in the handover
    notes as a known residual risk, not silently folded into this function.

    include_product=False short-circuits to proceed=False before inspecting anything
    else - there is no product to composite when the run itself doesn't want one."""
    if not include_product:
        return False, "include_product is False", None
    objects = blueprint.get("objects") or []
    candidates = [
        obj for obj in objects
        if isinstance(obj, dict) and obj.get("kind") == "product" and obj.get("disposition") == "substitute"
    ]
    if len(candidates) != 1:
        return False, (
            f"expected exactly one substitute-marked product object, found {len(candidates)}"
        ), None
    product_object = candidates[0]
    bbox = product_object.get("bbox")
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return False, "product object has no usable 4-element bbox", None
    object_id = product_object.get("object_id")
    description = (product_object.get("description") or "").lower()
    if any(kw in description for kw in _GRIP_DESCRIPTION_KEYWORDS):
        return False, (
            f"product description reads as held/gripped: {product_object.get('description')!r}"
        ), None
    holder = next(
        (obj for obj in objects
         if isinstance(obj, dict) and obj.get("kind") == "prop" and obj.get("serves_object_id") == object_id),
        None,
    )
    if holder is not None:
        return False, (
            f"object {holder.get('object_id')!r} (prop) serves this product - held or "
            f"staged, not free-standing"
        ), None
    light = ((blueprint.get("background") or {}).get("light") or "").lower()
    if any(kw in light for kw in _HARD_LIGHT_KEYWORDS):
        return False, f"background.light reads as hard/directional: {light!r}", None
    return True, "ok", product_object



def _match_brightness_conservative(cutout, scene_img):
    """Nudges the cutout's brightness toward the scene's own mid-tones - conservatively,
    since a wrong nudge is worse than none. Measures the scene's mean luminance over
    the WHOLE generated image (a single global figure, not a per-region sample - "a
    single conservative ImageEnhance pass" per the task, not a lighting model) against
    the cutout's own mean luminance over its OPAQUE pixels only (masked by its own
    alpha channel, so the transparent surround never pulls the mean toward black).
    Applies only HALF the measured correction, then clamps the resulting factor to
    [0.85, 1.15] - a bottle that's already close to the scene's tones is left alone
    entirely (a <2% factor is treated as a no-op) rather than nudged for no visible
    reason. Returns a new Image; never mutates the caller's own cutout."""
    scene_mean = ImageStat.Stat(scene_img.convert("L")).mean[0]
    cutout_l = cutout.convert("L")
    if cutout.mode == "RGBA":
        cutout_mean = ImageStat.Stat(cutout_l, mask=cutout.split()[3]).mean[0]
    else:
        cutout_mean = ImageStat.Stat(cutout_l).mean[0]
    if cutout_mean <= 0:
        return cutout
    raw_factor = scene_mean / cutout_mean
    factor = 1.0 + (raw_factor - 1.0) * 0.5
    factor = max(0.85, min(1.15, factor))
    if abs(factor - 1.0) < 0.02:
        return cutout
    return ImageEnhance.Brightness(cutout).enhance(factor)


def _draw_contact_shadow(base, paste_x, paste_y, cutout_w, cutout_h):
    """Draws a soft-edged elliptical shadow onto `base` (an RGBA image, mutated in
    place), positioned at the footprint of where the cutout is about to be pasted -
    called strictly BEFORE that paste so the shadow sits under the bottle, never on top
    of it. Subtle by construction: low peak opacity (70/255), a wide Gaussian blur, and
    an ellipse sized well within the cutout's own footprint - a shadow that reads as a
    hard grey oval is exactly the "pasted-on" look this whole gate exists to avoid."""
    shadow_layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(shadow_layer)
    ellipse_w = cutout_w * 0.7
    ellipse_h = max(8.0, cutout_h * 0.06)
    cx = paste_x + cutout_w / 2
    cy = paste_y + cutout_h - ellipse_h * 0.3
    draw.ellipse(
        [cx - ellipse_w / 2, cy - ellipse_h / 2, cx + ellipse_w / 2, cy + ellipse_h / 2],
        fill=(0, 0, 0, 70),
    )
    blur_radius = max(4.0, ellipse_h * 0.5)
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=blur_radius))
    base.paste(shadow_layer, (0, 0), shadow_layer)


def composite_product(image_bytes, cutout_bytes, bbox):
    """Paste the real product cutout into a generated draft, scaled to `bbox` while
    PRESERVING the cutout's own aspect ratio - derived from cutout.size directly (the
    authoritative asset is 503x1562, but this never hardcodes that ratio; it reads
    whatever the actual fetched cutout's real dimensions are, so a future re-export of
    the asset at a slightly different size still composites correctly with no constant
    to update). Never stretched to the bbox's own raw width:height, which would
    distort a rigid glass silhouette.
    Fits INSIDE the bbox on whichever axis is tighter (min of the two independent
    scale factors), then centres the result horizontally within the bbox and anchors
    it to the bbox's BOTTOM edge, so the cutout's own base lands where the drawn
    bottle's base was - bbox is deconstruct's estimate of where the DRAWN bottle sits,
    not a container this function is free to centre in on both axes.

    Draws a subtle contact shadow (_draw_contact_shadow) at the paste footprint before
    pasting, and nudges the cutout's brightness toward the scene's own mid-tones
    (_match_brightness_conservative) before that - shadow first, then the (possibly
    brightness-adjusted) cutout on top, so ordering matches what a real photograph
    would show.

    Only ever called after _composite_gate has already confirmed this is a placement
    Pillow can do convincingly - this function performs no gating of its own, it
    trusts its caller. Raises on a malformed image/cutout/bbox rather than failing
    silently - generate_image's own caller catches this and falls back to Gemini's
    unmodified render rather than lose a draft over a compositing bug.

    bbox is [x, y, w, h] as fractions of the FULL IMAGE (0.0-1.0) - the same convention
    every other bbox reader in this codebase (drift_check.py, generate_copy.py's
    _reading_order_key) already uses. Returns new PNG bytes."""
    base = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    cutout = Image.open(io.BytesIO(cutout_bytes)).convert("RGBA")

    img_w, img_h = base.size
    x, y, w, h = bbox
    box_x0, box_y0 = x * img_w, y * img_h
    box_w, box_h = w * img_w, h * img_h

    cutout_w, cutout_h = cutout.size
    scale = min(box_w / cutout_w, box_h / cutout_h)
    new_w = max(1, round(cutout_w * scale))
    new_h = max(1, round(cutout_h * scale))
    resized_cutout = cutout.resize((new_w, new_h), Image.LANCZOS)
    resized_cutout = _match_brightness_conservative(resized_cutout, base)

    paste_x = round(box_x0 + (box_w - new_w) / 2)
    paste_y = round(box_y0 + box_h - new_h)

    _draw_contact_shadow(base, paste_x, paste_y, new_w, new_h)
    base.paste(resized_cutout, (paste_x, paste_y), resized_cutout)

    out = io.BytesIO()
    base.convert("RGB").save(out, format="PNG")
    return out.getvalue()


def _bottle_geometry_clause():
    """2026-08-16: the ONE authoritative statement of the Besque bottle's actual
    proportions - a fixed, hardcoded constant, never assembled from blueprint,
    artifact, or DB fields, and never varying by call. Every other place in this file
    that used to describe bottle shape/proportions/pump/collar in its own words
    (_bottle_geometry_source_clause, the "confirm... proportions" phrase in the
    illustrated substitute/add branches of _edit_mode_instruction) has been folded to
    defer to THIS clause instead of re-stating the categories itself - a second,
    differently-worded geometry statement nearer the point of use is exactly the
    "closer wins" contradiction shape this codebase has hit repeatedly (see CLAUDE.md's
    2026-08-12 finding), so there is now only one place actual proportions are stated.

    Composed into build_image_prompt's generate path only (all three branches, gated
    on effective_include_product same as _bottle_identity_clause/_bottle_integration_
    clause) - never the realism-only targeted edit path (src/realism_deltas.py), which
    sends its own pre-authored delta sentence alone, by design."""
    return (
        "Bottle geometry is fixed and identical in every render. Total height is 4.33 "
        "times the glass body width. The glass body is a straight-sided cylinder with "
        "parallel walls and no taper, occupying the lower 2.85 body-widths. Above it a "
        "short shoulder of 0.21 body-widths meets a polished gold collar 0.75 "
        "body-widths wide and 0.63 body-widths tall. Above the collar sits a black "
        "pump: a stem 0.43 body-widths wide and a horizontal lever spout overhanging "
        "the body's left edge by 0.38 body-widths. These proportions never vary with "
        "scene, crop, style, realism, bottle count, or how large the bottle appears "
        "in frame."
    )


def _bottle_identity_clause(product):
    """Item 2 (2026-08-13 build): promotes bottle identity to a dedicated STRICT clause
    with the same weight/position as the numbered brand rules - appended immediately
    after brand_rules() in all three build_image_prompt branches (gated on
    effective_include_product, so it never fires on a deliberately productless run),
    rather than living solely inside product_desc's one generic paragraph.

    Root cause this addresses (2026-08-13 audit): the bottle's actual identity facts
    were previously stated exactly ONCE, as one prose sentence inside product_desc
    ("Its fixed visual appearance: {visual_desc}."), with no STRICT/numbered status -
    while HOW to stylize the bottle (STYLE_GUIDANCE prose, _bottle_register_clause) is
    comparatively extensive and carries rule-level prominence. This clause does not
    replace product_desc's own statement (still useful there, next to the ingredient/
    label-text rules) - it gives identity a SECOND, earlier, STRICT-weighted statement,
    the same redundancy-for-emphasis every numbered rule already gets relative to a
    single generic instruction.

    Fed STRUCTURALLY from product.visual_description/substance_colour/certifications -
    contains no colour, material, or design fact of its own; every specific fact in the
    returned text comes from the product dict passed in, never a literal invented here.
    product=None or a record with none of these three fields populated falls back to a
    generic "identity is fixed, once known, never invented" statement - the same
    never-guess contract product_desc's own NO_PRODUCT-shaped fallback already uses."""
    if not product:
        return (
            "BOTTLE IDENTITY (STRICT, BRAND-LEVEL, EVERY GENERATION PATH, EVERY "
            "PRODUCTION STYLE INCLUDING ILLUSTRATED/COMIC/FLAT-VECTOR): no product "
            "record was supplied for this run, so this bottle's exact colours, label "
            "design, and hardware are not stated here and must not be invented. "
            "Whatever generic product description appears elsewhere in this prompt is "
            "what governs. Once known, identity is fixed and must never vary by "
            "rendering register - see the material-realism and bottle-register clauses "
            "elsewhere for how it is LIT, never what it IS. "
        )
    facts = []
    visual_desc = (product.get("visual_description") or "").strip()
    if visual_desc:
        facts.append(visual_desc)
    substance_colour = (product.get("substance_colour") or "").strip()
    if substance_colour:
        facts.append(f"The oil itself is {substance_colour}.")
    certifications = [str(c).strip() for c in (product.get("certifications") or []) if str(c).strip()]
    if certifications:
        facts.append("Certification icons present on the label: " + ", ".join(certifications) + ".")
    fact_text = " ".join(facts) if facts else (
        "No further visual detail is on record for this product - do not invent one."
    )
    return (
        "BOTTLE IDENTITY (STRICT, BRAND-LEVEL, EVERY GENERATION PATH, EVERY PRODUCTION "
        "STYLE INCLUDING ILLUSTRATED/COMIC/FLAT-VECTOR - OVERRIDES ANY STYLISATION "
        "INSTRUCTION ELSEWHERE IN THIS PROMPT THAT WOULD CHANGE IT): this is what the "
        f"Besque bottle IS, not merely that it is fixed - {fact_text} These are the "
        "ONLY colours, materials, proportions, and label facts this bottle may show, "
        "in EVERY register this prompt might otherwise describe - photographic, "
        "3D-rendered, comic-panel, or flat-vector alike. This clause states what the "
        "label CONTAINS - what actually renders legibly at a given size or drawing "
        "style is governed separately (see the register-specific guidance and the "
        "small-scale simplification note elsewhere in this prompt), never by "
        "inventing different content here. Wherever a register or render size cannot "
        "carry full label detail, that guidance may simplify or drop SECONDARY "
        "content only (sub-lines, certification icons, fine print, border/rule "
        "detail) - the wordmark, product name, and colours are NEVER simplified away "
        "or altered, at any size or in any register. Simplification is about what is "
        "LEGIBLE, never a licence to invent a different, generic, or simplified-"
        "beyond-recognition bottle. Same proportions, same label artwork, same "
        "colours, same text, every single generation, regardless of what the "
        "reference ad's own product looks like or what stylisation guidance "
        "elsewhere in this prompt otherwise permits. "
    )


def _bottle_geometry_source_clause():
    """Edit mode only (2026-08-15): the ONE place two different images are both in play
    at once - the competitor's reference ad (attached as the first image, framed as "THE
    AD TO REPRODUCE") and Besque's OWN product reference photo(s) (attached separately,
    if any, framed by _reference_framing) - and nothing before this clause said, in so
    many words, which image supplies which fact about the bottle. Live evidence: across
    a 5-ad OSEA batch, the rendered bottle changed silhouette, height, width, and
    proportions between ads - tracking each reference ad's OWN product geometry instead
    of Besque's, exactly the failure mode this clause exists to name and forbid.

    REWRITTEN 2026-08-16: this used to enumerate the geometry categories itself
    (silhouette, height-to-width ratio, neck/shoulder/base geometry, pump/collar
    hardware design, label shape/placement/border/content) - a SECOND, differently-
    worded geometry statement sitting near the point of use, exactly the "closer wins"
    contradiction shape CLAUDE.md's 2026-08-12 finding already names. Now defers
    entirely to _bottle_geometry_clause's single hardcoded numbers instead of
    re-describing what they cover - there is exactly one place actual proportions are
    stated, this clause only says where they may NOT come from."""
    return (
        "BOTTLE GEOMETRY SOURCE (STRICT, NON-NEGOTIABLE, EVERY PRODUCTION STYLE): two "
        "different images are attached for two entirely different jobs, and they must "
        "never be conflated. The COMPETITOR'S reference ad (the first attached image) "
        "supplies RENDERING STYLE ONLY - register, lighting treatment, finish, and "
        "whether the scene reads photographic or flat/illustrated. It supplies NOTHING "
        "about the Besque bottle's own shape - the bottle's proportions are stated "
        "exactly, once, in the BOTTLE GEOMETRY clause above, and neither this reference "
        "ad NOR Besque's own product reference photo(s) (attached separately, where "
        "supplied) may override, adjust, or re-derive them from what either image shows; "
        "those photos confirm colour, label, and hardware finish only (see BOTTLE "
        "IDENTITY above), never shape. A bottle whose proportions change between "
        "generations because it is tracking the reference ad's own product shape, "
        "rather than the fixed clause above, is always wrong - in every register this "
        "prompt might otherwise describe. "
    )


def _bottle_integration_clause(suppress_bottle_identity=False):
    """Item 2 (2026-08-13 build): nothing in this prompt stated, before now, that the
    bottle must read as a participating object in the scene rather than a flat packshot
    pasted on top of it. Deliberately unconditional/generic - a behavioural requirement
    about HOW the (already identity-fixed) bottle sits in the composition, independent
    of what it looks like, so no product data is needed here.

    Contradiction check: edit mode's own photographic-substitute branch says to place
    the Besque product "matching the original shot's composition as faithfully as
    possible" - if the reference itself shows a floating, ungrounded packshot (a common
    competitor ad style), that instruction and this one would disagree about whether
    the SAME bottle floats or participates. Resolved by the explicit override sentence
    below, the same "this rule wins regardless of what the reference shows" pattern
    rule 10 already uses for age - "faithfully" governs POSITION/scale/framing within
    the composition, never whether the product is grounded vs. floating.

    Pump orientation (2026-08-15): every configured Besque product reference photo
    happens to show the pump facing the same way, and nothing before this clause said
    that facing is not itself a fixed fact - every generated bottle showed the pump
    facing that one direction regardless of the scene. Pump/collar DESIGN and geometry
    are governed by _bottle_geometry_source_clause/_bottle_identity_clause and stay
    fixed; FACING is a composition detail, addressed here alongside grip/scale/contact-
    shadow, the same category of fact as everything else in this clause.

    suppress_bottle_identity (2026-08-19, Route B double-bottle fix): True when
    generate_image's own _composite_gate has ALREADY decided this run will paste the
    real product cutout in after Gemini returns - same flag build_image_prompt
    already threads to _bottle_identity_clause/_bottle_geometry_clause for the same
    reason. LIVE BUG this closes, confirmed 2026-08-17 (gate passed 16:29, draft
    written 16:31, ad 2767866756880226): identity/geometry were already suppressed
    in this case, but this clause was left unconditional - still asking Gemini to
    draw "a PARTICIPATING OBJECT... held, in the process of being applied, or
    resting." Gemini complied and drew its OWN bottle (a taller amber bottle with a
    partial label) behind and left of the correctly-pasted real cutout, because
    nothing here told it not to draw one at all. When True, this clause asks for the
    SAME scene participation (scale, contact/grip shadow, grip conformation when a
    hand is present) but as an EMPTY, product-shaped space for the compositor to
    fill afterward, and explicitly forbids rendering the product's own form, label,
    or pump - closing the gap that produced the second bottle. False (the default)
    reproduces this clause's prior text byte-for-byte - no caller that doesn't know
    about compositing sees any change."""
    if suppress_bottle_identity:
        return (
            "BOTTLE INTEGRATION - COMPOSITING MODE (STRICT, EVERY GENERATION PATH, "
            "OVERRIDES ANY COMPOSITION-MATCHING INSTRUCTION ELSEWHERE THAT WOULD "
            "REPRODUCE A FLOATING PRODUCT SHOT OR DRAW A BOTTLE): a real product "
            "photograph will be PASTED into this exact scene after you generate it - "
            "you must leave the correct SPACE for it, but you must NEVER draw the "
            "bottle itself. Do not render any bottle, container, packaging, label, "
            "pump, cap, or liquid anywhere in this space, in any form, under any "
            "circumstances - not the reference's product, not an invented one, not a "
            "silhouette, outline, or placeholder shape standing in for it. Leave that "
            "region as clean, unoccupied surface, background, or skin, exactly as if "
            "no object were ever going to be there, EXCEPT for the surrounding "
            "context a real object would leave behind: scale the empty space "
            "consistently with whatever is nearest it - a hand, a shelf, a counter, a "
            "towel - never larger or smaller than that context would allow. Render a "
            "contact shadow (or, where held, a grip shadow) exactly where a bottle "
            "meeting a hand or surface would cast one, even though nothing is drawn "
            "in that space yet - its absence is what makes a later paste read as "
            "glued on. WHEN A HAND IS PRESENT: render the hand in a natural gripping "
            "pose around the empty space - fingers curled as if wrapped around a "
            "bottle's body, wrist at a natural angle for that grip - never resting "
            "open or flat as if holding nothing. Never let anything else (another "
            "prop, a caption, the hand itself) occupy or overlap the space reserved "
            "for the bottle. "
        )
    return (
        "BOTTLE INTEGRATION (STRICT, EVERY GENERATION PATH, OVERRIDES ANY COMPOSITION-"
        "MATCHING INSTRUCTION ELSEWHERE THAT WOULD REPRODUCE A FLOATING PRODUCT SHOT): "
        "the bottle is a PARTICIPATING OBJECT in this scene, never a flat packshot "
        "pasted on top of it. It must be held, in the process of being applied, or "
        "resting on a real surface within the scene - never floating, never centred on "
        "an empty background unrelated to the composition around it - even when the "
        "reference ad itself shows the competitor's product as a floating, ungrounded "
        "packshot: that presentation is never reproduced for Besque's own bottle, "
        "regardless of how faithfully the surrounding composition is otherwise matched. "
        "Scale it consistently with whatever is nearest it - a hand, a shelf, a "
        "counter, a towel - never larger or smaller than that context would allow. A "
        "contact shadow (or, where held, a grip shadow) must be visible wherever the "
        "bottle meets a hand or surface - its absence is what makes a packshot read as "
        "pasted in. WHEN HELD: fingers wrap convincingly around the bottle's body, the "
        "wrist sits at a natural angle for that grip, and the bottle is scaled "
        "correctly to the hand holding it - never a hand posed around a bottle-shaped "
        "gap, and never a hand too large or small for the bottle it holds. WHEN THE "
        "PRODUCT IS BEING APPLIED: show the oil visibly on the skin, not only the "
        "bottle in frame. The bottle must NEVER overlap a text block or caption - if "
        "the composition would otherwise place one over the other, move or resize the "
        "bottle within the scene's own logic (per its stated scale) rather than let it "
        "cross behind or in front of rendered text. PUMP/CAP ORIENTATION follows THIS "
        "SCENE's own composition and whichever hand or surface the bottle sits on or is "
        "held by - never fixed to match the facing shown in Besque's own reference "
        "photo(s), which fix the pump's design and geometry only, never which way it "
        "points. Rotate the pump/cap to whatever facing this scene's grip or resting "
        "position actually requires. "
    )


def _register_lighting_only_clause():
    return (
        "Only the bottle's lighting, grading, and finish adapt to match the rendering "
        "register - lit like a phone photo in a UGC frame, like a studio product shot in a "
        "studio frame, rendered in that illustration's own style in an illustrated frame - "
        "always the same bottle, same shape, same label. Never a hand-drawn bottle inside a "
        "photographic frame, never a photographic bottle inside an illustrated frame. "
    )


def _scene_lighting_facts(background):
    """Turn deconstruct.py's observed `background.light` phrase into a facts sentence -
    an OBSERVATION of this specific reference's own lighting, never a style label.

    REWIRED 2026-08-17: visual.scene_lighting's six discrete sub-fields (light_direction/
    hardness/shadow_behaviour/colour_temperature/grain/depth_of_field) no longer exist
    (schema/blueprint.schema.json - the objects-array refactor collapsed them into one
    top-level `background.light` free-text phrase, e.g. "soft warm light from
    upper-left"). That per-attribute structure is GENUINELY LOST, not reconstructed here:
    this function states the one phrase deconstruct.py actually records and nothing
    more - it does not attempt to parse "hardness" or "colour temperature" back out of
    prose that was never guaranteed to mention them.

    Returns "" when nothing was extracted (a pre-migration blueprint with no `background`
    key, or the model omitted `light` this run) - callers fall back to the generic
    register-matching wording in that case; this function never guesses a value to fill
    the gap."""
    background = background or {}
    light = background.get("light")
    if not light:
        log.error(
            "_scene_lighting_facts skipped - background.light is missing/empty on this "
            "blueprint. Callers fall back to generic register-matching wording with no "
            "observed lighting fact at all; this is the exact 'field collapsed by the "
            "2026-08-17 objects-array refactor and nothing downstream knows it' gap "
            "flagged in CLAUDE.md, not an expected per-run absence."
        )
        return ""
    return f"OBSERVED SCENE LIGHTING (a fact about this reference, not a style label): {light}. "


def _scene_composition_facts(layout_detail=None, visual=None, background=None):
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
    position.

    REWIRED 2026-08-17: layout_detail.background_type no longer exists
    (schema/blueprint.schema.json - collapsed into the new top-level `background` object,
    see _scene_lighting_facts). `background.surface` is this function's background fact
    now; `background.colour`, when present, is folded into the same fact rather than a
    second line - the same single-fact convention edit_capability._background_control
    already uses for its current_value string."""
    layout_detail = layout_detail or {}
    visual = visual or {}
    background = background or {}
    facts = []
    if visual.get("layout"):
        facts.append(f"overall layout: {visual['layout']}")
    if layout_detail.get("frame_division"):
        facts.append(f"frame divides as: {layout_detail['frame_division']}")
    if layout_detail.get("zone_positions"):
        facts.append("existing elements sit at: " + "; ".join(layout_detail["zone_positions"]))
    if background.get("surface"):
        bg_fact = f"background: {background['surface']}"
        if background.get("colour"):
            bg_fact += f" ({background['colour']})"
        facts.append(bg_fact)
    if not facts:
        log.error(
            "_scene_composition_facts skipped - none of visual.layout, layout_detail."
            "frame_division, layout_detail.zone_positions, or background.surface is "
            "present on this blueprint. Callers fall back to a generic composition-aware "
            "instruction with no observed placement fact at all for a new element being "
            "added to the scene."
        )
        return ""
    return ("OBSERVED SCENE COMPOSITION (facts about THIS reference's existing layout, "
            "never a fixed position): " + "; ".join(facts) + ". ")


def _bottle_register_clause(background, style=None):
    """Replaces the generic 'match the rendering register' instruction with a concrete
    observed fact about THIS reference's own lighting, whenever deconstruct.py extracted
    one - a wording-only "match the style" instruction has already failed three times on
    this exact bottle-register bug (see CLAUDE.md's guardrails note), so this states what
    the scene's lighting actually IS rather than asking the model to infer it.

    REWIRED 2026-08-17: `background` replaces the old six-field `scene_lighting` dict
    (light_direction/hardness/shadow_behaviour/colour_temperature/grain/depth_of_field) -
    those fields no longer exist (schema/blueprint.schema.json); `background.light` is
    one free-text phrase now. The per-attribute breakdown this clause used to be able to
    name explicitly (direction, hardness, colour temperature, grain, as separate facts)
    is GENUINELY LOST - this function no longer claims those specific attributes were
    observed, since they may not be; it only asserts the single phrase deconstruct.py
    actually recorded.

    style gates whether background.light is used AT ALL (item 3 fix, 2026-08-13,
    unchanged by this rewire): deconstruct.py's lighting field describes PHOTOGRAPHIC
    lighting - meaningless for a hand-drawn illustration, and live evidence (from the
    old six-field version) showed the model doesn't leave it blank for one; it writes a
    value like "Not applicable - no photographic lighting", which _scene_lighting_facts
    would then read as a real OBSERVED fact and assert verbatim, risking it rendering as
    literal on-image text (rule 8's own failure shape). Gating on style=="illustrated"
    BEFORE ever calling _scene_lighting_facts fixes this structurally, the same way
    _illustrated_elements_clause already gates on style elsewhere in this file - an
    illustrated register's drawing treatment always follows
    _register_lighting_only_clause()'s own style-driven wording ("rendered in that
    illustration's own style"), never the reference's photographic facts, regardless of
    what deconstruct.py happened to write into that field this run.

    For a photographic register (or style not given - callers that predate this param
    keep their old behaviour), falls back to _register_lighting_only_clause() only when
    background.light is entirely empty (nothing to state a fact about) - never a silent
    guess. When a fact IS present, it describes the SCENE's overall lighting character
    that the bottle's own surface rendering should read consistent with - but the
    bottle's own CONTACT or GRIP shadow, and how it is grounded in the frame, are
    explicitly carved OUT of "match exactly" and deferred to BOTTLE INTEGRATION's own
    composition instead (the contradiction this fixes): the reference's fact describes a
    scene that may have shown a floating, ungrounded product with no contact point at
    all, and BOTTLE INTEGRATION can require a materially different composition (held,
    applied, resting) than the reference did - a contact/grip shadow that composition
    requires is never something this fact could have observed, so "match exactly" must
    not be read to forbid it. Geometry, proportions, and label stay exactly as stated
    above regardless (bottle identity is untouched by this function, unchanged) - see the
    material realism clause elsewhere for the liquid/glass physical-realism requirement,
    also untouched."""
    if style == "illustrated":
        return _register_lighting_only_clause()
    facts = _scene_lighting_facts(background)
    if not facts:
        return _register_lighting_only_clause()
    return (
        facts +
        "This is a fact about THIS SCENE's overall lighting character which the "
        "bottle's own surface, highlights, and colour cast must read consistent with, "
        "never the separate, unrelated studio lighting the product's own reference "
        "photo(s) happen to have been shot under. This does NOT govern the bottle's own "
        "contact or grip shadow, or how it is grounded in the frame - those follow "
        "entirely from the composition BOTTLE INTEGRATION above actually describes "
        "(held, applied, resting), never from this observed fact: the reference may "
        "have shown a floating product with no contact point to observe a shadow from "
        "at all, and integration's own requirement for a grounded bottle wins wherever "
        "the two would otherwise disagree. Geometry, proportions, and label stay "
        "exactly as stated above regardless. "
    )


def _register_clause(style, background=None):
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
        log.error(
            "_register_clause skipped - no style/realism value was resolved before this "
            "call (edit mode should always resolve one, via the operator's run-strip "
            "choice or blueprint.production_style.style as a fallback). REGISTER "
            "guidance, the bottle-fixed clause, and the bottle-register clause are all "
            "silently omitted from this prompt as a result."
        )
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
        + _bottle_register_clause(background, style)
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

    A FOURTH exception was added 2026-08-13 (item 2 redesign) for the now-deleted
    illustrated-elements substitution clause - REPOINTED 2026-08-17 at the SCENE
    OBJECTS inventory (_objects_clause), which subsumed it: that clause runs earlier in
    the assembled prompt than this one and already states substitute/keep/drop for
    EVERY object individually, including any drawn prop making the competitor's
    argument. A dangling reference to a deleted clause name is exactly the
    referring-to-a-missing-instruction contradiction class this codebase has hit
    repeatedly (see CLAUDE.md's guardrails note) - fixed by repointing at the clause
    that actually replaced it, never by leaving the old name in place.

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
        "matter how broadly \"everything else\" might otherwise be read), EXCEPT "
        "the scale of any prop, holder, float, or opening sized for the reference's own "
        "product - that adapts to fit the substituted Besque bottle's real, fixed "
        "proportions instead, never the reverse (see the product instruction above), "
        "and EXCEPT any object given a substitute or drop disposition in the SCENE "
        "OBJECTS inventory above - those are substituted or removed per that "
        "inventory, never carried over unchanged just because they are otherwise "
        "part of the scene"
    )


def _edit_mode_instruction(text_in_image=False, headline=None, subtext=None, offer_text=None,
                            include_product=True, reference_has_product=True,
                            reference_has_text_zone=True, layout_detail=None, visual=None,
                            retheme_colours=True, palette=None,
                            substance_colour=None, style=None, background=None,
                            cta_text=None, product_name=None,
                            panel_copy=None, testimonial=None, certifications=None,
                            objects=None, face_present=None,
                            clone_mode=False, authorised_product_count=1):
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
    # offer_text is EFFECTIVELY present here only if this reference actually has a real
    # offer-shaped object (text_purpose "offer"/"price_anchor" - see
    # _objects_have_text_purpose/_TEXT_PURPOSE_OFFER_TYPES).
    #
    # REWIRED 2026-08-17 (was ORPHANED, always False under clone_mode - structural_zones
    # no longer exists): the objects-array refactor's own text_purpose classification is
    # exactly the "is this specifically an offer-shaped zone" signal the old
    # structural_zones-based check needed and, at the time, had no equivalent for -
    # text_purpose now IS that signal, more precisely than the old zone_type/keyword
    # combination ever was. clone_mode=False (the default) is unaffected: offer_text's
    # own truthiness is still the only gate.
    effective_offer_text = (
        offer_text if not clone_mode
        else (offer_text if _objects_have_text_purpose(objects, _TEXT_PURPOSE_OFFER_TYPES) else None)
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
            "\"which non-person elements appear\" might otherwise be read - NOR to any "
            "object given a substitute or drop disposition in the SCENE OBJECTS "
            "inventory above, which is substituted or removed per that inventory rather "
            "than carried over unchanged. "
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
            "governs those and overrides this reproduce instruction entirely - NOR to "
            "any object given a substitute or drop disposition in the SCENE OBJECTS "
            "inventory above, which is substituted or removed per that inventory "
            "rather than reproduced unchanged. "
            + exception_clause
        )
    opening += _register_clause(style, background)

    if include_product and reference_has_product and style == "illustrated":
        name = product_name or "Besque"
        base = opening + (
            "Changing ONLY the product. Remove the competitor's product entirely and draw "
            f"the Besque product NATIVELY in this scene's own illustrated visual language - "
            f"flat, matching the surrounding artwork's own line weight and shading, never a "
            f"photograph or photorealistic render composited into the drawing, REGARDLESS "
            f"of how photographic the attached product reference photo(s) (if any) look - a "
            f"photographic reference photo does not make the DRAWN bottle photographic. "
            f"Use those photos ONLY to confirm this product's exact identity - label design, "
            f"colours, and hardware finish - never as a rendering-style reference and never "
            f"for shape or proportions, which are fixed exactly as stated in the BOTTLE "
            f"GEOMETRY clause above, with or without a photo; "
            f"the leak this instruction replaced (2026-08-06, then reversed 2026-08-14) was "
            f"the photo's photographic REGISTER bleeding into the drawing, not its identity "
            f"facts, so withholding the photo is no longer how this is prevented. Where no "
            f"reference photo is attached for this run, work from colour and "
            f"the label name alone - \"{name}\". Secondary label content "
            f"(sub-lines, certification icons, fine print) does not need to be legible at "
            f"this scale in this style; name and colour accuracy matter, secondary-text "
            f"legibility does not. Exactly {authorised_product_count} Besque item(s) "
            f"belong in this scene - computed from the reference's own product "
            f"objects, never left for you to judge from the pixels. WHICH reference "
            f"position each one occupies (and which competitor product, if any, is "
            f"removed instead) is governed entirely by the SCENE OBJECTS inventory "
            f"above; follow it exactly, never inventing a different count or a "
            f"different assignment. "
            + _substance_recolour_clause(substance_colour) +
            f"Everything else in the scene - {_non_carryover_exceptions_clause()} - carries over "
            "from the source image exactly as the reproduce-faithfully instruction above states (never a stricter 'exactly' reintroduced here, including its small natural variation allowance). "
        )
    elif include_product and reference_has_product:
        lighting_facts = _scene_lighting_facts(background)
        # "with its lighting" (the old wording here) was ambiguous between "the scene's
        # lighting" and "the product reference photo's own separate studio lighting" - the
        # two contradict whenever the reference photo is a clean studio/cutout shot dropped
        # into a UGC or non-studio scene. State the scene's own observed lighting fact
        # explicitly instead, so there's nothing left to infer; falls back to a
        # register-level instruction (never the reference photo's own lighting) only when
        # no background.light was extracted at all.
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
            "FIXED exactly as stated in the BOTTLE GEOMETRY clause above - if the reference's own "
            "product sits inside, on, or against a prop, holder, float, or opening "
            "sized for ITS shape, that PROP is what adapts: resize or reshape it to "
            "properly fit the Besque bottle's real proportions, never the reverse "
            "(never stretch, squeeze, or shrink the bottle to fit a prop sized for a "
            "differently-shaped product). Exactly "
            f"{authorised_product_count} Besque bottle(s) belong in this scene - "
            "computed from the reference's own product objects, never left for you "
            "to judge from the pixels. WHICH reference position each one occupies "
            "(and which competitor product, if any, is removed instead) is governed "
            "entirely by the SCENE OBJECTS inventory above; follow it exactly, never "
            "inventing a different count or a different assignment. " + lighting_instruction
            + _substance_recolour_clause(substance_colour) +
            f"Everything else in the scene - {_non_carryover_exceptions_clause()} - carries over "
            "from the source image exactly as the reproduce-faithfully instruction above states (never a stricter 'exactly' reintroduced here, including its small natural variation allowance). "
        )
    elif include_product and not reference_has_product and style == "illustrated":
        # ADD, illustrated register (2026-08-07, reference usability gate reversal): the
        # reference has no product to substitute, but include_product=True still means
        # one belongs in the output - added natively into the scene's own illustrated
        # visual language, same drawing constraints as the substitute-illustrated branch
        # above (no reference photo attached, work from colour/name alone - shape is
        # always fixed by _bottle_geometry_clause regardless),
        # just with no competitor product to remove first. Placement is DERIVED from this
        # reference's own observed composition, never a fixed position.
        name = product_name or "Besque"
        composition_facts = _scene_composition_facts(layout_detail, visual, background)
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
            f"run, on purpose: work from colour and the label name alone - shape is "
            f"already fixed by the BOTTLE GEOMETRY clause above, with or without a "
            f"photo - \"{name}\". " + placement_instruction +
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
        lighting_facts = _scene_lighting_facts(background)
        lighting_instruction = lighting_facts or (
            "Light it to match THIS SCENE's own lighting register - never the separate, "
            "unrelated lighting the product's reference photo(s) happen to have been "
            "shot under. "
        )
        composition_facts = _scene_composition_facts(layout_detail, visual, background)
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
            "PERSON: if a person appears anywhere in the reference image - including a "
            "face inside a small avatar or profile picture within reproduced UI chrome "
            "(a UGC-style testimonial/social-post card), not only the ad's primary, "
            "large-in-frame subject - this is one instruction with two parts, not two "
            "competing ones, the same shape as the colour instruction above. REPRODUCE "
            "only the camera/scene mechanics: "
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
    # cta_text/testimonial/certifications (2026-08-17): UNUSED in this function
    # specifically, but no longer dead - their per-purpose substitution logic (fill an
    # offer object with offer_text, a certification object with real certifications, a
    # testimonial object with a real review, a cta object with cta_text) now lives in
    # _objects_clause/_substitute_object_line (restoring what the deleted
    # _structural_zones_clause used to do per zone_type, now driven by each object's own
    # text_purpose) - build_image_prompt calls _objects_clause separately, earlier in the
    # assembled prompt, with the same values via its own `objects_context`. panel_copy
    # remains genuinely unused (no per-zone panel-copy generation was restored - see
    # generate_copy.py's own handover notes for why) but is kept on this signature for
    # the same reason as before: removing it would cascade into callers for no benefit.
    if eff_headline:
        # The exact headline/subtext wording is stated ONCE, by rule 6's TEXT POLICY
        # above - this function must reference that authorisation, never re-quote the
        # string itself, or the same text ends up stated twice (2026-08-13 live finding:
        # the literal string appeared 2-3x in one assembled prompt across rule 6, this
        # TEXT branch, and STRUCTURAL ZONES' sub_line/body_copy fallback - the latter no
        # longer exists, but the single-statement discipline still matters).
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
            composition_facts = _scene_composition_facts(layout_detail, visual, background)
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
                    testimonial=None, product_count=None, clone_mode=False, object_copy=None):
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

    Illustrated register (2026-08-06 fix, REVISED 2026-08-14): the original fix for the
    Grüns GLP-1 leak (a photographic reference photo, attached while simultaneously
    demanding faithful substitution, produced a photorealistic bottle composited into an
    otherwise hand-drawn scene) dropped reference_images entirely whenever the effective
    style resolved to "illustrated" - no photo attached at all, identity described from
    visual_description text alone. That overcorrected: withholding identity is not what
    the leak needed fixed. pipeline.fetch_reference_images/pipeline.py's own comment
    already states plainly that building bottle identity from visual_description text
    alone "reliably gets pump direction and proportions wrong" - live evidence across
    four generations of the same product (no pump/screw neck, taller pumpless, squat
    with pump, one correct) traces to exactly this: illustrated-register runs were the
    ones with NO real photo to anchor identity to. The leak was the reference photo's
    PHOTOGRAPHIC REGISTER bleeding into the drawing, never the photo's IDENTITY facts
    (colour, label, proportions) - so reference_images are now attached in every style,
    including illustrated, and the register instruction instead (see
    _edit_mode_instruction's style=="illustrated" branch) tells Gemini to use the
    attached photo(s) ONLY to confirm identity, never as a rendering-style reference:
    the photo being photographic does not make the DRAWN bottle photographic. Applies in
    every mode, not just edit_mode - reference_images are attached the same way
    regardless, same as before this revision."""
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
    # Route B compositing gate (2026-08-17): evaluated HERE, before build_image_prompt,
    # not after Gemini returns - the gate outcome and the prompt text it gates must
    # never disagree about whether Gemini is being asked to draw the bottle. Every gate
    # is a structural blueprint fact (see _composite_gate's own docstring), so this
    # never needs the generated image itself to decide.
    should_composite, composite_gate_reason, composite_product_object = _composite_gate(
        blueprint, include_product
    )
    log.info("Ad %s: composite gate -> %s (%s)", ad_id, should_composite, composite_gate_reason)
    prompt = build_image_prompt(blueprint, product=product, include_product=include_product,
                                 text_in_image=text_in_image, headline=headline, subtext=subtext,
                                 creative_description=creative_description, edit_mode=edit_mode,
                                 offer_text=offer_text, operator_instruction=operator_instruction,
                                 retheme_colours=retheme_colours, brand_palette=brand_palette,
                                 realism=realism, critic_feedback=critic_feedback, cta_text=cta_text,
                                 panel_copy=panel_copy, testimonial=testimonial,
                                 product_count=product_count, clone_mode=clone_mode,
                                 object_copy=object_copy, suppress_bottle_identity=should_composite)
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
        # Product cutout (2026-08-16): an EXTRA reference Part, alongside the product's
        # own configured reference photos - every non-illustrated generate run, gated
        # the same way (include_product) plus a style check. Illustrated is excluded
        # for the same reason every other photographic reference is withheld there
        # (see this function's own illustrated-register docstring section above) - a
        # real photograph must not bleed its photographic register into a hand-drawn
        # scene. resolved_style mirrors build_image_prompt's own precedence exactly
        # (operator-supplied realism, else the reference's own observed production
        # style) so the two functions can never disagree about which register this is.
        resolved_style = (realism or "").strip() or (blueprint.get("production_style") or {}).get("style", "")
        if include_product and resolved_style != "illustrated":
            cutout_bytes = _fetch_product_cutout_bytes()
            if cutout_bytes:
                reference_images = reference_images + [cutout_bytes]
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

        # Route B compositing (2026-08-17): reassigns image_bytes ONLY - deliberately
        # between extraction and the disk write below, so every downstream consumer
        # (the local file, the GCS upload, pipeline.process_ad's own re-read for the
        # output critic, dedupe.save_artifact's stored path) sees the composited result
        # with no other change anywhere. should_composite was decided BEFORE
        # build_image_prompt ran (see above) - never re-evaluated here against the
        # generated image, so the prompt Gemini actually saw and what happens to its
        # output can never disagree about whether the bottle was suppressed. A
        # compositing failure (missing cutout, malformed bbox, a Pillow exception) logs
        # and falls back to Gemini's own render rather than losing the draft - this
        # mirrors _fetch_product_cutout_bytes' own fail-open contract for the same
        # asset used as a reference photo.
        if should_composite:
            cutout_bytes = _fetch_product_cutout_bytes()
            if not cutout_bytes:
                log.warning("Ad %s: composite gate passed but the product cutout is "
                            "unavailable - keeping Gemini's own render", ad_id)
            else:
                try:
                    image_bytes = composite_product(
                        image_bytes, cutout_bytes, composite_product_object["bbox"])
                    log.info("Ad %s: composited the real product cutout into the draft", ad_id)
                except Exception as e:
                    log.warning("Ad %s: compositing failed (%s: %s) - keeping Gemini's "
                                "own render", ad_id, type(e).__name__, e)

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


def _brand_wordmark_protection_clause(blueprint):
    """ALWAYS appended to a targeted-edit instruction, regardless of what's being
    edited - never target the brand wordmark rule (2026-08-14). Live failure: editing
    artifact 1251's "headline" (whose current_value didn't actually exist in the
    pixels) caused Gemini to overwrite the BESQUE wordmark region instead of leaving it
    alone.

    `blueprint` is accepted but unused (2026-08-17): this used to name the wordmark's
    own recorded position when a structural_zones brand_wordmark entry existed
    (src.edit_capability.get_brand_wordmark_zone) - that field/function no longer
    exist (schema/blueprint.schema.json - blueprint.objects replaces structural_zones),
    and there is no equivalent to rewire it to: blueprint.objects describes the
    COMPETITOR reference, never the drafted image, and Besque's own wordmark is never
    one of its rows - it is ADDED by brand_rules() rule 9, not tracked as an object
    here. Always falls back to the generic statement now, which is byte-for-byte what
    every call already produced in practice, since get_brand_wordmark_zone had already
    stopped finding anything (structural_zones never populated on a new blueprint)
    before this parameter was ever removed. Kept on the signature so this function's
    one caller doesn't need to change."""
    return (
        " The brand wordmark/logo, wherever it appears in the image, must NEVER be "
        "modified, replaced, resized, moved, or removed by this edit, regardless of the "
        "instruction above - it is not eligible for editing under any target."
    )


# The preservation-list terms, in the original fixed order. "all other text" is kept as
# ONE list item (never split into "text" + filtered separately) - the word "other"
# already excludes whichever text-shaped target changed, so no text target ever needs
# to remove anything from this list; see the 2026-08-14 diagnosis below for why every
# OTHER term needed this treatment.
_PRESERVATION_TERMS = (
    "layout", "product", "bottle", "all other text", "colours", "background",
    "lighting", "composition",
)

# Terms to drop from the preservation list for a given edit target - found live
# 2026-08-14 (artifact 1259): a product edit's own change instruction ("Change Product
# ... to: One bottle only... No separate photographic bottle anywhere in the image")
# was followed, in the SAME instruction, by "product, bottle ... must be reproduced
# EXACTLY as it appears ... completely unchanged" - the target named twice with opposite
# instructions, same contradiction class as the five found 12 Aug. The photoreal bottle
# survived the edit. "product" drops BOTH "product" and "bottle" - the bottle IS the
# product being edited, not a separate concept the list can leave standing. Only targets
# whose own name (or a synonym) is a literal term in _PRESERVATION_TERMS need an entry
# here - person_face/person_body/prop/badge/banner/typography/offer/headline/subtext/cta
# never match any of these words, so they are correctly absent and get the list
# unfiltered.
_TARGET_EXCLUDED_PRESERVATION_TERMS = {
    "product": frozenset({"product", "bottle"}),
    "background": frozenset({"background"}),
    "lighting": frozenset({"lighting"}),
}


def build_targeted_edit_instruction(descriptor, operation, new_value, blueprint=None):
    """Dynamic Edit System, Step 3: the ONLY prompt text sent to Gemini for a targeted
    edit. Deliberately NOT the assembled generation prompt, COMPLIANCE_RULES, brand_rules,
    or the stored image_prompt/copy_prompt - the CORE RULE this build exists to satisfy
    (see CLAUDE.md): a stored prompt is a lookup for CURRENT FIELD VALUES only, never
    prose pasted into the edit call - a full creative brief plus "change one word" reads
    as a generation instruction to Gemini and reintroduces the exact drift this system
    replaces. `descriptor` is one src/edit_capability.py control (target/attribute/label/
    current_value); its current_value is used only to phrase the ONE change, never
    restated as a scene description.

    operation is "change" or "remove" - both name exactly one thing to alter and demand
    everything else survive unchanged. blueprint (optional) feeds the ALWAYS-ON brand
    wordmark protection clause - omitted only by callers that predate it; every real
    caller in dashboard.py passes it.

    The preservation list (_PRESERVATION_TERMS) is filtered against descriptor['target']
    via _TARGET_EXCLUDED_PRESERVATION_TERMS (2026-08-14) - never a fixed string - so the
    edit target's own term is never told to change and stay unchanged in the same
    instruction. The wordmark protection clause stays unconditional regardless of
    target - it is never part of this filtering."""
    label = descriptor.get("label") or descriptor.get("attribute") or descriptor.get("target")
    if operation == "remove":
        change = f"REMOVE {label} entirely - it must not appear anywhere in the image afterward."
    else:
        change = f"Change {label} (currently: {descriptor.get('current_value')!r}) to: {new_value}"
    excluded_terms = _TARGET_EXCLUDED_PRESERVATION_TERMS.get(descriptor.get("target") or "", frozenset())
    preservation_list = ", ".join(t for t in _PRESERVATION_TERMS if t not in excluded_terms)
    return (
        "The attached image is FINAL and CORRECT exactly as it appears - this is a "
        "targeted edit to it, not a new composition and not a reinterpretation. Make "
        f"EXACTLY ONE change: {change}. Every other pixel in the image - {preservation_list} "
        "- must be reproduced EXACTLY as it appears in the attached image, completely unchanged."
        + _brand_wordmark_protection_clause(blueprint)
    )


def build_object_removal_instruction(description):
    """Stage 4 (2026-08-17): the fixed delta every per-object remove control sends -
    a standalone template, NOT built from build_targeted_edit_instruction's generic
    change-list machinery (_PRESERVATION_TERMS/_TARGET_EXCLUDED_PRESERVATION_TERMS,
    the "attached image is FINAL and CORRECT" framing, wordmark protection). The exact
    wording is fixed by design, not assembled per-call - same "pre-authored, no
    field-text construction" discipline src/realism_deltas.py already established for
    the bottle-realism edit control, applied here to object removal. `description` is
    edit_capability._object_remove_controls' own current_value (the object's plain-
    English description), the only thing this template ever substitutes."""
    return (
        f"Remove the {description} entirely and close the space naturally with the "
        f"surrounding surface and lighting. Everything else in the image is unchanged."
    )


def build_drift_retry_instruction(base_instruction, descriptor):
    """Dynamic Edit System, Step 4: the ONE automatic retry after a drift-check
    failure (src.drift_check) - appends a tightening note to the SAME base
    instruction, never a fresh prompt, so every other constraint (wordmark
    protection, the single-change framing) still applies unchanged. Called at most
    once per edit request - the caller enforces the one-retry cap, not this function."""
    label = descriptor.get("label") or descriptor.get("attribute") or descriptor.get("target")
    return (
        base_instruction
        + f" NOTE: your previous attempt changed pixels outside the {label} region - "
          f"this retry must change ONLY {label} itself and leave every other pixel, "
          "including areas near it, completely untouched."
    )


def apply_targeted_edit(source_image_bytes, instruction, reference_images=None):
    """Dynamic Edit System, Step 3 Gemini call: contents=[source_image_bytes,
    instruction] by default - nothing else. aspect_ratio is derived explicitly from
    source_image_bytes and always passed on ImageConfig (never omitted - see
    derive_aspect_ratio's own docstring on why omitting it is nondeterministic, not
    merely imprecise). Returns the new draft's raw PNG bytes, or None on failure -
    saving/versioning/DB bookkeeping is the caller's job (dashboard.py), the same
    separation edit_image already uses. Returns values explicitly rather than stashing
    onto a function attribute (see CLAUDE.md's .last_prompt note on why that pattern is
    deliberately not repeated in new code).

    reference_images (2026-08-15): optional list of extra reference photo bytes,
    attached AFTER the draft and framed via the SAME _reference_framing text
    generate_image already uses. No current caller in dashboard.py passes this - the
    product-realism control (2026-08-16) deliberately sends ONLY the v1 draft plus its
    pre-authored delta sentence (src/realism_deltas.py), no reference photos - but the
    capability stays available here for a future targeted edit that genuinely needs
    real geometry beyond what the current draft pixels show."""
    from google.genai import types as genai_types
    aspect_ratio = derive_aspect_ratio(source_image_bytes)
    image_config = (
        genai_types.ImageConfig(aspect_ratio=aspect_ratio, image_size=IMAGE_SIZE)
        if aspect_ratio is not None
        else genai_types.ImageConfig(image_size=IMAGE_SIZE)
    )
    try:
        client = genai.Client(vertexai=True, project="besque-martech", location="global")
        print(f"[apply_targeted_edit] instruction:\n{instruction}")
        contents = [genai_types.Part.from_bytes(data=source_image_bytes, mime_type="image/png")]
        text = instruction
        if reference_images:
            contents += [genai_types.Part.from_bytes(data=img, mime_type="image/png")
                         for img in reference_images]
            text = (
                "FIRST IMAGE ABOVE: the current draft to edit. " +
                _reference_framing(len(reference_images)) + instruction
            )
        contents.append(text)
        response = client.models.generate_content(
            model="gemini-3.1-flash-image",
            contents=contents,
            config=genai_types.GenerateContentConfig(image_config=image_config),
        )
        for part in response.candidates[0].content.parts:
            if part.inline_data is not None:
                return part.inline_data.data
        return None
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


def _regenerate_image_bytes(current_image_bytes, stored_prompt, instruction):
    """The core Gemini call regenerate_from_stored_prompt makes - extracted (2026-08-17,
    object-removal restoration) so a SECOND caller (dashboard.py's object-removal edit
    path, which needs its own versioned filename, not the plain `{stem}_draft.png`
    overwrite this function's own caller below writes) can reuse the identical
    rebuild-and-regenerate mechanism without inheriting that file-writing side effect.
    Returns raw PNG bytes, or None on failure - no file writing, no GCS upload, no
    ad_id/angle_slug parameter, since neither is needed for the Gemini call itself.

    Applies `instruction` as a delta on top of `stored_prompt` (a freshly REBUILT full
    prompt, never a frozen historical one - see regenerate_from_stored_prompt's own
    docstring for why), attached to current_image_bytes as the base image. Aspect ratio
    is derived from current_image_bytes itself, same reasoning as every other caller of
    derive_aspect_ratio in this file."""
    from google.genai import types as genai_types
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
        _regenerate_image_bytes.last_prompt = prompt
        return image_bytes
    except Exception:
        import traceback
        traceback.print_exc()
        return None


def regenerate_from_stored_prompt(current_image_bytes, stored_prompt, instruction, ad_id, angle_slug=None):
    """Regenerate a draft by applying `instruction` as a delta to `stored_prompt` (the
    exact prompt that produced current_image_bytes), never a fresh rebuild from current
    form state. Aspect ratio is derived from current_image_bytes itself, never a
    parameter. Caller must version the outgoing draft before calling this - it only
    overwrites. Returns the new draft path, or None on failure.

    2026-08-17: the actual Gemini call now lives in _regenerate_image_bytes (see its own
    docstring) - this function is a thin wrapper adding the plain-overwrite file write +
    GCS upload, unchanged from before that extraction, so every existing caller sees
    byte-for-byte identical behaviour."""
    stem = _draft_stem(ad_id, angle_slug)
    image_bytes = _regenerate_image_bytes(current_image_bytes, stored_prompt, instruction)
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
    regenerate_from_stored_prompt.last_prompt = getattr(_regenerate_image_bytes, "last_prompt", "")
    return str(dest)


def blueprint_with_object_dropped(blueprint, object_id):
    """A COPY of `blueprint` with exactly one objects[] entry's disposition forced to
    "drop" - never mutates the caller's own blueprint dict (same discipline
    deconstruct.strip_bottle_shape_language/_resolve_object_dispositions already use).

    Object-removal restoration (2026-08-17, Problem 2): the operator's remove control
    must close the scene coherently, not leave a hole - forcing disposition="drop" and
    rebuilding the FULL prompt via build_image_prompt gives _objects_clause a real
    ABSENT line ("close the space naturally with the surrounding surface and lighting")
    plus the closure sentence, the same mechanism a fresh generation already uses to
    remove an object cleanly - never an isolated inpaint instruction with no view of the
    rest of the composition.

    Returns (new_blueprint, target_object) - target_object is the ORIGINAL (unmodified)
    object dict if object_id matched an entry, else None. Callers must treat None as
    "object_id doesn't exist on this blueprint" and reject rather than silently
    proceeding with an unmodified blueprint."""
    objects = blueprint.get("objects") or []
    target_object = None
    new_objects = []
    for obj in objects:
        obj = obj or {}
        if obj.get("object_id") == object_id:
            target_object = obj
            new_objects.append({**obj, "disposition": "drop"})
        else:
            new_objects.append(obj)
    new_blueprint = dict(blueprint)
    new_blueprint["objects"] = new_objects
    return new_blueprint, target_object
