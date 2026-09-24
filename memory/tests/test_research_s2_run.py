"""S2 controller budget and append-only execution semantics."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tehm.evaluation import research_s2_run as subject


def _candidate(project: Path, design: str, policy: str, index: int,
               source: str) -> dict:
    return {"design_id": design, "policy": policy, "candidate_index": index,
            "candidate_digest": "sha256:" + str(index) * 64,
            "candidate_source": source, "project": str(project),
            "control_audit_digest": "sha256:" + "a" * 64,
            "candidate_stage_receipt_digest": "sha256:" + "b" * 64}


def test_groups_require_all_policies_and_cold_fallback(tmp_path):
    rows = [
        _candidate(tmp_path / "no", "d", "no_persistent_memory", 1, "cold_start"),
        _candidate(tmp_path / "legacy", "d", "legacy_memory", 1, "cold_start"),
        _candidate(tmp_path / "tehm", "d", "tehm", 1, "tehm"),
        _candidate(tmp_path / "fallback", "d", "tehm", 2, "cold_start"),
    ]
    plan = {"staged_candidates": rows, "candidate_limit": 3,
            "registered_policy_tasks": 3}
    assert len(subject._groups(plan)) == 3
    with pytest.raises(subject.ResearchS2RunError):
        subject._groups({**plan, "registered_policy_tasks": 4})
    with pytest.raises(subject.ResearchS2RunError):
        subject._groups({**plan, "staged_candidates": rows[:-1]})


def test_event_chain_detects_tampering(tmp_path):
    ledger = tmp_path / "attempt-events.jsonl"
    first = subject._event(ledger, None, 1, {
        "event": "POLICY_STARTED", "design_id": "d",
        "policy": "no_persistent_memory"})
    events, tail = subject._events(ledger)
    assert len(events) == 1 and tail == first
    ledger.write_text(ledger.read_text().replace(
        "no_persistent_memory", "tehm"), encoding="utf-8")
    with pytest.raises(subject.ResearchS2RunError, match="hash chain"):
        subject._events(ledger)


def test_same_controller_attempts_are_isolated_and_stop_on_pass(
        monkeypatch, tmp_path):
    plan_root = tmp_path / "plan"
    plan_root.mkdir()
    output = tmp_path / "run"
    project_root = tmp_path / "projects"
    rows = []
    for policy in subject.POLICIES:
        if policy == "tehm":
            rows.append(_candidate(project_root / "tehm1", "d", policy, 1, "tehm"))
            rows.append(_candidate(project_root / "tehm2", "d", policy, 2, "cold_start"))
        else:
            rows.append(_candidate(project_root / policy, "d", policy, 1,
                                   "cold_start"))
    for row in rows:
        (Path(row["project"]) / "backend").mkdir(parents=True)
    saved = {"plan_digest": "sha256:" + "c" * 64,
             "staged_candidates": rows, "candidate_limit": 3,
             "registered_policy_tasks": 3}
    (plan_root / "stage-plan.json").write_text(
        json.dumps(saved), encoding="utf-8")
    monkeypatch.setattr(subject, "verify_s2_actions",
                        lambda path: {"plan_digest": saved["plan_digest"]})
    monkeypatch.setattr(subject, "_epoch", lambda plan, epoch: {
        "epoch_digest": "sha256:" + "d" * 64,
        "memory_bundle_digest": "sha256:" + "e" * 64})
    monkeypatch.setattr(subject, "_environment", lambda plan, epoch: (
        Path("/unused"), {"R2G_MEMORY_BACKEND": "none"}, 50,
        {"candidate_limit": 3, "eda_call_limit": 6,
         "wallclock_limit_seconds": 7200}))
    monkeypatch.setattr(subject, "verify_staged_flow_project",
                        lambda project: {"valid": True})
    monkeypatch.setattr(subject, "verify_s2_pilot",
                        lambda **kwargs: {"valid": True,
                                          "all_registered_terminal": True})
    seen = []

    def fake_execute(script, project, variant, environment, log, timeout):
        assert environment["R2G_MEMORY_BACKEND"] == "none"
        assert environment["R2G_JOURNAL_DB"].startswith(str(output))
        seen.append((str(project), environment["R2G_JOURNAL_DB"]))
        (project / "backend" / "RUN_1").mkdir()
        log.write_text("fake runner log\n", encoding="utf-8")
        return 0, None

    def fake_audit(*, project, run_dir, producer_epoch, auditor_epoch, output):
        verdict = "FAIL" if project.name == "tehm1" else "PASS"
        return {"oracle_verdict": verdict,
                "oracle_reason": "fake", "failure_layer": "FLOW_TARGET_FAILURE",
                "audit_digest": "sha256:" + "f" * 64,
                "actual_cost": {"eda_stage_calls": 3, "model_calls": 0,
                                "stage_wallclock_seconds": 1}}

    monkeypatch.setattr(subject, "_execute", fake_execute)
    monkeypatch.setattr(subject, "audit_flow_run", fake_audit)
    result = subject.run_s2_pilot(
        plan=plan_root, epoch=tmp_path / "epoch", output=output)
    assert result["valid"] is True
    assert len(seen) == 4
    assert len({journal for _, journal in seen}) == 4
    events, tail = subject._events(output / "attempt-events.jsonl")
    assert tail == result["event_tail_digest"]
    terminal = [event for event in events if event["event"] == "POLICY_TERMINAL"]
    assert len(terminal) == 3
    assert all(event["oracle_verdict"] == "PASS" for event in terminal)
    assert all(event["stop_reason"] == "success" for event in terminal)
