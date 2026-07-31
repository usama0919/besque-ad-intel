"""Tests for the hard content-safety block (Prompt 4, Item 3). Unlike output_critic or
the bundle-format flag, this is NOT a judgment call - hard_block_reason() only fires on
an actual medical/clinical/anatomical keyword hit combined with a non-product-like
category, and the caller must skip before any generation, never flag afterward."""
from src import content_safety


def _blueprint(subject="", signals=None, category="not_product", headline="", fmt="", angle=""):
    return {
        "visual": {"subject": subject},
        "product_category": {"category": category, "signals": signals or []},
        "hook": {"headline_structure": headline},
        "format": fmt,
        "angle": angle,
    }


# ---- The exact reported failure ----

def test_hemorrhoid_ad_with_not_product_category_is_blocked():
    bp = _blueprint(
        subject="anatomical before/after illustration of a hemorrhoid treatment",
        category="not_product",
    )
    reason = content_safety.hard_block_reason(bp)
    assert reason is not None
    assert "medical" in reason.lower() or "anatom" in reason.lower()


def test_hemorrhoid_keyword_in_product_category_signals_is_blocked():
    bp = _blueprint(subject="", signals=["hemorrhoid treatment demonstration"], category="other")
    assert content_safety.hard_block_reason(bp) is not None


# ---- Combination requirement: medical keyword ALONE is not enough ----

def test_medical_keyword_with_ordinary_product_category_is_not_blocked():
    """A keyword hit alongside a normal product classification (body_oil) is treated as
    a loose word choice ("hair treatment"), not a genuine medical reference - the
    combination with not_product/other is what actually blocks."""
    bp = _blueprint(subject="a medical-grade hair treatment oil", category="body_oil")
    assert content_safety.hard_block_reason(bp) is None


def test_not_product_category_alone_without_medical_signal_is_not_blocked():
    """not_product alone (e.g. an ordinary tester/ambassador-recruitment ad) must not
    block - only the COMBINATION with an actual medical/clinical/anatomical signal does."""
    bp = _blueprint(subject="a founder telling her brand story", category="not_product")
    assert content_safety.hard_block_reason(bp) is None


def test_clean_skincare_ad_is_never_blocked():
    bp = _blueprint(subject="woman applying body oil in a bright bathroom", category="body_oil")
    assert content_safety.hard_block_reason(bp) is None


# ---- Signal sources: visual.subject, product_category.signals, hook, format, angle ----

def test_keyword_detected_via_hook_headline_structure():
    bp = _blueprint(headline="anatomical diagram + before/after claim", category="not_product")
    assert content_safety.hard_block_reason(bp) is not None


def test_keyword_detected_via_format():
    bp = {"visual": {"subject": ""}, "product_category": {"category": "other", "signals": []},
          "hook": {}, "format": "clinical diagram walkthrough", "angle": ""}
    assert content_safety.hard_block_reason(bp) is not None


def test_keyword_detected_via_angle():
    bp = {"visual": {"subject": ""}, "product_category": {"category": "not_product", "signals": []},
          "hook": {}, "format": "", "angle": "surgical recovery testimonial"}
    assert content_safety.hard_block_reason(bp) is not None


# ---- Robustness: missing fields, empty blueprint ----

def test_empty_blueprint_is_never_blocked():
    assert content_safety.hard_block_reason({}) is None
    assert content_safety.hard_block_reason(None) is None


def test_missing_product_category_treated_as_non_product():
    """No product_category at all (should never happen post-schema-validation, but the
    check must not crash) - category resolves to None, one of the blocking categories."""
    bp = {"visual": {"subject": "anatomical illustration"}}
    assert content_safety.hard_block_reason(bp) is not None


def test_reason_names_the_matched_keyword_and_category():
    bp = _blueprint(subject="an anatomical illustration", category="not_product")
    reason = content_safety.hard_block_reason(bp)
    assert "anatomical" in reason
    assert "not_product" in reason
    assert "not a judgment call" in reason
