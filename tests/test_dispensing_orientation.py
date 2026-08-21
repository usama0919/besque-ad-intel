"""Bug 1 fix (2026-08-21): pump/nozzle direction inconsistency. The generated pump
nozzle, the visible oil stream, and the target body area must stay one consistent
directional relationship - the reference relationship (pump/nozzle orientation -> oil
stream direction -> target body area) must be preserved.

Smallest production-safe fix using the EXISTING deconstruction/blueprint/prompt/critic
pipeline - no new object-detection architecture, no hardcoded Besque/bottle/pump
vocabulary in the derivation itself (see _relative_direction_phrase/
_dispensing_orientation_fact, which reason purely about bbox geometry and the generic
kind enum). Two reinforcing parts:
  1. A data-driven OBSERVED FACT (generate_image_prompt._dispensing_orientation_fact),
     derived from objects[] bbox/kind data deconstruct.py already produces, stating
     the reference's own product-to-person spatial relationship when both exist.
  2. A universal internal-consistency sentence added to _bottle_integration_clause's
     existing "WHEN THE PRODUCT IS BEING APPLIED" text, requiring the dispensing
     opening's facing, the visible stream, and where it lands to agree with each
     other - this part needs no data and covers the reported bug pattern even when
     no reference-grounded direction fact is available (e.g. a hand-only close-up
     with no whole-figure kind=="person" object - a documented, known gap for part 1).
  3. A new output_critic checklist category (DISPENSING/APPLICATION DIRECTIONAL
     CONSISTENCY) extending the SAME critic, gated through the EXISTING
     has_high_confidence()/MAX_IMAGE_ATTEMPTS=2 bounded-retry mechanism - no second
     critic, no new retry loop."""
import json

from src import generate_image_prompt as gip
from src import output_critic
from tests.blueprint_fixtures import load_blueprint_fixture

OSEA_BLUEPRINT = load_blueprint_fixture("osea_two_products_both_substitute")

PRODUCT = {
    "name": "Magic Body Oil",
    "visual_description": "a tall cylindrical bottle with a gold collar and black pump",
    "substance_colour": "golden-amber oil",
}


# ---- _relative_direction_phrase / _dispensing_orientation_fact (pure geometry) ----

def test_relative_direction_phrase_below_and_right():
    # product near top-left, target near bottom-right
    phrase = gip._relative_direction_phrase([0.05, 0.05, 0.1, 0.1], [0.6, 0.6, 0.2, 0.2])
    assert "below" in phrase
    assert "to the right of" in phrase


def test_relative_direction_phrase_directly_above():
    phrase = gip._relative_direction_phrase([0.4, 0.6, 0.1, 0.1], [0.4, 0.1, 0.1, 0.1])
    assert phrase == "directly above"


def test_relative_direction_phrase_aligned_returns_same_position():
    phrase = gip._relative_direction_phrase([0.4, 0.4, 0.1, 0.1], [0.41, 0.41, 0.1, 0.1])
    assert phrase == "at essentially the same position as"


def _product_obj(bbox, object_id="obj_01"):
    return {"object_id": object_id, "kind": "product", "disposition": "substitute", "bbox": bbox}


def _person_obj(bbox, object_id="obj_02"):
    return {"object_id": object_id, "kind": "person", "disposition": "keep", "bbox": bbox}


def test_dispensing_orientation_fact_fires_with_product_and_person():
    objects = [
        _product_obj([0.05, 0.05, 0.15, 0.3]),
        _person_obj([0.5, 0.5, 0.4, 0.4]),
    ]
    fact = gip._dispensing_orientation_fact(objects)
    assert "OBSERVED PRODUCT-TO-APPLICATION-AREA RELATIONSHIP" in fact
    assert "below and to the right of" in fact
    assert "dispensing opening" in fact
    assert "must all agree with EACH OTHER" in fact


def test_dispensing_orientation_fact_empty_without_person():
    objects = [_product_obj([0.05, 0.05, 0.15, 0.3])]
    assert gip._dispensing_orientation_fact(objects) == ""


def test_dispensing_orientation_fact_empty_without_substituting_product():
    objects = [
        {"object_id": "obj_01", "kind": "product", "disposition": "drop", "bbox": [0.05, 0.05, 0.15, 0.3]},
        _person_obj([0.5, 0.5, 0.4, 0.4]),
    ]
    assert gip._dispensing_orientation_fact(objects) == ""


def test_dispensing_orientation_fact_never_mentions_besque_or_pump_by_name():
    """Generality requirement: the derivation itself must not be hardcoded to
    Besque/bottle/pump vocabulary - it should read the same for any analogous
    dispensing/application relationship."""
    objects = [_product_obj([0.05, 0.05, 0.15, 0.3]), _person_obj([0.5, 0.5, 0.4, 0.4])]
    fact = gip._dispensing_orientation_fact(objects)
    for banned in ("Besque", "bottle", "pump "):
        assert banned not in fact
    assert "dispensing opening" in fact  # generic vocabulary instead


def test_dispensing_orientation_fact_picks_largest_of_each_kind():
    objects = [
        _product_obj([0.0, 0.0, 0.05, 0.05], object_id="obj_small_product"),
        _product_obj([0.6, 0.6, 0.3, 0.3], object_id="obj_big_product"),
        _person_obj([0.0, 0.9, 0.05, 0.05], object_id="obj_small_person"),
        _person_obj([0.1, 0.1, 0.3, 0.3], object_id="obj_big_person"),
    ]
    fact = gip._dispensing_orientation_fact(objects)
    # big product (0.6,0.6) -> big person (0.1,0.1): above and to the left
    assert "above and to the left of" in fact


# ---- End-to-end: the fact reaches the assembled prompt ----

def test_build_image_prompt_includes_dispensing_orientation_fact_end_to_end():
    blueprint = json.loads(json.dumps(OSEA_BLUEPRINT))
    blueprint["objects"] = [
        _product_obj([0.1, 0.1, 0.2, 0.4], object_id="obj_prod"),
        _person_obj([0.5, 0.5, 0.4, 0.45], object_id="obj_person"),
    ]
    prompt = gip.build_image_prompt(
        blueprint, product=PRODUCT, include_product=True, edit_mode=True, realism=None,
    )
    assert "OBSERVED PRODUCT-TO-APPLICATION-AREA RELATIONSHIP" in prompt


def test_build_image_prompt_suppresses_orientation_fact_when_compositing():
    """suppress_bottle_identity (Route B compositing) forbids drawing any liquid/pump
    at all - a direction fact about the never-drawn dispensing opening is moot, same
    suppression as geometry/identity."""
    blueprint = json.loads(json.dumps(OSEA_BLUEPRINT))
    blueprint["objects"] = [
        _product_obj([0.1, 0.1, 0.2, 0.4], object_id="obj_prod"),
        _person_obj([0.5, 0.5, 0.4, 0.45], object_id="obj_person"),
    ]
    prompt = gip.build_image_prompt(
        blueprint, product=PRODUCT, include_product=True, edit_mode=True, realism=None,
        suppress_bottle_identity=True,
    )
    assert "OBSERVED PRODUCT-TO-APPLICATION-AREA RELATIONSHIP" not in prompt


def test_bottle_integration_clause_states_universal_dispensing_consistency():
    clause = gip._bottle_integration_clause(suppress_bottle_identity=False)
    assert "DISPENSING DIRECTION MUST STAY INTERNALLY CONSISTENT" in clause
    assert "facing direction" in clause


# ---- Critic extension ----

def test_critic_system_has_dispensing_directional_consistency_category():
    assert "DISPENSING/APPLICATION DIRECTIONAL CONSISTENCY" in output_critic.CRITIC_SYSTEM
    assert "nozzle" in output_critic.CRITIC_SYSTEM.lower() or "pump" in output_critic.CRITIC_SYSTEM.lower()


def test_critic_system_dispensing_category_scoped_to_visible_gesture_only():
    """Must not fire on a bottle simply held/resting with no application gesture -
    stated explicitly so the critic doesn't over-flag ordinary product shots."""
    assert "simply held or resting with no" in output_critic.CRITIC_SYSTEM


def test_dispensing_category_not_forced_high_confidence_by_default():
    """Deliberate choice: unlike the categories in HIGH_CONFIDENCE_BY_DEFAULT (which
    all have a confirmed live-shipped-bug history), this is a brand-new check with no
    such history yet - left to the critic's own per-instance confidence judgement,
    same treatment as C8/C9 when they were first added."""
    assert not any("dispensing" in c.lower() for c in output_critic.HIGH_CONFIDENCE_BY_DEFAULT)


def test_has_high_confidence_gates_on_dispensing_finding():
    """The existing, generic gate (output_critic.has_high_confidence) works for this
    new category with zero code changes - proves the new category rides the EXISTING
    bounded-retry mechanism rather than needing one of its own."""
    findings = [{"category": "dispensing/application directional consistency",
                 "description": "nozzle faces left, oil pools on the right leg", "confidence": "high"}]
    assert output_critic.has_high_confidence(findings) is True


# ---- Retry stays bounded at one, even for this new finding shape ----

def test_process_ad_dispensing_finding_retries_at_most_once(monkeypatch, tmp_path):
    import uuid
    from src import pipeline, dedupe

    dedupe.init_db()
    dedupe.init_artifacts()
    ad_id = f"PIPE_{uuid.uuid4().hex[:8]}"
    ad = {"ad_id": ad_id, "page_name": "brand", "image_url": "http://x/img.jpg",
          "start_date": "", "destination_url": "", "text": "", "cta": "", "media_type": "IMAGE"}
    blueprint = {"format": "hero", "angle": "a", "objects": [
        _product_obj([0.1, 0.1, 0.2, 0.4]), _person_obj([0.5, 0.5, 0.4, 0.45]),
    ]}
    draft_path = tmp_path / "draft.png"
    draft_path.write_bytes(b"\x89PNG\r\n\x1a\nfakepngbytes")
    monkeypatch.setattr(pipeline.assets, "download_image", lambda url, aid: "fake.jpg")
    monkeypatch.setattr(pipeline.assets, "download_image_bytes", lambda url: b"fake-bytes")
    monkeypatch.setattr(pipeline.deconstruct, "deconstruct_image", lambda **k: blueprint)
    monkeypatch.setattr(pipeline.generate_copy, "generate_copy_live",
                        lambda bp, product=None, **k: {"headline": "H", "primary_text": "P",
                                                        "image_subtext": "S", "cta": "C"})
    monkeypatch.setattr(pipeline.compliance, "check_compliance", lambda copy, name, text, **k: (True, []))
    generate_image_calls = []
    monkeypatch.setattr(pipeline.generate_image_prompt, "generate_image",
                        lambda bp, aid, product=None, reference_images=None, **k: (
                            generate_image_calls.append(k) or str(draft_path)))
    monkeypatch.setattr(pipeline.slack_review, "post_review", lambda *a, **k: {"ts": "123"})
    monkeypatch.setattr(pipeline.dedupe, "save_artifact", lambda **k: None)
    monkeypatch.setattr(pipeline.dedupe, "update_artifact_findings", lambda *a, **k: None)
    monkeypatch.setattr(pipeline.output_critic, "check_draft", lambda *a, **k: [
        {"category": "dispensing/application directional consistency",
         "description": "always inconsistent", "confidence": "high"},
    ])

    result = pipeline.process_ad(ad, check_output=True)
    assert result == "processed"
    assert len(generate_image_calls) == 2  # original + exactly ONE corrective retry, never more
