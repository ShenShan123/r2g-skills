import importlib.util
import json
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO / "experiments/experiment3_protocol.py"
SPEC = importlib.util.spec_from_file_location("experiment3_protocol", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def _evidence(family, role, verdict, candidate_hash="abc", **extra):
    return {
        "candidate_hash": candidate_hash,
        "rtl_family_id": family,
        "evidence_role": role,
        "verdict": verdict,
        "provenance_complete": True,
        **extra,
    }


def test_hybrid_promotion_accepts_one_development_and_one_validation_win():
    verdict = MODULE.judge_promotion(
        [
            _evidence("family-a", "a_propose_frozen_replay", "win"),
            _evidence("family-b", "a_validation", "win"),
        ]
    )

    assert verdict["operational_promoted"] is True
    assert verdict["strict_validation_supported"] is False


def test_two_development_wins_do_not_promote_without_validation_support():
    verdict = MODULE.judge_promotion(
        [
            _evidence("family-a", "a_propose_frozen_replay", "win"),
            _evidence("family-b", "a_propose_frozen_replay", "win"),
        ]
    )

    assert verdict["operational_promoted"] is False
    assert verdict["status"] == "candidate"


def test_two_validation_wins_set_strict_support_flag():
    verdict = MODULE.judge_promotion(
        [
            _evidence("family-a", "a_validation", "win"),
            _evidence("family-b", "a_validation", "win"),
        ]
    )

    assert verdict["operational_promoted"] is True
    assert verdict["strict_validation_supported"] is True


def test_repeated_subject_is_only_one_vote():
    verdict = MODULE.judge_promotion(
        [
            _evidence("family-a", "a_validation", "win"),
            _evidence("family-a", "a_validation", "win"),
        ]
    )

    assert verdict["independent_wins"] == 1
    assert verdict["operational_promoted"] is False


def test_conflicting_validation_repeats_do_not_count_as_validation_win():
    verdict = MODULE.judge_promotion(
        [
            _evidence("family-a", "a_validation", "win"),
            _evidence("family-a", "a_validation", "loss"),
            _evidence("family-b", "a_propose_frozen_replay", "win"),
        ]
    )

    assert verdict["validation_wins"] == 0
    assert verdict["operational_promoted"] is False


def test_sentinel_regression_blocks_promotion():
    verdict = MODULE.judge_promotion(
        [
            _evidence("family-a", "a_validation", "win"),
            _evidence(
                "family-b",
                "a_propose_frozen_replay",
                "win",
                clean_sentinel_regression=True,
            ),
        ]
    )

    assert verdict["operational_promoted"] is False
    assert verdict["status"] == "shadow"


def test_mixed_candidate_hashes_are_rejected():
    try:
        MODULE.judge_promotion(
            [
                _evidence("family-a", "a_validation", "win", "abc"),
                _evidence("family-b", "a_validation", "win", "def"),
            ]
        )
    except ValueError as exc:
        assert "candidate hash" in str(exc)
    else:
        raise AssertionError("mixed candidate versions were allowed to pool evidence")


def test_build_sentinels_rejects_nonclean_baseline(tmp_path):
    project = tmp_path / "projects/task-a/baseline"
    project.mkdir(parents=True)
    (project / "repair_family_probe_result.json").write_text(
        json.dumps({"strict_clean": False}), encoding="utf-8"
    )
    (project / "metadata.json").write_text("{}", encoding="utf-8")
    (project / "repair_family_probe_input.json").write_text("{}", encoding="utf-8")
    args = type("Args", (), {
        "projects_root": tmp_path / "projects",
        "task_id": ["task-a"],
        "output": tmp_path / "sentinels.json",
    })()
    try:
        MODULE.command_build_sentinels(args)
    except ValueError as exc:
        assert "not complete strict clean" in str(exc)
    else:
        raise AssertionError("nonclean sentinel was accepted")
