#!/usr/bin/env python3
"""Run the Revision3 real-RTL memory-interference challenge.

The challenge is an evaluation-only negative-transfer lane.  It freezes the
known-good guard-strengthened source as the no-memory baseline, then executes
a deliberately harmful structured counterfactual through real Icarus
(`iverilog`/`vvp`).  The script derives typed ``MEMORY_INTERFERENCE`` receipts,
builds the P13 reason envelope and trigger, and applies the reason-specific
admission gate.  It never opens a fixture manifest, writes SQLite/canonical
memory, changes lifecycle authority, or enters production runtime.

Usage:
    python3 scripts/run_r3_memory_interference_challenge.py \
        --artifacts /data1/zhangdy/tehm-campaigns/tehm-r3-interference-challenge-20260902

The output directory is external to the repository by default.  Use
``--force`` only when intentionally replacing a previous challenge run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
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
from tehm.evolution.admission import admit_evolution_reason  # noqa: E402
from tehm.evolution.p12_shadow_trigger import (  # noqa: E402
    build_p12_shadow_update_triggers_from_reason_receipt,
)
from tehm.evolution.reason_derivation import (  # noqa: E402
    derive_memory_interference_reason,
    p13_reason_receipt_from_derivations,
)
from tehm.retrieval.structured_candidate import StructuredRepairCandidate  # noqa: E402


CAMPAIGN_ID = "tehm-r3-interference-challenge-20260902"
TOOLCHAIN_DIGEST = "sha256:r3-icarus-toolchain"
ORACLE_DIGEST = "sha256:r3-icarus-candidate-oracle"
PLATFORM_DIGEST = "sha256:r3-platform"
PDK_DIGEST = "sha256:r3-pdk"
MANIFEST_DIGEST = "sha256:r3-interference-challenge-manifest"

_SPECS = (
    ("req_ack", "req_ack_bug", "lineage-r3-handshake-send", "SEND", "DONE", "ack",
     "SEND: next_state = DONE;          // BUG: no ack guard",
     "SEND: if (ack) next_state = DONE;"),
    ("req_write", "req_ack_bug2", "lineage-r3-handshake-write", "WRITE", "VERIFY", "wr_ack",
     "WRITE:  next_state = VERIFY;          // BUG: no wr_ack guard",
     "WRITE:  if (wr_ack) next_state = VERIFY;"),
)


def _digest_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _harmful_candidate(key: str, source_state: str, target_state: str,
                       condition: str) -> StructuredRepairCandidate:
    # This removes the guard from the already-fixed baseline.  It is an
    # executable counterfactual used to detect interference, not a mutation
    # plan and not an authority-bearing memory object.
    target = (
        rf"(?m)^[ \t]*{re.escape(source_state)}:[ \t]*if[ \t]*"
        rf"\({re.escape(condition)}\)[ \t]*next_state[ \t]*=[ \t]*"
        rf"{re.escape(target_state)}[ \t]*;"
    )
    return StructuredRepairCandidate(
        candidate_id=f"r3-harmful-memory-{key}",
        resolved_state_id=f"r3-state-{key}",
        knowledge_object_id="r3-shadow-challenge@1",
        causal_path_ids=(f"r3-path-{key}",),
        asset_id="r3-harmful-guard-removal",
        action_family="AST_REWRITE",
        concrete_action={
            "domain": "rtl.AST_REWRITE",
            "transformation_family": "AST_REWRITE",
            "payload": {
                "target": target,
                "replacement": f"{source_state}: next_state = {target_state};",
                "count": 1,
            },
        },
        applicability_receipt_id=f"r3-app-{key}",
        binding_receipt_id=f"r3-bind-{key}",
        obligations=("RTL_TARGET_TEST_PASS", "RTL_FROZEN_REGRESSION_PASS", "RTL_COMPILE_PASS"),
        evidence_level="L3_REPLICATED_EFFECT",
        authority={"eligible": True}, risk={},
        provenance={"evaluation_only": True, "source": "r3_external_challenge_fixture"},
    )


def run(artifacts: Path, *, force: bool = False) -> dict:
    if artifacts.exists():
        if not force:
            raise RuntimeError(f"output exists; pass --force to replace it: {artifacts}")
        shutil.rmtree(artifacts)
    (artifacts / "sources").mkdir(parents=True)
    receipts_dir = artifacts / "receipts"
    receipts_dir.mkdir()

    cases: list[dict] = []
    arm_candidates: dict[str, dict] = {}
    routes: dict[str, MemoryRoutingDecision] = {}
    candidate_payloads: dict[str, dict] = {}
    fixtures = ROOT / "tests" / "fixtures" / "rtl_projects"

    for key, fixture, lineage, source_state, target_state, condition, buggy, fixed in _SPECS:
        source_dir = artifacts / "sources" / key
        source_dir.mkdir(parents=True)
        fixture_dir = fixtures / fixture
        # Do not copy manifest.json: fixture manifests contain gold/fix fields,
        # while this challenge must be driven only by source and testbench paths.
        for subdir in ("rtl", "tb"):
            shutil.copytree(fixture_dir / subdir, source_dir / subdir)
        source = source_dir / "rtl" / "req_ack_fsm.v"
        original = source.read_text()
        if buggy not in original:
            raise RuntimeError(f"fixture bug marker missing for {fixture}")
        source.write_text(original.replace(buggy, fixed, 1))

        candidate = _harmful_candidate(key, source_state, target_state, condition)
        case_id = f"r3-interference-{key}"
        route = MemoryRoutingDecision(
            decision="CONSIDER", resolved_state_id=f"r3-state-{key}",
            selected_rule_ids=(f"r3-rule-{key}",),
            selected_path_ids=(f"r3-path-{key}",),
            selected_asset_ids=("r3-harmful-guard-removal",),
            applicability={"status": "APPLICABLE"},
            causal_support={"causal_path_ids": [f"r3-path-{key}"]},
            risk={"level": "challenge"}, abstain_reasons=(),
            no_memory_budget=1, memory_budget=1,
        )
        case = {
            "case_id": case_id, "lineage_id": lineage,
            "rtl_source": str(source), "source_digest": _digest_file(source),
            "target_test": str(source_dir / "tb" / "tb_handshake.v"),
            "frozen_regression": str(source_dir / "tb" / "tb_basic.v"),
            "toolchain_digest": TOOLCHAIN_DIGEST, "oracle_digest": ORACLE_DIGEST,
            "platform_digest": PLATFORM_DIGEST, "pdk_digest": PDK_DIGEST,
            "routing_receipt_id": route.routing_receipt_id,
            "routing_decision": route.decision,
        }
        cases.append(case)
        routes[case_id] = route
        candidate_payloads[case_id] = candidate.to_dict()
        arm_candidates[case_id] = {
            arm: None if arm == "NO_MEMORY" else candidate for arm in P12_ARMS
        }

    cohort = execute_rtl_paired_cohort(
        cases, arm_candidates, campaign_id=CAMPAIGN_ID,
        campaign_manifest_digest=MANIFEST_DIGEST,
        platform_digest=PLATFORM_DIGEST, pdk_digest=PDK_DIGEST,
        oracle=IcarusCandidateOracle(), budget=3,
        toolchain_digest=TOOLCHAIN_DIGEST, oracle_digest=ORACLE_DIGEST,
        min_lineages=2,
    )

    derivations = {}
    for case_id, paired in cohort.case_receipts.items():
        reason = derive_memory_interference_reason(paired, campaign_id=CAMPAIGN_ID)
        if reason is None:
            raise RuntimeError(f"expected MEMORY_INTERFERENCE for {case_id}")
        derivations[case_id] = (reason,)
    reason_receipt = p13_reason_receipt_from_derivations(
        derivations, campaign_id=CAMPAIGN_ID,
        cohort_receipt_digest=cohort.receipt_digest,
    )
    eligibility = {case_id: True for case_id in cohort.case_receipts}
    triggers = build_p12_shadow_update_triggers_from_reason_receipt(
        cohort, memory_arm="ALWAYS_MEMORY", learner_eligible=True,
        reason_receipt=reason_receipt, min_lineages=2,
        routing_decisions=routes, case_learner_eligibility=eligibility,
        derivation_receipts=derivations,
    )
    admissions = {
        case_id: admit_evolution_reason(
            derivations[case_id][0], campaign_id=CAMPAIGN_ID,
            learner_eligible=True, paired=cohort.case_receipts[case_id],
        )
        for case_id in sorted(cohort.case_receipts)
    }

    _write_json(receipts_dir / "campaign_manifest.json", {
        "version": "tehm-r3-interference-challenge-v0.1",
        "campaign_id": CAMPAIGN_ID, "lane": "EVOLUTION_CHALLENGE",
        "oracle": "IcarusOracle via iverilog/vvp",
        "purpose": "negative-transfer challenge: fixed baseline vs harmful memory counterfactual",
        "evaluation_only": True, "canonical_memory_mutation": "none",
        "source_lineages": [case["lineage_id"] for case in cases],
        "memory_docs_submitted": False,
    })
    _write_json(receipts_dir / "cases.json", {
        "cases": cases, "candidate_payloads": candidate_payloads,
        "routing": {
            case_id: {**route.to_dict(), "decision_digest": route.decision_digest,
                      "routing_receipt_id": route.routing_receipt_id}
            for case_id, route in routes.items()
        },
    })
    _write_json(receipts_dir / "cohort.json",
                {**cohort.to_dict(), "receipt_digest": cohort.receipt_digest})
    _write_json(receipts_dir / "reason_derivation.json", {
        "derivations": {
            case_id: [{**item.to_dict(), "receipt_id": item.receipt_id,
                       "receipt_digest": item.receipt_digest} for item in items]
            for case_id, items in derivations.items()
        }
    })
    _write_json(receipts_dir / "p13_reason_receipt.json",
                {**reason_receipt.to_dict(), "receipt_digest": reason_receipt.receipt_digest})
    _write_json(receipts_dir / "p12_triggers.json", {
        "triggers": [{**item.to_dict(), "receipt_digest": item.receipt_digest}
                     for item in triggers]
    })
    _write_json(receipts_dir / "admissions.json", {
        "admissions": {
            case_id: {**item.to_dict(), "receipt_id": item.receipt_id,
                      "receipt_digest": item.receipt_digest}
            for case_id, item in admissions.items()
        }
    })
    summary = {
        "campaign_id": CAMPAIGN_ID, "lane": "EVOLUTION_CHALLENGE",
        "real_oracle": "iverilog/vvp", "case_count": len(cohort.case_receipts),
        "lineage_count": cohort.lineage_count, "lineages": cohort.lineage_ids,
        "outcome_counts": cohort.outcome_counts,
        "derived_reasons": reason_receipt.evolution_reasons,
        "triggered_count": sum(item.triggered for item in triggers),
        "trigger_reasons": {item.case_id: item.reason for item in triggers},
        "admitted_count": sum(item.admitted for item in admissions.values()),
        "admission_reasons": {case_id: item.blocked_reason
                              for case_id, item in admissions.items()},
        "evaluation_only": True, "canonical_memory_mutation": "none",
        "memory_docs_submitted": False,
    }
    _write_json(artifacts / "summary.json", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--artifacts", type=Path,
        default=Path("/tmp") / CAMPAIGN_ID,
        help=f"external output directory (default: /tmp/{CAMPAIGN_ID})",
    )
    parser.add_argument("--force", action="store_true",
                        help="replace an existing output directory")
    args = parser.parse_args(argv)
    try:
        summary = run(args.artifacts.expanduser().resolve(), force=args.force)
    except Exception as exc:
        print(f"challenge failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
