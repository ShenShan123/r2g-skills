"""P15-B routed-policy MIR aggregation and replay tests."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tehm.evaluation.candidate_executor import (
    CandidateExecutionReceipt, PairedCandidateExecutionReceipt,
)
from tehm.evaluation.policy_mir import (
    PolicyMIRError, build_routed_policy_mir, replay_routed_policy_mir,
)
from tehm.evaluation.production_readiness import _interference
from tehm.evaluation.rtl_cohort import RtlPairedCohortReceipt
from tehm.ids import stable_dumps


TOOLCHAIN = "sha256:policy-toolchain"
ORACLE = "sha256:policy-oracle"
PLATFORM = "sha256:policy-platform"
PDK = "sha256:policy-pdk"


def _execution(case_id: str, source: str, candidate_id: str, *, fallback: bool = False):
    return CandidateExecutionReceipt(
        case_id=case_id, candidate_id=candidate_id, source=source,
        action_digest="sha256:action-" + case_id,
        candidate_digest="sha256:candidate-" + candidate_id,
        compile_result="PASS", functional_result="PASS", signoff_result="PASS",
        outcome="PASS", created_regressions=(), obligations={},
        toolchain_digest=TOOLCHAIN, oracle_digest=ORACLE,
        produced_transition_id=None, budget=3,
        metadata={"oracle_available": True, "policy_fallback": fallback},
    )


def _cohort(tmp_path: Path, campaign: str, offset: int, *, policy_arm: str = "CAUSAL_NO_SKILL") -> Path:
    cases = {}
    source_digests = {}
    for index in range(2):
        number = offset + index
        case_id = f"policy-case-{number}"
        if policy_arm == "CAUSAL_NO_SKILL":
            routing = "NO_SKILL" if number % 2 else "CONSIDER"
            policy_source = "no_memory" if routing == "NO_SKILL" else "structured_memory"
            fallback = routing == "NO_SKILL"
            no_skill_reason = "NO_MATCH" if routing == "NO_SKILL" else None
        else:
            routing = "CONSIDER"
            policy_source = "structured_memory"
            fallback = False
            no_skill_reason = None
        cases[case_id] = PairedCandidateExecutionReceipt(
            case_id=case_id,
            arm_receipts={
                "NO_MEMORY": _execution(case_id, "no_memory", "no-memory:" + case_id),
                "ALWAYS_MEMORY": _execution(case_id, "structured_memory", "always:" + case_id),
                "APPLICABILITY_GATED": _execution(case_id, "structured_memory", "gated:" + case_id),
                "CAUSAL_NO_SKILL": _execution(
                    case_id, policy_source, "causal:" + case_id, fallback=fallback),
            },
            candidate_budget=3, case_digest=f"sha256:case-{number}",
            toolchain_digest=TOOLCHAIN, oracle_digest=ORACLE,
            paired=True, evaluation_only=True, lineage_id=f"lineage-{number}",
            no_skill_reason=no_skill_reason, routing_receipt_id=f"route-{number}",
            routing_decision=routing,
        )
        source_digests[case_id] = f"sha256:source-{number}"
    cohort = RtlPairedCohortReceipt(
        campaign_id=campaign, case_receipts=cases, source_digests=source_digests,
        candidate_budget=3, toolchain_digest=TOOLCHAIN, oracle_digest=ORACLE,
        platform_digest=PLATFORM, pdk_digest=PDK,
        campaign_manifest_digest=f"sha256:manifest-{campaign}",
    )
    path = tmp_path / f"{campaign}.json"
    path.write_text(json.dumps({**cohort.to_dict(), "receipt_digest": cohort.receipt_digest}))
    return path


def test_build_and_replay_aggregates_source_disjoint_cohorts(tmp_path):
    first = _cohort(tmp_path, "campaign-a", 0)
    second = _cohort(tmp_path, "campaign-b", 2)
    output = tmp_path / "policy-mir.json"
    payload = build_routed_policy_mir(
        [first, second], policy_arm="CAUSAL_NO_SKILL", output=output)
    assert payload["cohort_count"] == 2
    assert payload["case_count"] == payload["known_cases"] == 4
    assert payload["harmful_cases"] == 0
    assert payload["routing_receipt_coverage"] == 1.0
    report = json.loads(output.read_text())
    assert report["policy_mir"] == payload
    assert replay_routed_policy_mir(payload, base=tmp_path)["upper_ci"] == payload["upper_ci"]


def test_replay_rejects_tampered_aggregate(tmp_path):
    first = _cohort(tmp_path, "campaign-a", 0)
    second = _cohort(tmp_path, "campaign-b", 2)
    payload = build_routed_policy_mir([first, second], policy_arm="CAUSAL_NO_SKILL")
    tampered = dict(payload)
    tampered["harmful_cases"] = 1
    with pytest.raises(PolicyMIRError, match="aggregate disagrees"):
        replay_routed_policy_mir(tampered, base=tmp_path)


def test_replay_rejects_duplicate_source_across_cohorts(tmp_path):
    first = _cohort(tmp_path, "campaign-a", 0)
    second = _cohort(tmp_path, "campaign-b", 2)
    payload = build_routed_policy_mir([first, second], policy_arm="CAUSAL_NO_SKILL")
    raw_second = json.loads(second.read_text())
    raw_second["source_digests"]["policy-case-2"] = "sha256:source-0"
    # The cohort itself must be re-signed before it can be referenced; the
    # cross-cohort source overlap is then caught by the aggregate replay.
    raw_second.pop("receipt_digest", None)
    checked_second = RtlPairedCohortReceipt.from_dict(raw_second)
    raw_second["receipt_digest"] = checked_second.receipt_digest
    second.write_text(json.dumps(raw_second))
    tampered = dict(payload)
    refs = [dict(item) for item in payload["cohort_receipts"]]
    refs[1]["sha256"] = "sha256:" + hashlib.sha256(second.read_bytes()).hexdigest()
    refs[1]["receipt_digest"] = checked_second.receipt_digest
    tampered["cohort_receipts"] = refs
    tampered_unsigned = dict(tampered)
    tampered_unsigned.pop("receipt_digest")
    tampered["receipt_digest"] = "sha256:" + hashlib.sha256(
        stable_dumps(tampered_unsigned).encode()).hexdigest()
    with pytest.raises(PolicyMIRError, match="overlapping source digests"):
        replay_routed_policy_mir(tampered, base=tmp_path)


def test_build_rejects_environment_drift(tmp_path):
    first = _cohort(tmp_path, "campaign-a", 0)
    second = _cohort(tmp_path, "campaign-b", 2)
    raw = json.loads(second.read_text())
    raw["platform_digest"] = "sha256:other-platform"
    # The typed receipt digest is intentionally updated; the fixed-environment
    # check must still reject the cohort pair.
    from tehm.evaluation.rtl_cohort import RtlPairedCohortReceipt
    raw.pop("receipt_digest", None)
    checked = RtlPairedCohortReceipt.from_dict(raw)
    raw["receipt_digest"] = checked.receipt_digest
    second.write_text(json.dumps(raw))
    with pytest.raises(PolicyMIRError, match="environment drifted"):
        build_routed_policy_mir([first, second], policy_arm="CAUSAL_NO_SKILL")


def test_build_rejects_missing_routed_policy_semantics(tmp_path):
    first = _cohort(tmp_path, "campaign-a", 0)
    raw = json.loads(first.read_text())
    case = raw["case_receipts"]["policy-case-0"]
    # Keep the paired receipt content-addressed, but remove the route decision
    # that makes the CAUSAL_NO_SKILL no-memory source a meaningful policy arm.
    case.pop("routing_decision", None)
    checked = RtlPairedCohortReceipt.from_dict(raw)
    raw["receipt_digest"] = checked.receipt_digest
    first.write_text(json.dumps(raw))
    with pytest.raises(PolicyMIRError, match="routing decision is missing"):
        build_routed_policy_mir([first], policy_arm="CAUSAL_NO_SKILL")


def test_build_rejects_cross_lane_orfs_cohort_version(tmp_path):
    """Evolution/ORFS challenge receipts cannot enter the RTL policy MIR lane."""
    first = _cohort(tmp_path, "campaign-a", 0)
    raw = json.loads(first.read_text())
    raw["version"] = "orfs-p12-cohort-v0.1"
    first.write_text(json.dumps(raw))
    with pytest.raises(PolicyMIRError, match="cohort cannot replay"):
        build_routed_policy_mir([first], policy_arm="CAUSAL_NO_SKILL")


def test_build_rejects_overlapping_lineage_witness(tmp_path):
    first = _cohort(tmp_path, "campaign-a", 0)
    raw = json.loads(first.read_text())
    raw.pop("receipt_digest", None)
    raw["case_receipts"]["policy-case-1"]["lineage_id"] = "lineage-0"
    checked = RtlPairedCohortReceipt.from_dict(raw)
    raw["receipt_digest"] = checked.receipt_digest
    first.write_text(json.dumps(raw))
    with pytest.raises(PolicyMIRError, match="overlapping lineages"):
        build_routed_policy_mir([first], policy_arm="CAUSAL_NO_SKILL")


def test_production_interference_replays_v2_and_keeps_threshold_explicit(tmp_path):
    first = _cohort(tmp_path, "campaign-a", 0)
    second = _cohort(tmp_path, "campaign-b", 2)
    payload = build_routed_policy_mir([first, second], policy_arm="CAUSAL_NO_SKILL")
    report_path = tmp_path / "interference.json"
    report_path.write_text(json.dumps({
        "reason": "MEMORY_INTERFERENCE", "canonical_memory_mutation": "none",
        "production_authority_changed": False, "policy_mir": payload,
    }))
    passed, metrics = _interference(report_path, max_upper_ci=0.7)
    assert passed is True
    assert metrics["total_cases"] == 4
    assert metrics["upper_ci_threshold"] == 0.7
    passed_default, default_metrics = _interference(report_path, max_upper_ci=0.0)
    assert passed_default is False
    assert default_metrics["upper_ci_threshold"] == 0.0
