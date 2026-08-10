"""Classify product_reviews rows against the six angle slugs via a batched Claude pass
(Task E Part 1, 2026-08-07). Stores results in review_angle_matches - a many-to-many
join table, never a column - so a review can match more than one angle and a human can
correct/delete individual (review, angle) rows later without this script overwriting
that correction on a re-run (see dedupe.insert_review_angle_matches's ON CONFLICT DO
NOTHING).

Default mode classifies a random SAMPLE only (500 rows) - the operator asked to see this
sample's per-angle counts and confidence distribution before the full corpus runs.
--full is a separate, explicit opt-in this script supports but that nothing in this repo
invokes yet - do not run it without being asked.

Exclusion of the 48 price/discount rows and medical_flag rows (Task A's findings) is
DELIBERATELY NOT done here - that exclusion belongs at selection time (when a review is
picked for use in an ad), not at classification time, so this table stays a complete,
honest picture of the whole corpus, not a pre-filtered one.

Usage:
    python classify_review_angles.py --sample-size 500 [--seed 42] [--dry-run]
    python classify_review_angles.py --full   # NOT invoked anywhere yet - explicit opt-in
"""
import argparse
import json
import os
import random
import sys

from dotenv import load_dotenv
load_dotenv()

import anthropic

from src import dedupe, json_response  # noqa: E402  (load_dotenv must run first)

CLAUDE_MODEL = os.getenv("CLAUDE_HAIKU_MODEL", "claude-haiku-4-5-20251001")
BATCH_SIZE = 20

# Definitions written from the angle names/body_area in the `angles` table itself
# (seed_angles.py) - the table's own `notes` column is empty for every angle except
# loose_skin (which just says body_area isn't confirmed yet), so there was no existing
# richer definition to read instead of writing one.
ANGLE_DEFINITIONS = {
    "crepey_skin": (
        "the reviewer describes their OWN skin texture as crepey, crinkled, thin, or "
        "paper-like - an aging-skin-TEXTURE concern specifically, not general dryness."
    ),
    "menopause": (
        "the reviewer explicitly names menopause, perimenopause, or a hormonal life "
        "stage as the context for their skin concern."
    ),
    "glp1": (
        "the reviewer explicitly names a GLP-1 medication (Ozempic, Wegovy, Mounjaro, "
        "Zepbound, semaglutide, tirzepatide) or rapid medication-driven weight loss as "
        "the context for their skin concern."
    ),
    "bruising": (
        "the reviewer mentions bruising, bruising easily, or skin fragility/thinness "
        "that leads to bruises."
    ),
    "sun_damage": (
        "the reviewer mentions sun damage, sun spots, age spots, hyperpigmentation, or "
        "photoaging."
    ),
    "loose_skin": (
        "the reviewer describes loose, sagging, or lax skin as a general skin-laxity "
        "concern. If the review ALSO names a GLP-1 medication or rapid weight loss as "
        "the cause, match glp1 too (or instead, if that's the stronger read) - both "
        "matching is fine when both are genuinely present."
    ),
}


def build_prompt(batch, angle_slugs):
    angle_desc = "\n".join(f"- {slug}: {ANGLE_DEFINITIONS[slug]}" for slug in angle_slugs)
    review_lines = "\n".join(
        f"- review_id={r['review_id']!r} rating={r['rating']} text={r['review_text']!r}"
        for r in batch
    )
    return (
        "You are classifying real customer reviews of a body oil against six marketing "
        "angles (skin-concern categories used to pick which reviews' language informs a "
        "given ad). For EACH review below, return every angle it genuinely matches - a "
        "review may match zero, one, or several angles. Only match an angle when the "
        "review's own words genuinely support it; being generically positive about the "
        "product is not enough on its own.\n\n"
        f"ANGLES:\n{angle_desc}\n\n"
        f"REVIEWS:\n{review_lines}\n\n"
        "Return ONLY valid JSON, no markdown fences, no preamble, of the exact shape: "
        '{"results": [{"review_id": "...", "matches": [{"angle_slug": "...", '
        '"confidence": "high"|"medium"|"low", "rationale": "short phrase quoting or '
        'paraphrasing the specific words that justify this match"}]}]} '
        "- exactly one entry per review_id listed above, matches: [] when none apply."
    )


def classify_batch(client, batch, angle_slugs):
    prompt = build_prompt(batch, angle_slugs)
    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    raw_text = message.content[0].text if message.content else ""
    parsed = json_response.extract_json(raw_text)
    return parsed.get("results", [])


def main():
    parser = argparse.ArgumentParser(description="Classify product_reviews against angle slugs.")
    parser.add_argument("--sample-size", type=int, default=500)
    parser.add_argument("--seed", type=int, default=42, help="random seed for reproducible sampling")
    parser.add_argument("--full", action="store_true",
                         help="classify every unclassified review, not just a sample - explicit opt-in")
    parser.add_argument("--dry-run", action="store_true", help="classify and report, write nothing")
    args = parser.parse_args()

    dedupe.init_product_reviews()
    dedupe.init_angles()
    dedupe.init_angle_language()
    dedupe.init_review_angle_matches()

    angles = dedupe.get_angles()
    slug_to_angle_id = {a["slug"]: a["id"] for a in angles}
    angle_slugs = list(ANGLE_DEFINITIONS.keys())
    missing_angles = [s for s in angle_slugs if s not in slug_to_angle_id]
    if missing_angles:
        print(f"ERROR: angle slug(s) {missing_angles} not found in the angles table - aborting.")
        sys.exit(1)

    with dedupe.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, review_id, product_id, rating, review_text, medical_flag "
            "FROM product_reviews"
        )
        cols = ["id", "review_id", "product_id", "rating", "review_text", "medical_flag"]
        all_reviews = [dict(zip(cols, r)) for r in cur.fetchall()]

    already_classified = dedupe.get_classified_review_ids()
    candidates = [r for r in all_reviews if r["id"] not in already_classified]

    if args.full:
        to_classify = candidates
        print(f"--full: classifying all {len(to_classify)} not-yet-classified reviews "
              f"(of {len(all_reviews)} total, {len(already_classified)} already classified).")
    else:
        random.seed(args.seed)
        sample_size = min(args.sample_size, len(candidates))
        to_classify = random.sample(candidates, sample_size)
        print(f"SAMPLE MODE: classifying {len(to_classify)} of {len(all_reviews)} total reviews "
              f"(seed={args.seed}). Run with --full for the whole corpus - not done here.")

    client = anthropic.Anthropic(timeout=60.0, max_retries=2)
    review_by_id = {r["review_id"]: r for r in to_classify}

    per_angle_counts = {s: 0 for s in angle_slugs}
    per_angle_confidence = {s: {"high": 0, "medium": 0, "low": 0} for s in angle_slugs}
    no_match_count = 0
    rows_to_insert = []
    unparsed_review_ids = set(review_by_id.keys())

    batches = [to_classify[i:i + BATCH_SIZE] for i in range(0, len(to_classify), BATCH_SIZE)]
    for i, batch in enumerate(batches, 1):
        print(f"batch {i}/{len(batches)} ({len(batch)} reviews)...")
        try:
            results = classify_batch(client, batch, angle_slugs)
        except Exception as e:
            print(f"  batch {i} FAILED: {type(e).__name__}: {e} - skipping this batch")
            continue
        for entry in results:
            rid = entry.get("review_id")
            if rid not in review_by_id:
                continue
            unparsed_review_ids.discard(rid)
            matches = entry.get("matches") or []
            if not matches:
                no_match_count += 1
                continue
            for m in matches:
                slug = m.get("angle_slug")
                if slug not in slug_to_angle_id:
                    continue
                confidence = (m.get("confidence") or "").lower()
                if confidence not in ("high", "medium", "low"):
                    confidence = "low"
                per_angle_counts[slug] += 1
                per_angle_confidence[slug][confidence] += 1
                rows_to_insert.append({
                    "product_review_id": review_by_id[rid]["id"],
                    "angle_id": slug_to_angle_id[slug],
                    "confidence": confidence,
                    "rationale": m.get("rationale", ""),
                })

    print("\n=== Per-angle counts (a review may count toward more than one angle) ===")
    for slug in sorted(angle_slugs, key=lambda s: -per_angle_counts[s]):
        conf = per_angle_confidence[slug]
        print(f"{slug}: {per_angle_counts[slug]}  (high={conf['high']} medium={conf['medium']} low={conf['low']})")

    print(f"\nReviews matching NO angle: {no_match_count} / {len(to_classify)}")
    if unparsed_review_ids:
        print(f"\nWARNING: {len(unparsed_review_ids)} review(s) never came back in any batch result "
              f"(batch failure or model omission) - not counted above, not inserted: "
              f"{sorted(unparsed_review_ids)[:20]}")

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return

    inserted = dedupe.insert_review_angle_matches(rows_to_insert)
    print(f"\nInserted (or already-present, skipped): {inserted} match rows "
          f"across {len(to_classify) - len(unparsed_review_ids)} classified reviews.")


if __name__ == "__main__":
    main()
