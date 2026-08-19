"""Tests for B1 (schema/prompt contradiction: social_proof.owner) and B2 (the
validation error message naming the failing field path) - both 2026-08-19.

B1: deconstruct.py's BLUEPRINT_PROMPT has always told the model social_proof.owner
may be null ("owner is the brand/body the proof belongs to, or null") while the
schema typed it as a plain string - a live, non-deterministic failure with no other
change to the input. Fixed by loosening ONLY that field to match the documented
contract - offer.type/value/mechanic are deliberately left untouched (the prompt
documents only the containing `offer` object as nullable, never those three
sub-fields individually).

B2: validator.validation_error used to return jsonschema's bare e.message, which
never includes the failing field/path (that's a separate attribute) - so a genuinely
different failing field on a retry could produce the exact same generic string as
attempt 1, giving deconstruct._validation_retry_system nothing to act on. Fixed by
appending e.json_path to the returned message.
"""
from src import deconstruct, validator
from tests.blueprint_fixtures import load_blueprint_fixture


def _valid_blueprint():
    return load_blueprint_fixture("sample_hero_with_offer")


# ---- B1: social_proof.owner = null is now valid ----

def test_social_proof_owner_null_validates():
    bp = _valid_blueprint()
    bp["social_proof"] = {"type": "aggregate_bar", "owner": None}
    assert validator.is_valid(bp), validator.validation_error(bp)
    assert validator.validation_error(bp) is None


def test_social_proof_owner_string_still_validates():
    # The common case (a real owner name) must keep working byte-for-byte - loosening
    # the type to allow null must never also start rejecting the ordinary value.
    bp = _valid_blueprint()
    bp["social_proof"] = {"type": "single_quote", "owner": "reference"}
    assert validator.is_valid(bp), validator.validation_error(bp)


# ---- B1: fields the prompt never documents as nullable must NOT have been loosened ----

def test_offer_subfields_still_reject_null():
    # BLUEPRINT_PROMPT (deconstruct.py) documents the whole `offer` OBJECT as
    # nullable ("or null if no offer") but never says its type/value/mechanic
    # sub-fields individually may be null - these must still be plain strings.
    for field in ("type", "value", "mechanic"):
        bp = _valid_blueprint()
        bp["offer"] = {"type": "percentage_discount", "value": "20% off",
                        "mechanic": "code at checkout"}
        bp["offer"][field] = None
        assert validator.validation_error(bp) is not None, (
            f"offer.{field} must still reject null - the prompt never documents "
            f"this sub-field as nullable, only the containing `offer` object"
        )


def test_genuinely_required_string_set_to_null_still_fails():
    # A required, never-documented-nullable string (objects[].description) must
    # still fail loudly - B1 must not have loosened validation in general, only the
    # one documented field.
    bp = _valid_blueprint()
    bp["objects"][0]["description"] = None
    assert validator.validation_error(bp) is not None


# ---- B2: the error message names the failing field ----

def test_validation_error_message_contains_failing_field_path():
    bp = _valid_blueprint()
    bp["objects"][0]["description"] = None
    err = validator.validation_error(bp)
    assert err is not None
    assert "objects[0]" in err and "description" in err


def test_validation_error_message_contains_nested_path_for_typography():
    bp = _valid_blueprint()
    # Any kind=="text" object with a typography block - mutate a nested leaf.
    text_obj = next(o for o in bp["objects"] if o.get("kind") == "text")
    text_obj["typography"] = text_obj.get("typography") or {}
    text_obj["typography"]["colour"] = None
    err = validator.validation_error(bp)
    assert err is not None
    assert "typography" in err and "colour" in err


def test_two_different_failures_produce_two_different_messages():
    # The exact live symptom this fix closes: attempt 1 and attempt 2 hitting
    # DIFFERENT fields must no longer collapse to the identical generic string.
    bp1 = _valid_blueprint()
    bp1["objects"][0]["description"] = None
    err1 = validator.validation_error(bp1)

    bp2 = _valid_blueprint()
    bp2["objects"][0]["colours"] = None
    err2 = validator.validation_error(bp2)

    assert err1 != err2


# ---- B2: the retry system prompt carries the path through ----

def test_validation_retry_system_carries_path_through():
    bp = _valid_blueprint()
    bp["objects"][0]["description"] = None
    err = validator.validation_error(bp)
    system_prompt = deconstruct._validation_retry_system(err)
    assert "objects[0]" in system_prompt and "description" in system_prompt


def test_deconstruct_from_response_raises_with_path_in_validation_error_attribute():
    import json
    bp = _valid_blueprint()
    bp["objects"][0]["description"] = None
    raw = json.dumps(bp)
    try:
        deconstruct.deconstruct_from_response(raw)
        assert False, "expected BlueprintValidationError"
    except deconstruct.BlueprintValidationError as e:
        assert "objects[0]" in e.validation_error and "description" in e.validation_error
        assert "objects[0]" in str(e) and "description" in str(e)
