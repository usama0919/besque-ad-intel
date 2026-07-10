"""Scheduler: runs the pipeline on a daily cadence.

For the PoC this runs an immediate pass, then repeats every INTERVAL_HOURS.
In production, prefer the OS scheduler (cron / Task Scheduler / GCP Cloud
Scheduler) calling `python -m src.pipeline` once per day - see README.
"""
import time
import logging
from dotenv import load_dotenv
from src import pipeline

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("scheduler")

INTERVAL_HOURS = 24


def run_forever(interval_hours=INTERVAL_HOURS):
    """Run the pipeline immediately, then every interval_hours."""
    while True:
        log.info("Starting scheduled pipeline run")
        try:
            summary = pipeline.run_once()
            log.info("Scheduled run finished: %s", summary)
        except Exception as e:
            log.error("Scheduled run failed: %s (will retry next cycle)", e)
        log.info("Sleeping %d hours until next run", interval_hours)
        time.sleep(interval_hours * 3600)


if __name__ == "__main__":
    run_forever()
