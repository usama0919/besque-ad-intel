"""Dynamic Edit System - Step 2: derive editable controls from ONE artifact, at request
time. No fixed control list anywhere in this module or its caller - every control is
derived fresh from the artifact's own blueprint + copy columns; a control with no
structural basis is simply absent (fail closed), never guessed, defaulted, or hard-coded.

CORE DISTINCTION this whole module exists to enforce (see CLAUDE.md's Dynamic Edit
System notes): blueprint describes the COMPETITOR reference ad - it is STRUCTURE only
(does a headline-shaped text block exist at all, does a face appear, how many products).
blueprint.text_purpose/testimonial_zones hold the COMPETITOR's own strings verbatim and
must NEVER be read as a control's current_value. The artifact's own copy columns
(generated_copy.headline/primary_text/cta/image_subtext, artifacts.offer_text) are
Besque's ADAPTED values and are the ONLY source current_value is ever read from. Where
our adaptation dropped or never populated a field (no headline was generated, no offer
was supplied this run), the control is absent even if the competitor reference had one -
we edit what WE rendered, not what the reference showed.

Derived from the artifact dict shape dedupe.get_artifact_by_id returns (blueprint,
generated_copy, offer_text, text_in_image, ...). Zero image calls, zero DB calls -
pure function of the dict already fetched, unit-testable with plain fixtures.
"""
import re

from src import realism_deltas

# HUMAN_BODY_KEYWORDS/_is_human_body_element DELETED 2026-08-17: both existed only to
# route a scene_elements entry to target="person_body" vs target="prop" in
# _scene_element_controls (below, also deleted) - scene_elements no longer exists
# (schema/blueprint.schema.json - blueprint.objects replaces it with a real, structured
# `kind` field, e.g. "person", instead of a keyword guess against free text). See
# _object_remove_controls for the replacement.

# Rule 10's own floor (generate_image_prompt._RULE_10_SUBJECT_AGE): 45-60 bracket, grey/
# silver hair, visible facial lines, mature skin texture required. The edit engine (Step
# 3) clamps any requested age edit to this floor - never younger - this constant is the
# single source both this module's descriptor and the engine's clamp read from.
RULE_10_AGE_FLOOR = 45


def _blueprint(artifact):
    return artifact.get("blueprint") or {}


def _generated_copy(artifact):
    return artifact.get("generated_copy") or {}


# purpose values that actually shape headline/subtext-style copy. Deliberately EXCLUDES
# "other" and "testimonial" - live evidence (artifact 1251, 2026-08-14): its only
# text_purpose entries were two purpose="other" rows carrying the COMPETITOR's OWN
# wordmark/tagline text ("Crêpe Erase®", "by THE BODY FIRM™") and two purpose="testimonial"
# rows (the quote + attribution). "any entry at all" (the old check) read those "other"
# rows as "a headline zone exists," so the headline control was offered even though
# nothing in this reference was ever a headline-shaped text block - Besque's own
# generated_copy.headline was never rendered into the pixels at all (the image showed
# the BESQUE wordmark instead), and editing it asked Gemini to change text that was never
# there, which is what caused it to overwrite the wordmark region instead. "cta"/"offer"
# are excluded too - each already has its own dedicated detector (_cta_zone_exists,
# artifacts.offer_text) and must not double-count here.
_HEADLINE_SHAPED_PURPOSES = {"problem_hook", "efficacy_claim", "product_description"}


def _has_headline_shaped_text_purpose(blueprint):
    return any(
        (tp or {}).get("purpose") in _HEADLINE_SHAPED_PURPOSES
        for tp in blueprint.get("text_purpose") or []
    )


def _structural_zone_types(blueprint):
    return {(z or {}).get("zone_type") for z in (blueprint.get("structural_zones") or [])}


def get_brand_wordmark_zone(blueprint):
    """The structural_zones entry with zone_type=="brand_wordmark", or None. Used by the
    edit engine (generate_image_prompt.build_targeted_edit_instruction) to name the
    wordmark's own position in the ALWAYS-ON protection clause every targeted edit
    carries, regardless of what's being edited.

    ORPHANED 2026-08-17: structural_zones no longer exists for a blueprint deconstructed
    under the new objects schema (schema/blueprint.schema.json) - this always returns
    None for a new blueprint, same as it already did for any legacy blueprint with no
    brand_wordmark zone. Not rewired to blueprint.objects: a competitor logo object
    there is never Besque's own wordmark position (see _object_remove_controls's own
    docstring - blueprint.objects describes the reference, never the drafted image;
    Besque's wordmark is ADDED by brand_rules() rule 9, never tracked as an object
    here), so there is no equivalent signal to read instead. The protection clause this
    feeds degrades to its own generic wording with no specific position named -
    graceful, not a crash."""
    for z in blueprint.get("structural_zones") or []:
        if (z or {}).get("zone_type") == "brand_wordmark":
            return z
    return None


def _cta_zone_exists(blueprint):
    if "cta" in _structural_zone_types(blueprint):
        return True
    return any((tp or {}).get("purpose") == "cta" for tp in blueprint.get("text_purpose") or [])


def _sub_line_or_body_copy_zone_exists(blueprint):
    return bool(_structural_zone_types(blueprint) & {"sub_line", "body_copy"})


def _headline_control(artifact, blueprint):
    """Gated on BOTH text_in_image (was this run supposed to bake text into the pixels
    at all) AND a genuinely headline-shaped text_purpose entry (was there ever a
    headline-like zone in the reference for Besque's headline to occupy). Neither alone
    is sufficient - artifact 1251 had text_in_image=True with generated_copy.headline
    populated, and STILL never rendered a headline (see _has_headline_shaped_text_purpose's
    own comment for the full trace). Fail closed: missing either signal omits the control."""
    if not artifact.get("text_in_image"):
        return None
    if not _has_headline_shaped_text_purpose(blueprint):
        return None
    value = _generated_copy(artifact).get("headline")
    if not value:
        return None
    return {
        "target": "headline", "attribute": "text", "label": "Headline",
        "current_value": value, "allowed_ops": ["change"],
        "blueprint_path": "text_in_image + text_purpose[].purpose in "
                          f"{sorted(_HEADLINE_SHAPED_PURPOSES)} (structure only) + generated_copy.headline",
    }


def _subtext_control(artifact, blueprint):
    """Same two-signal gate as _headline_control - see its docstring. A sub_line/
    body_copy structural zone is an equally valid basis for subtext (that zone type IS
    subtext-shaped by definition, unlike text_purpose's ambiguous "other"), so either
    that OR a headline-shaped text_purpose entry satisfies the structural half of the gate."""
    if not artifact.get("text_in_image"):
        return None
    if not (_has_headline_shaped_text_purpose(blueprint) or _sub_line_or_body_copy_zone_exists(blueprint)):
        return None
    value = _generated_copy(artifact).get("image_subtext")
    if not value:
        return None
    return {
        "target": "subtext", "attribute": "text", "label": "Subtext",
        "current_value": value, "allowed_ops": ["change", "remove"],
        "blueprint_path": "text_in_image + (structural_zones[].zone_type in (sub_line, body_copy) / "
                          "text_purpose[].purpose headline-shaped) + generated_copy.image_subtext",
    }


def _cta_control(artifact, blueprint):
    value = _generated_copy(artifact).get("cta")
    if not value or not _cta_zone_exists(blueprint):
        return None
    return {
        "target": "cta", "attribute": "text", "label": "CTA",
        "current_value": value, "allowed_ops": ["change"],
        "blueprint_path": "structural_zones[].zone_type=cta / text_purpose[].purpose=cta "
                          "(structure only) + generated_copy.cta",
    }


def _offer_control(artifact):
    """current_value is ALWAYS artifacts.offer_text - the per-run operator input, never
    blueprint.offer.value (the competitor's own offer) - see this module's own docstring.
    blueprint.offer existing or not is not even consulted: an operator can supply
    offer_text for a run whose reference had no offer zone at all (a per-run free-text
    input, not derived from the reference - see CLAUDE.md's Prompt B team decisions),
    so gating this control on blueprint.offer would incorrectly hide a real, populated
    offer_text."""
    offer_text = artifact.get("offer_text")
    if not offer_text:
        return None
    return {
        "target": "offer", "attribute": "text", "label": "Offer",
        "current_value": offer_text, "allowed_ops": ["change", "remove"],
        "blueprint_path": "artifacts.offer_text (per-run operator input, not blueprint.offer)",
    }


def _person_face_controls(blueprint):
    """Fail-closed (2026-08-14, same principle as _product_control's agreement
    requirement): NEITHER Age nor Expression has a real per-artifact stored value
    anywhere in the data model today, so neither is emitted, regardless of
    face_present.has_face.

    Age previously reported rule 10's own prompt-text floor ("not individually
    tracked; rule 10 floor is 45-60...") as if it were this artifact's current_value -
    that is RULE description text, not an observed per-artifact fact, and it was being
    interpolated into a live delta edit instruction as "currently: not individually
    tracked...", which is not a real observation to hand Gemini. Expression's
    current_value was already None, which still rendered a live Apply button that would
    have submitted new_value="" - never emit a control whose current_value is None.

    Both are absent until a real per-artifact age/expression field exists to read a
    genuine current_value from - not guessed, not defaulted, not filled with rule text."""
    return []


def _object_remove_controls(blueprint):
    """Stage 4 (2026-08-17): one REMOVE-only control per object row in blueprint.objects
    - no hardcoded list, derived fresh from the artifact's own blueprint every call, the
    same pattern every other control in this module already follows. REPLACES
    _scene_element_controls (scene_elements no longer exists - blueprint.objects
    replaces it, see deconstruct.py/generate_image_prompt._objects_clause).

    Deliberately REMOVE-only, never "change" - the task this control was built for asks
    for a remove control per object, not a rename/re-describe operation; changing an
    object's own identity is a different, unspecified capability this does not attempt.

    No wordmark/brand-mark exclusion, unlike the old scene_elements-based
    _scene_element_controls: blueprint.objects describes the COMPETITOR reference,
    never the drafted image - Besque's own wordmark is never one of these rows (it is
    ADDED to the draft by brand_rules() rule 9, never tracked as an object here), so
    there is no "accidentally target Besque's own wordmark" risk this needs to guard
    against the way the old prop/person_body controls did. object_id is the stable
    identifier the delta instruction and drift_check's removal zone both key off -
    entries missing either object_id or description are skipped rather than offered
    with a blank label, fail-closed by the same convention every other control here uses."""
    controls = []
    for obj in blueprint.get("objects") or []:
        obj = obj or {}
        object_id = obj.get("object_id")
        description = obj.get("description")
        if not object_id or not description:
            continue
        controls.append({
            "target": "object", "attribute": object_id, "label": description,
            "current_value": description, "allowed_ops": ["remove"],
            "blueprint_path": "objects[].object_id",
        })
    return controls


def _product_control(artifact, blueprint):
    """Requires AGREEMENT between two independent signals, fails closed otherwise -
    exact predicate: layout_detail.product_count > 0 AND
    artifact.element_provenance.product == "substituted".

    Found live (2026-08-14): blueprint.layout_detail.product_count alone describes the
    COMPETITOR reference's structure, never what BESQUE's own draft actually rendered -
    artifact 1250 has product_count=0 (the reference had no product) but
    include_product=True and element_provenance.product="added" (Besque was SUPPOSED
    to add one) - yet the rendered draft has no bottle anywhere (confirmed by direct
    inspection), a generation-side bug logged separately in CLAUDE.md, not fixed here.

    "added" is deliberately NEVER trusted alone, even now that its live failure is
    known: unlike "substituted" (an existing structural zone is replaced - the
    reference already proves a product-shaped region exists to substitute into),
    "added" has no independent structural evidence backing it at all - the reference
    had NOTHING there. Requiring product_count > 0 as well means this control is only
    ever offered when the COMPETITOR reference itself showed a product AND Besque's own
    bookkeeping confirms it substituted (not invented) one - the one path with two
    independent signals in agreement, not just one field taken on faith. include_product
    is deliberately not part of this predicate either - it is operator INTENT for the
    run, not evidence of what was actually rendered, and 1250 shows intent and
    provenance can agree with each other while both being wrong about the pixels."""
    layout_detail = blueprint.get("layout_detail") or {}
    count = layout_detail.get("product_count")
    provenance = (artifact.get("element_provenance") or {}).get("product")
    if not count or count <= 0 or provenance != "substituted":
        return None
    return {
        "target": "product", "attribute": "placement", "label": "Product",
        "current_value": f"{int(count)} bottle(s) - identity/label fixed, see products.visual_description",
        "allowed_ops": ["change"],
        "blueprint_path": "layout_detail.product_count + artifacts.element_provenance.product",
    }


def _product_realism_control(artifact, blueprint):
    """Bottle-realism-only control (2026-08-16, superseding the 2026-08-15 version):
    re-render the Besque bottle's RENDERING TREATMENT ONLY over a FIXED set of four
    values (realism_deltas.REALISM_VALUES) - never shape, dimensions, proportions,
    label content, or hardware design, which realism_deltas' pre-authored per-value
    delta sentences explicitly hold fixed regardless of which value is chosen.

    `options` carries the fixed picker set verbatim from realism_deltas.REALISM_VALUES
    - the single source of truth for what the modal renders as segments - so this
    descriptor and the delta text it will be edited with can never drift apart.
    `current_value` is `blueprint.production_style.style` (or "unspecified") AS
    STORED, never coerced to one of `options` - a value that predates the 2026-08-11
    enum rename (e.g. "ugc_native" itself, or the dropped "hybrid") or one that
    otherwise doesn't match any of the four exactly is the caller's job to show as a
    "current: <value>" chip, never this function's job to normalise or default to
    options[0].

    Same fail-closed agreement _product_control (above) requires - product_count > 0
    AND element_provenance.product == "substituted" - for the identical reason: this
    control only makes sense when there's a real, confirmed Besque product in the
    pixels to re-render, never a guess. Distinct attribute from Product/placement
    (target="product", attribute="realism" vs. attribute="placement") so both can
    coexist as separate controls grouped under the same "product" section in the UI."""
    layout_detail = blueprint.get("layout_detail") or {}
    count = layout_detail.get("product_count")
    provenance = (artifact.get("element_provenance") or {}).get("product")
    if not count or count <= 0 or provenance != "substituted":
        return None
    current_style = (blueprint.get("production_style") or {}).get("style") or "unspecified"
    return {
        "target": "product", "attribute": "realism", "label": "Product — Realism",
        "current_value": current_style,
        "options": list(realism_deltas.REALISM_VALUES),
        "allowed_ops": ["change"],
        "blueprint_path": "layout_detail.product_count + artifacts.element_provenance.product "
                          "+ blueprint.production_style.style",
    }


def _background_control(blueprint):
    background_type = (blueprint.get("layout_detail") or {}).get("background_type")
    if not background_type:
        return None
    return {
        "target": "background", "attribute": "type", "label": "Background",
        "current_value": background_type, "allowed_ops": ["change"],
        "blueprint_path": "layout_detail.background_type",
    }


def _lighting_control(blueprint):
    scene_lighting = (blueprint.get("visual") or {}).get("scene_lighting")
    if not scene_lighting:
        return None
    parts = [f"{k}: {v}" for k, v in scene_lighting.items() if v]
    if not parts:
        return None
    return {
        "target": "lighting", "attribute": "scene_lighting", "label": "Lighting",
        "current_value": "; ".join(parts), "allowed_ops": ["change"],
        "blueprint_path": "visual.scene_lighting",
    }


def _typography_control(blueprint):
    typography = blueprint.get("typography") or {}
    fields = {k: v for k, v in typography.items() if v}
    if not fields:
        return None
    current_value = ", ".join(f"{k}: {v}" for k, v in fields.items())
    return {
        "target": "typography", "attribute": "style", "label": "Typography",
        "current_value": current_value, "allowed_ops": ["change"],
        "blueprint_path": "typography.*",
    }


def _badge_banner_controls(blueprint):
    layout_detail = blueprint.get("layout_detail") or {}
    controls = []
    if layout_detail.get("has_corner_badge"):
        controls.append({
            "target": "badge", "attribute": "corner_badge", "label": "Corner badge",
            "current_value": True, "allowed_ops": ["change", "remove"],
            "blueprint_path": "layout_detail.has_corner_badge",
        })
    if layout_detail.get("has_bottom_banner"):
        controls.append({
            "target": "banner", "attribute": "bottom_banner", "label": "Bottom banner",
            "current_value": True, "allowed_ops": ["change", "remove"],
            "blueprint_path": "layout_detail.has_bottom_banner",
        })
    return controls


def legacy_scene_summary(blueprint):
    """Stage 5 back-compat (2026-08-17): a READ-ONLY text summary of the OLD
    scene_elements/structural_zones inventory, for a blueprint deconstructed before the
    objects schema existed - ~300 existing artifact rows have no `objects` key at all
    (no backfill, no migration - see the task this was built for). This is display data
    ONLY, never a control: _object_remove_controls is the only thing that ever offers a
    real edit for scene/object content, and it only fires when `blueprint.objects` is
    actually present. Returns [] when `objects` IS present (a current-schema blueprint
    has nothing legacy to summarise) or when neither legacy field has anything in it -
    the caller (dashboard.py) treats an empty list as "nothing to show", never an error."""
    if blueprint.get("objects"):
        return []
    lines = []
    for e in blueprint.get("scene_elements") or []:
        e = e or {}
        element = e.get("element")
        if element:
            role = e.get("role") or ""
            lines.append(f"{element} - {role}" if role else element)
    for z in blueprint.get("structural_zones") or []:
        z = z or {}
        zone_type = z.get("zone_type")
        if zone_type:
            lines.append(f"{zone_type} at {z.get('position') or 'unknown position'}")
    return lines


def derive_edit_capabilities(artifact):
    """The complete, dynamically-derived control set for ONE artifact. artifact is the
    dict shape dedupe.get_artifact_by_id returns (or any dict with the same keys - tests
    pass plain fixtures). Order is stable (text controls, then person, then product/
    scene) but carries no meaning the UI should rely on beyond display grouping.

    Fails closed by construction: every _*_control function returns None (contributing
    nothing) the moment its one required field is missing/empty/falsy - there is no
    default branch anywhere in this module that fabricates a control from an ambiguous
    or partial field."""
    blueprint = _blueprint(artifact)
    controls = []
    for maker in (
        lambda: _headline_control(artifact, blueprint),
        lambda: _subtext_control(artifact, blueprint),
        lambda: _cta_control(artifact, blueprint),
        lambda: _offer_control(artifact),
        lambda: _product_control(artifact, blueprint),
        lambda: _product_realism_control(artifact, blueprint),
        lambda: _background_control(blueprint),
        lambda: _lighting_control(blueprint),
        lambda: _typography_control(blueprint),
    ):
        descriptor = maker()
        if descriptor:
            controls.append(descriptor)
    controls.extend(_person_face_controls(blueprint))
    controls.extend(_badge_banner_controls(blueprint))
    controls.extend(_object_remove_controls(blueprint))
    return controls


def clamp_person_age(new_value):
    """Dynamic Edit System, Step 3 house rule: "Person age edits clamp to the rule 10
    floor (older only, never younger)." Returns (resolved_value: str, was_clamped: bool).

    An explicit request to look YOUNGER (the word "young"/"younger"/"teen(age)") is
    always clamped, regardless of any number in the same text - rule 10's floor is never
    moved younger under any phrasing. A request naming a bare number below
    RULE_10_AGE_FLOOR (e.g. "make her 30") is clamped up to the floor. Anything else
    (a request to look older, or a request with no age-shaped content at all, e.g.
    "more relaxed expression" landing on this function by caller error) passes through
    unchanged - this function only ever moves a value UP to the floor, never rejects
    the edit outright, matching "clamp", not "reject", in the house rule's own wording."""
    text = (new_value or "").strip()
    lowered = text.lower()
    if re.search(r"\byoung(er)?\b", lowered) or re.search(r"\bteen(age)?\b", lowered):
        return (
            f"older only, within the existing rule 10 floor ({RULE_10_AGE_FLOOR}-60) - a "
            "younger subject was requested but is never permitted",
            True,
        )
    match = re.search(r"\b(\d{1,3})\b", lowered)
    if match and int(match.group(1)) < RULE_10_AGE_FLOOR:
        return (
            f"{RULE_10_AGE_FLOOR} (clamped up from the requested {match.group(1)} - rule "
            "10's floor is never moved younger)",
            True,
        )
    return (text, False)


def find_control(controls, target, attribute):
    """The one descriptor matching (target, attribute) from a derive_edit_capabilities()
    list, or None. The edit engine (Step 3) uses this to validate an incoming edit
    request against what's ACTUALLY derivable for this artifact right now, never against
    a fixed/remembered list - a request naming a target/attribute this call doesn't
    return is rejected, by construction, without any separate allow-list to maintain."""
    for c in controls:
        if c.get("target") == target and c.get("attribute") == attribute:
            return c
    return None
