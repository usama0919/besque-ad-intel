"""Text layer completion, Part D (2026-08-20): verbatim binding for testimonials
and claims.

Fabricated testimonials (e.g. "Leah E.") still occurred despite select_testimonial_
review already drawing exclusively from real product_reviews/angle_language.
best_verbatims rows - the actual gaps were: (1) a generic invented attribution
fallback ("a verified customer") used whenever the real source row carried none;
(2) no independently-attributable warning when a testimonial slot structurally
removes for lack of a source; (3) a bare competitor-story duration ("12 years")
matched none of the existing stat-claim patterns; (4) the critic's own confidence
judgement was the ONLY gate on an unauthorised testimonial finding, the exact
channel a fabrication has repeatedly slipped through on. Four items, tested
separately below."""
import pytest

from src import compliance, deconstruct, generate_image_prompt as gip, output_critic, pipeline


# ---- Item 1: attribution only as it exists in the source row, or none at all ----

def test_substitute_object_line_renders_real_attribution_when_present():
    obj = {"object_id": "obj_09", "description": "a customer quote"}
    context = {"testimonial": {"quote": "This oil changed my skin.", "attribution": "Leah E."}}
    line = gip._substitute_object_line(obj, "text", "testimonial", obj["description"], context)
    assert "Leah E." in line
    assert "a verified customer" not in line


def test_substitute_object_line_no_attribution_never_invents_a_placeholder():
    obj = {"object_id": "obj_09", "description": "a customer quote"}
    context = {"testimonial": {"quote": "Great oil.", "attribution": ""}}
    line = gip._substitute_object_line(obj, "text", "testimonial", obj["description"], context)
    assert "a verified customer" not in line
    assert "attributed to" not in line.lower()
    assert "no name" in line.lower() or "no attribution" in line.lower()


def test_substitute_object_line_missing_attribution_key_never_invents_a_placeholder():
    """testimonial.get("attribution") with the key absent entirely (not just an
    empty string) must behave identically to an explicit ""."""
    obj = {"object_id": "obj_09", "description": "a customer quote"}
    context = {"testimonial": {"quote": "Great oil."}}
    line = gip._substitute_object_line(obj, "text", "testimonial", obj["description"], context)
    assert "a verified customer" not in line


# ---- Item 2: no matching verbatim -> the slot removes structurally ----

def test_resolve_text_disposition_testimonial_drops_when_no_context_supplied():
    obj = {"object_id": "obj_t1", "kind": "text", "text_purpose": "testimonial",
           "ownership": "generic", "carries_brand_mark": False, "disposition": "keep"}
    assert deconstruct.resolve_disposition(obj, context=None) == "drop"
    assert deconstruct.resolve_disposition(obj, context={}) == "drop"
    assert deconstruct.resolve_disposition(obj, context={"testimonial": None}) == "drop"


def test_resolve_text_disposition_testimonial_substitutes_only_with_real_context():
    obj = {"object_id": "obj_t1", "kind": "text", "text_purpose": "testimonial",
           "ownership": "generic", "carries_brand_mark": False, "disposition": "keep"}
    context = {"testimonial": {"quote": "Real review.", "attribution": "Sam K."}}
    assert deconstruct.resolve_disposition(obj, context=context) == "substitute"


def test_select_testimonial_review_returns_none_never_a_placeholder(monkeypatch):
    """Never left empty with a 'do not invent' instruction - callers must treat
    None as 'remove the object' (the function's own documented contract)."""
    from src import dedupe
    monkeypatch.setattr(dedupe, "init_pipeline_warnings", lambda: None)
    monkeypatch.setattr(dedupe, "record_warning", lambda kind, detail: None)
    monkeypatch.setattr(dedupe, "get_reviews_for_product", lambda product_id: [])
    blueprint = {"objects": [
        {"kind": "text", "text_purpose": "testimonial", "object_id": "obj_t1"},
    ]}
    result = pipeline.select_testimonial_review(blueprint, {"id": 1}, "AD1")
    assert result is None


def test_select_testimonial_review_records_testimonial_slot_removed_warning(monkeypatch):
    from src import dedupe
    captured = []
    monkeypatch.setattr(dedupe, "init_pipeline_warnings", lambda: None)
    monkeypatch.setattr(dedupe, "record_warning",
                         lambda kind, detail: captured.append((kind, detail)))
    monkeypatch.setattr(dedupe, "get_reviews_for_product", lambda product_id: [])
    blueprint = {"objects": [
        {"kind": "text", "text_purpose": "testimonial", "object_id": "obj_t1"},
    ]}
    result = pipeline.select_testimonial_review(blueprint, {"id": 1}, "AD1")
    assert result is None
    kinds = [k for k, _ in captured]
    assert "testimonial_slot_removed_no_source" in kinds
    detail = next(d for k, d in captured if k == "testimonial_slot_removed_no_source")
    assert "AD1" in detail


def test_select_testimonial_review_no_warning_when_no_testimonial_wanted(monkeypatch):
    """wants_quote is False - nothing was ever supposed to render, so this is not
    a removal and must not be reported as one."""
    from src import dedupe
    captured = []
    monkeypatch.setattr(dedupe, "init_pipeline_warnings", lambda: None)
    monkeypatch.setattr(dedupe, "record_warning",
                         lambda kind, detail: captured.append((kind, detail)))
    blueprint = {"objects": [{"kind": "text", "text_purpose": "headline", "object_id": "obj_h1"}]}
    result = pipeline.select_testimonial_review(blueprint, {"id": 1}, "AD1")
    assert result is None
    assert captured == []


def test_select_testimonial_review_records_warning_for_missing_product_id(monkeypatch):
    from src import dedupe
    captured = []
    monkeypatch.setattr(dedupe, "init_pipeline_warnings", lambda: None)
    monkeypatch.setattr(dedupe, "record_warning",
                         lambda kind, detail: captured.append((kind, detail)))
    blueprint = {"objects": [{"kind": "text", "text_purpose": "testimonial", "object_id": "obj_t1"}]}
    result = pipeline.select_testimonial_review(blueprint, {}, "AD1")
    assert result is None
    assert any(k == "testimonial_slot_removed_no_source" for k, _ in captured)


def test_select_testimonial_review_records_warning_for_angle_with_no_match(monkeypatch):
    from src import dedupe
    captured = []
    monkeypatch.setattr(dedupe, "init_pipeline_warnings", lambda: None)
    monkeypatch.setattr(dedupe, "record_warning",
                         lambda kind, detail: captured.append((kind, detail)))
    monkeypatch.setattr(dedupe, "get_angle_language", lambda slug: None)
    blueprint = {"objects": [{"kind": "text", "text_purpose": "testimonial", "object_id": "obj_t1"}]}
    result = pipeline.select_testimonial_review(blueprint, {"id": 1}, "AD1", angle_slug="crepey_skin")
    assert result is None
    assert any(k == "testimonial_slot_removed_no_source" for k, _ in captured)


# ---- Item 3: any claim with a number/duration/result must trace to a source row ----

@pytest.mark.parametrize("text", [
    "After 12 years of trying everything, I finally found relief.",
    "3 months of consistent use changed everything.",
    "It took 6 weeks to notice a real difference.",
])
def test_duration_claim_pattern_matches_bare_competitor_durations(text):
    assert compliance.DURATION_CLAIM_PATTERN.search(text) is not None


def test_duration_claim_pattern_does_not_match_plain_text():
    assert compliance.DURATION_CLAIM_PATTERN.search("Nourish your skin every day.") is None


def test_check_unauthorized_efficacy_claim_flags_unapproved_duration():
    issues = compliance.check_unauthorized_efficacy_claim(
        {"primary_text": "After 12 years of struggling, this finally worked."})
    assert any("Duration" in i and "12 years" in i for i in issues)


def test_check_unauthorized_efficacy_claim_allows_approved_duration():
    issues = compliance.check_unauthorized_efficacy_claim(
        {"primary_text": "After 12 years of struggling, this finally worked."},
        approved_claims="12 years",
    )
    assert issues == []


def test_is_stat_shaped_text_drops_duration_claim_on_product_callout():
    obj = {"object_id": "obj_c1", "kind": "text", "text_purpose": "product_callout",
           "description": "12 years of frustration, gone", "ownership": "generic",
           "carries_brand_mark": False, "disposition": "keep"}
    assert deconstruct._is_stat_shaped_text(obj)
    assert deconstruct.resolve_disposition(obj) == "drop"


def test_is_stat_shaped_text_duration_on_text_content_sub_object():
    """Sub-objects dispatch via _prohibited_claim_text (content, not description) -
    this is the exact dispatch bug fixed earlier this session (2026-08-20) that a
    sub-object carrying a duration claim in its own `content` needs, not a new
    behaviour introduced by Part D."""
    sub = {"object_id": "obj_c1_txt_01", "content": "12 years of frustration, gone",
           "bbox": [0, 0, 0.3, 0.1], "text_purpose": "other", "ownership": "generic",
           "carries_brand_mark": False, "disposition": "keep"}
    assert deconstruct._is_stat_shaped_text(sub)
    assert deconstruct.resolve_disposition(sub) == "drop"


def test_duration_claim_on_headline_purpose_not_forced_to_drop_by_stat_check():
    """Scoped identically to the original pre-refactor stat-shape check
    (product_callout/other/None only) - headline/subtext wording is never copied
    from the reference, so forcing the SLOT to drop would only delete a headline
    position Besque's own (non-stat) wording still needs to occupy."""
    obj = {"object_id": "obj_h1", "kind": "text", "text_purpose": "headline",
           "description": "12 years in the making", "ownership": "generic",
           "carries_brand_mark": False, "disposition": "keep"}
    assert deconstruct.resolve_disposition(obj) == "substitute"


# ---- Item 4: critic gate - unauthorised testimonial forces failed-review ----

def _finding(category, confidence="low"):
    return {"category": category, "description": "some description", "confidence": confidence}


def test_has_unauthorised_testimonial_finding_true_for_testimonial_category():
    findings = [_finding("testimonial", confidence="low")]
    assert output_critic.has_unauthorised_testimonial_finding(findings) is True


def test_has_unauthorised_testimonial_finding_false_when_no_findings():
    assert output_critic.has_unauthorised_testimonial_finding([]) is False
    assert output_critic.has_unauthorised_testimonial_finding(None) is False


def test_has_unauthorised_testimonial_finding_false_for_unrelated_category():
    findings = [_finding("unauthorised offer", confidence="high")]
    assert output_critic.has_unauthorised_testimonial_finding(findings) is False


def test_has_unauthorised_testimonial_finding_gates_regardless_of_confidence():
    """The exact point of item 4 - confidence is irrelevant to this gate."""
    for confidence in ("low", "medium", "high"):
        findings = [_finding("fabricated review quote", confidence=confidence)]
        assert output_critic.has_unauthorised_testimonial_finding(findings) is True


def test_has_unauthorised_testimonial_finding_ignores_findings_already_dropped_as_authorised():
    """Caller contract: called AFTER drop_findings_contradicted_by_authorised - a
    correctly-authorised testimonial's own re-flagged mention must not reach this
    gate at all once that filter has already removed it."""
    testimonial = {"quote": "This oil changed my skin completely.", "attribution": "Sam K."}
    findings = [{
        "category": "testimonial", "confidence": "medium",
        "description": 'The quote "This oil changed my skin completely." attributed to "Sam K." appears.',
    }]
    filtered = output_critic.drop_findings_contradicted_by_authorised(findings, testimonial=testimonial)
    assert filtered == []
    assert output_critic.has_unauthorised_testimonial_finding(filtered) is False


def test_pipeline_regenerate_path_wires_unauthorised_testimonial_gate():
    """Source-inspection proof of wiring, matching this codebase's own established
    pattern for proving a mechanism is actually reachable from its real call site
    (rather than mocking the entire regenerate flow's DB/API surface) - the
    regenerate/check-only review_status computation must reference
    has_unauthorised_testimonial_finding, not just has_high_confidence alone."""
    import inspect
    source = inspect.getsource(pipeline)
    # Only one review_status assignment site should exist per branch; confirm the
    # new gate appears at least twice (regenerate path + main retry-loop path).
    assert source.count("has_unauthorised_testimonial_finding") >= 2
