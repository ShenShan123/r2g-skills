"""Unit boundaries for the real StateShift P13-to-P14 runner."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _module():
    path = Path(__file__).resolve().parents[1] / "scripts" / \
        "run_p14_state_shift_attribution.py"
    spec = importlib.util.spec_from_file_location("p14_state_shift", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_p14_self_digest_rejects_tampering():
    module = _module()
    payload = {"value": 1}
    payload["report_digest"] = module._digest(payload)
    assert module._self_digest(
        payload, "report_digest", "report") == payload["report_digest"]
    payload["value"] = 2
    with pytest.raises(module.P14StateShiftAttributionError, match="mismatch"):
        module._self_digest(payload, "report_digest", "report")


def test_p14_contexts_require_exact_admitted_unique_cases():
    module = _module()
    row = {
        "admitted_to_state_shift_bucket": True,
        "registration": {
            "challenge_id": "case-a", "current_context": {"fact": "a"}},
    }
    assert module._current_contexts({"cases": [row]}) == {
        "case-a": {"fact": "a"}}
    duplicate = {"cases": [row, row]}
    with pytest.raises(module.P14StateShiftAttributionError, match="duplicates"):
        module._current_contexts(duplicate)
    rejected = {"cases": [{**row, "admitted_to_state_shift_bucket": False}]}
    with pytest.raises(module.P14StateShiftAttributionError, match="admitted"):
        module._current_contexts(rejected)


def test_p14_runtime_binding_is_typed_and_complete():
    module = _module()
    payload = {
        "asset_id": "asset-a", "knowledge_id": "knowledge-a@2",
        "target_design": "design-a", "candidate_entities": ["X"],
        "selected_binding": {}, "structural_evidence": ["sha256:evidence"],
        "failure_evidence": [], "ambiguity_count": 0, "eligible": True,
        "reason": "fixed_training_config_delta",
        "binding_digest": "sha256:" + "a" * 64,
    }
    receipt = module._runtime_binding(payload)
    assert receipt.knowledge_id == "knowledge-a@2"
    assert receipt.to_dict() == payload
    incomplete = dict(payload)
    incomplete.pop("binding_digest")
    with pytest.raises(module.P14StateShiftAttributionError, match="incomplete"):
        module._runtime_binding(incomplete)


def test_p14_authority_boundary_stays_evaluation_only():
    module = _module()
    safe = {
        "evaluation_only": True, "canonical_memory_mutation": "none",
        "production_runtime_imported": False, "memory_docs_submitted": False,
    }
    assert module._closed(safe, "safe") is None
    for field, value in (
            ("evaluation_only", False),
            ("canonical_memory_mutation", "write"),
            ("production_runtime_imported", True),
            ("production_integration", "attempted"),
            ("memory_docs_submitted", True)):
        with pytest.raises(
                module.P14StateShiftAttributionError, match="authority boundary"):
            module._closed({**safe, field: value}, field)
