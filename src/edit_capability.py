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

# Scene_elements entries matching one of these (case-insensitive, WORD-boundary - never a
# raw substring test) are body-part shaped, not props - they route to a "person_body"
# control instead of a "prop" control, per the explicit rule: face_present.has_face==false
# must still surface a hand/skin control from scene_elements, never silently drop the
# element or mis-file it as a prop. Word-boundary matching is load-bearing, not cosmetic:
# a live artifact's own scene_elements carried the string "rocky shoreline visible in
# BACKGROUND" - plain substring containment misrouted it to person_body purely because
# "back" sits inside "background", with no actual body part depicted at all.
# per the explicit rule: face_present.has_face==false must still surface a hand/skin
# control from scene_elements, never silently drop the element or mis-file it as a prop.
# Deliberately excludes "face" itself - a face is governed exclusively by face_present
# (the person_face age/expression controls below), never by a scene_elements entry, so a
# stray "face" element string here would otherwise create a redundant second control.
HUMAN_BODY_KEYWORDS = (
    "hand", "hands", "arm", "arms", "leg", "legs", "torso", "skin", "body", "neck",
    "shoulder", "shoulders", "waist", "hip", "hips", "thigh", "thighs", "foot", "feet",
    "finger", "fingers", "hair", "back", "stomach", "abdomen", "chest", "wrist", "wrists",
    "ankle", "ankles", "knee", "knees", "elbow", "elbows",
)

_HUMAN_BODY_PATTERN = re.compile(
    r"\b(?:" + "|".join(HUMAN_BODY_KEYWORDS) + r")\b", re.IGNORECASE
)


def _is_human_body_element(element):
    """Word-boundary match against HUMAN_BODY_KEYWORDS - never plain substring
    containment (`kw in element.lower()`), which false-positives on ordinary words
    that merely CONTAIN a keyword ("background" contains "back", "chip" would contain
    nothing here but the principle is the same risk for "hip"/"arm"/etc.)."""
    return bool(_HUMAN_BODY_PATTERN.search(element or ""))

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


# Never target the brand wordmark rule (2026-08-14): on artifact 1251, a headline edit
# whose "current value" didn't actually exist in the pixels (see _has_headline_shaped_
# text_purpose's comment) caused Gemini to overwrite the BESQUE wordmark region instead -
# an edit must never be able to touch the wordmark, directly or as a side effect.
_WORDMARK_KEYWORDS = ("wordmark", "logo", "brand mark", "brandmark", "besque")
_WORDMARK_PATTERN = re.compile(r"\b(?:" + "|".join(_WORDMARK_KEYWORDS) + r")\b", re.IGNORECASE)


def _is_brand_wordmark_element(element):
    """Excludes any scene_elements entry that names the brand wordmark/logo from ever
    becoming its own editable prop/person_body control - defense in depth alongside the
    delta-instruction-level protection in generate_image_prompt.build_targeted_edit_
    instruction. No control in this registry targets "wordmark" directly today, but this
    stops a future scene_elements entry describing it from silently becoming editable."""
    return bool(_WORDMARK_PATTERN.search(element or ""))


def get_brand_wordmark_zone(blueprint):
    """The structural_zones entry with zone_type=="brand_wordmark", or None. Used by the
    edit engine (generate_image_prompt.build_targeted_edit_instruction) to name the
    wordmark's own position in the ALWAYS-ON protection clause every targeted edit
    carries, regardless of what's being edited."""
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


def _scene_element_controls(blueprint):
    """One control per scene_elements entry, labelled with that entry's OWN `element`
    string - never a generic grouped control. Human-body-shaped entries (see
    HUMAN_BODY_KEYWORDS) route to target="person_body" instead of target="prop"; both
    still get their own descriptor per entry, one each, same as every other entry.
    essential==true entries keep "remove" in allowed_ops (removal isn't blocked - the
    operator may have a good reason) but carry an explicit warning, surfaced by the UI,
    never silently allowed through as if it were any other prop."""
    controls = []
    for entry in blueprint.get("scene_elements") or []:
        entry = entry or {}
        element = entry.get("element")
        if not element:
            continue
        if _is_brand_wordmark_element(element):
            continue
        is_human = _is_human_body_element(element)
        essential = bool(entry.get("essential"))
        descriptor = {
            "target": "person_body" if is_human else "prop",
            "attribute": element,
            "label": element,
            "current_value": entry.get("role") or element,
            "allowed_ops": ["change", "remove"],
            "blueprint_path": "scene_elements[].element",
        }
        if essential:
            descriptor["essential"] = True
            descriptor["warning"] = (
                f"\"{element}\" is marked essential to this composition (scene_elements."
                "essential=true) - removing it may substantially alter the ad."
            )
        controls.append(descriptor)
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
        lambda: _background_control(blueprint),
        lambda: _lighting_control(blueprint),
        lambda: _typography_control(blueprint),
    ):
        descriptor = maker()
        if descriptor:
            controls.append(descriptor)
    controls.extend(_person_face_controls(blueprint))
    controls.extend(_badge_banner_controls(blueprint))
    controls.extend(_scene_element_controls(blueprint))
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
