#!/usr/bin/env python3
"""Run a real, evaluation-only Revision3 P15 calibration cohort.

The cohort is deliberately separate from the training/evolution database.  It
copies only RTL and testbench files, executes every case through the four P12
arms with Icarus, and derives oracle labels from the typed paired receipts
instead of from the router prediction.  The resulting report is external
evidence only: no canonical memory, support envelope, authority, or
production runtime is written.

Usage::

    PYTHONDONTWRITEBYTECODE=1 python3 memory/scripts/run_r3_p15_calibration.py \
      --evolution-artifacts /data1/zhangdy/tehm-campaigns/tehm-r3-state-shift-challenge-20260902-r37 \
      --artifacts /data1/zhangdy/tehm-campaigns/tehm-r3-p15-calibration-20260902
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
from tehm.evaluation.no_skill_calibration import (  # noqa: E402
    derive_no_skill_oracle_label,
)
from tehm.evaluation.rtl_candidate_oracle import IcarusCandidateOracle  # noqa: E402
from tehm.evaluation.rtl_cohort import execute_rtl_paired_cohort  # noqa: E402
from tehm.ids import stable_dumps  # noqa: E402
from tehm.knowledge import MechanismKnowledge  # noqa: E402
from tehm.retrieval.structured_candidate import StructuredRepairCandidate  # noqa: E402
from tehm.state import evaluate_state_shift  # noqa: E402
from tehm.state.support_envelope import SupportEnvelope  # noqa: E402
from scripts.build_no_skill_calibration_report import (  # noqa: E402
    MANIFEST_VERSION,
    build_no_skill_calibration_report,
)


CAMPAIGN_ID = "tehm-r3-p15-calibration-20260902"
TOOLCHAIN_DIGEST = "sha256:r3-p15-icarus-toolchain"
ORACLE_DIGEST = "sha256:r3-p15-icarus-candidate-oracle"
PLATFORM_DIGEST = "sha256:r3-p15-calibration-asap7"
PDK_DIGEST = "sha256:r3-p15-calibration-pdk"
MANIFEST_DIGEST = "sha256:r3-p15-calibration-manifest"

_FIXTURES = (
    {
        "key": "send", "fixture": "req_ack_bug", "source_state": "SEND",
        "target_state": "DONE", "condition": "ack",
        "buggy": "SEND: next_state = DONE;          // BUG: no ack guard",
        "fixed": "SEND: if (ack) next_state = DONE;",
    },
    {
        "key": "write", "fixture": "req_ack_bug2", "source_state": "WRITE",
        "target_state": "VERIFY", "condition": "wr_ack",
        "buggy": "WRITE:  next_state = VERIFY;          // BUG: no wr_ack guard",
        "fixed": "WRITE:  if (wr_ack) next_state = VERIFY;",
    },
    {
        "key": "read", "fixture": "req_ack_bug3", "source_state": "RCV",
        "target_state": "RD_DONE", "condition": "rd_ack",
        "buggy": "RCV:    next_state = RD_DONE;       // BUG: no rd_ack guard",
        "fixed": "RCV:    if (rd_ack) next_state = RD_DONE;",
    },
    {
        "key": "ready", "fixture": "req_ack_bug4", "source_state": "WAIT",
        "target_state": "DONE", "condition": "ready",
        "buggy": "WAIT: next_state = DONE;       // BUG: no ready guard",
        "fixed": "WAIT: if (ready) next_state = DONE;",
    },
)


def _digest(payload: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(payload).encode()).hexdigest()


def _file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _candidate(spec: dict, case_id: str, *, harmful: bool,
               safe: bool = False) -> StructuredRepairCandidate:
    state = re.escape(spec["source_state"])
    target = re.escape(spec["target_state"])
    condition = re.escape(spec["condition"])
    if harmful:
        action_target = (
            rf"(?m)^[ \t]*{state}:[ \t]*if[ \t]*\([ \t]*{condition}"
            rf"[ \t]*\)[ \t]*next_state[ \t]*=[ \t]*{target}[ \t]*;"
        )
        replacement = f"{spec['source_state']}: next_state = {spec['target_state']};"
        family = "r3_p15_harmful_guard_removal"
    elif safe:
        action_target = (
            rf"(?m)^[ \t]*{state}:[ \t]*"
            rf"(?:if[ \t]*\([ \t]*{condition}[ \t]*\)[ \t]*)?"
            rf"next_state[ \t]*=[ \t]*{target}[ \t]*;"
        )
        replacement = (
            f"{spec['source_state']}: if ({spec['condition']}) "
            f"next_state = {spec['target_state']};"
        )
        family = "r3_p15_safe_guard_strengthen"
    else:
        # This no-op-on-the-bug candidate is executable but cannot improve the
        # source.  It supplies an honest typed NO_MATCH denominator instead of
        # a hand-written label or an UNKNOWN oracle outcome.
        action_target = (
            rf"(?m)^[ \t]*{state}:[ \t]*next_state[ \t]*=[ \t]*{target}[ \t]*;"
        )
        replacement = f"{spec['source_state']}: next_state = {spec['target_state']};"
        family = "r3_p15_no_match_control"
    return StructuredRepairCandidate(
        candidate_id=f"{family}-{case_id}",
        resolved_state_id=f"r3-p15-state-{case_id}",
        knowledge_object_id="r3-handshake-knowledge@1",
        causal_path_ids=("r3-path-handshake",),
        asset_id=family,
        action_family="AST_REWRITE",
        concrete_action={
            "domain": "rtl.AST_REWRITE",
            "transformation_family": "AST_REWRITE",
            "payload": {"target": action_target, "replacement": replacement, "count": 1},
        },
        applicability_receipt_id=f"r3-p15-app-{case_id}",
        binding_receipt_id=f"r3-p15-bind-{case_id}",
        obligations=("RTL_TARGET_TEST_PASS", "RTL_FROZEN_REGRESSION_PASS", "RTL_COMPILE_PASS"),
        evidence_level="L3_REPLICATED_EFFECT", authority={"eligible": True}, risk={},
        provenance={"evaluation_only": True, "source": "r3_p15_calibration"},
    )


def _route(case_id: str, category: str, state_shift_receipt: dict | None) -> MemoryRoutingDecision:
    if category == "safe_memory":
        return MemoryRoutingDecision(
            decision="CONSIDER", resolved_state_id=f"r3-p15-state-{case_id}",
            selected_rule_ids=("r3-p15-safe-rule",),
            selected_path_ids=("r3-path-handshake",),
            selected_asset_ids=("r3-p15-safe-asset",),
            applicability={"status": "APPLICABLE", "calibration_category": category},
            causal_support={"status": "SUPPORTED"}, risk={},
            abstain_reasons=(), no_memory_budget=1, memory_budget=1)
    reason = {"state_shift": "STATE_SHIFT", "risk": "RISK", "no_match": "NO_MATCH"}[category]
    kwargs = {}
    if state_shift_receipt is not None:
        kwargs = {"state_shift_receipt_id": state_shift_receipt["receipt_id"]}
    return MemoryRoutingDecision(
        decision="NO_SKILL", resolved_state_id=f"r3-p15-state-{case_id}",
        selected_rule_ids=(), selected_path_ids=(), selected_asset_ids=(),
        applicability={"status": "ABSTAIN", "calibration_category": category},
        causal_support={"status": "SUPPORTED"}, risk={"calibration_category": category},
        abstain_reasons=(f"calibration_{category}",), no_memory_budget=1, memory_budget=0,
        no_skill_reason=reason, **kwargs)


def _strata(category: str, spec: dict) -> dict[str, str]:
    return {
        "mechanism_family": "HANDSHAKE_COMPLETION",
        "design": f"req_ack_{spec['key']}",
        "platform": "asap7",
        "flow_regime": "rtl_icarus_calibration",
        "model_identity": "typed-paired-oracle-v1",
        "state_shift_dimension": "flow_shift" if category == "state_shift" else "none",
    }


def run(evolution_artifacts: Path, artifacts: Path, *, force: bool = False) -> dict:
    evolution_artifacts = evolution_artifacts.expanduser().resolve()
    artifacts = artifacts.expanduser().resolve()
    training_path = evolution_artifacts / "receipts" / "training.json"
    if not training_path.is_file():
        raise RuntimeError(f"missing frozen training receipt: {training_path}")
    training = json.loads(training_path.read_text())
    knowledge = MechanismKnowledge.from_dict(training["knowledge"])
    envelope = SupportEnvelope.from_dict(training["support_envelope"])
    if envelope.training_only is not True or envelope.knowledge_object_id != knowledge.object_id:
        raise RuntimeError("training support envelope is not a matching training-only witness")
    if artifacts.exists():
        if not force:
            raise RuntimeError(f"output exists; pass --force to replace it: {artifacts}")
        shutil.rmtree(artifacts)
    source_root = artifacts / "sources"
    receipts_root = artifacts / "receipts"
    source_root.mkdir(parents=True)
    receipts_root.mkdir()

    categories = ("state_shift", "risk", "no_match", "safe_memory")
    cases: list[dict] = []
    arm_candidates: dict[str, dict] = {}
    routes: dict[str, MemoryRoutingDecision] = {}
    shifts: dict[str, object] = {}
    category_by_case: dict[str, str] = {}
    specs_by_case: dict[str, dict] = {}
    # Twenty real cases provide the default P15 denominator while retaining
    # multiple design lineages and all three reason strata.
    for index in range(20):
        spec = _FIXTURES[index % len(_FIXTURES)]
        category = categories[index % len(categories)]
        case_id = f"r3-p15-calibration-{index:02d}-{spec['key']}-{category}"
        case_root = source_root / case_id
        fixture_root = ROOT / "tests" / "fixtures" / "rtl_projects" / spec["fixture"]
        for subdir in ("rtl", "tb"):
            shutil.copytree(fixture_root / subdir, case_root / subdir)
        source = case_root / "rtl" / "req_ack_fsm.v"
        original = source.read_text()
        if category in {"state_shift", "risk"}:
            if spec["buggy"] not in original:
                raise RuntimeError(f"calibration bug marker missing: {spec['fixture']}")
            original = original.replace(spec["buggy"], spec["fixed"], 1)
        # The comment makes each frozen source content-disjoint without
        # changing executable behavior; the design/testbench remains real RTL.
        source.write_text(original + f"\n// TEHM P15 calibration case {index:02d}\n")

        state_shift = None
        if category == "state_shift":
            state_shift = evaluate_state_shift(
                {"mechanism_family": knowledge.mechanism_family,
                 "compatibility_profile": knowledge.compatibility_profile,
                 "platform": "asap7"},
                {"resolution_id": f"r3-p15-state-{case_id}"}, knowledge, envelope,
                evidence_refs=(case_id, "calibration"))
            shifts[case_id] = state_shift
        state_shift_payload = None if state_shift is None else state_shift.to_dict()
        route = _route(case_id, category, state_shift_payload)
        candidate = _candidate(
            spec, case_id, harmful=category in {"state_shift", "risk"},
            safe=category == "safe_memory")
        case = {
            "case_id": case_id, "lineage_id": f"lineage-r3-p15-{index:02d}",
            "rtl_source": str(source), "source_digest": _file_digest(source),
            "target_test": str(case_root / "tb" / "tb_handshake.v"),
            "frozen_regression": str(case_root / "tb" / "tb_basic.v"),
            "toolchain_digest": TOOLCHAIN_DIGEST, "oracle_digest": ORACLE_DIGEST,
            "platform_digest": PLATFORM_DIGEST, "pdk_digest": PDK_DIGEST,
            "routing_receipt_id": route.routing_receipt_id,
            "routing_decision": route.decision, "no_skill_reason": route.no_skill_reason,
            **({"state_shift_receipt_id": route.state_shift_receipt_id}
               if route.state_shift_receipt_id else {}),
        }
        cases.append(case)
        arm_candidates[case_id] = {
            "NO_MEMORY": None, "ALWAYS_MEMORY": candidate,
            "APPLICABILITY_GATED": candidate, "CAUSAL_NO_SKILL": candidate,
        }
        routes[case_id] = route
        category_by_case[case_id] = category
        specs_by_case[case_id] = spec

    cohort = execute_rtl_paired_cohort(
        cases, arm_candidates, campaign_id=CAMPAIGN_ID,
        campaign_manifest_digest=MANIFEST_DIGEST,
        platform_digest=PLATFORM_DIGEST, pdk_digest=PDK_DIGEST,
        oracle=IcarusCandidateOracle(), budget=3,
        toolchain_digest=TOOLCHAIN_DIGEST, oracle_digest=ORACLE_DIGEST,
        min_lineages=4)

    derivations: dict[str, dict] = {}
    oracle_labels: dict[str, dict] = {}
    paired_index: dict[str, dict] = {}
    for case_id, paired in sorted(cohort.case_receipts.items()):
        label = derive_no_skill_oracle_label(
            paired, state_shift_receipt=shifts.get(case_id),
            strata=_strata(category_by_case[case_id], specs_by_case[case_id]),
            confidence=0.95, split="calibration")
        derivations[case_id] = label["derivation"]
        oracle_labels[case_id] = {
            "expected_decision": label["expected_decision"],
            "expected_reason": label["expected_reason"],
            "confidence": label["confidence"], "strata": label["strata"],
        }
        paired_index[case_id] = {"routing_receipt_id": paired.routing_receipt_id}

    cohort_path = receipts_root / "cohort.json"
    _write_json(cohort_path, {**cohort.to_dict(), "receipt_digest": cohort.receipt_digest})
    routing_path = receipts_root / "routing.json"
    _write_json(routing_path, {
        "routes": {case_id: {**route.to_dict(),
                              "routing_receipt_id": route.routing_receipt_id,
                              "decision_digest": route.decision_digest}
                   for case_id, route in sorted(routes.items())}
    })
    derivation_path = receipts_root / "oracle_label_derivations.json"
    _write_json(derivation_path, {
        "version": "no-skill-oracle-label-derivations-v1",
        "campaign_id": CAMPAIGN_ID, "split": "calibration",
        "derivations": derivations,
        "evaluation_only": True, "canonical_memory_mutation": "none",
    })
    manifest = {
        "version": MANIFEST_VERSION,
        "campaign_id": CAMPAIGN_ID, "split": "calibration",
        "oracle_label_source": "typed-paired-icarus-oracle-v1",
        "paired_routing_index": {"case_receipts": paired_index},
        "routing_decisions": {
            case_id: {**route.to_dict(), "routing_receipt_id": route.routing_receipt_id}
            for case_id, route in sorted(routes.items())
        },
        "oracle_labels": oracle_labels,
        "evidence_refs": [
            {"id": "p15-cohort", "path": str(cohort_path),
             "sha256": _file_digest(cohort_path)},
            {"id": "p15-routing", "path": str(routing_path),
             "sha256": _file_digest(routing_path)},
            {"id": "p15-oracle-label-derivations", "path": str(derivation_path),
             "sha256": _file_digest(derivation_path)},
        ],
    }
    manifest_path = receipts_root / "calibration_manifest.json"
    _write_json(manifest_path, manifest)
    report_path = receipts_root / "calibration_report.json"
    report = build_no_skill_calibration_report(
        manifest_path, output=report_path,
        minimum_sample_count=20, minimum_reason_cases=2, calibration_bins=10)
    summary = {
        "campaign_id": CAMPAIGN_ID, "split": "calibration",
        "real_oracle": "iverilog/vvp", "case_count": len(cohort.case_receipts),
        "lineage_count": cohort.lineage_count, "lineages": cohort.lineage_ids,
        "outcome_counts": cohort.outcome_counts,
        "derived_oracle_decisions": {
            case_id: label["expected_decision"] for case_id, label in oracle_labels.items()
        },
        "derived_oracle_reasons": {
            case_id: label["expected_reason"] for case_id, label in oracle_labels.items()
        },
        "no_skill_calibration": report["receipt"],
        "canonical_memory_mutation": "none", "production_authority_changed": False,
        "production_promotion_eligible": False,
        "evaluation_only": True, "memory_docs_submitted": False,
    }
    _write_json(artifacts / "summary.json", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--evolution-artifacts", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path,
                        default=Path("/tmp") / CAMPAIGN_ID)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        summary = run(args.evolution_artifacts, args.artifacts, force=args.force)
    except Exception as exc:
        print(f"P15 calibration failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["no_skill_calibration"]["eligible"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
