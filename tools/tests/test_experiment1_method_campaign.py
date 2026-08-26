from pathlib import Path
from types import SimpleNamespace

import pytest

import tools.run_experiment1_method_campaign as campaign_runner
from tools.run_experiment1_rtl_acquisition import ExperimentError


def manifest_for(method_id: str) -> dict:
    return {
        "campaign_mode": "formal",
        "task_spec": {"path": "/frozen/task.json"},
        "batches": [
            {
                "method_id": method_id,
                "batch_id": batch,
                "status": "pending",
            }
            for batch in range(1, 5)
        ],
    }


def test_acquire_locks_four_batches_without_running_evaluator(tmp_path, monkeypatch):
    method_id = "qwen-vanilla"
    manifest = manifest_for(method_id)
    task = {
        "method_budget": {
            "vanilla_max_turns_per_batch": 100,
            "vanilla_max_output_tokens_per_turn": 4096,
        }
    }
    commands: list[list[str]] = []
    monkeypatch.setattr(
        campaign_runner,
        "read_json",
        lambda path: task if Path(path).name == "task.json" else manifest,
    )
    monkeypatch.setattr(campaign_runner, "verify_bound_campaign", lambda _value: None)

    def fake_run(command: list[str]) -> None:
        commands.append(command)
        if "accept-submission" in command:
            batch_id = int(Path(command[-1]).parent.name.split("batch", 1)[1].split(".", 1)[0])
            manifest["batches"][batch_id - 1]["status"] = "submitted"

    monkeypatch.setattr(campaign_runner, "run_checked", fake_run)
    campaign_runner.acquire(
        SimpleNamespace(
            campaign_root=tmp_path,
            method_id=method_id,
            cores=4,
            env_file=tmp_path / "api.env",
            max_turns=100,
            max_output_tokens=4096,
        )
    )
    assert sum("run_experiment1_vanilla_method.py" in " ".join(row) for row in commands) == 4
    assert sum("accept-submission" in row for row in commands) == 4
    assert not any("evaluate-batch" in row for row in commands)


def test_evaluate_refuses_partially_locked_method(tmp_path, monkeypatch):
    manifest = manifest_for("openai-vanilla")
    manifest["batches"][0]["status"] = "submitted"
    monkeypatch.setattr(campaign_runner, "read_json", lambda _path: manifest)
    monkeypatch.setattr(campaign_runner, "verify_bound_campaign", lambda _value: None)
    with pytest.raises(ExperimentError, match="all four"):
        campaign_runner.evaluate(
            SimpleNamespace(
                campaign_root=tmp_path,
                method_id="openai-vanilla",
                cores=4,
            )
        )
