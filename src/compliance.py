"""Compliance check: verifies generated Besque output contains no competitor
brand name, no verbatim competitor copy, and no fabricated testimonial or
unsubstantiated numeric claim (compliance rule C2 - see src/compliance_rules.py).
Acceptance criterion enforcement.

The testimonial/claim checks are a MECHANICAL BACKSTOP, not the primary defense -
prompt instructions (COMPLIANCE_RULES, sent to the model) are the primary defense.
This is regex/keyword pattern-matching, not semantic understanding: it reliably
catches quoted speech, first-person purchase/experience phrasing, and reported-
speech framing ("customers say..."), which is the exact failure class that
prompted rule C2. It will NOT catch a fabricated claim phrased to avoid all of
these patterns (e.g. a hedged third-person claim with no quote marks, no "I", and
no reported-speech marker). False positives should be rare, since Besque's brand
voice is never written in first person or with quoted/reported speech - a hit here
is almost always real.
"""
import re


def _normalize(text):
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _shares_word_run(candidate_norm, source_norm, run_len=6):
    """True if any `run_len`-word run from source_norm appears in candidate_norm.
    Falls back to plain substring containment when source is shorter than run_len
    words (e.g. a short approved testimonial), so a short supplied testimonial isn't
    penalized for being short."""
    if not source_norm:
        return False
    source_words = source_norm.split()
    if len(source_words) < run_len:
        return source_norm in candidate_norm
    for i in range(len(source_words) - run_len + 1):
        phrase = " ".join(source_words[i:i + run_len])
        if phrase in candidate_norm:
            return True
    return False


def _sentence_containing(text, pos):
    """The sentence (rough, punctuation-split) containing position `pos` in `text` -
    used to check a matched pattern's surrounding context against approved material,
    rather than just the few matched words."""
    starts = [text.rfind(c, 0, pos) for c in ".!?"]
    start = max(starts) + 1
    ends = [i for i in (text.find(c, pos) for c in ".!?") if i != -1]
    end = min(ends) if ends else len(text)
    return text[start:end].strip()


QUOTE_PATTERN = re.compile(r'["“]([^"”]{12,})["”]')

FIRST_PERSON_PATTERN = re.compile(
    r"\bI(?:'m|'ve|'d)?\b[^.!?]{0,60}\b(ordered|bought|received|tried|notic\w*|felt|"
    r"love[d]?|recommend|us(?:e|ed|ing)|started|switched|repurchased|purchased)\b",
    re.IGNORECASE,
)

REPORTED_SPEECH_PATTERN = re.compile(
    r"\b(customers?|users?|reviewers?|clients?)\s+"
    r"(say|says|report(?:s|ed)?|rave(?:s)?|claim(?:s)?|swear(?:s)?\s+by)\b",
    re.IGNORECASE,
)

NUMERIC_CLAIM_PATTERN = re.compile(r"\b\d{1,3}\s?%|\b\d+\s+out\s+of\s+\d+\b", re.IGNORECASE)

# "20% off" / "save 20%" is a price promotion, not a quantified efficacy claim - only
# percentages get this exemption, and only when a discount word sits DIRECTLY adjacent
# to that specific percentage (whitespace only between them). Deliberately NOT a wide
# character-window: a window is fooled the moment two unrelated numbers sit within it,
# e.g. "20% off today, 94% saw results" - "94%" is a genuine unsubstantiated efficacy
# claim just 14 characters from "off" and a window would wrongly exempt it too.
# "N out of M" is never a discount format, so it's never exempted.
DISCOUNT_PERCENTAGE_PATTERN = re.compile(
    r"\b(?:save|off|discount(?:ed)?|sale|reduced|deal|promo)\b\s*\d{1,3}\s?%"
    r"|\d{1,3}\s?%\s*\b(?:off|discount(?:ed)?|sale|reduced|deal|promo)\b",
    re.IGNORECASE,
)


def _is_discount_percentage(text, match):
    if "%" not in match.group(0):
        return False
    return any(d.start() <= match.start() and match.end() <= d.end()
               for d in DISCOUNT_PERCENTAGE_PATTERN.finditer(text))


def check_fabricated_testimonial(generated_copy, approved_testimonials=""):
    """Rule C2 mechanical check: quoted speech, first-person endorsement patterns,
    and reported-speech framing. Any hit is allowed through only if the flagged
    text shares real content with approved_testimonials (a 6-word run, or full
    containment for a short supplied testimonial) - otherwise it's flagged as
    fabricated. approved_testimonials is empty in current real usage, so every
    hit is flagged today; that's intentional, not a bug."""
    issues = []
    gen = " ".join(str(v) for v in generated_copy.values())
    approved_norm = _normalize(approved_testimonials)

    for match in QUOTE_PATTERN.finditer(gen):
        quoted = match.group(1)
        if len(quoted.split()) < 4:
            continue  # short quoted phrase (e.g. a single emphasized word) - not testimonial-shaped
        if _shares_word_run(_normalize(quoted), approved_norm):
            continue
        issues.append(
            f"Quoted customer-style speech with no matching APPROVED TESTIMONIALS material: \"{quoted}\""
        )

    for pattern, label in ((FIRST_PERSON_PATTERN, "First-person customer-endorsement pattern"),
                           (REPORTED_SPEECH_PATTERN, "Reported-speech testimonial framing")):
        match = pattern.search(gen)
        if not match:
            continue
        sentence = _sentence_containing(gen, match.start())
        if _shares_word_run(_normalize(sentence), approved_norm):
            continue
        issues.append(
            f"{label} with no matching APPROVED TESTIMONIALS material: '{sentence}'"
        )

    return issues


def check_unapproved_numeric_claims(generated_copy, approved_claims=""):
    """Rule C2/C3 mechanical check: any quantified/numeric EFFICACY claim (percentage,
    or "N out of M") must be explicitly present in APPROVED CLAIMS. A price/discount
    percentage ("20% off", "save 20%") is exempt - see _is_discount_percentage - since
    it's a promotion, not an efficacy claim, and appears on legitimate drafts today.
    approved_claims is empty in current real usage (pipeline.py does not pass it), so
    today this flags every non-exempt numeric claim - intentional, not a bug: with
    nothing supplied to substantiate a number, any number in generated copy is by
    definition unsubstantiated under today's actual usage. The message says so
    explicitly so it doesn't misread as a false positive once real approved claims
    exist."""
    issues = []
    gen = " ".join(str(v) for v in generated_copy.values())
    approved_norm = _normalize(approved_claims)
    for match in NUMERIC_CLAIM_PATTERN.finditer(gen):
        if _is_discount_percentage(gen, match):
            continue
        claim = match.group(0)
        if approved_norm and _normalize(claim) in approved_norm:
            continue
        reason = ("no APPROVED CLAIMS were supplied to substantiate it (approved_claims is empty)"
                  if not approved_norm else
                  "it does not appear in the supplied APPROVED CLAIMS")
        issues.append(
            f"Numeric/quantified claim '{claim}' found in generated copy but {reason} - "
            f"flagged as likely fabricated, not a false positive."
        )
    return issues


def check_compliance(generated_copy, competitor_page_name, competitor_text="",
                      approved_claims="", approved_testimonials=""):
    """Return (ok: bool, issues: list[str]).

    Flags:
      - competitor brand/page name appearing in generated copy
      - any long verbatim run (>=6 words) copied from the competitor's ad text
      - fabricated testimonials / unapproved quoted or first-person endorsement (rule C2)
      - unapproved numeric/quantified claims (rule C2/C3)
    """
    issues = []
    gen = " ".join(str(v) for v in generated_copy.values())
    gen_norm = _normalize(gen)

    # 1. Competitor brand name present
    name = _normalize(competitor_page_name)
    if name and name in gen_norm:
        issues.append(f"Competitor name '{competitor_page_name}' appears in generated copy")

    # 2. Verbatim copy: any 6-word run from competitor text reused
    comp_words = _normalize(competitor_text).split()
    if len(comp_words) >= 6:
        for i in range(len(comp_words) - 5):
            phrase = " ".join(comp_words[i:i + 6])
            if phrase in gen_norm:
                issues.append(f"Verbatim competitor phrase reused: '{phrase}'")
                break

    # 3. Fabricated testimonials (rule C2)
    issues.extend(check_fabricated_testimonial(generated_copy, approved_testimonials))

    # 4. Unapproved numeric/quantified claims (rule C2/C3)
    issues.extend(check_unapproved_numeric_claims(generated_copy, approved_claims))

    return (len(issues) == 0, issues)
