"""P12-B controlled real-tool execution through the structured candidate seam."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tehm.evaluation.candidate_executor import (
    P12_ARMS, PairedCandidateExecutionReceipt, execute_candidate,
    execute_paired_candidates,
)
from tehm.evaluation.rtl_candidate_oracle import IcarusCandidateOracle
from tehm.rtl.rtl_oracle import IcarusOracle
from tehm.retrieval.structured_candidate import StructuredRepairCandidate


PROJECT = Path(__file__).resolve().parent / "fixtures" / "rtl_projects" / "req_ack_bug"


def _candidate() -> StructuredRepairCandidate:
    return StructuredRepairCandidate(
        candidate_id="rtl-p12-guard-candidate",
        resolved_state_id="state-req-ack",
        knowledge_object_id="mk-handshake@1",
        causal_path_ids=("path-handshake",),
        asset_id="asset-guard-strengthen",
        action_family="GUARD_STRENGTHEN",
        concrete_action={
            "domain": "rtl.GUARD_STRENGTHEN",
            "transformation_family": "GUARD_STRENGTHEN",
            "payload": {
                "module": "req_ack_fsm", "source_state": "SEND",
                "target_state": "DONE", "add_condition": "ack",
            },
        },
        applicability_receipt_id="applicability-p12",
        binding_receipt_id="binding-p12",
        obligations=("RTL_TARGET_TEST_PASS", "RTL_FROZEN_REGRESSION_PASS",
                     "RTL_COMPILE_PASS"),
        evidence_level="L3_REPLICATED_EFFECT",
        authority={"eligible": True}, risk={},
        provenance={"evaluation_only": True, "source": "controlled_fixture"},
    )


def _case() -> dict[str, str]:
    return {
        "case_id": "req-ack-p12-controlled",
        "rtl_source": str(PROJECT / "rtl" / "req_ack_fsm.v"),
        "target_test": str(PROJECT / "tb" / "tb_handshake.v"),
        "frozen_regression": str(PROJECT / "tb" / "tb_basic.v"),
        # Fixed values make the paired contract independently replayable even
        # when the host resolves a different PATH.
        "toolchain_digest": "sha256:p12-controlled-icarus",
        "oracle_digest": "sha256:p12-controlled-oracle",
    }


def test_icarus_candidate_executes_real_target_and_regression_without_source_write():
    if not IcarusOracle().available:
        pytest.skip("iverilog/vvp not available")
    case = _case()
    source_before = hashlib.sha256(Path(case["rtl_source"]).read_bytes()).hexdigest()
    receipt = execute_candidate(
        _candidate(), case, oracle=IcarusCandidateOracle(), budget=3)
    assert receipt.outcome == "PASS"
    assert receipt.compile_result == "PASS"
    assert receipt.functional_result == "PASS"
    assert receipt.signoff_result == "PASS"
    assert receipt.produced_transition_id is None
    assert receipt.evaluation_only is True
    source_after = hashlib.sha256(Path(case["rtl_source"]).read_bytes()).hexdigest()
    assert source_after == source_before
    assert receipt.obligations["RTL_TARGET_TEST_PASS"] == "PASS"
    assert receipt.obligations["RTL_FROZEN_REGRESSION_PASS"] == "PASS"


def test_icarus_p12_pair_is_real_and_keeps_no_memory_baseline():
    if not IcarusOracle().available:
        pytest.skip("iverilog/vvp not available")
    candidate = _candidate()
    bundle = execute_paired_candidates(
        _case(), {arm: (None if arm == "NO_MEMORY" else candidate)
                  for arm in P12_ARMS},
        oracle=IcarusCandidateOracle(), budget=3)
    assert isinstance(bundle, PairedCandidateExecutionReceipt)
    assert bundle.case_digest.startswith("sha256:")
    assert bundle.arm_receipts["NO_MEMORY"].outcome == "FAIL"
    assert all(bundle.arm_receipts[arm].outcome == "PASS" for arm in P12_ARMS[1:])
    assert all(receipt.toolchain_digest == "sha256:p12-controlled-icarus"
               for receipt in bundle.arm_receipts.values())
    assert all(receipt.oracle_digest == "sha256:p12-controlled-oracle"
               for receipt in bundle.arm_receipts.values())


def test_icarus_candidate_case_has_no_manifest_or_gold_dependency():
    if not IcarusOracle().available:
        pytest.skip("iverilog/vvp not available")
    case = _case()
    case["manifest"] = "/path/that/does/not/exist/manifest.json"
    # The adapter ignores this field entirely, proving that its execution does
    # not depend on a fixture manifest or its gold ``fix`` payload.
    receipt = execute_candidate(
        _candidate(), case, oracle=IcarusCandidateOracle(), budget=3)
    assert receipt.outcome == "PASS"
