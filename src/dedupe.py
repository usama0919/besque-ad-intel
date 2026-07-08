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