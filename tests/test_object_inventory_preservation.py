"""Bug 2 fix (2026-08-21): generated images introducing or removing reference objects.
Preserve the reference's non-product object inventory - no invented foreground/
midground objects, no accidental duplicates, no accidental removal of meaningful
reference objects. Product substitution (the competitor product intentionally
replaced by the selected Besque product) must remain allowed and never be treated as
an object-inventory violation.

Smallest production-safe fix using the EXISTING deconstruction/object information as
the source of truth - no new object-detection architecture, no object-level editing/
removal. Three parts:
  1. output_critic.reference_object_inventory derives the reference's own meaningful
     non-product objects (kind in person/prop/surface/graphic, disposition=="keep")
     straight from blueprint.objects - no new detection.
  2. _build_user_prompt/check_draft thread this list to the critic as an explicit
     REFERENCE OBJECT INVENTORY checklist category, extending the SAME critic -
     distinguishing missing/unexpected/duplicate/allowed-product-substitution.
  3. generate_image_prompt._OBJECT_CLOSURE_SENTENCE (already the generation-side
     "no invented objects" constraint) is strengthened to also ban duplicates.

Gated through the EXISTING has_high_confidence()/MAX_IMAGE_ATTEMPTS=2 bounded-retry
mechanism - no second critic, no new retry loop."""
from src import output_critic


def _obj(kind, disposition, description, object_id="obj_01", required_in_output=None):
    o = {"object_id": object_id, "kind": kind, "disposition": disposition, "description": description}
    if required_in_output is not None:
        o["required_in_output"] = required_in_output
    return o


# ---- reference_object_inventory: pure data extraction from existing objects[] ----

def test_reference_object_inventory_extracts_kept_non_product_objects():
    objects = [
        _obj("prop", "keep", "wooden tray", object_id="obj_tray"),
        _obj("surface", "keep", "marble countertop", object_id="obj_counter"),
        _obj("person", "keep", "woman applying oil to her legs", object_id="obj_person"),
        _obj("product", "substitute", "competitor's oil bottle", object_id="obj_product"),
        _obj("text", "keep", "headline text block", object_id="obj_text"),
    ]
    inventory = output_critic.reference_object_inventory(objects)
    assert "wooden tray" in inventory
    assert "marble countertop" in inventory
    assert "woman applying oil to her legs" in inventory
    # product and text are never part of the non-product inventory
    assert not any("competitor" in i for i in inventory)
    assert not any("headline" in i for i in inventory)
    assert len(inventory) == 3


def test_reference_object_inventory_excludes_dropped_and_substitute_props():
    objects = [
        _obj("prop", "drop", "competitor's applicator tool", object_id="obj_1"),
        _obj("graphic", "substitute", "competitor's badge graphic", object_id="obj_2"),
    ]
    assert output_critic.reference_object_inventory(objects) == []


def test_reference_object_inventory_excludes_not_required_objects():
    objects = [_obj("prop", "keep", "a stray hair strand", object_id="obj_1", required_in_output=False)]
    assert output_critic.reference_object_inventory(objects) == []


def test_reference_object_inventory_empty_for_product_only_reference():
    objects = [_obj("product", "substitute", "the competitor's bottle", object_id="obj_1")]
    assert output_critic.reference_object_inventory(objects) == []


def test_reference_object_inventory_falls_back_to_object_id_when_no_description():
    objects = [{"object_id": "obj_tray", "kind": "prop", "disposition": "keep"}]
    assert output_critic.reference_object_inventory(objects) == ["obj_tray"]


# ---- _build_user_prompt: product substitution explicitly allowed, never a violation ----

def test_build_user_prompt_states_reference_object_inventory():
    prompt = output_critic._build_user_prompt(
        "rules", reference_objects=["wooden tray", "marble countertop"],
    )
    assert "wooden tray" in prompt
    assert "marble countertop" in prompt
    assert "REFERENCE OBJECT INVENTORY" in prompt


def test_build_user_prompt_states_product_substitution_never_a_violation():
    prompt = output_critic._build_user_prompt("rules", reference_objects=["wooden tray"])
    assert "product substitution is always allowed and is never a violation" in prompt


def test_build_user_prompt_omits_inventory_section_when_none_supplied():
    """Every pre-existing caller (no reference_objects kwarg) must be byte-for-byte
    unaffected - no empty section, no stray heading."""
    prompt = output_critic._build_user_prompt("rules")
    assert "REFERENCE OBJECT INVENTORY" not in prompt
    prompt_empty_list = output_critic._build_user_prompt("rules", reference_objects=[])
    assert "REFERENCE OBJECT INVENTORY" not in prompt_empty_list


def test_check_draft_forwards_reference_objects_to_the_prompt(monkeypatch):
    import json as _json

    captured = {}

    class _CapturingMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return type("M", (), {"content": [type("C", (), {"text": _json.dumps({"violations": []})})()]})()

    class _CapturingClient:
        def __init__(self, *a, **k):
            self.messages = _CapturingMessages()

    monkeypatch.setattr(output_critic.anthropic, "Anthropic", _CapturingClient)
    output_critic.check_draft(
        b"\x89PNG\r\n\x1a\ndraft", "rules", reference_objects=["a wooden tray", "a marble counter"],
    )
    text_content = next(c for c in captured["messages"][0]["content"] if c["type"] == "text")
    assert "a wooden tray" in text_content["text"]
    assert "a marble counter" in text_content["text"]


# ---- Critic checklist: the four shapes, and the texture/lighting exclusion ----

def test_critic_system_has_reference_object_inventory_category_with_four_shapes():
    system = output_critic.CRITIC_SYSTEM
    assert "REFERENCE OBJECT INVENTORY" in system
    assert "MISSING" in system
    assert "UNEXPECTED" in system
    assert "DUPLICATE" in system
    assert "ALLOWED PRODUCT SUBSTITUTION" in system
    assert "NEVER a violation" in system


def test_critic_system_excludes_background_texture_lighting_from_object_check():
    system = output_critic.CRITIC_SYSTEM
    assert "Do NOT report background" in system
    assert "shadows, reflections" in system
    assert "natural photographic variation" in system
    assert "semantically meaningful, nameable objects only" in system


def test_reference_object_inventory_category_not_forced_high_confidence():
    """Deliberate choice, same reasoning as Bug 1's dispensing category - a brand-new
    check with no confirmed live-shipped-bug history yet, left to the critic's own
    per-instance judgement rather than forced high by default."""
    assert not any("object inventory" in c.lower() for c in output_critic.HIGH_CONFIDENCE_BY_DEFAULT)


def test_has_high_confidence_gates_on_unexpected_object_finding():
    findings = [{"category": "unexpected generated object", "description": "an invented lamp appears",
                 "confidence": "high"}]
    assert output_critic.has_high_confidence(findings) is True


def test_has_high_confidence_gates_on_missing_object_finding():
    findings = [{"category": "missing reference object", "description": "the wooden tray is gone",
                 "confidence": "high"}]
    assert output_critic.has_high_confidence(findings) is True


def test_has_high_confidence_does_not_gate_on_allowed_product_substitution():
    """A finding correctly shaped as the allowed exception should never be reported
    as high-confidence by a well-behaved critic in the first place - this test
    documents the expected non-gating outcome IF one somehow arrived at low/medium,
    the same treatment every other category gets."""
    findings = [{"category": "allowed product substitution", "description": "besque bottle replaces competitor's",
                 "confidence": "low"}]
    assert output_critic.has_high_confidence(findings) is False


# ---- Generation-side reinforcement: the object closure sentence bans duplicates too ----

def test_object_closure_sentence_bans_duplicate_instances():
    from src import generate_image_prompt as gip
    assert "EXACTLY ONCE" in gip._OBJECT_CLOSURE_SENTENCE
    assert "duplicate instance" in gip._OBJECT_CLOSURE_SENTENCE


# ---- Retry stays bounded at one, even for this new finding shape ----

def test_process_ad_object_inventory_finding_retries_at_most_once(monkeypatch, tmp_path):
    import uuid
    from src import pipeline, dedupe

    dedupe.init_db()
    dedupe.init_artifacts()
    ad_id = f"PIPE_{uuid.uuid4().hex[:8]}"
    ad = {"ad_id": ad_id, "page_name": "brand", "image_url": "http://x/img.jpg",
          "start_date": "", "destination_url": "", "text": "", "cta": "", "media_type": "IMAGE"}
    blueprint = {"format": "hero", "angle": "a", "objects": [
        _obj("prop", "keep", "wooden tray", object_id="obj_tray"),
        _obj("product", "substitute", "competitor bottle", object_id="obj_product"),
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
    captured_reference_objects = []

    def fake_check_draft(*a, **k):
        captured_reference_objects.append(k.get("reference_objects"))
        return [{"category": "missing reference object", "description": "the wooden tray never appears",
                 "confidence": "high"}]
    monkeypatch.setattr(pipeline.output_critic, "check_draft", fake_check_draft)

    result = pipeline.process_ad(ad, check_output=True)
    assert result == "processed"
    assert len(generate_image_calls) == 2  # original + exactly ONE corrective retry, never more
    # the pipeline actually threaded THIS blueprint's own inventory through, both attempts
    assert captured_reference_objects == [["wooden tray"], ["wooden tray"]]
