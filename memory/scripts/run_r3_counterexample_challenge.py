#!/usr/bin/env python3
"""Run the Revision3 P1-R3 counterexample challenge with real Icarus/vvp."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tehm.evaluation.candidate_executor import execute_candidate  # noqa: E402
from tehm.evaluation.rtl_candidate_oracle import (  # noqa: E402
    execute_rtl_candidate,
)
from tehm.evolution.admission import admit_evolution_reason  # noqa: E402
from tehm.evolution.counterexample import detect_counterexample  # noqa: E402
from tehm.evolution.reason_derivation import (  # noqa: E402
    derive_counterexample_reason,
)
from tehm.rtl.rtl_oracle import IcarusOracle  # noqa: E402
from tehm.retrieval.structured_candidate import StructuredRepairCandidate  # noqa: E402


PROJECT = ROOT / "tests/fixtures/rtl_projects/req_ack_bug"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _candidate() -> StructuredRepairCandidate:
    # This action is intentionally wrong: it is still a valid structured,
    # bound action, but its constant-false guard violates the asset's PASS
    # prediction and lets the detector prove that candidate FAIL alone is not
    # the reason.
    return StructuredRepairCandidate(
        candidate_id="r3-counterexample-wrong-guard",
        resolved_state_id="state-send", knowledge_object_id="knowledge-handshake@1",
        causal_path_ids=("path-handshake",), asset_id="asset-guard-strengthen",
        action_family="GUARD_STRENGTHEN",
        concrete_action={
            "domain": "rtl.GUARD_STRENGTHEN",
            "transformation_family": "GUARD_STRENGTHEN",
            "payload": {"module": "req_ack_fsm", "source_state": "SEND",
                        "target_state": "DONE", "add_condition": "1'b0"},
        },
        applicability_receipt_id="applicability-r3-counterexample",
        binding_receipt_id="binding-r3-counterexample",
        obligations=("RTL_TARGET_TEST_PASS", "RTL_FROZEN_REGRESSION_PASS",
                     "RTL_COMPILE_PASS"),
        evidence_level="L3_REPLICATED_EFFECT", authority={"eligible": True}, risk={},
        provenance={"source": "r3-counterexample-challenge", "evaluation_only": True},
    )


def _case() -> dict[str, str]:
    return {
        "case_id": "r3-counterexample-req-ack",
        "rtl_source": str(PROJECT / "rtl/req_ack_fsm.v"),
        "target_test": str(PROJECT / "tb/tb_handshake.v"),
        "frozen_regression": str(PROJECT / "tb/tb_basic.v"),
        "toolchain_digest": "sha256:r3-counterexample-icarus",
        "oracle_digest": "sha256:r3-counterexample-oracle",
    }


class _CounterexampleOracle:
    """Add explicit mediated observations to the real candidate oracle output."""

    def __init__(self, oracle: IcarusOracle):
        self.oracle = oracle

    def __call__(self, candidate, frozen_case, budget):
        result = execute_rtl_candidate(candidate, frozen_case, budget,
                                        oracle=self.oracle)
        metadata = dict(result.get("metadata") or {})
        # These fields are emitted by the oracle adapter, not inferred by the
        # reason detector.  They are intentionally independent of fixture gold.
        metadata.update({
            "oracle_complete": result.get("metadata", {}).get("oracle_complete") is True,
            "observed_outcome": {"outcome": result.get("outcome")},
            "observed_effects": [{
                "effect": "RTL_TRANSFER_COMPLETION",
                "status": result.get("functional_result"),
            }],
        })
        result["metadata"] = metadata
        return result


def run_challenge(output_dir: Path, *, campaign_id: str) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    source = Path(_case()["rtl_source"])
    before = _sha256(source)
    oracle = IcarusOracle()
    if not oracle.available:
        raise RuntimeError("Icarus/vvp is required for the counterexample challenge")
    candidate = _candidate()
    execution = execute_candidate(candidate, _case(), oracle=_CounterexampleOracle(oracle), budget=3)
    if execution.outcome != "FAIL" or execution.signoff_result != "FAIL":
        raise AssertionError("challenge must produce a complete candidate FAIL")
    applicability = {"receipt_id": candidate.applicability_receipt_id,
                     "status": "APPLICABLE"}
    binding = {"receipt_id": candidate.binding_receipt_id, "status": "BOUND",
               "candidate_digest": candidate.candidate_digest,
               "action_digest": execution.action_digest}
    counterexample = detect_counterexample(
        candidate, execution,
        prediction={"expected_outcome": {"outcome": "PASS"},
                    "predicted_effects": ({"effect": "RTL_TRANSFER_COMPLETION",
                                           "status": "PASS"},)},
        applicability=applicability, binding=binding,
        campaign_id=campaign_id, lineage_id="lineage-r3-counterexample")
    if counterexample is None:
        raise AssertionError("explicit prediction should be contradicted")
    derivation = derive_counterexample_reason(
        counterexample, campaign_id=campaign_id, case_id=execution.case_id)
    admission = admit_evolution_reason(
        derivation, campaign_id=campaign_id, learner_eligible=True,
        counterexample=counterexample)
    if not admission.admitted:
        raise AssertionError(f"counterexample admission blocked: {admission.blocked_reason}")
    after = _sha256(source)
    if after != before:
        raise AssertionError("counterexample evaluation modified the source fixture")
    report = {
        "version": "r3-counterexample-challenge-v1", "campaign_id": campaign_id,
        "case": _case(), "source_sha256_before": before,
        "source_sha256_after": after, "real_oracle": "icarus/vvp",
        "candidate_execution": {**execution.to_dict(),
                                "execution_digest": execution.execution_digest},
        "counterexample_receipt": {**counterexample.to_dict(),
                                    "receipt_id": counterexample.receipt_id,
                                    "receipt_digest": counterexample.receipt_digest},
        "evolution_reason_derivation": {**derivation.to_dict(),
                                         "receipt_id": derivation.receipt_id,
                                         "receipt_digest": derivation.receipt_digest},
        "evolution_admission": {**admission.to_dict(),
                                 "receipt_id": admission.receipt_id,
                                 "receipt_digest": admission.receipt_digest},
        "canonical_memory_mutation": "none",
        "memory_docs_submitted": False,
        "production_runtime": {"promotion_attempted": False,
                               "production_promotion_eligible": False,
                               "runtime_authority_changed": False},
        "negative_control": "candidate FAIL without explicit prediction/effect witness is rejected",
    }
    (output_dir / "counterexample_challenge_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--campaign-id", default="tehm-r3-counterexample-20260902")
    args = parser.parse_args(argv)
    report = run_challenge(args.output, campaign_id=args.campaign_id)
    print(json.dumps({
        "reason": report["evolution_reason_derivation"]["reason"],
        "admitted": report["evolution_admission"]["admitted"],
        "candidate_outcome": report["candidate_execution"]["outcome"],
        "contradiction_types": report["counterexample_receipt"]["contradiction_types"],
        "canonical_memory_mutation": report["canonical_memory_mutation"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
