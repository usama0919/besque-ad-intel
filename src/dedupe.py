"""Persistent dedupe store. Tracks which competitor ad IDs we've already seen."""
import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/besque")


def get_conn():
    return psycopg2.connect(DB_URL)


def init_db():
    """Create the seen_ads table if it doesn't exist."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS seen_ads (
                ad_id        TEXT PRIMARY KEY,
                page_name    TEXT,
                first_seen   TIMESTAMPTZ DEFAULT now()
            )
        """)
        conn.commit()


def is_new(ad_id: str) -> bool:
    """Return True if this ad_id has not been seen before."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM seen_ads WHERE ad_id = %s", (ad_id,))
        return cur.fetchone() is None


def mark_seen(ad_id: str, page_name: str = "") -> None:
    """Record an ad_id as seen. Ignores duplicates safely."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO seen_ads (ad_id, page_name) VALUES (%s, %s) "
            "ON CONFLICT (ad_id) DO NOTHING",
            (ad_id, page_name),
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
                decided_at  TIMESTAMPTZ DEFAULT now()
            )
        """)
        conn.commit()


def record_decision(ad_id: str, decision: str, reason: str = "") -> None:
    """Record an approve/reject decision for an ad, with a timestamp and optional reason."""
    if decision not in ("approve", "reject"):
        raise ValueError("decision must be 'approve' or 'reject'")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO review_decisions (ad_id, decision, reason) VALUES (%s, %s, %s)",
            (ad_id, decision, reason or ""),
        )
        conn.commit()


def get_decisions(ad_id: str = None):
    """Return decisions, optionally filtered by ad_id. List of (ad_id, decision, decided_at)."""
    with get_conn() as conn, conn.cursor() as cur:
        if ad_id:
            cur.execute("SELECT ad_id, decision, decided_at FROM review_decisions WHERE ad_id = %s ORDER BY decided_at", (ad_id,))
        else:
            cur.execute("SELECT ad_id, decision, decided_at FROM review_decisions ORDER BY decided_at")
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
                created_at    TIMESTAMPTZ DEFAULT now()
            )
        """)
        conn.commit()


def save_artifact(ad_id, page_name, image_path, blueprint, generated_copy, draft_image, metadata, image_prompt="", copy_prompt="", model_info=""):
    """Persist all artifacts for one ad with a timestamp. Skips if ad_id already stored."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM artifacts WHERE ad_id = %s", (ad_id,))
        if cur.fetchone() is not None:
            return
        cur.execute(
            """INSERT INTO artifacts
               (ad_id, page_name, image_path, blueprint, generated_copy, draft_image, metadata, image_prompt, copy_prompt, model_info)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (ad_id, page_name, image_path,
             _json.dumps(blueprint), _json.dumps(generated_copy),
             draft_image, _json.dumps(metadata), image_prompt, copy_prompt, model_info),
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
                created_at  TIMESTAMPTZ DEFAULT now()
            )
        """)
        conn.commit()


def add_competitor(name: str, page_id: str) -> int:
    """Append a new competitor row. Never overwrites existing rows."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO competitors (name, page_id) VALUES (%s, %s) RETURNING id",
            (name, page_id),
        )
        new_id = cur.fetchone()[0]
        conn.commit()
        return new_id


def get_competitors():
    """Return all competitors, oldest first. List of dicts: id, name, page_id, created_at."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, name, page_id, created_at, suggested_name FROM competitors ORDER BY id")
        cols = ["id", "name", "page_id", "created_at", "suggested_name"]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def update_competitor(competitor_id: int, name: str, page_id: str) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE competitors SET name = %s, page_id = %s WHERE id = %s",
            (name, page_id, competitor_id),
        )
        conn.commit()


def delete_competitor(competitor_id: int) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM competitors WHERE id = %s", (competitor_id,))
        conn.commit()


# ---- Dashboard read: full artifact data including images ----

def get_artifacts_full(limit=50):
    """Return full artifact records for the dashboard, newest first.
    Returns list of dicts with everything needed to display."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT a.ad_id, a.page_name, a.image_path, a.blueprint,
                   a.generated_copy, a.draft_image, a.metadata, a.created_at,
                   d.decision, a.image_prompt, a.copy_prompt, a.model_info
            FROM artifacts a
            LEFT JOIN LATERAL (
                SELECT decision FROM review_decisions r
                WHERE r.ad_id = a.ad_id ORDER BY decided_at DESC LIMIT 1
            ) d ON true
            ORDER BY a.created_at DESC
            LIMIT %s
        """, (limit,))
        cols = ["ad_id", "page_name", "image_path", "blueprint", "generated_copy",
                "draft_image", "metadata", "created_at", "decision",
                "image_prompt", "copy_prompt", "model_info"]
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
                created_at TIMESTAMP DEFAULT NOW()
            )"""
        )
        conn.commit()


def get_products():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, name, description, ingredients, hero_claim, image_key FROM products ORDER BY id")
        return [
            {"id": r[0], "name": r[1], "description": r[2], "ingredients": r[3], "hero_claim": r[4], "image_key": r[5] or ""}
            for r in cur.fetchall()
        ]


def add_product(name, description="", ingredients="", hero_claim=""):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO products (name, description, ingredients, hero_claim) VALUES (%s, %s, %s, %s) RETURNING id",
            (name, description, ingredients, hero_claim),
        )
        new_id = cur.fetchone()[0]
        conn.commit()
        return new_id


def update_product(product_id, name, description, ingredients, hero_claim):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE products SET name=%s, description=%s, ingredients=%s, hero_claim=%s WHERE id=%s",
            (name, description, ingredients, hero_claim, product_id),
        )
        conn.commit()


def delete_product(product_id):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM products WHERE id=%s", (product_id,))
        conn.commit()


def get_product(product_id):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, name, description, ingredients, hero_claim, image_key FROM products WHERE id=%s", (product_id,))
        r = cur.fetchone()
        if r is None:
            return None
        return {"id": r[0], "name": r[1], "description": r[2], "ingredients": r[3], "hero_claim": r[4], "image_key": r[5] or ""}


def update_artifact_copy(ad_id, generated_copy):
    """Replace the generated copy for one artifact."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("UPDATE artifacts SET generated_copy=%s WHERE ad_id=%s", (_json.dumps(generated_copy), ad_id))
        conn.commit()


def get_artifact(ad_id):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT ad_id, page_name, blueprint, generated_copy, draft_image FROM artifacts WHERE ad_id=%s ORDER BY id DESC LIMIT 1", (ad_id,))
        r = cur.fetchone()
        if r is None:
            return None
        import json as _j
        bp = r[2] if isinstance(r[2], dict) else _j.loads(r[2] or "{}")
        cp = r[3] if isinstance(r[3], dict) else _j.loads(r[3] or "{}")
        return {"ad_id": r[0], "page_name": r[1], "blueprint": bp, "generated_copy": cp, "draft_image": r[4]}


def set_suggested_name(competitor_id, suggested):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("UPDATE competitors SET suggested_name = %s WHERE id = %s", (suggested or "", competitor_id))
        conn.commit()
