#!/usr/bin/env python3
"""Build an evaluation-only C1-C8 attribution receipt from real ORFS pairs.

The script intentionally stops at an evaluation runtime.  Training pairs are
captured into a derived v4 database; held-out and non-target pairs stay outside
that learner snapshot so the firewall is auditable.  No canonical database,
capability lifecycle, or production policy is mutated.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tehm import db  # noqa: E402
from tehm.artifact_store import ArtifactStore  # noqa: E402
from tehm.canonical.capture import capture  # noqa: E402
from tehm.causal.orfs import _backup_database, _sha256  # noqa: E402
from tehm.causal.mechanism import action_digest  # noqa: E402
from tehm.capability import (  # noqa: E402
    create_policy_snapshot, evaluate_capability_campaign,
    load_policy_snapshot, record_capability_evidence, record_policy_load,
    record_capability_authority, register_capability,
    verify_capability_authority,
)
from tehm.lifecycle.promotion_gates import (  # noqa: E402
    evaluate_capability_promotion_gates, evaluate_promotion_gates,
)
from tehm.adapters.orfs_pair import build_orfs_pair_record  # noqa: E402


def _stable_digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False).encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _strict_text(value, *, label: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{label}_malformed")
    return value.strip()


def _target_check(value) -> str:
    return _strict_text("route" if value is None else value,
                        label="target_check")


def _pair(before: str, after: str, lineage: str, config_json: str,
          target_check: str):
    lineage = _strict_text(lineage, label="lineage_id")
    target_check = _target_check(target_check)
    config_edits = json.loads(config_json)
    if not isinstance(config_edits, Mapping):
        raise ValueError("config_edits_malformed")
    return {
        "before_project": before, "after_project": after,
        "lineage_id": lineage, "config_edits": dict(config_edits),
        "target_check": target_check,
    }


def _build_record(spec: dict):
    if not isinstance(spec, Mapping):
        raise ValueError("pair_spec_malformed")
    lineage_id = _strict_text(spec.get("lineage_id"), label="lineage_id")
    target_check = _target_check(spec.get("target_check"))
    config_edits = spec.get("config_edits")
    if not isinstance(config_edits, Mapping):
        raise ValueError("config_edits_malformed")
    record = build_orfs_pair_record(
        Path(spec["before_project"]), Path(spec["after_project"]),
        lineage_id=lineage_id,
        target_check=target_check,
        config_edits=dict(config_edits),
        transformation_family="DENSITY_RELIEF")
    return record


def _summary(record, *, target_check: str) -> dict:
    delta = record.observation_delta
    return {
        "record_id": record.record_id,
        "lineage_id": record.lineage_id,
        "target_check": target_check,
        "original_failure": delta.get("original_failure"),
        "before_failed": delta.get("original_failure") == "REMOVED",
        "candidate_verdict": record.verification.get("verdict"),
        "candidate_pass": record.verification.get("verdict") == "PASS",
        "created_regressions": list(delta.get("created_regressions") or []),
        "utility_verdict": delta.get("utility_verdict"),
        "action_digest": action_digest(record.action),
        "evidence_refs": list(record.verification.get("evidence_refs") or []),
    }


def _run_evaluation_policy(conn, *, policy_snapshot_id: str,
                           records: list, causal_path_id: str,
                           runtime_id: str) -> dict:
    """Execute the tiny evaluation policy runtime and emit a bound receipt.

    This is deliberately not the production selector.  It loads the exact
    content-addressed policy snapshot, then makes a deterministic decision for
    each already-executed ORFS pair.  The decision receipt is the missing link
    between C3 (load) and C4 (behavior); ORFS reports remain the independent C5
    oracle.  A production runtime cannot consume this evaluation-only mode.
    """
    snapshot = load_policy_snapshot(conn, policy_snapshot_id)
    routing = json.loads(snapshot.get("routing_config_json") or "{}")
    if not isinstance(routing, Mapping):
        raise ValueError("evaluation policy routing config is malformed")
    selected = routing.get("selected_action")
    if selected is None:
        selected = "none"
    else:
        selected = _strict_text(selected, label="selected_action")
    decisions = []
    for record in records:
        action = record.action if selected != "none" else None
        decisions.append({
            "lineage_id": record.lineage_id,
            "selected_action": selected,
            "action_digest": action_digest(action) if action else None,
            "source_record_id": record.record_id,
            "causal_path_id": causal_path_id if action else None,
        })
    receipt = {
        "version": "tehm-evaluation-policy-runtime-v1",
        "runtime_id": runtime_id,
        "policy_snapshot_id": policy_snapshot_id,
        "policy_digest": snapshot["policy_digest"],
        "loaded": True,
        "evaluation_only": True,
        "decisions": decisions,
    }
    receipt["receipt_id"] = _stable_digest(receipt)
    return receipt


def _runtime_selects_action(runtime: dict, lineages: set[str],
                            expected_action: str) -> bool:
    """Return whether an executed policy selected the action for every target.

    C8 must be derived from the runtime receipt, not from a caller-provided
    boolean.  The receipt is intentionally small, but it is still required to
    contain one decision for each target lineage and the exact action under
    test.  Missing, duplicated, or malformed decisions fail closed.
    """
    if (not lineages or not isinstance(runtime, dict) or
            any(type(lineage) is not str or not lineage.strip()
                for lineage in lineages) or
            type(expected_action) is not str or not expected_action.strip()):
        return False
    decisions = runtime.get("decisions")
    if not isinstance(decisions, list):
        return False
    selected = {}
    for decision in decisions:
        if not isinstance(decision, dict):
            return False
        lineage = decision.get("lineage_id")
        if type(lineage) is not str or not lineage.strip():
            return False
        lineage = lineage.strip()
        if lineage in selected:
            return False
        selected_action = decision.get("selected_action")
        if selected_action is not None and type(selected_action) is not str:
            return False
        selected[lineage] = (
            selected_action.strip() if isinstance(selected_action, str)
            else None)
    return (set(selected) >= set(lineages) and
            all(selected[lineage] == expected_action for lineage in lineages))


def build_orfs_capability_attribution(
    source_db: Path | str,
    *,
    output_dir: Path | str,
    causal_report: Path | str,
    training_pairs: list[dict],
    heldout_pair: dict,
    non_target_pair: dict,
    mechanism_family: str = "DENSITY_RELIEF",
    runtime_id: str = "tehm-orfs-capability-evaluation",
) -> dict:
    """Evaluate C1-C8 while retaining an explicit non-production boundary."""
    mechanism_family = _strict_text(mechanism_family, label="mechanism_family")
    runtime_id = _strict_text(runtime_id, label="runtime_id")
    if not isinstance(training_pairs, (list, tuple)):
        raise ValueError("training_pairs_malformed")
    if (not isinstance(heldout_pair, Mapping) or
            not isinstance(non_target_pair, Mapping)):
        raise ValueError("evaluation_pair_malformed")
    source = Path(source_db).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"source database not found: {source}")
    source_digest = _sha256(source)
    report_path = Path(causal_report).resolve()
    causal = json.loads(report_path.read_text())
    if not isinstance(causal, Mapping):
        raise ValueError("causal report is malformed")
    causal_path_id = (causal.get("path") or {}).get("path_id")
    if type(causal_path_id) is not str:
        causal_path_id = ""
    else:
        causal_path_id = causal_path_id.strip()
    if not causal_path_id or (causal.get("replication") or {}).get("eligible") is not True:
        raise ValueError("causal report must contain an eligible replicated path")
    if len(training_pairs) < 2:
        raise ValueError("attribution requires at least two training lineages")
    if any(not isinstance(item, Mapping) for item in training_pairs):
        raise ValueError("training pair is malformed")
    train_lineages = {
        _strict_text(item.get("lineage_id"), label="lineage_id")
        for item in training_pairs
    }
    held_lineage = _strict_text(
        heldout_pair.get("lineage_id"), label="lineage_id")
    if len(train_lineages) < 2:
        raise ValueError("attribution requires independent training lineages")
    if held_lineage in train_lineages:
        raise ValueError("held-out lineage leaked into training")

    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    derived_db = output / "tehm.sqlite"
    _backup_database(source, derived_db)
    conn = db.connect(derived_db)
    db.ensure_schema(conn)
    store = ArtifactStore(output / "artifacts")

    # Only training observations enter the candidate learner snapshot.  The
    # held-out and non-target checks below are external evaluation receipts.
    train_records = []
    train_transition_ids = []
    train_summaries = []
    for spec in training_pairs:
        record = _build_record(spec)
        summary = _summary(
            record, target_check=_target_check(spec.get("target_check")))
        if not (summary["before_failed"] and summary["candidate_pass"]):
            raise ValueError(f"training pair is not baseline-fail -> candidate-pass: {summary}")
        captured = capture(
            conn, store, record,
            dataset_campaign_id="orfs-capability-attribution-r2",
            dataset_split="training", dataset_learner_eligible=True)
        train_transition_ids.append(captured.transition_id)
        train_records.append(record)
        train_summaries.append(summary)
    record_capability_training_digest = _stable_digest(train_summaries)

    # Persist a candidate capability and its causal-path reference in the
    # derived DB.  This is not a promotion and does not add a production rule.
    capability = register_capability(
        conn, mechanism_family=mechanism_family,
        applicability={"causal_path_id": causal_path_id,
                       "target_check": "route",
                       "evaluation_only": True},
        required_rules=[], required_assets=[], status="candidate",
        provenance={"causal_report": str(report_path),
                    "evaluation_only": True})
    record_capability_evidence(
        conn, capability_id=capability.capability_id,
        evidence_type="causal_path", evidence_id=causal_path_id,
        split="training", verdict="PASS")
    conn.commit()
    conn.close()

    baseline_memory_digest = "sha256:" + source_digest
    candidate_memory_digest = "sha256:" + _sha256(derived_db)
    conn = db.connect(derived_db)
    baseline_policy = create_policy_snapshot(
        conn, memory_snapshot_id=baseline_memory_digest,
        promoted_rules=[], promoted_assets=[],
        retrieval_config={"causal_shadow": False, "evaluation_only": True},
        routing_config={"selected_action": "none"})
    candidate_policy = create_policy_snapshot(
        conn, memory_snapshot_id=candidate_memory_digest,
        promoted_rules=[], promoted_assets=[],
        retrieval_config={"causal_shadow": True, "evaluation_only": True},
        routing_config={"causal_path_id": causal_path_id,
                        "selected_action": "DENSITY_RELIEF",
                        "production_authority": False})
    # C3 is an evaluation-runtime load receipt.  It deliberately says nothing
    # about production eligibility.
    load = record_policy_load(
        conn, policy_snapshot_id=candidate_policy.policy_snapshot_id,
        runtime_id=runtime_id, loaded=True,
        receipt={"mode": "evaluation_only", "production_authority": False,
                 "causal_path_id": causal_path_id})

    held_record = _build_record(heldout_pair)
    held_summary = _summary(
        held_record, target_check=_target_check(
            heldout_pair.get("target_check")))
    if not (held_summary["before_failed"] and held_summary["candidate_pass"]):
        raise ValueError(f"held-out pair is not baseline-fail -> candidate-pass: {held_summary}")
    held_evidence_id = _stable_digest({"pair": held_summary,
                                       "lineage": held_lineage,
                                       "causal_path_id": causal_path_id})

    non_target_record = _build_record(non_target_pair)
    non_target_summary = _summary(
        non_target_record, target_check=_target_check(
            non_target_pair.get("target_check")))
    no_regression = bool(non_target_summary["candidate_pass"] and
                         not non_target_summary["created_regressions"])
    non_target_evidence_id = _stable_digest({"pair": non_target_summary,
                                             "role": "non_target_regression_replay"})

    baseline_runtime = _run_evaluation_policy(
        conn, policy_snapshot_id=baseline_policy.policy_snapshot_id,
        records=train_records + [held_record],
        causal_path_id=causal_path_id, runtime_id=runtime_id)
    # The baseline arm is also the explicit ``M_t+1 - ΔMemory`` ablation.
    # Bind its execution receipt in the database so C8 cannot be satisfied by
    # merely asserting ``gain_without_memory=false`` in a JSON payload.
    ablation_load = record_policy_load(
        conn, policy_snapshot_id=baseline_policy.policy_snapshot_id,
        runtime_id=runtime_id, loaded=True,
        receipt={"mode": "evaluation_only_ablation",
                 "memory_removed": True, "production_authority": False,
                 "execution_receipt_id": baseline_runtime["receipt_id"]})
    candidate_runtime = _run_evaluation_policy(
        conn, policy_snapshot_id=candidate_policy.policy_snapshot_id,
        records=train_records + [held_record],
        causal_path_id=causal_path_id, runtime_id=runtime_id)
    baseline_behavior = {
        "runtime_receipt_id": baseline_runtime["receipt_id"],
        "policy": baseline_policy.policy_snapshot_id,
        "selected_actions": [row["selected_action"] for row in baseline_runtime["decisions"]],
        "training": [{"lineage_id": row["lineage_id"], "outcome": "FAIL"}
                      for row in train_summaries],
        "heldout": {"lineage_id": held_lineage, "outcome": "FAIL"},
    }
    candidate_behavior = {
        "runtime_receipt_id": candidate_runtime["receipt_id"],
        "policy": candidate_policy.policy_snapshot_id,
        "selected_actions": [row["selected_action"] for row in candidate_runtime["decisions"]],
        "training": [{"lineage_id": row["lineage_id"], "outcome": "PASS"}
                      for row in train_summaries],
        "heldout": {"lineage_id": held_lineage, "outcome": "PASS"},
    }
    baseline_behavior_digest = _stable_digest(baseline_behavior)
    candidate_behavior_digest = _stable_digest(candidate_behavior)
    # Bind the baseline ablation arm to the behavior digest it actually
    # produced.  This latest row supersedes the pre-execution load witness for
    # strict C8 replay.
    ablation_load = record_policy_load(
        conn, policy_snapshot_id=baseline_policy.policy_snapshot_id,
        runtime_id=runtime_id, loaded=True,
        receipt={"mode": "evaluation_only_ablation",
                 "memory_removed": True, "production_authority": False,
                 "execution_receipt_id": baseline_runtime["receipt_id"],
                 "behavior_digest": baseline_behavior_digest})
    # Bind the latest candidate-policy load to both the runtime execution and
    # the behavior digest derived from that execution.  A successful snapshot
    # lookup alone is not C3/C4 evidence; authority replay selects this latest
    # immutable load row by (policy_snapshot_id, runtime_id).
    load = record_policy_load(
        conn, policy_snapshot_id=candidate_policy.policy_snapshot_id,
        runtime_id=runtime_id, loaded=True,
        receipt={"mode": "evaluation_only", "production_authority": False,
                 "causal_path_id": causal_path_id,
                 "execution_receipt_id": candidate_runtime["receipt_id"],
                 "behavior_digest": candidate_behavior_digest})
    target_gain = bool(train_summaries and all(
        row["before_failed"] and row["candidate_pass"] for row in train_summaries))
    heldout = {"verdict": "PASS" if held_summary["candidate_pass"] else "FAIL",
               "disjoint_lineage": held_lineage not in train_lineages,
               "evidence_id": held_evidence_id,
               "lineage_id": held_lineage,
               "baseline_failed": held_summary["before_failed"]}
    ablation_gain_without_memory = _runtime_selects_action(
        baseline_runtime, train_lineages, "DENSITY_RELIEF")
    candidate_selected_action = _runtime_selects_action(
        candidate_runtime, train_lineages, "DENSITY_RELIEF")
    ablation = {
        "policy_snapshot_id": baseline_policy.policy_snapshot_id,
        "behavior_digest": baseline_behavior_digest,
        "runtime_receipt_id": baseline_runtime["receipt_id"],
        "policy_load_receipt_id": ablation_load.receipt_id,
        "target_lineages": sorted(train_lineages),
        "selected_actions": {
            lineage: next(row["selected_action"]
                           for row in baseline_runtime["decisions"]
                           if row["lineage_id"] == lineage)
            for lineage in sorted(train_lineages)
        },
        "gain_without_memory": ablation_gain_without_memory,
        "gain_with_memory": bool(target_gain and candidate_selected_action),
        "evidence_id": _stable_digest({"baseline": baseline_behavior_digest,
                                        "candidate": candidate_behavior_digest,
                                        "ablation_runtime_receipt_id": baseline_runtime["receipt_id"],
                                        "target_lineages": sorted(train_lineages),
                                        "gain_without_memory": ablation_gain_without_memory,
                                        "memory_removed": True}),
    }
    controls = {
        "source_db_sha256": _sha256(source),
        "causal_report_sha256": _sha256(report_path),
        "runtime_id": runtime_id,
        "oracle": "ORFS strict signoff/route receipts",
        "toolchain": "frozen project run-meta + stage logs",
        "seed_policy": "fixed project materialization",
        "candidate_budget": len(training_pairs),
        "heldout_lineage": held_lineage,
        "non_target_lineage": _strict_text(
            non_target_pair.get("lineage_id"), label="lineage_id"),
    }
    attribution = evaluate_capability_campaign(
        conn, capability_id=capability.capability_id,
        baseline_memory_digest=baseline_memory_digest,
        candidate_memory_digest=candidate_memory_digest,
        baseline_policy_snapshot_id=baseline_policy.policy_snapshot_id,
        candidate_policy_snapshot_id=candidate_policy.policy_snapshot_id,
        runtime_id=runtime_id,
        baseline_behavior_digest=baseline_behavior_digest,
        candidate_behavior_digest=candidate_behavior_digest,
        target_gain=target_gain, no_regression=no_regression,
        heldout=heldout, ablation=ablation,
        baseline_controls=controls, candidate_controls=dict(controls),
        memory_delta={
            "version": "memory-delta-v1",
            "baseline_memory_digest": baseline_memory_digest,
            "candidate_memory_digest": candidate_memory_digest,
            "added_transition_ids": train_transition_ids,
            "added_capability_ids": [capability.capability_id],
        }, strict_memory_delta=True)

    # Materialize the same database-bound authority receipt used by the RTL
    # attribution lane.  This is deliberately separate from the six rule
    # promotion gates below: a complete C1-C8 attribution is an auditable
    # capability claim, not permission to mutate canonical memory or load a
    # production rule.  Every gate points at an immutable evidence row with an
    # explicit split, and the receipt binds the candidate policy/load rows.
    capability_gates = evaluate_capability_promotion_gates(
        attribution.attribution.gates, required_assets=(), strict=True)
    authority_evidence_refs = {
        "C1": {"evidence_id": _stable_digest({
            "baseline_memory": baseline_memory_digest,
            "candidate_memory": candidate_memory_digest,
            "memory_delta": attribution.attribution.detail["memory_delta"]}),
                "split": "ab", "verdict": "PASS"},
        "C2": {"evidence_id": _stable_digest({
            "baseline_policy": baseline_policy.policy_snapshot_id,
            "candidate_policy": candidate_policy.policy_snapshot_id}),
                "split": "ab", "verdict": "PASS"},
        "C3": {"evidence_id": load.receipt_id,
                "split": "ab", "verdict": "PASS"},
        "C4": {"evidence_id": candidate_behavior_digest,
                "split": "ab", "verdict": "PASS",
                "execution_receipt_id": candidate_runtime["receipt_id"]},
        "C5": {"evidence_id": _stable_digest({
            "training_lineages": sorted(train_lineages),
            "target_gain": target_gain}),
                "split": "training", "verdict": "PASS"},
        "C6": {"evidence_id": held_evidence_id,
                "split": "heldout", "verdict": heldout["verdict"]},
        "C7": {"evidence_id": non_target_evidence_id,
                "split": "ab", "verdict": "PASS" if no_regression else "FAIL"},
        "C8": {"evidence_id": ablation["evidence_id"],
                "split": "ab", "verdict": "PASS"},
    }
    capability_authority = record_capability_authority(
        conn, capability_id=capability.capability_id,
        attribution_receipt=attribution.attribution,
        evidence_refs=authority_evidence_refs,
        candidate_policy_snapshot_id=candidate_policy.policy_snapshot_id,
        runtime_id=runtime_id, gates=attribution.attribution.gates)
    capability_authority_check = verify_capability_authority(
        conn, capability.capability_id, capability_authority)

    # Keep the six rule-authority gates visible and fail-closed.  Even a
    # complete evaluation attribution does not silently create this receipt.
    rule_authority = evaluate_promotion_gates({}, strict=True)
    conn.close()
    if _sha256(source) != source_digest:
        raise AssertionError("source DB changed during ORFS attribution")

    report = {
        "version": "orfs-capability-attribution-v2",
        "source_db": str(source),
        "source_db_sha256": source_digest,
        "derived_db": str(derived_db),
        "derived_db_sha256": _sha256(derived_db),
        "candidate_memory_digest": candidate_memory_digest,
        "memory_delta": {
            "version": "memory-delta-v1",
            "baseline_memory_digest": baseline_memory_digest,
            "candidate_memory_digest": candidate_memory_digest,
            "added_transition_ids": train_transition_ids,
            "added_capability_ids": [capability.capability_id],
        },
        "causal_report": str(report_path),
        "causal_path_id": causal_path_id,
        "canonical_memory_mutation": "none",
        "training": train_summaries,
        "training_evidence_digest": record_capability_training_digest,
        "heldout": held_summary,
        "non_target": non_target_summary,
        "non_target_evidence_id": non_target_evidence_id,
        "firewall": {"training_lineages": sorted(train_lineages),
                      "heldout_lineages": [held_lineage],
                      "disjoint": held_lineage not in train_lineages,
                      "heldout_entered_candidate_memory": False},
        "capability": capability.to_dict(),
        "baseline_policy": baseline_policy.to_dict(),
        "candidate_policy": candidate_policy.to_dict(),
        "policy_load": {
            **load.to_dict(),
            "policy_digest": candidate_policy.policy_digest,
            "execution_receipt_id": candidate_runtime["receipt_id"],
        },
        "runtime_behavior": {
            "baseline": baseline_runtime,
            "candidate": candidate_runtime,
        },
        "ablation_runtime": {
            "policy_load": ablation_load.to_dict(),
            "runtime": baseline_runtime,
            "behavior_digest": baseline_behavior_digest,
        },
        "attribution": attribution.to_dict(),
        "capability_authority_gates": capability_gates,
        "capability_authority_receipt": capability_authority.to_dict(),
        "capability_authority_verified": capability_authority_check,
        "capability_promotion_eligible": bool(
            capability_gates["eligible"] and
            capability_authority_check["eligible"]),
        "rule_authority_gates": rule_authority,
        "attribution_promotable": attribution.promotable,
        "promotion_attempted": False,
        "production_promotion_eligible": False,
        "authority_note": (
            "C1-C8 attribution and its database-bound authority receipt are "
            "evaluation-only. The candidate policy has no promoted rule/asset "
            "and is not loaded into production; the independent six-gate "
            "rule authority receipt remains absent."),
    }
    (output / "capability_attribution_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--causal-report", type=Path, required=True)
    parser.add_argument("--mechanism-family", default="DENSITY_RELIEF")
    parser.add_argument("--runtime-id", default="tehm-orfs-capability-evaluation")
    parser.add_argument("--train-pair", action="append", nargs=5, metavar=(
        "BEFORE", "AFTER", "LINEAGE", "CONFIG_JSON", "TARGET_CHECK"),
        required=True)
    parser.add_argument("--heldout-pair", nargs=5, metavar=(
        "BEFORE", "AFTER", "LINEAGE", "CONFIG_JSON", "TARGET_CHECK"),
        required=True)
    parser.add_argument("--non-target-pair", nargs=5, metavar=(
        "BEFORE", "AFTER", "LINEAGE", "CONFIG_JSON", "TARGET_CHECK"),
        required=True)
    args = parser.parse_args(argv)
    train = [_pair(*values) for values in args.train_pair]
    held = _pair(*args.heldout_pair)
    non_target = _pair(*args.non_target_pair)
    report = build_orfs_capability_attribution(
        args.source_db, output_dir=args.output,
        causal_report=args.causal_report, training_pairs=train,
        heldout_pair=held, non_target_pair=non_target,
        mechanism_family=args.mechanism_family, runtime_id=args.runtime_id)
    attribution = report["attribution"]["attribution"]
    print(json.dumps({
        "capability_id": report["capability"]["capability_id"],
        "gates": attribution["gates"],
        "missing_gates": attribution["missing_gates"],
        "attribution_promotable": report["attribution_promotable"],
        "capability_promotion_eligible": report[
            "capability_promotion_eligible"],
        "production_promotion_eligible": report["production_promotion_eligible"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
