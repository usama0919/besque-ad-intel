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
                format_flag TEXT DEFAULT '',
                product_override_note TEXT DEFAULT ''
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
        # product_override_note (silent-override audit, 2026-08-05): set when
        # resolve_effective_include_product forced include_product off against an
        # explicit operator True - a human decision silently overruled with no feedback
        # is the actual defect this closes, same "surface, never gate" pattern as
        # format_flag above.
        cur.execute("ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS product_override_note TEXT DEFAULT ''")
        # include_product/retheme_colours/realism/body_area/offer_text/product_id
        # (2026-08-06, the regenerate-freezes-the-prompt-forever fix): every run-strip
        # input process_ad actually used, deliberately left NULL-able (no DEFAULT) so a
        # historical row predating this migration is visibly "never recorded" rather than
        # indistinguishably "recorded as the default" - pipeline._regenerate_existing_draft
        # checks for NULL specifically and logs which inputs it had to default, per the
        # explicit requirement that a missing stored input is reported, not silently
        # guessed. Needed because regenerate must REBUILD generate_image_prompt.build_image_prompt
        # from these stored inputs (so current rules/guardrails/compliance apply) rather
        # than replaying the artifact's own frozen historical image_prompt text forever -
        # discovered live when the Grüns GLP-1 illustrated-mode fix silently never reached
        # an ad that had already been regenerated once before the fix landed.
        cur.execute("ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS include_product BOOLEAN")
        cur.execute("ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS retheme_colours BOOLEAN")
        cur.execute("ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS realism TEXT")
        cur.execute("ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS body_area TEXT")
        cur.execute("ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS offer_text TEXT")
        cur.execute("ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS product_id INTEGER")
        # element_provenance (2026-08-07, reference usability gate reversal): which
        # elements were ADDED (no existing reference zone, placed newly into the scene)
        # vs SUBSTITUTED (an existing reference zone replaced) vs never rendered at all -
        # {"product": "added"|"substituted"|"none", "text": "added"|"substituted"|"none"}.
        # Replaces the old product_override_note mechanism (kept for historical rows,
        # but nothing sets it any more) now that a productless/textless reference is
        # never skipped and never suppressed - a reviewer needs to see WHICH path each
        # element took, not just that generation happened. DEFAULT '{}' so a
        # pre-migration row reads as "nothing recorded", never guessed.
        cur.execute("ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS element_provenance JSONB DEFAULT '{}'")
        conn.commit()


def save_artifact(ad_id, page_name, image_path, blueprint, generated_copy, draft_image, metadata,
                   image_prompt="", copy_prompt="", model_info="", angle_id=None, text_in_image=False,
                   operator_instruction="", format_flag="", product_override_note="", regenerate=None,
                   include_product=None, retheme_colours=None, realism=None, body_area=None,
                   offer_text=None, product_id=None, element_provenance=None):
    """Persist all artifacts for one (ad_id, angle_id) pair with a timestamp. Skips if that
    exact pair is already stored. angle_id=None reproduces the pre-angle behaviour exactly -
    one artifact per ad_id. A different angle_id for an already-processed ad is a distinct
    pair and gets its own row alongside the existing one(s), never replacing them.

    operator_instruction (Step 2) is stored verbatim alongside image_prompt - the
    auditability requirement: a reviewer looking at a wrong draft must be able to see
    whether the operator asked for it, not just infer it from the assembled prompt.

    format_flag (Prompt 4, Item 4) is reference_format.format_flag_reason's verdict on
    the COMPETITOR reference (e.g. "reference was a 6-product bundle offer") - a flag for
    a human to weigh, never a reason to skip generation.

    product_override_note (silent-override audit, 2026-08-05) is set by process_ad when
    resolve_effective_include_product forced include_product off against an explicit
    operator True - worded so the operator understands the reference had no product to
    substitute, not just that the toggle "didn't work." Empty when no override happened.

    regenerate (Chunk 5, Item 7b): explicit per-call override for whether an existing
    (ad_id, angle_id) row gets replaced. regenerate=None (the default) preserves EXACTLY
    today's behaviour - driven by the FORCE_REPROCESS env var read once at import time -
    so every existing caller is unaffected. Pass True/False explicitly to decide this
    PER CALL instead: pipeline.process_ad's deliberate-regenerate path (an operator
    explicitly re-selecting an already-generated ad) passes this rather than requiring
    FORCE_REPROCESS=1 set for the whole process, which would also silently affect every
    OTHER save_artifact call happening anywhere else in that same run.

    include_product/retheme_colours/realism/body_area/offer_text/product_id (2026-08-06):
    the run-strip inputs process_ad actually used, persisted so a FUTURE regenerate can
    rebuild this exact generation's prompt from current code instead of replaying a frozen
    historical one - see pipeline._regenerate_existing_draft. All default None/NULL, never
    guessed at here - a caller that doesn't pass them (every pre-existing call site until
    updated) reproduces today's schema exactly, and a regenerate reading NULL back knows
    to log that it defaulted rather than assume a real recorded value.

    element_provenance (2026-08-07, reference usability gate reversal): {"product": ...,
    "text": ...}, each one of "added"/"substituted"/"none" - which path each element
    actually took for THIS generation, computed by process_ad from the blueprint's own
    fields (never a fixed value, never keyed off ad_id/competitor/page). None (a caller
    that doesn't pass it) stores '{}', read back as "nothing recorded" by any future
    reader, never guessed at."""
    effective_regenerate = FORCE_REPROCESS if regenerate is None else regenerate
    with get_conn() as conn, conn.cursor() as cur:
        if effective_regenerate:
            cur.execute("DELETE FROM artifacts WHERE ad_id = %s AND angle_id IS NOT DISTINCT FROM %s", (ad_id, angle_id))
        else:
            cur.execute("SELECT 1 FROM artifacts WHERE ad_id = %s AND angle_id IS NOT DISTINCT FROM %s", (ad_id, angle_id))
            if cur.fetchone() is not None:
                return
        cur.execute(
            """INSERT INTO artifacts
               (ad_id, page_name, image_path, blueprint, generated_copy, draft_image, metadata,
                image_prompt, copy_prompt, model_info, angle_id, text_in_image, operator_instruction,
                format_flag, product_override_note, include_product, retheme_colours, realism,
                body_area, offer_text, product_id, element_provenance)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (ad_id, page_name, image_path,
             _json.dumps(blueprint), _json.dumps(generated_copy),
             draft_image, _json.dumps(metadata), image_prompt, copy_prompt, model_info,
             angle_id, text_in_image, operator_instruction or "", format_flag or "",
             product_override_note or "", include_product, retheme_colours, realism,
             body_area, offer_text, product_id, _json.dumps(element_provenance or {})),
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
                   a.format_flag, a.product_override_note, a.element_provenance
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
                "format_flag", "product_override_note", "element_provenance"]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def get_pending_artifacts(limit=500):
    """The actual pending-review queue - get_artifacts_full's rows, minus any draft the
    critic retry loop (pipeline.process_ad's MAX_IMAGE_ATTEMPTS) still found HIGH-confidence
    after its one corrective retry. No new column: output_critic.has_high_confidence reads
    the same critic_findings every other caller already does, so a still-flagged draft is
    excluded here purely in Python, in-process - it's never presented as clean, but it's
    also never lost; a reviewer can still find it (it's in get_artifacts_full, just not
    here)."""
    from src import output_critic
    return [a for a in get_artifacts_full(limit=limit)
            if not output_critic.has_high_confidence(a.get("critic_findings") or [])]


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
                visual_description TEXT DEFAULT '',
                substance_colour TEXT DEFAULT '',
                shopify_product_ids JSONB DEFAULT '[]'::jsonb
            )"""
        )
        # Self-migrating (Item 6b, 2026-08-04), same pattern as artifacts' operator_instruction/
        # critic_findings/format_flag - CREATE TABLE IF NOT EXISTS above is a no-op against an
        # already-existing products table, so this is what actually adds the column in
        # production. Free text naming the product-derived substance's real colour (e.g.
        # "bright golden-amber oil"), used verbatim by generate_image_prompt's edit-mode
        # substance-recolour instruction INSTEAD OF parsing visual_description - that field is
        # prose, not reliably parseable. Empty by default: omit the colour phrase entirely
        # rather than inventing one, see generate_image_prompt._substance_recolour_clause.
        cur.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS substance_colour TEXT DEFAULT ''")
        # shopify_product_ids (2026-08-06, C1 build item 3): the Shopify export's productId
        # values that belong to this internal product - a DIFFERENT id namespace from this
        # table's own `id` (e.g. Magic Body Oil's real Shopify variant ids are
        # 13-digit-ish strings, not our small integer). This is the one mapping the reviews
        # importer reads instead of a hardcoded Python set, so scoping reviews to a product
        # is a config change (edit this JSONB array) never a code change. Same shape as
        # image_keys (a JSONB array on this same table) rather than a separate mapping
        # table, deliberately - there's no need for a join, just a per-product list.
        # Present in CREATE TABLE too (not just this ALTER) - several existing columns on
        # this table are ALTER-only and not reproducible from code against a fresh DB; this
        # one shouldn't join that list.
        cur.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS shopify_product_ids JSONB DEFAULT '[]'::jsonb")
        # certifications (Task, badge/banner substitution row, 2026-08-07): a STRUCTURED
        # list (e.g. ["Vegan", "Cruelty Free", "100% Natural"]), never parsed out of
        # visual_description's prose - same reasoning already established for
        # substance_colour above ("that field is prose, not reliably parseable"). This is
        # the one place generate_image_prompt's badge substitution reads a genuine Besque
        # counterpart from; empty by default, so a badge with no real counterpart falls
        # through to removal exactly as before, never a guessed cert.
        cur.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS certifications JSONB DEFAULT '[]'::jsonb")
        conn.commit()


_PRODUCT_COLS = ("id, name, description, ingredients, hero_claim, image_key, category, "
                  "image_keys, visual_description, substance_colour, shopify_product_ids, certifications")


def _product_row_to_dict(r):
    return {"id": r[0], "name": r[1], "description": r[2], "ingredients": r[3], "hero_claim": r[4],
            "image_key": r[5] or "", "category": r[6] or "", "image_keys": r[7] or [],
            "visual_description": r[8] or "", "substance_colour": r[9] or "",
            "shopify_product_ids": r[10] or [], "certifications": r[11] or []}


def get_products():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {_PRODUCT_COLS} FROM products ORDER BY id")
        return [_product_row_to_dict(r) for r in cur.fetchall()]


def add_product(name, description="", ingredients="", hero_claim="", category="", visual_description="",
                 substance_colour=""):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO products (name, description, ingredients, hero_claim, category, visual_description, "
            "substance_colour) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (name, description, ingredients, hero_claim, category, visual_description, substance_colour),
        )
        new_id = cur.fetchone()[0]
        conn.commit()
        return new_id


def update_product(product_id, name, description, ingredients, hero_claim, category="", visual_description="",
                    substance_colour=""):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE products SET name=%s, description=%s, ingredients=%s, hero_claim=%s, category=%s, "
            "visual_description=%s, substance_colour=%s WHERE id=%s",
            (name, description, ingredients, hero_claim, category, visual_description, substance_colour, product_id),
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


def set_shopify_product_ids(product_id, shopify_product_ids):
    """Targeted single-column UPDATE - deliberately not update_product(), which is a
    read-modify-write over every product field and has already wiped verified data once
    for a different table (competitors.page_id) from exactly this shape of call. Replaces
    the whole list (like set, not append) - the caller is expected to pass the complete,
    reviewed set of Shopify productIds for this product, not one to add."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM products WHERE id=%s", (product_id,))
        if cur.fetchone() is None:
            raise ValueError(f"product {product_id} not found")
        cur.execute("UPDATE products SET shopify_product_ids=%s WHERE id=%s",
                    (_json.dumps(list(shopify_product_ids)), product_id))
        conn.commit()


# ---- Product reviews (Chunk 9, C1 - 2026-08-06). Imported from a Shopify review-app
# export, scoped to whichever products.shopify_product_ids the import script resolves each
# raw row against - never a hardcoded product assumption here or in the importer. nickname
# only: full_name and email are never read from the source file, let alone stored - see
# import_reviews.py's own field list. medical_flag is a STORED marker (the matched
# content_safety.MEDICAL_KEYWORDS term, or NULL), not a pre-import exclusion - a review
# mentioning a medical term is still real, still imported, just excluded from generation
# by default via get_reviews_for_product's own filter, so it stays visible/auditable
# rather than silently vanishing from the corpus. ----

def init_product_reviews():
    """Create the product_reviews table if missing. review_id (the source export's own
    review id) is UNIQUE so re-running the importer is idempotent - see import_reviews.py,
    which checks existing review_ids before inserting rather than relying on this
    constraint to reject duplicates as an error path."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """CREATE TABLE IF NOT EXISTS product_reviews (
                id SERIAL PRIMARY KEY,
                review_id TEXT NOT NULL UNIQUE,
                product_id INTEGER NOT NULL REFERENCES products(id),
                shopify_product_id TEXT NOT NULL,
                handle TEXT DEFAULT '',
                variant TEXT DEFAULT '',
                nickname TEXT DEFAULT '',
                rating INTEGER,
                review_date TIMESTAMPTZ,
                review_text TEXT NOT NULL,
                char_length INTEGER NOT NULL,
                medical_flag TEXT,
                imported_at TIMESTAMPTZ DEFAULT NOW()
            )"""
        )
        conn.commit()


_REVIEW_COLS = ("id, review_id, product_id, shopify_product_id, handle, variant, nickname, "
                "rating, review_date, review_text, char_length, medical_flag, imported_at")


def _review_row_to_dict(r):
    return {"id": r[0], "review_id": r[1], "product_id": r[2], "shopify_product_id": r[3],
            "handle": r[4], "variant": r[5], "nickname": r[6], "rating": r[7],
            "review_date": r[8], "review_text": r[9], "char_length": r[10],
            "medical_flag": r[11], "imported_at": r[12]}


def get_existing_review_ids():
    """Every review_id already imported - the importer's own idempotency check (fetched
    once, up front, rather than a per-row existence query against ~19k rows)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT review_id FROM product_reviews")
        return {r[0] for r in cur.fetchall()}


def insert_product_reviews(rows):
    """Bulk insert - rows is a list of dicts with exactly this table's own columns (minus
    id/imported_at). ON CONFLICT (review_id) DO NOTHING as a second idempotency layer
    underneath get_existing_review_ids's own pre-filter, never an error on a re-run."""
    if not rows:
        return 0
    with get_conn() as conn, conn.cursor() as cur:
        for row in rows:
            cur.execute(
                """INSERT INTO product_reviews
                   (review_id, product_id, shopify_product_id, handle, variant, nickname,
                    rating, review_date, review_text, char_length, medical_flag)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (review_id) DO NOTHING""",
                (row["review_id"], row["product_id"], row["shopify_product_id"], row["handle"],
                 row["variant"], row["nickname"], row["rating"], row["review_date"],
                 row["review_text"], row["char_length"], row["medical_flag"]),
            )
        conn.commit()
    return len(rows)


def get_reviews_for_product(product_id, exclude_medical_flag=True):
    """The actual product-agnostic query point: filters by OUR internal product_id, never
    a Shopify id or a hardcoded assumption about which product is being asked for.
    exclude_medical_flag=True (the default) matches today's "usable for generation" set -
    pass False to see everything stored, including medically-flagged rows, for audit."""
    with get_conn() as conn, conn.cursor() as cur:
        query = f"SELECT {_REVIEW_COLS} FROM product_reviews WHERE product_id=%s"
        if exclude_medical_flag:
            query += " AND medical_flag IS NULL"
        cur.execute(query, (product_id,))
        return [_review_row_to_dict(r) for r in cur.fetchall()]


# ---- Review <-> angle classification (Task E Part 1, 2026-08-07) - a many-to-many join
# table, NOT a column on product_reviews: a review may genuinely speak to more than one
# angle (e.g. a menopause review that also mentions crepey skin), and the team reviews/
# corrects these after the fact, so this must stay freely editable/deletable per
# (review, angle) pair rather than a single overwritable classification per review. ----

def init_review_angle_matches():
    """Create the review_angle_matches table if missing. UNIQUE(product_review_id,
    angle_id) so re-running the classifier on an already-classified review is idempotent
    (ON CONFLICT DO NOTHING in insert_review_angle_matches below), never a duplicate row."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """CREATE TABLE IF NOT EXISTS review_angle_matches (
                id SERIAL PRIMARY KEY,
                product_review_id INTEGER NOT NULL REFERENCES product_reviews(id),
                angle_id INTEGER NOT NULL REFERENCES angles(id),
                confidence TEXT NOT NULL,
                rationale TEXT DEFAULT '',
                corrected BOOLEAN DEFAULT false,
                classified_at TIMESTAMPTZ DEFAULT NOW()
            )"""
        )
        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS review_angle_matches_uq "
            "ON review_angle_matches (product_review_id, angle_id)"
        )
        conn.commit()


def insert_review_angle_matches(rows):
    """rows: list of dicts with product_review_id, angle_id, confidence, rationale.
    ON CONFLICT DO NOTHING - re-classifying an already-matched (review, angle) pair never
    errors and never overwrites a human correction (corrected=true rows are left alone,
    since this never UPDATEs, only INSERTs)."""
    if not rows:
        return 0
    with get_conn() as conn, conn.cursor() as cur:
        for row in rows:
            cur.execute(
                """INSERT INTO review_angle_matches
                   (product_review_id, angle_id, confidence, rationale)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (product_review_id, angle_id) DO NOTHING""",
                (row["product_review_id"], row["angle_id"], row["confidence"], row.get("rationale", "")),
            )
        conn.commit()
    return len(rows)


def get_classified_review_ids():
    """Every product_reviews.id that already has at least one row in
    review_angle_matches - lets a classification run skip rows it's already covered,
    the same up-front-fetch idempotency shape as get_existing_review_ids()."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT product_review_id FROM review_angle_matches")
        return {r[0] for r in cur.fetchall()}


# ---- Angle language (SUBSTITUTION AS ONE RULE task, 2026-08-07) - the per-angle
# vocabulary docs/angle_language.md supplies: a TABLE keyed on angle slug, not a column,
# because it's an independent axis from the angles table's own operator-curated
# defaults (body_area/default_realism/includes_product) - this is text content, angles
# is generation config. Source doc is now committed at docs/angle_language.md - see
# that file for the six angles' core_angle/causes/main_pain_point/main_benefit/
# common_phrases/result_phrases/image_direction content, and its override note on
# why the doc's own Step 3/Step 4 sections must never be encoded into a prompt.
# headline/subtext generation in generate_copy.py stay documented stubs until a loader
# populates this table from the doc. ----

def init_angle_language():
    """Create the angle_language table if missing. angle_slug REFERENCES angles(slug) so
    a typo'd slug fails loudly at insert time rather than silently orphaning a row no
    query will ever join back to a real angle. common_phrases/result_phrases/
    best_verbatims are JSONB arrays - best_verbatims entries are
    {"quote": str, "attribution": "nickname + first initial ONLY"} per the doc's
    non-negotiable override (no age, no full name, no platform name)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """CREATE TABLE IF NOT EXISTS angle_language (
                id SERIAL PRIMARY KEY,
                angle_slug TEXT NOT NULL UNIQUE REFERENCES angles(slug),
                core_angle TEXT NOT NULL DEFAULT '',
                causes TEXT NOT NULL DEFAULT '',
                main_pain_point TEXT NOT NULL DEFAULT '',
                main_benefit TEXT NOT NULL DEFAULT '',
                common_phrases JSONB NOT NULL DEFAULT '[]',
                result_phrases JSONB NOT NULL DEFAULT '[]',
                best_verbatims JSONB NOT NULL DEFAULT '[]',
                image_direction TEXT NOT NULL DEFAULT '',
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )"""
        )
        conn.commit()


_ANGLE_LANGUAGE_COLS = ("angle_slug, core_angle, causes, main_pain_point, main_benefit, "
                         "common_phrases, result_phrases, best_verbatims, image_direction")


def _angle_language_row_to_dict(r):
    return {"angle_slug": r[0], "core_angle": r[1], "causes": r[2], "main_pain_point": r[3],
            "main_benefit": r[4], "common_phrases": r[5] or [], "result_phrases": r[6] or [],
            "best_verbatims": r[7] or [], "image_direction": r[8]}


def get_angle_language(angle_slug):
    """None if this angle has no language row yet - callers must treat that as "no
    vocabulary available for this angle," never fall back to guessing one."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {_ANGLE_LANGUAGE_COLS} FROM angle_language WHERE angle_slug=%s", (angle_slug,))
        r = cur.fetchone()
        return _angle_language_row_to_dict(r) if r else None


def upsert_angle_language(angle_slug, core_angle, causes, main_pain_point, main_benefit,
                           common_phrases, result_phrases, best_verbatims, image_direction):
    """Insert or fully replace this angle's language row - a loader re-run (e.g. the doc
    was corrected) always reflects the doc's current content exactly, never merges stale
    fields with new ones."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO angle_language
               (angle_slug, core_angle, causes, main_pain_point, main_benefit,
                common_phrases, result_phrases, best_verbatims, image_direction)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (angle_slug) DO UPDATE SET
                 core_angle=EXCLUDED.core_angle, causes=EXCLUDED.causes,
                 main_pain_point=EXCLUDED.main_pain_point, main_benefit=EXCLUDED.main_benefit,
                 common_phrases=EXCLUDED.common_phrases, result_phrases=EXCLUDED.result_phrases,
                 best_verbatims=EXCLUDED.best_verbatims, image_direction=EXCLUDED.image_direction,
                 updated_at=NOW()""",
            (angle_slug, core_angle, causes, main_pain_point, main_benefit,
             _json.dumps(common_phrases), _json.dumps(result_phrases),
             _json.dumps(best_verbatims), image_direction),
        )
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
    than falling back to brand_rules()'s hardcoded defaults. Also returns image_path/
    metadata/image_prompt/copy_prompt/model_info/format_flag/product_override_note/
    element_provenance - pipeline.py's regenerate path carries these forward unchanged
    onto the new row (element_provenance is recomputed fresh there, same as testimonial -
    see pipeline._regenerate_existing_draft).

    Also returns include_product/retheme_colours/realism/body_area/offer_text/product_id
    (2026-08-06) AS STORED - None for any of these means "never recorded" (a row from
    before this migration, or a caller that didn't pass it), NOT "recorded as False/empty".
    pipeline._regenerate_existing_draft relies on that distinction to know which stored
    inputs it has to default and log, rather than treating None and a real False/empty
    value as the same thing."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT ad_id, page_name, blueprint, generated_copy, draft_image, angle_id, text_in_image, "
            "image_path, metadata, image_prompt, copy_prompt, model_info, format_flag, product_override_note, "
            "include_product, retheme_colours, realism, body_area, offer_text, product_id, element_provenance "
            "FROM artifacts WHERE ad_id=%s AND angle_id IS NOT DISTINCT FROM %s ORDER BY id DESC LIMIT 1",
            (ad_id, angle_id),
        )
        r = cur.fetchone()
        if r is None:
            return None
        import json as _j
        bp = r[2] if isinstance(r[2], dict) else _j.loads(r[2] or "{}")
        cp = r[3] if isinstance(r[3], dict) else _j.loads(r[3] or "{}")
        meta = r[8] if isinstance(r[8], dict) else _j.loads(r[8] or "{}")
        elem_prov = r[20] if isinstance(r[20], dict) else _j.loads(r[20] or "{}")
        return {"ad_id": r[0], "page_name": r[1], "blueprint": bp, "generated_copy": cp,
                "draft_image": r[4], "angle_id": r[5], "text_in_image": r[6],
                "image_path": r[7] or "", "metadata": meta, "image_prompt": r[9] or "",
                "copy_prompt": r[10] or "", "model_info": r[11] or "",
                "format_flag": r[12] or "", "product_override_note": r[13] or "",
                "include_product": r[14], "retheme_colours": r[15], "realism": r[16],
                "body_area": r[17], "offer_text": r[18], "product_id": r[19],
                "element_provenance": elem_prov}


def set_suggested_name(competitor_id, suggested):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("UPDATE competitors SET suggested_name = %s WHERE id = %s", (suggested or "", competitor_id))
        conn.commit()


# ---- Scraped ad pool (fetch-and-store only - pipeline.fetch_pool's home table).
# Deliberately separate from seen_ads/artifacts: this is the candidate pool BEFORE
# any dedup/generation gate, not a replacement for either. gcs_path is left NULL by
# fetch_pool for now - no step yet downloads bytes into the bucket, only Apify's own
# image_url and metadata are stored here. ----

def init_scraped_ads():
    """Create the scraped_ads table if missing. Unique on (ad_id, competitor_id), not
    ad_id alone - the same ad_id can legitimately show up under two different
    competitors' searches (a reshare/cross-post), and that's a distinct pool row,
    not a duplicate to collapse."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS scraped_ads (
                id            SERIAL PRIMARY KEY,
                ad_id         TEXT NOT NULL,
                competitor_id INT NOT NULL,
                image_url     TEXT,
                gcs_path      TEXT,
                raw_meta      JSONB,
                fetched_at    TIMESTAMPTZ DEFAULT now(),
                status        TEXT DEFAULT 'pool'
            )
        """)
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS scraped_ads_ad_competitor_uq
            ON scraped_ads (ad_id, competitor_id)
        """)
        # Self-migrating (Chunk 2C): the filter now accepts DCO/CAROUSEL records
        # alongside plain IMAGE ones (scrape.py's REJECT_NOT_IMAGE became "no usable
        # static image", not "wrong media_type") - the grid needs the REAL media_type
        # on the row to display it, not a normalised "IMAGE" for everything.
        cur.execute("ALTER TABLE scraped_ads ADD COLUMN IF NOT EXISTS media_type TEXT DEFAULT ''")
        conn.commit()


def upsert_scraped_ad(ad_id, competitor_id, image_url, raw_meta, media_type=""):
    """Insert one scraped ad into the pool, or refresh raw_meta/media_type/fetched_at
    if this exact (ad_id, competitor_id) pair is already stored. A direct upsert on
    the pool's own unique index - NOT update_competitor's read-modify-write shape,
    which wiped six verified page_ids once already (see CLAUDE.md); there is no
    partial-field update path here to get wrong.

    Stores exactly ONE row per (ad_id, competitor_id) even when the source record
    carries multiple images (a DCO/CAROUSEL variant set) - image_url is always the
    caller's chosen first image, the rest live in raw_meta untouched. The unique
    index is (ad_id, competitor_id), not (ad_id, competitor_id, image_index) - a
    row-per-variant shape is explicitly out of scope."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO scraped_ads (ad_id, competitor_id, image_url, raw_meta, media_type)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (ad_id, competitor_id) DO UPDATE
               SET raw_meta = EXCLUDED.raw_meta, media_type = EXCLUDED.media_type, fetched_at = now()""",
            (ad_id, competitor_id, image_url, _json.dumps(raw_meta), media_type or ""),
        )
        conn.commit()


def get_scraped_ad_ids(competitor_id):
    """Existing ad_ids already stored for this competitor, as a set."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT ad_id FROM scraped_ads WHERE competitor_id = %s", (competitor_id,))
        return {r[0] for r in cur.fetchall()}


def get_scraped_ads(competitor_id=None, status=None, limit=None, offset=0):
    """Return pool rows, newest first, AS STORED - callers get raw_meta back exactly
    as it was upserted, no derivation. Optional filters by competitor_id and/or
    status. limit=None (the default) returns everything, matching this function's
    original callers; dashboard.py's GET /api/pool passes an explicit limit/offset
    so pagination is real SQL LIMIT/OFFSET, not a fetch-everything-then-slice."""
    with get_conn() as conn, conn.cursor() as cur:
        query = "SELECT id, ad_id, competitor_id, image_url, gcs_path, raw_meta, fetched_at, status, media_type FROM scraped_ads WHERE 1=1"
        params = []
        if competitor_id is not None:
            query += " AND competitor_id = %s"
            params.append(competitor_id)
        if status is not None:
            query += " AND status = %s"
            params.append(status)
        query += " ORDER BY fetched_at DESC"
        if limit is not None:
            query += " LIMIT %s OFFSET %s"
            params += [limit, offset]
        cur.execute(query, params)
        cols = ["id", "ad_id", "competitor_id", "image_url", "gcs_path", "raw_meta", "fetched_at", "status", "media_type"]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def count_scraped_ads(competitor_id=None, status=None):
    """Total row count for the same filters get_scraped_ads accepts - lets a paginated
    caller (dashboard.py's GET /api/pool) know if there's more without fetching
    everything just to len() it."""
    with get_conn() as conn, conn.cursor() as cur:
        query = "SELECT COUNT(*) FROM scraped_ads WHERE 1=1"
        params = []
        if competitor_id is not None:
            query += " AND competitor_id = %s"
            params.append(competitor_id)
        if status is not None:
            query += " AND status = %s"
            params.append(status)
        cur.execute(query, params)
        return cur.fetchone()[0]


def get_scraped_ads_by_ad_ids(ad_ids):
    """Batch-fetch scraped_ads rows for an explicit list of ad_ids (Chunk 4's
    pipeline.generate_from_selection) in one query. Returns {ad_id: row}. If the
    same ad_id exists under more than one competitor (a rare but legitimate
    reshare/cross-post case - scraped_ads is unique on (ad_id, competitor_id), not
    ad_id alone), the most recently fetched row wins - callers get exactly one row
    per requested ad_id, never a list to disambiguate themselves."""
    if not ad_ids:
        return {}
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id, ad_id, competitor_id, image_url, gcs_path, raw_meta, fetched_at, status, media_type
               FROM scraped_ads WHERE ad_id = ANY(%s) ORDER BY fetched_at ASC""",
            (list(ad_ids),),
        )
        cols = ["id", "ad_id", "competitor_id", "image_url", "gcs_path", "raw_meta", "fetched_at", "status", "media_type"]
        result = {}
        for row in cur.fetchall():
            d = dict(zip(cols, row))
            result[d["ad_id"]] = d  # ASC order - later (more recent) row overwrites earlier
        return result


def update_scraped_ad_status(ad_id, competitor_id, status):
    """Move one scraped_ads row's status - e.g. off 'pool' as
    pipeline.generate_from_selection progresses it (Chunk 4), so the grid can show
    what's already been generated from without a separate join against artifacts.
    Scoped by (ad_id, competitor_id), the table's own unique key - never ad_id
    alone, which isn't guaranteed unique across competitors."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE scraped_ads SET status=%s WHERE ad_id=%s AND competitor_id=%s",
            (status, ad_id, competitor_id),
        )
        conn.commit()


def get_artifact_ad_ids(ad_ids, angle_id=None):
    """Which of the given ad_ids already have an artifacts row for angle_id
    (Chunk 5, Item 3) - the grid marks a card as already-generated for the
    CURRENTLY SELECTED angle BEFORE the operator clicks, not after. Deliberately
    angle_id-specific, unlike scraped_ads.status (Chunk 4), which is angle-
    agnostic: the same ad can be generated for one angle and still fresh for
    another, and the grid's marking must reflect THAT, not a single flat
    per-row status that can't tell the two apart."""
    if not ad_ids:
        return set()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ad_id FROM artifacts WHERE ad_id = ANY(%s) AND angle_id IS NOT DISTINCT FROM %s",
            (list(ad_ids), angle_id),
        )
        return {row[0] for row in cur.fetchall()}


# ---- Generate jobs (Chunk 5, Item 4) - backgrounds POST /api/generate exactly
# like fetch_jobs backgrounds POST /api/fetch, so a browser request doesn't block
# on a multi-ad generation run. Keyed by an opaque job id (a uuid, not a
# competitor_id) since one generation call can span ad_ids from different
# competitors - there is no single natural key the way fetch_pool has one.
# DB-backed for the same multi-instance reason fetch_jobs/pipeline_warnings/
# run_progress all are (CLAUDE.md, ship.ps1's --min-instances 1 --max-instances 5). ----

def init_generate_jobs():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS generate_jobs (
                id             TEXT PRIMARY KEY,
                status         TEXT NOT NULL DEFAULT 'running',
                ad_ids         JSONB,
                progress       JSONB DEFAULT '{}'::jsonb,
                result         JSONB,
                error          TEXT,
                stop_requested BOOLEAN DEFAULT false,
                started_at     TIMESTAMPTZ DEFAULT now(),
                finished_at    TIMESTAMPTZ
            )
        """)
        conn.commit()


def start_generate_job(job_id, ad_ids):
    """Create a 'running' job row - one per POST /api/generate call, never reused
    across calls (unlike fetch_jobs, which is one row per competitor and gets
    reclaimed on the next fetch)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO generate_jobs (id, status, ad_ids, progress, result, error, stop_requested, started_at, finished_at)
               VALUES (%s, 'running', %s, '{}'::jsonb, NULL, NULL, false, now(), NULL)""",
            (job_id, _json.dumps(list(ad_ids))),
        )
        conn.commit()


def update_generate_job_progress(job_id, ad_id, result):
    """Merge one ad's result into the job's progress dict - called after EACH ad
    finishes (pipeline.generate_from_selection's on_ad_done callback), so a
    poller sees live per-ad progress, not just the final summary once the whole
    selection is done. The JSONB `||` merge is a single atomic statement, not
    read-then-write - safe even if something else touched this row concurrently."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE generate_jobs SET progress = progress || %s::jsonb WHERE id=%s",
            (_json.dumps({ad_id: result}), job_id),
        )
        conn.commit()


def finish_generate_job(job_id, result=None, error=None):
    """Record the terminal state of one generate job - 'done' with the
    generate_from_selection summary dict, or 'error' with the exception message.
    Must be called from the background thread's own try/except (both branches) -
    a thread that dies without reaching this leaves the row stuck on 'running'."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE generate_jobs SET status=%s, result=%s, error=%s, finished_at=now() WHERE id=%s",
            ("error" if error else "done",
             _json.dumps(result) if result is not None else None,
             error, job_id),
        )
        conn.commit()


def request_generate_job_stop(job_id):
    """Flip stop_requested for one job - polled by the background thread's own
    should_stop callable, checked both between ads AND immediately before the
    paid Gemini call (Chunk 5, Item 5) via the same plumbing Chunk 4 already
    built into process_ad/generate_from_selection."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("UPDATE generate_jobs SET stop_requested=true WHERE id=%s", (job_id,))
        conn.commit()


def get_generate_job(job_id):
    """Return one generation job's state, or None if job_id is unrecognised.
    Self-heals a stale 'running' row (older than GENERATE_JOB_STALE_SECONDS) the
    moment anyone polls it - unlike fetch_jobs, a dead generate_jobs thread can't
    block a FUTURE call (each POST /api/generate always claims a brand-new
    job_id), but the poller watching THIS job_id would otherwise see 'running'
    forever with no terminal state if the thread died - same self-recovery
    principle as get_fetch_job, applied here for visibility rather than
    unblocking a collision."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, status, ad_ids, progress, result, error, stop_requested, started_at, finished_at "
            "FROM generate_jobs WHERE id=%s",
            (job_id,),
        )
        r = cur.fetchone()
        if r is None:
            return None
        job = {"id": r[0], "status": r[1], "ad_ids": r[2], "progress": r[3], "result": r[4],
               "error": r[5], "stop_requested": r[6], "started_at": r[7], "finished_at": r[8]}
    if job["status"] == "running" and _is_stale(job["started_at"], GENERATE_JOB_STALE_SECONDS):
        message = f"stale: claimed but never finished within {GENERATE_JOB_STALE_SECONDS}s, treated as failed"
        finish_generate_job(job_id, error=message)
        job["status"], job["error"], job["result"] = "error", message, None
    return job


# ---- Fetch jobs (Chunk 2C) - backgrounds POST /api/fetch so a browser request
# doesn't block on the ~minutes-long live Apify call. Keyed by competitor_id (one
# in-flight fetch per competitor, not a job history) - a real second click while one
# is already running must be rejected, not queued or silently duplicated, so
# try_start_fetch_job is a single atomic INSERT..ON CONFLICT..WHERE statement, not
# read-then-write: two concurrent POSTs for the same competitor_id must not both
# believe they started it. DB-backed for the same reason pipeline_warnings/
# run_progress are (CLAUDE.md): ship.ps1 runs besque-dashboard with
# --min-instances 1 --max-instances 5, so an in-memory dict on one container
# wouldn't be visible to a status poll that lands on another. ----

# A 'running' row this old is treated as failed rather than blocking (fetch_jobs)
# or silently hanging forever in the poller's eyes (generate_jobs) - a background
# thread that dies (a killed process, an OOM, the missing-Pillow crash that
# prompted this fix) never reaches finish_fetch_job/finish_generate_job, and
# without this, try_start_fetch_job's own WHERE guard blocks that competitor's
# fetches forever, exactly what happened live on 2026-08-04 for competitor 1.
# fetch_pool is Apify-only (no deconstruct/Gemini) - real calls observed
# completing in seconds to low minutes, with one actor run as long as ~5 min
# noted elsewhere in this codebase - 15 minutes gives real runs generous
# headroom while still self-recovering same-session.
FETCH_JOB_STALE_SECONDS = 900
# generate_from_selection is sequential, ~2 min/ad (CLAUDE.md's own measured
# sweep timing) - a several-ad grid selection can legitimately run well past
# fetch's own timeout, so this gets a longer one: generous for realistic
# selection sizes, still short enough to recover same-session.
GENERATE_JOB_STALE_SECONDS = 1800


def init_fetch_jobs():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS fetch_jobs (
                competitor_id INTEGER PRIMARY KEY,
                status        TEXT NOT NULL DEFAULT 'done',
                result        JSONB,
                error         TEXT,
                started_at    TIMESTAMPTZ,
                finished_at   TIMESTAMPTZ
            )
        """)
        conn.commit()


def try_start_fetch_job(competitor_id):
    """Atomically claim the 'running' slot for one competitor's fetch job. Returns
    True if this call won the claim: a fresh row, an existing row that was NOT
    'running' (a prior done/error job for this competitor gets overwritten, same
    as a fresh start), OR a 'running' row stale beyond FETCH_JOB_STALE_SECONDS -
    self-recovery for a thread that claimed the slot and then died before ever
    calling finish_fetch_job, so it can't block that competitor's fetches
    forever. Returns False only for a job that's still 'running' AND fresh.
    The WHERE clause on the DO UPDATE is what makes this safe under a race: only
    one of two concurrent callers can ever see their own write take effect
    against a row that was already 'running' (and not yet stale)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO fetch_jobs (competitor_id, status, result, error, started_at, finished_at)
               VALUES (%s, 'running', NULL, NULL, now(), NULL)
               ON CONFLICT (competitor_id) DO UPDATE
               SET status = 'running', result = NULL, error = NULL,
                   started_at = now(), finished_at = NULL
               WHERE fetch_jobs.status != 'running'
                  OR fetch_jobs.started_at < now() - (%s * interval '1 second')
               RETURNING competitor_id""",
            (competitor_id, FETCH_JOB_STALE_SECONDS),
        )
        won = cur.fetchone() is not None
        conn.commit()
        return won


def finish_fetch_job(competitor_id, result=None, error=None):
    """Record the terminal state of one fetch job - 'done' with the fetch_pool
    result dict, or 'error' with the exception message. Must be called from
    inside the background thread's own try/except (both branches, success and
    failure) - a thread that dies without reaching this leaves the row stuck on
    'running' until it's either reclaimed (try_start_fetch_job, once stale) or
    read (get_fetch_job self-heals it directly)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE fetch_jobs SET status=%s, result=%s, error=%s, finished_at=now() WHERE competitor_id=%s",
            ("error" if error else "done",
             _json.dumps(result) if result is not None else None,
             error, competitor_id),
        )
        conn.commit()


def get_fetch_job(competitor_id):
    """Return one competitor's fetch job state, or None if none has ever run.
    Self-heals a stale 'running' row (older than FETCH_JOB_STALE_SECONDS) the
    moment anyone reads it - a poller must eventually see a real terminal state
    rather than 'running' forever if the background thread died, even before
    anyone retries the fetch (which is try_start_fetch_job's own, separate
    self-recovery path)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT competitor_id, status, result, error, started_at, finished_at "
            "FROM fetch_jobs WHERE competitor_id=%s",
            (competitor_id,),
        )
        r = cur.fetchone()
        if r is None:
            return None
        job = {"competitor_id": r[0], "status": r[1], "result": r[2], "error": r[3],
               "started_at": r[4], "finished_at": r[5]}
    if job["status"] == "running" and _is_stale(job["started_at"], FETCH_JOB_STALE_SECONDS):
        message = f"stale: claimed but never finished within {FETCH_JOB_STALE_SECONDS}s, treated as failed"
        finish_fetch_job(competitor_id, error=message)
        job["status"], job["error"], job["result"] = "error", message, None
    return job


def _is_stale(started_at, timeout_seconds):
    """True if started_at is more than timeout_seconds in the past. Shared by
    fetch_jobs and generate_jobs' staleness checks so there's one definition of
    "how old is too old", not two that could drift."""
    if started_at is None:
        return False
    import datetime as _dt
    now = _dt.datetime.now(started_at.tzinfo) if started_at.tzinfo else _dt.datetime.utcnow()
    return (now - started_at).total_seconds() > timeout_seconds
