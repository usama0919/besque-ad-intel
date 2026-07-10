"""Compliance check: verifies generated Besque output contains no competitor
brand name or verbatim competitor copy. Acceptance criterion enforcement.
"""
import re


def _normalize(text):
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def check_compliance(generated_copy, competitor_page_name, competitor_text=""):
    """Return (ok: bool, issues: list[str]).

    Flags:
      - competitor brand/page name appearing in generated copy
      - any long verbatim run (>=6 words) copied from the competitor's ad text
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

    return (len(issues) == 0, issues)
