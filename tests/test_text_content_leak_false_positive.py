"""Fix for a false positive in _assert_no_text_content_leak (2026-08-21).

The assertion (added 2026-08-20, see tests/test_text_content_leak_sites.py) scans
every objects[].text_content[].content string and raises TextContentLeakError if it
appears verbatim anywhere in the assembled prompt - a whole-prompt substring test.
That's correct when the ONLY way the string could be in the prompt is via the leak
itself, but it also fires when the prompt legitimately contains that exact string
from a completely different, authorised source - e.g. a text_content entry recording
"Magic Body Oil" (real on-image text the vision model detected) coincidentally
matches the Besque product's own name, which product_clause always states in the
prompt regardless of any object. That is not a leak; the assertion had no way to
tell the two apart.

Fixed via _legitimate_prompt_source_strings, which builds a {normalized: source_name}
map from this call's own runtime data (the product row, this run's copy/operator
inputs - never a hardcoded literal) and _assert_no_text_content_leak now checks a
verbatim match against that map BEFORE raising. A match is skipped (not raised) but
still recorded via dedupe.record_warning (kind
text_content_leak_matched_legitimate_source) naming the ad, the object/sub-object id,
and which source field matched - so a real leak that happens to collide with a
legitimate source stays visible. Anything not explained by the map still raises,
unchanged.

SECOND FIX (2026-08-21, same file): the first version above matched by EQUALITY
against the whole normalised field value, which missed the actual reported case -
two ads whose detected on-image text was a single TOKEN ("BESQUE", "MAGIC"), a
fragment of the full "Besque Magic Body Oil" field, never the whole field verbatim.
_find_legitimate_source_match now does a whole-word-bounded SUBSTRING search over
every legitimate source value instead of an exact-key lookup - see its own docstring
in generate_image_prompt.py. _MIN_TEXT_CONTENT_LEAK_CHECK_LENGTH (a floor on the
sub-object's own content, checked before this match is ever attempted) still applies
unchanged, so a 1-3 char content string is never excused by matching inside a long
field."""
import json

from src import generate_image_prompt as gip
from tests.blueprint_fixtures import load_blueprint_fixture

OSEA_BLUEPRINT = load_blueprint_fixture("osea_two_products_both_substitute")

PRODUCT = {
    "name": "Magic Body Oil",
    "visual_description": "a tall cylindrical bottle with a gold collar and black pump",
    "substance_colour": "golden-amber oil",
    "certifications": ["Vegan", "Cruelty Free", "100% Natural"],
}


def _sub(object_id, content, **overrides):
    base = {
        "object_id": object_id, "content": content, "bbox": [0.1, 0.1, 0.2, 0.05],
        "text_purpose": "other", "ownership": "competitor_branded",
        "carries_brand_mark": True, "disposition": "keep",
    }
    base.update(overrides)
    return base


def _mock_dedupe_warnings(monkeypatch):
    from src import dedupe
    warnings = []
    monkeypatch.setattr(dedupe, "init_pipeline_warnings", lambda: None)
    monkeypatch.setattr(dedupe, "record_warning", lambda kind, detail: warnings.append((kind, detail)))
    return warnings


# ---- Unit: _legitimate_prompt_source_strings / _normalize_leak_text ----

def test_legitimate_prompt_source_strings_collects_product_fields():
    sources = gip._legitimate_prompt_source_strings(product=PRODUCT)
    assert sources[gip._normalize_leak_text("Magic Body Oil")] == "product.name"
    assert sources[gip._normalize_leak_text("Vegan")] == "product.certifications"


def test_normalize_leak_text_ignores_case_and_punctuation():
    assert gip._normalize_leak_text("Magic Body Oil!") == gip._normalize_leak_text("magic   body oil")


# ---- End-to-end: content matching a product field must not raise, must warn ----

def test_text_content_matching_product_field_does_not_raise_and_warns(monkeypatch):
    warnings = _mock_dedupe_warnings(monkeypatch)
    blueprint = json.loads(json.dumps(OSEA_BLUEPRINT))
    blueprint["objects"].append({
        "object_id": "obj_95", "kind": "graphic", "role": "environment",
        "colours": ["gold"], "ownership": "generic", "carries_brand_mark": False,
        "persuasive_function": "background texture", "disposition": "keep",
        "text_content": [_sub("obj_95_txt_01", "Magic Body Oil")],
    })

    prompt = gip.build_image_prompt(
        blueprint, product=PRODUCT, include_product=True, edit_mode=True, realism=None,
    )

    assert "Magic Body Oil" in prompt  # from product_clause, a legitimate source
    collision_warnings = [w for w in warnings if w[0] == "text_content_leak_matched_legitimate_source"]
    assert len(collision_warnings) == 1
    kind, detail = collision_warnings[0]
    assert "obj_95_txt_01" in detail
    assert "obj_95" in detail
    assert "product.name" in detail


def test_text_content_not_matching_anything_still_raises(monkeypatch):
    warnings = _mock_dedupe_warnings(monkeypatch)
    blueprint = json.loads(json.dumps(OSEA_BLUEPRINT))
    blueprint["objects"].append({
        "object_id": "obj_96", "kind": "graphic", "role": "environment",
        "colours": ["gold"], "ownership": "generic", "carries_brand_mark": False,
        "persuasive_function": "background texture", "disposition": "keep",
        "text_content": [_sub("obj_96_txt_01", "Dew Glow Botanicals")],
    })

    def _leaky_note(obj, context):
        return " LEAKED: Dew Glow Botanicals"

    monkeypatch.setattr(gip, "_text_content_removal_note", _leaky_note)

    try:
        gip.build_image_prompt(
            blueprint, product=PRODUCT, include_product=True, edit_mode=True, realism=None,
        )
        assert False, "expected TextContentLeakError"
    except gip.TextContentLeakError:
        pass

    assert not any(w[0] == "text_content_leak_matched_legitimate_source" for w in warnings)


# ---- Word-boundary substring matching (2026-08-21 fix) ----

def test_content_besque_token_matches_product_name_substring_does_not_raise_and_warns(monkeypatch):
    """The reported failure shape: the detected on-image text is a single TOKEN
    ("BESQUE"), a fragment of the product's own full name ("Besque Magic Body
    Oil"), never the whole field verbatim - the equality-based first version of this
    fix could not match this, only the whole-word substring search does."""
    warnings = _mock_dedupe_warnings(monkeypatch)
    product = {**PRODUCT, "name": "Besque Magic Body Oil"}
    blueprint = json.loads(json.dumps(OSEA_BLUEPRINT))
    blueprint["objects"].append({
        "object_id": "obj_94", "kind": "graphic", "role": "environment",
        "colours": ["gold"], "ownership": "generic", "carries_brand_mark": False,
        "persuasive_function": "background texture", "disposition": "keep",
        "text_content": [_sub("obj_94_txt_01", "BESQUE")],
    })

    prompt = gip.build_image_prompt(
        blueprint, product=product, include_product=True, edit_mode=True, realism=None,
    )

    assert "BESQUE" in prompt
    collision_warnings = [w for w in warnings if w[0] == "text_content_leak_matched_legitimate_source"]
    assert len(collision_warnings) == 1
    kind, detail = collision_warnings[0]
    assert "obj_94_txt_01" in detail
    assert "obj_94" in detail
    assert "product.name" in detail


def test_content_string_appears_nowhere_in_any_source_still_raises(monkeypatch):
    """A content string with no relationship to any legitimate source - not equal to
    one, not a whole-word substring of one either - must still raise, proving the
    substring search doesn't quietly excuse everything."""
    warnings = _mock_dedupe_warnings(monkeypatch)
    blueprint = json.loads(json.dumps(OSEA_BLUEPRINT))
    blueprint["objects"].append({
        "object_id": "obj_92", "kind": "graphic", "role": "environment",
        "colours": ["gold"], "ownership": "generic", "carries_brand_mark": False,
        "persuasive_function": "background texture", "disposition": "keep",
        "text_content": [_sub("obj_92_txt_01", "Frost Renewal Complex")],
    })

    def _leaky_note(obj, context):
        return " LEAKED: Frost Renewal Complex"

    monkeypatch.setattr(gip, "_text_content_removal_note", _leaky_note)

    try:
        gip.build_image_prompt(
            blueprint, product=PRODUCT, include_product=True, edit_mode=True, realism=None,
        )
        assert False, "expected TextContentLeakError"
    except gip.TextContentLeakError:
        pass

    assert not any(w[0] == "text_content_leak_matched_legitimate_source" for w in warnings)
