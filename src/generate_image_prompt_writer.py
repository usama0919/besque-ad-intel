"""Claude prompt-writer pass (Part 5 of the messaging-angles feature): asks Claude to
WRITE the actual Gemini image-generation prompt, mirroring the marketing team's real
workflow - Claude writes the prompt, Gemini renders it. That second model pass is the
piece their process has that the old template-only pipeline didn't.

Sits ON TOP OF build_image_prompt, never replaces it: brand_rules()/compliance C1-C6 and
the product's factual visual_description are always appended mechanically by
build_image_prompt regardless of what this returns - this module only supplies the
creative/composition text that slots into that assembly. If this fails for ANY reason
(timeout, API error, unparseable output), the caller falls back to build_image_prompt's
own template assembly by treating the return value as None - never to nothing.
"""
import os
import logging
import anthropic

from src import json_response

CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")

log = logging.getLogger("generate_image_prompt_writer")

WRITER_SYSTEM = (
    "You write a single image-generation prompt for Google's Gemini image model (internal "
    "codename nano banana), for Besque, a natural body-oil skincare brand for women 40+. "
    "You are NOT writing brand rules, compliance rules, or the product's exact ingredient "
    "list - those are enforced separately and mechanically by the calling system; do not "
    "restate, invent, or contradict them. Your job is purely creative: scene and setting, "
    "subject, product placement, text content and styling if any is mentioned below, "
    "colour palette, and realism/production register. Write ONE flowing paragraph of plain "
    "prose - no headers, no bullet points, no markdown, no JSON inside the description "
    "itself. Return ONLY valid JSON of the exact shape {\"creative_description\": \"...\"}, "
    "nothing else."
)


def _build_user_prompt(blueprint, product=None, angle=None, realism=None, body_area=None,
                        offer_text=None, reference_image_count=0, text_in_image=False,
                        include_product=True, headline=None, subtext=None):
    """Assemble the text handed to Claude. Pure and side-effect free so it's directly
    testable without mocking the API.

    blueprint["visual"]["subject"] is deliberately NEVER read here, for the same reason
    build_image_prompt never reads it: it's where the vision step puts rich,
    identity-carrying descriptions of the COMPETITOR's model AND product (e.g. "two
    amber glass bottles, blonde model in dark bikini..."). Handing that to a creative
    writer is exactly how a real incident produced "two Besque Magic amber glass
    bottles" - the writer described the competitor's own subject/product count back at
    us. If a future change needs `subject` for better composition, it must come with an
    explicit compliance override alongside it, not instead of one."""
    lines = []
    angle = angle or {}
    lines.append(f"Messaging angle: {angle.get('name', 'unspecified')}.")
    # angles.notes is the operator's own per-angle guidance channel for exactly this pass -
    # it exists for nothing else, so it must be consumed here, not left unread.
    if angle.get("notes"):
        lines.append(f"Operator guidance for this angle (from the angle's notes field): {angle['notes']}")
    if body_area:
        # Per-run, NEVER angle.body_area - body area varies every run and is never fixed
        # per angle (confirmed by the team). This is the only body-area value fed in here.
        lines.append(f"Body area to feature in THIS image (operator-specified for this run): {body_area}.")
    if realism:
        lines.append(f"Realism / production register for this image: {realism}.")
    if offer_text:
        lines.append(
            f"Offer to reflect in the scene if relevant, exactly as given - do not invent "
            f"numbers or terms beyond it: {offer_text}."
        )
    if product:
        visual_desc = product.get("visual_description", "")
        if visual_desc:
            lines.append(
                f"Fixed product visual facts (for scene composition only - the exact "
                f"label/ingredient wording is enforced separately, do not restate it "
                f"yourself): {visual_desc}"
            )
    if reference_image_count:
        lines.append(
            f"{reference_image_count} reference photo(s) of the exact product will be attached "
            f"separately at render time - assume the rendered product matches those photos."
        )
    else:
        lines.append(
            "No reference photos will be attached - describe the product only from the "
            "visual facts above, if any were given."
        )
    visual = (blueprint or {}).get("visual", {}) or {}
    if visual.get("layout"):
        lines.append(
            f"Composition/framing inspiration from the competitor ad (borrow the composition "
            f"idea only, never literal text from it - see rule 8): {visual['layout']}"
        )
    if visual.get("palette_mood"):
        lines.append(f"Palette/mood inspiration from the competitor ad: {visual['palette_mood']}")

    # These two constraints are stated LAST, right before the writing instruction, and in
    # absolute terms - not "inspiration", not overridable by anything above (including the
    # competitor's own layout/palette). A real failure: with text_in_image=False the writer
    # described "gold serif headline text reads 'CREPEY SKIN MEETS ITS MATCH'" (rule 6
    # forbids all text) and "two Besque Magic amber glass bottles" (rule 7 permits exactly
    # one) - Gemini then discarded the whole composition rather than reconciling the
    # contradiction with brand_rules(). The writer must never write a scene the guardrails
    # will then have to override.
    if include_product:
        lines.append(
            "Product count (STRICT, overrides anything above): describe EXACTLY ONE Besque "
            "product bottle in the scene - never two, never a range, lineup, or duplicate, "
            "even if the competitor ad's own layout showed multiple products. Collapse any "
            "multi-product composition to a single bottle."
        )
    else:
        lines.append(
            "Product presence (STRICT, overrides anything above): this is a deliberately "
            "PRODUCTLESS, educational/illustrative image. Do NOT describe any Besque "
            "product, bottle, jar, tube, or packaging anywhere in the scene - not even one."
        )
    if text_in_image and headline:
        permitted = f'the headline "{headline}"'
        if subtext:
            permitted += f' and the supporting text "{subtext}"'
        lines.append(
            f"Text in image (STRICT, overrides anything above): describe {permitted} "
            f"rendered as in-scene typography - describe its placement and styling, but do "
            f"not invent, alter, quote different wording, or add any other text, price, or "
            f"caption. Never quote a headline other than the one given here."
        )
    else:
        lines.append(
            "Text in image (STRICT, overrides anything above): this image must contain NO "
            "typography, headline, or quoted text of any kind, even if the competitor ad had "
            "text. Describe RESERVED NEGATIVE SPACE where a headline could later be added as "
            "a separate overlay - never describe words, letters, or typography in the scene."
        )

    lines.append(
        "Write the creative_description now: one paragraph covering scene and setting, "
        "subject, product placement, text content and styling (if any text was specified "
        "above), colour palette, and realism level."
    )
    return "\n".join(lines)


def write_creative_description(blueprint, product=None, angle=None, realism=None,
                                body_area=None, offer_text=None, reference_image_count=0,
                                text_in_image=False, include_product=True, headline=None,
                                subtext=None):
    """Ask Claude to write the creative description that build_image_prompt will slot in
    place of its own template assembly. Returns the text, or None on ANY failure - never
    raises, so callers can treat None as "fall back to the template" without their own
    try/except.

    text_in_image/include_product/headline/subtext are the SAME mode flags brand_rules()
    enforces mechanically - the writer must be told them explicitly (see
    _build_user_prompt's STRICT block) so it never describes a scene the guardrails then
    have to override. Defaults (False/True/None/None) match brand_rules()'s own defaults."""
    user_prompt = _build_user_prompt(blueprint, product=product, angle=angle, realism=realism,
                                      body_area=body_area, offer_text=offer_text,
                                      reference_image_count=reference_image_count,
                                      text_in_image=text_in_image, include_product=include_product,
                                      headline=headline, subtext=subtext)
    try:
        client = anthropic.Anthropic(timeout=60.0, max_retries=1)  # reads ANTHROPIC_API_KEY from env
        write_creative_description.last_prompt = user_prompt
        message = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=3072,
            system=WRITER_SYSTEM,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw = message.content[0].text
        data = json_response.extract_json(raw)
        desc = data.get("creative_description")
        if not desc or not isinstance(desc, str):
            log.warning("writer returned no usable creative_description, falling back to template")
            return None
        return desc.strip()
    except Exception as e:
        log.warning("prompt-writer pass failed (%s: %s), falling back to template assembly",
                    type(e).__name__, e)
        return None
