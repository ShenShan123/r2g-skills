from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import tehm.evaluation.research_ledger as ledger_module
from tehm.evaluation.research_ledger import (
    ResearchLedgerError,
    append_attempt_event,
    artifact_reference,
    audit_attempt_ledger,
    verify_attempt_ledger,
    verify_research_audit,
)


ZERO_DIGEST = "sha256:" + "0" * 64


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _prepared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, retry_limit: int = 0,
              retryable: list[str] | None = None) -> Path:
    root = tmp_path / "prepared"
    campaign = {
        "campaign_id": "campaign-1",
        "prepared_campaign_digest": "sha256:prepared",
        "task_ids": ["task-1"],
        "policies": ["no_persistent_memory"],
        "budget": {
            "candidate_limit": 1,
            "eda_call_limit": 3,
            "model_call_limit": 0,
            "model_token_limit": 0,
            "wallclock_limit_seconds": 100,
            "retry_limit": retry_limit,
            "retryable_classes": retryable or [],
        },
    }
    context = {
        "task_id": "task-1",
        "contract": {
            "required_checks": ["source_integrity", "elaboration", "synthesis"],
            "optional_checks": [],
            "eligible_for_repair_metric": False,
        },
    }
    _write(root / "prepared-campaign.json", campaign)
    _write(root / "task-contexts/task-1.json", context)
    monkeypatch.setattr(ledger_module, "verify_prepared_campaign", lambda path: {
        "valid": True, "campaign_id": "campaign-1",
        "prepared_campaign_digest": "sha256:prepared",
    })
    return root


def _append(prepared: Path, ledger: Path, event_type: str, payload: dict,
            *, attempt_id: str = "attempt-1") -> dict:
    return append_attempt_event(
        prepared=prepared, ledger=ledger, event_type=event_type,
        task_id="task-1", policy="no_persistent_memory",
        attempt_id=attempt_id, payload=payload,
        timestamp="2026-09-18T00:00:00.000000+00:00",
    )


def _start(prepared: Path, ledger: Path, *, attempt_id: str = "attempt-1",
           retry_of: str | None = None) -> None:
    _append(prepared, ledger, "ATTEMPT_STARTED",
            {} if retry_of is None else {"retry_of": retry_of}, attempt_id=attempt_id)


def _tool(prepared: Path, ledger: Path, raw: Path, *, attempt_id: str = "attempt-1") -> None:
    _append(prepared, ledger, "TOOL_RESULT", {
        "tool_name": "yosys",
        "call_kind": "EDA",
        "command_digest": ZERO_DIGEST,
        "result_digest": "sha256:" + hashlib.sha256(raw.read_bytes()).hexdigest(),
        "toolchain_digest": ZERO_DIGEST,
        "exit_code": 0,
        "wallclock_seconds": 1.5,
        "raw_artifacts": [artifact_reference(raw)],
    }, attempt_id=attempt_id)


def _check(prepared: Path, ledger: Path, raw: Path, name: str, verdict: str,
           *, attempt_id: str = "attempt-1") -> None:
    _append(prepared, ledger, "CHECK_RECORDED", {
        "check_name": name,
        "verdict": verdict,
        "scope": {"kind": "frontend_synthesis"},
        "raw_report_refs": [artifact_reference(raw)] if verdict != "UNKNOWN" else [],
    }, attempt_id=attempt_id)


def _terminal(prepared: Path, ledger: Path, *, attempt_id: str = "attempt-1",
              status: str = "COMPLETED", exception: dict | None = None,
              eda_calls: int = 1) -> None:
    _append(prepared, ledger, "ATTEMPT_TERMINAL", {
        "execution_status": status,
        "terminal_exception": exception,
        "actual_cost": {
            "eda_calls": eda_calls,
            "model_calls": 0,
            "model_tokens": 0,
            "wallclock_seconds": 2.0,
        },
    }, attempt_id=attempt_id)


def test_append_chain_and_independent_pass_audit(tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    prepared = _prepared(tmp_path, monkeypatch)
    ledger = tmp_path / "attempts/ledger.jsonl"
    raw = tmp_path / "raw/yosys.log"
    raw.parent.mkdir()
    raw.write_text("synthesis ok\n", encoding="utf-8")
    _start(prepared, ledger)
    _tool(prepared, ledger, raw)
    for name in ("source_integrity", "elaboration", "synthesis"):
        _check(prepared, ledger, raw, name, "PASS")
    _terminal(prepared, ledger)

    verified = verify_attempt_ledger(prepared=prepared, ledger=ledger)
    assert verified["event_count"] == 6
    assert verified["terminal_attempt_count"] == 1
    result = audit_attempt_ledger(
        prepared=prepared, ledger=ledger, output=tmp_path / "audit")
    assert result["all_registered_terminal"] is True
    assert result["oracle_verdict_counts"] == {"PASS": 1}
    audited = json.loads((tmp_path / "audit/audited-attempts.json").read_text())
    assert audited["producer_success_fields_trusted"] is False
    assert audited["attempts"][0]["oracle_reason"] == "all_required_checks_passed"
    projected = verify_research_audit(tmp_path / "audit")
    assert projected["valid"] is True
    assert projected["registered_case_count"] == 1


def test_missing_report_is_unknown_and_stays_in_denominator(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepared = _prepared(tmp_path, monkeypatch)
    ledger = tmp_path / "ledger.jsonl"
    raw = tmp_path / "raw.log"
    raw.write_text("partial\n", encoding="utf-8")
    _start(prepared, ledger)
    _tool(prepared, ledger, raw)
    _check(prepared, ledger, raw, "source_integrity", "PASS")
    _terminal(prepared, ledger)
    result = audit_attempt_ledger(
        prepared=prepared, ledger=ledger, output=tmp_path / "audit")
    assert result["registered_case_count"] == 1
    assert result["oracle_verdict_counts"] == {"UNKNOWN": 1}


def test_terminal_cost_and_producer_oracle_are_fail_closed(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepared = _prepared(tmp_path, monkeypatch)
    ledger = tmp_path / "ledger.jsonl"
    _start(prepared, ledger)
    with pytest.raises(ResearchLedgerError, match="EDA call count"):
        _terminal(prepared, ledger, eda_calls=1)
    with pytest.raises(ResearchLedgerError, match="producer cannot"):
        _append(prepared, ledger, "ATTEMPT_TERMINAL", {
            "execution_status": "COMPLETED",
            "terminal_exception": None,
            "producer_oracle_verdict": "PASS",
            "actual_cost": {"eda_calls": 0, "model_calls": 0,
                            "model_tokens": 0, "wallclock_seconds": 0},
        })


def test_retry_requires_linked_retryable_terminal(tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    prepared = _prepared(
        tmp_path, monkeypatch, retry_limit=1,
        retryable=["infrastructure_transient"],
    )
    ledger = tmp_path / "ledger.jsonl"
    _start(prepared, ledger)
    _terminal(prepared, ledger, status="TOOL_ERROR",
              exception={"class": "infrastructure_transient", "detail": "worker lost"},
              eda_calls=0)
    with pytest.raises(ResearchLedgerError, match="declare retry_of"):
        _start(prepared, ledger, attempt_id="attempt-2")
    _start(prepared, ledger, attempt_id="attempt-2", retry_of="attempt-1")
    _terminal(prepared, ledger, attempt_id="attempt-2", eda_calls=0)
    assert verify_attempt_ledger(prepared=prepared, ledger=ledger)["attempt_count"] == 2


def test_chain_and_raw_evidence_tamper_are_detected(tmp_path: Path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    prepared = _prepared(tmp_path, monkeypatch)
    ledger = tmp_path / "ledger.jsonl"
    raw = tmp_path / "raw.log"
    raw.write_text("original\n", encoding="utf-8")
    _start(prepared, ledger)
    _tool(prepared, ledger, raw)
    _terminal(prepared, ledger)
    raw.write_text("changed\n", encoding="utf-8")
    with pytest.raises(ResearchLedgerError, match="raw evidence drifted"):
        audit_attempt_ledger(prepared=prepared, ledger=ledger, output=tmp_path / "audit")

    lines = ledger.read_text(encoding="utf-8").splitlines()
    event = json.loads(lines[0])
    event["task_id"] = "tampered"
    lines[0] = json.dumps(event)
    ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ResearchLedgerError, match="event digest mismatch"):
        verify_attempt_ledger(prepared=prepared, ledger=ledger)
