"""Persistent dedupe store. Tracks which competitor ad IDs we've already seen."""
import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/besque")
FORCE_REPROCESS = os.getenv("FORCE_REPROCESS") == "1"


def get_conn():
    return psycopg2.connect(DB_URL)


def init_db():
    """Create the seen_ads table if it doesn't exist. angle_id is nullable - NULL means
    "no messaging angle selected", the same dedup identity as before this column existed.
    The uniqueness guarantee is (ad_id, angle_id) via an expression index rather than a
    plain PRIMARY KEY, because Postgres treats every NULL as distinct from every other
    NULL: a plain UNIQUE(ad_id, angle_id) would let two (ad_id, NULL) rows coexist.
    COALESCE(angle_id, 0) collapses every "no angle" row onto the same key (0 is never a
    real angles.id, SERIAL starts at 1), which is what the ON CONFLICT target in
    mark_seen() below matches against."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS seen_ads (
                ad_id        TEXT,
                page_name    TEXT,
                first_seen   TIMESTAMPTZ DEFAULT now(),
                angle_id     INTEGER
            )
        """)
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS seen_ads_ad_angle_uq
            ON seen_ads (ad_id, COALESCE(angle_id, 0))
        """)
        conn.commit()


def is_new(ad_id: str, angle_id: int = None) -> bool:
    """Return True if this (ad_id, angle_id) pair has not been seen before. angle_id=None
    (no angle selected) checks the same identity as before angle support existed - a plain
    ad_id lookup. A different angle_id for an ad already seen under another angle (or under
    no angle) is a DIFFERENT pair and is_new correctly returns True for it: one ad is meant
    to produce one draft per angle. If this ad has only ever been processed with no angle
    selected, the first angle-tagged run against it will add a second row alongside the
    existing NULL-angle one, not replace it - expected, not a duplicate bug."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM seen_ads WHERE ad_id = %s AND angle_id IS NOT DISTINCT FROM %s",
            (ad_id, angle_id),
        )
        return cur.fetchone() is None


def mark_seen(ad_id: str, page_name: str = "", angle_id: int = None) -> None:
    """Record an (ad_id, angle_id) pair as seen. Ignores duplicates safely."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO seen_ads (ad_id, page_name, angle_id) VALUES (%s, %s, %s) "
            "ON CONFLICT (ad_id, COALESCE(angle_id, 0)) DO NOTHING",
            (ad_id, page_name, angle_id),
        )
        conn.commit()

# ---- Review decision capture (approve/reject persistence) ----

def init_decisions():
    """Create the review_decisions table if it doesn't exist."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS review_decisions (
                id          SERIAL PRIMARY KEY,
                ad_id       TEXT NOT NULL,
                decision    TEXT NOT NULL,
                decided_at  TIMESTAMPTZ DEFAULT now(),
                angle_id    INTEGER
            )
        """)
        conn.commit()


def record_decision(ad_id: str, decision: str, reason: str = "", angle_id: int = None) -> None:
    """Record an approve/reject decision for one (ad_id, angle_id) artifact, with a
    timestamp and optional reason. angle_id must match the artifact being decided on -
    without it, get_artifacts_full's decision lookup can't tell two angle-variants of the
    same ad apart and would show one's decision on both."""
    if decision not in ("approve", "reject"):
        raise ValueError("decision must be 'approve' or 'reject'")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO review_decisions (ad_id, decision, reason, angle_id) VALUES (%s, %s, %s, %s)",
            (ad_id, decision, reason or "", angle_id),
        )
        conn.commit()


def get_decisions(ad_id: str = None, angle_id: int = None):
    """Return decisions, optionally filtered by ad_id (and angle_id). List of
    (ad_id, decision, decided_at, reason)."""
    with get_conn() as conn, conn.cursor() as cur:
        if ad_id and angle_id is not None:
            cur.execute("SELECT ad_id, decision, decided_at, reason FROM review_decisions WHERE ad_id = %s AND angle_id IS NOT DISTINCT FROM %s ORDER BY decided_at", (ad_id, angle_id))
        elif ad_id:
            cur.execute("SELECT ad_id, decision, decided_at, reason FROM review_decisions WHERE ad_id = %s ORDER BY decided_at", (ad_id,))
        else:
            cur.execute("SELECT ad_id, decision, decided_at, reason FROM review_decisions ORDER BY decided_at")
        return cur.fetchall()


# ---- Artifact persistence (blueprint + generated output, timestamped) ----
import json as _json


def init_artifacts():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS artifacts (
                id            SERIAL PRIMARY KEY,
                ad_id         TEXT NOT NULL,
                page_name     TEXT,
                image_path    TEXT,
                blueprint     JSONB,
                generated_copy JSONB,
                draft_image   TEXT,
                metadata      JSONB,
                created_at    TIMESTAMPTZ DEFAULT now(),
                image_prompt  TEXT DEFAULT '',
                copy_prompt   TEXT DEFAULT '',
                model_info    TEXT DEFAULT '',
                archived      BOOLEAN DEFAULT false,
                angle_id      INTEGER,
                text_in_image BOOLEAN DEFAULT false,
                operator_instruction TEXT DEFAULT '',
                critic_findings JSONB DEFAULT '[]',
                format_flag TEXT DEFAULT ''
            )
        """)
        # Self-migrating: unlike angle_id/text_in_image/category before it (which each
        # needed a separate manually-run migrate_*.sql - see CLAUDE.md), these columns add
        # themselves to an already-existing table on every init_artifacts() call. ADD
        # COLUMN IF NOT EXISTS is additive and idempotent - safe to run unconditionally,
        # and closes the exact "migration not yet run" gap that stalled the angles rollout.
        cur.execute("ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS operator_instruction TEXT DEFAULT ''")
        # critic_findings (Prompt 4, Item 1): the output critic's violations for the
        # CURRENT draft only - update_artifact_findings replaces this wholesale on every
        # regenerate, it never accumulates.
        cur.execute("ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS critic_findings JSONB DEFAULT '[]'")
        # format_flag (Prompt 4, Item 4): reference_format.format_flag_reason's verdict -
        # a FLAG, never a filter, so it's just a string surfaced on the card, not
        # anything that gates save_artifact itself.
        cur.execute("ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS format_flag TEXT DEFAULT ''")
        conn.commit()


def save_artifact(ad_id, page_name, image_path, blueprint, generated_copy, draft_image, metadata,
                   image_prompt="", copy_prompt="", model_info="", angle_id=None, text_in_image=False,
                   operator_instruction="", format_flag=""):
    """Persist all artifacts for one (ad_id, angle_id) pair with a timestamp. Skips if that
    exact pair is already stored. angle_id=None reproduces the pre-angle behaviour exactly -
    one artifact per ad_id. A different angle_id for an already-processed ad is a distinct
    pair and gets its own row alongside the existing one(s), never replacing them.

    operator_instruction (Step 2) is stored verbatim alongside image_prompt - the
    auditability requirement: a reviewer looking at a wrong draft must be able to see
    whether the operator asked for it, not just infer it from the assembled prompt.

    format_flag (Prompt 4, Item 4) is reference_format.format_flag_reason's verdict on
    the COMPETITOR reference (e.g. "reference was a 6-product bundle offer") - a flag for
    a human to weigh, never a reason to skip generation."""
    with get_conn() as conn, conn.cursor() as cur:
        if FORCE_REPROCESS:
            cur.execute("DELETE FROM artifacts WHERE ad_id = %s AND angle_id IS NOT DISTINCT FROM %s", (ad_id, angle_id))
        else:
            cur.execute("SELECT 1 FROM artifacts WHERE ad_id = %s AND angle_id IS NOT DISTINCT FROM %s", (ad_id, angle_id))
            if cur.fetchone() is not None:
                return
        cur.execute(
            """INSERT INTO artifacts
               (ad_id, page_name, image_path, blueprint, generated_copy, draft_image, metadata,
                image_prompt, copy_prompt, model_info, angle_id, text_in_image, operator_instruction,
                format_flag)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (ad_id, page_name, image_path,
             _json.dumps(blueprint), _json.dumps(generated_copy),
             draft_image, _json.dumps(metadata), image_prompt, copy_prompt, model_info,
             angle_id, text_in_image, operator_instruction or "", format_flag or ""),
        )
        conn.commit()


def get_artifacts(ad_id=None):
    with get_conn() as conn, conn.cursor() as cur:
        if ad_id:
            cur.execute("SELECT ad_id, blueprint, generated_copy, created_at FROM artifacts WHERE ad_id = %s", (ad_id,))
        else:
            cur.execute("SELECT ad_id, blueprint, generated_copy, created_at FROM artifacts ORDER BY created_at")
        return cur.fetchall()


# ---- Competitor watchlist (Postgres-backed, replaces static watchlist.yaml) ----

def init_competitors():
    """Create the competitors table if it doesn't exist."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS competitors (
                id          SERIAL PRIMARY KEY,
                name        TEXT NOT NULL,
                page_id     TEXT NOT NULL,
                created_at  TIMESTAMPTZ DEFAULT now(),
                category    TEXT DEFAULT ''
            )
        """)
        conn.commit()


def add_competitor(name: str, page_id: str, category: str = "") -> int:
    """Append a new competitor row. Never overwrites existing rows."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO competitors (name, page_id, category) VALUES (%s, %s, %s) RETURNING id",
            (name, page_id, category),
        )
        new_id = cur.fetchone()[0]
        conn.commit()
        return new_id


def get_competitors():
    """Return all competitors, oldest first. List of dicts: id, name, page_id, created_at, category."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, name, page_id, created_at, suggested_name, category FROM competitors ORDER BY id")
        cols = ["id", "name", "page_id", "created_at", "suggested_name", "category"]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def update_competitor(competitor_id: int, name: str, page_id: str = None, category: str = "") -> None:
    """page_id=None (the default) leaves the existing page_id column untouched. Unlike
    add_competitor, where defaulting page_id to name is a reasonable placeholder for a
    brand-new row, an UPDATE has a real, possibly already-verified numeric page_id sitting
    in the row - overwriting it just because a caller omitted the param (e.g. a
    category-only edit) is the exact bug that wiped six verified page_ids on 2026-07-30."""
    with get_conn() as conn, conn.cursor() as cur:
        if page_id is not None:
            cur.execute(
                "UPDATE competitors SET name = %s, page_id = %s, category = %s WHERE id = %s",
                (name, page_id, category, competitor_id),
            )
        else:
            cur.execute(
                "UPDATE competitors SET name = %s, category = %s WHERE id = %s",
                (name, category, competitor_id),
            )
        conn.commit()


def delete_competitor(competitor_id: int) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM competitors WHERE id = %s", (competitor_id,))
        conn.commit()


# ---- Dashboard read: full artifact data including images ----

def get_artifacts_full(limit=50):
    """Return full artifact records for the dashboard, newest first.
    Returns list of dicts with everything needed to display. The LATERAL join now matches
    on angle_id too (IS NOT DISTINCT FROM, so NULL-angle artifacts still match NULL-angle
    decisions) - without that, two angle-variant rows for the same ad_id would both show
    whichever one's decision was recorded most recently."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT a.ad_id, a.page_name, a.image_path, a.blueprint,
                   a.generated_copy, a.draft_image, a.metadata, a.created_at,
                   d.decision, a.image_prompt, a.copy_prompt, a.model_info,
                   a.angle_id, a.text_in_image, a.operator_instruction, a.critic_findings,
                   a.format_flag
            FROM artifacts a
            LEFT JOIN LATERAL (
                SELECT decision FROM review_decisions r
                WHERE r.ad_id = a.ad_id AND r.angle_id IS NOT DISTINCT FROM a.angle_id
                ORDER BY decided_at DESC LIMIT 1
            ) d ON true
            ORDER BY a.created_at DESC
            LIMIT %s
        """, (limit,))
        cols = ["ad_id", "page_name", "image_path", "blueprint", "generated_copy",
                "draft_image", "metadata", "created_at", "decision",
                "image_prompt", "copy_prompt", "model_info",
                "angle_id", "text_in_image", "operator_instruction", "critic_findings",
                "format_flag"]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


# ---- Products library ----

def init_products():
    """Create the products table if missing."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """CREATE TABLE IF NOT EXISTS products (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                ingredients TEXT DEFAULT '',
                hero_claim TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT NOW(),
                image_key TEXT DEFAULT '',
                category TEXT DEFAULT '',
                image_keys JSONB DEFAULT '[]'::jsonb,
                visual_description TEXT DEFAULT ''
            )"""
        )
        conn.commit()


_PRODUCT_COLS = "id, name, description, ingredients, hero_claim, image_key, category, image_keys, visual_description"


def _product_row_to_dict(r):
    return {"id": r[0], "name": r[1], "description": r[2], "ingredients": r[3], "hero_claim": r[4],
            "image_key": r[5] or "", "category": r[6] or "", "image_keys": r[7] or [],
            "visual_description": r[8] or ""}


def get_products():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {_PRODUCT_COLS} FROM products ORDER BY id")
        return [_product_row_to_dict(r) for r in cur.fetchall()]


def add_product(name, description="", ingredients="", hero_claim="", category="", visual_description=""):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO products (name, description, ingredients, hero_claim, category, visual_description) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (name, description, ingredients, hero_claim, category, visual_description),
        )
        new_id = cur.fetchone()[0]
        conn.commit()
        return new_id


def update_product(product_id, name, description, ingredients, hero_claim, category="", visual_description=""):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE products SET name=%s, description=%s, ingredients=%s, hero_claim=%s, category=%s, "
            "visual_description=%s WHERE id=%s",
            (name, description, ingredients, hero_claim, category, visual_description, product_id),
        )
        conn.commit()


def delete_product(product_id):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM products WHERE id=%s", (product_id,))
        conn.commit()


def get_product(product_id):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {_PRODUCT_COLS} FROM products WHERE id=%s", (product_id,))
        r = cur.fetchone()
        if r is None:
            return None
        return _product_row_to_dict(r)


MAX_PRODUCT_IMAGES = 4


def add_product_image(product_id, key):
    """Append a reference image key to a product's fixed photo set. Raises ValueError
    past MAX_PRODUCT_IMAGES rather than silently dropping or replacing an existing photo -
    the set is meant to be curated, not rotating."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT image_keys FROM products WHERE id=%s", (product_id,))
        r = cur.fetchone()
        if r is None:
            raise ValueError(f"product {product_id} not found")
        keys = (r[0] or []) + [key]
        if len(keys) > MAX_PRODUCT_IMAGES:
            raise ValueError(f"product {product_id} already has {MAX_PRODUCT_IMAGES} reference images")
        cur.execute("UPDATE products SET image_keys=%s WHERE id=%s", (_json.dumps(keys), product_id))
        conn.commit()


def remove_product_image(product_id, key):
    """Remove one reference image key from a product's photo set. Does not touch the
    stored blob - callers that also want the blob deleted must do that themselves."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT image_keys FROM products WHERE id=%s", (product_id,))
        r = cur.fetchone()
        if r is None:
            raise ValueError(f"product {product_id} not found")
        keys = [k for k in (r[0] or []) if k != key]
        cur.execute("UPDATE products SET image_keys=%s WHERE id=%s", (_json.dumps(keys), product_id))
        conn.commit()


# ---- Messaging angles (operator-curated, not a Python enum - the set has already
# changed once, and format/realism choices per angle are judgment calls, not constants) ----

def init_angles():
    """Create the angles table if missing."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """CREATE TABLE IF NOT EXISTS angles (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                slug TEXT NOT NULL,
                body_area TEXT DEFAULT '',
                default_realism TEXT DEFAULT '',
                includes_product BOOLEAN DEFAULT true,
                notes TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT NOW()
            )"""
        )
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS angles_slug_uq ON angles (slug)")
        conn.commit()


_ANGLE_COLS = "id, name, slug, body_area, default_realism, includes_product, notes"


def _angle_row_to_dict(r):
    return {"id": r[0], "name": r[1], "slug": r[2], "body_area": r[3] or "",
            "default_realism": r[4] or "", "includes_product": r[5], "notes": r[6] or ""}


def get_angles():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {_ANGLE_COLS} FROM angles ORDER BY id")
        return [_angle_row_to_dict(r) for r in cur.fetchall()]


def get_angle(angle_id):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {_ANGLE_COLS} FROM angles WHERE id=%s", (angle_id,))
        r = cur.fetchone()
        if r is None:
            return None
        return _angle_row_to_dict(r)


def add_angle(name, slug, body_area="", default_realism="", includes_product=True, notes=""):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO angles (name, slug, body_area, default_realism, includes_product, notes) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (name, slug, body_area, default_realism, includes_product, notes),
        )
        new_id = cur.fetchone()[0]
        conn.commit()
        return new_id


def update_angle(angle_id, name, slug, body_area="", default_realism="", includes_product=True, notes=""):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE angles SET name=%s, slug=%s, body_area=%s, default_realism=%s, "
            "includes_product=%s, notes=%s WHERE id=%s",
            (name, slug, body_area, default_realism, includes_product, notes, angle_id),
        )
        conn.commit()


def delete_angle(angle_id):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM angles WHERE id=%s", (angle_id,))
        conn.commit()


# ---- Pipeline warnings (durable, so a run triggered via the Cloud Run Job path
# surfaces problems in the dashboard exactly the same as a local run - the job
# process's return value is otherwise never seen by anything) ----

def init_pipeline_warnings():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS pipeline_warnings (
                id          SERIAL PRIMARY KEY,
                kind        TEXT NOT NULL,
                detail      TEXT NOT NULL,
                created_at  TIMESTAMPTZ DEFAULT now()
            )
        """)
        conn.commit()


def record_warning(kind, detail):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO pipeline_warnings (kind, detail) VALUES (%s, %s)",
            (kind, detail),
        )
        conn.commit()


def get_recent_warnings(limit=20):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, kind, detail, created_at FROM pipeline_warnings ORDER BY id DESC LIMIT %s",
            (limit,),
        )
        cols = ["id", "kind", "detail", "created_at"]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


# ---- Run progress (single-row, path-agnostic - same reasoning as pipeline_warnings above:
# the Cloud Run Job path is a separate process with no shared memory with the dashboard, so
# "which competitor is being processed right now" has to be a DB read, not an in-memory
# variable, or it would only ever work for the LOCAL_RUN path) ----

def init_run_progress():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS run_progress (
                id                INTEGER PRIMARY KEY DEFAULT 1,
                competitor_name   TEXT DEFAULT '',
                competitor_index  INTEGER DEFAULT 0,
                competitor_total  INTEGER DEFAULT 0,
                updated_at        TIMESTAMPTZ DEFAULT now()
            )
        """)
        cur.execute("INSERT INTO run_progress (id) VALUES (1) ON CONFLICT (id) DO NOTHING")
        conn.commit()


def set_run_progress(competitor_name, competitor_index, competitor_total):
    """Record which competitor run_once is currently on. competitor_name="" (with index/
    total 0) is how run_once clears this at the end of a run, so a finished run doesn't
    leave a stale "processing X" behind for the next poll."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE run_progress SET competitor_name=%s, competitor_index=%s, "
            "competitor_total=%s, updated_at=now() WHERE id=1",
            (competitor_name, competitor_index, competitor_total),
        )
        conn.commit()


def get_run_progress():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT competitor_name, competitor_index, competitor_total, updated_at "
            "FROM run_progress WHERE id=1"
        )
        r = cur.fetchone()
        if r is None:
            return None
        return {"competitor_name": r[0] or "", "competitor_index": r[1] or 0,
                "competitor_total": r[2] or 0, "updated_at": r[3]}


# ---- Brand settings (Prompt 4, Item 5) - single-row, self-migrating, editable from the
# UI. Palette substitution ("re-themed to Besque's terracotta/maroon/gold/cream palette")
# must be DATA, not a hardcoded string in generate_image_prompt.py, so a future correction
# in the UI takes effect immediately, the same reasoning as products.visual_description
# (Step 3, Part 3's verification). ----

DEFAULT_PALETTE = "terracotta, maroon, gold, cream"


def init_brand_settings():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS brand_settings (
                id          INTEGER PRIMARY KEY DEFAULT 1,
                palette     TEXT DEFAULT '{DEFAULT_PALETTE}',
                updated_at  TIMESTAMPTZ DEFAULT now()
            )
        """)
        cur.execute("INSERT INTO brand_settings (id) VALUES (1) ON CONFLICT (id) DO NOTHING")
        conn.commit()


def get_brand_settings():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT palette FROM brand_settings WHERE id=1")
        r = cur.fetchone()
        if r is None:
            return {"palette": DEFAULT_PALETTE}
        return {"palette": r[0] or DEFAULT_PALETTE}


def update_brand_settings(palette):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE brand_settings SET palette=%s, updated_at=now() WHERE id=1",
            (palette or DEFAULT_PALETTE,),
        )
        conn.commit()


def update_artifact_copy(ad_id, generated_copy, angle_id=None):
    """Replace the generated copy for one (ad_id, angle_id) artifact. Without angle_id,
    a plain WHERE ad_id=%s UPDATE would rewrite every angle-variant row sharing that
    ad_id, not just the one being edited."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE artifacts SET generated_copy=%s WHERE ad_id=%s AND angle_id IS NOT DISTINCT FROM %s",
            (_json.dumps(generated_copy), ad_id, angle_id),
        )
        conn.commit()


def update_artifact_image_prompt(ad_id, image_prompt, angle_id=None):
    """Replace the recorded image prompt for one (ad_id, angle_id) artifact, so the prompt
    shown in the dashboard matches the PNG currently on disk after an image edit."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE artifacts SET image_prompt=%s WHERE ad_id=%s AND angle_id IS NOT DISTINCT FROM %s",
            (image_prompt, ad_id, angle_id),
        )
        conn.commit()


def update_artifact_findings(ad_id, findings, angle_id=None):
    """Replace the output critic's findings for one (ad_id, angle_id) artifact - REPLACES
    wholesale, never accumulates, so a regenerate's findings reflect only the CURRENT
    draft, not a stale one still describing a violation that's no longer there."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE artifacts SET critic_findings=%s WHERE ad_id=%s AND angle_id IS NOT DISTINCT FROM %s",
            (_json.dumps(findings or []), ad_id, angle_id),
        )
        conn.commit()


def get_artifact(ad_id, angle_id=None):
    """Return the latest artifact for one (ad_id, angle_id) pair. angle_id=None matches
    the pre-angle behaviour exactly. Without angle_id, ORDER BY id DESC LIMIT 1 would
    return whichever angle-variant was generated most recently, not necessarily the one
    the caller means.

    Returns angle_id/text_in_image too - callers editing a draft (dashboard.py's
    api_edit_image) need these to restore the ORIGINAL generation's rule-6 mode rather
    than falling back to brand_rules()'s hardcoded defaults."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT ad_id, page_name, blueprint, generated_copy, draft_image, angle_id, text_in_image "
            "FROM artifacts WHERE ad_id=%s AND angle_id IS NOT DISTINCT FROM %s ORDER BY id DESC LIMIT 1",
            (ad_id, angle_id),
        )
        r = cur.fetchone()
        if r is None:
            return None
        import json as _j
        bp = r[2] if isinstance(r[2], dict) else _j.loads(r[2] or "{}")
        cp = r[3] if isinstance(r[3], dict) else _j.loads(r[3] or "{}")
        return {"ad_id": r[0], "page_name": r[1], "blueprint": bp, "generated_copy": cp,
                "draft_image": r[4], "angle_id": r[5], "text_in_image": r[6]}


def set_suggested_name(competitor_id, suggested):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("UPDATE competitors SET suggested_name = %s WHERE id = %s", (suggested or "", competitor_id))
        conn.commit()
