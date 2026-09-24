"""One-shot selected-treatment ledger and no-overwrite guards."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tehm.evaluation import research_s1_treatment as treatment


def test_treatment_event_chain_is_content_addressed(tmp_path: Path) -> None:
    ledger = tmp_path / "attempt-events.jsonl"
    first = treatment._append_event(
        ledger, None, 1, {"event": "STARTED", "design_id": "a"})
    second = treatment._append_event(
        ledger, first, 2, {"event": "TERMINAL", "design_id": "a"})
    rows = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert rows[0]["event_digest"] == first
    assert rows[1]["previous_digest"] == first
    assert rows[1]["event_digest"] == second


def test_treatment_runner_refuses_existing_output(tmp_path: Path) -> None:
    with pytest.raises(treatment.ResearchS1TreatmentError, match="overwrite"):
        treatment.run_s1_treatments(
            plan=tmp_path / "missing-plan", epoch=tmp_path / "missing-epoch",
            output=tmp_path)


def test_treatment_verifier_rejects_changed_execution_binding(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan = tmp_path / "plan"
    plan.mkdir()
    (plan / "treatment-plan.json").write_text(
        json.dumps({"plan_digest": "sha256:plan", "preflight_path": "unused"}),
        encoding="utf-8")
    monkeypatch.setattr(treatment, "verify_s1_treatment_plan",
                        lambda *args, **kwargs: {"valid": True,
                                                 "plan_digest": "sha256:plan"})
    output = tmp_path / "output"
    output.mkdir()
    (output / "execution-binding.json").write_text(
        json.dumps({"schema": "tehm-r4-s1-treatment-execution-binding-v1",
                    "binding_digest": "sha256:wrong"}), encoding="utf-8")
    with pytest.raises(treatment.ResearchS1TreatmentError,
                       match="execution binding changed"):
        treatment.verify_s1_treatments(plan=plan, output=output)
