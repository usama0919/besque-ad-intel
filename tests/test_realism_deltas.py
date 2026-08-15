"""Tests for src/realism_deltas.py - the bottle-realism-only targeted edit control
(2026-08-16). No DB, no network: pure string/data assertions on the module's own
constants, plus the edit_capability descriptor contract that feeds the modal's
segmented picker."""
from src import realism_deltas
from src.edit_capability import derive_edit_capabilities, find_control

# Forbidden strings (case-insensitive): none may appear in any delta. Brand/product
# name and label colour/typeface are identity facts that belong to
# products.visual_description, never restated in a delta; bottle geometry nouns
# (cylindrical/collar/pump) are the same kind of restated-identity leak.
_FORBIDDEN_SUBSTRINGS = (
    "besque", "magic", "maroon", "terracotta", "serif", "sans-serif",
    "vegan", "cruelty", "cylindrical", "collar", "pump",
)


def test_exactly_four_realism_values():
    assert realism_deltas.REALISM_VALUES == ("ugc_native", "high_spec", "hybrid", "illustrated")
    assert set(realism_deltas.REALISM_DELTAS.keys()) == set(realism_deltas.REALISM_VALUES)


def test_no_delta_contains_forbidden_identity_strings():
    for value, delta in realism_deltas.REALISM_DELTAS.items():
        lowered = delta.lower()
        for forbidden in _FORBIDDEN_SUBSTRINGS:
            assert forbidden not in lowered, f"{value!r} delta leaked forbidden term {forbidden!r}"


def test_every_delta_states_label_is_unchanged():
    for value, delta in realism_deltas.REALISM_DELTAS.items():
        assert "label's content, wording, icons, proportions, and position" in delta, value
        assert "unchanged" in delta.lower(), value


def test_every_delta_states_everything_else_is_unchanged():
    for value, delta in realism_deltas.REALISM_DELTAS.items():
        assert "every other element in the image" in delta.lower(), value
        assert "completely unchanged" in delta, value


def test_every_delta_names_the_bottle_only_never_the_whole_scene():
    for value, delta in realism_deltas.REALISM_DELTAS.items():
        assert "the product bottle only" in delta, value


def test_get_delta_returns_exact_pre_authored_sentence():
    assert realism_deltas.get_delta("illustrated") == realism_deltas.REALISM_DELTAS["illustrated"]
    assert realism_deltas.get_delta("high_spec") == realism_deltas.REALISM_DELTAS["high_spec"]


def test_get_delta_fails_closed_on_unknown_value():
    assert realism_deltas.get_delta("photoreal_4k") is None
    assert realism_deltas.get_delta("") is None
    assert realism_deltas.get_delta(None) is None


# ---- edit_capability contract the modal's segmented picker relies on ----

def _artifact_with(production_style=None):
    blueprint = {
        "layout_detail": {"product_count": 1},
        "structural_zones": [], "text_purpose": [], "scene_elements": [],
        "face_present": {"has_face": False},
    }
    if production_style is not None:
        blueprint["production_style"] = {"style": production_style}
    return {"blueprint": blueprint, "element_provenance": {"product": "substituted"},
            "text_in_image": False, "offer_text": None}


def test_realism_control_options_come_from_realism_deltas_module():
    control = find_control(derive_edit_capabilities(_artifact_with("illustrated")), "product", "realism")
    assert control["options"] == list(realism_deltas.REALISM_VALUES)


def test_realism_control_current_value_matching_an_option_is_preselectable():
    control = find_control(derive_edit_capabilities(_artifact_with("hybrid")), "product", "realism")
    assert control["current_value"] == "hybrid"
    assert control["current_value"] in control["options"]


def test_unknown_stored_value_stays_verbatim_never_coerced_to_first_option():
    """The exact contract behind 'unknown stored value renders the chip, not option
    index 0': a stored production_style.style that predates the 2026-08-11 enum rename
    (schema now allows only ugc/high_spec/illustrated) must never be silently replaced
    by options[0] ('ugc_native') - it must be returned exactly as stored, so the modal
    can detect the mismatch and show a "current: <value>" chip instead of preselecting
    the first segment."""
    control = find_control(derive_edit_capabilities(_artifact_with("ugc")), "product", "realism")
    assert control["current_value"] == "ugc"
    assert control["current_value"] not in control["options"]
    assert control["options"][0] == "ugc_native"
    assert control["current_value"] != control["options"][0]


def test_current_value_unspecified_when_no_style_recorded_also_not_in_options():
    control = find_control(derive_edit_capabilities(_artifact_with(None)), "product", "realism")
    assert control["current_value"] == "unspecified"
    assert control["current_value"] not in control["options"]
