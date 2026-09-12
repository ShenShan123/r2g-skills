"""Binder boundary fixtures; mocked oracle replay is NOT empirical evidence."""
import copy
import json
from pathlib import Path

import pytest

from scripts import build_p13_interference_anti_forgetting_evidence as mod
from scripts.build_p13_interference_source_bound_plan import _digest, _sha256


def _write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    return {"path": str(path), "sha256": _sha256(path)}


def _signed(payload):
    payload = copy.deepcopy(payload)
    payload.pop("report_digest", None)
    payload["report_digest"] = _digest(payload)
    return payload


def _fixture(tmp_path, monkeypatch, passed=True):
    plan = {"path": "/fixture/plan.json", "sha256": "sha256:fixture", "report_digest": "sha256:plan"}
    freeze = _signed({"source_bound_plan": plan, "child_content_digest": "sha256:child"})
    freeze_ref = _write(tmp_path / "freeze.json", freeze)
    freeze_ref["report_digest"] = freeze["report_digest"]
    refs = {view: _write(tmp_path / "executions" / (view + "-report.json"), {"fixture": view})
            for view in mod.VIEWS}
    report = _signed({"version": "p13-interference-policy-execution-audit-v1", "purpose": "heldout",
                      "source_bound_plan": plan, "passed": passed, "policy_freeze": freeze_ref,
                      "executed_cohorts": refs, "receipt_id": "fixture-gate"})
    gate_path = tmp_path / "gate.json"
    _write(gate_path, report)
    calls = []
    def replay(policy_freeze, reports_dir, *, purpose, output):
        calls.append((policy_freeze, reports_dir, purpose, output))
        return copy.deepcopy(report)
    monkeypatch.setattr(mod, "audit_policy_execution", replay)
    return gate_path, report, plan, calls


@pytest.mark.parametrize("passed", [True, False])
def test_gate_status_is_taken_only_after_replay(tmp_path, monkeypatch, passed):
    path, _, plan, calls = _fixture(tmp_path, monkeypatch, passed)
    result, ref = mod._gate_evidence(path, purpose="heldout", plan_ref=plan,
                                   child_digest="sha256:child", replay_dir=tmp_path / "replay")
    assert result["passed"] is passed
    assert ref["receipt_id"] == "fixture-gate"
    assert len(calls) == 1 and calls[0][2] == "heldout"


def test_rehashed_caller_pass_boolean_cannot_override_cold_oracle(tmp_path, monkeypatch):
    path, report, plan, _ = _fixture(tmp_path, monkeypatch, False)
    changed = _signed({**report, "passed": True})
    _write(path, changed)
    with pytest.raises(mod.InterferenceAntiForgettingError, match="does not replay"):
        mod._gate_evidence(path, purpose="heldout", plan_ref=plan,
                           child_digest="sha256:child", replay_dir=tmp_path / "replay")


@pytest.mark.parametrize("purpose,plan_change,child", [
    ("target_replay", False, "sha256:child"),
    ("heldout", True, "sha256:child"),
    ("heldout", False, "sha256:other-child"),
])
def test_other_purpose_plan_or_child_is_not_same_mutation(tmp_path, monkeypatch, purpose, plan_change, child):
    path, _, plan, _ = _fixture(tmp_path, monkeypatch)
    if plan_change:
        plan = {**plan, "report_digest": "sha256:other-plan"}
    with pytest.raises(mod.InterferenceAntiForgettingError, match="mismatch|different child"):
        mod._gate_evidence(path, purpose=purpose, plan_ref=plan,
                           child_digest=child, replay_dir=tmp_path / "replay")


def test_incomplete_view_set_cannot_be_bound(tmp_path, monkeypatch):
    path, report, plan, _ = _fixture(tmp_path, monkeypatch)
    report["executed_cohorts"].pop("Mt_plus_delta_minus_delta")
    _write(path, _signed(report))
    with pytest.raises(mod.InterferenceAntiForgettingError, match="three real"):
        mod._gate_evidence(path, purpose="heldout", plan_ref=plan,
                           child_digest="sha256:child", replay_dir=tmp_path / "replay")


def test_mixed_execution_directories_rejected_before_oracle(tmp_path, monkeypatch):
    path, report, plan, calls = _fixture(tmp_path, monkeypatch)
    report["executed_cohorts"]["Mt_plus_delta_minus_delta"] = _write(
        tmp_path / "foreign" / "Mt_plus_delta_minus_delta-report.json", {"fixture": "foreign"})
    _write(path, _signed(report))
    with pytest.raises(mod.InterferenceAntiForgettingError, match="directory/view"):
        mod._gate_evidence(path, purpose="heldout", plan_ref=plan,
                           child_digest="sha256:child", replay_dir=tmp_path / "replay")
    assert not calls


def test_checker_file_drift_rejected_before_binding(tmp_path, monkeypatch):
    path, report, plan, _ = _fixture(tmp_path, monkeypatch)
    cohort = report["executed_cohorts"]["Mt"]
    Path(cohort["path"]).write_text("changed")
    with pytest.raises(ValueError, match="digest mismatch"):
        mod._gate_evidence(path, purpose="heldout", plan_ref=plan,
                           child_digest="sha256:child", replay_dir=tmp_path / "replay")


def test_previous_output_is_never_overwritten(tmp_path):
    output = tmp_path / "existing"
    output.mkdir()
    with pytest.raises(mod.InterferenceAntiForgettingError, match="must be new"):
        mod.build_interference_anti_forgetting_evidence("missing", "missing", "a", "b", "c", output_dir=output)


def test_alias_gate_files_cannot_count_as_independent_audits(tmp_path):
    gate = tmp_path / "gate.json"
    gate.write_text("{}")
    alias = tmp_path / "alias.json"
    alias.symlink_to(gate)
    with pytest.raises(mod.InterferenceAntiForgettingError, match="distinct"):
        mod.build_interference_anti_forgetting_evidence("missing", "missing", gate, alias, "c",
                                                      output_dir=tmp_path / "new")
    assert not (tmp_path / "new").exists()


def test_missing_inputs_produce_no_witness_or_authority(tmp_path):
    output = tmp_path / "new"
    with pytest.raises(ValueError):
        mod.build_interference_anti_forgetting_evidence("missing", "missing", "a", "b", "c", output_dir=output)
    assert not output.exists()


@pytest.mark.parametrize("changed_key", ["toolchain_digest", "oracle_digest", "candidate_budget",
                                        "utility_contract_digest"])
def test_comparison_gate_cannot_silently_change_training_pins(tmp_path, monkeypatch, changed_key):
    path, report, plan, _ = _fixture(tmp_path, monkeypatch)
    comparison = {key: 3 if key == "candidate_budget" else "fixture-" + key
                  for key in mod._COMPARISON_FIELDS}
    freeze_path = Path(report["policy_freeze"]["path"])
    freeze = json.loads(freeze_path.read_text())
    manifests = {}
    for view in mod.VIEWS:
        manifest = dict(comparison)
        if view == "Mt_plus_delta":
            manifest[changed_key] = 4 if changed_key == "candidate_budget" else "changed"
        manifests[view] = _write(tmp_path / view / "manifest.json", manifest)
    freeze["policy_manifests"] = manifests
    freeze = _signed(freeze)
    report["policy_freeze"] = {**_write(freeze_path, freeze), "report_digest": freeze["report_digest"]}
    report = _signed(report)
    _write(path, report)
    monkeypatch.setattr(mod, "audit_policy_execution", lambda *args, **kwargs: copy.deepcopy(report))
    with pytest.raises(mod.InterferenceAntiForgettingError, match="training toolchain/oracle/budget/objective"):
        mod._gate_evidence(path, purpose="heldout", plan_ref=plan, child_digest="sha256:child",
                           replay_dir=tmp_path / "replay", comparison_pins=comparison)


@pytest.mark.parametrize("non_target_passed", [True, False])
def test_witness_glue_preserves_failed_gate_and_never_creates_update(tmp_path, monkeypatch, non_target_passed):
    source = tmp_path / "source.fixture"
    source.write_bytes(b"unchanged fixture, not an actual SQLite replay")
    source_sha = _sha256(source)
    manifest_path = tmp_path / "training-manifest.json"
    manifest_ref = _write(manifest_path, {key: 3 if key == "candidate_budget" else "fixture-" + key
                                        for key in mod._COMPARISON_FIELDS})
    bundle_ref = _write(tmp_path / "bundle.json", {"manifest": str(manifest_path),
                                                  "manifest_sha256": manifest_ref["sha256"]})
    plan = _signed({"campaign_id": "interference-fixture", "physical_utility_contract_bound": True,
                    "reason_bundle": bundle_ref,
                    "source_database": {"path": str(source), "sha256": source_sha,
                                        "logical_digest": "sha256:fixture-logical"}})
    plan_path = tmp_path / "plan.json"
    plan_ref = {**_write(plan_path, plan), "report_digest": plan["report_digest"]}
    preflight = _signed({"source_bound_plan": plan_ref, "preflight_passed": True,
                         "child_content_digest": "sha256:fixture-child", "raw_evidence_preserved": True,
                         "source_database_unchanged": True, "staging_discarded": True,
                         "case_routes": {"fixture-case": {"before_route": {"decision": "CONSIDER"}}},
                         "rollback_routes": {"fixture-case": {"decision": "CONSIDER"}}})
    preflight_path = tmp_path / "preflight.json"
    _write(preflight_path, preflight)
    gate_paths = [tmp_path / (purpose + ".json") for purpose in ("target_replay", "non_target", "heldout")]
    for gate in gate_paths:
        _write(gate, {"fixture": gate.stem})
    calls = []
    def gate_oracle(path, *, purpose, **kwargs):
        calls.append(purpose)
        return {"passed": non_target_passed if purpose == "non_target" else True}, {
            **mod._pin(path), "receipt_id": "fixture-" + purpose}
    monkeypatch.setattr(mod, "_gate_evidence", gate_oracle)
    monkeypatch.setattr(mod, "audit_shadow_view", lambda *args, **kwargs: copy.deepcopy(preflight))
    report = mod.build_interference_anti_forgetting_evidence(
        plan_path, preflight_path, *gate_paths, output_dir=tmp_path / "output")
    assert report["eligible"] is non_target_passed
    assert report["gate_passed"]["non_target"] is non_target_passed
    witness = json.loads((tmp_path / "output/anti-forgetting-witness.json").read_text())
    assert witness["witness"]["non_target_regression_free"] is non_target_passed
    assert report["applied_shadow_update_receipt_created"] is False
    assert report["promotion_attempted"] is False
    assert _sha256(source) == source_sha
    assert calls == ["target_replay", "non_target", "heldout"]
