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

# Shared with generate_image_prompt.py's mechanical operator-instruction clause (imported
# from there, since generate_image_prompt already imports this module - the reverse import
# would be circular) so a pasted brief is clipped to the SAME length before reaching either
# consumer, not clipped twice to two different lengths.
MAX_OPERATOR_INSTRUCTION_CHARS = 500


def clip_operator_instruction(operator_instruction):
    """Cap a free-text operator instruction so a pasted brief can't blow max_tokens.
    Idempotent - clipping an already-clipped string is a no-op - so it's safe to call at
    every consumption point regardless of whether an earlier caller already clipped it."""
    text = (operator_instruction or "").strip()
    if len(text) > MAX_OPERATOR_INSTRUCTION_CHARS:
        text = text[:MAX_OPERATOR_INSTRUCTION_CHARS].rstrip() + "..."
    return text

WRITER_SYSTEM = (
    "You write a single image-generation prompt for Google's Gemini image model (internal "
    "codename nano banana), for Besque, a natural body-oil skincare brand for women 40+. "
    "You are NOT writing brand rules, compliance rules, or the product's exact ingredient "
    "list - those are enforced separately and mechanically by the calling system; do not "
    "restate, invent, or contradict them. In particular, if your scene includes a human "
    "subject, never assign or imply a specific age or a youthful descriptor (e.g. do not "
    "write 'a young woman' or 'a woman in her 20s/30s') - subject age is governed entirely "
    "by a brand-level rule appended separately, and a specific age you invent here would "
    "compete with it. Your job is purely creative: scene and setting, "
    "subject, product placement, text content and styling if any is mentioned below, "
    "colour palette, and realism/production register. Write ONE flowing paragraph of plain "
    "prose - no headers, no bullet points, no markdown, no JSON inside the description "
    "itself. Return ONLY valid JSON of the exact shape {\"creative_description\": \"...\"}, "
    "nothing else."
)


def effective_body_area(blueprint, body_area):
    """The body area actually appropriate for this generation, given what the reference
    ad's own blueprint (deconstruct.py's body_area_shown, Item E, 2026-08-05) says is
    actually in frame. A real live failure: a product-only reference (no human subject at
    all) generated with operator body_area="legs" produced illustrated legs draped over
    the bottle - so a reference with NO human subject forces this to None regardless of
    what the operator typed, not just when they left it blank; there is nothing to feature
    a body area ON.

    When the reference DOES show a human subject, its own detected region is the DEFAULT,
    but an explicit operator body_area OVERRIDES it - operator input is an override, never
    a default, matching the team's confirmed answer that body area is per-run and never
    fixed (the same reasoning angles.body_area is already never read here for).

    body_area_shown absent entirely (a blueprint from before this field existed) falls back
    to today's behaviour unchanged - the operator's body_area passes straight through -
    since there is no reference-derived signal either way to act on."""
    shown = (blueprint or {}).get("body_area_shown")
    if shown is None:
        return body_area
    shown = shown.strip()
    if not shown or shown.lower() == "none":
        return None
    return (body_area or "").strip() or shown


# Per-register guidance (Chunk 13), keyed by blueprint.production_style.style. This is now
# the SINGLE source of truth for all three places register guidance reaches a model: the
# writer's own prompt to Claude (below), the flat generate-mode template
# (generate_image_prompt.build_image_prompt's no-angle branch), and edit mode's
# _register_clause. A short-lived second dict, PRODUCTION_STYLE_GUIDANCE, existed here
# briefly on the theory that "read by Claude" vs "assembled straight into the Gemini
# prompt" were different enough consumers to need different text - collapsed the same day
# once edit mode's _register_clause proved that theory wrong: raw STYLE_GUIDANCE prose
# works directly in a Gemini-facing prompt exactly as well as in a Claude-facing one, so
# keeping a thinner, separately-drifting dict for the flat-template branch had no real
# justification left, only the risk of the two silently disagreeing on the same register.
# Sourced from the marketing team's own style doc's framing phrases, lighting cues, and
# hard-won negative knowledge - the concrete vocabulary that actually produces each look,
# not just the register's name.
#
# The doc's own bottle/label text (amber glass, gold pump top, maroon label, gold serif
# "BESQUE MAGIC") is NOT reproduced here - it's the disputed description; the verified
# product is clear glass, black pump head, terracotta rust-red label, white sans-serif
# capitals - and the doc's instruction to restate the full label in every prompt is also
# not followed - the actual product record and build_image_prompt's bottle-fixed clause
# already govern the bottle downstream of the writer, so hardcoding either description
# here would just give the writer a second, competing source of truth for it. What IS kept
# is the doc's underlying insight for "illustrated": label detail drifts into unreadable
# scribbles unless the writer is explicitly told to keep it legible - encoded below as a
# requirement, without naming what the label actually says or looks like. That requirement
# originally said "photorealistic label detail", which overshot the team's actual rule and
# produced a live bug (2026-08-06, Grüns GLP-1 reference): a photorealistic bottle composited
# into an otherwise hand-drawn illustrated scene. The team's rule is narrower - geometry,
# proportions and label CONTENT stay exactly accurate for every register, but the RENDERING
# of the bottle (and its label) always matches the surrounding scene's own visual style. Fixed
# to say "accurate and legible, rendered in this scene's own illustrated visual language".
STYLE_GUIDANCE = {
    # 2026-08-11: keys renamed ugc_native->ugc, high_spec_studio->high_spec, and "hybrid"
    # dropped entirely, matching the tightened production_style.style enum in
    # schema/blueprint.schema.json (deconstruct.py's classifier prompt now instructs the
    # UGC signals directly, so this entry restates the same observable vocabulary - phone-
    # camera framing, available light, imperfect composition, domestic setting, natural
    # grain - for the writer/edit-mode consumers of this dict specifically).
    "ugc": (
        "Framing: extremely realistic UGC-style photograph, shot on a smartphone/phone front "
        "camera, authentic phone-camera quality, not professionally staged or shot - an "
        "authentic UNPOLISHED look, never a polished studio look mistaken for this register. "
        "Setting: a domestic or non-studio setting (bathroom, bedroom, kitchen, car, outdoors), "
        "never a studio or professionally art-directed backdrop. Imperfection cues are critical "
        "- without them the output skews too polished: slightly grainy, authentic amateur photo "
        "quality, minor grain and imperfect exposure, slight natural lens characteristics, "
        "casual and unposed like a genuine quick photo, natural imperfections, raw and "
        "unpolished, no studio polish, no AI-smooth skin. Composition: imperfect and "
        "unposed - off-centre, slightly tilted, or awkwardly cropped, like a real quick photo, "
        "never a deliberately balanced studio composition. Lighting: natural indoor or window "
        "light for a daytime scene, available/uncontrolled light only - never a lighting rig; "
        "for an evening or low-light scene, state the time of day explicitly (e.g. warm "
        "artificial evening bathroom lighting only, no natural daylight) - left unstated, the "
        "model defaults to bright even daylight regardless of what the scene otherwise "
        "describes. Describe shadows as realistic, never as 'dramatic' or 'cinematic' - those "
        "two words push the look toward a studio production instead. Skin/subject: realistic "
        "natural skin texture with visible fine lines, freckles, and authentic detail, not "
        "overly airbrushed; genuine, unposed, candid framing; for an older/mature subject "
        "specifically, describe the skin as authentic and dignified so the model doesn't "
        "over-smooth it. Post-processing (STRICT, 2026-08-12): NO retouching, NO AI "
        "brightness lift, and NO colour grading of any kind - a real UGC photo straight "
        "off a phone has none of these, and applying any of them is what turns this "
        "register into a studio finish. Skin texture specifically must show the SAME lack "
        "of polish as the rest of the frame - grain, imperfect exposure, and unretouched "
        "skin are one consistent look, not skin rendered smoother/brighter than the "
        "surrounding photo. A UGC reference rendered with a studio finish (smoothed, "
        "colour-graded, or brightness-lifted, even subtly) is a failure of this register, "
        "not a stylistic variant of it."
    ),
    "high_spec": (
        "Framing: high-end studio product photograph, professional editorial beauty-campaign "
        "lighting, polished and editorial, premium beauty-brand aesthetic, ultra-realistic "
        "high-end product photography. Lighting: soft diffused key light with subtle rim "
        "lighting, sharp studio lighting with a clean reflection on the glass, gentle highlights, "
        "shallow depth of field with the subject in sharp focus. 'Dramatic' and 'cinematic' both "
        "belong here, not in ugc. Composition: clean and minimal, sophisticated and "
        "luxurious, a high-end editorial skincare aesthetic matching a luxury brand campaign. "
        "Describe only how the product is lit and shot, never its own geometry or label (fixed "
        "elsewhere): realistic glass reflections, accurate light falloff, natural shadows, sharp "
        "label detail, true-to-life material texture."
    ),
    "illustrated": (
        "Framing: name the exact reference style, never a generic label - '3D Pixar/Disney "
        "animated style illustration' (naming a specific animation studio produces far more "
        "consistent results than generic 'cartoon'), 'vintage-style comic strip illustration, "
        "retro pop-art aesthetic', or 'flat vector-style digital illustration'. Technical "
        "vocabulary by sub-style: 3D/Pixar - polished 3D animated character rendering, soft "
        "rounded Pixar aesthetic, stylized but realistic muscle/skin detail, cinematic lighting, "
        "rim light, heroic low-angle framing. Vintage comic - bold black outlines, halftone "
        "shading, warm nostalgic colour palette, 1960s-70s advertisement comic panel, thin black "
        "border, halftone dot shading on skin. Flat vector - clean bold outlines, flat shading, "
        "bright warm colour palette, playful and eye-catching, sticker-style aesthetic. Label "
        "fidelity: illustrated styles tend to abstract the product's label into unreadable "
        "scribbles unless told otherwise - explicitly state that the bottle stays "
        "RECOGNISABLE by silhouette, colour, and the product's NAME on the label (that much "
        "stays legible), but rendered in THIS SCENE'S OWN illustrated visual language, never "
        "photorealistic and never a photographic bottle composited into the drawing - a "
        "3D/Pixar scene gets a 3D/Pixar-rendered label, a vintage comic scene gets the same "
        "bold-outline/halftone treatment, a flat vector scene gets the same flat-shaded "
        "treatment. Secondary label content - sub-lines, certification icons, fine print - "
        "does NOT need to stay legible at this scale in this style; demanding it produced "
        "exactly the unreadable-scribble failure this note exists to prevent (2026-08-06, a "
        "real Grüns GLP-1 illustrated draft composited a photorealistic bottle into a "
        "hand-drawn scene specifically to keep that secondary text legible). Name and colour "
        "accuracy matter; secondary-text legibility does not, in this register only. Never "
        "describe what the label actually says or looks like here - that comes from the "
        "product record and the bottle-fixed clause downstream."
    ),
}
# Coverage against a schema addition to production_style is asserted in
# test_style_guidance_has_every_canonical_style (tests/test_generate_image_prompt.py),
# not here (2026-08-06, item 3): a module-level assert ran on every import of this file,
# which pipeline.py imports - if it ever tripped in the deployed container, the whole
# import chain fails and every request 500s, the exact shape of the missing-Pillow
# outage (see CLAUDE.md). `python -O` also strips assert statements entirely, so it was
# unreliable as a safety net even where it did run. The test gives the identical
# coverage in CI/dev, with no way to take production down if it starts failing.


def _build_user_prompt(blueprint, product=None, angle=None, realism=None, body_area=None,
                        offer_text=None, reference_image_count=0, text_in_image=False,
                        include_product=True, headline=None, subtext=None,
                        operator_instruction=None):
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
    eff_body_area = effective_body_area(blueprint, body_area)
    if eff_body_area:
        source = "operator-specified for this run" if (body_area or "").strip() else "the region shown in the reference ad"
        lines.append(f"Body area to feature in THIS image ({source}): {eff_body_area}.")
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
    if (blueprint or {}).get("creative_objective"):
        lines.append(
            f"Creative objective of the competitor ad (strategic inspiration only): "
            f"{blueprint['creative_objective']}"
        )
    if (blueprint or {}).get("target_audience"):
        lines.append(
            f"Audience the competitor ad targeted (Besque's own audience may differ - "
            f"context, not an override): {blueprint['target_audience']}"
        )
    typography = (blueprint or {}).get("typography") or {}
    typo_bits = []
    if typography.get("headline_face"):
        typo_bits.append(f"face: {typography['headline_face']}")
    if typography.get("headline_weight"):
        typo_bits.append(f"weight: {typography['headline_weight']}")
    if typography.get("hierarchy_levels"):
        typo_bits.append("hierarchy: " + "; ".join(typography["hierarchy_levels"]))
    if typography.get("case_treatment"):
        typo_bits.append(f"case: {typography['case_treatment']}")
    if typo_bits:
        lines.append(
            "Typography STYLE inspiration from the competitor ad (styling only - the "
            "wording itself is governed separately by the text-in-image rule below, never "
            "quote literal text from here): " + "; ".join(typo_bits)
        )
    layout_detail = (blueprint or {}).get("layout_detail") or {}
    ld_bits = []
    if layout_detail.get("zone_positions"):
        ld_bits.append("zones: " + "; ".join(layout_detail["zone_positions"]))
    if layout_detail.get("has_bottom_banner"):
        ld_bits.append("has a full-width bottom banner")
    if layout_detail.get("has_corner_badge"):
        ld_bits.append("has a corner badge")
    if layout_detail.get("frame_division"):
        ld_bits.append(f"frame division: {layout_detail['frame_division']}")
    if ld_bits:
        lines.append(
            "Layout structure inspiration from the competitor ad (composition only, adapt "
            "don't copy - see rule 8): " + "; ".join(ld_bits)
        )

    # Text DENSITY to match, distinct from exact wording (governed separately by the
    # text-in-image STRICT rule below). Real failure this closes: subtext carried the
    # full ~80-word Facebook primary_text body copy against a reference that legibility_notes/
    # typography showed carried only a short headline and a name - Gemini rendered the
    # whole paragraph into the scene. hierarchy_levels' length is the best available proxy
    # for how many distinct text tiers the reference actually had.
    density_bits = []
    if (blueprint or {}).get("legibility_notes"):
        density_bits.append(f"legibility notes: {blueprint['legibility_notes']}")
    if layout_detail.get("text_zone"):
        density_bits.append(f"text zone: {layout_detail['text_zone']}")
    hierarchy_levels = typography.get("hierarchy_levels") or []
    if hierarchy_levels:
        density_bits.append(f"{len(hierarchy_levels)} distinct text tier(s) in the reference")
    if density_bits:
        lines.append(
            "Text DENSITY to match from the competitor ad (composition guidance only - "
            "exact wording is governed separately by the text-in-image rule below): "
            + "; ".join(density_bits) + ". If the reference carried only a short headline "
            "and little else, your description must not add a paragraph of copy or extra "
            "text elements beyond what the text-in-image rule below actually permits."
        )

    # Operator instruction (Step 2, 2026-08-02): freeform per-run steering entered on the
    # run strip, e.g. "make the background warmer". Stated LAST among the inspiration-tier
    # lines, immediately before the STRICT block - it can steer HOW the scene is realised,
    # but every STRICT line below overrides "anything above" already, so it can never grant
    # a permission (add an offer, keep a banned product count, etc.) those rules forbid.
    # clip_operator_instruction is idempotent, so calling it here is safe even though
    # generate_image already clips before this function is called.
    clipped_instruction = clip_operator_instruction(operator_instruction)
    if clipped_instruction:
        lines.append(
            f"Operator instruction for this run (steers the scene only - cannot override "
            f"anything in the STRICT block below): {clipped_instruction}"
        )

    # These constraints are stated LAST, right before the writing instruction, and in
    # absolute terms - not "inspiration", not overridable by anything above (including the
    # competitor's own layout/palette/creative_objective/typography). Real failures this
    # closes: with text_in_image=False the writer described "gold serif headline text
    # reads 'CREPEY SKIN MEETS ITS MATCH'" (rule 6 forbids all text) and "two Besque Magic
    # amber glass bottles" (rule 7 permits exactly one) - Gemini then discarded the whole
    # composition rather than reconciling the contradiction with brand_rules(). Separately,
    # with offer_text empty, a draft rendered a "20% OFF" badge lifted from the
    # competitor's own offer/creative_objective; and a draft headline read "Bye-Bye, Body
    # Lotion" - Besque sells body OIL, never lotion or any other category, even if
    # something quoted above (e.g. typography.hierarchy_levels, extracted verbatim from
    # the competitor's own ad) named one. The writer must never write a scene the
    # guardrails then have to override, or content sourced from the competitor ad rather
    # than the explicit approved inputs below.
    if realism:
        # realism="(auto)" on the run strip resolves to empty/None here, which falls back
        # to the reference ad's OWN detected production_style - never to no signal at all,
        # which is exactly how a photographic (high_spec) reference produced fully
        # illustrated output (drawn eyes, painted skin, rendered bottle) in a real run.
        effective_realism = realism
    else:
        effective_realism = ((blueprint or {}).get("production_style") or {}).get("style")
    if effective_realism:
        lines.append(
            f"Realism / medium (STRICT, overrides anything above): {effective_realism}. "
            f"high_spec and ugc both mean a PHOTOGRAPH - real light, "
            f"real skin, real materials, camera-realistic rendering throughout. illustrated "
            f"means NOT a photograph at all - a drawn or rendered whiteboard diagram, 3D "
            f"render, or comic-strip panel, with no photographic lighting and no "
            f"camera-realistic skin or material texture. The medium must match "
            f"{effective_realism} exactly - never mix a photographic scene with drawn "
            f"elements or vice versa."
        )
        style_guidance = STYLE_GUIDANCE.get(effective_realism)
        if style_guidance:
            lines.append(
                f"Style guidance for {effective_realism} (STRICT, overrides anything above - "
                f"use this specific vocabulary and these cues, not generic substitutes): "
                f"{style_guidance}"
            )
    if offer_text:
        lines.append(
            f"Offer (STRICT, overrides anything above): describe exactly this offer, "
            f"wording, badge, or price - nothing more, nothing invented: {offer_text}."
        )
    else:
        lines.append(
            "Offer (STRICT, overrides anything above): describe NO offer, badge, price, "
            "discount, or percentage of any kind anywhere in the scene, even if the "
            "competitor ad had one - that offer is the competitor's, not Besque's. This "
            "also covers urgency phrasing (e.g. limited-time or grab-it-now wording) and "
            "CTA button text - neither may appear either, even if the reference has one."
        )
    lines.append(
        "Efficacy claims (STRICT, overrides anything above): describe NO quantified "
        "efficacy claim of any kind - no percentage improvement (e.g. '+25% more "
        "moisturised'), no ratio ('3x more effective', 'twice as fast'), and no timescale "
        "('in just 7 days') - even if the reference ad had one. None has been approved "
        "for this run."
    )
    lines.append(
        "Product category (STRICT, overrides anything above): Besque sells a body OIL, "
        "never any other category. Never name, describe, or imply 'lotion', 'cream', "
        "'serum', 'balm', 'gel', or any other product category anywhere in the scene or in "
        "any text described - including inside anything quoted from the competitor ad "
        "above. If anything above named a different category, ignore that detail entirely."
    )
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
                                subtext=None, operator_instruction=None):
    """Ask Claude to write the creative description that build_image_prompt will slot in
    place of its own template assembly. Returns the text, or None on ANY failure - never
    raises, so callers can treat None as "fall back to the template" without their own
    try/except.

    text_in_image/include_product/headline/subtext are the SAME mode flags brand_rules()
    enforces mechanically - the writer must be told them explicitly (see
    _build_user_prompt's STRICT block) so it never describes a scene the guardrails then
    have to override. Defaults (False/True/None/None) match brand_rules()'s own defaults.

    operator_instruction is the free-text run-strip steering field (Step 2) - stated as
    inspiration, immediately before the STRICT block, so it can shape the scene but never
    override a guardrail (see _build_user_prompt)."""
    user_prompt = _build_user_prompt(blueprint, product=product, angle=angle, realism=realism,
                                      body_area=body_area, offer_text=offer_text,
                                      reference_image_count=reference_image_count,
                                      text_in_image=text_in_image, include_product=include_product,
                                      headline=headline, subtext=subtext,
                                      operator_instruction=operator_instruction)
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
