"""End-to-end pipeline: scrape -> dedupe -> image -> blueprint -> copy -> Slack.

One scheduled run across the watchlist. Each ad is failure-isolated: one bad
ad or failed stage is skipped cleanly without stopping the run.
"""
import os
import logging
from dataclasses import dataclass
from src import dedupe, scrape, assets, deconstruct, generate_copy, generate_image_prompt, generate_image_prompt_writer, slack_review, compliance, output_critic, content_safety, reference_format
from src.retry import with_retry

FORCE_REPROCESS = os.getenv("FORCE_REPROCESS") == "1"

# Product scope guard (2026-08-06, item 4): Magic Body Oil (id 1) is the only product
# live for generation today - Besque Shower Oil (id 2) is a DIFFERENT product with its
# own cutout and its own visual_description, has no image_keys/visual_description
# configured yet, and must never be selectable as a reference for a Magic Body Oil ad.
# Scoped by product_id, never a name/category match - pool.html's product picker listed
# every product with nothing stopping an operator from selecting the wrong one, and
# nothing downstream validated it either. Expansion is a config change (add the id to
# this set) once a product is genuinely ready, never a code change or a name check.
ENABLED_PRODUCT_IDS_FOR_GENERATION = {1}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pipeline")
log.info("FORCE_REPROCESS=%s", FORCE_REPROCESS)


def effective_image_keys(product):
    """The reference image keys to use for a product: the fixed multi-photo set if any
    is configured, else a single-item list from the legacy image_key (frozen, pre-multi-
    image products), else empty. Isolates the back-compat fallback to one place rather
    than scattering `image_keys or [image_key]` checks across every caller."""
    if not product:
        return []
    keys = product.get("image_keys") or []
    if keys:
        return keys
    return [product["image_key"]] if product.get("image_key") else []


def _stable_index(key, modulus):
    """A stable index for picking the SAME item for the SAME key, forever - unlike
    builtin `hash()` on a string, which is randomised per-process (PYTHONHASHSEED) and
    would pick a DIFFERENT review for the same ad on every restart."""
    import hashlib
    digest = hashlib.sha256(str(key).encode("utf-8")).hexdigest()
    return int(digest, 16) % modulus


# Keyword heuristic, not semantic understanding - same limitation this codebase's other
# regex/keyword compliance checks (compliance.py) already accept. Found live 2026-08-06:
# a real, genuine 5-star review ("Well I do love it but I have not received it yet and
# it's been weeks since I ordered it") passed the rating+length filter cleanly - star
# rating alone does NOT mean the TEXT itself reads as a positive testimonial; shipping/
# service complaints often ride alongside a high rating for the product itself. A real
# quote that reads as a complaint when presented as a testimonial is a different failure
# mode from fabrication, but just as unusable.
_TESTIMONIAL_COMPLAINT_SIGNALS = (
    "not received", "never received", "haven't received", "have not received",
    "never arrived", "still waiting", "since i ordered", "refund", "disappointed",
    "broken", "damaged", "wrong item", "cancel", "customer service", "did not work",
    "didn't work", "doesn't work", "does not work", "waste of money", "return it",
    "complain",
)


def _reads_like_a_complaint(review_text):
    lowered = (review_text or "").lower()
    return any(signal in lowered for signal in _TESTIMONIAL_COMPLAINT_SIGNALS)


def _select_angle_verbatim(angle_slug, ad_id, max_chars):
    """The angle-matched half of select_testimonial_review (2026-08-19, fabricated-
    named-testimonials fix - see CLAUDE.md). Returns {"quote", "customer_name", "age"}
    or None - None means "no real verbatim curated for this angle short enough to use",
    which the caller must treat as drop, never a cue to fall back to product_reviews
    (angle_language.best_verbatims is authoritative once an angle is set - see this
    module's own docstring for why product_reviews is never mixed back in here).

    age is read from the table but deliberately never returned to a caller that
    renders anything - see select_testimonial_review's own return, which drops it -
    preserving the standing "no age shown on the image" team decision (CLAUDE.md
    2026-08-04) even though the table now stores it for completeness.

    Deterministic by ad_id, same _stable_index discipline as the product_reviews
    path below, so a regenerate never silently swaps which real quote is shown."""
    language = dedupe.get_angle_language(angle_slug)
    if not language:
        return None
    candidates = [v for v in (language.get("best_verbatims") or [])
                  if len((v or {}).get("quote") or "") <= max_chars]
    if not candidates:
        return None
    candidates_sorted = sorted(candidates, key=lambda v: v["quote"])
    return candidates_sorted[_stable_index(ad_id, len(candidates_sorted))]


def select_testimonial_review(blueprint, product, ad_id, max_chars=140, angle_slug=None):
    """Pick a REAL, approved customer review to substitute into a testimonial-purposed
    text object - real review or nothing, never fabricated (2026-08-06: Gemini was
    inventing customer quotes wholesale for a testimonial-shaped zone it had nothing
    real to fill, styled exactly like a genuine customer quote with a star rating - see
    CLAUDE.md). Returns None (never a placeholder) when: the blueprint has no
    text_purpose=="testimonial" object at all (skips the DB read entirely), no
    product/product_id is given, or no review both short enough to read as a single
    in-image line AND free of complaint-shaped language (see _reads_like_a_complaint)
    exists for this product. Callers must treat None as "remove the object" - see
    generate_image_prompt._objects_clause/_substitute_object_line.

    REWIRED 2026-08-17: structural_zones/social_proof_kind no longer exist
    (schema/blueprint.schema.json - blueprint.objects replaces them). The old
    single_quote/aggregate_bar distinction has no equivalent in the objects model - any
    text_purpose=="testimonial" object is treated as wanting a real quote, same as a
    single_quote zone used to; an aggregate review-count/average is still not built
    anywhere downstream (held pending Harry, see CLAUDE.md), so this can never
    accidentally start rendering one.

    ANGLE-MATCHED VERBATIMS NOW AUTHORITATIVE WHEN AN ANGLE IS SET (2026-08-19,
    fabricated-named-testimonials fix - five real drafts rendered invented named
    quotes, e.g. "Cynthia Renee W.", none of them real customers, despite
    docs/angle_language.md's own hand-curated "best verbatims" existing per angle and
    never having been loaded - see CLAUDE.md). When angle_slug is given, this function
    draws EXCLUSIVELY from angle_language.best_verbatims (via _select_angle_verbatim)
    - NEVER falls back to product_reviews below, even if a product_reviews candidate
    would have existed: a real quote curated for a DIFFERENT angle is exactly the kind
    of mismatch this fix exists to prevent, not a fallback worth preferring over a
    clean drop. No matching verbatim for that angle -> None -> the testimonial object
    drops, never invents one.

    angle_slug=None (no angle selected for this run - a real, common case: every
    scheduled Cloud Run Job sweep runs angle-less by default, and pool.html's operator
    UI has an explicit "No angle" option) leaves this function's ORIGINAL
    product_reviews-based behaviour completely unchanged - there is no angle to match
    against, and this isn't the failure mode the fix above addresses.

    Deterministic by ad_id, not random: the SAME ad picks the SAME review on every
    regenerate (reproducible - a real review disappearing and reappearing on repeat runs
    would itself look like a bug), while different ads spread across the pool.

    Every outcome is logged by name (2026-08-19) - which verbatim/review was selected
    (angle-matched or product_reviews-based), or that none was found and the
    testimonial object(s) will drop - so a run's own log shows whether the angle table
    was actually consulted, never silently.

    max_chars keeps the pick short enough to read as a single in-image line, the same
    "short line" constraint image_subtext already applies - not a length pulled from
    nowhere. Reuse-cooldown matching is still not built (product_reviews has no
    per-review "last used" tracking, and best_verbatims has no independent
    "times used" state either) - the team's stated want for a future version of this
    feature, not buildable with today's schema; deferred rather than guessed at, same
    as before this fix (see CLAUDE.md 2026-08-06)."""
    objects = (blueprint or {}).get("objects") or []
    wants_quote = any(
        (obj or {}).get("kind") == "text" and (obj or {}).get("text_purpose") == "testimonial"
        for obj in objects
    )
    if not wants_quote:
        return None

    if angle_slug:
        verbatim = _select_angle_verbatim(angle_slug, ad_id, max_chars)
        if verbatim is None:
            log.info(
                "Ad %s: no angle-matched testimonial verbatim found for angle=%r "
                "(short enough, %s chars) - testimonial object(s) will drop",
                ad_id, angle_slug, max_chars,
            )
            return None
        log.info(
            "Ad %s: testimonial verbatim selected for angle=%r: customer=%r",
            ad_id, angle_slug, verbatim.get("customer_name") or "",
        )
        return {"quote": verbatim["quote"], "attribution": verbatim.get("customer_name") or ""}

    product_id = (product or {}).get("id")
    if product_id is None:
        return None
    reviews = dedupe.get_reviews_for_product(product_id)
    candidates = [
        r for r in reviews
        if (r.get("char_length") or 10 ** 9) <= max_chars
        and (r.get("review_text") or "").strip()
        and not _reads_like_a_complaint(r.get("review_text"))
    ]
    if not candidates:
        log.info("Ad %s: no product_reviews testimonial candidate found (no angle set) "
                  "- testimonial object(s) will drop", ad_id)
        return None
    candidates.sort(key=lambda r: r["id"])
    chosen = candidates[_stable_index(ad_id, len(candidates))]
    log.info("Ad %s: testimonial review selected from product_reviews (no angle set): "
              "customer=%r", ad_id, chosen.get("nickname") or "")
    return {"quote": chosen["review_text"].strip(), "attribution": chosen.get("nickname") or ""}


def fetch_reference_images(product):
    """Fetch bytes for every effective reference image key of `product`. Returns
    (images, warning) where warning is None, or a (kind, detail) tuple describing
    either "no images configured" or "N of M configured images failed to fetch" -
    the caller decides what to do with it (log + persist), so this stays a pure
    fetch. Never silently returns an empty list without saying why."""
    keys = effective_image_keys(product)
    if not keys:
        return [], ("no_reference_photo",
                     f"Product '{product.get('name', '?')}' (id={product.get('id', '?')}) "
                     f"has no reference images configured - generating without a fixed reference.")
    images = []
    failed = []
    for key in keys:
        try:
            from google.cloud import storage as _storage
            blob = _storage.Client().bucket(assets.asset_bucket_name()).blob(key)
            if blob.exists():
                images.append(blob.download_as_bytes())
            else:
                failed.append(f"{key}: not found in bucket")
        except Exception as e:
            failed.append(f"{key}: {e}")
    if failed:
        return images, ("reference_photo_fetch_failed",
                         f"Product '{product.get('name', '?')}' (id={product.get('id', '?')}): "
                         f"{len(failed)} of {len(keys)} configured reference image(s) failed to fetch - "
                         + "; ".join(failed))
    return images, None


def fetch_pool(competitor_id, cap=50, start_date_min=None, start_date_max=None, active_status="active"):
    """Fetch a pool of candidate ads for one competitor and store them, unprocessed,
    in scraped_ads. Fetch-and-store ONLY - does not call deconstruct, generate_image,
    or touch seen_ads/artifacts in any way. This populates the pool that run_once's
    dedup gates sit downstream of; it is not a replacement for either gate.

    Runs the exact same Apify scrape and image-only/page-match filter run_once uses
    (scrape.scrape_ads_with_raw shares scrape.py's _scrape_raw with scrape_ads, so
    the two filters can never drift apart), then upserts every survivor via
    dedupe.upsert_scraped_ad - a direct upsert on scraped_ads' own unique index, not
    a read-modify-write pass-through like update_competitor.

    start_date_min/start_date_max (Chunk 6.2): an actual date WINDOW passed straight
    to the actor (see scrape.py's own docstring) - better than a sort parameter (the
    actor doesn't have one) since it lets the pool request exactly the range that
    matters instead of paging through relevance-ordered results and truncating.
    Both None (the default) omits them entirely - today's behaviour, unchanged.

    active_status (Chunk 6.2): "active" (the default) matches today's behaviour,
    but that default is the reason a page with ~1,200 ads returned zero live - all
    of them paused. "inactive" or "all" surfaces those. mediaType handling is NOT
    touched here or in scrape.py - client-side image filtering stays as the only
    real gate, since the actor's own mediaType enum has no equivalent to Meta's
    image_and_meme and doesn't reliably honour what's passed anyway.

    Returns {"fetched": n_raw, "stored": n_stored, "skipped": {reason: n, ...}}.
    n_raw is every record Apify's dataset returned before the image/page filter;
    skipped breaks down by scrape.py's REJECT_* reason (not_image/wrong_page/
    no_image_url), "duplicate" (same ad_id twice in ONE pull), and "already_present"
    (ad_id already stored for this competitor from a prior fetch - not upserted again).

    Duplicate-run guard (2026-08-12, fetch-hang fix): before starting a fresh Apify
    actor run, checks whether this competitor's fetch_jobs row already carries a
    run_id from a PRIOR attempt whose own process died mid-poll (never reached
    finish_fetch_job) - dedupe.try_start_fetch_job's stale-reclaim path preserves
    run_id/dataset_id across a reclaim precisely so this check has something to
    read. If scrape.get_run_status confirms that run is still genuinely active on
    Apify, adopts and watches it instead of starting a second, real, billed run for
    the same competitor. Either way, scrape.scrape_ads_with_raw's on_run_started
    callback persists whatever run this attempt ends up using the moment it's known
    - not after the (possibly long) watch loop - so a later retry can make this same
    check even if THIS process also dies mid-poll."""
    competitor = next((c for c in dedupe.get_competitors() if c["id"] == competitor_id), None)
    if not competitor:
        raise ValueError(f"competitor {competitor_id} not found")
    dedupe.init_scraped_ads()

    existing_job = dedupe.get_fetch_job(competitor_id)
    existing_run_id = None
    if existing_job and existing_job.get("status") == "running" and existing_job.get("run_id"):
        run_status = scrape.get_run_status(existing_job["run_id"])
        if run_status and run_status["status"] in scrape.ACTIVE_RUN_STATUSES:
            log.info("fetch_pool: adopting still-active Apify run %s for competitor %s instead of "
                      "starting a duplicate", existing_job["run_id"], competitor_id)
            existing_run_id = existing_job["run_id"]

    def _on_run_started(run_id, dataset_id):
        dedupe.record_fetch_run(competitor_id, run_id, dataset_id)

    triples = scrape.scrape_ads_with_raw(competitor["name"], max_results=cap, page_id=competitor.get("page_id"),
                                          start_date_min=start_date_min, start_date_max=start_date_max,
                                          active_status=active_status,
                                          on_run_started=_on_run_started, existing_run_id=existing_run_id)
    existing_ad_ids = dedupe.get_scraped_ad_ids(competitor_id)
    skipped = {"not_image": 0, "wrong_page": 0, "no_image_url": 0, "duplicate": 0, "already_present": 0}
    seen_ad_ids = set()
    stored = 0
    for raw, mapped, reason in triples:
        if mapped is None:
            skipped[reason] = skipped.get(reason, 0) + 1
            continue
        if mapped["ad_id"] in seen_ad_ids:
            skipped["duplicate"] += 1
            continue
        seen_ad_ids.add(mapped["ad_id"])
        dedupe.upsert_scraped_ad(ad_id=mapped["ad_id"], competitor_id=competitor_id,
                                  media_type=mapped.get("media_type", ""),
                                  image_url=mapped["image_url"], raw_meta=raw)
        if mapped["ad_id"] in existing_ad_ids:
            skipped["already_present"] += 1
        else:
            stored += 1
    return {"fetched": len(triples), "stored": stored, "skipped": skipped}


def _reference_has_nothing_to_clone(blueprint):
    """INFORMATIONAL ONLY (2026-08-07, reference usability gate reversal - this used to
    be a skip gate, and that was wrong; see process_ad's own note where this is read
    now). True when a reference blueprint shows no product (layout_detail.product_count
    == 0), no headline or other text-bearing zone, and no offer - reused for the pool
    badge and for logging, never again to skip an ad.

    A reference like this is STILL a usable scene: with include_product/text_in_image
    on, the correct output is that scene reproduced with the Besque product and copy
    ADDED into it, never substituted (there is nothing to substitute into) and never
    skipped. See generate_image_prompt.reference_has_product/reference_has_text_zone,
    which drive the actual per-element ADD-vs-SUBSTITUTE wording in generation - this
    function is a coarser, combined signal for the badge/log line only, built from those
    same two predicates plus the offer check, so it can never drift from what generation
    actually does.

    This is "there is nothing here to clone", never a taste judgment ("this ad is bad") -
    it reads only structural fields already in deconstruct.py's schema (product_count,
    headline_verbatim, structural_zones, offer), never anything about quality, tone, or
    creative merit. Deliberately reads an EXISTING blueprint only - never triggers a new
    model call itself; the caller decides where that blueprint comes from (generate_
    from_selection reads it from an already-generated artifact, since a never-processed
    ad has no blueprint yet to check)."""
    blueprint = blueprint or {}
    if generate_image_prompt.reference_has_product(blueprint):
        return False
    if generate_image_prompt.reference_has_text_zone(blueprint):
        return False
    if blueprint.get("offer"):
        return False
    return True


@dataclass(frozen=True)
class BatchAdConfig:
    """The run-strip controls that are singular per generate_from_selection/run_once call
    today (see the 2026-08-10 CLAUDE.md note: every creative control on this function's own
    signature is batch-wide, not per-ad) - snapshotted into one immutable value per ad so a
    future per-ad override can be introduced by constructing a different instance per ad,
    without hunting down every place a run-strip value is read out of the enclosing
    function's own arguments.

    frozen=True raises on reassigning a field (config.realism = "x") but does NOT deep-freeze
    - a field holding a mutable value (list/dict) could still be mutated in place. None of
    these eight fields are ever a list/dict today (angle_id/realism/body_area/offer_text/
    operator_instruction are str-or-None, text_in_image/include_product/edit_mode are
    bool-or-None), so this is a non-issue for THIS dataclass as defined - noted for whoever
    adds a field later.

    Deliberately excludes product/reference_images/messaging_angle - those are mutable,
    resolved once per whole batch (not per ad), and continue to be passed as separate
    arguments exactly as before this dataclass existed."""
    angle_id: int = None
    realism: str = None
    body_area: str = None
    text_in_image: bool = False
    include_product: bool = None
    edit_mode: bool = None
    offer_text: str = None
    operator_instruction: str = None


def generate_from_selection(ad_ids, angle_id=None, body_area=None, offer_text=None,
                             instruction=None, product_id=None, should_stop=None,
                             regenerate=False, on_ad_done=None,
                             text_in_image=False, include_product=None, edit_mode=None,
                             check_output=False, retheme_colours=None, realism=None,
                             per_ad_overrides=None, clone_mode=False):
    """Generate drafts for an EXPLICIT list of already-fetched ads, rather than
    driving generation off scrape order. No Apify call - fetch_pool already
    stored the pool; this only reads scraped_ads and calls process_ad per
    selected ad, reusing it rather than duplicating its body (run_once's own
    inner loop and this one both just call process_ad).

    Deconstruct (a paid vision call) runs HERE, per selected ad, AFTER the
    operator's selection - never over the whole pool. That separation is the
    entire point of splitting fetch (Chunk 2) from generate (this chunk): the
    pool can be browsed for free, and only the ads actually picked cost anything.

    ad_ids refer to scraped_ads.ad_id (the Apify ad_archive_id) - matching what
    the Chunk 3 grid surfaces per card and what Chunk 5 passes through, NOT
    scraped_ads.id. Each row's `ad` dict is reconstructed via scrape._map_ad on
    its stored raw_meta - the exact same mapping scrape_ads itself produces, so
    process_ad behaves identically regardless of which path fed it. An ad_id with
    no matching scraped_ads row is recorded as "failed" and skipped, not raised -
    one bad id in a multi-ad selection must not abort the rest.

    text_in_image/include_product/edit_mode/check_output/retheme_colours (Chunk
    6.1, Item 1 - urgent live fix) are threaded straight through to process_ad,
    same names and same defaults as process_ad/run_once already use - a live run
    produced images with no baked-in copy because this function silently fell
    back to process_ad's text_in_image=False default with no way for the
    operator to override it (pool.html had no toggle for it at all).

    realism (2026-08-06, item 2): every one of the four STYLE_GUIDANCE registers exists
    and is reachable in the generator - the actual gap was that pool.html had no control
    for it at all, so every draft generated through this path ran with realism=None.
    With an angle selected, that falls back to the reference's own detected
    production_style (the writer's effective_realism logic), never to silence - but an
    operator could never override it. Forwarded straight through to process_ad, same
    name, same default.

    per_ad_overrides (batch-quality fix): optional {ad_id: {field: value}} map, ad_id
    keyed exactly like ad_ids/on_ad_done. Each value dict may hold any SUBSET of realism/
    body_area/include_product/edit_mode/text_in_image - the five fields that were
    previously identical for every ad in a selection because they only ever came from one
    shared run strip (see BatchAdConfig's own docstring and the 2026-08-10 CLAUDE.md note
    on batch degradation). A key present in an ad's override dict wins over the run-strip
    value for that ad only; a key genuinely ABSENT falls back to the run-strip value
    exactly as before this parameter existed - same None-means-not-supplied convention
    used everywhere else in this module, so an override explicitly set to None (rather
    than omitted) DOES win and overrides to None, same as any other field. offer_text and
    operator_instruction are deliberately NOT overridable here - the team confirmed those
    stay run-level free text, not per-ad. An empty dict, an ad_id with no entry, or
    per_ad_overrides=None (the default) all behave identically to today - nothing here
    changes behaviour for a caller that doesn't pass this.

    clone_mode (2026-08-11): OFF by default - nothing changes for a caller that doesn't
    pass this. When True, realism/include_product/text_in_image/body_area/offer_text are
    derived per ad from THAT ad's own blueprint instead of coming from one run strip
    shared by every ad in the selection (see process_ad's own clone_mode docstring for
    the derivation and precedence - run strip is the fallback for whatever the blueprint
    can't answer, never an override of it; a per-ad override in per_ad_overrides always
    wins over the blueprint-derived value). angle_id is NOT derived - it stays run-level,
    same as always; a blueprint has no angle concept to read one from.

    used_headlines (2026-08-11, same-run copy convergence fix): one plain list, created
    HERE, the same object passed into every ad's process_ad call this run - process_ad
    appends to it once each ad's own copy passes compliance, so ad N's generate_copy_live
    call sees every headline/image_subtext already produced by ads 1..N-1 in THIS run
    (see process_ad's and generate_copy._used_copy_clause's own docstrings for the full
    reasoning and its limits). A single-ad selection never accumulates anything to see,
    so its prompt is byte-identical to before this existed.

    should_stop, if given, is checked BETWEEN ads (same as run_once) AND is
    forwarded into process_ad, which checks it once more immediately before the
    paid Gemini call (see process_ad's own should_stop docstring) - the same
    responsiveness guarantee run_once provides on its own path, verified
    reachable here by test.

    on_ad_done(ad_id, result), if given, is called after EACH ad's result is known
    (success, failure, or already-generated skip) - purely additive, so callers
    that don't need per-ad progress can ignore it. dashboard.py's POST
    /api/generate background thread uses this to write live progress into the
    generate_jobs table as each ad finishes, rather than the caller only finding
    out anything once the entire selection is done - the whole point of Chunk 5's
    "surface per-ad progress" requirement. Exceptions from this callback are
    swallowed (logged, not raised) - a progress-reporting bug must never abort an
    otherwise-successful generation.

    Reference usability (Task H, 2026-08-07): see process_ad's own docstring -
    _reference_has_nothing_to_clone runs INSIDE process_ad, right after deconstruct
    returns and before generation, using the fresh blueprint that call just produced. Not
    this function's concern - it's mentioned here only because an ad skipped that way
    still comes back through this loop as an ordinary "skipped" result, same as any other
    process_ad outcome, with nothing special for this function to do about it.

    product_id is checked against ENABLED_PRODUCT_IDS_FOR_GENERATION (item 4, 2026-08-06)
    before anything else runs - an out-of-scope product refuses the WHOLE selection with
    every ad marked "failed" and a "product_scope_refused" pipeline_warning naming why,
    never a silent per-ad skip. Found unguarded: pool.html's product picker lists every
    product with nothing stopping an operator selecting Besque Shower Oil (a different
    product, its own cutout/visual_description, not yet configured) for what's meant to
    be a Magic Body Oil ad.

    Reference photo total-fetch-failure guard: same refuse-before-any-paid-call shape as
    the product scope guard above, immediately after it. fetch_reference_images returning
    a "reference_photo_fetch_failed" warning with ZERO images actually fetched - every
    configured reference photo failed, not just some - means include_product (when on,
    the default) has nothing to substitute the Besque bottle with; Gemini falls back to
    building it from visual_description text alone, which reliably gets pump direction
    and proportions wrong, and that's a full paid deconstruct+copy+image call spent on a
    draft that was never usable. Refused the same way as the product scope guard: every
    ad in the selection marked "failed", a pipeline_warning recorded, nothing generated.
    Named as the likely cause because the observed failure mode (every configured key
    fails at once, across otherwise-healthy keys) matches expired ADC far more often than
    a genuinely missing/renamed file - see CLAUDE.md's own operational note on this.
    include_product=False (explicitly off for the whole batch) skips this guard entirely
    - no bottle is being composited in, so a missing reference is irrelevant. A PARTIAL
    failure (some configured images fetched, some didn't) is unchanged - still proceeds
    with the existing reference_photo_fetch_failed warning, never refused, since there's
    still at least one real photo for Gemini to work from.

    Explicit selection deliberately overrides the seen_ads skip
    (process_ad(explicit_selection=True)) - the operator picked this ad on
    purpose, so "already seen" must never silently no-op it. mark_seen still
    runs at the end exactly as normal, so a LATER non-explicit run (run_once)
    still treats it as seen.

    regenerate (Chunk 5, Item 7 fix - was a reported, unresolved conflict in
    Chunk 4, now fixed): False (the default) means an ad that already has an
    artifact for this angle_id is skipped BEFORE any paid call and reported as
    "already_generated" - not "processed", not silently discarded after being
    paid for. True is the operator's explicit ask (the grid marks an
    already-generated card before they click, per Chunk 5 Item 3 - selecting it
    anyway IS the ask) - process_ad then versions the outgoing draft (reusing
    edit_image's own versioning scheme) before overwriting it, and passes
    regenerate=True through to save_artifact's now-explicit per-call parameter
    (Item 7b) instead of requiring FORCE_REPROCESS=1 process-wide. This applies
    to the WHOLE selection in one call - no per-ad_id override in this chunk.

    Each selected row's scraped_ads.status moves off 'pool' as it progresses:
    'generating' immediately before process_ad runs, then the ad's own result
    string ('processed'/'skipped'/'failed'/'already_generated') once it returns -
    so the grid can show what's already been generated from without a separate
    join.

    Returns {"processed": n, "skipped": n, "failed": n, "already_generated": n,
    "by_ad": {ad_id: result}}."""
    from src.config_check import validate_config
    validate_config()
    dedupe.init_db()
    dedupe.init_artifacts()
    dedupe.init_scraped_ads()
    dedupe.init_angles()
    dedupe.init_angle_language()
    dedupe.init_products()

    def _report(ad_id, result):
        if on_ad_done is None:
            return
        try:
            on_ad_done(ad_id, result)
        except Exception as e:
            log.warning("generate_from_selection: on_ad_done callback raised for %s (%s: %s), ignored",
                        ad_id, type(e).__name__, e)

    product = dedupe.get_product(product_id) if product_id else None

    # Product scope guard (item 4): refused BEFORE any paid call, for the WHOLE
    # selection - every ad in a batch shares the same product_id, so one check here
    # covers all of them rather than each ad failing individually with no visible
    # reason. Ahead of fetch_reference_images deliberately: an out-of-scope product
    # must not even trigger a GCS reference-photo lookup. A pipeline_warning is the
    # reason a human actually sees (dashboard.py doesn't read this function's return
    # value for anything but counts - see the pipeline_warnings note elsewhere in
    # this file), never a silent skip.
    if product_id is not None and product_id not in ENABLED_PRODUCT_IDS_FOR_GENERATION:
        reason = (
            f"product_id={product_id} ({product.get('name') if product else 'unknown product'}) "
            f"is not enabled for generation - only {sorted(ENABLED_PRODUCT_IDS_FOR_GENERATION)} "
            f"is live today. Refused before any paid call; nothing was generated."
        )
        log.warning("generate_from_selection refused: %s", reason)
        dedupe.init_pipeline_warnings()
        dedupe.record_warning("product_scope_refused", reason)
        summary = {"processed": 0, "skipped": 0, "failed": len(ad_ids), "already_generated": 0,
                   "by_ad": {}, "error": reason}
        for ad_id in ad_ids:
            summary["by_ad"][ad_id] = "failed"
            _report(ad_id, "failed")
        return summary

    messaging_angle = dedupe.get_angle(angle_id) if angle_id else None
    reference_images = []
    if product:
        reference_images, reference_warning = fetch_reference_images(product)
        if reference_warning:
            kind, detail = reference_warning
            log.warning("%s: %s", kind, detail)
            dedupe.init_pipeline_warnings()
            dedupe.record_warning(kind, detail)
            # Total fetch failure guard: refused BEFORE any paid call, same shape as the
            # product scope guard above - include_product is on (None also normalizes to
            # True, same convention process_ad itself uses) and EVERY configured reference
            # image failed to fetch, not just some, so there is nothing for Gemini to
            # composite the Besque bottle from. Partial failure (some images fetched) is
            # deliberately excluded - reference_images is non-empty there, so this branch
            # never fires and the existing warning-only behaviour continues unchanged.
            effective_include_product = True if include_product is None else include_product
            if (effective_include_product and kind == "reference_photo_fetch_failed"
                    and not reference_images):
                reason = (
                    f"{detail} Likely cause: expired Google Cloud credentials (ADC), not a "
                    f"missing file - re-auth and restart, then retry. Refused before any "
                    f"paid call; nothing was generated."
                )
                log.warning("generate_from_selection refused: %s", reason)
                dedupe.record_warning("reference_photo_fetch_refused", reason)
                summary = {"processed": 0, "skipped": 0, "failed": len(ad_ids), "already_generated": 0,
                           "by_ad": {}, "error": reason}
                for ad_id in ad_ids:
                    summary["by_ad"][ad_id] = "failed"
                    _report(ad_id, "failed")
                return summary
    _stop = should_stop or (lambda: False)

    rows_by_ad_id = dedupe.get_scraped_ads_by_ad_ids(ad_ids)
    summary = {"processed": 0, "skipped": 0, "failed": 0, "already_generated": 0, "by_ad": {}}
    # Run-scoped, not per-ad: ONE list, created here, the SAME object handed to every
    # process_ad call below - process_ad appends to it in place once an ad's own copy
    # passes compliance (see process_ad's own used_headlines docstring). Never reset
    # mid-loop, never touched by BatchAdConfig (that's frozen; this is deliberately
    # mutable and shared).
    used_headlines = []
    for ad_id in ad_ids:
        if _stop():
            log.info("Stop requested, halting selection run.")
            break
        row = rows_by_ad_id.get(ad_id)
        if row is None:
            log.warning("Selected ad %s not found in scraped_ads, marking failed.", ad_id)
            summary["failed"] += 1
            summary["by_ad"][ad_id] = "failed"
            _report(ad_id, "failed")
            continue
        ad_overrides = (per_ad_overrides or {}).get(ad_id) or {}
        cfg = BatchAdConfig(
            angle_id=angle_id,
            realism=ad_overrides.get("realism", realism),
            body_area=ad_overrides.get("body_area", body_area),
            text_in_image=ad_overrides.get("text_in_image", text_in_image),
            include_product=ad_overrides.get("include_product", include_product),
            edit_mode=ad_overrides.get("edit_mode", edit_mode),
            offer_text=offer_text,
            operator_instruction=instruction,
        )
        ad = scrape._map_ad(row.get("raw_meta") or {})
        dedupe.update_scraped_ad_status(ad_id, row["competitor_id"], "generating")
        result = process_ad(
            ad, product=product, reference_images=reference_images, messaging_angle=messaging_angle,
            body_area=body_area, offer_text=offer_text, operator_instruction=instruction,
            text_in_image=text_in_image, include_product=include_product, edit_mode=edit_mode,
            check_output=check_output, retheme_colours=retheme_colours, realism=realism,
            should_stop=should_stop, explicit_selection=True, regenerate=regenerate,
            config=cfg, used_headlines=used_headlines,
            clone_mode=clone_mode, ad_overrides=ad_overrides,
        )
        dedupe.update_scraped_ad_status(ad_id, row["competitor_id"], result)
        summary[result] += 1
        summary["by_ad"][ad_id] = result
        _report(ad_id, result)
    log.info("generate_from_selection complete: %s", summary)
    return summary


def resolve_regenerate_input(live_value, stored_value, default_value):
    """Precedence for one field on a regenerate call: live operator input for THIS call >
    stored artifact value from the prior generation > hardcoded default. Returns
    (resolved_value, source) where source is "live"/"stored"/"default" - the caller logs
    it by name whenever source != "live" (Task F, point 1, 2026-08-07).

    live_value is None exactly when the caller genuinely did not supply this field for
    this call (see process_ad's live_include_product etc.) - never conflated with an
    explicit False/empty-string, which is why dashboard.py had to stop pre-applying its
    own default before pipeline ever saw the call."""
    if live_value is not None:
        return live_value, "live"
    if stored_value is not None:
        return stored_value, "stored"
    return default_value, "default"


def _regenerate_existing_draft(ad, angle_id, angle_slug, delta_instruction, should_stop,
                                live_include_product=None, live_retheme_colours=None,
                                live_realism=None, live_body_area=None, live_offer_text=None,
                                check_output=False, clone_mode=False):
    """Apply delta_instruction as a targeted change to a prompt REBUILT from CURRENT code
    (generate_image_prompt.build_image_prompt) and the artifact's own stored inputs - never
    the artifact's frozen historical image_prompt text.

    check_output (2026-08-10): CHECK-ONLY, deliberately no corrective retry - the retry
    process_ad's own generation loop gets would mean composing a SECOND delta clause
    (critic feedback) on top of delta_instruction against an already-rendered image via
    regenerate_from_stored_prompt, which has no hook for this at all today (confirmed:
    its only composition is stored_prompt + ONE delta clause). Forcing a second one in
    risks the exact shape that produced artifact 1136 - a prompt that simultaneously
    demands and forbids the same element. Regenerate is already operator-driven: a
    failed-review result here is the operator's own cue to regenerate again with a
    better instruction, not something the pipeline should try to self-correct.

    review_status/critic_findings are ALWAYS rewritten via update_artifact_findings
    after save_artifact below, never conditionally skipped - save_artifact's own
    regenerate=True path DELETEs and re-INSERTs the row, and critic_findings/
    review_status are NOT in that INSERT's column list, so they'd silently reset to
    their column defaults ('[]'/'ok') regardless of what this function does UNLESS the
    prior value is explicitly carried forward and rewritten afterward. When
    check_output=False, or the critic block itself fails, the EXISTING stored
    findings/review_status (from `existing`, fetched via dedupe.get_artifact above) are
    what get carried forward - an unchecked regenerate must never look like it was
    freshly verified clean.

    2026-08-06, the regenerate-freezes-the-prompt-forever fix: before this, every
    regenerate replayed the artifact's stored image_prompt verbatim and just appended a
    delta to it - meaning ANY rule, guardrail, or compliance change made after a draft's
    FIRST generation could never reach that ad through regenerate again, no matter how
    many times an operator clicked it. Discovered live: the Grüns GLP-1 illustrated-mode
    fix silently never took effect on an ad that had already been regenerated once before
    the fix landed, even after a full process restart - because regenerate never re-runs
    build_image_prompt at all, by design, until now. Drafts an operator regenerates were
    therefore the LEAST protected against a later fix, not the most.

    2026-08-07, Task F point 1: live_include_product/live_retheme_colours/live_realism/
    live_body_area/live_offer_text are process_ad's own RAW parameters for THIS call
    (None when not supplied) - resolved against the stored artifact value via
    resolve_regenerate_input above, live always winning when present. Before this, none
    of process_ad's live values reached this function at all (only delta_instruction did),
    so a live override was silently discarded in favour of whatever the artifact already
    had stored, or a hardcoded default - exactly what ads 1888339248562394/1194229189228603
    demonstrated (both logged "defaulted True" despite the operator having just switched
    the toggle off).

    Returns None (not "failed") when no artifact exists for this (ad_id, angle_id) yet, OR
    when the artifact exists but its current draft image can no longer be read (2026-08-07,
    Task F point 2 - ad 1888339248562394: the row existed, the image did not) - both signal
    the caller to fall through to a normal first generation instead of failing an ad. That
    fallthrough runs process_ad's OWN body from the top, which already resolved
    edit_mode/include_product/retheme_colours from this SAME call's live values before this
    function was ever entered - so it preserves edit_mode correctly with no extra plumbing
    here. (Closes "regenerate requested but no existing artifact for angle_id=None" too -
    the same root cause: this function assumed history always exists once regenerate is
    requested.)

    clone_mode (2026-08-11): improves the DEFAULT tier ONLY for include_product/realism/
    body_area - live (this call's explicit input) and stored (existing.get(...), the
    artifact's own prior value) both keep winning over it exactly as before, unchanged;
    clone_mode never touches those two tiers, only what the resolver falls through to
    when both are None. This is deliberate, not an oversight: regenerate should still
    reflect what was explicitly asked for last time, and this path is already the more
    fragile one - clone_mode only makes the DEFAULT smarter, never the precedence.
    offer_text is handled separately, as a post-resolve SUPPRESSION (never a default):
    once the normal live > stored > default chain resolves a value, clone_mode forces it
    back to None if this blueprint has no offer-shaped zone at all (see generate_image_
    prompt.reference_has_offer_zone) - an offer with nowhere to render is suppressed
    regardless of which tier supplied it. text_in_image is NOT touched here at all - this
    function has no live_text_in_image parameter to begin with (a separate, pre-existing
    gap: it reads existing.get("text_in_image") unconditionally, below); fixing that is a
    bigger change (converting text_in_image's False-default to the same None-means-not-
    supplied convention include_product/edit_mode already use, across four call sites)
    and is deliberately out of scope for this feature."""
    ad_id = ad.get("ad_id")
    _stop = should_stop or (lambda: False)
    existing = dedupe.get_artifact(ad_id, angle_id=angle_id)
    if existing is None:
        log.info("Ad %s: regenerate requested but no existing artifact for angle_id=%s - "
                 "falling back to a normal first generation instead of failing", ad_id, angle_id)
        return None
    blueprint = existing.get("blueprint") or {}
    if not blueprint:
        log.error("Ad %s: regenerate requested but the existing artifact has no stored blueprint", ad_id)
        dedupe.init_pipeline_warnings()
        dedupe.record_warning(
            "regenerate_missing_blueprint",
            f"Ad {ad_id} ({ad.get('page_name', '?')}): regenerate requested but no blueprint "
            f"was stored on the existing artifact - failed rather than rebuilding one from nothing.",
        )
        return "failed"

    # Legacy re-deconstruct (2026-08-17): a blueprint stored before 6b82f60 has no
    # `objects` key at all - build_image_prompt's _objects_clause silently returns ""
    # for that shape (empty-objects early return, generate_image_prompt.py:889-891),
    # so every regenerate of a pre-refactor ad was reproducing the OLD, pre-objects-
    # model prompt indefinitely, with none of the objects model's per-object
    # substitute/keep/drop mechanics ever reaching the prompt. Fixed the same way a
    # frozen prompt was fixed on 2026-08-06 (see this file's own history) - never
    # replay stale stored state when current code can regenerate it fresh. Mirrors
    # process_ad's own deconstruct_image call (line ~1168) exactly - same argument
    # shape, same source (`ad`, the same dict this function already received) - so a
    # legacy regenerate produces a blueprint indistinguishable from a first-time
    # Generate on this same ad today.
    #
    # "Persist... and use it": the re-deconstructed blueprint is used for the rest of
    # THIS function (testimonial selection, element_provenance, build_image_prompt,
    # below) and reaches storage via this function's own existing, unconditional
    # save_artifact call further down (blueprint=blueprint) - no separate, earlier
    # save_artifact call is added here on purpose: a second regenerate=True call this
    # early would DELETE+INSERT the row twice in one request and re-trip the documented
    # column-reset trap (CLAUDE.md: any column absent from that INSERT's column list
    # silently resets) for no benefit, since the one save at the end of this function
    # already covers it.
    if "objects" not in blueprint:
        log.info("Ad %s: stored blueprint predates the objects model (no 'objects' key) - "
                 "re-deconstructing before regenerating", ad_id)
        image_bytes = assets.download_image_bytes(ad["image_url"])
        blueprint = deconstruct.deconstruct_image(
            image_bytes=image_bytes,
            ad_id=ad_id,
            source_page=ad.get("page_name", ""),
            captured_at=ad.get("start_date", ""),
            destination_url=ad.get("destination_url", ""),
            ad_text=ad.get("text", ""),
            cta=ad.get("cta", ""),
        )

    generated_copy = existing.get("generated_copy") or {}
    text_in_image = bool(existing.get("text_in_image"))
    stored_operator_instruction = existing.get("operator_instruction") or ""

    # Clone mode (2026-08-11): smarter DEFAULTS only, computed from THIS blueprint - never
    # touches the live/stored tiers below. clone_mode=False (the default) reproduces the
    # exact literal defaults (True/None/None) this always used, byte-for-byte.
    clone_include_product_default = (
        generate_image_prompt.reference_has_product(blueprint) if clone_mode else True
    )
    clone_realism_default = (
        ((blueprint.get("production_style") or {}).get("style") or None) if clone_mode else None
    )
    clone_body_area_default = (
        generate_image_prompt_writer.effective_body_area(blueprint, None) if clone_mode else None
    )

    # Task F, point 1 (2026-08-07): live operator input for THIS call > stored artifact
    # value from the ORIGINAL generation > hardcoded default - resolved per field via
    # resolve_regenerate_input, live always winning when the operator actually supplied
    # it this run. Before this fix, every one of these five came ONLY from `existing`
    # (the stored value), with process_ad's own live parameters never even reaching this
    # function - exactly the bug ads 1888339248562394/1194229189228603 demonstrated.
    missing = []
    include_product, ip_source = resolve_regenerate_input(
        live_include_product, existing.get("include_product"), clone_include_product_default)
    if ip_source != "live":
        missing.append(f"include_product -> {ip_source} ({include_product})")
    retheme_colours, rc_source = resolve_regenerate_input(
        live_retheme_colours, existing.get("retheme_colours"), True)
    if rc_source != "live":
        missing.append(f"retheme_colours -> {rc_source} ({retheme_colours})")
    # realism/body_area/offer_text are nullable free-text inputs where None is ALSO the
    # normal, legitimate "operator left this blank" state - so unlike the two booleans
    # and product_id above, a "default" resolution here can't be told apart from a real
    # migration gap. Logged anyway, worded to not overclaim a cause that isn't actually
    # known.
    realism, realism_source = resolve_regenerate_input(live_realism, existing.get("realism"), clone_realism_default)
    if realism_source != "live":
        missing.append(f"realism -> {realism_source} ({realism!r}); auto if still None")
    body_area, body_area_source = resolve_regenerate_input(
        live_body_area, existing.get("body_area"), clone_body_area_default)
    if body_area_source != "live":
        missing.append(f"body_area -> {body_area_source} ({body_area!r})")
    offer_text, offer_text_source = resolve_regenerate_input(live_offer_text, existing.get("offer_text"), None)
    if offer_text_source != "live":
        missing.append(f"offer_text -> {offer_text_source} ({offer_text!r})")
    # Clone mode's offer rule is a SUPPRESSION, never a default - it never invents an
    # offer, it only withholds an already-resolved one (from whichever tier supplied it)
    # when this blueprint has nowhere to render it at all.
    if clone_mode and offer_text and not generate_image_prompt.reference_has_offer_zone(blueprint):
        log.info("Ad %s: clone_mode suppressing offer_text - reference has no offer-shaped zone", ad_id)
        offer_text = None
    product_id = existing.get("product_id")
    product = dedupe.get_product(product_id) if product_id is not None else None
    if product is None:
        fallback_id = next(iter(ENABLED_PRODUCT_IDS_FOR_GENERATION))
        missing.append(f"product_id -> defaulted to the sole enabled product (id={fallback_id})")
        product = dedupe.get_product(fallback_id)
    if missing:
        # info, not warning: "stored" is the expected outcome whenever this regenerate
        # call didn't touch a given field (the normal case) - only a "default" resolution
        # for include_product/retheme_colours/product_id represents a genuine gap (a
        # pre-migration row, or a caller that never passed it), and it's still named
        # explicitly in the message either way, never silently guessed.
        log.info("Ad %s: regenerate input resolution (live > stored > default): %s",
                 ad_id, "; ".join(missing))

    cta_text = generated_copy.get("cta") or None
    panel_copy = generated_copy.get("panel_copy") or None
    brand_palette = dedupe.get_brand_settings().get("palette") if retheme_colours else None
    # Recomputed fresh, not read from the artifact - select_testimonial_review is
    # deterministic by ad_id, so this reproduces the SAME pick a normal generation would
    # make with this same blueprint/product, no separate storage needed (2026-08-06).
    testimonial = select_testimonial_review(blueprint, product, ad_id, angle_slug=angle_slug)
    # element_provenance (2026-08-07, reference usability gate reversal): recomputed
    # fresh here too, same reasoning as testimonial above - regenerate always rebuilds
    # against edit_mode=True (this function's only mode, see build_image_prompt call
    # below), so reference_has_product/reference_has_text_zone are read the same way
    # process_ad reads them for a first generation, never a separate derivation.
    reference_has_product = generate_image_prompt.reference_has_product(blueprint)
    reference_has_text_zone = generate_image_prompt.reference_has_text_zone(blueprint)
    eff_headline, eff_subtext = generate_image_prompt.effective_authorised_text(
        text_in_image, generated_copy.get("headline"), generated_copy.get("image_subtext") or None,
    )
    element_provenance = {
        "product": (
            "none" if not include_product
            else "substituted" if reference_has_product
            else "added"
        ),
        "text": (
            "none" if not eff_headline
            else "substituted" if reference_has_text_zone
            else "added"
        ),
    }

    rebuilt_prompt = generate_image_prompt.build_image_prompt(
        blueprint, product=product, include_product=include_product,
        text_in_image=text_in_image, headline=generated_copy.get("headline"),
        subtext=generated_copy.get("image_subtext") or None, edit_mode=True,
        offer_text=offer_text, operator_instruction=stored_operator_instruction,
        retheme_colours=retheme_colours, brand_palette=brand_palette,
        realism=realism, cta_text=cta_text, panel_copy=panel_copy,
        testimonial=testimonial, clone_mode=clone_mode,
        object_copy=generated_copy.get("object_copy") or None,
    )

    draft_bytes = generate_image_prompt._current_draft_bytes(ad_id, angle_slug)
    if draft_bytes is None:
        # Task F, point 2 (2026-08-07): the artifact ROW existed but its image did not
        # (ad 1888339248562394) - same "no history to regenerate from" shape as the
        # missing-artifact case above, so it gets the same fallback: fall through to a
        # normal first generation rather than failing the ad. process_ad's own body
        # (which runs next) already resolved edit_mode/include_product/retheme_colours
        # from THIS call's live values before ever entering this function, so the
        # fallthrough preserves them correctly with no extra plumbing here - the earlier
        # bug (a fallback silently running edit_mode=False, ad 2577024936146615) was a
        # consequence of this branch returning "failed" outright and never reaching that
        # already-correct fallthrough at all.
        log.info("Ad %s: regenerate requested but no current draft image could be read - "
                 "falling back to a normal first generation instead of failing", ad_id)
        return None
    if _stop():
        log.info("Ad %s: stop requested, skipping before the paid regenerate call", ad_id)
        return "skipped"
    versioned = generate_image_prompt.version_current_draft(
        ad_id, angle_slug, current_prompt=existing.get("image_prompt", ""),
    )
    if versioned:
        log.info("Ad %s: versioned outgoing draft as %s before regenerating", ad_id, versioned)
    new_draft = generate_image_prompt.regenerate_from_stored_prompt(
        draft_bytes, rebuilt_prompt, delta_instruction or "", ad_id, angle_slug=angle_slug,
    )
    if not new_draft:
        log.error("Ad %s: regenerate failed - no draft image produced", ad_id)
        return "failed"
    img_prompt = getattr(generate_image_prompt.regenerate_from_stored_prompt, "last_prompt", "")

    # CHECK-ONLY critic pass (2026-08-10) - see this function's own docstring for why no
    # retry happens here. Mirrors process_ad's own check/drop/confidence sequence exactly.
    findings = None
    if check_output:
        try:
            from pathlib import Path as _Path
            draft_bytes_for_check = _Path(new_draft).read_bytes()
            brand_rules_text = generate_image_prompt.brand_rules(
                include_product=include_product, text_in_image=text_in_image,
                headline=generated_copy.get("headline"), subtext=generated_copy.get("image_subtext") or None,
                edit_mode=True,
            )
            # reference_image_bytes (Item 2, 2026-08-12): always edit_mode=True in this
            # function (see brand_rules() call above), so a real competitor reference
            # exists on disk at the artifact's own stored image_path - read it the same
            # way draft_bytes_for_check is read above, inside the SAME try/except, so a
            # missing/unreadable reference degrades to no-comparison (same as today)
            # rather than failing the whole check.
            reference_path = existing.get("image_path") or ""
            reference_image_bytes = _Path(reference_path).read_bytes() if reference_path else None
            findings = output_critic.check_draft(
                draft_bytes_for_check, brand_rules_text, headline=eff_headline,
                subtext=eff_subtext, offer_text=offer_text, include_product=include_product,
                visual_description=(product or {}).get("visual_description"),
                ingredients=(product or {}).get("ingredients"),
                testimonial=testimonial,
                reference_image_bytes=reference_image_bytes,
            )
        except Exception as e:
            log.warning("Ad %s: output critic block raised on regenerate (%s: %s), "
                        "review_status carried forward unchanged", ad_id, type(e).__name__, e)
        if findings is None:
            dedupe.init_pipeline_warnings()
            dedupe.record_warning(
                "critic_failed",
                f"Ad {ad_id} ({ad.get('page_name', '?')}): output critic check failed or was "
                f"unparseable on regenerate - review_status carried forward unchanged.",
            )
        else:
            findings = output_critic.drop_findings_contradicted_by_authorised(findings, testimonial=testimonial)

    if findings is None:
        # check_output was off, or the critic block failed/errored - no fresh verdict to
        # trust. Carry the EXISTING findings/review_status forward unchanged - see the
        # docstring for why this must be an explicit rewrite, not a skipped call.
        findings = existing.get("critic_findings") or []
        review_status = existing.get("review_status") or "ok"
    else:
        review_status = "failed-review" if output_critic.has_high_confidence(findings) else "ok"

    dedupe.save_artifact(
        ad_id=ad_id, page_name=ad.get("page_name", ""),
        image_path=existing.get("image_path", ""),
        blueprint=blueprint,
        generated_copy=generated_copy,
        draft_image=new_draft,
        image_prompt=img_prompt,
        copy_prompt=existing.get("copy_prompt", ""),
        model_info=existing.get("model_info", ""),
        metadata=existing.get("metadata") or {},
        angle_id=angle_id,
        text_in_image=text_in_image,
        operator_instruction=delta_instruction or "",
        format_flag=existing.get("format_flag", ""),
        product_override_note=existing.get("product_override_note", ""),
        include_product=include_product,
        retheme_colours=retheme_colours,
        realism=realism,
        body_area=body_area,
        offer_text=offer_text,
        product_id=(product or {}).get("id"),
        element_provenance=element_provenance,
        regenerate=True,
    )
    dedupe.update_artifact_findings(ad_id, findings, angle_id=angle_id, review_status=review_status)
    dedupe.mark_seen(ad_id, ad.get("page_name", ""), angle_id)
    return "processed"


def process_ad(ad, product=None, reference_images=None, messaging_angle=None,
                realism=None, text_in_image=False, include_product=None,
                body_area=None, offer_text=None, edit_mode=None, operator_instruction=None,
                check_output=False, retheme_colours=None, ad_index=None, total_ads=None,
                should_stop=None, explicit_selection=False, regenerate=False, product_count=None,
                config=None, used_headlines=None, clone_mode=False, ad_overrides=None):
    """Run one ad through the full pipeline. Returns processed/skipped/failed.

    clone_mode (2026-08-11): when True, realism/include_product/text_in_image/body_area/
    offer_text are RE-DERIVED from THIS ad's own freshly-deconstructed blueprint right
    after deconstruct returns (see the derivation block below, just after the deconstruct
    call) - overwriting whatever config/run-strip value the normalization above already
    set, but ONLY for a field this specific ad's own per_ad_override didn't already pin
    (see ad_overrides below) and ONLY when the blueprint actually answers that field.
    False (the default) skips this entirely - config's own resolved values pass straight
    through unchanged, byte-for-byte today's behaviour. This is a first-generation-only
    derivation; the regenerate path (_regenerate_existing_draft) has its OWN, earlier
    clone_mode hook, described in that function's own docstring, with a DIFFERENT
    precedence (live > stored > blueprint-derived, never overriding stored) because that
    path already has a live/stored/default resolver these overwrites would otherwise
    fight with.

    ad_overrides, if given, is the SAME per-ad override dict generate_from_selection's own
    per_ad_overrides feature already resolved for this ad_id (BatchAdConfig's own
    construction site) - passed here ONLY so clone_mode can tell "the operator explicitly
    set this field for THIS card" apart from "this field is just the run-strip default,"
    which config's own already-merged value can no longer distinguish by the time it
    reaches this function. A field present in ad_overrides is left completely alone by
    clone_mode - an explicit per-ad choice always wins over a blueprint heuristic. None
    (the default) means clone_mode may freely derive every field.

    used_headlines (2026-08-11, same-run copy convergence fix): an optional, CALLER-OWNED
    mutable list of {"headline", "image_subtext"} dicts - the caller (generate_from_
    selection/run_once) creates ONE such list per run (never per ad) and passes the SAME
    list object into every ad's process_ad call. Read here to build the copy prompt's
    "already used this run" clause (see generate_copy._used_copy_clause), then APPENDED
    to in place once this ad's own copy passes compliance - never returned, since
    process_ad's own return contract (processed/skipped/failed) is relied on everywhere
    and mutating a list the caller already holds a reference to needs no new plumbing.
    Safe only because processing is strictly sequential within one run (same reasoning
    CLAUDE.md already documents for .last_prompt) - no two process_ad calls ever run
    concurrently against the same list. None (the default, e.g. a test or the writer
    calling process_ad directly) disables this entirely: no clause is added and nothing
    is appended anywhere.

    Only the most recent USED_HEADLINES_CAP (3) entries are ever READ into the prompt
    (2026-08-11) - the accumulator itself stays unbounded and append-only, this function
    just slices the tail of it. An unbounded list was making copy noticeably worse by the
    5th/6th ad in a run: later ads were avoiding every earlier headline AND subline at
    once, writing around a growing constraint list instead of a stable-sized one.

    include_product/retheme_colours/edit_mode default to None, not a concrete bool
    (Task F, point 1, 2026-08-07) - None means "the caller genuinely did not supply this
    for this call," never conflated with an explicit False. dashboard.py now passes None
    when its own request body omits the key, rather than pre-applying a default before
    pipeline ever sees the call - the ONLY way "operator explicitly set false" can be told
    apart from "operator did not touch it." Normalized to a concrete bool a few lines
    below, before any other use in this function, so every other line keeps working with
    a real bool exactly as before this change. realism/body_area/offer_text were already
    nullable and are unaffected here.
    messaging_angle, if given, is a resolved angle dict (dedupe.get_angle's shape) - it
    changes the dedup identity of this ad to (ad_id, angle_id) instead of ad_id alone, so
    the same ad can produce one draft per angle rather than being skipped as already seen
    the second time around.

    realism/text_in_image/include_product are the operator-set run-strip controls.
    text_in_image is persisted onto the saved artifact (for the dashboard's future
    overlay-suppression logic) AND forwarded to generate_image, which now enforces the
    conditional brand_rules (rule 6's text allow-list, rule 7's productless mode). realism
    is still inert - nothing yet reads it to change the register of what's generated; that
    lands with the Claude prompt-writer pass.

    body_area/offer_text are per-run free-text operator inputs, threaded exactly like
    realism - also inert until the writer pass. body_area is NEVER read from
    messaging_angle["body_area"] here: the team confirmed body area varies every run and
    isn't fixed per angle, so an angle's stored body_area is only ever a UI pre-fill
    suggestion in the dashboard, never an authoritative value used in generation.

    edit_mode, if True, reuses the SAME competitor image bytes already downloaded below for
    the deconstruct call (never a second download) and hands them to generate_image so
    Gemini can reproduce the actual reference photo rather than a text description of it.
    The team confirmed edit-vs-generate usage is about 50/50, so this defaults to False -
    today's generate-only path is unchanged.

    Reference usability (Task H, 2026-08-07; REVERSED same day - see CLAUDE.md): a
    reference with no product and no text zone is still a usable scene, never skipped.
    _reference_has_nothing_to_clone is now read purely for logging/the pool badge - see
    element_provenance below, which is what actually varies per reference: with
    include_product/text_in_image on, the product and/or copy are ADDED into the scene
    (derived from the blueprint's own observed composition - see
    generate_image_prompt._scene_composition_facts) whenever the reference has nothing
    of that kind to substitute into, rather than the ad being skipped or the element
    being suppressed. This must behave identically across every reference shape - no
    ad_id/competitor/page ever gates this decision, only the blueprint's own fields.

    operator_instruction (Step 2) is the run-strip's free-text steering field - forwarded
    to generate_image (which clips it and hands it to both the writer and
    build_image_prompt) AND persisted onto the saved artifact below, so a reviewer can see
    whether a wrong draft is what the operator actually asked for.

    check_output (Prompt 4, Item 1) gates the output critic - a SAFETY control, not a
    quality feature, since every guardrail up to this point is prompt-only and nothing
    inspected what Gemini actually produced before this existed. Defaults to False - this
    is an extra vision call per ad, real cost that multiplies across a sweep, so it's
    opt-in per run.

    Corrective-retry loop (2026-08-05): a HIGH-confidence critic finding on the first
    attempt triggers exactly ONE regeneration, with the specific findings fed back into
    the image prompt as corrections (generate_image_prompt's critic_feedback). This closes
    the gap a real leak exposed - a draft that reproduced a competitor's product name, body
    copy, CTA, and product category verbatim was correctly flagged at HIGH confidence on
    every count and still saved as an ordinary-looking pending draft, because nothing ever
    read critic_findings to decide anything. save_artifact now runs ONCE, after the loop
    resolves (still never before generation, same "can't lose a draft" guarantee) - with
    the retry's draft if it came back clean, or with the still-flagged draft if the retry
    didn't fix it (never discarded, but also never left indistinguishable from a clean
    pending draft: see output_critic.has_high_confidence, the single signal both this loop
    and the card's "Failed Review" state key off - no new column, critic_findings' own
    per-finding confidence is the only signal needed). A critic failure or timeout at any
    point still can never lose a draft: it stops the loop and keeps whatever was already
    generated, unflagged, same as the pre-retry behaviour.

    retheme_colours (Prompt 4, Item 5) only affects edit_mode - defaults to True, since
    the team's own doc calls for re-theming the reference's palette to Besque's on every
    clone unless the angle specifically calls for something else; the operator disables
    it per run for that stated exception, which also protects today's validated
    faithful-clone behaviour.

    product_count (Task F, point 6, 2026-08-07): an explicit operator override for how
    many Besque products to place, forwarded to generate_image_prompt.generate_image ->
    build_image_prompt -> resolve_product_count. None (the default - no per-run control
    for this exists yet) means the reference's own observed
    blueprint.layout_detail.product_count is the baseline instead. See
    resolve_product_count's own docstring for the full precedence and why a resolved
    count above 1 still always renders exactly one bottle today (only one Besque SKU/
    visual_description exists to render).

    ad_index/total_ads are diagnostic-only (silent-hang investigation, 2026-08-04) - purely
    for the entry-point log line below, no effect on behaviour. None/None (the default,
    e.g. when a test or the writer calls process_ad directly) just omits them from that line.

    Silent-override audit (2026-08-05): two places where a derived value can override an
    explicit operator input now both surface it rather than doing so silently. (1) When
    resolve_effective_include_product forces include_product off against an explicit True
    (the reference ad has no product to substitute), a pipeline_warning is recorded AND
    product_override_note is persisted onto the artifact for the card - a human decision
    silently overruled with no feedback anywhere was the actual defect, not just the two
    critic false positives it also caused (see effective_include_product's use below,
    replacing the raw include_product the critic used to be told). (2) A pasted
    operator_instruction longer than generate_image_prompt_writer.MAX_OPERATOR_INSTRUCTION_CHARS
    is silently truncated by clip_operator_instruction - now also recorded as a
    pipeline_warning naming the original length, same defect class, just for free text
    instead of a toggle.

    should_stop (Stop-button responsiveness, 2026-08-05): run_once's own should_stop is
    only checked BETWEEN ads/competitors - a click mid-ad couldn't interrupt an in-flight
    paid Gemini call, observed live as "Stop greyed out, run continued for over a minute."
    Forwarded here and checked once more, immediately before generate_image, so a stop
    request can't be missed for the cost of one full image generation. None (the default,
    e.g. a test or the writer calling process_ad directly) behaves as "never stop," same
    fallback run_once itself already uses for its own should_stop.

    explicit_selection (Chunk 4, generate_from_selection): the operator picked THIS ad
    on purpose from the pool grid, so the seen_ads skip below must not silently no-op it -
    True bypasses that check regardless of FORCE_REPROCESS. mark_seen still runs at the
    end exactly as normal (unchanged), so a LATER non-explicit run (run_once) still treats
    it as seen.

    regenerate: only meaningful when explicit_selection=True (run_once never regenerates).
    False (default) returns "already_generated" if an artifact exists, spending nothing.
    True hands off to _regenerate_existing_draft - REBUILDS the image prompt from current
    code and the artifact's own stored inputs (2026-08-06 - never the artifact's frozen
    historical image_prompt text, see that function's docstring for why), then applies
    operator_instruction as a targeted delta on top. Never a fresh deconstruct/copy run -
    those stay reused from the existing artifact. Falls back to a normal first generation
    (this same function, from the top) when no artifact exists yet, rather than failing an
    ad for having no history.

    config, if given, is a BatchAdConfig (frozen, generate_from_selection) snapshot of the
    eight run-strip fields it holds - belt and braces with the caller's own explicit
    keyword args, which generate_from_selection still passes unchanged. Both carry the same
    values today, so this is output-identical whether or not config is supplied; the point
    is only to have one immutable per-ad value future per-ad-override work can build on,
    not to change what any single call resolves to yet. angle_id is deliberately NOT read
    from config here - messaging_angle keeps flowing exactly as before."""
    if config is not None:
        realism = config.realism
        body_area = config.body_area
        text_in_image = config.text_in_image
        include_product = config.include_product
        edit_mode = config.edit_mode
        offer_text = config.offer_text
        operator_instruction = config.operator_instruction

    # Captured RAW (possibly None) before normalization, so _regenerate_existing_draft's
    # resolver can tell "this call explicitly supplied a value" apart from "nothing came
    # in, fall back to the stored artifact value" (Task F, point 1). This is the exact gap
    # that let ads 1888339248562394/1194229189228603 log "defaulted True" while the
    # operator had just switched a toggle off live - the value never reached the
    # regenerate path to be considered in the first place.
    live_include_product = include_product
    live_retheme_colours = retheme_colours
    live_realism = realism
    live_body_area = body_area
    live_offer_text = offer_text
    if include_product is None:
        include_product = True
    if retheme_colours is None:
        retheme_colours = True
    if edit_mode is None:
        edit_mode = False

    ad_id = ad.get("ad_id")
    if not ad_id:
        return "failed"
    angle_id = messaging_angle["id"] if messaging_angle else None
    angle_slug = messaging_angle["slug"] if messaging_angle else None
    _stop = should_stop or (lambda: False)
    try:
        # angle_id=None here checks the exact same identity as before angle support
        # existed. All 138 pre-angle artifacts have angle_id NULL, so the first
        # angle-tagged run against an already-processed ad is EXPECTED to add a second
        # row alongside the existing NULL-angle one, not replace it - that will look like
        # duplication the first time it's seen; it is correct, not a bug to "fix".
        if not explicit_selection and not FORCE_REPROCESS and not dedupe.is_new(ad_id, angle_id):
            log.info("Ad %s already seen for angle_id=%s, skipping", ad_id, angle_id)
            return "skipped"

        # Item 7a: check BEFORE any paid call, not after. explicit_selection is the only
        # path this can ever fire on - run_once never sets it, so its behaviour is
        # untouched. Never pay for a deconstruct/Gemini call just to have save_artifact
        # silently discard the result afterward.
        if explicit_selection and not regenerate:
            existing = dedupe.get_artifact(ad_id, angle_id=angle_id)
            if existing is not None:
                log.info("Ad %s already generated for angle_id=%s, skipping (regenerate not requested)",
                         ad_id, angle_id)
                return "already_generated"

        # Regenerate supersedes the rest of this pipeline entirely - no deconstruct, no
        # fresh copy - but the image PROMPT is rebuilt from current code (2026-08-06),
        # never replayed frozen. Falls through to a normal first generation below when no
        # artifact exists yet for this (ad_id, angle_id) - never fails an ad for having no
        # history.
        if explicit_selection and regenerate:
            regen_result = _regenerate_existing_draft(
                ad, angle_id, angle_slug, operator_instruction, should_stop,
                live_include_product=live_include_product, live_retheme_colours=live_retheme_colours,
                live_realism=live_realism, live_body_area=live_body_area, live_offer_text=live_offer_text,
                check_output=check_output, clone_mode=clone_mode,
            )
            if regen_result is not None:
                return regen_result
            # else: no existing artifact yet - fall through, exactly as if regenerate had
            # never been requested.

        log.info("Ad %s (index %s/%s): deconstruct starting", ad_id, ad_index, total_ads)
        image_bytes = assets.download_image_bytes(ad["image_url"])
        image_path = assets.download_image(ad["image_url"], ad_id)
        blueprint = deconstruct.deconstruct_image(
            image_bytes=image_bytes,
            ad_id=ad_id,
            source_page=ad.get("page_name", ""),
            captured_at=ad.get("start_date", ""),
            destination_url=ad.get("destination_url", ""),
            ad_text=ad.get("text", ""),
            cta=ad.get("cta", ""),
        )
        # REWIRED 2026-08-17: visual.scene_lighting no longer exists (schema/
        # blueprint.schema.json - the objects-array refactor collapsed it into the new
        # top-level `background.light` phrase, see generate_image_prompt._scene_lighting_
        # facts). Reading the old key here always returned falsy after that refactor,
        # which made this warning fire on literally every ad rather than only genuine
        # extraction gaps - checking the new location instead.
        if not (blueprint.get("background") or {}).get("light"):
            log.warning("Ad %s: blueprint.background.light not extracted this run - "
                        "falling back to generic register-matching wording for the bottle's "
                        "lighting instead of an observed fact", ad_id)

        # Clone mode (2026-08-11): per-ad config derived from THIS ad's OWN fresh
        # blueprint, right where that blueprint first exists - config's own values (run
        # strip, or a per-ad override already merged in above) are the FALLBACK, only
        # overwritten per field when clone_mode is on, this field wasn't explicitly
        # pinned by ad_overrides for THIS ad, and the blueprint actually answers it. See
        # process_ad's own clone_mode/ad_overrides docstring for the full precedence.
        if clone_mode:
            _overrides = ad_overrides or {}
            if "realism" not in _overrides:
                _derived_realism = (blueprint.get("production_style") or {}).get("style")
                if _derived_realism:
                    realism = _derived_realism
            if "include_product" not in _overrides:
                include_product = generate_image_prompt.reference_has_product(blueprint)
            if "text_in_image" not in _overrides:
                text_in_image = generate_image_prompt.reference_has_text_zone(blueprint)
            if "body_area" not in _overrides:
                body_area = generate_image_prompt_writer.effective_body_area(blueprint, body_area)
            # offer_text is never per-ad-overridable (stays run-level, see BatchAdConfig's
            # own docstring), so there's no ad_overrides check here - this is a pure
            # SUPPRESSION, never invents an offer: withholds the operator's run-strip
            # offer_text when this specific reference has nowhere to render one.
            if offer_text and not generate_image_prompt.reference_has_offer_zone(blueprint):
                log.info("Ad %s: clone_mode suppressing offer_text - reference has no "
                         "offer-shaped zone", ad_id)
                offer_text = None

        # Hard block (Prompt 4, Item 3): a medical/clinical/intimate-health/anatomically
        # explicit reference must never be cloned - not a judgment call for a human to
        # weigh (unlike output_critic below), so this skips BEFORE any generation starts.
        # mark_seen here too, or this ad would burn a fresh deconstruct_image vision call
        # (and get hard-blocked again) every single future run that scrapes it.
        block_reason = content_safety.hard_block_reason(blueprint)
        if block_reason:
            log.warning("Ad %s hard-blocked before generation: %s", ad_id, block_reason)
            dedupe.init_pipeline_warnings()
            dedupe.record_warning(
                "hard_blocked_medical",
                f"Ad {ad_id} ({ad.get('page_name', '?')}): {block_reason}",
            )
            dedupe.mark_seen(ad_id, ad.get("page_name", ""), angle_id)
            return "skipped"

        # No-product-object placement (2026-08-19): include_product is True but the
        # reference has NO kind=="product" object at all - previously nothing told
        # Gemini WHERE the bottle goes, and it improvised. Confirmed live, ad
        # 1567146038752995 (one person, substitute; one sofa, keep; zero product
        # objects): the draft invented a wooden side table and cropped the woman down
        # to legs. Same on a separate pool ad, 2026-08-19 22:24: two bottles appeared
        # on an invented tray. See generate_image_prompt.resolve_no_product_placement/
        # find_supported_placement_bbox for the full reasoning (a region must rest on
        # a real, kept support surface - never the largest empty gap for its own sake,
        # and never a floating placement).
        #
        # Deliberately does NOT call dedupe.mark_seen, unlike the hard-block skip
        # immediately above - a hard-blocked medical reference can never become
        # generateable, but this case is only "unplaceable with TODAY's placement
        # logic": a future improvement to find_supported_placement_bbox, or a
        # re-deconstruct that reasons about the scene differently, could make the same
        # ad placeable later. Marking it seen would lock it out of ever being retried.
        needs_placement, product_placement_bbox, placement_reason = (
            generate_image_prompt.resolve_no_product_placement(blueprint, include_product)
        )
        if needs_placement and product_placement_bbox is None:
            log.warning("Ad %s: no-product-object placement failed: %s", ad_id, placement_reason)
            dedupe.init_pipeline_warnings()
            dedupe.record_warning(
                "no_product_placement_failed",
                f"Ad {ad_id} ({ad.get('page_name', '?')}): {placement_reason}",
            )
            return "skipped"
        if needs_placement:
            log.info(
                "Ad %s: no product object in reference - computed placement bbox %s "
                "(resting on a real kept support surface)", ad_id, product_placement_bbox,
            )

        # Reference usability (Task H, 2026-08-07; gate REVERSED same day, see CLAUDE.md):
        # a reference with nothing to clone is NOT skipped - it's logged, purely
        # informational, so the pool badge/logs can tell an operator what to expect
        # (product/copy ADDED rather than substituted) without ever blocking generation.
        if _reference_has_nothing_to_clone(blueprint):
            log.info(
                "Ad %s: reference has no product, no headline, no text-bearing zone, "
                "and no offer - product/copy will be ADDED into the scene rather than "
                "substituted, generation proceeds normally", ad_id,
            )

        # Format flag (Prompt 4, Item 4): a FLAG, never a filter - computed purely from
        # blueprint data already extracted (no vision call), carried through to
        # save_artifact below regardless of what happens next, so it's on the card
        # whether the ad is approved, rejected, or still pending.
        format_flag = reference_format.format_flag_reason(blueprint) or ""

        # effective_include_product is the SAME value build_image_prompt will use
        # internally for this exact blueprint - resolve_effective_include_product is the
        # single source both call, so there's one derivation to keep in sync, not two
        # (see effective_authorised_text above, added after rule 6 and the critic
        # drifted apart on exactly this shape of duplication).
        #
        # REVERSED 2026-08-07 (reference usability gate reversal): this used to silently
        # override include_product off when the reference had no product, with a
        # "product_override_no_reference_product" warning as the only feedback. That
        # premise is gone - effective_include_product now always equals include_product
        # exactly (see resolve_effective_include_product); reference_has_product only
        # selects ADD-vs-SUBSTITUTE wording downstream, never the boolean outcome. See
        # element_provenance below, computed after copy generation, for the new
        # "which elements were added vs substituted" signal that replaces this override
        # note going forward - product_override_note itself is kept (still read by
        # dashboard.html/regenerate for historical rows written under the old behaviour)
        # but nothing sets it to a non-empty value for a NEW generation any more.
        effective_include_product, reference_has_product = generate_image_prompt.resolve_effective_include_product(
            blueprint, include_product, edit_mode
        )
        reference_has_text_zone = generate_image_prompt.reference_has_text_zone(blueprint) if edit_mode else True
        product_override_note = ""

        # Silent-override audit (2026-08-05): a pasted operator brief past
        # MAX_OPERATOR_INSTRUCTION_CHARS is silently truncated by clip_operator_instruction
        # (appends "..." with no signal anywhere that anything was cut) - same defect
        # class as the product override above, just for free text instead of a toggle.
        _stripped_operator_instruction = (operator_instruction or "").strip()
        if len(_stripped_operator_instruction) > generate_image_prompt_writer.MAX_OPERATOR_INSTRUCTION_CHARS:
            dedupe.init_pipeline_warnings()
            dedupe.record_warning(
                "operator_instruction_truncated",
                f"Ad {ad_id} ({ad.get('page_name', '?')}): operator instruction was "
                f"{len(_stripped_operator_instruction)} characters, truncated to "
                f"{generate_image_prompt_writer.MAX_OPERATOR_INSTRUCTION_CHARS} for generation.",
            )

        MAX_COPY_ATTEMPTS = 2
        ok, issues = False, []
        # angle_language lookup lives HERE, not inside generate_copy.py - that module
        # imports only json_response and compliance_rules today and stays free of a
        # dedupe dependency; it receives a plain dict (or None) and never queries the DB
        # itself. messaging_angle is None (no angle selected) or its slug has no language
        # row yet: both resolve angle_language to None, which generate_copy.py's own
        # NO_ANGLE_LANGUAGE fallback treats as "no angle-specific language supplied" -
        # never a substituted/guessed vocabulary from a different angle.
        angle_language = dedupe.get_angle_language(messaging_angle["slug"]) if messaging_angle else None
        # Capped to the most recent USED_HEADLINES_CAP entries (2026-08-11) - the full,
        # unbounded run history was making copy quality degrade noticeably by the 5th/6th
        # ad: later ads were told to avoid every earlier headline AND subline, writing
        # around a growing constraint list rather than a stable one. The accumulator
        # itself (generate_from_selection/run_once) stays a plain, unbounded, append-only
        # list - this caps only what's READ from it here, so nothing is ever lost, and
        # lowering/raising the cap later is a one-line change with no effect on the
        # accumulation logic itself.
        USED_HEADLINES_CAP = 3
        for copy_attempt in range(1, MAX_COPY_ATTEMPTS + 1):
            copy_kwargs = {"product": product, "offer_text": offer_text}
            if angle_language:
                copy_kwargs["angle_language"] = angle_language
            if used_headlines:
                copy_kwargs["used_headlines"] = used_headlines[-USED_HEADLINES_CAP:]
            if copy_attempt > 1:
                # Fail-soft: feed the SPECIFIC prior failure back rather than discarding
                # the ad outright. On-category pool is small (36 ads) - throwing one away
                # over a fixable copy issue is expensive. Same blueprint, only copy retried.
                copy_kwargs["compliance_feedback"] = issues
            copy = generate_copy.generate_copy_live(blueprint, **copy_kwargs)
            # offer_text=offer_text (not omitted) activates check_unauthorized_offer - a
            # real incident produced "50% off - ONLY while stock lasts" in generated copy
            # with offer_text empty, sourced from the competitor's own blueprint.offer.
            # testimonial_attribution is deliberately left at its default ("" - nothing
            # authorized): select_testimonial_review's real review is never passed into
            # generate_copy_live at all (image-path-only, substituted directly into the
            # image prompt's social_proof zone - see select_testimonial_review's own
            # docstring), so generated copy has no legitimate reason to contain ANY
            # personal name, real or borrowed - "" here doesn't relax the check, it's
            # simply always correct. Computing the real testimonial here just to pass its
            # attribution through would call select_testimonial_review (a DB read) before
            # this loop unconditionally, on every ad, even ones this loop already fails
            # before reaching image generation at all - a real regression, caught by
            # test_pipeline.py, when tried during this same fix.
            ok, issues = compliance.check_compliance(copy, ad.get("page_name", ""), ad.get("text", ""),
                                                      offer_text=offer_text)
            # Per-object compliance (2026-08-17 restoration, item 7): the whole-draft
            # check above already scans object_copy's stringified content incidentally
            # (check_compliance joins every dict value with str()), but running each
            # object's own line through the SAME check independently attributes a
            # failure to the specific object_id that caused it, rather than one general
            # "the draft failed" issue smeared across the whole copy - "compliance must
            # apply per object, not once per draft." Feeds into the SAME ok/issues this
            # loop already retries on - no separate retry mechanism invented for this.
            for entry in (copy.get("object_copy") or []):
                if not isinstance(entry, dict):
                    continue
                entry_text = (entry.get("text") or "").strip()
                if not entry_text:
                    continue
                entry_ok, entry_issues = compliance.check_compliance(
                    {"text": entry_text}, ad.get("page_name", ""), ad.get("text", ""),
                    offer_text=offer_text,
                )
                if not entry_ok:
                    ok = False
                    issues = issues + [
                        f"object {entry.get('object_id', '?')}: {issue}" for issue in entry_issues
                    ]
            if ok:
                break
            log.warning("Ad %s failed compliance check (attempt %s/%s): %s",
                        ad_id, copy_attempt, MAX_COPY_ATTEMPTS, issues)
            log.warning("Ad %s generated_copy at failed attempt %s/%s: %s",
                        ad_id, copy_attempt, MAX_COPY_ATTEMPTS, copy)
        if not ok:
            reason = f"Ad {ad_id} ({ad.get('page_name', '?')}) failed compliance after {MAX_COPY_ATTEMPTS} attempt(s): {issues}"
            dedupe.init_pipeline_warnings()
            dedupe.record_warning("compliance_failed", reason)
            return "failed"
        # Distinctness (2026-08-17 restoration, item 5): enforced in CODE, never a
        # prompt sentence asking the model to avoid repetition (this codebase's own
        # standing finding is that prompt-only rules do not reliably bind - see
        # CLAUDE.md's guardrails note). Two DIFFERENT text objects resolving to the
        # identical generated line is a defect - detected here, surfaced as a
        # pipeline_warning, never blocking generation (same "detect and surface, never
        # gate" precedent as reference_format.py's bundle-offer flag / the output
        # critic's own findings) - a real customer testimonial elsewhere in this same
        # draft can legitimately share wording with nothing, and re-running the whole
        # copy loop over a distinctness defect alone risks the same demand-and-forbid
        # contradiction shape that produced artifact 1136.
        collisions = generate_copy.find_object_copy_collisions(
            blueprint.get("objects"), copy.get("object_copy"))
        if collisions:
            dedupe.init_pipeline_warnings()
            dedupe.record_warning(
                "object_copy_collision",
                f"Ad {ad_id} ({ad.get('page_name', '?')}): {len(collisions)} text object "
                f"pair(s) with different object_ids resolved to identical generated copy "
                f"despite different role/description/persuasive_function - {collisions}",
            )
        # Recorded ONCE the copy actually passed compliance - never a rejected/retried
        # attempt's copy - so the next ad in this run sees only real, final wording, not
        # something already discarded. Mutates the caller's own list in place (see this
        # function's used_headlines docstring) - None means the caller didn't opt in.
        if used_headlines is not None:
            used_headlines.append({"headline": copy.get("headline", ""),
                                    "image_subtext": copy.get("image_subtext", "")})
        if text_in_image and not copy.get("headline"):
            # Compliance passed but the copy has no usable headline (e.g. an empty string
            # slipped past validate_copy's key-presence check) - rule 6 will silently fall
            # back to its default blanket text ban, producing a plain, textless image with
            # no visible explanation of why the requested headline never appeared.
            dedupe.init_pipeline_warnings()
            dedupe.record_warning(
                "text_in_image_no_headline",
                f"Ad {ad_id} ({ad.get('page_name', '?')}): text_in_image was requested but "
                f"generated copy had no headline - image rendered without in-image text.",
            )
        # element_provenance (2026-08-07, reference usability gate reversal): which
        # elements were ADDED vs SUBSTITUTED vs not rendered at all, persisted on the
        # artifact for a reviewer to see - replaces the removed product-override note
        # above with a signal that's accurate under the new (never-skip, never-suppress)
        # behaviour. Derived purely from blueprint/authorisation state - a reference with
        # both a product and a text zone always reads "substituted"/"substituted" here
        # (byte-for-byte the only outcome before this task), one with neither reads
        # "added"/"added" whenever the operator authorised that element this run. Must
        # generalise across every reference shape - nothing here keys off ad_id/
        # competitor/page, only the blueprint's own fields plus this run's toggles.
        eff_headline, eff_subtext = generate_image_prompt.effective_authorised_text(
            text_in_image, copy.get("headline"), copy.get("image_subtext") or None,
        )
        element_provenance = {
            "product": (
                "none" if not effective_include_product
                else "substituted" if (edit_mode and reference_has_product)
                else "added"
            ),
            "text": (
                "none" if not eff_headline
                else "substituted" if (edit_mode and reference_has_text_zone)
                else "added"
            ),
        }

        if _stop():
            log.info("Ad %s: stop requested, skipping before the paid image generation call", ad_id)
            return "skipped"

        # Reference-photo count made explicit here (2026-08-14): fetch_reference_images
        # returns whatever succeeded on a PARTIAL GCS failure (0 to MAX_PRODUCT_IMAGES),
        # logging only a warning at fetch time - two runs of the SAME ad could silently
        # receive a different number of real reference photos, with nothing at
        # generation time itself showing which one this run got. "attached" (post
        # effective_include_product gate) is what actually reaches Gemini; "fetched" is
        # what fetch_reference_images returned (may be less than configured, on a
        # partial failure); "configured" is effective_image_keys(product)'s own count -
        # the ceiling this run could have had.
        _fetched_reference_count = len(reference_images or [])
        _attached_reference_count = _fetched_reference_count if effective_include_product else 0
        log.info(
            "Ad %s: image generation starting (edit_mode=%s, reference_images: "
            "%s attached, %s fetched, %s configured)",
            ad_id, edit_mode, _attached_reference_count, _fetched_reference_count,
            len(effective_image_keys(product)),
        )

        # testimonial (2026-08-06, fabricated-testimonials fix): computed ONCE, outside
        # the retry loop below - select_testimonial_review is deterministic by ad_id, so
        # every attempt (including the corrective retry) gets the SAME real review, never
        # a different one and never a fabricated one.
        testimonial = select_testimonial_review(blueprint, product, ad_id, angle_slug=angle_slug)

        # Corrective-retry loop (2026-08-05): observe (generate) -> evaluate (critic) ->
        # correct (feed the specific findings back) -> re-generate, capped at ONE retry.
        # Only engages when check_output is on AND the critic returns a HIGH-confidence
        # finding - check_output=False, or a clean/medium-only first attempt, is exactly
        # one generate_image call, byte-for-byte today's behaviour. save_artifact now runs
        # ONCE, after this loop resolves, with whichever attempt is final - never the
        # pre-retry draft when a retry cleaned it up, and never silently discarding a
        # still-flagged draft either (see the HIGH-after-retry branch below, which keeps
        # and marks it rather than losing it). A critic failure/timeout at any point still
        # can never lose a draft - it just stops the loop and keeps whatever was already
        # generated, unflagged, same fallback as before this loop existed.
        MAX_IMAGE_ATTEMPTS = 2
        draft_image, img_prompt, findings = None, "", None
        review_status = "ok"
        for image_attempt in range(1, MAX_IMAGE_ATTEMPTS + 1):
            gen_kwargs = {}
            if image_attempt > 1:
                # Fail-soft, same shape as the copy retry above: feed the SPECIFIC prior
                # violations back as corrections, not a generic "try again" - the critic's
                # descriptions are concrete enough to be actionable (e.g. "the headline
                # reads the competitor's product name, not the authorised one").
                gen_kwargs["critic_feedback"] = [
                    f"{f.get('category', '')}: {f.get('description', '')}" for f in findings
                ]
                log.warning(
                    "Ad %s: output critic found high-confidence issue(s), retrying image "
                    "generation once with corrections: %s", ad_id, findings,
                )
                if _stop():
                    log.info("Ad %s: stop requested, skipping the corrective retry", ad_id)
                    break
            try:
                new_draft_image = generate_image_prompt.generate_image(
                    blueprint, ad_id, product=product, reference_images=reference_images, angle_slug=angle_slug,
                    include_product=include_product, text_in_image=text_in_image,
                    # subtext MUST be the short image_subtext field, never primary_text: primary_text
                    # is long-form Facebook post body copy (~80 words) - passing it as subtext meant
                    # rule 6 permitted rendering the ENTIRE thing as in-scene typography against
                    # references that carried 10-20 words. image_subtext missing/empty (older copy,
                    # or Claude omitted it) falls back to headline-only, never to the paragraph.
                    headline=copy.get("headline"), subtext=copy.get("image_subtext") or None,
                    messaging_angle=messaging_angle, realism=realism,
                    body_area=body_area, offer_text=offer_text,
                    edit_mode=edit_mode, competitor_image_bytes=(image_bytes if edit_mode else None),
                    operator_instruction=operator_instruction, retheme_colours=retheme_colours,
                    # cta_text (2026-08-06): the generated copy's own CTA label, only
                    # consumed by build_image_prompt's structural_zones "cta" substitution
                    # in edit mode - a no-op everywhere else (generate mode/no structural
                    # zones), same "caller decides, this just forwards" pattern as
                    # offer_text/realism.
                    cta_text=copy.get("cta") or None,
                    # panel_copy (2026-08-06, generalised 2026-08-11): generate_copy_live's
                    # per-zone output for any sub_line/body_copy zone(s) (see
                    # generate_copy.text_zone_targets) - only consumed by structural_zones'
                    # sub_line/body_copy routing in edit mode, a no-op everywhere else, same
                    # forwarding pattern as cta_text.
                    panel_copy=copy.get("panel_copy") or None,
                    # object_copy (2026-08-17 restoration, root-cause fix): generate_
                    # copy_live's per-object output for any kind=="text" object with no
                    # recognised text_purpose (see generate_copy.text_objects_needing_
                    # copy) - consumed by generate_image_prompt._substitute_object_line's
                    # fallback branch, keyed by object_id, a no-op everywhere else, same
                    # forwarding pattern as cta_text/panel_copy.
                    object_copy=copy.get("object_copy") or None,
                    testimonial=testimonial,
                    product_count=product_count,
                    clone_mode=clone_mode,
                    no_product_placement_bbox=product_placement_bbox,
                    **gen_kwargs,
                )
            except Exception as e:
                log.error("Ad %s failed: image generation raised (attempt %s/%s): %s",
                          ad_id, image_attempt, MAX_IMAGE_ATTEMPTS, e)
                new_draft_image = None
            if not new_draft_image:
                if draft_image:
                    # The retry itself produced nothing - keep the pre-retry draft rather
                    # than losing it; its (already HIGH) findings still stand.
                    break
                log.error("Ad %s failed: no draft image produced - not saving a half-complete artifact", ad_id)
                return "failed"
            draft_image = new_draft_image
            img_prompt = getattr(generate_image_prompt.generate_image, "last_prompt", "")

            if not check_output:
                break  # critic disabled - one generation, no findings, today's behaviour

            # Strictly after generation - the draft already exists in memory/on disk
            # before this ever runs. Wrapped in its own try/except (on top of
            # check_draft's own internal never-raises contract) as defense in depth: even
            # a bug in this block (e.g. the draft file missing on disk) must never fail an
            # otherwise-successful run or cost this ad its draft.
            try:
                from pathlib import Path as _Path
                draft_bytes = _Path(draft_image).read_bytes()
                # effective_include_product, not the raw operator toggle - the critic must
                # be told the SAME product-presence rule the generator was actually given
                # (build_image_prompt resolved this identically for this blueprint), never
                # the pre-override value. Same asymmetry class as the text_in_image fix
                # below - this was the OTHER live false positive ("Missing authorised
                # product" on a run where none was ever authorised).
                brand_rules_text = generate_image_prompt.brand_rules(
                    include_product=effective_include_product, text_in_image=text_in_image,
                    headline=copy.get("headline"), subtext=copy.get("image_subtext") or None,
                    edit_mode=edit_mode,
                )
                # The critic must never be told something the generator wasn't told (a
                # real HIGH "Missing authorised text" false positive, 2026-08-04): rule 6
                # above gates the headline/subtext it permits on text_in_image via
                # effective_authorised_text - the critic's own authorised-text line must
                # be built from the SAME call, not a second, independent truthy check on
                # copy.get("headline") alone (which is always truthy regardless of
                # text_in_image - generate_copy_live produces a headline whether or not
                # the operator asked for it in-image).
                # eff_headline/eff_subtext: computed ONCE above (element_provenance) -
                # reused here rather than recomputed, since text_in_image/copy don't
                # change between attempts; same value either way, one derivation not two.
                log.info("Ad %s: output critic starting (attempt %s/%s)", ad_id, image_attempt, MAX_IMAGE_ATTEMPTS)
                findings = output_critic.check_draft(
                    draft_bytes, brand_rules_text, headline=eff_headline,
                    subtext=eff_subtext, offer_text=offer_text,
                    include_product=effective_include_product,
                    # PART 1G (2026-08-06): the critic must be told the SAME documented
                    # bottle facts build_image_prompt's product_clause already gives the
                    # generator - without this, it has no way to tell the real, verified
                    # label sub-lines from invented/leaked text and judges purely off rule
                    # 1's bare "name only" wording, which is exactly what produced a false
                    # positive on the L'Occitane run's own (correct) label.
                    visual_description=(product or {}).get("visual_description"),
                    ingredients=(product or {}).get("ingredients"),
                    # testimonial (2026-08-07): the SAME real-review-or-nothing pick the
                    # generator was given - closes the false positive where the critic
                    # flagged select_testimonial_review's own correct output as a
                    # fabricated quote (C2), never having been told one was authorised.
                    testimonial=testimonial,
                    # reference_image_bytes (Item 2, 2026-08-12): the SAME competitor ad
                    # bytes attached to Gemini for generation (see edit_mode's
                    # competitor_image_bytes below) - without this the critic's SUBJECT
                    # IDENTITY category has nothing to compare the draft against. Only in
                    # edit_mode: generate mode never had a real reference photo to clone
                    # an identity FROM, so there is nothing meaningful to compare there.
                    reference_image_bytes=(image_bytes if edit_mode else None),
                )
            except Exception as e:
                log.warning("Ad %s: output critic block raised (%s: %s), draft left unflagged",
                            ad_id, type(e).__name__, e)
                findings = None

            if findings is None:
                dedupe.init_pipeline_warnings()
                dedupe.record_warning(
                    "critic_failed",
                    f"Ad {ad_id} ({ad.get('page_name', '?')}): output critic check "
                    f"failed or was unparseable - draft saved and shown unflagged, "
                    f"not automatically re-checked.",
                )
                break
            # Mechanical backstop, general - not testimonial-specific (2026-08-07): drop
            # any finding that quotes back content THIS prompt actually authorised
            # (testimonial/offer_text/headline/subtext) before it can trigger a retry -
            # feeding such a finding into gen_kwargs["critic_feedback"] tells Gemini to
            # remove content the SAME rebuilt prompt is simultaneously instructing it to
            # render, spending a paid generation on an unsatisfiable prompt. See
            # output_critic.drop_findings_contradicted_by_authorised.
            dropped_count = len(findings)
            findings = output_critic.drop_findings_contradicted_by_authorised(
                findings, testimonial=testimonial,
            )
            if len(findings) != dropped_count:
                log.info(
                    "Ad %s: %s critic finding(s) dropped as contradicted by authorised "
                    "content, %s remain", ad_id, dropped_count - len(findings), len(findings),
                )
            if not output_critic.has_high_confidence(findings):
                break  # clean, or medium/low only - keep this draft
            if image_attempt >= MAX_IMAGE_ATTEMPTS:
                # Retry exhausted and still HIGH: never discard the draft, but mark it
                # review_status='failed-review' (2026-08-10) so it can never look clean.
                # Confirmed live before this fix: ad 820540537722129 saved with no
                # failure signal anywhere - critic_findings alone was NOT read by
                # dashboard.html to drive any card state (that claim, made here
                # previously, was wrong); review_status is the actual mechanism, written
                # in the same call as the findings below, and rendered as a distinct
                # "Failed Review" badge in dashboard.html (see templates/dashboard.html).
                review_status = "failed-review"
                dedupe.init_pipeline_warnings()
                dedupe.record_warning(
                    "critic_high_after_retry",
                    f"Ad {ad_id} ({ad.get('page_name', '?')}): output critic still found "
                    f"high-confidence issue(s) after one corrective retry - draft saved "
                    f"but marked failed review: {findings}",
                )
                break
            # HIGH finding(s), and a retry remains - loop continues into the next
            # attempt, which feeds `findings` back as gen_kwargs["critic_feedback"] above.

        # Critic-gate continuation (2026-08-10): save_artifact(regenerate=True) DELETEs
        # then re-INSERTs, and critic_findings/review_status are not in that INSERT's
        # column list (see the dedupe.py schema audit) - they would silently reset to
        # '[]'/'ok' on every regenerate unless the PRIOR values are captured before the
        # DELETE destroys them and explicitly rewritten afterward. Mirrors
        # _regenerate_existing_draft's own fix exactly - this is the sibling gap that
        # fix didn't close, because THIS save_artifact call belongs to process_ad's own
        # fresh-generation body, reached via the Task F point 2 fallthrough (existing
        # row, unreadable draft image) when explicit_selection+regenerate are both true.
        # Gated on the SAME condition save_artifact's own `regenerate=` argument below
        # resolves truthy on - a genuine first generation has no prior row and must not
        # fetch one.
        prior_critic_findings, prior_review_status = None, None
        if explicit_selection and regenerate:
            prior_artifact = dedupe.get_artifact(ad_id, angle_id=angle_id)
            if prior_artifact is not None:
                prior_critic_findings = prior_artifact.get("critic_findings") or []
                prior_review_status = prior_artifact.get("review_status") or "ok"

        cp_prompt = getattr(generate_copy.generate_copy_live, "last_prompt", "")
        dedupe.save_artifact(
            ad_id=ad_id,
            page_name=ad.get("page_name", ""),
            image_path=image_path,
            blueprint=blueprint,
            generated_copy=copy,
            draft_image=draft_image,
            image_prompt=img_prompt,
            copy_prompt=cp_prompt,
            model_info="image: gemini-3.1-flash-image (vertex) | copy: claude-sonnet-4-6",
            metadata={
                "start_date": ad.get("start_date", ""),
                "cta": ad.get("cta", ""),
                "destination_url": ad.get("destination_url", ""),
                "media_type": ad.get("media_type", ""),
            },
            angle_id=angle_id,
            text_in_image=text_in_image,
            operator_instruction=operator_instruction or "",
            format_flag=format_flag,
            product_override_note=product_override_note,
            # include_product is the RAW operator toggle (not effective_include_product) -
            # a future regenerate rebuild must re-derive effective_include_product itself
            # from the (also stored) blueprint, the same way this call did, not inherit an
            # already-narrowed value. realism/body_area/offer_text/retheme_colours are the
            # same run-strip inputs process_ad received, straight through - see
            # _regenerate_existing_draft (2026-08-06) for why these are persisted now.
            include_product=include_product,
            retheme_colours=retheme_colours,
            realism=realism,
            body_area=body_area,
            offer_text=offer_text,
            product_id=(product or {}).get("id"),
            element_provenance=element_provenance,
            # None (not explicit_selection) preserves save_artifact's own
            # FORCE_REPROCESS-driven default exactly - only an explicit-selection
            # regenerate passes an explicit True, per Item 7b.
            regenerate=(regenerate if explicit_selection else None),
        )
        # findings is None when check_output was off, or the critic never produced a
        # verdict (failure/timeout) - distinct from an empty list (checked, clean), which
        # still needs writing so critic_findings reflects an actual clean check.
        if findings is not None:
            dedupe.update_artifact_findings(ad_id, findings, angle_id=angle_id, review_status=review_status)
        elif prior_review_status is not None:
            # No fresh verdict this run, but this WAS a regenerate of an existing row -
            # carry the prior findings/review_status forward explicitly. Skipping this
            # write would NOT preserve it - the save_artifact call above already reset it
            # via its DELETE+INSERT; this is the only way to undo that.
            dedupe.update_artifact_findings(ad_id, prior_critic_findings, angle_id=angle_id,
                                             review_status=prior_review_status)

        dedupe.mark_seen(ad_id, ad.get("page_name", ""), angle_id)

        try:
            slack_review.post_review(ad, blueprint, copy, image_ref=draft_image or image_path)
            log.info("Ad %s processed and posted to Slack", ad_id)
        except Exception as e:
            log.warning("Ad %s saved but Slack post failed: %s", ad_id, e)

        return "processed"
    except Exception as e:
        log.error("Ad %s failed: %s", ad_id, e)
        return "failed"


def run_once(max_per_competitor=5, competitor_id=None, should_stop=None, product_id=None, category=None,
             angle_id=None, realism=None, text_in_image=False, include_product=True,
             body_area=None, offer_text=None, edit_mode=False, operator_instruction=None,
             check_output=False, retheme_colours=True):
    """One scheduled run across the watchlist, or a single competitor if
    competitor_id is given, or every competitor tagged with `category` if that's
    given instead. competitor_id takes precedence if both are somehow passed.
    category="" is treated the same as category=None (no filter, every
    competitor runs) - it does NOT mean "match competitors with no category set."
    should_stop is an optional zero-arg callable checked between ads/competitors
    to cooperatively halt the run early.

    max_per_competitor caps ATTEMPTS per competitor, not successes (Chunk 4 fix,
    2026-08-06) - every ad that clears the cheap already-seen check is a real paid
    deconstruct call and counts against the cap whether it ends up processed,
    hard-blocked, or failed. The old success-only count let the loop keep trying
    ad after ad past the requested cap until enough succeeded (observed live as a
    1-ad request processing nine) - this matters more now that the widened
    scrape.py filter has roughly doubled the candidate pool per competitor.

    angle_id, if given, resolves to a messaging angle applied to every ad in this run -
    it changes dedup identity to (ad_id, angle_id), so an ad already processed with no
    angle (or a different angle) will be processed again under this one, producing an
    additional artifact rather than being skipped. angle_id=None behaves exactly as
    before angle support existed.

    realism/text_in_image/include_product/body_area/offer_text/edit_mode/operator_instruction
    are the other run-strip controls, applied to every ad in this run - operator-set per
    run, never auto-detected from the angle or the competitor ad. Defaults (None/False/
    True/None/None/False/None) reproduce today's behaviour exactly. body_area is a
    per-run free-text value, deliberately never read from the resolved angle's own
    body_area column (see process_ad's docstring).

    BatchAdConfig (2026-08-18): a fresh instance is built inside the per-ad loop below and
    passed as process_ad's config= argument, mirroring generate_from_selection's own
    per-ad construction - run_once has no per-ad-override input, so every field still
    holds the same run-strip value for every ad in the sweep (that hasn't changed), but
    process_ad no longer receives this run's settings only via its own enclosing-scope
    locals with config=None. realism="(auto)" (None here) still resolves PER AD from
    that ad's own detected production_style inside build_image_prompt, unaffected by
    this - see BatchAdConfig's own docstring.

    used_headlines (2026-08-11, same-run copy convergence fix): one plain list, created
    HERE, scoped to this WHOLE run_once call - shared across every competitor and every
    ad in the sweep, not reset per competitor. Same mechanism as generate_from_selection's
    own (see process_ad's/generate_copy._used_copy_clause's docstrings).

    The returned summary gains "by_competitor": {name: {ads_seen, processed, skipped,
    failed, error}} - a category sweep's total is otherwise illegible, since image yield
    varies hugely per brand (roughly 1/10 to 8/10 across pages per CLAUDE.md), so a low
    total can be the pool, not a bug. dedupe.set_run_progress records which competitor is
    currently running, DB-backed (not an in-memory variable) so it's readable the same way
    whether this runs in-process (LOCAL_RUN) or as a separate Cloud Run Job.

    Reference photo total-fetch-failure guard: mirrors generate_from_selection's own
    (see that function's docstring for the full reasoning - Gemini building the bottle
    from visual_description text alone gets pump direction/proportions wrong, and every
    ad in the run would still cost a full paid deconstruct+copy+image call for a draft
    that was never usable). Refused BEFORE the competitor loop starts - no competitor has
    been scraped yet at this point, so unlike generate_from_selection there is no known
    ad_id list to mark "failed" one by one; the return is {"processed": 0, "skipped": 0,
    "failed": 0, ...} with the reason in "error", zero competitors ever touched. Matters
    MORE here than on the dashboard path: this is also job_runner.py's entrypoint, an
    unattended scheduled Cloud Run Job - nobody is watching a run-strip warning banner
    for it, so an unguarded run would silently burn the whole watchlist's worth of paid
    calls on bottle-less drafts with no human ever seeing why. include_product=False
    skips this guard entirely (default is True here, never None - unlike generate_from_
    selection's own None-means-not-supplied convention, run_once's include_product is
    always a concrete bool). A partial fetch failure (some images fetched) is unchanged -
    proceeds with the existing warning, never refused."""
    from src.config_check import validate_config
    validate_config()
    dedupe.init_db()
    dedupe.init_decisions()
    dedupe.init_artifacts()
    dedupe.init_competitors()
    dedupe.init_products()
    dedupe.init_angles()
    dedupe.init_angle_language()
    product = dedupe.get_product(product_id) if product_id else None
    messaging_angle = dedupe.get_angle(angle_id) if angle_id else None
    reference_images = []
    reference_warning = None
    if product:
        reference_images, reference_warning = fetch_reference_images(product)
        if reference_warning:
            kind, detail = reference_warning
            log.warning("%s: %s", kind, detail)
            dedupe.init_pipeline_warnings()
            dedupe.record_warning(kind, detail)
            # Total fetch failure guard (mirrors generate_from_selection's own) - refused
            # BEFORE any competitor is scraped, let alone any paid call. Partial failure
            # (reference_images non-empty) never reaches here - this branch only fires
            # when EVERY configured key failed.
            if include_product and kind == "reference_photo_fetch_failed" and not reference_images:
                reason = (
                    f"{detail} Likely cause: expired Google Cloud credentials (ADC), not a "
                    f"missing file - re-auth and restart, then retry. Refused before any "
                    f"paid call; nothing was generated."
                )
                log.warning("run_once refused: %s", reason)
                dedupe.record_warning("reference_photo_fetch_refused", reason)
                return {"processed": 0, "skipped": 0, "failed": 0,
                        "reference_photo_warning": reference_warning,
                        "by_competitor": {}, "error": reason}
    should_stop = should_stop or (lambda: False)
    # Run-scoped, not per-competitor/per-ad: ONE list for the whole run_once call, the
    # SAME object handed to every process_ad call below across every competitor -
    # process_ad appends to it in place once an ad's own copy passes compliance (see
    # process_ad's own used_headlines docstring). Mirrors generate_from_selection's own.
    used_headlines = []

    competitors = dedupe.get_competitors()
    if competitor_id is not None:
        competitors = [c for c in competitors if c.get("id") == competitor_id]
    elif category:
        # Falsy check, not `is not None`: category="" must mean "no filter", the
        # same as category=None, NOT "match untagged competitors."
        competitors = [c for c in competitors if (c.get("category") or "") == category]
    # by_competitor makes a category sweep's yield legible rather than mysterious: image
    # yield varies hugely per brand (CLAUDE.md: measured 1/10 to 8/10 across pages), so a
    # sweep returning far fewer ads than brands is the pool, not a bug - this is what lets
    # an operator see THAT, instead of just a suspiciously low total.
    summary = {"processed": 0, "skipped": 0, "failed": 0, "reference_photo_warning": reference_warning,
               "by_competitor": {}}

    # DB-backed, not an in-memory variable - the Cloud Run Job path is a separate process
    # with no shared memory with the dashboard, so "which competitor is running now" has to
    # be readable the same way for both run paths (see dedupe.set_run_progress's docstring).
    dedupe.init_run_progress()
    total_competitors = len(competitors)
    for idx, competitor in enumerate(competitors, 1):
        if should_stop():
            log.info("Stop requested, halting run.")
            break
        name = competitor.get("name", "?")
        dedupe.set_run_progress(name, idx, total_competitors)
        comp_summary = {"ads_seen": 0, "processed": 0, "skipped": 0, "failed": 0, "error": None}
        try:
            ads = with_retry(lambda: scrape.scrape_ads(name, page_id=competitor.get("page_id")),
                             attempts=2, delay=2)
            comp_summary["ads_seen"] = len(ads)
            log.info("Scrape complete for %s: %s ads returned", name, len(ads))
            # suggest the real page name if it differs from our list
            try:
                if ads:
                    real = (ads[0].get("page_name") or "").strip()
                    if real and real.lower() != str(name).strip().lower():
                        dedupe.set_suggested_name(competitor["id"], real)
            except Exception as _e:
                log.warning("name suggestion failed (non-fatal): %s", _e)
            # auto-capture: lock the exact page id from the first matched ad
            try:
                current_pid = str(competitor.get("page_id") or "").strip()
                if ads and (not current_pid or current_pid == str(name).strip()):
                    found = next((a.get("page_id") for a in ads if a.get("page_id")), None)
                    if found:
                        dedupe.update_competitor(competitor["id"], name=name, page_id=found,
                                                  category=competitor.get("category") or "")
                        log.info("Auto-captured page_id %s for %s", found, name)
            except Exception as _e:
                log.warning("page_id auto-capture failed (non-fatal): %s", _e)
        except Exception as e:
            log.error("Scrape failed for %s: %s (clean skip)", name, e)
            comp_summary["error"] = str(e)
            summary["by_competitor"][name] = comp_summary
            continue
        attempts_this_comp = 0
        for ad_index, ad in enumerate(ads, 1):
            if should_stop():
                log.info("Stop requested, halting run.")
                break
            if attempts_this_comp >= max_per_competitor:
                log.info("Reached cap of %s attempted ads for %s, stopping.", max_per_competitor, name)
                break
            # Attempt-counting, not success-counting (2026-08-06 fix): a cheap
            # already-seen skip costs nothing and must not consume the budget, but
            # ANY ad that reaches deconstruct - whether it ends up processed,
            # hard-blocked, or failed - is a real PAID attempt and must count against
            # the cap. Counting only "processed" (the old check) let the loop keep
            # burning paid calls on ad after ad past the requested cap until enough
            # of them happened to succeed - observed live as a 1-ad request
            # processing nine. This mirrors process_ad's own seen_ads check exactly
            # (read-only, not a second gating decision - process_ad still makes the
            # real call) purely to know whether the upcoming call is free or paid.
            ad_id = ad.get("ad_id")
            already_seen = bool(ad_id) and not FORCE_REPROCESS and not dedupe.is_new(ad_id, angle_id)
            if not already_seen:
                attempts_this_comp += 1
            # Per-ad BatchAdConfig (2026-08-18): run_once has no per-ad-override input
            # (unlike generate_from_selection's per_ad_overrides), so every field here
            # still holds the same run-strip value for every ad in the sweep - that part
            # is unchanged and correct, a scheduled sweep genuinely has one operator-set
            # config today. What changes is HOW process_ad receives it: a fresh
            # BatchAdConfig instance built here, at the top of this ad's own iteration,
            # rather than process_ad reading run_once's enclosing-scope locals directly
            # (config=None previously, so process_ad's config-override block never ran
            # for this call path at all). realism="(auto)" (None here) still resolves
            # PER AD from that ad's own detected production_style inside
            # build_image_prompt, unaffected by this - this only changes the plumbing
            # process_ad/generate_image receive it through, not the resolution logic.
            cfg = BatchAdConfig(
                angle_id=angle_id,
                realism=realism,
                body_area=body_area,
                text_in_image=text_in_image,
                include_product=include_product,
                edit_mode=edit_mode,
                offer_text=offer_text,
                operator_instruction=operator_instruction,
            )
            result = process_ad(ad, product=product, reference_images=reference_images, messaging_angle=messaging_angle,
                                realism=realism, text_in_image=text_in_image, include_product=include_product,
                                body_area=body_area, offer_text=offer_text, edit_mode=edit_mode,
                                operator_instruction=operator_instruction, check_output=check_output,
                                retheme_colours=retheme_colours, ad_index=ad_index, total_ads=len(ads),
                                should_stop=should_stop, used_headlines=used_headlines, config=cfg)
            summary[result] += 1
            comp_summary[result] += 1
        summary["by_competitor"][name] = comp_summary

    dedupe.set_run_progress("", 0, 0)  # clear - a finished run must not leave a stale entry
    log.info("Run complete: %s", summary)
    return summary


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    run_once()
