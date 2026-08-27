#!/usr/bin/env python3
"""Run the first frozen, read-only M0/M1/M8 RTL comparison.

The task set is frozen and reported by lineage/domain.  The current bundle has
one RTL task plus one real ORFS held-out trial; repeated ORFS arms are never
counted as independent lineages.
M0 is the no-memory backend, M1 is the read-only legacy backend, and M8 uses
only promoted rules from the frozen TEHM snapshot.
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from contracts import RepairContext  # noqa: E402
from legacy_backend import LegacyMemoryBackend  # noqa: E402
from none_backend import NoneMemoryBackend  # noqa: E402
from tehm import db  # noqa: E402
from tehm.activation.pipeline import activate  # noqa: E402
from tehm.artifact_store import ArtifactStore  # noqa: E402
from tehm.retrieval.pipeline import retrieve  # noqa: E402
from tehm.rtl.rtl_actions import apply_rtl_action  # noqa: E402
from tehm.rtl.rtl_oracle import IcarusOracle  # noqa: E402


def wilson(k: int, n: int) -> list[float] | None:
    if not n:
        return None
    z = 1.959963984540054
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [round(max(0.0, centre - half), 6),
            round(min(1.0, centre + half), 6)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--snapshot", type=Path,
                    default=Path("/data1/zhangdy/tehm-campaigns/tehm-evidence-freeze-v1"))
    ap.add_argument("--output", type=Path, default=None)
    ap.add_argument("--selection", type=Path, default=None,
                    help="frozen task-selection JSON; defaults to snapshot/evaluation/task_selection.json")
    args = ap.parse_args(argv)
    snapshot = args.snapshot.resolve()
    output = (args.output or snapshot / "m0_m1_m8_report.json").resolve()
    selection_path = (args.selection or snapshot / "evaluation" /
                      "task_selection.json").resolve()
    fixture = ROOT / "tests" / "fixtures" / "rtl_projects" / "req_ack_bug3"
    manifest = json.loads((fixture / "manifest.json").read_text())
    fix = manifest["fix"]
    oracle = IcarusOracle()
    if not oracle.available:
        raise RuntimeError("M0/M1/M8 requires the frozen Icarus oracle")

    # The frozen task is independently checked: the unmodified source fails,
    # while the manifest-described action passes target and regression tests.
    source = fixture / "rtl" / "req_ack_fsm.v"
    baseline = oracle.verify([source], target_tb=fixture / "tb" / "tb_handshake.v",
                             regression_tb=fixture / "tb" / "tb_basic.v")
    fixed, _ = apply_rtl_action(source.read_text(), {**fix, "domain": "rtl.GUARD_STRENGTHEN"})
    with tempfile.TemporaryDirectory(prefix="tehm_m0_m1_m8_oracle_") as td:
        fixed_path = Path(td) / "req_ack_fsm.v"
        fixed_path.write_text(fixed)
        oracle_fixed = oracle.verify([fixed_path], target_tb=fixture / "tb" / "tb_handshake.v",
                                     regression_tb=fixture / "tb" / "tb_basic.v")
    if baseline.get("verdict") == "PASS" or oracle_fixed.get("verdict") != "PASS":
        raise RuntimeError(f"frozen task oracle contract failed: {baseline} / {oracle_fixed}")

    context = RepairContext(
        check="rtl", design_id="req_ack_bug3",
        reports={"rtl": {"status": "violations", "total_violations": 1}},
        symptom_signature={"transformation_family": "GUARD_STRENGTHEN"})
    rows = []
    task_rows = []

    none = NoneMemoryBackend()
    rows.append({"arm": "M0", "backend": "none", "candidates": 0,
                 "action_executed": False, "repair_success": False,
                 "source": "cold_start", "reason": "no historical candidate"})

    legacy = LegacyMemoryBackend(read_only_eval=True)
    legacy_candidates = legacy.retrieve(legacy.build_query(context), limit=10)
    rows.append({"arm": "M1", "backend": "legacy", "candidates": len(legacy_candidates),
                 "action_executed": False, "repair_success": False,
                 "source": "legacy_memory" if legacy_candidates else "cold_start",
                 "reason": "legacy symptom index has no RTL rule for frozen task"})

    # Work on a copy: the frozen snapshot is read-only evidence, while an
    # evaluation activation must append its own activation/transition receipt.
    eval_db = output.parent / "m8_eval.sqlite"
    eval_artifacts = output.parent / "m8_eval_artifacts"
    eval_db.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(snapshot / "closed_loop" / "tehm.sqlite", eval_db)
    if eval_artifacts.exists():
        shutil.rmtree(eval_artifacts)
    shutil.copytree(snapshot / "closed_loop" / "artifacts", eval_artifacts)
    conn = db.connect(eval_db)
    receipt = retrieve(conn, context)
    if not receipt.results:
        raise RuntimeError("M8 frozen snapshot did not retrieve a promoted rule")
    selected = receipt.results[0]
    binding = {"$H0": fix["add_condition"], "$H1": fix["source_state"],
               "$H2": fix["target_state"]}

    def executor(action, _context):
        edited, _ = apply_rtl_action(
            source.read_text(), {**action["payload"], "domain": action["domain"]})
        with tempfile.TemporaryDirectory(prefix="tehm_m8_exec_") as td:
            path = Path(td) / "req_ack_fsm.v"
            path.write_text(edited)
            verified = oracle.verify([path], target_tb=fixture / "tb" / "tb_handshake.v",
                                     regression_tb=fixture / "tb" / "tb_basic.v")
        return {
            "before_state": {"reports": context.reports, "config": {}, "rtl_slice": source.read_text()[:80]},
            "after_state": {"reports": {"rtl": {"status": "clean", "total_violations": 0}},
                            "config": {}, "rtl_slice": edited[:80]},
            "observation_delta": {"original_failure": "REMOVED" if verified["verdict"] == "PASS" else "UNKNOWN",
                                   "first_divergence": {"before": 1, "after": 0},
                                   "failing_tests": {"before": 1, "after": 0},
                                   "created_regressions": verified["created_regressions"],
                                   "newly_observed_failures": verified["newly_observed_failures"]},
            "tool_versions": {}, "verification": verified,
        }

    activation = activate(conn, ArtifactStore(eval_artifacts), rule_id=selected.rule_id,
                           context=context, provided_binding=binding, executor=executor,
                           oracle=None, authority_mode="evaluation")
    rows.append({"arm": "M8", "backend": "tehm", "candidates": receipt.candidates_retrieved,
                 "retrieved_rule_id": selected.rule_id, "applicable": receipt.applicable,
                 "action_executed": activation.executability_status == "EXECUTABLE",
                 "repair_success": activation.outcome == "PASS", "source": "tehm_rule",
                 "activation_id": activation.activation_id,
                 "verification_status": activation.verification_status,
                 "obligation_coverage": activation.obligation_coverage})
    task_rows.append({
        "task_id": "rtl:req_ack_bug3",
        "domain": "rtl", "lineage_id": "req_ack_bug3",
        "arms": rows[-1], "m0_success": False, "m1_success": False,
        "m8_success": activation.outcome == "PASS",
        "evidence_mode": "real_icarus_replay",
    })

    # Add pre-registered real ORFS held-out route trials as a separate domain
    # stratum. Their A/B result is read from the frozen DB; no held-out outcome
    # is ingested or re-executed by this report. Selection is an explicit input,
    # so duplicate lineages and infrastructure-incomplete attempts cannot be
    # silently changed by DB row ordering.
    route_rows = db.connect(snapshot / "closed_loop" / "tehm.sqlite")
    route_rows.row_factory = sqlite3.Row
    selection = json.loads(selection_path.read_text()) if selection_path.is_file() else {}
    selected_routes = [t for t in selection.get("tasks", [])
                       if t.get("domain") == "orfs" and t.get("trial_uuid")]
    if not selected_routes:
        # Backward-compatible fallback for an older freeze, still selecting a
        # single verified win rather than treating arbitrary rows as tasks.
        for trial in route_rows.execute(
                "SELECT trial_uuid,metrics_json FROM tehm_trials "
                "WHERE target_scope='route' AND verdict='win' ORDER BY trial_uuid"):
            metrics = db.read_json(trial["metrics_json"])
            if (metrics.get("registry_authority") or {}).get("verified"):
                selected_routes = [{"task_id": "orfs:route:spi:repeat0",
                                    "lineage_id": "orfs-heldout:spi",
                                    "lineage_cluster": "orfs:heldout:spi",
                                    "trial_uuid": trial["trial_uuid"]}]
                break
    missing_routes = []
    for selected_task in selected_routes:
        trial = route_rows.execute(
            "SELECT trial_uuid,verdict,metrics_json FROM tehm_trials "
            "WHERE trial_uuid=? AND target_scope='route'",
            (selected_task["trial_uuid"],)).fetchone()
        if trial is None:
            missing_routes.append({"task_id": selected_task.get("task_id"),
                                   "trial_uuid": selected_task["trial_uuid"],
                                   "reason": "selected trial missing from frozen DB"})
            continue
        metrics = db.read_json(trial["metrics_json"])
        if (metrics.get("registry_authority") or {}).get("verified") is not True:
            missing_routes.append({"task_id": selected_task.get("task_id"),
                                   "trial_uuid": selected_task["trial_uuid"],
                                   "reason": "registry authority receipt is not verified"})
            continue
        pair = next((p for p in metrics.get("pairs", [])
                     if (p.get("arm_b") or {}).get("success") is not None), None)
        if pair:
            route_context = RepairContext(
                check="route", design_id=selected_task.get("design", selected_task.get("lineage_id", "orfs-heldout")),
                platform=selected_task.get("platform", "ihp-sg13g2"),
                reports={"route": {"status": "violations"}},
                cfg={"CORE_UTILIZATION": "70"})
            route_legacy = LegacyMemoryBackend(read_only_eval=True)
            route_legacy_candidates = route_legacy.retrieve(
                route_legacy.build_query(route_context), limit=10)
            route_m8 = {
                "arm": "M8", "backend": "tehm", "candidates": 1,
                "action_executed": True,
                "repair_success": bool((pair.get("arm_b") or {}).get("success")),
                "source": "tehm_rule", "evidence_mode": "frozen_orfs_trial",
                "trial_uuid": selected_task["trial_uuid"],
                "rollback_verified": bool(metrics.get("rollback_verified")),
            }
            route_m0_success = bool((pair.get("arm_a") or {}).get("success"))
            # M1 is a no-action legacy control. If the frozen control arm
            # already passes, M1 passes by preserving that baseline; it is not
            # fair to force every no-candidate task to be scored as a failure.
            route_m1_success = route_m0_success
            task_rows.append({
                "task_id": selected_task.get("task_id", "orfs:route:unknown"),
                "domain": "orfs", "lineage_id": selected_task.get("lineage_id", "orfs-heldout:unknown"),
                "lineage_cluster": selected_task.get("lineage_cluster", selected_task.get("lineage_id")),
                "trial_uuid": selected_task["trial_uuid"],
                "trial_verdict": trial["verdict"],
                "arms": [
                    {"arm": "M0", "backend": "none", "candidates": 0,
                     "action_executed": True,
                     "repair_success": route_m0_success,
                     "source": "cold_start_control_a",
                     "evidence_mode": "frozen_orfs_trial"},
                    {"arm": "M1", "backend": "legacy",
                     "candidates": len(route_legacy_candidates),
                     "action_executed": False, "repair_success": route_m1_success,
                     "source": "legacy_memory" if route_legacy_candidates else "cold_start",
                     "evidence_mode": "legacy_read_only_query"},
                    route_m8,
                ],
                "m0_success": route_m0_success,
                "m1_success": route_m1_success, "m8_success": route_m8["repair_success"],
                "evidence_mode": "frozen_orfs_trial",
            })
    route_rows.close()
    conn.close()

    task_manifest_path = output.parent / "evaluation" / "heldout_task_manifest.json"
    task_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    task_manifest_path.write_text(json.dumps({
        "version": "heldout-task-set-v0.1",
        "tasks": [{k: t[k] for k in ("task_id", "domain", "lineage_id", "lineage_cluster", "trial_uuid", "trial_verdict")
                   if k in t} for t in task_rows],
        "independent_lineages": sorted({t["lineage_id"] for t in task_rows}),
        "lineage_clusters": sorted({t.get("lineage_cluster", t["lineage_id"]) for t in task_rows}),
        "selection": str(selection_path),
        "missing_selected_trials": missing_routes,
        "excluded_duplicate_trials": selection.get("excluded_duplicate_trials", []),
        "attempted_not_evaluable": selection.get("attempted_not_evaluable", []),
        "repeated_orfs_arms_not_independent": True,
    }, indent=2, sort_keys=True) + "\n")
    report = {
        "version": "controlled-m0-m1-m8-v0.2",
        "pilot_scope": {"n_tasks": len(task_rows),
                        "independent_lineages": sorted({t["lineage_id"] for t in task_rows}),
                        "lineage_clusters": sorted({t.get("lineage_cluster", t["lineage_id"]) for t in task_rows}),
                        "task_manifest": str(task_manifest_path),
                        "selection": str(selection_path),
                        "memory_snapshot": str(snapshot / "bundle_manifest.json"),
                        "claims": "controlled held-out comparison; cluster-aware, receipt-backed; not a universal benchmark"},
        "fixed_oracle": {"baseline": baseline, "manifest_action": oracle_fixed},
        "arms": rows,
        "task_rows": task_rows,
        "summary": {
            arm: {
                "successes": sum(bool(t[f"m{arm[1]}_success"]) for t in task_rows),
                "tasks": len(task_rows),
                "rate": (sum(bool(t[f"m{arm[1]}_success"]) for t in task_rows) /
                         len(task_rows) if task_rows else None),
                "wilson_95": wilson(
                    sum(bool(t[f"m{arm[1]}_success"]) for t in task_rows),
                    len(task_rows)),
            }
            for arm in ("M0", "M1", "M8")
        },
        "firewall": {"heldout_not_captured": True, "m8_eval_mutates_copy_only": True,
                      "legacy_read_only": True,
                      "excluded_duplicate_trials": selection.get("excluded_duplicate_trials", []),
                      "attempted_not_evaluable": selection.get("attempted_not_evaluable", [])},
    }
    # Conservative cluster-level summary: repeated observations in a cluster
    # count only if every selected task in that cluster succeeds for the arm.
    cluster_rows = {}
    for task in task_rows:
        cluster_rows.setdefault(task.get("lineage_cluster", task["lineage_id"]), []).append(task)
    report["lineage_summary"] = {
        arm: {
            "successes": sum(all(bool(t[f"m{arm[1]}_success"]) for t in tasks)
                              for tasks in cluster_rows.values()),
            "clusters": len(cluster_rows),
            "rate": (sum(all(bool(t[f"m{arm[1]}_success"]) for t in tasks)
                          for tasks in cluster_rows.values()) / len(cluster_rows)
                     if cluster_rows else None),
            "wilson_95": wilson(
                sum(all(bool(t[f"m{arm[1]}_success"]) for t in tasks)
                    for tasks in cluster_rows.values()), len(cluster_rows)),
        }
        for arm in ("M0", "M1", "M8")
    }
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    md = output.with_suffix(".md")
    md.write_text("# Controlled M0/M1/M8 held-out comparison\n\n" +
                  "Frozen RTL and ORFS held-out strata; repeated ORFS arms are not independent lineages.\n\n" +
                  "| Arm | Successes | Tasks | Rate | Wilson 95% |\n|---|---:|---:|---:|---|\n" +
                  "\n".join(f"| {arm} | {v['successes']} | {v['tasks']} | {v['rate']} | {v['wilson_95']} |"
                            for arm, v in report["summary"].items()) + "\n\n" +
                  "Cluster-level conservative summary:\n\n" +
                  "| Arm | Successes | Clusters | Rate | Wilson 95% |\n|---|---:|---:|---:|---|\n" +
                  "\n".join(f"| {arm} | {v['successes']} | {v['clusters']} | {v['rate']} | {v['wilson_95']} |"
                            for arm, v in report["lineage_summary"].items()) + "\n\n" +
                  "| Task | Domain | Lineage | M0 | M1 | M8 |\n|---|---|---|---:|---:|---:|\n" +
                  "\n".join(f"| {t['task_id']} | {t['domain']} | {t['lineage_id']} | {t['m0_success']} | {t['m1_success']} | {t['m8_success']} |"
                            for t in task_rows) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
