"""Layer A regression protection, item 3: make silent returns loud.

Systematic search (grep for every literal `return ""` in src/generate_image_prompt.py)
found 9 sites total. This file's assertions record which got an ERROR log added and
why, so the split is a documented decision, not an accident a future edit could
silently undo:

LOGGED (missing data that a valid new-schema blueprint/run should always supply -
its absence signals a dead-key regression, the exact class this task exists to
catch, not a normal per-run state):
  - _objects_clause, first return (objects missing/empty entirely) - ALREADY logged,
    added in an earlier session (Task 2). Covered by test_objects_schema.py, not
    re-tested here.
  - _objects_clause, second return (objects present but every one resolved to
    something other than keep/substitute/drop, so zero lines were built) - NEW.
  - _scene_lighting_facts (background.light missing) - NEW. This is the exact
    "scene lighting facts are always empty, and nothing downstream knows it" gap
    CLAUDE.md already names as a live, unresolved bug from the 2026-08-17 refactor.
  - _scene_composition_facts (none of visual.layout/layout_detail.frame_division/
    layout_detail.zone_positions/background.surface present) - NEW.
  - _register_clause (style not resolved) - NEW. Named explicitly in the task.

NOT LOGGED, deliberately (the missing input is a normal, frequently-empty per-run
toggle with no "should always be present" expectation - logging ERROR here would
fire on the common case, e.g. every first-attempt generation with no prior critic
finding, burying the genuine signal the four logs above exist to surface):
  - _operator_instruction_clause (most runs carry no free-text operator instruction)
  - _critic_feedback_clause (empty on every first attempt - only non-empty on a
    corrective retry)
  - _semantic_split_clause (most references are not a before/after split format)
  - _suppressed_container_exception (most runs suppress nothing)

If a future session decides one of the four "not logged" cases actually IS a dead-
field regression rather than a normal empty state, that is a deliberate change to
this file's own recorded reasoning, not a bug fix - update this docstring alongside
the log."""
import logging

from src import generate_image_prompt as gip


def _error_records(caplog):
    return [r for r in caplog.records if r.levelno == logging.ERROR]


# ---- LOGGED sites: error fires on missing input, silent on populated input ----

def test_objects_clause_logs_when_no_object_resolves_to_a_renderable_line(caplog):
    # Every object here is generic/unbranded/non-text/non-person with a stored
    # disposition resolve_disposition passes through unchanged (obj.get("disposition"))
    # - and that stored value is deliberately not "keep"/"substitute"/"drop", so no
    # line is built for it despite `objects` itself being non-empty.
    objects = [{
        "object_id": "obj_01", "kind": "prop", "description": "a ceramic dish",
        "bbox": [0.1, 0.1, 0.1, 0.1], "colours": ["white"], "ownership": "generic",
        "role": "supporting_prop", "carries_brand_mark": False,
        "persuasive_function": "styling", "disposition": "unresolved",
    }]
    with caplog.at_level(logging.ERROR, logger="generate_image_prompt"):
        result = gip._objects_clause(objects=objects, ad_id="FIXTURE_test")
    assert result == ""
    errors = _error_records(caplog)
    assert any("SCENE OBJECTS" in r.message for r in errors)


def test_scene_lighting_facts_logs_when_light_missing(caplog):
    with caplog.at_level(logging.ERROR, logger="generate_image_prompt"):
        result = gip._scene_lighting_facts({"surface": "counter", "colour": "cream"})
    assert result == ""
    errors = _error_records(caplog)
    assert any("_scene_lighting_facts" in r.message for r in errors)


def test_scene_lighting_facts_silent_when_light_present(caplog):
    with caplog.at_level(logging.ERROR, logger="generate_image_prompt"):
        result = gip._scene_lighting_facts({"light": "soft warm light from upper-left"})
    assert result != ""
    assert not _error_records(caplog)


def test_scene_composition_facts_logs_when_nothing_extracted(caplog):
    with caplog.at_level(logging.ERROR, logger="generate_image_prompt"):
        result = gip._scene_composition_facts(layout_detail={}, visual={}, background={})
    assert result == ""
    errors = _error_records(caplog)
    assert any("_scene_composition_facts" in r.message for r in errors)


def test_scene_composition_facts_silent_when_a_fact_is_present(caplog):
    with caplog.at_level(logging.ERROR, logger="generate_image_prompt"):
        result = gip._scene_composition_facts(
            layout_detail={}, visual={}, background={"surface": "marble counter"}
        )
    assert result != ""
    assert not _error_records(caplog)


def test_register_clause_logs_when_style_missing(caplog):
    with caplog.at_level(logging.ERROR, logger="generate_image_prompt"):
        result = gip._register_clause(style=None, background={})
    assert result == ""
    errors = _error_records(caplog)
    assert any("_register_clause" in r.message for r in errors)


def test_register_clause_silent_when_style_present(caplog):
    with caplog.at_level(logging.ERROR, logger="generate_image_prompt"):
        result = gip._register_clause(style="ugc", background={"light": "soft light"})
    assert result != ""
    assert not _error_records(caplog)


# ---- NOT LOGGED sites: stay silent on their normal, frequently-empty input ----

def test_operator_instruction_clause_silent_when_absent(caplog):
    with caplog.at_level(logging.ERROR, logger="generate_image_prompt"):
        result = gip._operator_instruction_clause(None)
    assert result == ""
    assert not _error_records(caplog)


def test_critic_feedback_clause_silent_when_absent(caplog):
    with caplog.at_level(logging.ERROR, logger="generate_image_prompt"):
        result = gip._critic_feedback_clause(None)
    assert result == ""
    assert not _error_records(caplog)


def test_semantic_split_clause_silent_when_not_a_split(caplog):
    with caplog.at_level(logging.ERROR, logger="generate_image_prompt"):
        result = gip._semantic_split_clause({"is_split": False})
    assert result == ""
    assert not _error_records(caplog)


def test_suppressed_container_exception_silent_when_nothing_suppressed(caplog):
    with caplog.at_level(logging.ERROR, logger="generate_image_prompt"):
        result = gip._suppressed_container_exception(False, False, False)
    assert result == ""
    assert not _error_records(caplog)
