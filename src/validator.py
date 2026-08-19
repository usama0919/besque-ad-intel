"""Validates a creative blueprint dict against the JSON schema."""
import json
from pathlib import Path
from jsonschema import validate, ValidationError

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "blueprint.schema.json"

with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
    _SCHEMA = json.load(f)


def product_categories() -> list[str]:
    """The product_category enum, read from the schema so the dashboard dropdown and
    the products table cannot drift from what the validator actually enforces."""
    return list(
        _SCHEMA["properties"]["product_category"]["properties"]["category"]["enum"]
    )


def production_styles() -> list[str]:
    """The production_style.style enum, read from the schema so every other reader
    (deconstruct.py's classifier prompt, generate_image_prompt.py's guidance lookup, the
    dashboard's realism dropdown) consumes this instead of repeating the literal list -
    the same reasoning as product_categories() above."""
    return list(
        _SCHEMA["properties"]["production_style"]["properties"]["style"]["enum"]
    )


def creative_formats() -> list[str]:
    """The creative_format enum, read from the schema for the same reason as
    production_styles() above - generate_image_prompt_writer.py's TYPOGRAPHY_GUIDANCE
    lookup (Prompt 4, Item 5) asserts it covers every value here, so the two can't drift."""
    return list(_SCHEMA["properties"]["creative_format"]["enum"])


def _duplicate_object_ids_error(blueprint: dict) -> str | None:
    """Object-level uniqueness check the JSON schema itself cannot express: draft-07's
    `uniqueItems` compares whole items, not one sub-field, so two `objects` entries
    with identical `object_id` but different `description`/`bbox`/etc. would pass a
    pure jsonschema check silently. Every downstream consumer (the edit modal's
    per-object remove control, drift_check's removal-zone lookup) keys off object_id
    alone, so a duplicate is not cosmetic - it makes "remove obj_03" ambiguous about
    which of two objects is meant. Returns None when `objects` is missing/not a list
    (the schema's own `required`/`type` checks already own that failure) - this check
    only ever adds a NEW failure reason, never masks a different one."""
    objects = blueprint.get("objects")
    if not isinstance(objects, list):
        return None
    seen = set()
    for obj in objects:
        if not isinstance(obj, dict):
            continue
        object_id = obj.get("object_id")
        if object_id is None:
            continue
        if object_id in seen:
            return f"Duplicate object_id {object_id!r} in objects - every object_id must be unique."
        seen.add(object_id)
    return None


def is_valid(blueprint: dict) -> bool:
    """Return True if the blueprint matches the schema, else False."""
    return validation_error(blueprint) is None


def validation_error(blueprint: dict) -> str | None:
    """Return the error message if invalid, else None. Runs the jsonschema check
    first (schema shape, required fields, bbox type/bounds) - only reaches the
    duplicate-object_id check (see _duplicate_object_ids_error) once the blueprint is
    already schema-valid, so that check never has to guard against `objects` being
    absent or malformed itself.

    2026-08-19 (B2): the message now names the failing field. `e.message` alone
    (e.g. "None is not of type 'string'") never includes the path to the field that
    failed - jsonschema keeps that on a SEPARATE attribute (`e.absolute_path`, or its
    ready-made string rendering `e.json_path`, e.g. "$.social_proof.owner" or
    "$.objects[3].typography.colour") that this function previously discarded. Every
    caller of this function (deconstruct.py's log line, and the retry system prompt
    built from `BlueprintValidationError.validation_error`) only ever had the bare
    message to work with, so a genuinely different failing field on attempt 2 could
    - and did, live - produce the exact same generic string as attempt 1, giving the
    model nothing to act on. `e.json_path` is jsonschema's own formatting, not
    hand-rolled here, so this can't drift from what the pinned jsonschema version
    (see requirements.txt) actually produces."""
    try:
        validate(instance=blueprint, schema=_SCHEMA)
    except ValidationError as e:
        return f"{e.message} (at {e.json_path})"
    return _duplicate_object_ids_error(blueprint)