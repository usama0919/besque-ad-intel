from src import compliance


def test_clean_copy_passes():
    copy = {"headline": "Firmer skin at any age", "primary_text": "Natural oils for women 40+", "cta": "Shop"}
    ok, issues = compliance.check_compliance(copy, "CeraVe", "CeraVe dermatologist cleanser")
    assert ok is True
    assert issues == []


def test_competitor_name_flagged():
    copy = {"headline": "Better than CeraVe", "primary_text": "x", "cta": "y"}
    ok, issues = compliance.check_compliance(copy, "CeraVe", "")
    assert ok is False
    assert any("CeraVe" in i for i in issues)


def test_verbatim_phrase_flagged():
    comp = "developed with dermatologists for sensitive oily skin types"
    copy = {"headline": "developed with dermatologists for sensitive oily skin types", "primary_text": "x", "cta": "y"}
    ok, issues = compliance.check_compliance(copy, "SomeBrand", comp)
    assert ok is False
    assert any("Verbatim" in i for i in issues)


def test_short_competitor_text_not_flagged():
    copy = {"headline": "Radiant skin", "primary_text": "x", "cta": "y"}
    ok, issues = compliance.check_compliance(copy, "Brand", "buy now")
    assert ok is True
