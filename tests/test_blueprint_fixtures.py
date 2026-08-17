"""Layer A regression protection, item 1: blueprint fixtures.

Each fixture under tests/fixtures/blueprints/ is a real-shaped deconstruct output
(see tests/blueprint_fixtures.py for the loader and dump_blueprint.py at the repo
root for the format it matches). Every fixture must independently satisfy:

- it validates against schema/blueprint.schema.json
- every objects[] entry carries all schema-required per-object fields
- no competitor_branded/carries_brand_mark object EVER resolves to "keep"
- no kind=="person" object EVER resolves to "keep"
- text objects whose text_purpose is offer/price_anchor/certification/testimonial
  never resolve to "keep" (regardless of context)

Per the explicit amendment to this task: these disposition assertions call
deconstruct.resolve_disposition(obj, context) directly - the production entry
point - never read the stored obj["disposition"] field. The dual-resolution
design (deconstruct._resolve_object_dispositions runs context=None at deconstruct
time and stores that; generate_image_prompt._objects_clause re-resolves with the
real run context at prompt-build time) means the stored field can legitimately
disagree with what actually renders for offer/price_anchor/certification/
testimonial purposes - asserting on the stored field would produce false
failures. Every context-gated assertion below therefore exercises BOTH an empty
context (matching deconstruct time) and a populated one (matching prompt-build
time for a run that actually supplies the matching value).
"""
import pytest

from src import deconstruct, validator
from tests.blueprint_fixtures import list_fixture_names, load_blueprint_fixture

REQUIRED_OBJECT_FIELDS = [
    "object_id", "kind", "description", "bbox", "colours", "ownership",
    "role", "carries_brand_mark", "persuasive_function", "disposition",
]

# A context populated with every value a context-gated text_purpose can be
# resolved against, so the "with context" half of each assertion below
# exercises the real substitute path, not another no-op drop.
FULL_CONTEXT = {
    "offer_text": "20% off, this week only",
    "certifications": ["Vegan", "Cruelty Free", "100% Natural"],
    "testimonial": {"quote": "This changed my skin routine.", "name": "Jane D."},
}

FIXTURE_NAMES = list_fixture_names()


@pytest.fixture(params=FIXTURE_NAMES)
def blueprint(request):
    return load_blueprint_fixture(request.param)


def test_at_least_one_fixture_exists():
    assert FIXTURE_NAMES, "tests/fixtures/blueprints/ has no fixtures - nothing to guard"


def test_fixture_validates_against_schema(blueprint):
    assert validator.is_valid(blueprint), validator.validation_error(blueprint)


def test_fixture_objects_carry_all_required_fields(blueprint):
    for obj in blueprint["objects"]:
        for field in REQUIRED_OBJECT_FIELDS:
            assert field in obj, f"object {obj.get('object_id')} missing required field {field!r}"
        if obj["kind"] == "text":
            assert "text_purpose" in obj, (
                f"text object {obj.get('object_id')} missing required text_purpose"
            )


def test_fixture_competitor_branded_objects_never_resolve_to_keep(blueprint):
    for obj in blueprint["objects"]:
        if obj.get("ownership") == "competitor_branded" or obj.get("carries_brand_mark"):
            for context in (None, FULL_CONTEXT):
                resolved = deconstruct.resolve_disposition(obj, context)
                assert resolved != "keep", (
                    f"object {obj['object_id']} (ownership={obj.get('ownership')!r}, "
                    f"carries_brand_mark={obj.get('carries_brand_mark')!r}) resolved to "
                    f"'keep' with context={context!r} - a competitor brand mark or "
                    f"branded object must never be reproduced unchanged"
                )


def test_fixture_person_objects_never_resolve_to_keep(blueprint):
    for obj in blueprint["objects"]:
        if obj.get("kind") == "person":
            for context in (None, FULL_CONTEXT):
                resolved = deconstruct.resolve_disposition(obj, context)
                assert resolved != "keep", (
                    f"person object {obj['object_id']} resolved to 'keep' with "
                    f"context={context!r} - a real person's likeness must never be "
                    f"reproduced unchanged"
                )


CONTEXT_GATED_PURPOSES = {"offer", "price_anchor", "certification", "testimonial"}


def test_fixture_context_gated_text_objects_never_resolve_to_keep(blueprint):
    for obj in blueprint["objects"]:
        if obj.get("kind") == "text" and obj.get("text_purpose") in CONTEXT_GATED_PURPOSES:
            for context in (None, FULL_CONTEXT):
                resolved = deconstruct.resolve_disposition(obj, context)
                assert resolved != "keep", (
                    f"text object {obj['object_id']} (text_purpose="
                    f"{obj['text_purpose']!r}) resolved to 'keep' with "
                    f"context={context!r} - offer/price_anchor/certification/"
                    f"testimonial text must always substitute or drop, never survive "
                    f"verbatim"
                )


def test_fixture_context_gated_text_objects_drop_without_context_and_substitute_with_it(blueprint):
    """Exercises the dual-resolution design explicitly, not just its "never keep"
    consequence: no context (deconstruct time) must drop; the matching context
    (prompt-build time) must substitute - proving the resolver actually reacts to
    context rather than happening to avoid "keep" some other way."""
    found_any = False
    for obj in blueprint["objects"]:
        if obj.get("kind") == "text" and obj.get("text_purpose") in CONTEXT_GATED_PURPOSES:
            found_any = True
            without_context = deconstruct.resolve_disposition(obj, None)
            with_context = deconstruct.resolve_disposition(obj, FULL_CONTEXT)
            assert without_context == "drop", (
                f"object {obj['object_id']} (text_purpose={obj['text_purpose']!r}) "
                f"resolved to {without_context!r} with no context, expected 'drop'"
            )
            assert with_context == "substitute", (
                f"object {obj['object_id']} (text_purpose={obj['text_purpose']!r}) "
                f"resolved to {with_context!r} with a populated matching context, "
                f"expected 'substitute'"
            )
    if not found_any:
        pytest.skip("fixture has no offer/price_anchor/certification/testimonial text object")
