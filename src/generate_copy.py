import logging
import os
import json
import re

from src import json_response
from src.compliance_rules import COMPLIANCE_RULES
from src.compliance import PERSONAL_NAME_ATTRIBUTION_PATTERN

CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")

# Propagates to the root handler configured in pipeline.py, so these lines appear
# inline with the rest of the run log rather than on a separate stream.
log = logging.getLogger("generate_copy")

COPY_PROMPT = """You are a senior copywriter for Besque, a natural skincare brand for women 40+. Using the creative blueprint below, write Besque-adapted ad copy, following the voice, claim, and compliance rules given below.

Return ONLY valid JSON, no preamble or markdown, with exactly these fields:
- headline (string)
- primary_text (string)
- cta (string)
{image_subtext_field}

Rules:
- Do NOT mention or reference any competitor brand name.
- Do NOT copy competitor wording verbatim; adapt the angle, not the words.
- Keep claims within the APPROVED CLAIMS section below. Where it says none are supplied, stay strictly within the PRODUCT facts.
- A section reading "None supplied." means that material genuinely does not exist. Write the copy from what IS given; do not ask for the missing material and do not decline.

{compliance_rules}

BRAND VOICE GUIDE:
{brand_voice}

APPROVED CLAIMS:
{approved_claims}

APPROVED TESTIMONIALS:
{approved_testimonials}

PRODUCT (a CONSTRAINT, not a copy source: bounds what may be CLAIMED about the product - use ONLY these facts when making any product claim, never invent claims or ingredients beyond them. This is NOT where headline or hook language comes from - that is TIER 1 in ANGLE LANGUAGE below):
{product_info}

ANGLE LANGUAGE (real customer language for the selected messaging angle, tiered strictly by how it may be used - see the tier rules inside):
{angle_language_clause}

OFFER (per-run operator input - governs what offer language, if any, may appear; overrides anything the CREATIVE BLUEPRINT below shows the competitor ad had):
{offer_clause}

LANGUAGE: Write the copy in the SAME language as the competitor ad shown in the blueprint. If the blueprint's text is in Italian, write Italian; if German, German; and so on. Default to English only if the source language is unclear.

CREATIVE BLUEPRINT:
{blueprint}
"""


NO_BRAND_VOICE = (
    "None supplied. Write in a warm, plain-spoken, non-hyperbolic voice suited to a "
    "natural skincare brand for women 40+."
)
NO_APPROVED_CLAIMS = (
    "None supplied. Make claims using ONLY the PRODUCT facts below - do not introduce "
    "efficacy, clinical or timescale claims those facts do not support."
)
NO_APPROVED_TESTIMONIALS = (
    "None supplied. Do not invent, quote, or imply any customer testimonial, review, "
    "star rating, or first-person endorsement - per compliance rule C2 above."
)
NO_PRODUCT = (
    "None supplied. Refer to a Besque natural body oil in general terms only; do not "
    "invent a product name, ingredients, percentages or numeric results."
)
NO_OFFER_TEXT = (
    "No offer has been supplied for this run. headline/primary_text/image_subtext must "
    "contain NO discount, percentage, price, sale, or urgency/scarcity mechanic of any "
    "kind (e.g. \"50% off\", \"while stock lasts\", \"today only\") even if the CREATIVE "
    "BLUEPRINT below shows the competitor ad had one - that offer belongs to the "
    "competitor, not Besque. A real incident produced \"50% off - ONLY while stock "
    "lasts\" this way, lifted from the competitor's own clearance sale."
)
NO_ANGLE_LANGUAGE = (
    "No angle-specific customer language supplied for this run (no angle selected, or "
    "the angle has no language row yet). Write from the PRODUCT facts and blueprint "
    "only - do not invent customer phrasing to fill this gap, and do not substitute "
    "vocabulary from a different angle."
)

# image_subtext's field description is CONDITIONAL, computed once per call by
# _image_subtext_field (2026-08-11) - never both variants in the same prompt. Stating the
# empty-string permission unconditionally at the top AND revoking it later (once a zone is
# known to exist) would be exactly the "prompt demands and forbids the same thing" shape
# that produced artifact 1136 (see CLAUDE.md) - so the field's own description is what
# changes per call, not a later clause contradicting an earlier one.
IMAGE_SUBTEXT_FIELD_DEFAULT = (
    '- image_subtext (string): ONE short line suitable for rendering directly ONTO the '
    'image itself - under about 12 words, NOT the full primary_text body copy. Empty '
    'string "" if no short line is appropriate for this ad.'
)
IMAGE_SUBTEXT_FIELD_ZONE_PRESENT = (
    '- image_subtext (string): ONE short line suitable for rendering directly ONTO the '
    'image itself - under about 12 words, NOT the full primary_text body copy. This '
    'reference has a subtext-shaped text block that will be REMOVED from the generated '
    'image if this is left empty - a real container exists there and needs real Besque '
    'wording, not the reference\'s own text. Must NOT be empty string this time.'
)


def _image_subtext_field(has_text_zone):
    return IMAGE_SUBTEXT_FIELD_ZONE_PRESENT if has_text_zone else IMAGE_SUBTEXT_FIELD_DEFAULT

PRODUCT_FACT_KEYS = ("name",)


def _offer_clause(offer_text=""):
    """STRICT offer instruction, mirroring the image prompt-writer's own offer rule
    (generate_image_prompt_writer._build_user_prompt) so the same operator input governs
    both copy and image generation identically: exact wording only when supplied, an
    absolute ban on any offer/discount/urgency language when it isn't."""
    if offer_text:
        return (
            f"An offer has been supplied for this run: {offer_text}. Use ONLY this exact "
            f"offer/wording where an offer is mentioned - do not invent a different "
            f"number, percentage, or term, and do not add a second offer."
        )
    return NO_OFFER_TEXT


def _product_facts(product):
    """Only the fields the copywriter needs as CLAIM CONSTRAINTS - keeps id, image_key
    and category out of the prompt. Empty fields are omitted rather than sent as blanks.

    PRODUCT_FACT_KEYS is name-only (2026-08-11) - description AND ingredients were both
    removed, not just reworded. description was removed first (a marketing sentence, "A
    luxury fragrant blend of 7 cold-pressed oils.") - the 2026-08-10 fix (TIER 1 escalated
    to REQUIRED, PRODUCT reworded as a "CONSTRAINT, not a copy source") was a stronger
    instruction, not a removal, and three drafts in one run still converged on that exact
    phrasing. Removing description did NOT close the gap: ingredients ("Almond
    (hydrates...); Primrose (increases elasticity...)...") still listed "7 cold-pressed
    oils" worth of material for the model to draw the same headline from, same failure
    shape, different field. hero_claim is ALSO removed even though it's currently blanked
    in the DB (pending Harry's approved_claims) - leaving the key in PRODUCT_FACT_KEYS
    would silently start reaching this prompt again the moment that field is repopulated,
    with nothing here to catch it. name is the only field left because it's not a claim
    at all - nothing to substantiate, nothing to paraphrase into a headline."""
    if not product:
        return NO_PRODUCT
    facts = {k: product.get(k) for k in PRODUCT_FACT_KEYS
             if str(product.get(k) or "").strip()}
    return json.dumps(facts, indent=2) if facts else NO_PRODUCT


def _angle_language_clause(angle_language):
    """angle_language is a plain dict shaped like dedupe.get_angle_language()'s return
    value (or None) - this module never imports dedupe; the caller (pipeline.py) does
    the lookup and hands over a dict or None. Deliberately NOT flattened into one bag of
    "angle vocabulary": common_phrases is safe to write from directly (the customer's own
    problem language); core_angle/main_pain_point set tone but must never be echoed
    verbatim. image_direction and best_verbatims are never read here at all -
    image_direction is image-path-only context, and quote sourcing for best_verbatims-
    shaped content stays with select_testimonial_review, not this prompt.

    TIER 1 escalated from a preference to a REQUIREMENT (2026-08-10): two drafts from
    the same angle behaved differently - one built its headline from a TIER 1 phrase,
    the other fell back to paraphrasing products.description - because TIER 1 was
    optional ("prefer...") while PRODUCT read like a legitimate copy source. TIER 1 is
    now mandatory for the headline; no escape hatch for a "no fit" case was added (that
    would mean a JSON schema change) - instead the model is told to pick the CLOSEST
    phrase and adapt it, since these lists run 22-45 entries per angle, so a genuine
    no-fit case isn't realistic. PRODUCT is reworded above to state outright that it's a
    constraint, not a source - so the two sections no longer compete for the same job.

    TIER 3 (result_phrases/main_benefit) REMOVED ENTIRELY (2026-08-11), not just
    reworded: it was labeled "REFERENCE ONLY, EXPLICITLY FORBIDDEN AS COPY", but
    "forbidden to emit" was still a text instruction sitting next to the actual
    customer-reported-outcome phrases ("neck firmed up", "visibly firms" for glp1) -
    the model lifted bare words like "firmness" out of it into real headlines despite
    the ban, the same "prompt-only guardrail doesn't bind" failure pattern already
    proven on the image path. TIER 3's own stated secondary purpose - "may inform which
    problem phrase you choose from TIER 1" - doesn't survive scrutiny either: TIER 1's
    common_phrases are already scoped to this one angle (22-45 entries, all the same
    problem), so knowing the eventual benefit adds little disambiguation. TIER 2 (core_
    angle/main_pain_point) is deliberately left untouched - it's problem/emotional
    narrative, not efficacy/outcome claims, so it doesn't carry the same compliance-risk
    category TIER 3 did (checked: glp1's core_angle/main_pain_point contain no
    "firm"-rooted word at all; only the now-deleted result_phrases/main_benefit did)."""
    if not angle_language:
        return NO_ANGLE_LANGUAGE
    common_phrases = angle_language.get("common_phrases") or []
    phrase_lines = "\n".join(f"- {p}" for p in common_phrases) or "(none)"
    return (
        "TIER 1 - WRITE FROM THIS (REQUIRED). The customer's own words for her PROBLEM:\n"
        f"{phrase_lines}\n"
        "The headline MUST be built from one of these phrases - selected, or adapted, "
        "never invented from scratch and never a paraphrase of the PRODUCT facts below. "
        "If no phrase is an exact fit for this reference's layout or format, select the "
        "closest one and adapt it to fit - these lists run 22 to 45 entries per angle, "
        "so there is always a usable one. The rule is absolute: the headline comes from "
        "TIER 1, never from PRODUCT facts.\n\n"
        "TIER 2 - TONE ONLY, NEVER EMIT. Context for the emotional register the copy must "
        "sit inside. No sentence from this tier may appear in the output, verbatim or "
        "reworded, as a claim or otherwise:\n"
        f"Core angle: {angle_language.get('core_angle') or '(none)'}\n"
        f"Main pain point: {angle_language.get('main_pain_point') or '(none)'}\n\n"
        "No statistic and no timeframe (e.g. \"two weeks\", \"27 days\", \"3 months\") may "
        "appear anywhere in this copy unless it is present in a real stored quote - none "
        "is supplied to this call, regardless of how many appear in the angle language above."
    )


# Item 2 (2026-08-13, sharpened): a personal-name-shaped construct in a reference-
# derived field (testimonial_zones.attribution, or an "attributed to X"/em-dash
# signature sitting inside text_purpose.text_verbatim or a structural_zones.detail)
# must never reach a copy prompt at all - live evidence, "Sean R." (the COMPETITOR's
# own testimonial attribution) survived into generated Besque copy verbatim even
# though brand/product substitution worked correctly elsewhere on the same draft.
# Filtered here at CONSUMPTION (the point where reference text is inserted into a
# prompt), not requested at deconstruct time: this codebase's own guardrails note
# (CLAUDE.md) already proved a vision model does not reliably obey a "don't extract X"
# instruction, especially for content already sitting in the pixels/source text - a
# deterministic strip of what deconstruct.py already extracted is not subject to that
# failure mode, since nothing here asks a model to comply with anything.
#
# _JSON_ATTRIBUTION_KEY_PATTERN handles blueprint's own "attribution": "..." JSON key
# (testimonial_zones' dedicated field) when the WHOLE blueprint is dumped verbatim into
# the prompt (see build_copy_prompt's blueprint=... kwarg below) - replaced with an
# empty value rather than deleted outright, so the dumped JSON stays well-formed.
# PERSONAL_NAME_ATTRIBUTION_PATTERN (imported from src.compliance, not redefined here)
# catches the same shape wherever it appears in ordinary prose (text_verbatim/detail) -
# ONE definition shared with compliance.check_borrowed_personal_attribution (rule C9),
# so the INPUT-side filter here and the OUTPUT-side mechanical backstop there can never
# recognise the shape differently from each other.
_JSON_ATTRIBUTION_KEY_PATTERN = re.compile(r'"attribution"\s*:\s*"[^"]*"')


def _redact_personal_attribution(text):
    """Strip any personal-name-shaped construct from reference-derived text before it
    reaches a copy prompt - see the module comment above. Returns "" for falsy input,
    same contract as _normalize elsewhere in this codebase."""
    text = _JSON_ATTRIBUTION_KEY_PATTERN.sub('"attribution": ""', text or "")
    return PERSONAL_NAME_ATTRIBUTION_PATTERN.sub("", text)


# text_zone_targets/_text_zone_copy_clause/_cta_zone/_cta_zone_clause DELETED 2026-08-17:
# their sole input, structural_zones, no longer exists (schema/blueprint.schema.json -
# blueprint.objects replaces it). The per-zone-type copy precision they carried (a
# DIFFERENT line of Besque copy per sub_line/body_copy/product_callout zone, matched
# back to the image path by echoing the zone's own position string verbatim) has NO
# equivalent in the objects model and was NOT restored - Stage 2 of the objects
# restoration this session scoped product_callout down to a single substitution (the
# bare Besque product name, via generate_image_prompt._substitute_object_line), not
# per-callout benefit copy; see the handover report for this session. What WAS
# restored is the narrower, still-load-bearing half of what these functions did:
# telling generate_copy_live/validate_copy whether a real subtext/cta-shaped text block
# exists in the reference at all, so an empty cta/image_subtext against one still fails
# validation - see _has_text_purpose_object below, which replaces both existence checks
# with one exact match against each object's own text_purpose.
def _has_text_purpose_object(blueprint, purpose):
    return any(
        (obj or {}).get("kind") == "text" and (obj or {}).get("text_purpose") == purpose
        for obj in (blueprint or {}).get("objects") or []
    )


def _used_copy_clause(used_headlines):
    """Additive clause (same pattern as _text_zone_copy_clause/compliance_feedback below) -
    non-empty only when the caller (pipeline.py) is threading same-run awareness across
    ads via one shared, run-scoped list of {"headline", "image_subtext"} dicts. None or
    an empty list returns "" - the prompt is then byte-identical to before this existed,
    which is what makes a single-ad run unaffected.

    Closes a real gap, not just a temptation: nothing before this gave generate_copy_live
    any signal that a DIFFERENT ad in the SAME run had already produced copy - three
    separate calls in one run converged on near-identical wording (a live incident, see
    CLAUDE.md 2026-08-11) with no code-level leak involved, purely because every call
    shared the same PRODUCT/ANGLE LANGUAGE inputs and had zero information about what a
    sibling call already wrote. This is still a text instruction, not a mechanical check
    - same class of guardrail this codebase has repeatedly found unreliable alone - but
    unlike a ban on tempting material already in front of the model, there was previously
    no information here for the model to reason about at all; no instruction can
    substitute for that missing information, so giving it the actual list is the real
    fix, and the instruction on top of it is only enforcement, not the whole mechanism."""
    if not used_headlines:
        return ""
    lines = "\n".join(
        f'- headline: "{u.get("headline", "")}" / image_subtext: "{u.get("image_subtext", "")}"'
        for u in used_headlines
    )
    return (
        "\n\nALREADY USED EARLIER IN THIS RUN (STRICT): the following headline/"
        "image_subtext pairs were already generated for a DIFFERENT ad in this same run "
        "- your headline and image_subtext must be genuinely distinct from every one of "
        "them, not a close paraphrase or the same sentence structure with one word "
        "swapped, even if both draw from the same TIER 1 phrase:\n" + lines + "\n"
    )


# _normalize_position/_text_purpose_clause DELETED 2026-08-17: their sole shared input,
# the top-level text_purpose array, no longer exists (schema/blueprint.schema.json -
# text_purpose is now a PER-OBJECT field on blueprint.objects, one classification word,
# not a separate array of {text_verbatim, purpose, placement} entries with its own
# wording to redact/quote). _text_purpose_clause's "communicative purpose" steering
# (an offer-led reference should produce offer-led Besque copy, a problem-hook
# reference a problem-hook) has no restored equivalent - it was a copy-QUALITY aid, not
# a compliance mechanism (copy content stays bounded by APPROVED CLAIMS/TESTIMONIALS
# regardless), and Stage 2 of the objects restoration this session scoped text_purpose
# consumption to the disposition/substitution question only - see the handover report
# for this session for why this was deleted rather than rebuilt against the new
# per-object shape. _normalize_position had no other caller once
# _dedupe_text_purpose_against_zones (its own sole caller) was deleted the same session
# structural_zones was removed.


def build_copy_prompt(blueprint, brand_voice="", approved_claims="", product=None,
                       approved_testimonials="", compliance_feedback=None, offer_text="",
                       angle_language=None, used_headlines=None):
    """compliance_feedback is the list of issue strings from a prior failed
    check_compliance call - only passed on a retry, so it's appended as an explicit
    revision instruction rather than a template placeholder that's usually empty.

    offer_text is the per-run operator input (dashboard run-strip control, threaded
    exactly like realism/body_area) - never sourced from blueprint.offer, which is the
    competitor's own offer, not an authorized Besque one.

    angle_language is a plain dict (dedupe.get_angle_language()'s shape) or None - the
    caller (pipeline.py) resolves it from the selected messaging angle's slug; this
    module has no dedupe dependency and never guesses a substitute when it's None (no
    angle selected, or the angle has no language row). See _angle_language_clause for
    the two-tier treatment: common_phrases is safe to write from, core_angle/
    main_pain_point are tone-only. result_phrases/main_benefit are never read from this
    dict at all (removed 2026-08-11 - see _angle_language_clause's own docstring for
    why "forbidden to emit" wasn't enough). image_direction/best_verbatims are never
    read from this dict either - image-path-only and select_testimonial_review's job, respectively.

    used_headlines (2026-08-11) - see _used_copy_clause's own docstring. Appended the
    same additive way as panel_copy/compliance_feedback - None or [] (a single-ad run,
    or any caller that doesn't pass it) leaves the prompt byte-identical to before this
    existed.

    image_subtext_field (see _image_subtext_field) drops its "empty string is fine"
    permission whenever blueprint.objects has a real text_purpose=="subtext" object
    (see _has_text_purpose_object) - REWIRED 2026-08-17, was gated on
    text_zone_targets(blueprint) finding a sub_line/body_copy structural zone, which no
    longer exists. generate_copy_live's own require_image_subtext/require_cta read the
    identical _has_text_purpose_object check, so the prompt's own permission wording and
    the mechanical validate_copy backstop can never disagree about whether this
    reference has a zone that needs filling.

    blueprint=... below is passed through _redact_personal_attribution (item 2,
    sharpened, 2026-08-13) - the raw blueprint dump is where a reference-derived
    object's own `description` (the competitor's own testimonial name/handle, wordmark
    text, etc.) would otherwise reach Claude with no clause governing it at all. One
    redaction pass over the whole dumped JSON covers this and any other current or
    future field with the same shape, rather than a per-field filter that could miss
    one."""
    prompt = COPY_PROMPT.format(
        image_subtext_field=_image_subtext_field(_has_text_purpose_object(blueprint, "subtext")),
        brand_voice=brand_voice or NO_BRAND_VOICE,
        approved_claims=approved_claims or NO_APPROVED_CLAIMS,
        approved_testimonials=approved_testimonials or NO_APPROVED_TESTIMONIALS,
        compliance_rules=COMPLIANCE_RULES,
        blueprint=_redact_personal_attribution(json.dumps(blueprint, indent=2)),
        product_info=_product_facts(product),
        offer_clause=_offer_clause(offer_text),
        angle_language_clause=_angle_language_clause(angle_language),
    )
    if used_headlines:
        prompt += _used_copy_clause(used_headlines)
    if compliance_feedback:
        issues_text = "\n".join(f"- {issue}" for issue in compliance_feedback)
        prompt += (
            "\n\nREVISION REQUIRED: your previous attempt was rejected for the following "
            "compliance issue(s). Fix these specific problems while keeping everything else "
            "on-brief:\n" + issues_text + "\n"
        )
    return prompt


def parse_copy(raw_text):
    """Parse Claude's text response into a copy dict. Tolerates markdown fences
    and surrounding prose - see json_response.extract_json."""
    return json_response.extract_json(raw_text)


# Em dash (—), en dash (–), and the double-hyphen substitute ("--") all read as
# distinctly AI-generated punctuation in ad copy - a real draft's "The arms I'd been
# hiding — finally uncovered." is the exact tell. Mechanical strip, not a prompt request
# (COPY_PROMPT rules like "no preamble or markdown" have never reliably bound model
# output on their own - see CLAUDE.md's top note on prompt-only guardrails). Replaced
# with a plain comma+space rather than dropped outright: a dash overwhelmingly joins two
# clauses that read fine comma-joined, and dropping it with nothing in its place would
# glue two words together. Not fully safe for a numeric range ("7-10 days"), but numeric
# timeframe/efficacy language is already heavily restricted elsewhere in this file's own
# rules, so that collision is rare enough not to special-case here.
BANNED_DASH_PATTERN = re.compile(r"\s*(?:--|—|–)\s*")


def strip_banned_dashes(copy):
    """Mechanically replaces every em dash/en dash/double-hyphen in every string value of
    a parsed copy dict with a plain comma - applied uniformly to every field, never
    guessing which ones might contain one. panel_copy (see _text_zone_copy_clause) is the
    one field whose real copy text sits nested inside a list of {"position", "text"}
    dicts rather than as a plain string value - handled by name, the same way this
    module's other panel_copy-aware code already does, rather than generic recursion into
    every possible nested shape."""
    result = {}
    for k, v in copy.items():
        if isinstance(v, str):
            result[k] = BANNED_DASH_PATTERN.sub(", ", v)
        elif k == "panel_copy" and isinstance(v, list):
            result[k] = [
                {**entry, "text": BANNED_DASH_PATTERN.sub(", ", entry["text"])}
                if isinstance(entry, dict) and isinstance(entry.get("text"), str)
                else entry
                for entry in v
            ]
        else:
            result[k] = v
    return result


REQUIRED_COPY_FIELDS = {"headline", "primary_text", "cta"}

# SEASON CONTRADICTION (2026-08-12 15:13 sweep): a real draft rendered "Show it off
# this spring" as the headline with "Give your skin some love this winter" beneath it
# as body copy - the REFERENCE ad's own copy mixed seasons (a competitor running a
# stale/rolled-over creative), and both got inherited verbatim into Besque's copy with
# nothing anywhere noticing they contradict each other. This is a MECHANICAL check,
# not a prompt request - a prompt asking the model not to do this is the exact class
# of fix already proven unreliable elsewhere in this codebase (see CLAUDE.md's top
# note); this catches it deterministically after the fact instead, the same shape as
# require_cta/require_image_subtext above. Scoped to SEASON specifically, not
# "premise" generally: season names are keyword-detectable the same way
# compliance.py's other checks are; a broader problem-aware-vs-solution-aware or
# tonal premise clash has no equivalent keyword to key off and is not covered here -
# see the CLAUDE.md note recorded alongside this fix. "fall" is matched as a season
# alias for autumn and can false-positive on a non-seasonal use of the word, the same
# accepted trade-off compliance.py's own keyword checks already make.
SEASON_PATTERNS = {
    "spring": (r"\bspring\b",),
    "summer": (r"\bsummer\b",),
    "autumn": (r"\bautumn\b", r"\bfall\b"),
    "winter": (r"\bwinter\b",),
}


def _seasons_mentioned(text):
    text = (text or "").lower()
    return {season for season, patterns in SEASON_PATTERNS.items()
            if any(re.search(p, text) for p in patterns)}


def validate_copy(copy, require_cta=False, require_image_subtext=False):
    """require_cta/require_image_subtext (2026-08-11): mechanical backstop for the same
    condition _image_subtext_field's prompt text already asks for - a
    prompt instruction alone is the exact pattern that has repeatedly failed on this
    codebase (see CLAUDE.md's top note). Before this, an empty cta already passed this
    function silently: REQUIRED_COPY_FIELDS only checks the KEY exists, never that its
    value is non-empty, so a model that decided no CTA was needed could return cta=""
    with nothing here ever noticing. Callers pass True only when the reference actually
    has a matching zone (see generate_copy_live) - the empty string is perfectly valid
    whenever no such zone exists, exactly like today."""
    missing = REQUIRED_COPY_FIELDS - copy.keys()
    if missing:
        raise ValueError("Copy missing required fields: " + str(missing))
    if require_cta and not (copy.get("cta") or "").strip():
        raise ValueError(
            "cta is empty but this reference has a cta zone that needs matching wording - "
            "the zone will be removed from the image otherwise."
        )
    if require_image_subtext and not (copy.get("image_subtext") or "").strip():
        raise ValueError(
            "image_subtext is empty but this reference has a sub_line/body_copy zone that "
            "needs matching wording - the zone will be removed from the image otherwise."
        )
    combined = " ".join([copy.get("headline") or "", copy.get("primary_text") or "",
                          copy.get("image_subtext") or ""])
    seasons = _seasons_mentioned(combined)
    if len(seasons) > 1:
        raise ValueError(
            f"Generated copy names more than one season ({', '.join(sorted(seasons))}) across "
            f"headline/primary_text/image_subtext - copy must be internally consistent, one "
            f"season and one premise only. A reference ad's own copy can legitimately mix "
            f"seasons (e.g. a stale or rolled-over competitor creative) and both get inherited "
            f"verbatim if nothing catches it."
        )


def copy_from_response(raw_text, require_cta=False, require_image_subtext=False):
    copy = parse_copy(raw_text)
    copy = strip_banned_dashes(copy)
    validate_copy(copy, require_cta=require_cta, require_image_subtext=require_image_subtext)
    return copy


# ---- Live Claude copy call (wired at kickoff) ----
import anthropic


JSON_ONLY_SYSTEM = (
    "You return only raw JSON. Your entire response must begin with { and end with } - "
    "no markdown fences, no ```json marker, no preamble, no commentary, no trailing text. "
    "Keep primary_text concise enough that the JSON object always completes."
)

# (max_tokens, system) per attempt. The retry widens the token budget AND adds the
# JSON-only system prompt, because truncation and fenced/prose output both surface as
# the same "Expecting value: line 1 column 1 (char 0)" - so fix both at once.
# Deliberately not src.retry.with_retry: that repeats one callable unchanged, and
# these two attempts differ.
_COPY_ATTEMPTS = (
    (3072, None),
    (8192, JSON_ONLY_SYSTEM),
)


def _log_parse_failure(attempt, total, max_tokens, message, raw_text, exc):
    """Record exactly what came back, so an intermittent failure stays diagnosable
    from the run log even when the retry goes on to succeed."""
    usage = getattr(message, "usage", None)
    log.error("copy parse failed (attempt %s/%s): %s: %s",
              attempt, total, type(exc).__name__, exc)
    log.error("stop_reason=%r output_tokens=%s input_tokens=%s max_tokens=%s content_blocks=%s",
              getattr(message, "stop_reason", "?"),
              getattr(usage, "output_tokens", "?"),
              getattr(usage, "input_tokens", "?"),
              max_tokens,
              len(getattr(message, "content", None) or []))
    log.error("raw_text len=%s chars", len(raw_text))
    log.error("raw_text repr (first 2000): %r", raw_text[:2000])
    if len(raw_text) > 2000:
        log.error("raw_text repr (last 500): %r", raw_text[-500:])


def generate_copy_live(blueprint, brand_voice="", approved_claims="", product=None,
                        approved_testimonials="", compliance_feedback=None, offer_text="",
                        angle_language=None, used_headlines=None):
    """Send a blueprint to Claude and return validated Besque-adapted copy.

    Normally ONE API call. If the response cannot be parsed into the required fields,
    retries ONCE with a larger max_tokens and a JSON-only system prompt. Raises if the
    retry fails too. (compliance_feedback is a separate, outer retry driven by
    pipeline.py's fail-soft loop after a compliance check - not this JSON-parsing retry.)

    angle_language: see build_copy_prompt's docstring - forwarded straight through,
    None reproduces today's exact prompt (plus the new ANGLE LANGUAGE section's
    "none supplied" fallback text - see NO_ANGLE_LANGUAGE).

    used_headlines: see build_copy_prompt's/_used_copy_clause's docstrings - forwarded
    straight through, None or [] reproduces today's exact prompt.

    require_cta/require_image_subtext (2026-08-11) are derived from THIS SAME blueprint,
    once, here - never passed in by the caller - so the mechanical check in validate_copy
    can never drift from what the prompt itself just asked for (_image_subtext_field
    above reads the identical _has_text_purpose_object(blueprint, "subtext") check).
    REWIRED 2026-08-17: previously `bool(_cta_zone(blueprint))`/
    `bool(text_zone_targets(blueprint))`, both structural_zones-based and now deleted -
    see _has_text_purpose_object. A raised ValueError is caught by this function's OWN
    retry loop below exactly like any other validation failure - an empty cta/
    image_subtext against a real zone gets the same second attempt a malformed-JSON
    response would, no separate retry path invented for this."""
    prompt = build_copy_prompt(blueprint, brand_voice, approved_claims, product=product,
                                approved_testimonials=approved_testimonials,
                                compliance_feedback=compliance_feedback, offer_text=offer_text,
                                angle_language=angle_language, used_headlines=used_headlines)
    require_cta = _has_text_purpose_object(blueprint, "cta")
    require_image_subtext = _has_text_purpose_object(blueprint, "subtext")
    client = anthropic.Anthropic(timeout=60.0, max_retries=1)  # reads ANTHROPIC_API_KEY from env

    total = len(_COPY_ATTEMPTS)
    for attempt, (max_tokens, system) in enumerate(_COPY_ATTEMPTS, 1):
        # Set per attempt so the prompt the dashboard shows is the one that produced the copy.
        generate_copy_live.last_prompt = prompt if not system else f"[system] {system}\n\n{prompt}"
        kwargs = {
            "model": CLAUDE_MODEL,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        message = client.messages.create(**kwargs)
        raw_text = ""
        try:
            raw_text = message.content[0].text if message.content else ""
            return copy_from_response(raw_text, require_cta=require_cta, require_image_subtext=require_image_subtext)
        except Exception as e:
            _log_parse_failure(attempt, total, max_tokens, message, raw_text, e)
            if attempt == total:
                raise
            log.warning("retrying copy generation: max_tokens %s -> %s, adding JSON-only system prompt",
                        max_tokens, _COPY_ATTEMPTS[attempt][0])
