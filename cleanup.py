from dotenv import load_dotenv
load_dotenv()
from src import dedupe

prefixes = ('DEC_', 'ART_', 'PIPE_', 'SAMPLE_', 'TEST_', 'RUN3_')
conn = dedupe.get_conn()
cur = conn.cursor()

for table in ('review_decisions', 'artifacts'):
    for p in prefixes:
        cur.execute(f"DELETE FROM {table} WHERE ad_id LIKE %s", (p + '%',))
conn.commit()
print("Cleaned. Test entries removed.")

rows = dedupe.get_artifacts_full(50)
print("Artifacts left:", len(rows))
print("Sample IDs:", [r['ad_id'] for r in rows[:5]])