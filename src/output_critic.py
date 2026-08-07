"""Output critic (Prompt 4, Item 1): a SAFETY control, not a quality feature.

Every guardrail on the image path up to this point is prompt-only - nothing inspects what
Gemini actually produced. Four rounds of increasingly explicit prompt instructions failed
to stop offer/claim text leaking from reference ads in edit mode; the pattern is
conclusive - prompt-only guardrails do not reliably bind in edit mode. This module is the
response: after generation, send the draft image back to Claude with the same rules it was
supposed to follow and ask what it actually violated.

Non-blocking by design, and this is load-bearing, not incidental: runs AFTER
dedupe.save_artifact (never before - a slow/failed check must never lose a draft), and
check_draft() never raises. Any failure (timeout, API error, unparseable JSON) is caught
and returns None; the caller (pipeline.process_ad) must treat None as "the check did not
run" - record a pipeline_warning and show the card unflagged, never as a finding of its
own. A flag is something a human sees and decides on; this module surfaces, it never acts -
no auto-reject, no auto-regenerate.
"""
import os
import re
import logging
import anthropic

from src import json_response

CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")

log = logging.getLogger("output_critic")

# These are the categories that have ACTUALLY shipped in real drafts despite four rounds
# of prompt-only guardrails - a cautious "medium" burying a repeat of the exact failure
# that prompted this module is worse than a false positive, so the prompt tells Claude to
# default these to high confidence rather than hedge.
HIGH_CONFIDENCE_BY_DEFAULT = (
    "unauthorised offer", "scarcity claim", "promo code", "efficacy claim", "testimonial",
    "product category mismatch", "regulatory text carried over from the reference",
    "product register mismatch", "nudity or sexualised content",
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
    "(the OFFER instruction)\n"
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
    "(rule 6)\n"
    "- regulatory, legal, or medical disclaimer text carried over from the reference - an "
    "FDA/dietary-supplement statement, drug facts, a clinical-trial footnote, "
    "country-specific legal text, or a competitor's own T&Cs - on ANY product, even one "
    "the disclaimer plainly doesn't apply to (the disclaimer-removal instruction). Also "
    "flag a dangling asterisk or footnote marker left with no referent after disclaimer "
    "text was removed - that's its own defect, just as bad as the disclaimer surviving\n"
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
    "reference, product register mismatch, nudity or sexualised content. These are the "
    "exact categories that have shipped in real drafts before this check existed."
)

# CRITIC_SYSTEM is an INDEPENDENTLY hand-written checklist, not generated from
# brand_rules()/compliance_rules.py's actual text - flagged as a real drift risk: if those
# rules are renumbered, reworded, or removed later and nobody updates this checklist to
# match, the critic keeps citing a rule that no longer says what it claims. This is a
# tripwire, not a fix for that risk - it only catches the citation disappearing from
# CRITIC_SYSTEM's own text, not the cited rule drifting out of sync with brand_rules()
# itself. See test_output_critic.py's rule-citation tests.
CITED_RULE_IDS = ("C6", "rule 9", "rules 1-2", "C3", "C2", "rule 7", "rule 5", "rule 6")
assert all(rule_id in CRITIC_SYSTEM for rule_id in CITED_RULE_IDS), (
    "CRITIC_SYSTEM no longer cites one of CITED_RULE_IDS - the checklist and the actual "
    "rule numbering have drifted apart"
)


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


def drop_findings_contradicted_by_authorised(findings, testimonial=None, offer_text=None,
                                              headline=None, subtext=None):
    """Defense in depth, general - NOT testimonial-specific (2026-08-07): a finding whose
    description quotes back content THIS generation's own prompt explicitly authorised
    (the real testimonial select_testimonial_review picked, the operator's own offer_text,
    the authorised headline/subtext) is not a real defect - it's the critic re-flagging
    something the SAME prompt told Gemini to render. Feeding a finding like that into the
    corrective retry as-is produces a self-contradictory prompt (one clause says render
    it, the critic-feedback clause says remove it) and spends a paid generation trying to
    satisfy an instruction that can't be satisfied. Confirmed live: ad 1653458269057951
    (2026-08-07) - a real product_reviews-sourced testimonial ("Works like magic!" -
    SANDY O.) was correctly authorised, the critic flagged it as fabricated anyway, and
    the retry that followed asked Gemini to remove the exact quote the structural-zones
    clause was still instructing it to render in the same prompt.

    This is independent of, and a backstop for, telling the critic what's authorised up
    front (see check_draft's testimonial/offer_text/headline params) - an LLM critic can
    still misjudge even when told; the whole reason this module exists is that prompt-only
    instructions don't reliably bind, and that applies to the critic's own prompt just as
    much as the generator's.

    Matching is substring containment against the finding's OWN description (normalised -
    see _normalize_for_match), not a category-name match - this confirms the SPECIFIC
    quoted content is what was authorised, so a genuinely different or additional
    violation reported under the same category (e.g. a second, uninvited quote) still
    gets through untouched. Candidate snippets shorter than
    MIN_CONTRADICTION_SNIPPET_LEN are skipped - a short authorised string (a single
    initial, a two-letter word) would false-match unrelated findings on common substrings
    otherwise.

    Never mutates `findings`; returns a new, possibly-shorter list. Every drop is logged -
    a finding silently disappearing here must still be visible to whoever reads the logs."""
    authorised = []
    if testimonial:
        authorised.append(testimonial.get("quote"))
        authorised.append(testimonial.get("attribution"))
    authorised.extend([offer_text, headline, subtext])
    snippets = [
        _normalize_for_match(s) for s in authorised
        if s and len(s.strip()) >= MIN_CONTRADICTION_SNIPPET_LEN
    ]
    snippets = [s for s in snippets if s.strip()]
    if not snippets:
        return list(findings or [])
    kept = []
    for finding in (findings or []):
        desc = _normalize_for_match(finding.get("description"))
        hit = next((s for s in snippets if s in desc), None)
        if hit:
            log.info(
                "Output critic finding dropped as contradicted by authorised content "
                "(matches %r): %s", hit.strip(), finding,
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
                        testimonial=None):
    """Pure and side-effect free so it's directly testable without mocking the API.

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
    return (
        "RULES THIS IMAGE WAS GENERATED UNDER:\n"
        f"{brand_rules_text.strip()}\n\n"
        f"{visual_line}"
        f"{ingredients_line}"
        f"Authorised text_in_image content: {text_line}.\n"
        f"Authorised offer: {offer_line}.\n"
        f"Product presence authorised: {product_line}.\n"
        f"{testimonial_line}\n"
        "Review the attached image against these rules and report every violation you "
        "actually see, per the categories in your instructions."
    )


def check_draft(image_bytes, brand_rules_text, headline=None, subtext=None, offer_text=None,
                 include_product=True, visual_description=None, ingredients=None,
                 testimonial=None):
    """Ask Claude to inspect a GENERATED draft image for rule violations.

    Returns a list of {"category", "description", "confidence"} dicts, medium/high
    confidence only (a critic that flags everything becomes noise, so low-confidence hits
    are dropped here rather than left for the caller to filter) - or None on ANY failure.
    Never raises: callers must treat None as "the check did not run", not as "no
    violations found" - see this module's docstring."""
    user_prompt = _build_user_prompt(brand_rules_text, headline=headline, subtext=subtext,
                                      offer_text=offer_text, include_product=include_product,
                                      visual_description=visual_description, ingredients=ingredients,
                                      testimonial=testimonial)
    try:
        import base64
        media_type = _sniff_mime_type(image_bytes)
        b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
        client = anthropic.Anthropic(timeout=60.0, max_retries=1)  # reads ANTHROPIC_API_KEY from env
        message = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1024,
            system=CRITIC_SYSTEM,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}},
                    {"type": "text", "text": user_prompt},
                ],
            }],
        )
        raw = message.content[0].text
        data = json_response.extract_json(raw)
        violations = data.get("violations") or []
        return [v for v in violations if (v.get("confidence") or "").lower() in ("high", "medium")]
    except Exception as e:
        log.warning("output critic failed (%s: %s), draft left unflagged", type(e).__name__, e)
        return None
