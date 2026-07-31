"""Hard content-safety block (Prompt 4, Item 3).

A hemorrhoid treatment ad with anatomical before/after illustrations was cloned into a
Besque draft with the headline swapped - nothing stopped it, because nothing asked what
the reference actually was. Unlike the output critic (src/output_critic.py) or the
bundle-format flag, this is NOT a judgment call surfaced for a human to weigh: a
medical/clinical/intimate-health/anatomically-explicit reference must never be cloned at
all, so this blocks the ad BEFORE generation ever starts, not after.

Deliberately reuses signals ALREADY extracted by deconstruct.py's classifier
(product_category.signals, visual.subject, hook, format, angle) rather than adding a new
blueprint field - deconstruct.py's BLUEPRINT_PROMPT was strengthened to explicitly name
medical/clinical/anatomical content in product_category.signals when it's present, so the
keyword scan below has real classifier-written text to match against, not just a guess
made from nothing."""

MEDICAL_KEYWORDS = (
    "hemorrhoid", "haemorrhoid", "anatomical", "anatomy", "medical condition",
    "medical treatment", "medical procedure", "clinical trial", "clinical diagram",
    "clinical treatment", "diagnosis", "diagnostic", "prescription", "surgical", "surgery",
    "varicose vein", "incontinence", "intimate-health", "intimate health", "vaginal",
    "genital", "erectile", "menstrual disorder", "digestive tract", "internal organ",
    "disease", "disorder", "symptom of", "patient", "physician",
)

# product_category values that don't already mean "an ordinary retail product is being
# sold" - a medical-keyword hit alongside one of these is the combination that blocks.
# Alongside a normal skincare/body_oil classification, a keyword hit is far more likely a
# loosely-used word (e.g. "treatment" in "hair treatment") than a genuine medical
# reference, so it does NOT block on its own.
_NON_PRODUCT_CATEGORIES = ("not_product", "other", None, "")


def _medical_signal_text(blueprint):
    """Every already-extracted text field worth scanning, joined into one lowercase
    string. Reads only what deconstruct.py already writes - no new blueprint field."""
    blueprint = blueprint or {}
    parts = []
    visual = blueprint.get("visual") or {}
    if visual.get("subject"):
        parts.append(visual["subject"])
    product_category = blueprint.get("product_category") or {}
    parts.extend(product_category.get("signals") or [])
    hook = blueprint.get("hook") or {}
    if hook.get("headline_structure"):
        parts.append(hook["headline_structure"])
    if blueprint.get("format"):
        parts.append(blueprint["format"])
    if blueprint.get("angle"):
        parts.append(blueprint["angle"])
    return " ".join(str(p) for p in parts).lower()


def hard_block_reason(blueprint):
    """Return a human-readable reason string if this blueprint must be hard-blocked
    before generation, else None.

    This is a binary skip, not a confidence-scored flag like the output critic - it only
    fires on an actual keyword hit combined with a non-product-like category, never a
    guess. product_category=="not_product"/"other" combined with a medical signal, per
    the spec; a medical-sounding word alongside an ordinary product category (e.g.
    body_oil) is treated as a loose word choice, not a real medical reference."""
    text = _medical_signal_text(blueprint)
    hit = next((kw for kw in MEDICAL_KEYWORDS if kw in text), None)
    if not hit:
        return None
    category = ((blueprint or {}).get("product_category") or {}).get("category")
    if category not in _NON_PRODUCT_CATEGORIES:
        return None
    return (
        f"blueprint indicates a medical/clinical/intimate-health/anatomically explicit "
        f"subject (matched {hit!r}, product_category={category!r}) - hard-blocked before "
        f"generation, not a judgment call."
    )
