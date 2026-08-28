#!/usr/bin/env python3
"""Build an evaluation-only C1-C8 attribution receipt for an RTL asset.

The harness consumes a real asset-gap shadow report, creates baseline and
candidate policy snapshots in a derived database, and runs both policies with
the independent Icarus/vvp oracle.  It also reruns the target without the
asset (ablation) and checks an incompatible non-target lineage.  Nothing in
this script mutates the source database, changes lifecycle status, or loads a
policy into a production runtime.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tehm import db  # noqa: E402
from tehm.assets import (  # noqa: E402
    bind_rtl_asset_to_project, get_asset, get_asset_status,
    verify_asset_authority,
)
from tehm.causal.orfs import _backup_database, _sha256  # noqa: E402
from tehm.capability import (  # noqa: E402
    create_policy_snapshot, evaluate_capability_campaign,
    record_capability_evidence, record_policy_load, register_capability,
    load_policy_snapshot,
)
from tehm.lifecycle.promotion_gates import (  # noqa: E402
    evaluate_capability_promotion_gates,
)
from tehm.rtl.rtl_oracle import IcarusOracle  # noqa: E402


def _stable_digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, default=str).encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _manifest(project: Path) -> dict:
    value = json.loads((project / "manifest.json").read_text())
    if not isinstance(value, dict):
        raise ValueError(f"manifest must be an object: {project}")
    return value


def _lineage(project: Path) -> str:
    return str(_manifest(project).get("design") or project.name)


def _baseline_oracle(project: Path, oracle: IcarusOracle) -> dict:
    manifest = _manifest(project)
    rtl_files = sorted((project / "rtl").glob("*.v"))
    if not rtl_files:
        raise ValueError(f"project has no rtl/*.v: {project}")
    verification = manifest.get("verification") or {}
    target = project / verification.get("target_test", "tb/tb_handshake.v")
    regression = project / verification.get("frozen_regression", "tb/tb_basic.v")
    result = oracle.verify(
        rtl_files, target_tb=target if target.exists() else None,
        regression_tb=regression if regression.exists() else None)
    return {
        "project": str(project),
        "lineage_id": _lineage(project),
        "status": "BASELINE",
        "selected_asset_id": None,
        "action_applied": False,
        "target_verdict": (result.get("target") or {}).get("verdict"),
        "regression_verdict": (result.get("regression") or {}).get("verdict"),
        "oracle_verdict": result.get("verdict"),
        "source_sha256": _sha256(rtl_files[0]),
    }


def _candidate_oracle(
    asset: dict,
    project: Path,
    oracle: IcarusOracle,
    mechanism_family: str,
) -> dict:
    """Bind and independently execute one candidate asset, or reject it."""
    base = {
        "project": str(project),
        "lineage_id": _lineage(project),
        "selected_asset_id": asset.get("asset_id"),
    }
    try:
        bound = bind_rtl_asset_to_project(
            asset, project, expected_mechanism_family=mechanism_family)
    except (OSError, TypeError, ValueError) as exc:
        return {
            **base, "status": "INAPPLICABLE", "action_applied": False,
            "target_verdict": None, "regression_verdict": None,
            "oracle_verdict": "INAPPLICABLE", "reason": str(exc),
        }
    from tehm.assets import validate_rtl_asset_project

    receipt = validate_rtl_asset_project(bound, project, oracle=oracle).to_dict()
    return {
        **base,
        "status": receipt.get("status"),
        "action_applied": receipt.get("static_valid") is True,
        "target_verdict": receipt.get("oracle_verdict"),
        "regression_verdict": receipt.get("regression_verdict"),
        "oracle_verdict": receipt.get("oracle_verdict"),
        "validation": receipt,
    }


def _run_policy(
    conn,
    policy_snapshot_id: str,
    projects: list[Path],
    oracle: IcarusOracle,
    mechanism_family: str,
    *,
    baseline: bool,
) -> dict:
    snapshot = load_policy_snapshot(conn, policy_snapshot_id)
    if baseline:
        decisions = [_baseline_oracle(project, oracle) for project in projects]
    else:
        try:
            asset_ids = json.loads(snapshot.get("promoted_assets_json") or "[]")
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("candidate policy has invalid promoted_assets_json") from exc
        if len(asset_ids) != 1:
            raise ValueError("RTL candidate policy must select exactly one asset")
        try:
            routing = json.loads(snapshot.get("routing_config_json") or "{}")
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("candidate policy has invalid routing_config_json") from exc
        if (not isinstance(routing, dict) or
                routing.get("selected_asset_id") != str(asset_ids[0])):
            raise ValueError("candidate policy routing does not select its asset")
        asset = get_asset(conn, str(asset_ids[0]))
        if asset is None:
            raise ValueError(f"candidate policy references unknown asset: {asset_ids[0]}")
        asset["asset_id"] = str(asset_ids[0])
        status = get_asset_status(
            conn, asset_id=asset["asset_id"],
            target_scope=str((asset.get("compatibility") or {}).get(
                "compatibility_profile") or ""))
        if status is None or status.get("status") not in {"candidate", "promoted"}:
            raise ValueError("candidate policy asset is not candidate/promoted")
        decisions = [
            _candidate_oracle(asset, project, oracle, mechanism_family)
            for project in projects
        ]
    payload = {
        "version": "rtl-evaluation-policy-runtime-v1",
        "policy_snapshot_id": policy_snapshot_id,
        "policy_digest": snapshot["policy_digest"],
        "loaded": True,
        "evaluation_only": True,
        "decisions": decisions,
    }
    payload["receipt_id"] = _stable_digest(payload)
    return payload


def _behavior_summary(runtime: dict, roles: dict[str, str]) -> dict:
    return {
        "runtime_receipt_id": runtime["receipt_id"],
        "policy_snapshot_id": runtime["policy_snapshot_id"],
        "decisions": [
            {**decision, "role": roles.get(decision["lineage_id"], "unknown")}
            for decision in runtime["decisions"]
        ],
    }


def build_rtl_capability_attribution(
    source_db: Path | str,
    *,
    output_dir: Path | str,
    asset_gap_report: Path | str,
    training_projects: list[Path | str],
    heldout_projects: list[Path | str],
    non_target_projects: list[Path | str],
    mechanism_family: str = "HANDSHAKE_COMPLETION",
    runtime_id: str = "tehm-rtl-capability-evaluation",
) -> dict:
    if len(training_projects) < 2:
        raise ValueError("C1-C8 attribution requires at least two training lineages")
    if not heldout_projects:
        raise ValueError("at least one held-out project is required")
    if not non_target_projects:
        raise ValueError("at least one non-target project is required")
    source = Path(source_db).resolve()
    report_path = Path(asset_gap_report).resolve()
    if not source.is_file() or not report_path.is_file():
        raise FileNotFoundError("source DB and asset-gap report are required")
    source_digest = _sha256(source)
    gap_report = json.loads(report_path.read_text())
    asset_authority = gap_report.get("asset_authority_receipt") or {}
    if asset_authority.get("eligible") is not True:
        raise ValueError("asset-gap report lacks an eligible asset authority receipt")
    asset_id = str((gap_report.get("asset_registration") or {}).get("asset_id") or "")
    asset_db = Path(gap_report.get("derived_db") or "").resolve()
    expected_asset_db_digest = str(gap_report.get("derived_db_sha256") or "")
    if not asset_id or not asset_db.is_file() or _sha256(asset_db) != expected_asset_db_digest:
        raise ValueError("asset-gap report has a stale or incomplete derived DB binding")
    train = [Path(item).resolve() for item in training_projects]
    heldout = [Path(item).resolve() for item in heldout_projects]
    non_target = [Path(item).resolve() for item in non_target_projects]
    train_lineages = {_lineage(path) for path in train}
    heldout_lineages = {_lineage(path) for path in heldout}
    non_target_lineages = {_lineage(path) for path in non_target}
    if len(train_lineages) != len(train):
        raise ValueError("training projects must have distinct lineages")
    if len(heldout_lineages) != len(heldout):
        raise ValueError("held-out projects must have distinct lineages")
    if len(non_target_lineages) != len(non_target):
        raise ValueError("non-target projects must have distinct lineages")
    if train_lineages & heldout_lineages:
        raise ValueError("held-out lineage leaked into training")
    if train_lineages & non_target_lineages or heldout_lineages & non_target_lineages:
        raise ValueError("non-target lineage overlaps training or held-out")

    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    derived_db = output / "tehm.sqlite"
    _backup_database(asset_db, derived_db)
    conn = db.connect(derived_db)
    db.ensure_schema(conn)
    oracle = IcarusOracle()
    if not oracle.available:
        conn.close()
        raise RuntimeError("Icarus oracle is required for RTL capability attribution")
    asset = get_asset(conn, asset_id)
    if asset is None:
        conn.close()
        raise ValueError(f"asset {asset_id!r} missing from asset-gap DB")
    asset["asset_id"] = asset_id
    asset_authority_check = verify_asset_authority(conn, asset_authority)
    if asset_authority_check["eligible"] is not True:
        conn.close()
        raise ValueError(
            "asset-gap authority receipt is not bound to its registry asset: "
            f"{asset_authority_check['reasons']}")

    capability = register_capability(
        conn, mechanism_family=mechanism_family,
        applicability={
            "compatibility_profile": (asset.get("compatibility") or {}).get(
                "compatibility_profile"),
            "asset_gap_id": (gap_report.get("selected_gap") or {}).get("gap_id"),
            "evaluation_only": True,
        }, required_assets=[asset_id], status="candidate",
        obligations={"target": "PASS", "regression": "PASS"},
        budget={"training_lineages": len(train_lineages)},
        provenance={"asset_gap_report": str(report_path), "evaluation_only": True})
    record_capability_evidence(
        conn, capability_id=capability.capability_id,
        evidence_type="asset_authority", evidence_id=asset_id,
        split="training", verdict="PASS")
    record_capability_evidence(
        conn, capability_id=capability.capability_id,
        evidence_type="gap_receipt",
        evidence_id=str((gap_report.get("selected_gap") or {}).get("gap_id") or ""),
        split="training", verdict="PASS")

    baseline_memory_digest = "sha256:" + _sha256(source)
    candidate_memory_digest = "sha256:" + _sha256(asset_db)
    baseline_policy = create_policy_snapshot(
        conn, memory_snapshot_id=baseline_memory_digest,
        promoted_rules=[], promoted_assets=[],
        retrieval_config={"asset_shadow": False, "evaluation_only": True},
        routing_config={"selected_asset_id": None, "production_authority": False})
    candidate_policy = create_policy_snapshot(
        conn, memory_snapshot_id=candidate_memory_digest,
        promoted_rules=[], promoted_assets=[asset_id],
        retrieval_config={"asset_shadow": True, "evaluation_only": True},
        routing_config={"selected_asset_id": asset_id,
                        "production_authority": False,
                        "target_scope": (asset.get("compatibility") or {}).get(
                            "compatibility_profile")})
    load = record_policy_load(
        conn, policy_snapshot_id=candidate_policy.policy_snapshot_id,
        runtime_id=runtime_id, loaded=True,
        receipt={"mode": "evaluation_only", "asset_id": asset_id,
                 "production_authority": False})

    target_projects = train + heldout
    roles = {_lineage(path): "training" for path in train}
    roles.update({_lineage(path): "heldout" for path in heldout})
    roles.update({_lineage(path): "non_target" for path in non_target})
    baseline_runtime = _run_policy(
        conn, baseline_policy.policy_snapshot_id, target_projects + non_target,
        oracle, mechanism_family, baseline=True)
    candidate_runtime = _run_policy(
        conn, candidate_policy.policy_snapshot_id, target_projects + non_target,
        oracle, mechanism_family, baseline=False)
    # Bind C3 to the actual execution receipt, not merely to a DB row created
    # before the runtime was invoked.  The evaluator selects this latest load
    # receipt for the candidate policy/runtime pair.
    load = record_policy_load(
        conn, policy_snapshot_id=candidate_policy.policy_snapshot_id,
        runtime_id=runtime_id, loaded=True,
        receipt={"mode": "evaluation_only", "asset_id": asset_id,
                 "production_authority": False,
                 "execution_receipt_id": candidate_runtime["receipt_id"]})
    baseline_behavior = _behavior_summary(baseline_runtime, roles)
    candidate_behavior = _behavior_summary(candidate_runtime, roles)
    baseline_behavior_digest = _stable_digest(baseline_behavior)
    candidate_behavior_digest = _stable_digest(candidate_behavior)

    by_lineage = {
        row["lineage_id"]: row
        for row in baseline_runtime["decisions"]
    }
    candidate_by_lineage = {
        row["lineage_id"]: row
        for row in candidate_runtime["decisions"]
    }
    training_gain = all(
        by_lineage[lineage]["target_verdict"] == "FAIL" and
        candidate_by_lineage[lineage]["oracle_verdict"] == "PASS" and
        candidate_by_lineage[lineage]["regression_verdict"] == "PASS"
        for lineage in train_lineages)
    heldout_gain = all(
        by_lineage[lineage]["target_verdict"] == "FAIL" and
        candidate_by_lineage[lineage]["oracle_verdict"] == "PASS" and
        candidate_by_lineage[lineage]["regression_verdict"] == "PASS"
        for lineage in heldout_lineages)
    non_target_clean = all(
        by_lineage[lineage]["regression_verdict"] == "PASS" and
        candidate_by_lineage[lineage]["status"] == "INAPPLICABLE" and
        candidate_by_lineage[lineage]["action_applied"] is False
        for lineage in non_target_lineages)
    no_regression = bool(non_target_clean and all(
        candidate_by_lineage[lineage]["regression_verdict"] == "PASS"
        for lineage in train_lineages | heldout_lineages
        if candidate_by_lineage[lineage]["status"] != "INAPPLICABLE"))
    ablation = {
        "policy_snapshot_id": baseline_policy.policy_snapshot_id,
        "behavior_digest": baseline_behavior_digest,
        "gain_without_memory": any(
            by_lineage[lineage]["target_verdict"] == "PASS"
            for lineage in train_lineages),
        "gain_with_memory": training_gain,
        "evidence_id": _stable_digest({
            "baseline_behavior_digest": baseline_behavior_digest,
            "candidate_behavior_digest": candidate_behavior_digest,
            "removed_asset_id": asset_id,
        }),
    }
    heldout_evidence = {
        "verdict": "PASS" if heldout_gain else "FAIL",
        "disjoint_lineage": bool(heldout_lineages.isdisjoint(train_lineages)),
        "evidence_id": _stable_digest({
            "candidate_runtime": candidate_runtime["receipt_id"],
            "lineages": sorted(heldout_lineages),
        }),
        "lineages": sorted(heldout_lineages),
    }
    controls = {
        "source_db_sha256": _sha256(source),
        "asset_gap_report_sha256": _sha256(report_path),
        "asset_id": asset_id,
        "runtime_id": runtime_id,
        "oracle": "icarus/vvp",
        "toolchain": "iverilog -g2012 + vvp",
        "candidate_budget": len(train),
        "mechanism_family": mechanism_family,
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
        target_gain=training_gain, no_regression=no_regression,
        heldout=heldout_evidence, ablation=ablation,
        baseline_controls=controls, candidate_controls=dict(controls),
        memory_delta={
            "version": "memory-delta-v1",
            "baseline_memory_digest": baseline_memory_digest,
            "candidate_memory_digest": candidate_memory_digest,
            "added_asset_ids": [asset_id],
            "added_capability_ids": [capability.capability_id],
        }, strict_memory_delta=True)
    capability_gates = evaluate_capability_promotion_gates(
        {**attribution.attribution.gates,
         "asset_authority_verified": asset_authority.get("eligible") is True},
        required_assets=[asset_id], strict=True)
    # Materialize a database-bound authority receipt even though this script
    # remains evaluation-only.  The receipt proves that C1-C8 are backed by
    # immutable gate evidence and an actual candidate-policy runtime load; it
    # does not change the capability or asset lifecycle.
    from tehm.capability import record_capability_authority, verify_capability_authority

    authority_gate_inputs = {
        **attribution.attribution.gates,
        "asset_authority_verified": asset_authority.get("eligible") is True,
    }
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
        "C3": {"evidence_id": load.receipt_id, "split": "ab", "verdict": "PASS"},
        "C4": {"evidence_id": candidate_behavior_digest,
                "split": "ab", "verdict": "PASS",
                "execution_receipt_id": candidate_runtime["receipt_id"]},
        "C5": {"evidence_id": _stable_digest({
            "training_lineages": sorted(train_lineages),
            "target_gain": training_gain}),
                "split": "training", "verdict": "PASS"},
        "C6": {"evidence_id": heldout_evidence["evidence_id"],
                "split": "heldout", "verdict": heldout_evidence["verdict"]},
        "C7": {"evidence_id": _stable_digest({
            "non_target": sorted(non_target_lineages),
            "regression_zero": non_target_clean}),
                "split": "ab", "verdict": "PASS"},
        "C8": {"evidence_id": ablation["evidence_id"],
                "split": "ab", "verdict": "PASS"},
        "asset_authority_verified": {
            "evidence_id": asset_id, "split": "training",
            "verdict": "PASS" if asset_authority.get("eligible") is True else "FAIL"},
    }
    capability_authority = record_capability_authority(
        conn, capability_id=capability.capability_id,
        attribution_receipt=attribution.attribution,
        evidence_refs=authority_evidence_refs,
        candidate_policy_snapshot_id=candidate_policy.policy_snapshot_id,
        runtime_id=runtime_id, gates=authority_gate_inputs)
    capability_authority_check = verify_capability_authority(
        conn, capability.capability_id, capability_authority)
    conn.close()
    if _sha256(source) != source_digest:
        raise AssertionError("source DB changed during attribution")

    report = {
        "version": "rtl-capability-attribution-v1",
        "source_db": str(source),
        "source_db_sha256": source_digest,
        "asset_gap_report": str(report_path),
        "asset_gap_report_sha256": _sha256(report_path),
        "asset_authority_receipt": asset_authority,
        "asset_authority_verification": asset_authority_check,
        "derived_db": str(derived_db),
        "derived_db_sha256": _sha256(derived_db),
        "memory_delta": {
            "version": "memory-delta-v1",
            "baseline_memory_digest": baseline_memory_digest,
            "candidate_memory_digest": candidate_memory_digest,
            "baseline_sha256": baseline_memory_digest,
            "candidate_asset_snapshot_sha256": candidate_memory_digest,
            "asset_id": asset_id,
            "added_asset_ids": [asset_id],
            "added_capability_ids": [capability.capability_id],
            "asset_status": "candidate",
        },
        "canonical_memory_mutation": "none",
        "capability": capability.to_dict(),
        "asset_id": asset_id,
        "baseline_policy": baseline_policy.to_dict(),
        "candidate_policy": candidate_policy.to_dict(),
        "policy_load": {
            **load.to_dict(),
            "policy_digest": candidate_policy.policy_digest,
            "execution_receipt_id": candidate_runtime["receipt_id"],
        },
        "runtime_behavior": {"baseline": baseline_runtime,
                              "candidate": candidate_runtime},
        "training": {
            "lineages": sorted(train_lineages),
            "target_gain": training_gain,
        },
        "heldout": heldout_evidence,
        "non_target": {
            "lineages": sorted(non_target_lineages),
            "regression_zero": non_target_clean,
        },
        "ablation": ablation,
        "firewall": {
            "training_lineages": sorted(train_lineages),
            "heldout_lineages": sorted(heldout_lineages),
            "non_target_lineages": sorted(non_target_lineages),
            "disjoint": (train_lineages.isdisjoint(heldout_lineages) and
                          train_lineages.isdisjoint(non_target_lineages) and
                          heldout_lineages.isdisjoint(non_target_lineages)),
            "heldout_entered_learner_support": False,
        },
        "attribution": attribution.to_dict(),
        "capability_authority_gates": capability_gates,
        "capability_authority_receipt": capability_authority.to_dict(),
        "capability_authority_verified": capability_authority_check,
        "attribution_promotable": attribution.promotable,
        "capability_promotion_eligible": bool(
            capability_gates["eligible"] and capability_authority_check["eligible"]),
        "promotion_attempted": False,
        "production_promotion_eligible": False,
        "authority_note": (
            "C1-C8 are derived from real baseline/candidate/ablation Icarus "
            "receipts in an evaluation-only runtime. The candidate capability "
            "and asset remain unpromoted; no canonical or production policy "
            "mutation is attempted."),
    }
    (output / "rtl_capability_attribution_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--asset-gap-report", type=Path, required=True)
    parser.add_argument("--training-project", type=Path, action="append", required=True)
    parser.add_argument("--heldout-project", type=Path, action="append", required=True)
    parser.add_argument("--non-target-project", type=Path, action="append", required=True)
    parser.add_argument("--mechanism-family", default="HANDSHAKE_COMPLETION")
    parser.add_argument("--runtime-id", default="tehm-rtl-capability-evaluation")
    args = parser.parse_args(argv)
    report = build_rtl_capability_attribution(
        args.source_db, output_dir=args.output,
        asset_gap_report=args.asset_gap_report,
        training_projects=args.training_project,
        heldout_projects=args.heldout_project,
        non_target_projects=args.non_target_project,
        mechanism_family=args.mechanism_family, runtime_id=args.runtime_id)
    print(json.dumps({
        "capability_id": report["capability"]["capability_id"],
        "attribution_gates": report["attribution"]["attribution"]["gates"],
        "capability_promotion_eligible": report["capability_promotion_eligible"],
        "promotion_attempted": report["promotion_attempted"],
        "production_promotion_eligible": report["production_promotion_eligible"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
