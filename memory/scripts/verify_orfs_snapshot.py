#!/usr/bin/env python3
"""Fail-closed verifier for the frozen real ORFS A/B receipt.

This checks the persisted production evidence and authority chain.  It does
not re-run a multi-minute PnR flow; a true flow re-run remains an explicit
campaign operation, while this command proves that the frozen snapshot is
internally complete and replayable from its preserved sandbox receipts.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--output", type=Path, default=None)
    ap.add_argument("--selection", type=Path, default=None)
    args = ap.parse_args(argv)
    conn = sqlite3.connect(args.db.resolve())
    conn.row_factory = sqlite3.Row
    selection_path = (args.selection or args.db.resolve().parent.parent /
                      "evaluation" / "task_selection.json")
    selected = []
    if selection_path.is_file():
        selected = [t["trial_uuid"] for t in json.loads(
            selection_path.read_text()).get("tasks", [])
                    if t.get("domain") == "orfs" and t.get("trial_uuid")]
    if not selected:
        selected = [r["trial_uuid"] for r in conn.execute(
            "SELECT trial_uuid FROM tehm_trials WHERE target_scope='route' "
            "AND verdict IN ('win','inconclusive') ORDER BY trial_uuid")]
    if not selected:
        raise SystemExit("no selected ORFS route trial in frozen DB")

    trial_reports = []
    for trial_uuid in selected:
        trial_row = conn.execute(
            "SELECT trial_uuid,rule_id,target_scope,verdict,metrics_json "
            "FROM tehm_trials WHERE trial_uuid=? AND target_scope='route'",
            (trial_uuid,)).fetchone()
        if trial_row is None:
            trial_reports.append({"trial_uuid": trial_uuid,
                                  "checks": {"trial_present": False},
                                  "pair_evidence": []})
            continue
        metrics = json.loads(trial_row["metrics_json"])
        authority = metrics.get("registry_authority") or {}
        checks = {
            "trial_present": True,
            "trial_verdict_allowed": trial_row["verdict"] in {"win", "inconclusive"},
            "registry_authority_verified": authority.get("verified") is True,
            "rollback_verified": metrics.get("rollback_verified") is True,
            "arms_differ": metrics.get("arms_differ") is True,
            "obligation_coverage": metrics.get("obligation_coverage") == 1.0,
        }
        pair_evidence = []
        for pair in metrics.get("pairs") or []:
            rollback = pair.get("rollback_receipt") or {}
            sandbox = Path(rollback.get("sandbox_root", ""))
            arm_rows = {}
            for arm in ("arm_a", "arm_b"):
                arm_dir = sandbox / arm
                metas = sorted(arm_dir.glob("backend/RUN_*/run-meta.json"))
                logs = sorted(arm_dir.glob("backend/RUN_*/stage_log.jsonl"))
                arm_rows[arm] = {
                    "success": bool((pair.get(arm) or {}).get("success")),
                    "run_meta_present": bool(metas),
                    "stage_log_present": bool(logs),
                    "run_meta": str(metas[-1]) if metas else None,
                    "stage_log": str(logs[-1]) if logs else None,
                }
            pair_evidence.append({"repeat": pair.get("repeat"), "arms": arm_rows,
                                  "rollback_verified": rollback.get("verified") is True})
        checks["arm_receipts_present"] = bool(pair_evidence) and all(
            e["arms"][a]["run_meta_present"] and e["arms"][a]["stage_log_present"]
            for e in pair_evidence for a in ("arm_a", "arm_b"))
        checks["pair_rollback_receipts"] = bool(pair_evidence) and all(
            e["rollback_verified"] for e in pair_evidence)
        activation = conn.execute(
            "SELECT activation_id,outcome,applicability_status,executability_status,"
            "verification_status FROM tehm_activations WHERE trial_uuid=? LIMIT 1",
            (trial_uuid,)).fetchone()
        checks["activation_receipt"] = activation is not None and all([
            activation["outcome"] == "PASS",
            activation["applicability_status"] == "APPLICABLE",
            activation["executability_status"] == "EXECUTABLE",
            activation["verification_status"] == "PASS",
        ])
        trial_reports.append({"trial_uuid": trial_uuid, "rule_id": trial_row["rule_id"],
                              "verdict": trial_row["verdict"], "checks": checks,
                              "pair_evidence": pair_evidence})
    conn.close()
    all_checks = sorted({key for item in trial_reports for key in item["checks"]})
    checks = {key: all(item["checks"].get(key) is True for item in trial_reports)
              for key in all_checks}
    report = {"version": "orfs-snapshot-verifier-v0.2",
              "trial_uuid": trial_reports[0]["trial_uuid"],
              "trial_uuids": [item["trial_uuid"] for item in trial_reports],
              "checks": checks,
              "trial_reports": trial_reports,
              "flow_reexecuted": False,
              "interpretation": "selected real ORFS receipts verified; no PnR re-execution",
              "selection": str(selection_path)}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
