from pathlib import Path
import threading
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
            for batch in (1,)
        ],
    }


def test_acquire_locks_one_continuous_run_without_running_evaluator(tmp_path, monkeypatch):
    method_id = "qwen-vanilla"
    manifest = manifest_for(method_id)
    task = {
        "method_budget": {
            "vanilla_max_turns_per_run": 1600,
            "vanilla_max_output_tokens_per_turn": 4096,
        },
        "batch_policy": {"batches_per_method": 1, "target_candidates_per_batch": 200},
    }
    commands: list[list[str]] = []
    monkeypatch.setattr(
        campaign_runner,
        "read_json",
        lambda path: task if Path(path).name == "task.json" else manifest,
    )
    monkeypatch.setattr(campaign_runner, "verify_bound_campaign", lambda _value: None)
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")

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
            max_turns=1600,
            max_output_tokens=4096,
        )
    )
    assert sum("run_experiment1_vanilla_method.py" in " ".join(row) for row in commands) == 1
    assert sum("accept-submission" in row for row in commands) == 1
    assert any("--target" in row and "200" in row for row in commands)
    assert not any("evaluate-batch" in row for row in commands)


def test_formal_acquire_requires_authenticated_github(tmp_path, monkeypatch):
    manifest = manifest_for("qwen-vanilla")
    monkeypatch.setattr(campaign_runner, "read_json", lambda _path: manifest)
    monkeypatch.setattr(campaign_runner, "verify_bound_campaign", lambda _value: None)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    with pytest.raises(ExperimentError, match="authenticated GITHUB_TOKEN"):
        campaign_runner.acquire(
            SimpleNamespace(
                campaign_root=tmp_path,
                method_id="qwen-vanilla",
                cores=4,
                env_file=tmp_path / "api.env",
                max_turns=1600,
                max_output_tokens=4096,
            )
        )


def test_campaign_acquisition_lease_serializes_methods(tmp_path):
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()

    def first_method():
        with campaign_runner.campaign_acquisition_lease(tmp_path, "first"):
            first_entered.set()
            assert release_first.wait(timeout=2)

    def second_method():
        with campaign_runner.campaign_acquisition_lease(tmp_path, "second"):
            second_entered.set()

    first = threading.Thread(target=first_method)
    second = threading.Thread(target=second_method)
    first.start()
    assert first_entered.wait(timeout=1)
    second.start()
    assert not second_entered.wait(timeout=0.1)
    release_first.set()
    assert second_entered.wait(timeout=1)
    first.join(timeout=1)
    second.join(timeout=1)
    assert not first.is_alive()
    assert not second.is_alive()


def test_evaluate_refuses_unlocked_continuous_run(tmp_path, monkeypatch):
    manifest = manifest_for("openai-vanilla")
    task = {"batch_policy": {"batches_per_method": 1}}
    monkeypatch.setattr(
        campaign_runner,
        "read_json",
        lambda path: task if Path(path).name == "task.json" else manifest,
    )
    manifest["task_spec"] = {"path": "/frozen/task.json"}
    monkeypatch.setattr(campaign_runner, "verify_bound_campaign", lambda _value: None)
    with pytest.raises(ExperimentError, match="submission"):
        campaign_runner.evaluate(
            SimpleNamespace(
                campaign_root=tmp_path,
                method_id="openai-vanilla",
                cores=4,
            )
        )
