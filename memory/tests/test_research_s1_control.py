"""Bounded unit checks for the S1 control-only acquisition lane."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tehm.evaluation import research_s1_control as s1


def test_control_event_chain_is_content_addressed(tmp_path: Path) -> None:
    ledger = tmp_path / "attempt-events.jsonl"
    first = s1._event(ledger, None, 1, {"event": "STARTED", "design_id": "a"})
    second = s1._event(ledger, first, 2, {"event": "TERMINAL", "design_id": "a"})
    rows = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert rows[0]["event_digest"] == first
    assert rows[1]["previous_digest"] == first
    assert rows[1]["event_digest"] == second


def test_control_runner_refuses_existing_output_before_execution(tmp_path: Path) -> None:
    with pytest.raises(s1.ResearchS1ControlError, match="overwrite"):
        s1.run_s1_controls(binding=tmp_path / "missing.json", output=tmp_path)


def test_control_binding_rejects_changed_task_digest(tmp_path: Path) -> None:
    task = tmp_path / "task.json"
    task.write_text('{"schema":"wrong"}\n', encoding="utf-8")
    binding = tmp_path / "binding.json"
    binding.write_text(json.dumps({"schema": s1.BINDING_SCHEMA,
                                   "task_spec": str(task),
                                   "task_spec_sha256": "not-the-digest"}),
                       encoding="utf-8")
    with pytest.raises(s1.ResearchS1ControlError, match="task spec digest"):
        s1.verify_s1_control_binding(binding)


def test_control_verifier_rejects_incomplete_denominator(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(s1, "verify_s1_control_binding", lambda *args, **kwargs: {
        "tasks": [{"design_id": "a"}, {"design_id": "b"}]})
    (tmp_path / "attempt-events.jsonl").write_text("", encoding="utf-8")
    with pytest.raises(s1.ResearchS1ControlError, match="denominator"):
        s1.verify_s1_controls(binding=tmp_path / "binding.json", output=tmp_path)
