"""P17 MIR sample-size planning contract tests."""

import json

import pytest

from tehm.evaluation.mir_sample_plan import (
    MIRError, build_mir_sample_plan, replay_mir_sample_plan,
)


def test_zero_harm_plan_matches_strict_wilson_bound():
    report = build_mir_sample_plan(
        current_known_cases=6, current_harmful_cases=0,
        thresholds=(0.10, 0.05, 0.02, 0.01, 0.0),
        current_evidence={
            "receipt_digest": "sha256:aggregate",
            "known_cases": 6, "harmful_cases": 0,
        })
    receipt = report["mir_sample_plan"]
    rows = {row["threshold"]: row for row in receipt["thresholds"]}
    assert receipt["current_upper_ci"] == 0.390334
    assert rows[0.10]["minimum_known_cases"] == 35
    assert rows[0.05]["minimum_known_cases"] == 73
    assert rows[0.02]["minimum_known_cases"] == 189
    assert rows[0.01]["minimum_known_cases"] == 381
    assert rows[0.0]["minimum_known_cases"] is None
    assert rows[0.0]["status"] == "finite_wilson_upper_bound_is_positive"
    assert rows[0.05]["additional_known_cases"] == 67


def test_plan_accounts_for_observed_harmful_cases():
    report = build_mir_sample_plan(
        current_known_cases=6, current_harmful_cases=1,
        thresholds=(0.5,), max_search_cases=1000)
    row = report["mir_sample_plan"]["thresholds"][0]
    assert row["minimum_known_cases"] > 6
    assert row["upper_ci_at_minimum"] < 0.5


def test_plan_is_content_addressed_and_replayable(tmp_path):
    path = tmp_path / "mir_sample_plan.json"
    report = build_mir_sample_plan(
        current_known_cases=6, current_harmful_cases=0,
        thresholds=(0.05,), output=path)
    receipt = replay_mir_sample_plan(path)
    assert receipt.receipt_id == report["receipt_id"]
    assert receipt.receipt_digest == report["receipt_digest"]

    payload = json.loads(path.read_text())
    payload["mir_sample_plan"]["current_known_cases"] = 7
    path.write_text(json.dumps(payload))
    with pytest.raises(MIRError, match="digest mismatch|does not replay"):
        replay_mir_sample_plan(path)


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"current_known_cases": 0, "current_harmful_cases": 1}, "cannot exceed"),
        ({"current_known_cases": 1, "current_harmful_cases": 0,
          "thresholds": (0.1, 0.1)}, "duplicates"),
        ({"current_known_cases": 1, "current_harmful_cases": 0,
          "thresholds": (0.1,), "max_search_cases": 0}, "positive integer"),
    ],
)
def test_plan_rejects_malformed_inputs(kwargs, message):
    with pytest.raises(MIRError, match=message):
        build_mir_sample_plan(**kwargs)
