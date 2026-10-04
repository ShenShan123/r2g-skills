import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace


REPO = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO / "tools/run_experiment3_campaign.py"
SPEC = importlib.util.spec_from_file_location("run_experiment3_campaign", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def _campaign(tmp_path, status="frozen"):
    limits = tmp_path / "limits.json"
    limits.write_text(
        json.dumps(
            {
                "status": status,
                "a_propose": {
                    "rounds": 3,
                    "api_calls_per_model_per_domain": 3,
                    "raw_proposals_per_call": 2,
                    "max_output_tokens_per_call": 2048,
                    "total_tokens_per_model": 60000,
                    "pooled_executions_per_domain_per_round": 4,
                    "active_candidates_per_domain": 8,
                },
                "pure_llm": {
                    "api_calls_per_task": 3,
                    "max_output_tokens_per_call": 2048,
                    "total_tokens_per_model_per_task": 20000,
                },
                "eda": {
                    "concurrent_trials_initial": 2,
                    "concurrent_trials_conditional_max": 3,
                    "cores_per_trial": 4,
                    "timeout_seconds_per_trial": 7200,
                },
            }
        ),
        encoding="utf-8",
    )
    root = tmp_path / "campaign"
    root.mkdir()
    args = SimpleNamespace(
        campaign_root=root,
        runtime_env=None,
        api_env_file=None,
        eda_host=None,
        remote_repo=None,
        remote_python=Path("/usr/bin/python3"),
        eda_workers=None,
        routes=tmp_path / "routes.json",
        resource_limits=limits,
        action_policy=tmp_path / "action_policy.json",
        a_propose_prompt=tmp_path / "a_propose_prompt.md",
        pure_llm_prompt=tmp_path / "pure_llm_prompt.md",
    )
    return MODULE.Campaign(args)


def test_status_can_inspect_pending_campaign(tmp_path):
    campaign = _campaign(tmp_path, status="pending_user_freeze")
    assert campaign.status()["resource_status"] == "pending_user_freeze"
    try:
        campaign.require_frozen_resources()
    except ValueError as exc:
        assert "frozen" in str(exc)
    else:
        raise AssertionError("pending campaign was executable")


def test_matrix_command_has_one_worker_option_and_matching_cpu_sets(tmp_path):
    campaign = _campaign(tmp_path)
    command = campaign.matrix_base(workers=1)
    assert command.count("--workers") == 1
    assert command[command.index("--workers") + 1] == "1"
    assert command.count("--cpu-set") == 1
    assert "160-163" in command


def test_distributed_matrix_rewrites_only_runner_and_pins_remote_env(tmp_path):
    campaign = _campaign(tmp_path)
    campaign.eda_host = "memlab203"
    campaign.remote_repo = Path("/remote/runtime")
    campaign.remote_python = Path("/remote/python3")
    campaign.runtime_env = Path("/shared/campaign/runtime.env")
    command = campaign.matrix_base(workers=1)
    remote = campaign._remote_matrix_command(command)
    assert remote[:2] == ["env", "R2G_ENV_FILE=/shared/campaign/runtime.env"]
    assert remote[2] == "/remote/python3"
    assert remote[3] == "/remote/runtime/tools/run_experiment3_ablation.py"
    assert "/home/yangao/r2g-skills/tools/run_experiment3_ablation.py" not in remote


def test_conditional_third_worker_is_allowed_but_fourth_is_rejected(tmp_path):
    campaign = _campaign(tmp_path)
    campaign.eda_workers = 3
    command = campaign.matrix_base()
    assert command[command.index("--workers") + 1] == "3"
    assert command.count("--cpu-set") == 3

    limits = json.loads((tmp_path / "limits.json").read_text(encoding="utf-8"))
    limits["eda"]["concurrent_trials_conditional_max"] = 3
    (tmp_path / "limits.json").write_text(json.dumps(limits), encoding="utf-8")
    args = SimpleNamespace(
        campaign_root=tmp_path / "campaign-four",
        runtime_env=None,
        api_env_file=None,
        eda_host="memlab203",
        remote_repo=Path("/remote/runtime"),
        remote_python=Path("/usr/bin/python3"),
        eda_workers=4,
        routes=tmp_path / "routes.json",
        resource_limits=tmp_path / "limits.json",
        action_policy=tmp_path / "action_policy.json",
        a_propose_prompt=tmp_path / "a_propose_prompt.md",
        pure_llm_prompt=tmp_path / "pure_llm_prompt.md",
    )
    args.campaign_root.mkdir()
    try:
        MODULE.Campaign(args)
    except ValueError as exc:
        assert "1..3" in str(exc)
    else:
        raise AssertionError("four EDA workers exceeded the frozen maximum")


def test_feedback_excludes_paths_and_unregistered_fields():
    rows = MODULE.Campaign.feedback_rows(
        [
            {
                "task_id": "task-a",
                "candidate_hash": "abc",
                "verdict": "loss",
                "project": "/secret/path",
                "action_metrics": {"setup_wns_ns": -1.0},
                "unexpected": "hidden",
            }
        ]
    )
    assert rows[0]["task_id"] == "task-a"
    assert "project" not in rows[0]
    assert "unexpected" not in rows[0]
    assert "action_metrics" not in rows[0]


def test_attempted_hashes_are_scoped_to_domain_and_prior_rounds(tmp_path):
    campaign = _campaign(tmp_path)
    plan_root = campaign.a_root / "plans"
    for round_index, domain, fingerprint in (
        (0, "setup_timing", "setup-old"),
        (0, "drc_edge_pin", "drc-old"),
        (1, "setup_timing", "setup-current"),
    ):
        path = plan_root / f"round-{round_index}" / f"{domain}.json"
        MODULE.write_json(
            path,
            {
                "selected": [
                    {"candidate": {"candidate_hash": fingerprint}}
                ]
            },
        )
    output = tmp_path / "attempted.json"
    campaign.attempted_hashes("setup_timing", output, before_round=1)
    assert MODULE.read_json(output)["candidate_hashes"] == ["setup-old"]
