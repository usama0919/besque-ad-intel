"""Demo launcher: starts the Slack button listener in the background,
then runs one pipeline pass. One command for a clean live demo.
"""
import threading
import logging
import time
import os
import sys
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("demo")


def _start_listener():
    from src import slack_listener
    from slack_bolt.adapter.socket_mode import SocketModeHandler
    slack_listener.dedupe.init_decisions()
    handler = SocketModeHandler(slack_listener.app, os.getenv("SLACK_APP_TOKEN"))
    handler.start()


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 2

    log.info("Starting Slack button listener in background...")
    t = threading.Thread(target=_start_listener, daemon=True)
    t.start()
    time.sleep(3)

    log.info("Running pipeline (%d ads per competitor)...", n)
    from src import pipeline
    summary = pipeline.run_once(max_per_competitor=n)
    log.info("Pipeline complete: %s", summary)

    log.info("Listener still running - Approve/Reject buttons are live. Press Ctrl+C when done.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Demo ended.")