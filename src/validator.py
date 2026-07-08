"""Validates a creative blueprint dict against the JSON schema."""
import json
from pathlib import Path
from jsonschema import validate, ValidationError

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "blueprint.schema.json"

with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
    _SCHEMA = json.load(f)


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