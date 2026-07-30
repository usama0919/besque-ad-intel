"""Tests for the competitors CRUD in src/dedupe.py, focused on the page_id-preservation
bug: update_competitor(page_id=None) must leave the existing page_id untouched, never
default it to name (that's add_competitor's job for a brand-new row, not an update)."""
import uuid
from src import dedupe


def _make_competitor(page_id, **kw):
    dedupe.init_competitors()
    name = f"__test_{uuid.uuid4().hex[:8]}__"
    new_id = dedupe.add_competitor(name, page_id, **kw)
    return new_id, name


def _get(competitor_id):
    return next(c for c in dedupe.get_competitors() if c["id"] == competitor_id)


def test_category_only_update_preserves_verified_page_id():
    """Regression guard for the 2026-07-30 incident: a category-only edit (page_id
    omitted/None) must not wipe an already-verified numeric page_id."""
    cid, name = _make_competitor(page_id="1936234786698582", category="")
    try:
        dedupe.update_competitor(cid, name=name, category="body_oil")  # page_id omitted
        row = _get(cid)
        assert row["page_id"] == "1936234786698582"
        assert row["category"] == "body_oil"
    finally:
        dedupe.delete_competitor(cid)


def test_update_competitor_page_id_none_preserves_existing_value():
    """Same as above but with page_id explicitly passed as None, matching what the PUT
    route now forwards when the query param is absent."""
    cid, name = _make_competitor(page_id="555444333", category="haircare")
    try:
        dedupe.update_competitor(cid, name=name, page_id=None, category="haircare")
        row = _get(cid)
        assert row["page_id"] == "555444333"
    finally:
        dedupe.delete_competitor(cid)


def test_update_competitor_with_explicit_page_id_still_overwrites():
    """The fix must not make page_id unwritable - an explicitly supplied value must still
    take effect, e.g. pipeline.py's auto-capture correcting a placeholder page_id."""
    cid, name = _make_competitor(page_id="TEMP", category="")
    try:
        dedupe.update_competitor(cid, name=name, page_id="998877665", category="")
        row = _get(cid)
        assert row["page_id"] == "998877665"
    finally:
        dedupe.delete_competitor(cid)


def test_update_competitor_name_only_change_preserves_page_id():
    cid, name = _make_competitor(page_id="112233445566", category="")
    try:
        new_name = name + "_renamed"
        dedupe.update_competitor(cid, name=new_name)
        row = _get(cid)
        assert row["name"] == new_name
        assert row["page_id"] == "112233445566"
    finally:
        dedupe.delete_competitor(cid)
