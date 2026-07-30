-- Migration for the messaging-angles feature (angles table, ad x angle dedup,
-- text_in_image persistence). NOT YET RUN — review before executing.
--
-- Confirmed by the current pytest run: seen_ads/artifacts/review_decisions already exist
-- in the live DB from before this session, so init_db()/init_artifacts()/init_decisions()'s
-- "CREATE TABLE IF NOT EXISTS" bodies are no-ops against them (same drift pattern CLAUDE.md
-- documents for competitors.category). Every statement below is checked safe against the
-- existing rows (138+ artifacts, matching counts in seen_ads/review_decisions) - see the
-- comment above each one.

-- 1. angles: brand-new table, so dedupe.init_angles() already self-provisions this on any
--    fresh DB. Included here too so this file is the complete picture in one place.
CREATE TABLE IF NOT EXISTS angles (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT NOT NULL,
    body_area TEXT DEFAULT '',
    default_realism TEXT DEFAULT '',
    includes_product BOOLEAN DEFAULT true,
    notes TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS angles_slug_uq ON angles (slug);

-- 2. seen_ads: drop the ad_id-only PRIMARY KEY (safe - removes only the constraint, no
--    rows deleted), add the new nullable angle_id column, then rebuild the uniqueness
--    guarantee as an expression index over (ad_id, COALESCE(angle_id, 0)).
--    Why an expression index and not a plain UNIQUE(ad_id, angle_id): Postgres treats
--    every NULL as distinct from every other NULL, so a plain composite unique constraint
--    would silently allow two (ad_id, NULL) rows to coexist - COALESCE collapses every
--    "no angle" row onto the same key (0 is never a real angles.id; SERIAL starts at 1).
--    Safety against existing rows: every row today has angle_id NULL (just-added column),
--    so COALESCE(angle_id,0) = 0 for all of them, and ad_id was already unique under the
--    dropped PK - so (ad_id, 0) stays unique and this index builds with zero conflicts.
ALTER TABLE seen_ads DROP CONSTRAINT IF EXISTS seen_ads_pkey;
ALTER TABLE seen_ads ADD COLUMN IF NOT EXISTS angle_id INTEGER;
CREATE UNIQUE INDEX IF NOT EXISTS seen_ads_ad_angle_uq ON seen_ads (ad_id, COALESCE(angle_id, 0));

-- 3. artifacts: two new nullable/defaulted columns, no constraint changes. artifacts has
--    never had a DB-level uniqueness guarantee (dedup is app-level, in save_artifact's
--    SELECT-then-insert) - this migration doesn't add one, consistent with that existing
--    design. Safe: adding a nullable column, and a boolean column with a literal default,
--    touches no existing data.
ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS angle_id INTEGER;
ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS text_in_image BOOLEAN DEFAULT false;

-- 4. review_decisions: one new nullable column, so get_artifacts_full's LATERAL join can
--    match a decision to the correct angle-variant artifact row instead of "most recent
--    decision for this ad_id regardless of angle" (the bug this whole column exists to
--    close - see dedupe.record_decision's docstring). Safe: nullable column, no rewrite.
ALTER TABLE review_decisions ADD COLUMN IF NOT EXISTS angle_id INTEGER;
