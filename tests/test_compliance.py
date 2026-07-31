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


# ---- Unauthorized offer/discount/urgency mechanic (2026-07-31): a real draft read
# "50% off - ONLY while stock lasts" with offer_text empty, lifted from the competitor's
# own clearance-sale blueprint.offer. Opt-in via the offer_text kwarg (see the _UNSET
# sentinel in compliance.py) so every test above, which doesn't know about offer_text,
# keeps passing unchanged. ----

def test_discount_percentage_flagged_when_offer_text_omitted_entirely():
    """Backward-compat guard: check_compliance called exactly as every pre-existing
    caller calls it (no offer_text kwarg at all) must be COMPLETELY unaffected by this
    new rule - the discount exemption tests above must keep passing forever."""
    copy = {"headline": "20% off today only", "primary_text": "x", "cta": "Shop"}
    ok, issues = compliance.check_compliance(copy, "Brand", "")
    assert ok is True
    assert issues == []


def test_discount_percentage_flagged_when_offer_text_explicitly_empty():
    copy = {"headline": "50% off", "primary_text": "ONLY while stock lasts", "cta": "Shop"}
    ok, issues = compliance.check_compliance(copy, "Brand", "", offer_text="")
    assert ok is False
    assert any("50% off" in i for i in issues)
    assert any("while stock lasts" in i for i in issues)


def test_discount_percentage_flagged_when_offer_text_none():
    copy = {"headline": "50% off today", "primary_text": "x", "cta": "Shop"}
    ok, issues = compliance.check_compliance(copy, "Brand", "", offer_text=None)
    assert ok is False


def test_offer_allowed_when_offer_text_supplied():
    """The same discount language passes once an operator-supplied offer_text exists for
    this run - it's no longer unauthorized, it's the run's own configured offer."""
    copy = {"headline": "50% off - ONLY while stock lasts", "primary_text": "x", "cta": "Shop"}
    ok, issues = compliance.check_compliance(copy, "Brand", "", offer_text="50% off this week")
    assert ok is True
    assert issues == []


def test_price_flagged_when_offer_text_absent():
    copy = {"headline": "Now just $19.99", "primary_text": "x", "cta": "Shop"}
    issues = compliance.check_unauthorized_offer(copy, offer_text="")
    assert any("$19.99" in i for i in issues)


def test_urgency_mechanic_flagged_without_discount():
    copy = {"headline": "Limited time only", "primary_text": "x", "cta": "Shop"}
    issues = compliance.check_unauthorized_offer(copy, offer_text="")
    assert any("Limited time" in i or "limited time" in i for i in issues)


def test_check_unauthorized_offer_empty_when_offer_text_given():
    copy = {"headline": "50% off while stock lasts", "primary_text": "x", "cta": "Shop"}
    assert compliance.check_unauthorized_offer(copy, offer_text="50% off") == []


def test_discount_exemption_is_local_not_blanket():
    """The discount exemption must be scoped to the specific percentage it's adjacent
    to - a legitimate '20% off' elsewhere in the copy must NOT blanket-exempt an
    unrelated fabricated efficacy percentage."""
    copy = {"headline": "20% off today", "primary_text": "94% saw results in clinical trials", "cta": "Shop"}
    ok, issues = compliance.check_compliance(copy, "Brand", "")
    assert ok is False
    assert any("94%" in i for i in issues)
    assert not any("20%" in i for i in issues)
