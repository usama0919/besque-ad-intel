"""Slack Socket Mode listener: handles Approve/Reject clicks, saves to Postgres.
Run alongside the pipeline."""
import os
import logging
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from src import dedupe

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("slack_listener")

app = App(token=os.getenv("SLACK_BOT_TOKEN"))


@app.action("approve")
def handle_approve(ack, body, respond):
    ack()
    ad_id = body["actions"][0]["value"]
    dedupe.record_decision(ad_id, "approve")
    log.info("Ad %s APPROVED", ad_id)
    respond(f":white_check_mark: Approved (ad {ad_id}) - saved to database.")


@app.action("reject")
def handle_reject(ack, body, respond):
    ack()
    ad_id = body["actions"][0]["value"]
    dedupe.record_decision(ad_id, "reject")
    log.info("Ad %s REJECTED", ad_id)
    respond(f":x: Rejected (ad {ad_id}) - saved to database.")


if __name__ == "__main__":
    dedupe.init_decisions()
    handler = SocketModeHandler(app, os.getenv("SLACK_APP_TOKEN"))
    log.info("Slack listener started - waiting for button clicks...")
    handler.start()
