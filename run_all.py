"""Runs the dashboard AND the Slack listener together, synced via the database.
One command for the full live demo.
"""
import threading
import os
import uvicorn
from dotenv import load_dotenv

load_dotenv()


def start_listener():
    """Slack button listener in a background thread."""
    from src import slack_listener
    from slack_bolt.adapter.socket_mode import SocketModeHandler
    slack_listener.dedupe.init_decisions()
    handler = SocketModeHandler(slack_listener.app, os.getenv("SLACK_APP_TOKEN"))
    handler.connect()


if __name__ == "__main__":
    # Start Slack listener in background
    t = threading.Thread(target=start_listener, daemon=True)
    t.start()
    print(">> Slack listener started (buttons live)")
    print(">> Dashboard starting at http://localhost:8080")
    # Start dashboard (blocks)
    uvicorn.run("dashboard:app", host="0.0.0.0", port=int(os.getenv("PORT", 8080)), log_level="warning")
