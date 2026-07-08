"""Retry helper: run a flaky operation with a few attempts before giving up.

Used to wrap the Apify scrape and other network calls so transient failures
retry, and a persistent failure returns cleanly instead of crashing the run.
"""
import time
import logging

log = logging.getLogger("retry")


def with_retry(fn, attempts=3, delay=1.0, backoff=2.0):
    """Call fn() with retries. Returns fn()'s result, or raises the last error
    after all attempts are exhausted.

    attempts: total tries. delay: seconds before first retry. backoff: multiplier.
    """
    last_err = None
    wait = delay
    for i in range(1, attempts + 1):
        try:
            return fn()
        except Exception as e:
            last_err = e
            log.warning("Attempt %d/%d failed: %s", i, attempts, e)
            if i < attempts:
                time.sleep(wait)
                wait *= backoff
    raise last_err


def try_or_skip(fn, default=None):
    """Run fn(); if it raises, log and return default (clean skip, no crash)."""
    try:
        return fn()
    except Exception as e:
        log.error("Operation failed, skipping cleanly: %s", e)
        return default
