import importlib.util
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO / "tools/experiment3_candidate_bank.py"
SPEC = importlib.util.spec_from_file_location("experiment3_candidate_bank", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def _candidate(**changes):
    value = {
        "candidate_id": "timing-map",
        "candidate_version": 1,
        "source_model": "test-model",
        "failure_domain": "setup_timing",
        "strategy": "abc_timing_mapping",
        "rationale": "Prefer timing-driven ABC mapping.",
        "applicability": {
            "platform": "sky130hd",
            "required_failure_signatures": ["SETUP_TIMING"],
            "requires_negative_setup_wns": True,
        },
        "config_edits": {"ABC_AREA": "0"},
        "action_policy_sha256": MODULE.sha256_file(MODULE.DEFAULT_POLICY),
    }
    value.update(changes)
    return value


def test_freeze_is_deterministic_and_policy_bound():
    first = MODULE.freeze_candidate(_candidate())
    second = MODULE.freeze_candidate(_candidate())
    assert first["candidate_hash"] == second["candidate_hash"]
    assert MODULE.validate_candidate(first) == []


def test_equivalent_actions_from_different_models_share_one_hash():
    first = MODULE.freeze_candidate(_candidate(source_model="gpt", rationale="first"))
    second = MODULE.freeze_candidate(
        _candidate(candidate_id="another-name", source_model="claude", rationale="second")
    )
    assert first["candidate_hash"] == second["candidate_hash"]


def test_structural_dedup_preserves_all_sources():
    first = _candidate(source_model="gpt")
    second = _candidate(candidate_id="another-name", source_model="claude")
    frozen = MODULE.freeze_candidate(first)
    evidence = [{"candidate_hash": frozen["candidate_hash"], "infrastructure_complete": True, "verdict": "win"}]
    records = MODULE.deduplicate_candidates([first, second], evidence)
    assert len(records) == 1
    assert records[0]["proposal_sources"] == ["claude", "gpt"]
    assert records[0]["admitted"] is True


def test_footprint_changing_edit_is_rejected():
    candidate = _candidate(config_edits={"CORE_UTILIZATION": "10"})
    errors = MODULE.validate_candidate(candidate)
    assert any("not allowed" in error for error in errors)


def test_candidate_admission_requires_complete_positive_evidence():
    candidate = MODULE.freeze_candidate(_candidate())
    rejected = MODULE.admit_candidate(
        candidate,
        [{"candidate_hash": candidate["candidate_hash"], "infrastructure_complete": False, "verdict": "win"}],
    )
    admitted = MODULE.admit_candidate(
        candidate,
        [{"candidate_hash": candidate["candidate_hash"], "infrastructure_complete": True, "verdict": "win"}],
    )
    assert rejected["admitted"] is False
    assert admitted["admitted"] is True


def test_candidate_admission_is_revoked_by_later_sentinel_regression():
    candidate = MODULE.freeze_candidate(_candidate())
    record = MODULE.admit_candidate(
        candidate,
        [
            {
                "candidate_hash": candidate["candidate_hash"],
                "infrastructure_complete": True,
                "verdict": "win",
            },
            {
                "candidate_hash": candidate["candidate_hash"],
                "infrastructure_complete": True,
                "verdict": "loss",
                "clean_sentinel_regression": True,
            },
        ],
    )
    assert record["admitted"] is False
    assert record["reason"] == "hard_regression"


def test_m2_ignores_b_evidence():
    one = MODULE.admit_candidate(
        MODULE.freeze_candidate(_candidate(candidate_id="aaa")),
        [],
    )
    two = MODULE.admit_candidate(
        MODULE.freeze_candidate(_candidate(candidate_id="bbb", candidate_version=2)),
        [],
    )
    one["admitted"] = True
    two["admitted"] = True
    ordered = MODULE.m2_order(
        [one, two],
        [
            {"candidate_hash": two["candidate_hash"], "split": "b_heldout", "provenance_complete": True, "verdict": "win"},
            {"candidate_hash": one["candidate_hash"], "split": "a_validation", "provenance_complete": True, "verdict": "win"},
        ],
    )
    assert ordered[0]["candidate_hash"] == one["candidate_hash"]


def test_m3_keeps_only_promoted_recipes_in_m2_evidence_order():
    records = [
        {"candidate_hash": key, "admitted": True,
         "candidate": {"failure_domain": "antenna_drc", "candidate_id": key,
                       "candidate_version": 1}}
        for key in ("a", "b", "c")
    ]
    formal = [
        {"candidate_hash": "b", "split": "a_validation", "provenance_complete": True,
         "verdict": "win"},
        {"candidate_hash": "c", "split": "a_validation", "provenance_complete": True,
         "verdict": "win", "strict_clean_after_repair": True},
    ]
    promotions = [
        {"candidate_hash": "a", "promotion": {"operational_promoted": True}},
        {"candidate_hash": "b", "promotion": {"operational_promoted": True}},
        {"candidate_hash": "c", "promotion": {"operational_promoted": False}},
    ]
    assert [row["candidate_hash"] for row in MODULE.m3_order(records, formal, promotions)] == ["b", "a"]


def test_nangate45_policy_binds_platform_and_domain(tmp_path):
    policy = tmp_path / "policy.json"
    policy.write_text(
        '{"platform":"nangate45","failure_domains":["antenna_drc"],'
        '"allowed_numeric_knobs":{},"allowed_string_knobs":'
        '{"MAX_REPAIR_ANTENNAS_ITER_DRT":["10"]}}',
        encoding="utf-8",
    )
    candidate = _candidate(
        failure_domain="antenna_drc",
        applicability={
            "platform": "nangate45",
            "required_failure_signatures": ["DRC:*_ANTENNA"],
        },
        config_edits={"MAX_REPAIR_ANTENNAS_ITER_DRT": "10"},
        action_policy_sha256=MODULE.sha256_file(policy),
    )
    assert MODULE.validate_candidate(candidate, policy) == []
