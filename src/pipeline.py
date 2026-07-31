"""End-to-end pipeline: scrape -> dedupe -> image -> blueprint -> copy -> Slack.

One scheduled run across the watchlist. Each ad is failure-isolated: one bad
ad or failed stage is skipped cleanly without stopping the run.
"""
import os
import logging
from src import dedupe, scrape, assets, deconstruct, generate_copy, generate_image_prompt, slack_review, compliance, output_critic, content_safety, reference_format
from src.retry import with_retry

FORCE_REPROCESS = os.getenv("FORCE_REPROCESS") == "1"

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


def process_ad(ad, product=None, reference_images=None, messaging_angle=None,
                realism=None, text_in_image=False, include_product=True,
                body_area=None, offer_text=None, edit_mode=False, operator_instruction=None,
                check_output=False, retheme_colours=True):
    """Run one ad through the full pipeline. Returns processed/skipped/failed.
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

    operator_instruction (Step 2) is the run-strip's free-text steering field - forwarded
    to generate_image (which clips it and hands it to both the writer and
    build_image_prompt) AND persisted onto the saved artifact below, so a reviewer can see
    whether a wrong draft is what the operator actually asked for.

    check_output (Prompt 4, Item 1) gates the output critic - a SAFETY control, not a
    quality feature, since every guardrail up to this point is prompt-only and nothing
    has ever inspected what Gemini actually produced. Runs strictly AFTER save_artifact
    (never blocks or risks losing a draft) and never fails the run: any critic failure is
    caught, recorded as a pipeline_warning, and the card is left unflagged - never treated
    as a finding of its own. Defaults to False - this is an extra vision call per ad, real
    cost that multiplies across a sweep, so it's opt-in per run.

    retheme_colours (Prompt 4, Item 5) only affects edit_mode - defaults to True, since
    the team's own doc calls for re-theming the reference's palette to Besque's on every
    clone unless the angle specifically calls for something else; the operator disables
    it per run for that stated exception, which also protects today's validated
    faithful-clone behaviour."""
    ad_id = ad.get("ad_id")
    if not ad_id:
        return "failed"
    angle_id = messaging_angle["id"] if messaging_angle else None
    angle_slug = messaging_angle["slug"] if messaging_angle else None
    try:
        # angle_id=None here checks the exact same identity as before angle support
        # existed. All 138 pre-angle artifacts have angle_id NULL, so the first
        # angle-tagged run against an already-processed ad is EXPECTED to add a second
        # row alongside the existing NULL-angle one, not replace it - that will look like
        # duplication the first time it's seen; it is correct, not a bug to "fix".
        if not FORCE_REPROCESS and not dedupe.is_new(ad_id, angle_id):
            log.info("Ad %s already seen for angle_id=%s, skipping", ad_id, angle_id)
            return "skipped"

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

        # Format flag (Prompt 4, Item 4): a FLAG, never a filter - computed purely from
        # blueprint data already extracted (no vision call), carried through to
        # save_artifact below regardless of what happens next, so it's on the card
        # whether the ad is approved, rejected, or still pending.
        format_flag = reference_format.format_flag_reason(blueprint) or ""

        MAX_COPY_ATTEMPTS = 2
        ok, issues = False, []
        for copy_attempt in range(1, MAX_COPY_ATTEMPTS + 1):
            # Deliberately angle-blind for now: messaging_angle is not passed here, only
            # into the image side below. Fine while the image's baked-in text is off by
            # default, but once text_in_image renders an angle-specific headline into the
            # image, copy that doesn't know the angle is likely to read mismatched - revisit
            # generate_copy_live's inputs if that mismatch shows up in practice.
            copy_kwargs = {"product": product, "offer_text": offer_text}
            if copy_attempt > 1:
                # Fail-soft: feed the SPECIFIC prior failure back rather than discarding
                # the ad outright. On-category pool is small (36 ads) - throwing one away
                # over a fixable copy issue is expensive. Same blueprint, only copy retried.
                copy_kwargs["compliance_feedback"] = issues
            copy = generate_copy.generate_copy_live(blueprint, **copy_kwargs)
            # offer_text=offer_text (not omitted) activates check_unauthorized_offer - a
            # real incident produced "50% off - ONLY while stock lasts" in generated copy
            # with offer_text empty, sourced from the competitor's own blueprint.offer.
            ok, issues = compliance.check_compliance(copy, ad.get("page_name", ""), ad.get("text", ""),
                                                      offer_text=offer_text)
            if ok:
                break
            log.warning("Ad %s failed compliance check (attempt %s/%s): %s",
                        ad_id, copy_attempt, MAX_COPY_ATTEMPTS, issues)
        if not ok:
            reason = f"Ad {ad_id} ({ad.get('page_name', '?')}) failed compliance after {MAX_COPY_ATTEMPTS} attempt(s): {issues}"
            dedupe.init_pipeline_warnings()
            dedupe.record_warning("compliance_failed", reason)
            return "failed"
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
        try:
            draft_image = generate_image_prompt.generate_image(
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
            )
        except Exception as e:
            log.error("Ad %s failed: image generation raised: %s", ad_id, e)
            draft_image = None
        if not draft_image:
            log.error("Ad %s failed: no draft image produced - not saving a half-complete artifact", ad_id)
            return "failed"

        img_prompt = getattr(generate_image_prompt.generate_image, "last_prompt", "")
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
        )

        if check_output:
            # Strictly AFTER save_artifact - the draft is already safely persisted before
            # this ever runs, so nothing here can lose it. Wrapped in its own try/except
            # (on top of check_draft's own internal never-raises contract) as defense in
            # depth: even a bug in THIS block (e.g. the draft file missing on disk) must
            # never fail an otherwise-successful run.
            try:
                from pathlib import Path as _Path
                draft_bytes = _Path(draft_image).read_bytes()
                brand_rules_text = generate_image_prompt.brand_rules(
                    include_product=include_product, text_in_image=text_in_image,
                    headline=copy.get("headline"), subtext=copy.get("image_subtext") or None,
                    edit_mode=edit_mode,
                )
                findings = output_critic.check_draft(
                    draft_bytes, brand_rules_text, headline=copy.get("headline"),
                    subtext=copy.get("image_subtext") or None, offer_text=offer_text,
                    include_product=include_product,
                )
                if findings is None:
                    dedupe.init_pipeline_warnings()
                    dedupe.record_warning(
                        "critic_failed",
                        f"Ad {ad_id} ({ad.get('page_name', '?')}): output critic check "
                        f"failed or was unparseable - draft saved and shown unflagged, "
                        f"not automatically re-checked.",
                    )
                else:
                    dedupe.update_artifact_findings(ad_id, findings, angle_id=angle_id)
            except Exception as e:
                log.warning("Ad %s: output critic block raised (%s: %s), draft left unflagged",
                            ad_id, type(e).__name__, e)

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

    The returned summary gains "by_competitor": {name: {ads_seen, processed, skipped,
    failed, error}} - a category sweep's total is otherwise illegible, since image yield
    varies hugely per brand (roughly 1/10 to 8/10 across pages per CLAUDE.md), so a low
    total can be the pool, not a bug. dedupe.set_run_progress records which competitor is
    currently running, DB-backed (not an in-memory variable) so it's readable the same way
    whether this runs in-process (LOCAL_RUN) or as a separate Cloud Run Job."""
    from src.config_check import validate_config
    validate_config()
    dedupe.init_db()
    dedupe.init_decisions()
    dedupe.init_artifacts()
    dedupe.init_competitors()
    dedupe.init_products()
    dedupe.init_angles()
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
    should_stop = should_stop or (lambda: False)

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
        processed_this_comp = 0
        for ad in ads:
            if should_stop():
                log.info("Stop requested, halting run.")
                break
            if processed_this_comp >= max_per_competitor:
                log.info("Reached cap of %s new ads for %s, stopping.", max_per_competitor, name)
                break
            result = process_ad(ad, product=product, reference_images=reference_images, messaging_angle=messaging_angle,
                                realism=realism, text_in_image=text_in_image, include_product=include_product,
                                body_area=body_area, offer_text=offer_text, edit_mode=edit_mode,
                                operator_instruction=operator_instruction, check_output=check_output,
                                retheme_colours=retheme_colours)
            summary[result] += 1
            comp_summary[result] += 1
            if result == "processed":
                processed_this_comp += 1
        summary["by_competitor"][name] = comp_summary

    dedupe.set_run_progress("", 0, 0)  # clear - a finished run must not leave a stale entry
    log.info("Run complete: %s", summary)
    return summary


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    run_once()
