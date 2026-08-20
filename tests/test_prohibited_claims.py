"""Text layer completion, Part A (2026-08-20): prohibited claim phrases.

Live case: ad 1357229623024367 rendered "Clinically Proven" and "Dermatologist
Developed" - Besque makes no efficacy/authority/certification claims of any kind,
confirmed by the brand owner, not a judgment call. Four independent layers, each
tested here separately so a failure in one is diagnosable without auditing the
others:
1. compliance.py - the pattern/match function and the generated-copy mechanical
   check (rule C10).
2. deconstruct.resolve_disposition - forces "drop" for any matching object or
   text_content sub-object, checked FIRST, before every other rule.
3. generate_image_prompt._assert_no_prohibited_claim_leak - a runtime assertion at
   the end of build_image_prompt.
4. output_critic - a distinct HIGH-confidence-by-default checklist category, the
   only enforcement for a phrase Gemini reproduces directly from an attached
   reference image rather than one the text prompt introduced.
"""
import pytest

from src import compliance, deconstruct, generate_image_prompt as gip, output_critic


# ---- Layer 1: compliance.py ----

@pytest.mark.parametrize("phrase", [
    "Clinically Proven",
    "clinically-proven",
    "Clinically  Tested",
    "Dermatologist Approved",
    "Dermatologist-Developed",
    "dermatologist recommended",
    "Doctor Approved",
    "Doctor-Recommended",
    "Medically Proven",
    "Scientifically Proven",
    "Board-Certified",
    "board certified",
])
def test_prohibited_claim_match_catches_variants(phrase):
    match = compliance.prohibited_claim_match(f"Some text {phrase} more text")
    assert match is not None
    assert match.lower() == phrase.lower()


def test_prohibited_claim_match_none_for_clean_text():
    assert compliance.prohibited_claim_match("Nourish your skin with Besque Magic Body Oil") is None


def test_prohibited_claim_match_none_for_empty_or_none():
    assert compliance.prohibited_claim_match("") is None
    assert compliance.prohibited_claim_match(None) is None


def test_check_prohibited_claim_flags_generated_copy():
    issues = compliance.check_prohibited_claim({
        "headline": "Clinically Proven Results",
        "primary_text": "Feel the difference today.",
    })
    assert len(issues) == 1
    assert "Clinically Proven" in issues[0]


def test_check_prohibited_claim_no_approved_claims_escape_hatch():
    """Unlike an ordinary numeric claim, a prohibited phrase is never approvable -
    check_prohibited_claim takes no approved_claims parameter at all."""
    import inspect
    sig = inspect.signature(compliance.check_prohibited_claim)
    assert "approved_claims" not in sig.parameters


def test_check_compliance_includes_prohibited_claim_issue():
    ok, issues = compliance.check_compliance(
        {"headline": "Dermatologist Developed Formula"}, competitor_page_name="Rival Co",
    )
    assert ok is False
    assert any("Dermatologist Developed" in i for i in issues)


# ---- Layer 2: deconstruct.resolve_disposition ----

def _text_obj(object_id, description="", persuasive_function="", **overrides):
    base = {
        "object_id": object_id, "kind": "text", "description": description,
        "persuasive_function": persuasive_function, "text_purpose": "product_callout",
        "ownership": "generic", "carries_brand_mark": False, "disposition": "keep",
    }
    base.update(overrides)
    return base


def test_resolve_disposition_drops_prohibited_claim_in_description():
    obj = _text_obj("obj_10", description="Dermatologist Developed",
                     persuasive_function="Created by a board-certified dermatologist")
    assert deconstruct.resolve_disposition(obj) == "drop"


def test_resolve_disposition_prohibited_claim_wins_over_always_substitute_purpose():
    """product_callout is unconditionally in _TEXT_PURPOSE_ALWAYS_SUBSTITUTE - the
    prohibited-claim check must still win, since it is checked FIRST."""
    obj = _text_obj("obj_10", description="Clinically Proven", text_purpose="product_callout")
    assert deconstruct.resolve_disposition(obj) == "drop"


def test_resolve_disposition_prohibited_claim_wins_over_headline_purpose():
    obj = _text_obj("obj_11", description="Dermatologist Approved", text_purpose="headline")
    assert deconstruct.resolve_disposition(obj) == "drop"


def test_resolve_disposition_prohibited_claim_wins_over_part_of_substitute_parent():
    """Checked before the part_of branch too - same structural position as the
    docstring states ('same position as part_of inheritance', checked first)."""
    obj = _text_obj("obj_12", description="Scientifically Proven", part_of="obj_hero")
    assert deconstruct.resolve_disposition(
        obj, part_of_parent_disposition="substitute") == "drop"


def test_resolve_disposition_clean_text_object_unaffected():
    obj = _text_obj("obj_13", description="Deeply Nourishing", text_purpose="product_callout")
    assert deconstruct.resolve_disposition(obj) == "substitute"


def test_resolve_disposition_drops_prohibited_claim_on_text_content_sub_object():
    """text_content sub-objects have no `kind`/`description` - they carry `content`
    directly, dispatched via _prohibited_claim_text."""
    sub = {"object_id": "obj_08_txt_01", "content": "Clinically Proven", "bbox": [0, 0, 0.2, 0.05],
           "text_purpose": "other", "ownership": "generic", "carries_brand_mark": False,
           "disposition": "keep"}
    assert deconstruct.resolve_disposition(sub) == "drop"


def _mock_warnings(monkeypatch):
    from src import dedupe
    captured = []
    monkeypatch.setattr(dedupe, "init_pipeline_warnings", lambda: None)
    monkeypatch.setattr(dedupe, "record_warning",
                         lambda kind, detail: captured.append((kind, detail)))
    return captured


def test_deconstruct_time_resolution_records_prohibited_claim_dropped_warning(monkeypatch):
    warnings = _mock_warnings(monkeypatch)
    blueprint = {"ad_id": "AD1", "objects": [
        _text_obj("obj_10", description="Dermatologist Developed",
                  persuasive_function="Created by a board-certified dermatologist"),
    ]}
    resolved = deconstruct._resolve_object_dispositions(blueprint)
    assert resolved["objects"][0]["disposition"] == "drop"
    kinds = [k for k, _ in warnings]
    assert "prohibited_claim_dropped" in kinds
    detail = next(d for k, d in warnings if k == "prohibited_claim_dropped")
    assert "obj_10" in detail
    assert "AD1" in detail


def test_deconstruct_time_resolution_flags_prohibited_claim_on_sub_object(monkeypatch):
    warnings = _mock_warnings(monkeypatch)
    blueprint = {"ad_id": "AD1", "objects": [
        {"object_id": "obj_container", "kind": "graphic", "role": "secondary",
         "description": "a callout badge", "ownership": "generic",
         "carries_brand_mark": False, "disposition": "keep",
         "text_content": [
             {"object_id": "obj_container_txt_01", "content": "Clinically Proven",
              "bbox": [0, 0, 0.3, 0.1], "text_purpose": "other",
              "ownership": "generic", "carries_brand_mark": False, "disposition": "keep"},
         ]},
    ]}
    resolved = deconstruct._resolve_object_dispositions(blueprint)
    sub = resolved["objects"][0]["text_content"][0]
    assert sub["disposition"] == "drop"
    kinds = [k for k, _ in warnings]
    assert "prohibited_claim_dropped" in kinds


# ---- Layer 3: build_image_prompt assertion ----

def test_assert_no_prohibited_claim_leak_raises_on_match():
    with pytest.raises(gip.ProhibitedClaimLeakError):
        gip._assert_no_prohibited_claim_leak(
            "This ad is Clinically Proven to work.", ad_id="AD1")


def test_assert_no_prohibited_claim_leak_passes_on_clean_prompt():
    gip._assert_no_prohibited_claim_leak("Nourish your skin with real oils.", ad_id="AD1")


def test_prohibited_claim_leak_error_is_distinct_exception_type():
    assert gip.ProhibitedClaimLeakError is not gip.TextContentLeakError
    assert issubclass(gip.ProhibitedClaimLeakError, RuntimeError)


def test_build_image_prompt_never_leaks_dropped_prohibited_claim_object(monkeypatch):
    """End-to-end reproduction of the real ad 1357229623024367 case: a
    product_callout-purposed object whose OWN description is the prohibited
    phrase must never reach the assembled prompt at all - resolve_disposition
    drops it before _objects_clause ever emits a SUBSTITUTE/KEEP line for it."""
    from tests.blueprint_fixtures import load_blueprint_fixture
    monkeypatch.setattr("src.dedupe.init_pipeline_warnings", lambda: None)
    monkeypatch.setattr("src.dedupe.record_warning", lambda kind, detail: None)
    blueprint = load_blueprint_fixture("osea_two_products_both_substitute")
    blueprint = dict(blueprint)
    blueprint["objects"] = list(blueprint["objects"]) + [
        _text_obj("obj_90", description="Dermatologist Developed",
                  persuasive_function="Created by a board-certified dermatologist",
                  disposition="keep"),
    ]
    product = {
        "name": "Magic Body Oil",
        "visual_description": "a tall cylindrical bottle with a gold collar and black pump",
        "substance_colour": "golden-amber oil",
        "certifications": ["Vegan", "Cruelty Free", "100% Natural"],
    }
    prompt = gip.build_image_prompt(
        blueprint, product=product, include_product=True, edit_mode=True, realism=None,
    )
    assert "Dermatologist Developed" not in prompt
    assert "board-certified dermatologist" not in prompt
    assert "ABSENT" in prompt


# ---- Layer 4: output_critic checklist ----

def test_prohibited_claim_category_in_high_confidence_by_default():
    assert "prohibited efficacy/authority/certification claim" in output_critic.HIGH_CONFIDENCE_BY_DEFAULT


def test_critic_system_mentions_prohibited_claim_examples():
    assert "Clinically Proven" in output_critic.CRITIC_SYSTEM
    assert "Dermatologist" in output_critic.CRITIC_SYSTEM
    assert "C10" in output_critic.CRITIC_SYSTEM
