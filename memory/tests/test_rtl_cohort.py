"""P12-B Stage-B cohort boundary tests."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tehm.evaluation.candidate_executor import P12_ARMS
from tehm.evaluation.rtl_candidate_oracle import IcarusCandidateOracle
from tehm.evaluation.rtl_cohort import (
    RtlCohortError, RtlPairedCohortReceipt, execute_rtl_paired_cohort,
)
from tehm.rtl.rtl_oracle import IcarusOracle
from tehm.retrieval.structured_candidate import StructuredRepairCandidate


PROJECTS = Path(__file__).resolve().parent / "fixtures" / "rtl_projects"
PLATFORM = "sha256:p12-platform"
PDK = "sha256:p12-pdk"
TOOLCHAIN = "sha256:p12-toolchain"
ORACLE = "sha256:p12-oracle"


def _candidate(case_name: str) -> StructuredRepairCandidate:
    if case_name == "req_ack_bug":
        source_state, target_state, condition = "SEND", "DONE", "ack"
    else:
        source_state, target_state, condition = "WRITE", "VERIFY", "wr_ack"
    return StructuredRepairCandidate(
        candidate_id=f"candidate-{case_name}",
        resolved_state_id=f"state-{case_name}",
        knowledge_object_id="mk-handshake@1", causal_path_ids=("path-handshake",),
        asset_id="asset-guard-strengthen", action_family="GUARD_STRENGTHEN",
        concrete_action={"domain": "rtl.GUARD_STRENGTHEN",
                         "transformation_family": "GUARD_STRENGTHEN",
                         "payload": {"module": "req_ack_fsm",
                                     "source_state": source_state,
                                     "target_state": target_state,
                                     "add_condition": condition}},
        applicability_receipt_id=f"app-{case_name}",
        binding_receipt_id=f"binding-{case_name}",
        obligations=("RTL_TARGET_TEST_PASS", "RTL_FROZEN_REGRESSION_PASS",
                     "RTL_COMPILE_PASS"),
        evidence_level="L3_REPLICATED_EFFECT", authority={"eligible": True},
        risk={}, provenance={"evaluation_only": True, "source": "cohort_fixture"})


def _case(name: str) -> dict[str, str]:
    project = PROJECTS / name
    source = project / "rtl" / "req_ack_fsm.v"
    return {
        "case_id": f"p12-{name}",
        "rtl_source": str(source),
        "source_digest": "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest(),
        "target_test": str(project / "tb" / "tb_handshake.v"),
        "frozen_regression": str(project / "tb" / "tb_basic.v"),
        "toolchain_digest": TOOLCHAIN,
        "oracle_digest": ORACLE,
        "platform_digest": PLATFORM,
        "pdk_digest": PDK,
    }


def test_source_disjoint_fixed_environment_cohort_replays():
    if not IcarusOracle().available:
        pytest.skip("iverilog/vvp not available")
    cases = [_case("req_ack_bug"), _case("req_ack_bug2")]
    arms = {
        case["case_id"]: {
            arm: None if arm == "NO_MEMORY" else _candidate(
                case["case_id"].removeprefix("p12-"))
            for arm in P12_ARMS
        }
        for case in cases
    }
    receipt = execute_rtl_paired_cohort(
        cases, arms, campaign_id="p12-controlled-cohort",
        campaign_manifest_digest="sha256:p12-manifest",
        platform_digest=PLATFORM, pdk_digest=PDK,
        oracle=IcarusCandidateOracle(), budget=3,
        toolchain_digest=TOOLCHAIN, oracle_digest=ORACLE)
    assert isinstance(receipt, RtlPairedCohortReceipt)
    assert receipt.source_disjoint is True
    assert receipt.source_restore_verified is True
    assert receipt.outcome_counts["NO_MEMORY"]["FAIL"] == 2
    for arm in P12_ARMS[1:]:
        assert receipt.outcome_counts[arm]["PASS"] == 2
    replay = RtlPairedCohortReceipt.from_dict(
        {**receipt.to_dict(), "receipt_digest": receipt.receipt_digest})
    assert replay.to_dict() == receipt.to_dict()


def test_cohort_rejects_duplicate_source_or_environment_drift():
    first = _case("req_ack_bug")
    duplicate = dict(first)
    duplicate["case_id"] = "p12-duplicate-source"
    arms = {
        first["case_id"]: {arm: _candidate("req_ack_bug") for arm in P12_ARMS
                            if arm != "NO_MEMORY"} | {"NO_MEMORY": None},
        duplicate["case_id"]: {arm: _candidate("req_ack_bug") for arm in P12_ARMS
                                if arm != "NO_MEMORY"} | {"NO_MEMORY": None},
    }
    kwargs = dict(campaign_id="p12-invalid", campaign_manifest_digest="sha256:manifest",
                  platform_digest=PLATFORM, pdk_digest=PDK,
                  toolchain_digest=TOOLCHAIN, oracle_digest=ORACLE)
    with pytest.raises(RtlCohortError, match="disjoint"):
        execute_rtl_paired_cohort([first, duplicate], arms, **kwargs)

    drifted = _case("req_ack_bug2")
    drifted["platform_digest"] = "sha256:other-platform"
    arms = {drifted["case_id"]: {
        arm: None if arm == "NO_MEMORY" else _candidate("req_ack_bug2")
        for arm in P12_ARMS}}
    with pytest.raises(RtlCohortError, match="platform"):
        execute_rtl_paired_cohort([drifted], arms, **kwargs)
