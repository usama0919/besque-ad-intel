"""Layer A regression protection, item 2: non-empty clause guard.

Every clause-building function in src/generate_image_prompt.py is expected to
render SOMETHING into the assembled prompt when given a fully-populated, valid
scenario - a function that silently returns "" because a consumer reads a field
that no longer exists (the exact 2026-08-17 objects-array-refactor failure shape
this task exists to guard against - scene lighting, drift_check, and
_edit_mode_instruction's clone_mode check were all found this way, by accident,
days after the fact) degrades a draft with no signal anywhere that it happened.

Functions are found BY INTROSPECTION (name ends in "_clause"/"_facts", or is
brand_rules - the two naming conventions every clause-building function in this
module already follows, confirmed by grep against every def in the file), not a
hand-maintained list, so a newly added clause is covered automatically without
this file needing an edit.

Each matched function is called with only the parameters it actually declares,
sourced from POOL by parameter name - a single, richly-populated fixture scenario
built to activate every conditionally-empty clause's non-empty branch (a real
offer, a real critic finding, an actual before/after split, a resolved style),
not the degenerate empty-input case every one of these functions ALSO has to
handle correctly (that is item 3's job, in
tests/test_generate_image_prompt_silent_returns.py - the two are deliberately
separate: one proves the happy path renders something, the other proves the
unhappy path is now loud rather than silent)."""
import inspect

import pytest

from src import generate_image_prompt
from tests.blueprint_fixtures import load_blueprint_fixture

_FIXTURE_BLUEPRINT = load_blueprint_fixture("sample_hero_with_offer")

POOL = {
    "blueprint": _FIXTURE_BLUEPRINT,
    "product": {
        "name": "Magic Body Oil",
        "visual_description": "a tall cylindrical bottle with a gold collar and black pump",
        "substance_colour": "golden-amber oil",
        "certifications": ["Vegan", "Cruelty Free", "100% Natural"],
    },
    "background": {
        "surface": "marble bathroom counter",
        "colour": "warm cream and grey veining",
        "light": "soft warm light from upper-left, low contrast",
    },
    "style": "ugc",
    "instruction": "Make the headline larger and move the badge to the left.",
    "objects": _FIXTURE_BLUEPRINT["objects"],
    "context": {
        "offer_text": "20% off, this week only",
        "certifications": ["Vegan", "Cruelty Free", "100% Natural"],
        "testimonial": {"quote": "This changed my skin routine.", "name": "Jane D."},
    },
    "ad_id": "FIXTURE_clause_coverage",
    "operator_instruction": "Add more warmth to the lighting.",
    "critic_feedback": ["C2: fabricated testimonial detected in the previous attempt"],
    "semantic_split": {
        "is_split": True,
        "split_axis": "vertical",
        "left_or_before": "before: visibly crepey skin on the forearm",
        "right_or_after": "after: visibly smoother, firmer-looking skin",
    },
    "substance_colour": "golden-amber oil",
    "layout_detail": {
        "text_zone": "top third and lower-right corner",
        "product_count": 1,
        "zone_positions": ["headline top-right", "offer badge bottom-right", "product mid-left"],
        "has_bottom_banner": False,
        "has_corner_badge": True,
        "frame_division": "product left half, person right half",
    },
    "visual": {
        "layout": "product hero left-of-frame, headline top-right, offer badge bottom-right",
        "subject": "a woman applying oil to her forearm over a bathroom counter",
        "palette_mood": "warm neutral, soft morning light",
        "text_placement": "headline upper third, offer badge lower-right corner",
    },
}


def _is_clause_function(name, fn):
    if not inspect.isfunction(fn):
        return False
    if fn.__module__ != generate_image_prompt.__name__:
        return False
    return name.endswith("_clause") or name.endswith("_facts") or name == "brand_rules"


def _build_kwargs(fn):
    kwargs = {}
    missing_required = []
    for pname, param in inspect.signature(fn).parameters.items():
        if pname in POOL:
            kwargs[pname] = POOL[pname]
        elif param.default is inspect.Parameter.empty:
            missing_required.append(pname)
    return kwargs, missing_required


CLAUSE_FUNCTIONS = [
    (name, fn) for name, fn in vars(generate_image_prompt).items()
    if _is_clause_function(name, fn)
]


def test_clause_functions_were_actually_found():
    names = {name for name, _ in CLAUSE_FUNCTIONS}
    # Sentinel names that must always be present - if introspection ever finds zero
    # matches (e.g. a naming-convention drift), this fails loudly instead of the
    # parametrised test below silently collecting nothing.
    assert {"_objects_clause", "_register_clause", "brand_rules"} <= names


@pytest.mark.parametrize(
    "name", [name for name, _ in CLAUSE_FUNCTIONS], ids=[name for name, _ in CLAUSE_FUNCTIONS]
)
def test_clause_function_returns_non_empty_text_given_a_populated_scenario(name):
    fn = dict(CLAUSE_FUNCTIONS)[name]
    kwargs, missing_required = _build_kwargs(fn)
    assert not missing_required, (
        f"{name} declares required parameter(s) {missing_required} with no entry in "
        f"POOL - add one so this guard can actually exercise the function"
    )
    result = fn(**kwargs)
    assert isinstance(result, str), f"{name} did not return a string: {result!r}"
    assert result.strip() != "", (
        f"{name} returned empty text given a fully-populated, valid scenario - either "
        f"a required field's name changed underneath it, or this function needs a "
        f"POOL entry activating its non-empty branch"
    )
