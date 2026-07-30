"""Tests for the angles table CRUD in src/dedupe.py (messaging angles as operator-curated
data, not a Python enum). Same pattern as test_dedupe_products.py - real DB connection,
uuid-suffixed rows, cleaned up in try/finally."""
import uuid
from src import dedupe


def _make_angle(**kw):
    dedupe.init_angles()
    name = f"__test_{uuid.uuid4().hex[:8]}__"
    slug = f"test_{uuid.uuid4().hex[:8]}"
    return dedupe.add_angle(name, slug, **kw)


def test_add_angle_and_get_round_trips_all_fields():
    aid = _make_angle(body_area="elbow and forearm", default_realism="high_spec_studio",
                       includes_product=True, notes="fixed reference photos")
    try:
        a = dedupe.get_angle(aid)
        assert a["body_area"] == "elbow and forearm"
        assert a["default_realism"] == "high_spec_studio"
        assert a["includes_product"] is True
        assert a["notes"] == "fixed reference photos"
    finally:
        dedupe.delete_angle(aid)


def test_add_angle_defaults_body_area_empty_when_not_confirmed():
    """Mirrors the real loose_skin case: body_area is left empty rather than guessed."""
    aid = _make_angle()
    try:
        assert dedupe.get_angle(aid)["body_area"] == ""
    finally:
        dedupe.delete_angle(aid)


def test_add_angle_includes_product_false_for_productless_angle():
    """Mirrors the real glp1 case: an educational diagram with no product."""
    aid = _make_angle(includes_product=False)
    try:
        assert dedupe.get_angle(aid)["includes_product"] is False
    finally:
        dedupe.delete_angle(aid)


def test_update_angle_changes_fields():
    aid = _make_angle(body_area="forearm")
    try:
        a = dedupe.get_angle(aid)
        dedupe.update_angle(aid, a["name"], a["slug"], body_area="shoulder and upper back",
                            default_realism="ugc_native", includes_product=True, notes="updated")
        updated = dedupe.get_angle(aid)
        assert updated["body_area"] == "shoulder and upper back"
        assert updated["default_realism"] == "ugc_native"
        assert updated["notes"] == "updated"
    finally:
        dedupe.delete_angle(aid)


def test_delete_angle_removes_it():
    aid = _make_angle()
    dedupe.delete_angle(aid)
    assert dedupe.get_angle(aid) is None


def test_get_angles_includes_new_row():
    aid = _make_angle()
    try:
        ids = [a["id"] for a in dedupe.get_angles()]
        assert aid in ids
    finally:
        dedupe.delete_angle(aid)


def test_get_angle_missing_returns_none():
    assert dedupe.get_angle(-1) is None
