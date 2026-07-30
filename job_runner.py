"""Cloud Run Job entrypoint: runs the pipeline once, to completion.
Reads RUN_COMPETITOR_ID, RUN_MAX_PER_COMPETITOR, RUN_PRODUCT_ID, RUN_ANGLE_ID, RUN_REALISM,
RUN_TEXT_IN_IMAGE, RUN_INCLUDE_PRODUCT, RUN_BODY_AREA, RUN_OFFER_TEXT from the environment.
"""
import os
from dotenv import load_dotenv
load_dotenv()

from src import pipeline

def main():
    cid = os.getenv("RUN_COMPETITOR_ID")
    competitor_id = int(cid) if cid and cid.strip() else None
    n = int(os.getenv("RUN_MAX_PER_COMPETITOR", "2"))
    pid = os.getenv("RUN_PRODUCT_ID")
    product_id = int(pid) if pid and pid.strip() else None
    aid = os.getenv("RUN_ANGLE_ID")
    angle_id = int(aid) if aid and aid.strip() else None
    realism = os.getenv("RUN_REALISM") or None
    text_in_image = os.getenv("RUN_TEXT_IN_IMAGE") == "1"
    # Absent env var (e.g. an older deployed image that predates this var) must still mean
    # "include product", matching today's default - only an explicit "0" turns it off.
    include_product = os.getenv("RUN_INCLUDE_PRODUCT", "1") != "0"
    # Free-text, per-run, never sourced from angles.body_area - see api_run's docstring.
    body_area = os.getenv("RUN_BODY_AREA") or None
    offer_text = os.getenv("RUN_OFFER_TEXT") or None
    print(f">> Job starting: competitor_id={competitor_id}, max_per_competitor={n}, "
          f"product_id={product_id}, angle_id={angle_id}, realism={realism!r}, "
          f"text_in_image={text_in_image}, include_product={include_product}, "
          f"body_area={body_area!r}, offer_text={offer_text!r}")
    summary = pipeline.run_once(max_per_competitor=n, competitor_id=competitor_id, product_id=product_id,
                                 angle_id=angle_id, realism=realism, text_in_image=text_in_image,
                                 include_product=include_product, body_area=body_area, offer_text=offer_text)
    print(f">> Job done: {summary}")

if __name__ == "__main__":
    main()
