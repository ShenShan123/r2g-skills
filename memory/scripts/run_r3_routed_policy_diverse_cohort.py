#!/usr/bin/env python3
"""Freeze a diverse, real-Icarus routed-policy challenge cohort.

This producer intentionally hard-codes only the typed action descriptors that
define this evaluation cohort.  It never reads a fixture manifest or a gold
``fix`` field.  Each copied RTL source is executed through all four P12 arms;
the route is ``CONSIDER`` so the causal policy arm must really execute the
selected structured candidate.  All artifacts are external and evaluation
only: no SQLite store, canonical memory, authority ledger, or production
runtime is opened or changed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contracts import MemoryRoutingDecision  # noqa: E402
from tehm.evaluation.candidate_executor import P12_ARMS  # noqa: E402
from tehm.evaluation.rtl_candidate_oracle import IcarusCandidateOracle  # noqa: E402
from tehm.evaluation.rtl_cohort import execute_rtl_paired_cohort  # noqa: E402
from tehm.retrieval.structured_candidate import StructuredRepairCandidate  # noqa: E402


TOOLCHAIN_DIGEST = "sha256:r3-icarus-toolchain"
ORACLE_DIGEST = "sha256:r3-icarus-candidate-oracle"
PLATFORM_DIGEST = "sha256:r3-platform"
PDK_DIGEST = "sha256:r3-pdk"


# These are deliberately explicit typed descriptors, not fixture-manifest
# imports.  The source, testbench, and transformation are independently
# inspected by the real oracle when the cohort runs.
_SPECS = {
    "p3_obligation_recovery": {
        "source": "obligation_recovery_fsm.v", "module": "obligation_recovery_fsm",
        "target": "tb_handshake.v", "regression": "tb_basic.v",
        "mechanism_family": "HANDSHAKE_COMPLETION", "family": "GUARD_STRENGTHEN",
        "domain": "rtl.GUARD_STRENGTHEN",
        "action": {"source_state": "WAIT_REPLY", "target_state": "COMPLETE",
                   "add_condition": "reply"},
    },
    "p3_obligation_recovery_b": {
        "source": "obligation_recovery_b_fsm.v", "module": "obligation_recovery_b_fsm",
        "target": "tb_handshake.v", "regression": "tb_basic.v",
        "mechanism_family": "HANDSHAKE_COMPLETION", "family": "GUARD_STRENGTHEN",
        "domain": "rtl.GUARD_STRENGTHEN",
        "action": {"source_state": "WAIT_ACK", "target_state": "COMPLETE",
                   "add_condition": "ack_reply"},
    },
    "p3_predicate_unknown": {
        "source": "predicate_unknown_fsm.v", "module": "predicate_unknown_fsm",
        "target": "tb_handshake.v", "regression": "tb_basic.v",
        "mechanism_family": "HANDSHAKE_COMPLETION", "family": "GUARD_STRENGTHEN",
        "domain": "rtl.GUARD_STRENGTHEN",
        "action": {"source_state": "WAIT", "target_state": "COMMIT",
                   "add_condition": "ready"},
    },
    "p3_role_collision": {
        "source": "role_collision_fsm.v", "module": "role_collision_fsm",
        "target": "tb_handshake.v", "regression": "tb_basic.v",
        "mechanism_family": "HANDSHAKE_COMPLETION", "family": "GUARD_STRENGTHEN",
        "domain": "rtl.GUARD_STRENGTHEN",
        "action": {"source_state": "CAPTURE", "target_state": "COMMIT",
                   "add_condition": "ack"},
    },
    "p3_validity_boundary": {
        "source": "validity_boundary_fsm.v", "module": "validity_boundary_fsm",
        "target": "tb_handshake.v", "regression": "tb_basic.v",
        "mechanism_family": "HANDSHAKE_COMPLETION", "family": "GUARD_STRENGTHEN",
        "domain": "rtl.GUARD_STRENGTHEN",
        "action": {"source_state": "ARM", "target_state": "FINISH",
                   "add_condition": "ack"},
    },
    "p3_overlap_priority_a": {
        "source": "overlap_priority_a.v", "module": "overlap_priority_a",
        "target": "tb_priority.v", "regression": "tb_basic.v",
        "mechanism_family": "OVERLAP_PRIORITY_ARBITRATION", "family": "PRIORITY_REORDER",
        "domain": "rtl.PRIORITY_REORDER",
        "action": {"case_expr": "{req, psel}", "higher_label": "2'b?1",
                   "lower_label": "2'b1?"},
    },
    "p3_overlap_priority_b": {
        "source": "overlap_priority_b.v", "module": "overlap_priority_b",
        "target": "tb_priority.v", "regression": "tb_basic.v",
        "mechanism_family": "OVERLAP_PRIORITY_ARBITRATION", "family": "PRIORITY_REORDER",
        "domain": "rtl.PRIORITY_REORDER",
        "action": {"case_expr": "{valid, select}", "higher_label": "2'b?1",
                   "lower_label": "2'b1?"},
    },
    "p3_overlap_priority_c": {
        "source": "overlap_priority_c.v", "module": "overlap_priority_c",
        "target": "tb_priority.v", "regression": "tb_basic.v",
        "mechanism_family": "OVERLAP_PRIORITY_ARBITRATION", "family": "PRIORITY_REORDER",
        "domain": "rtl.PRIORITY_REORDER",
        "action": {"case_expr": "{request, grant_req}", "higher_label": "2'b?1",
                   "lower_label": "2'b1?"},
    },
    "p3_reset_restore_a": {
        "source": "reset_restore_a.v", "module": "reset_restore_a",
        "target": "tb_reset.v", "regression": "tb_basic.v",
        "mechanism_family": "RESET_SEMANTIC_LOSS", "family": "RESET_RESTORE",
        "domain": "rtl.RESET_RESTORE",
        "action": {"target": "done <= 1'b1;", "replacement": "done <= 1'b0;",
                   "reset_signal": "rst_n"},
    },
    "p3_reset_restore_b": {
        "source": "reset_restore_b.v", "module": "reset_restore_b",
        "target": "tb_reset.v", "regression": "tb_basic.v",
        "mechanism_family": "RESET_SEMANTIC_LOSS", "family": "RESET_RESTORE",
        "domain": "rtl.RESET_RESTORE",
        "action": {"target": "complete <= 1'b1;", "replacement": "complete <= 1'b0;",
                   "reset_signal": "rst_n"},
    },
    "p3_reset_restore_c": {
        "source": "reset_restore_c.v", "module": "reset_restore_c",
        "target": "tb_reset.v", "regression": "tb_basic.v",
        "mechanism_family": "RESET_SEMANTIC_LOSS", "family": "RESET_RESTORE",
        "domain": "rtl.RESET_RESTORE",
        "action": {"target": "finished <= 1'b1;", "replacement": "finished <= 1'b0;",
                   "reset_signal": "rst_n"},
    },
    "p3_width_correct_a": {
        "source": "width_correct_a.v", "module": "width_correct_a",
        "target": "tb_width.v", "regression": "tb_basic.v",
        "mechanism_family": "WIDTH_SEMANTIC_LOSS", "family": "WIDTH_CORRECT",
        "domain": "rtl.WIDTH_CORRECT",
        "action": {"signal": "out", "target": "out = data[1:0];",
                   "replacement": "out = data[3:0];"},
    },
    "p3_width_correct_b": {
        "source": "width_correct_b.v", "module": "width_correct_b",
        "target": "tb_width.v", "regression": "tb_basic.v",
        "mechanism_family": "WIDTH_SEMANTIC_LOSS", "family": "WIDTH_CORRECT",
        "domain": "rtl.WIDTH_CORRECT",
        "action": {"signal": "result", "target": "result = sample[3:0];",
                   "replacement": "result = sample[7:0];"},
    },
    "p3_width_correct_c": {
        "source": "width_correct_c.v", "module": "width_correct_c",
        "target": "tb_width.v", "regression": "tb_basic.v",
        "mechanism_family": "WIDTH_SEMANTIC_LOSS", "family": "WIDTH_CORRECT",
        "domain": "rtl.WIDTH_CORRECT",
        "action": {"signal": "payload_out", "target": "payload_out = payload[1:0];",
                   "replacement": "payload_out = payload[5:0];"},
    },
}


def _file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _route(fixture: str) -> MemoryRoutingDecision:
    return MemoryRoutingDecision(
        decision="CONSIDER", resolved_state_id=f"r3-diverse-state-{fixture}",
        selected_rule_ids=(f"r3-diverse-rule-{fixture}",),
        selected_path_ids=(f"r3-diverse-path-{fixture}",),
        selected_asset_ids=(f"r3-diverse-asset-{fixture}",),
        applicability={"status": "APPLICABLE", "source": "r3-diverse-cohort"},
        causal_support={"status": "SUPPORTED",
                        "causal_path_ids": [f"r3-diverse-path-{fixture}"]},
        risk={"level": "heldout"}, abstain_reasons=(),
        no_memory_budget=1, memory_budget=1)


def _candidate(fixture: str, spec: dict) -> StructuredRepairCandidate:
    return StructuredRepairCandidate(
        candidate_id=f"r3-diverse-candidate-{fixture}",
        resolved_state_id=f"r3-diverse-state-{fixture}",
        knowledge_object_id="r3-diverse-routed-policy@1",
        causal_path_ids=(f"r3-diverse-path-{fixture}",),
        asset_id=f"r3-diverse-asset-{fixture}", action_family=spec["family"],
        concrete_action={"domain": spec["domain"],
                         "transformation_family": spec["family"],
                         "payload": {"module": spec["module"], **spec["action"]}},
        applicability_receipt_id=f"r3-diverse-app-{fixture}",
        binding_receipt_id=f"r3-diverse-bind-{fixture}",
        obligations=("RTL_TARGET_TEST_PASS", "RTL_FROZEN_REGRESSION_PASS",
                     "RTL_COMPILE_PASS"),
        evidence_level="L3_REPLICATED_EFFECT", authority={"eligible": True}, risk={},
        provenance={"evaluation_only": True, "source": "r3-diverse-routed-policy"},
    )


def run(artifacts: Path, fixtures: list[str] | None = None, *, force: bool = False) -> dict:
    artifacts = artifacts.expanduser().resolve()
    fixtures = list(_SPECS) if fixtures is None else list(fixtures)
    if not fixtures:
        raise RuntimeError("at least one diverse fixture is required")
    if len(set(fixtures)) != len(fixtures):
        raise RuntimeError("diverse fixtures must be unique")
    unknown = sorted(set(fixtures) - set(_SPECS))
    if unknown:
        raise RuntimeError("unknown diverse fixture(s): " + ", ".join(unknown))
    if artifacts.exists():
        if not force:
            raise RuntimeError(f"output exists; pass --force to replace it: {artifacts}")
        shutil.rmtree(artifacts)
    source_root, receipt_root = artifacts / "sources", artifacts / "receipts"
    source_root.mkdir(parents=True)
    receipt_root.mkdir()
    cases: list[dict] = []
    arm_candidates: dict[str, dict] = {}
    mechanism_families: dict[str, str] = {}
    for index, fixture in enumerate(fixtures):
        spec = _SPECS[fixture]
        source_dir = source_root / fixture
        fixture_dir = ROOT / "tests" / "fixtures" / "rtl_projects" / fixture
        for subdir in ("rtl", "tb"):
            shutil.copytree(fixture_dir / subdir, source_dir / subdir)
        source = source_dir / "rtl" / spec["source"]
        target = source_dir / "tb" / spec["target"]
        regression = source_dir / "tb" / spec["regression"]
        route = _route(fixture)
        case_id = f"r3-routed-diverse-{fixture}"
        cases.append({
            "case_id": case_id,
            "lineage_id": f"lineage-r3-diverse-{index:02d}-{fixture}",
            "rtl_source": str(source), "source_digest": _file_digest(source),
            "target_test": str(target), "frozen_regression": str(regression),
            "toolchain_digest": TOOLCHAIN_DIGEST, "oracle_digest": ORACLE_DIGEST,
            "platform_digest": PLATFORM_DIGEST, "pdk_digest": PDK_DIGEST,
            "routing_receipt_id": route.routing_receipt_id,
            "routing_decision": route.decision,
        })
        arm_candidates[case_id] = {
            arm: (None if arm == "NO_MEMORY" else _candidate(fixture, spec))
            for arm in P12_ARMS
        }
        mechanism_families[fixture] = spec["mechanism_family"]

    campaign = "tehm-r3-routed-policy-diverse-20260903-" + "-".join(fixtures)
    manifest = "sha256:r3-routed-policy-diverse-manifest-" + "-".join(fixtures)
    cohort = execute_rtl_paired_cohort(
        cases, arm_candidates, campaign_id=campaign,
        campaign_manifest_digest=manifest, platform_digest=PLATFORM_DIGEST,
        pdk_digest=PDK_DIGEST, oracle=IcarusCandidateOracle(), budget=3,
        toolchain_digest=TOOLCHAIN_DIGEST, oracle_digest=ORACLE_DIGEST,
        min_lineages=min(2, len(fixtures)))
    cohort_path = receipt_root / "cohort.json"
    _write(cohort_path, {**cohort.to_dict(), "receipt_digest": cohort.receipt_digest})
    _write(receipt_root / "cases.json", {
        "cases": cases, "mechanism_families": mechanism_families,
        "routing_decision": "CONSIDER", "memory_docs_submitted": False,
        "source_provenance": "repository_p3_fixture",
        "statistical_independence_claim": "source_lineage_disjoint_only",
    })
    summary = {
        "campaign_id": campaign, "lane": "EVOLUTION_CHALLENGE",
        "real_oracle": "iverilog/vvp", "case_count": len(cases),
        "lineage_count": cohort.lineage_count, "lineages": cohort.lineage_ids,
        "mechanism_families": mechanism_families,
        "source_provenance": "repository_p3_fixture",
        # The typed cohort gate proves source/lineage disjointness; it does
        # not by itself establish IID sampling or a production claim.
        "statistical_independence_claim": "source_lineage_disjoint_only",
        "outcome_counts": cohort.outcome_counts,
        "routed_policy_arm": "CAUSAL_NO_SKILL", "routing_decision": "CONSIDER",
        "source_mode": "buggy_selected_memory", "selected_memory_arms": 3,
        "canonical_memory_mutation": "none", "production_integration": "not_attempted",
        "evaluation_only": True, "memory_docs_submitted": False,
        "cohort_receipt": str(cohort_path),
        "cohort_receipt_digest": cohort.receipt_digest,
    }
    _write(artifacts / "summary.json", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--fixture", action="append", dest="fixtures",
                        choices=sorted(_SPECS),
                        help="diverse fixture; omit to use all 14")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        summary = run(args.artifacts, args.fixtures, force=args.force)
    except Exception as exc:
        print(f"diverse routed-policy cohort failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
