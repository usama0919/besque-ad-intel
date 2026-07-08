"""Tests for the Slack review message formatter (no posting)."""
from src import slack_review


def _inputs():
    ad = {"ad_id": "AD1", "page_name": "CompetitorX", "destination_url": "https://x.com/ad"}
    blueprint = {"format": "product_hero", "angle": "firmer skin at any age"}
    copy = {"headline": "Firmer, Radiant Skin", "primary_text": "Botanical oils.", "cta": "Shop Now"}
    return ad, blueprint, copy


def test_message_has_blocks():
    ad, bp, copy = _inputs()
    msg = slack_review.build_review_message(ad, bp, copy)
    assert "blocks" in msg
    assert len(msg["blocks"]) > 0


def test_message_includes_copy_and_competitor():
    ad, bp, copy = _inputs()
    msg = slack_review.build_review_message(ad, bp, copy)
    text_blob = str(msg)
    assert "Firmer, Radiant Skin" in text_blob
    assert "CompetitorX" in text_blob


def test_message_has_approve_reject_buttons():
    ad, bp, copy = _inputs()
    msg = slack_review.build_review_message(ad, bp, copy)
    actions = [b for b in msg["blocks"] if b.get("type") == "actions"]
    assert actions, "should have an actions block"
    labels = [e["text"]["text"] for e in actions[0]["elements"]]
    assert "Approve" in labels and "Reject" in labels


def test_image_ref_included_when_present():
    ad, bp, copy = _inputs()
    msg = slack_review.build_review_message(ad, bp, copy, image_ref="gs://bucket/img.png")
    assert "gs://bucket/img.png" in str(msg)