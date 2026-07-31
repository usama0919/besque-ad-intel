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


def is_valid(blueprint: dict) -> bool:
    """Return True if the blueprint matches the schema, else False."""
    try:
        validate(instance=blueprint, schema=_SCHEMA)
        return True
    except ValidationError:
        return False


def validation_error(blueprint: dict) -> str | None:
    """Return the error message if invalid, else None."""
    try:
        validate(instance=blueprint, schema=_SCHEMA)
        return None
    except ValidationError as e:
        return e.message