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
    "- a competitor logo, seal, badge, or brand mark anywhere in the image (rule 9)\n"
    "- a competitor brand or product name in any rendered text (rules 1-2)\n"
    "- an unauthorised offer, price, discount, promo code, scarcity, or stock-count claim "
    "(the OFFER instruction)\n"
    "- a quantified efficacy claim (a percentage, ratio, or \"X% more\"-style claim) not "
    "explicitly authorised below (C3, the EFFICACY CLAIMS instruction)\n"
    "- a testimonial, quote, or star rating (C2)\n"
    "- a Besque product present when none was authorised, or more than one product where "
    "only one was authorised (rule 7)\n"
    "- a product-derived substance (a drip, pour, pool, or smear) in the wrong colour for "
    "the actual Besque product described below (the product-substance instruction)\n"
    "- an empty graphic container (a badge, oval, bubble, banner, or ribbon) with no "
    "content\n"
    "- garbled or illegible rendered text\n"
    "- text rendered when none was authorised below, or missing when it was authorised "
    "(rule 6)\n\n"
    "Treat a hit in these categories as HIGH confidence by default unless you are quite "
    "sure it's a false read: unauthorised offer, scarcity claim, promo code, efficacy "
    "claim, testimonial. These are the exact categories that have shipped in real drafts "
    "before this check existed."
)

# CRITIC_SYSTEM is an INDEPENDENTLY hand-written checklist, not generated from
# brand_rules()/compliance_rules.py's actual text - flagged as a real drift risk: if those
# rules are renumbered, reworded, or removed later and nobody updates this checklist to
# match, the critic keeps citing a rule that no longer says what it claims. This is a
# tripwire, not a fix for that risk - it only catches the citation disappearing from
# CRITIC_SYSTEM's own text, not the cited rule drifting out of sync with brand_rules()
# itself. See test_output_critic.py's rule-citation tests.
CITED_RULE_IDS = ("rule 9", "rules 1-2", "C3", "C2", "rule 7", "rule 6")
assert all(rule_id in CRITIC_SYSTEM for rule_id in CITED_RULE_IDS), (
    "CRITIC_SYSTEM no longer cites one of CITED_RULE_IDS - the checklist and the actual "
    "rule numbering have drifted apart"
)


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
                        include_product=True):
    """Pure and side-effect free so it's directly testable without mocking the API."""
    if headline:
        text_line = f'headline {headline!r}' + (f', subtext {subtext!r}' if subtext else '')
    else:
        text_line = "NONE - no text was authorised for this image"
    offer_line = repr(offer_text) if offer_text else "NONE - no offer was authorised for this image"
    product_line = ("exactly one Besque product" if include_product
                     else "NONE - this was a deliberately productless image")
    return (
        "RULES THIS IMAGE WAS GENERATED UNDER:\n"
        f"{brand_rules_text.strip()}\n\n"
        f"Authorised text_in_image content: {text_line}.\n"
        f"Authorised offer: {offer_line}.\n"
        f"Product presence authorised: {product_line}.\n\n"
        "Review the attached image against these rules and report every violation you "
        "actually see, per the categories in your instructions."
    )


def check_draft(image_bytes, brand_rules_text, headline=None, subtext=None, offer_text=None,
                 include_product=True):
    """Ask Claude to inspect a GENERATED draft image for rule violations.

    Returns a list of {"category", "description", "confidence"} dicts, medium/high
    confidence only (a critic that flags everything becomes noise, so low-confidence hits
    are dropped here rather than left for the caller to filter) - or None on ANY failure.
    Never raises: callers must treat None as "the check did not run", not as "no
    violations found" - see this module's docstring."""
    user_prompt = _build_user_prompt(brand_rules_text, headline=headline, subtext=subtext,
                                      offer_text=offer_text, include_product=include_product)
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
