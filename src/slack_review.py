"""Review queue: formats the per-ad Slack message. Posting is wired in at kickoff."""


def build_review_message(ad: dict, blueprint: dict, copy: dict, image_ref: str = "") -> dict:
    """Build a Slack Block Kit payload for one competitor ad review item."""
    headline = copy.get("headline", "(no headline)")
    primary = copy.get("primary_text", "")
    cta = copy.get("cta", "")
    fmt = blueprint.get("format", "unknown")
    angle = blueprint.get("angle", "")
    original_link = ad.get("destination_url", "") or ad.get("link", "")

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "New competitor ad — review"}},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*Competitor:*\n{ad.get('page_name', 'unknown')}"},
            {"type": "mrkdwn", "text": f"*Format:*\n{fmt}"},
        ]},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*Blueprint angle:* {angle}"}},
        {"type": "divider"},
        {"type": "section", "text": {"type": "mrkdwn",
            "text": f"*Besque draft:*\n*{headline}*\n{primary}\n_CTA: {cta}_"}},
    ]

    if image_ref:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*Draft image:* {image_ref}"}})
    if original_link:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*Original ad:* <{original_link}|view>"}})

    blocks.append({"type": "actions", "elements": [
        {"type": "button", "text": {"type": "plain_text", "text": "Approve"}, "style": "primary", "value": ad.get("ad_id", "")},
        {"type": "button", "text": {"type": "plain_text", "text": "Reject"}, "style": "danger", "value": ad.get("ad_id", "")},
    ]})

    return {"blocks": blocks}