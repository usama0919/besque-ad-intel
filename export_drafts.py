"""Standalone export utility - NOT part of the app. Exports Besque draft images
(plus optionally the competitor reference each was cloned from) to a local
folder and zips it, with a manifest.csv Harry can use to tell drafts apart.

Read-only against the database: only SELECTs, never an INSERT/UPDATE/DELETE.
Never imports or calls dashboard.py/pipeline.py - only src.dedupe (for the DB
connection helper) and src.assets (for the bucket-name resolver), both used
read-only here exactly as they're used read-only elsewhere in the app.

Schema note: artifacts has no competitor_id foreign key, only a free-text
page_name column captured at generation time - --competitor-id is therefore
resolved to that competitor's tracked `name` and matched against page_name
with a case-insensitive ILIKE, not a clean join. If page_name drifted from the
tracked name for some ads (the same page-name-drift this codebase already
notes elsewhere), those specific rows would be missed by --competitor-id.
--angle has no such gap - artifacts.angle_id is a real integer column.

Versioning note: an artifact's draft_image column always points at the
CURRENT file ({ad_id}_draft.png, or {ad_id}__{slug}_draft.png for an angle) -
that filename never changes across a regenerate. What gets versioned aside as
_draft_v1.png, _draft_v2.png etc. are the OLDER, superseded snapshots, written
there by generate_image_prompt.version_current_draft/edit_image BEFORE the
current file is overwritten - never the other way around. So reading
draft_image as stored already gives the latest version; this script does not
hunt for the highest-numbered _v{n}.png file, since that would fetch an OLDER
draft instead of the current one.

Usage:
    python export_drafts.py [--competitor-id ID] [--angle NAME_OR_ID]
                             [--since ISO_DATE_OR_TIMESTAMP] [--limit N]
                             [--out DIR] [--include-reference]
"""
import argparse
import csv
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from src import dedupe, assets  # noqa: E402  (load_dotenv must run first)


def _sanitize(value):
    """Strip anything Windows can't have in a filename. Collapses whitespace
    to underscores too, so the result stays legible without opening the file."""
    value = str(value or "")
    value = re.sub(r'[:/\\"\'<>|?*\r\n\t]', "", value)
    value = re.sub(r"\s+", "_", value.strip())
    return value or "untitled"


def _short_date(dt):
    if dt is None:
        return "unknown-date"
    return dt.strftime("%Y%m%d")


def _resolve_competitor_name(competitor_id):
    if competitor_id is None:
        return None
    for c in dedupe.get_competitors():
        if c["id"] == competitor_id:
            return c["name"]
    raise ValueError(f"competitor id {competitor_id} not found")


def _resolve_angle(angle_arg):
    """--angle accepts either a numeric id or a name (case-insensitive).
    Returns (angle_id, angle_name)."""
    if angle_arg is None:
        return None, None
    angles = dedupe.get_angles()
    try:
        aid = int(angle_arg)
        match = next((a for a in angles if a["id"] == aid), None)
        if match:
            return match["id"], match["name"]
        raise ValueError(f"angle id {aid} not found")
    except ValueError:
        pass
    match = next((a for a in angles if a["name"].strip().lower() == str(angle_arg).strip().lower()), None)
    if not match:
        raise ValueError(f"angle '{angle_arg}' not found (checked by id and by name)")
    return match["id"], match["name"]


def _parse_since(since_arg):
    if since_arg is None:
        return None
    try:
        return datetime.fromisoformat(since_arg)
    except ValueError:
        raise ValueError(f"--since {since_arg!r} is not a valid ISO date/timestamp (e.g. 2026-08-04)")


def fetch_rows(competitor_id=None, angle_arg=None, since_arg=None, limit=None):
    """Read-only query. Mirrors dashboard.py's own get_artifacts_full LATERAL
    join pattern for the latest review decision per (ad_id, angle_id) - written
    fresh here rather than importing dashboard.py, per this script's own
    constraint of never touching it."""
    competitor_name = _resolve_competitor_name(competitor_id)
    angle_id, angle_name = _resolve_angle(angle_arg)
    since_dt = _parse_since(since_arg)

    angles_by_id = {a["id"]: a["name"] for a in dedupe.get_angles()}

    query = """
        SELECT a.id, a.ad_id, a.page_name, a.image_path, a.draft_image, a.created_at,
               a.angle_id, a.critic_findings, d.decision,
               a.generated_copy->>'headline' AS headline,
               a.generated_copy->>'image_subtext' AS image_subtext
        FROM artifacts a
        LEFT JOIN LATERAL (
            SELECT decision FROM review_decisions r
            WHERE r.ad_id = a.ad_id AND r.angle_id IS NOT DISTINCT FROM a.angle_id
            ORDER BY decided_at DESC LIMIT 1
        ) d ON true
        WHERE 1=1
    """
    params = []
    if competitor_name:
        query += " AND a.page_name ILIKE %s"
        params.append(competitor_name)
    if angle_id is not None:
        query += " AND a.angle_id = %s"
        params.append(angle_id)
    if since_dt is not None:
        query += " AND a.created_at >= %s"
        params.append(since_dt)
    query += " ORDER BY a.created_at DESC"
    if limit is not None:
        query += " LIMIT %s"
        params.append(limit)

    with dedupe.get_conn() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        cols = ["id", "ad_id", "page_name", "image_path", "draft_image", "created_at",
                "angle_id", "critic_findings", "decision", "headline", "image_subtext"]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    for r in rows:
        r["angle_name"] = angles_by_id.get(r["angle_id"], "") if r["angle_id"] else ""
    return rows


def check_gcs_auth():
    """Probe once, up front, so a broken ADC session is reported plainly
    instead of surfacing as a wall of generic per-file 'not found' skips that
    would look like the files just don't exist. Returns (ok, message)."""
    try:
        from google.cloud import storage
        from google.auth import exceptions as auth_exceptions
    except ImportError as e:
        return False, f"google-cloud-storage not installed ({e}) - local-only export will be attempted"
    try:
        bucket = storage.Client().bucket(assets.asset_bucket_name())
        bucket.exists()
        return True, None
    except Exception as e:
        msg = str(e)
        return False, msg


def _fetch_bytes(filename, gcs_ok, bucket_cache):
    """Local assets/ dir first, then the GCS bucket - same resolution order
    dashboard.py's own /assets/{filename} route uses. Returns (bytes_or_None,
    failure_reason_or_None)."""
    local = Path("assets") / filename
    if local.exists():
        try:
            return local.read_bytes(), None
        except Exception as e:
            return None, f"local read failed: {e}"
    if not gcs_ok:
        return None, "GCS auth failed - not attempted (see the warning above; re-authenticate and re-run)"
    try:
        from google.cloud import storage
        if "client" not in bucket_cache:
            bucket_cache["client"] = storage.Client().bucket(assets.asset_bucket_name())
        blob = bucket_cache["client"].blob(filename)
        if not blob.exists():
            return None, f"not found locally or in bucket ({filename})"
        return blob.download_as_bytes(), None
    except Exception as e:
        return None, f"bucket fetch failed: {e}"


def summarize_critic_findings(findings):
    if not findings:
        return ""
    parts = []
    for f in findings:
        if isinstance(f, dict):
            parts.append(f"{f.get('confidence', '?')} {f.get('category', '?')}: {f.get('description', '')}")
        else:
            parts.append(str(f))
    return " | ".join(parts)


def main():
    parser = argparse.ArgumentParser(description="Export Besque draft images + manifest, zipped.")
    parser.add_argument("--competitor-id", type=int, default=None)
    parser.add_argument("--angle", default=None, help="angle name or id")
    parser.add_argument("--since", default=None, help="ISO date or timestamp, e.g. 2026-08-04")
    parser.add_argument("--limit", type=int, default=None, help="default: no limit")
    parser.add_argument("--out", default=None, help="default: ./exports/<timestamp>/")
    parser.add_argument("--include-reference", action="store_true")
    args = parser.parse_args()

    try:
        rows = fetch_rows(args.competitor_id, args.angle, args.since, args.limit)
    except ValueError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    if not rows:
        print("No matching artifacts found - nothing to export.")
        sys.exit(0)

    out_dir = Path(args.out) if args.out else Path("exports") / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir.mkdir(parents=True, exist_ok=True)

    gcs_ok, gcs_error = check_gcs_auth()
    if not gcs_ok:
        print("=" * 70)
        print("WARNING: GCS auth check failed - bucket fallback is UNAVAILABLE.")
        print(f"  Reason: {gcs_error}")
        print("  Re-authenticate (e.g. `gcloud auth application-default login`) and re-run.")
        print("  Continuing with LOCAL assets/ only - this run's export may be incomplete.")
        print("=" * 70)

    bucket_cache = {}
    manifest_rows = []
    exported, skipped_existing, failed = 0, 0, []

    def export_one(source_filename, ad_id, competitor, angle, created_at,
                    decision, critic_summary, kind, headline, image_subtext):
        dest_name = f"{_sanitize(competitor)}_{_sanitize(angle)}_{_sanitize(ad_id)}_{_short_date(created_at)}"
        dest_name += "_reference" if kind == "reference" else ""
        dest_name += Path(source_filename).suffix or ".png"
        dest_path = out_dir / dest_name

        # headline/image_subtext (2026-08-06): with text_in_image=False (today's default -
        # see the pool-UI default check reported alongside this), the copy exists ONLY as
        # the dashboard's HTML overlay, never baked into the PNG - a draft exported without
        # these columns is a text-free image with no way to tell what was supposed to run on
        # it. Reference rows never had generated copy of their own, so both are blank there,
        # not "None" - a human reading the CSV shouldn't have to parse a Python null.
        row_headline = headline or "" if kind != "reference" else ""
        row_subtext = image_subtext or "" if kind != "reference" else ""

        if dest_path.exists():
            manifest_rows.append([dest_name, ad_id, competitor, angle,
                                   created_at.isoformat() if created_at else "", decision or "pending",
                                   critic_summary, row_headline, row_subtext])
            return "skipped_existing"

        data, err = _fetch_bytes(os.path.basename(str(source_filename).replace("\\", "/")), gcs_ok, bucket_cache)
        if data is None:
            failed.append((dest_name, err))
            return "failed"
        dest_path.write_bytes(data)
        manifest_rows.append([dest_name, ad_id, competitor, angle,
                               created_at.isoformat() if created_at else "", decision or "pending",
                               critic_summary, row_headline, row_subtext])
        return "exported"

    for r in rows:
        competitor = r["page_name"] or "unknown-competitor"
        angle = r["angle_name"] or "no-angle"
        critic_summary = summarize_critic_findings(r["critic_findings"])

        if r["draft_image"]:
            result = export_one(r["draft_image"], r["ad_id"], competitor, angle,
                                 r["created_at"], r["decision"], critic_summary, "draft",
                                 r["headline"], r["image_subtext"])
            if result == "exported":
                exported += 1
            elif result == "skipped_existing":
                skipped_existing += 1
        else:
            failed.append((r["ad_id"], "artifact has no draft_image recorded"))

        if args.include_reference and r["image_path"]:
            result = export_one(r["image_path"], r["ad_id"], competitor, angle,
                                 r["created_at"], r["decision"], critic_summary, "reference",
                                 r["headline"], r["image_subtext"])
            if result == "exported":
                exported += 1
            elif result == "skipped_existing":
                skipped_existing += 1

    manifest_path = out_dir / "manifest.csv"
    with open(manifest_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["filename", "ad_id", "competitor", "angle", "created_at", "status",
                          "critic_findings", "headline", "image_subtext"])
        writer.writerows(manifest_rows)

    zip_base = str(out_dir)
    zip_path = shutil.make_archive(zip_base, "zip", root_dir=out_dir)

    print()
    print("=" * 70)
    print(f"Exported:        {exported}")
    print(f"Already present: {skipped_existing} (skipped, not re-fetched)")
    print(f"Failed:          {len(failed)}")
    for name, reason in failed:
        print(f"  - {name}: {reason}")
    if not gcs_ok:
        print()
        print("GCS AUTH FAILED during this run - re-authenticate and re-run to pick up")
        print("anything that needed the bucket and was skipped above.")
    print(f"Manifest: {manifest_path}")
    print(f"Zip:      {zip_path}")
    print("=" * 70)


if __name__ == "__main__":
    main()
