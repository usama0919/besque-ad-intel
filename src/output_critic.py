"""Output critic (Prompt 4, Item 1): a SAFETY control, not a quality feature.

Every guardrail on the image path up to this point is prompt-only - nothing inspects what
Gemini actually produced. Four rounds of increasingly explicit prompt instructions failed
to stop offer/claim text leaking from reference ads in edit mode; the pattern is
conclusive - prompt-only guardrails do not reliably bind in edit mode. This module is the
response: after generation, send the draft image back to Claude with the same rules it was
supposed to follow and ask what it actually violated.

Non-blocking by design, and this is load-bearing, not incidental: runs AFTER
dedupe.save_artifact (never before - a slow/failed check must never lose a draft), and
check_draft() never raises. A transient failure retries once (2026-08-12, with a
JSON-escaping nudge - see MAX_CRITIC_ATTEMPTS); if it STILL fails, check_draft returns
CRITIC_CHECK_FAILED_FINDING - a synthetic HIGH-confidence finding - rather than silently
passing a draft that was never actually reviewed. check_draft no longer returns None at
all: a check that could not run twice in a row is marked, exactly like a genuine
violation, not treated as "clean." A flag is something a human sees and decides on; this
module surfaces, it never acts - no auto-reject, no auto-regenerate.
"""
import os
import re
import logging
import anthropic

from src import json_response
from src.deconstruct import JSON_ESCAPE_SYSTEM

CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")

# Total vision-call attempts for one check_draft call: the original call plus exactly one
# retry - mirrors deconstruct.py's _MAX_DECONSTRUCT_ATTEMPTS shape and reuses the SAME
# JSON_ESCAPE_SYSTEM nudge (imported above, not re-typed) rather than a second copy of it.
MAX_CRITIC_ATTEMPTS = 2

# Returned instead of None when the critic's response fails to parse even after the retry
# (2026-08-12, live incident: "output critic failed (JSONDecodeError: Invalid \escape),
# draft left unflagged" on ad 926730636855002's attempt 2 - a parse failure silently
# passed the draft with no signal anywhere it was never actually reviewed). Shaped as an
# ordinary HIGH-confidence finding, not a special sentinel, so has_high_confidence()
# reads it as a real violation with zero changes needed to pipeline.process_ad's existing
# retry-then-mark-failed-review logic - a check that genuinely could not run twice in a
# row gets marked exactly like a real HIGH violation would, never silently passed.
CRITIC_CHECK_FAILED_FINDING = [{
    "category": "critic_check_failed",
    "description": (
        "The output critic's response could not be parsed, even after one retry with a "
        "JSON-escaping nudge - this draft could not be automatically reviewed and needs "
        "manual review. This is a check-mechanism failure, not a defect actually observed "
        "in the image."
    ),
    "confidence": "high",
}]

log = logging.getLogger("output_critic")

# These are the categories that have ACTUALLY shipped in real drafts despite four rounds
# of prompt-only guardrails - a cautious "medium" burying a repeat of the exact failure
# that prompted this module is worse than a false positive, so the prompt tells Claude to
# default these to high confidence rather than hedge.
HIGH_CONFIDENCE_BY_DEFAULT = (
    "unauthorised offer", "scarcity claim", "promo code", "efficacy claim", "testimonial",
    "product category mismatch", "regulatory text carried over from the reference",
    "product register mismatch", "nudity or sexualised content", "subject age violation",
    "subject identity",
)

CRITIC_SYSTEM = (
    "You are a compliance and brand-safety reviewer for Besque, a natural body-oil "
    "skincare brand for women 40+. You will be shown a GENERATED advertisement image and "
    "the brand/compliance rules it was supposed to follow. Your job is ONLY to report "
    "what the image ACTUALLY shows that violates those rules - you are not generating, "
    "editing, or suggesting anything. Return ONLY valid JSON of the exact shape "
    "{\"violations\": [{\"category\": \"...\", \"description\": \"...\", \"confidence\": "
    "\"high\"|\"medium\"|\"low\"}, ...]} - an empty violations array if you find nothing.\n\n"
    "Check specifically for:\n"
    "- NUDITY OR SEXUALISED CONTENT (C6): bare breasts/chest, genitals, or a sexualised "
    "pose, camera framing, or narrative context in the GENERATED image itself - "
    "regardless of what the reference ad showed or implied, and regardless of whether "
    "the reference's own human subject was nude or sexualised. This is a check on OUR "
    "OUTPUT only, never a judgment on the reference: depicting bare skin or body areas "
    "relevant to product application (legs, torso, underarms) is expected for a body-oil "
    "ad and is NOT itself a violation - only actual nudity or sexualisation in what was "
    "generated is\n"
    "- a competitor logo, seal, badge, or brand mark anywhere in the image (rule 9)\n"
    "- a competitor brand or product name in any rendered text (rules 1-2). If the "
    "Besque product's own documented label/bottle design is supplied below, judge the "
    "product's label text against THAT, not against rule 1's bare wording alone: label "
    "text/sub-lines/claims that match the documented design are the REAL label, not a "
    "violation, even if they say more than just the bare product name - only label text "
    "matching NEITHER the exact product name NOR the documented design is a genuine "
    "violation of this category\n"
    "- a prop, ingredient, or scene element carried over from the competitor's reference "
    "that visibly implies an ingredient or property this Besque product does not have or "
    "claim - e.g. the reference's almonds, citrus fruit, or another named ingredient shown "
    "in the scene when the product's actual ingredients (supplied below, if given) don't "
    "include it. This is a NEW, flag-only category: nothing in the generator's own prompt "
    "bans this yet, so report it for a human to weigh, don't assume it's necessarily wrong\n"
    "- an unauthorised offer, price, discount, promo code, scarcity, or stock-count claim "
    "(the OFFER instruction). If an authorised offer is supplied below, judge offer text "
    "in the image against THAT exact wording: text matching the authorised offer is the "
    "real, approved offer, not a violation. If NO offer is supplied below (empty or "
    "absent), NONE is authorised - any offer, price, discount, promo code, scarcity, or "
    "stock-count content appearing anywhere in the image is a violation of this category, "
    "full stop, even if the reference ad had one\n"
    "- a quantified efficacy claim (a percentage, ratio, or \"X% more\"-style claim) not "
    "explicitly authorised below (C3, the EFFICACY CLAIMS instruction)\n"
    "- a testimonial, quote, or star rating (C2). If an authorised testimonial is supplied "
    "below, judge quotes/attributions in the image against THAT: text matching the "
    "authorised quote and/or attribution is the real, approved review, not a violation, "
    "even though it's a quote with a name attached - only a quote or attribution matching "
    "NEITHER the authorised testimonial NOR nothing (when none was authorised at all) is a "
    "genuine violation of this category\n"
    "- a Besque product present when none was authorised, or more than one product where "
    "only one was authorised (rule 7)\n"
    "- the product shown or described as any category OTHER than a body oil - a mist, "
    "spray, cream, serum, lotion, gel, balm, or any other non-oil category, on the label, "
    "the packaging shape, or in any rendered text - regardless of what category the "
    "competitor's OWN reference ad sells (rule 5). The reference supplies composition, "
    "layout, and styling only, never product identity: whatever the competitor sells, this "
    "is always a Besque body oil ad, and a mismatched reference category is never a reason "
    "to relax this or read it as intentional\n"
    "- a product-derived substance (a drip, pour, pool, or smear) in the wrong colour for "
    "the actual Besque product described below (the product-substance instruction)\n"
    "- an empty graphic container (a badge, oval, bubble, banner, or ribbon) with no "
    "content\n"
    "- garbled or illegible rendered text\n"
    "- text rendered when none was authorised below, or missing when it was authorised "
    "(rule 6). If an authorised headline and/or supporting text is supplied below, judge "
    "rendered in-scene text against THOSE exact strings: text matching the authorised "
    "headline or subtext is correctly rendered, not a violation - report this category "
    "only for text matching NEITHER authorised string (an unauthorised addition) or for "
    "authorised text that is genuinely absent from the image (not rendered anywhere), "
    "never for authorised text that IS rendered as given\n"
    "- regulatory, legal, or medical disclaimer text carried over from the reference - an "
    "FDA/dietary-supplement statement, drug facts, a clinical-trial footnote, "
    "country-specific legal text, or a competitor's own T&Cs - on ANY product, even one "
    "the disclaimer plainly doesn't apply to (the disclaimer-removal instruction). Also "
    "flag a dangling asterisk or footnote marker left with no referent after disclaimer "
    "text was removed - that's its own defect, just as bad as the disclaimer surviving\n"
    "- SUBJECT AGE VIOLATION (rule 10): any human subject in the GENERATED image reading "
    "younger than 45, or reading as youthful/smooth-skinned rather than visibly midlife "
    "(45-60) - this is its OWN dedicated category, checked explicitly, not something to "
    "notice only incidentally while checking something else. Look specifically for: "
    "smooth/poreless/airbrushed skin, no visible fine lines or skin laxity, or an overall "
    "impression of a subject in their 20s-30s. State explicitly: any human subject must "
    "read 45-60 in FACE, BODY, and CLOTHING/STYLING together - a youthful body shape or "
    "on-trend youthful clothing paired with an older-reading face is still a violation of "
    "this category, not just the face in isolation. This applies regardless of what age "
    "the reference ad's own model was - a young-reading subject is a violation even when "
    "the reference itself showed a young model, since rule 10 requires 45-60 "
    "unconditionally, never inherited from the reference's apparent age. Confirmed live: "
    "a ~30-year-old subject shipped completely unflagged on one ad while a separate ad's "
    "clear violation was correctly caught - report this category every time a subject "
    "reads under 45, not only when it seems obviously egregious\n"
    "- SKIN TEXTURE REALISM (rule 11): wherever loose, crepey, or aged skin is depicted "
    "in the GENERATED image, it must read as real, photographed human skin - irregular "
    "wrinkle patterns, uneven tone, real light response. Uniform texture or a smoothed, "
    "AI-looking skin surface is a violation of this category, independently of whether "
    "the subject's apparent AGE (above) is otherwise correct - both are separate "
    "requirements. Applies to BOTH halves of a before/after composition, not only the "
    "side depicting the concern\n"
    "- SUBJECT IDENTITY (compares the GENERATED image against the REFERENCE image "
    "attached alongside it, when one is attached - see below): flag HIGH whenever the "
    "draft's human subject is recognisably the SAME PERSON as the reference's - same "
    "face, same hair, same clothing, same pose. The reference's model must be "
    "SUBSTITUTED, never reproduced (compliance rule C1, rule 10 above) - this is a "
    "rights/identity violation, not a fidelity choice. Judge holistically: even if no "
    "single feature is an exact pixel match, a strong overall impression of the same "
    "individual (same facial structure, same hair colour/style, same wardrobe, same body "
    "position) is enough to flag - do not require every feature to match before "
    "reporting this. This category ONLY applies when a reference image is actually "
    "attached alongside the draft (edit mode) - there is nothing to compare against "
    "otherwise, and this category should not fire without a reference image present\n"
    "- LAYOUT DESCRIPTORS RENDERED AS LITERAL TEXT (rule 8): a layout/composition/framing "
    "descriptor's own WORDS appearing as actual visible typography in the GENERATED "
    "image - e.g. the literal words 'headline', 'stacked', 'split-screen', 'banner', or "
    "similar layout terminology rendered as if it were the ad's own copy. These words "
    "describe how elements are arranged; they must never themselves appear as text in "
    "the image\n"
    "- PRODUCT REGISTER MISMATCH: the product rendered in a different visual register than "
    "the surrounding scene. This has TWO distinct shapes, both count: (1) a photorealistic "
    "bottle composited into an otherwise illustrated/hand-drawn scene, or the reverse; (2) "
    "WITHIN the same photographic scene, the bottle's own lighting direction, shadow "
    "hardness, colour temperature, grain, or depth of field visibly does not match the "
    "surrounding scene's - e.g. a crisply studio-lit, evenly-graded bottle with a soft "
    "even highlight pasted into a warm ambient UGC frame, or a flash-lit bottle dropped "
    "into diffuse window light, so the product reads as cut-and-pasted even though both "
    "the scene and the product are photographic. The bottle's geometry and label CONTENT "
    "must stay accurate in every case, but its RENDERING - including lighting, shadow, "
    "grain, and depth of field, not just photographic-vs-illustrated - must match the "
    "scene around it (the bottle-rendering-matches-scene instruction)\n\n"
    "Treat a hit in these categories as HIGH confidence by default unless you are quite "
    "sure it's a false read: unauthorised offer, scarcity claim, promo code, efficacy "
    "claim, testimonial, product category mismatch, regulatory text carried over from the "
    "reference, product register mismatch, nudity or sexualised content, subject age "
    "violation, subject identity. These are the exact categories that have shipped in "
    "real drafts before this check existed."
)

# CRITIC_SYSTEM is an INDEPENDENTLY hand-written checklist, not generated from
# brand_rules()/compliance_rules.py's actual text - flagged as a real drift risk: if those
# rules are renumbered, reworded, or removed later and nobody updates this checklist to
# match, the critic keeps citing a rule that no longer says what it claims. See
# test_output_critic.py's rule-citation tests (rules 1-2/5/6/7/9 and C2/C3/C6 each have
# their own dedicated test pinning the exact citation text).
#
# Item 4 (2026-08-12): the tuple that USED to live here (CITED_RULE_IDS) was a
# hand-maintained allowlist of "rules I've already cited" - opt-in, so a NEW rule added
# to generate_image_prompt.py had no way to make itself known to this file. Rule 11
# (SKIN TEXTURE REALISM) was added there the same session with no checklist entry, and
# the old assert/test below it happily passed, because neither ever knew rule 11 existed
# at all - they only ever checked the rules someone had remembered to list. INVERTED:
# _numbered_rule_ids() below discovers every rule generate_image_prompt.py gives its own
# dedicated _RULE_<N>_... module constant (currently 8, 9, 10, 11) by introspection, not
# a maintained list - a new _RULE_12_... constant is automatically required to be cited
# the moment it's added, with nothing else to remember to update. Rules 1-5 (bundled in
# one _RULES_1_TO_5 string) and 6-7 (built by functions, not constants) are excluded by
# this SAME naming convention, not a second exclusion list - they were never individual
# constants to derive an id FROM, and their own existing citations are independently
# pinned by test_critic_system_cites_rule_5_next_to_product_category et al above.
#
# No module-level assert (unlike the version this replaces): a module-level assert here
# runs on every import of this file, including the deployed container - exactly the
# outage shape CLAUDE.md's own note on generate_image_prompt_writer.py's STYLE_GUIDANCE
# coverage check already describes (and already fixed there the same way, by moving the
# check into a test). `python -O` also strips assert statements entirely, so it was
# never a reliable safety net even where it did run.
def _numbered_rule_ids():
    """Every generate_image_prompt.py rule with its OWN dedicated module-level constant
    (the _RULE_<N>_... naming convention), discovered by introspection. See the module
    comment above for why this replaces a hand-maintained tuple."""
    from src import generate_image_prompt
    ids = []
    for name in dir(generate_image_prompt):
        m = re.match(r"^_RULE_(\d+)_", name)
        if m:
            ids.append(int(m.group(1)))
    return sorted(ids)


def has_high_confidence(findings):
    """True if any finding in `findings` (the list check_draft returns) is HIGH
    confidence. The single gate condition the retry loop and the "failed review" card
    state both key off - added 2026-08-05 after a real draft (L'Occitane edit-mode leak:
    competitor headline, body copy, CTA, and product category all survived verbatim) was
    correctly flagged at HIGH confidence on 8/8 findings and still saved as an ordinary,
    unflagged-looking pending draft - the critic was reporting, not gating. No new column
    needed: `confidence` is already on every finding, so this is the only signal a caller
    (pipeline.process_ad's retry loop, or a template deciding how to badge a card) needs."""
    return any((f.get("confidence") or "").lower() == "high" for f in (findings or []))


MIN_CONTRADICTION_SNIPPET_LEN = 6


def _normalize_for_match(text):
    """Lowercase, and collapse quote/dash punctuation to a space, so a finding's own
    quoted text - which may use curly quotes or an em-dash when Claude echoes content back
    (confirmed live: the critic wrote 'SANDY O.' with a curly apostrophe-style quote) -
    still matches the authorised value it's quoting, punctuation style aside."""
    text = (text or "").lower()
    return re.sub(r"[\"'‘’“”–—-]+", " ", text)


# Category keywords recognising the testimonial/quote/review shape CRITIC_SYSTEM's own
# checklist names for C2 ("a testimonial, quote, or star rating"). category is
# model-authored free text, not an enum - this is a best-effort, fragile match, exactly
# as fragile as the description match below. That is why BOTH conditions are required
# and the default is KEEP, not why this list tries to be exhaustive.
_TESTIMONIAL_CATEGORY_KEYWORDS = ("testimonial", "quote", "review", "star rating")


def _is_testimonial_shaped_category(category):
    lowered = (category or "").lower()
    return any(kw in lowered for kw in _TESTIMONIAL_CATEGORY_KEYWORDS)


def drop_findings_contradicted_by_authorised(findings, testimonial=None):
    """TESTIMONIAL ONLY (narrowed 2026-08-10, was general - offer_text/headline/subtext
    removed from scope entirely, not merely deprioritised): a live false-positive sweep
    found three genuine, distinct violations on one ad - a leaked unauthorised
    comparison-label, a missing headline, a missing subtext - ALL wrongly dropped by the
    prior version because their descriptions happened to quote the authorised headline
    text while reporting something else entirely (an absence, or an unrelated leak).
    Containment of an authorised string inside a finding's free-text description proves
    nothing about polarity: it cannot tell "this authorised text rendered correctly,
    re-flagged" from "this authorised text is MISSING" or "something unrelated is quoted
    alongside it." Testimonial re-flagging (ad 1653458269057951, 2026-08-07 - a real
    product_reviews quote correctly authorised via select_testimonial_review, flagged as
    fabricated anyway) remains the ONE CONFIRMED motivating case for this filter existing
    at all. offer_text/headline/subtext are dropped from scope, not narrowed, because
    there is no confirmed case for them, and a separate live sweep the same day this was
    narrowed found three drafts rendering a fabricated "20% OFF" with offer_text empty -
    if offer findings were ever being dropped here, this filter is a plausible reason
    nothing surfaced it. (The real fix for offer/headline/subtext false positives is
    CRITIC_SYSTEM itself now telling the critic what's authorised up front - see the
    OFFER and rule-6 bullets above - not this post-hoc filter.)

    FAIL OPEN: category is model-authored free text (CRITIC_SYSTEM never gives Claude a
    fixed enum), so matching on it is exactly as fragile as matching on description - the
    fix is not a smarter match, it's making the DEFAULT outcome KEEP. A finding is
    dropped ONLY when BOTH hold: its category reads as testimonial-shaped (see
    _is_testimonial_shaped_category) AND the authorised quote text itself (not
    attribution alone - a name matching proves nothing about the quote) appears in its
    description. Anything unmatched or ambiguous on either condition is kept,
    unconditionally.

    Never mutates `findings`; returns a new, possibly-shorter list. Every drop is logged -
    a finding silently disappearing here must still be visible to whoever reads the logs."""
    if not testimonial:
        return list(findings or [])
    quote = testimonial.get("quote")
    if not quote or len(quote.strip()) < MIN_CONTRADICTION_SNIPPET_LEN:
        return list(findings or [])
    snippet = _normalize_for_match(quote)
    kept = []
    for finding in (findings or []):
        if (_is_testimonial_shaped_category(finding.get("category"))
                and snippet in _normalize_for_match(finding.get("description"))):
            log.info(
                "Output critic finding dropped as contradicted by authorised testimonial "
                "(matches %r): %s", snippet.strip(), finding,
            )
            continue
        kept.append(finding)
    return kept


def _sniff_mime_type(data):
    """Magic-byte sniff - duplicated rather than imported from deconstruct.py/
    generate_image_prompt.py, matching this codebase's existing precedent for this exact
    one-line lookup (both of those modules already duplicate it too)."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:4] == b"GIF8":
        return "image/gif"
    return "image/jpeg"


def _build_user_prompt(brand_rules_text, headline=None, subtext=None, offer_text=None,
                        include_product=True, visual_description=None, ingredients=None,
                        testimonial=None, has_reference_image=False):
    """Pure and side-effect free so it's directly testable without mocking the API.

    has_reference_image (Item 2, 2026-08-12): True when check_draft is also attaching
    the competitor's ORIGINAL reference image alongside the draft (edit mode only - see
    check_draft's own reference_image_bytes parameter). Changes ONLY the closing
    instruction, telling Claude which of the two attached images is which and that the
    SUBJECT IDENTITY category specifically requires comparing them - the checklist text
    itself already says this category doesn't apply without a reference attached, so
    this is about orienting Claude to the two images, not duplicating that scope note.

    visual_description/ingredients (2026-08-06, PART 1G): the product's OWN documented
    facts - the same fields build_image_prompt's product_clause already hands the
    generator - now also handed to the critic, closing a real false positive: the
    original L'Occitane run's critic flagged the Besque bottle's real, documented label
    sub-lines ("LUXURY BODY OIL", "NOURISH, HYDRATE & SMOOTH SKIN", ...) as an invented
    violation of rule 1's bare "name only" wording, because the critic had never been told
    what the real label actually says beyond the name. Both are optional and additive -
    omitting them (every pre-existing caller, until pipeline.py is updated alongside this)
    reproduces today's prompt byte-for-byte.

    testimonial (2026-08-07): the SAME dict select_testimonial_review picked and handed to
    build_image_prompt for this generation - {"quote", "attribution"}, or None. Closes the
    same class of false positive as visual_description/ingredients above, this time on the
    fabricated-testimonials fix (808ddee) itself: that fix substitutes a REAL
    product_reviews row into a social_proof zone, but the critic was never told a
    testimonial had actually been authorised, so its checklist's "a testimonial, quote, or
    star rating (C2)" line flagged the fix's own correct output as fabricated - confirmed
    live, ad 1653458269057951, real review id 13308 ("Works like magic!" - SANDY O.)."""
    if headline:
        text_line = f'headline {headline!r}' + (f', subtext {subtext!r}' if subtext else '')
    else:
        text_line = "NONE - no text was authorised for this image"
    offer_line = repr(offer_text) if offer_text else "NONE - no offer was authorised for this image"
    product_line = ("exactly one Besque product" if include_product
                     else "NONE - this was a deliberately productless image")
    visual_line = (
        f"The Besque product's own documented label/bottle design (judge label text "
        f"against THIS, not rule 1's bare wording alone - see the rule-9/rules-1-2 "
        f"instruction above): {visual_description}\n"
        if visual_description else ""
    )
    ingredients_line = (
        f"The Besque product's actual ingredients (for judging the new carried-over-prop "
        f"category above): {ingredients}\n"
        if ingredients else ""
    )
    if testimonial and (testimonial.get("quote") or "").strip():
        quote = testimonial["quote"].strip()
        attribution = (testimonial.get("attribution") or "").strip()
        testimonial_line = (
            f"Authorised testimonial (judge C2 against THIS, not a bare "
            f"any-quote-is-fabricated reading - see the C2 instruction above): the quote "
            f"{quote!r}" + (f", attributed to {attribution!r},"
                             if attribution else ", with no attribution,")
            + " is a REAL, approved customer review and is authorised to appear.\n"
        )
    else:
        testimonial_line = "Authorised testimonial: NONE - no testimonial was authorised for this image.\n"
    if has_reference_image:
        closing = (
            "TWO images are attached: the FIRST is the competitor's ORIGINAL reference "
            "ad; the SECOND is the GENERATED Besque draft you are reviewing. Review the "
            "SECOND (GENERATED) image against these rules and report every violation "
            "you actually see, per the categories in your instructions - including "
            "SUBJECT IDENTITY, which requires comparing the two images directly."
        )
    else:
        closing = (
            "Review the attached image against these rules and report every violation you "
            "actually see, per the categories in your instructions."
        )
    return (
        "RULES THIS IMAGE WAS GENERATED UNDER:\n"
        f"{brand_rules_text.strip()}\n\n"
        f"{visual_line}"
        f"{ingredients_line}"
        f"Authorised text_in_image content: {text_line}.\n"
        f"Authorised offer: {offer_line}.\n"
        f"Product presence authorised: {product_line}.\n"
        f"{testimonial_line}\n"
        f"{closing}"
    )


def check_draft(image_bytes, brand_rules_text, headline=None, subtext=None, offer_text=None,
                 include_product=True, visual_description=None, ingredients=None,
                 testimonial=None, reference_image_bytes=None):
    """Ask Claude to inspect a GENERATED draft image for rule violations.

    Returns a list of {"category", "description", "confidence"} dicts, medium/high
    confidence only (a critic that flags everything becomes noise, so low-confidence hits
    are dropped here rather than left for the caller to filter).

    reference_image_bytes (Item 2, 2026-08-12): the competitor's ORIGINAL reference ad
    image - the SAME bytes edit_mode attaches to Gemini for generation (pipeline.py
    passes them through). Attached as a SECOND image ahead of the draft so the critic
    can actually judge SUBJECT IDENTITY (same face/hair/clothing/pose as the reference)
    - the checklist previously named this comparison with nothing to compare against,
    since only the draft was ever attached. None (every pre-existing caller, and every
    generate-mode call - there is no real reference photo outside edit mode) reproduces
    today's single-image prompt exactly.

    Retries ONCE (2026-08-12) with the same JSON_ESCAPE_SYSTEM nudge deconstruct_image
    already uses, appended to CRITIC_SYSTEM - added after a real parse failure
    ("JSONDecodeError: Invalid \\escape") silently left a draft unflagged with no signal
    anywhere it was never actually reviewed. If the retry ALSO fails (for any reason -
    still unparseable, a timeout, an API error), this now returns
    CRITIC_CHECK_FAILED_FINDING instead of None: never raises, but also never silently
    passes a draft that was never actually checked - has_high_confidence() reads that
    finding as a real HIGH violation, so pipeline.process_ad's existing retry-then-mark-
    failed-review logic marks it exactly like a genuine violation would, with no special
    case needed there. This module NEVER returns None any more; callers written against
    the older "None means the check did not run" contract still work, since that branch
    simply never fires - it is not relied upon here to distinguish anything."""
    user_prompt = _build_user_prompt(brand_rules_text, headline=headline, subtext=subtext,
                                      offer_text=offer_text, include_product=include_product,
                                      visual_description=visual_description, ingredients=ingredients,
                                      testimonial=testimonial, has_reference_image=bool(reference_image_bytes))
    import base64
    media_type = _sniff_mime_type(image_bytes)
    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    image_content = []
    if reference_image_bytes:
        ref_media_type = _sniff_mime_type(reference_image_bytes)
        ref_b64 = base64.standard_b64encode(reference_image_bytes).decode("utf-8")
        image_content.append(
            {"type": "image", "source": {"type": "base64", "media_type": ref_media_type, "data": ref_b64}}
        )
    image_content.append({"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}})
    system = CRITIC_SYSTEM
    last_exc = None
    for attempt in range(1, MAX_CRITIC_ATTEMPTS + 1):
        try:
            # Client construction INSIDE the try, not before the loop: a real regression
            # caught by test_check_draft_returns_none_on_api_error - a constructor failure
            # (e.g. missing API key) must retry/fall through to CRITIC_CHECK_FAILED_FINDING
            # exactly like a parse failure does, never propagate uncaught.
            client = anthropic.Anthropic(timeout=60.0, max_retries=1)  # reads ANTHROPIC_API_KEY from env
            message = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=1024,
                system=system,
                messages=[{
                    "role": "user",
                    "content": image_content + [
                        {"type": "text", "text": user_prompt},
                    ],
                }],
            )
            raw = message.content[0].text
            data = json_response.extract_json(raw)
            violations = data.get("violations") or []
            return [v for v in violations if (v.get("confidence") or "").lower() in ("high", "medium")]
        except Exception as e:
            last_exc = e
            log.warning("output critic failed (attempt %s/%s): %s: %s",
                        attempt, MAX_CRITIC_ATTEMPTS, type(e).__name__, e)
            if attempt < MAX_CRITIC_ATTEMPTS:
                system = CRITIC_SYSTEM + "\n\n" + JSON_ESCAPE_SYSTEM
    log.warning(
        "output critic failed after %s attempt(s) (%s: %s) - marking draft for manual "
        "review instead of leaving it unflagged", MAX_CRITIC_ATTEMPTS, type(last_exc).__name__, last_exc,
    )
    return list(CRITIC_CHECK_FAILED_FINDING)
