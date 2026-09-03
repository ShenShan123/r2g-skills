#!/usr/bin/env python3
"""Freeze a real-Icarus source-disjoint routed-policy MIR cohort.

The producer has two explicit evaluation modes.  ``NO_SKILL`` keeps the
legacy no-memory fallback cohort: the copied source is repaired before the
pair is run.  ``CONSIDER`` keeps the copied source buggy and routes the
selected structured candidate through the causal arm, so the paired receipt
contains a real baseline-fail/candidate-pass observation.  Both modes are
evaluation-only and external: no fixture manifest, SQLite store, canonical
memory, or production authority is opened or changed.
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

_FIXTURES = {
    "req_ack_bug3": ("lineage-r3-heldout-read", "RCV", "RD_DONE", "rd_ack",
                      "RCV:    next_state = RD_DONE;       // BUG: no rd_ack guard",
                      "RCV:    if (rd_ack) next_state = RD_DONE;"),
    "req_ack_bug4": ("lineage-r3-heldout-ready", "WAIT", "DONE", "ready",
                      "WAIT: next_state = DONE;       // BUG: no ready guard",
                      "WAIT: if (ready) next_state = DONE;"),
    "valid_ready_bug": ("lineage-r3-heldout-valid-ready", "ACCEPT", "COMMIT", "ready",
                         "ACCEPT: next_state = COMMIT; // BUG: commit must wait for ready",
                         "ACCEPT: if (ready) next_state = COMMIT;"),
    "fifo_space_bug": ("lineage-r3-heldout-fifo", "DRAIN", "DONE", "space_return",
                        "DRAIN: next_state = DONE; // BUG: must wait for returned capacity",
                        "DRAIN: if (space_return) next_state = DONE;"),
}


def _sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _candidate(fixture: str, source_state: str, target_state: str,
               condition: str) -> StructuredRepairCandidate:
    return StructuredRepairCandidate(
        candidate_id=f"r3-routed-candidate-{fixture}",
        resolved_state_id=f"r3-state-{fixture}",
        knowledge_object_id="r3-routed-policy-cohort@1",
        causal_path_ids=(f"r3-path-{fixture}",),
        asset_id="r3-guard-strengthen", action_family="GUARD_STRENGTHEN",
        concrete_action={
            "domain": "rtl.GUARD_STRENGTHEN",
            "transformation_family": "GUARD_STRENGTHEN",
            "payload": {"module": "req_ack_fsm", "source_state": source_state,
                        "target_state": target_state, "add_condition": condition},
        },
        applicability_receipt_id=f"r3-app-{fixture}",
        binding_receipt_id=f"r3-bind-{fixture}",
        obligations=("RTL_TARGET_TEST_PASS", "RTL_FROZEN_REGRESSION_PASS",
                     "RTL_COMPILE_PASS"),
        evidence_level="L3_REPLICATED_EFFECT", authority={"eligible": True},
        risk={}, provenance={"evaluation_only": True, "source": "r3-heldout-cohort"},
    )


def _route(fixture: str, routing_decision: str) -> MemoryRoutingDecision:
    if routing_decision == "NO_SKILL":
        return MemoryRoutingDecision(
            decision="NO_SKILL", resolved_state_id=f"r3-state-{fixture}",
            selected_rule_ids=(), selected_path_ids=(), selected_asset_ids=(),
            applicability={"status": "NO_MATCH"},
            causal_support={"status": "NONE"}, risk={"level": "heldout"},
            abstain_reasons=(), no_memory_budget=1, memory_budget=0,
            no_skill_reason="NO_MATCH")
    if routing_decision == "CONSIDER":
        return MemoryRoutingDecision(
            decision="CONSIDER", resolved_state_id=f"r3-state-{fixture}",
            selected_rule_ids=(f"r3-rule-{fixture}",),
            selected_path_ids=(f"r3-path-{fixture}",),
            selected_asset_ids=("r3-guard-strengthen",),
            applicability={"status": "APPLICABLE", "source": "r3-heldout"},
            causal_support={"status": "SUPPORTED",
                            "causal_path_ids": [f"r3-path-{fixture}"]},
            risk={"level": "heldout"}, abstain_reasons=(),
            no_memory_budget=1, memory_budget=1)
    raise RuntimeError(
        f"unsupported routed-policy decision {routing_decision!r}; "
        "use NO_SKILL or CONSIDER")


def run(artifacts: Path, fixtures: list[str], *, force: bool = False,
        routing_decision: str = "NO_SKILL") -> dict:
    artifacts = artifacts.expanduser().resolve()
    if artifacts.exists():
        if not force:
            raise RuntimeError(f"output exists; pass --force to replace it: {artifacts}")
        shutil.rmtree(artifacts)
    source_root = artifacts / "sources"
    receipt_root = artifacts / "receipts"
    source_root.mkdir(parents=True)
    receipt_root.mkdir()
    cases = []
    arms = {}
    for fixture in fixtures:
        if fixture not in _FIXTURES:
            raise RuntimeError(f"unknown held-out fixture: {fixture}")
        lineage, source_state, target_state, condition, buggy, fixed = _FIXTURES[fixture]
        source_dir = source_root / fixture
        fixture_dir = ROOT / "tests" / "fixtures" / "rtl_projects" / fixture
        for subdir in ("rtl", "tb"):
            shutil.copytree(fixture_dir / subdir, source_dir / subdir)
        source = source_dir / "rtl" / "req_ack_fsm.v"
        text = source.read_text()
        if buggy not in text:
            raise RuntimeError(f"guard marker missing for {fixture}")
        # NO_SKILL deliberately uses a repaired source so the fallback is a
        # valid control.  CONSIDER leaves the source buggy; only the selected
        # structured candidate may repair it inside the disposable oracle.
        if routing_decision == "NO_SKILL":
            source.write_text(text.replace(buggy, fixed, 1))
        elif routing_decision != "CONSIDER":
            raise RuntimeError(
                f"unsupported routed-policy decision {routing_decision!r}; "
                "use NO_SKILL or CONSIDER")
        case_id = f"r3-routed-{fixture}"
        route = _route(fixture, routing_decision)
        case = {
            "case_id": case_id, "lineage_id": lineage,
            "rtl_source": str(source), "source_digest": _sha(source),
            "target_test": str(source_dir / "tb" / "tb_handshake.v"),
            "frozen_regression": str(source_dir / "tb" / "tb_basic.v"),
            "toolchain_digest": TOOLCHAIN_DIGEST, "oracle_digest": ORACLE_DIGEST,
            "platform_digest": PLATFORM_DIGEST, "pdk_digest": PDK_DIGEST,
            "routing_receipt_id": route.routing_receipt_id,
            "routing_decision": route.decision,
        }
        if route.no_skill_reason is not None:
            case["no_skill_reason"] = route.no_skill_reason
        cases.append(case)
        candidate = _candidate(fixture, source_state, target_state, condition)
        if routing_decision == "NO_SKILL":
            # The source is already repaired.  Structured arms are retained in
            # the pair, while the routed causal arm is explicitly no-memory
            # fallback.
            arms[case_id] = {
                arm: (None if arm in {"NO_MEMORY", "CAUSAL_NO_SKILL"}
                      else candidate) for arm in P12_ARMS}
        else:
            # On the buggy source every memory arm receives the same selected
            # candidate; the causal arm is now a genuine CONSIDER execution.
            arms[case_id] = {
                arm: (None if arm == "NO_MEMORY" else candidate)
                for arm in P12_ARMS}

    campaign = ("tehm-r3-routed-policy-cohort-20260903-" +
                routing_decision.lower() + "-" + "-".join(fixtures))
    manifest = ("sha256:r3-routed-policy-manifest-" +
                routing_decision.lower() + "-" + "-".join(fixtures))
    cohort = execute_rtl_paired_cohort(
        cases, arms, campaign_id=campaign, campaign_manifest_digest=manifest,
        platform_digest=PLATFORM_DIGEST, pdk_digest=PDK_DIGEST,
        oracle=IcarusCandidateOracle(), budget=3,
        toolchain_digest=TOOLCHAIN_DIGEST, oracle_digest=ORACLE_DIGEST,
        min_lineages=min(2, len(fixtures)),
    )
    cohort_path = receipt_root / "cohort.json"
    _write(cohort_path, {**cohort.to_dict(), "receipt_digest": cohort.receipt_digest})
    _write(receipt_root / "cases.json", {"cases": cases,
                                          "memory_docs_submitted": False})
    summary = {
        "campaign_id": campaign, "lane": "EVOLUTION_CHALLENGE",
        "real_oracle": "iverilog/vvp", "case_count": len(cases),
        "lineage_count": cohort.lineage_count, "lineages": cohort.lineage_ids,
        "outcome_counts": cohort.outcome_counts,
        "routed_policy_arm": "CAUSAL_NO_SKILL",
        "routing_decision": routing_decision,
        "source_mode": ("repaired_control" if routing_decision == "NO_SKILL"
                         else "buggy_selected_memory"),
        "selected_memory_arms": (0 if routing_decision == "NO_SKILL" else 3),
        "canonical_memory_mutation": "none", "production_integration": "not_attempted",
        "evaluation_only": True, "memory_docs_submitted": False,
        "cohort_receipt": str(cohort_path), "cohort_receipt_digest": cohort.receipt_digest,
    }
    _write(artifacts / "summary.json", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--fixture", action="append", dest="fixtures", required=True,
                        choices=sorted(_FIXTURES),
                        help="held-out fixture; repeat for a source-disjoint cohort")
    parser.add_argument(
        "--routing-decision", choices=("NO_SKILL", "CONSIDER"), default="NO_SKILL",
        help=("routing arm to freeze: NO_SKILL creates the repaired fallback "
              "control; CONSIDER keeps buggy RTL and executes the selected "
              "structured candidate"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        summary = run(args.artifacts, args.fixtures, force=args.force,
                      routing_decision=args.routing_decision)
    except Exception as exc:
        print(f"routed-policy cohort failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
