"""Cloud Run Job entrypoint: runs the pipeline once, to completion.
Reads RUN_COMPETITOR_ID and RUN_MAX_PER_COMPETITOR from the environment.
"""
import os
from dotenv import load_dotenv
load_dotenv()

from src import pipeline

def main():
    cid = os.getenv("RUN_COMPETITOR_ID")
    competitor_id = int(cid) if cid and cid.strip() else None
    n = int(os.getenv("RUN_MAX_PER_COMPETITOR", "2"))
    print(f">> Job starting: competitor_id={competitor_id}, max_per_competitor={n}")
    pid = os.getenv("RUN_PRODUCT_ID")
    product_id = int(pid) if pid and pid.strip() else None
    summary = pipeline.run_once(max_per_competitor=n, competitor_id=competitor_id, product_id=product_id)
    print(f">> Job done: {summary}")

if __name__ == "__main__":
    main()
