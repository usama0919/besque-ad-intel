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


# ---- Rule C2: fabricated testimonials (mechanical backstop) ----

def test_incident_first_person_endorsement_flagged():
    """The actual incident: a fabricated first-person testimonial with no source material."""
    copy = {"headline": "Firmer skin, naturally",
            "primary_text": "I ordered a second bottle so I would never be without it",
            "cta": "Shop now"}
    ok, issues = compliance.check_compliance(copy, "CeraVe", "")
    assert ok is False
    assert any("First-person" in i for i in issues)


def test_quoted_testimonial_flagged_without_approved_material():
    copy = {"headline": "Real results", "primary_text": '"This changed my skin completely in two weeks"', "cta": "y"}
    ok, issues = compliance.check_compliance(copy, "Brand", "")
    assert ok is False
    assert any("Quoted" in i for i in issues)


def test_quoted_testimonial_passes_when_matching_approved_material():
    testimonial = "This changed my skin completely in two weeks - Jane, verified customer"
    copy = {"headline": "Real results", "primary_text": '"This changed my skin completely in two weeks"', "cta": "y"}
    ok, issues = compliance.check_compliance(copy, "Brand", "", approved_testimonials=testimonial)
    assert ok is True
    assert issues == []


def test_short_quoted_phrase_not_flagged():
    """A short quoted word/phrase (e.g. emphasis) is not testimonial-shaped."""
    copy = {"headline": '"Natural" beauty, redefined', "primary_text": "x", "cta": "y"}
    ok, issues = compliance.check_compliance(copy, "Brand", "")
    assert ok is True


def test_reported_speech_flagged_without_approved_material():
    copy = {"headline": "Radiant skin", "primary_text": "Customers say it's a game changer for their skin", "cta": "Shop"}
    ok, issues = compliance.check_compliance(copy, "Brand", "")
    assert ok is False
    assert any("Reported-speech" in i for i in issues)


def test_numeric_claim_flagged_when_no_approved_claims():
    copy = {"headline": "94% saw results", "primary_text": "x", "cta": "y"}
    ok, issues = compliance.check_compliance(copy, "Brand", "")
    assert ok is False
    assert any("94%" in i and "approved_claims is empty" in i for i in issues), \
        "message must say WHY it fired, so it doesn't read as a bug once approved claims exist"


def test_numeric_claim_passes_when_present_in_approved_claims():
    copy = {"headline": "94% saw results", "primary_text": "x", "cta": "y"}
    ok, issues = compliance.check_compliance(copy, "Brand", "", approved_claims="94% saw results in an independent consumer trial")
    assert ok is True


def test_discount_percentage_off_not_flagged():
    """'20% off' is a price promotion, not an efficacy claim, and appears on legitimate
    drafts today - it must not burn both compliance attempts."""
    copy = {"headline": "20% off today only", "primary_text": "x", "cta": "Shop"}
    ok, issues = compliance.check_compliance(copy, "Brand", "")
    assert ok is True


def test_discount_percentage_save_not_flagged():
    copy = {"headline": "Save 20% on your first order", "primary_text": "x", "cta": "Shop"}
    ok, issues = compliance.check_compliance(copy, "Brand", "")
    assert ok is True
    assert issues == []


def test_n_out_of_m_claim_flagged():
    copy = {"headline": "9 out of 10 women agreed", "primary_text": "x", "cta": "y"}
    ok, issues = compliance.check_compliance(copy, "Brand", "")
    assert ok is False
    assert any("9 out of 10" in i for i in issues)


def test_discount_exemption_is_local_not_blanket():
    """The discount exemption must be scoped to the specific percentage it's adjacent
    to - a legitimate '20% off' elsewhere in the copy must NOT blanket-exempt an
    unrelated fabricated efficacy percentage."""
    copy = {"headline": "20% off today", "primary_text": "94% saw results in clinical trials", "cta": "Shop"}
    ok, issues = compliance.check_compliance(copy, "Brand", "")
    assert ok is False
    assert any("94%" in i for i in issues)
    assert not any("20%" in i for i in issues)
