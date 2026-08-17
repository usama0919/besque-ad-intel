"""Loader for real-shaped blueprint fixtures under tests/fixtures/blueprints/.

Each fixture is a bare blueprint dict, JSON-dumped in exactly the format
dump_blueprint.py (repo root) produces for a real deconstructed ad - no wrapper,
no metadata alongside it. Named FIXTURE_<slug> as its ad_id so it can never be
mistaken for a real numeric Facebook ad_archive_id (see CLAUDE.md: "No ad_id...
in src/" - fixtures live in tests/, but the same non-numeric-id convention keeps
them visually distinct from a real ad_id at a glance).
"""

import json
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "blueprints"


def list_fixture_names():
    return sorted(p.stem for p in FIXTURES_DIR.glob("*.json"))


def load_blueprint_fixture(name):
    path = FIXTURES_DIR / f"{name}.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
