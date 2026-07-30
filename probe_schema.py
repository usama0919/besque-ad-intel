"""Read-only diagnostic: what does the LIVE DB schema actually look like right now,
vs what src/dedupe.py's code assumes. No migration, no writes - every query below is a
SELECT against pg_catalog/information_schema."""
import psycopg2
from dotenv import load_dotenv
import os

load_dotenv()
DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/besque")

conn = psycopg2.connect(DB_URL)
cur = conn.cursor()

print("=== constraints on seen_ads (pg_constraint) ===")
cur.execute("""
    SELECT conname, contype FROM pg_constraint WHERE conrelid = 'seen_ads'::regclass
""")
rows = cur.fetchall()
if not rows:
    print("  (no constraints found)")
for conname, contype in rows:
    print(f"  {conname}  (contype={contype})")

print()
print("=== every table with a column named angle_id ===")
cur.execute("""
    SELECT table_name FROM information_schema.columns
    WHERE column_name = 'angle_id' ORDER BY table_name
""")
rows = cur.fetchall()
if not rows:
    print("  (none)")
for (table_name,) in rows:
    print(f"  {table_name}")

print()
print("=== angles table ===")
cur.execute("""
    SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'angles')
""")
exists = cur.fetchone()[0]
print(f"  exists: {exists}")
if exists:
    cur.execute("""
        SELECT column_name, data_type FROM information_schema.columns
        WHERE table_name = 'angles' ORDER BY ordinal_position
    """)
    for column_name, data_type in cur.fetchall():
        print(f"  {column_name}  ({data_type})")

print()
print("=== artifacts.text_in_image ===")
cur.execute("""
    SELECT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'artifacts' AND column_name = 'text_in_image'
    )
""")
print(f"  exists: {cur.fetchone()[0]}")

cur.close()
conn.close()
