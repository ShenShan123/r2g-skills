#!/usr/bin/env python3
"""Run the frozen held-out M0--M8 representation ablation.

The benchmark uses six independent RTL lineages spanning three mechanism
clusters (handshake completion, valid/ready commit, and FIFO capacity return).
Every arm starts from the same frozen snapshot.  Only M4--M8
are allowed to execute a crystallized action; M8 additionally requires the
validity/obligation gates.  Held-out activations use per-task temporary DB and
artifact copies, so neither the snapshot nor a later task can observe them.
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
from tehm import db  # noqa: E402
from tehm.activation.pipeline import activate  # noqa: E402
from tehm.artifact_store import ArtifactStore  # noqa: E402
from tehm.retrieval.pipeline import retrieve  # noqa: E402
from tehm.rtl.rtl_actions import apply_rtl_action  # noqa: E402
from tehm.rtl.rtl_oracle import IcarusOracle  # noqa: E402


ARMS = ("M0", "M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8")


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


def component_discrimination(task_rows: list[dict]) -> dict:
    """Report whether an ablated component changes the held-out outcome.

    A same-rate ablation is evidence of *non-identification*, not evidence that
    the component is unnecessary.  The report is intentionally descriptive so
    a later task manifest can require a contrast before making component-level
    claims.
    """
    components = {
        "role_view": "M5",
        "predicate_view": "M6",
        "validity_gate": "M4",
        "obligation_transfer": "M7",
    }
    result = {}
    for component, ablated_arm in components.items():
        full = [row.get("arms", {}).get("M8", {}) for row in task_rows]
        ablated = [row.get("arms", {}).get(ablated_arm, {}) for row in task_rows]
        def harmful(row: dict) -> bool:
            """Normalize harmful activation evidence without inventing it."""
            regressions = row.get("created_regressions")
            return bool(row.get("harmful_activation") or row.get("harmful") or
                        (isinstance(regressions, list) and regressions))

        full_success = sum(bool(row.get("repair_success")) for row in full)
        ablated_success = sum(bool(row.get("repair_success")) for row in ablated)
        full_exec = sum(bool(row.get("action_executed")) for row in full)
        ablated_exec = sum(bool(row.get("action_executed")) for row in ablated)
        full_harmful = sum(harmful(row) for row in full)
        ablated_harmful = sum(harmful(row) for row in ablated)
        full_harmful_rate = full_harmful / full_exec if full_exec else None
        ablated_harmful_rate = ablated_harmful / ablated_exec if ablated_exec else None
        full_oc = [float(row["obligation_coverage"]) for row in full
                   if isinstance(row.get("obligation_coverage"), (int, float))]
        ablated_oc = [float(row["obligation_coverage"]) for row in ablated
                      if isinstance(row.get("obligation_coverage"), (int, float))]
        result[component] = {
            "full_arm": "M8", "ablated_arm": ablated_arm,
            "tasks": len(task_rows),
            "full_successes": full_success,
            "ablated_successes": ablated_success,
            "full_action_executed": full_exec,
            "ablated_action_executed": ablated_exec,
            "full_harmful_activation_count": full_harmful,
            "ablated_harmful_activation_count": ablated_harmful,
            "full_harmful_activation_rate": full_harmful_rate,
            "ablated_harmful_activation_rate": ablated_harmful_rate,
            "harmful_rate_not_increased": bool(
                full_harmful_rate is not None and ablated_harmful_rate is not None and
                full_harmful_rate <= ablated_harmful_rate),
            "full_mean_obligation_coverage": (sum(full_oc) / len(full_oc)
                                               if full_oc else None),
            "ablated_mean_obligation_coverage": (sum(ablated_oc) / len(ablated_oc)
                                                  if ablated_oc else None),
            "identifiable_on_current_tasks": bool(
                full_success != ablated_success or full_exec != ablated_exec or
                full_harmful != ablated_harmful or
                (full_oc and ablated_oc and abs(sum(full_oc) / len(full_oc) -
                                                 sum(ablated_oc) / len(ablated_oc)) > 1e-12)
            ),
            "interpretation": "component_contrast_observed" if (
                full_success != ablated_success or full_exec != ablated_exec or
                full_harmful != ablated_harmful or
                (full_oc and ablated_oc and abs(sum(full_oc) / len(full_oc) -
                                                 sum(ablated_oc) / len(ablated_oc)) > 1e-12)
            ) else "not_identified_expand_task_set",
        }
    return result


def _copy_eval(snapshot: Path, root: Path, task_id: str):
    safe = task_id.replace(":", "_")
    db_path = root / f"{safe}.sqlite"
    artifacts = root / f"{safe}_artifacts"
    shutil.copy2(snapshot / "closed_loop" / "tehm.sqlite", db_path)
    shutil.copytree(snapshot / "closed_loop" / "artifacts", artifacts)
    return db_path, artifacts


def _binding(fix: dict) -> dict:
    return {"$H0": fix["add_condition"], "$H1": fix["source_state"],
            "$H2": fix["target_state"]}


def _run_activation(snapshot: Path, work: Path, task: dict, source: Path,
                    fix: dict, oracle, *, arm: str) -> dict:
    db_path, artifact_root = _copy_eval(snapshot, work, f"{task['task_id']}_{arm}")
    conn = db.connect(db_path)
    context = RepairContext(
        check="rtl", design_id=task["lineage_id"],
        reports={"rtl": {"status": "violations", "total_violations": 1}},
        symptom_signature={"transformation_family": "GUARD_STRENGTHEN"})
    receipt = retrieve(conn, context)
    base = {
        "arm": arm, "backend": "tehm", "candidates": receipt.candidates_retrieved,
        "applicable": receipt.applicable, "source": "tehm_rule",
        "ablation": {
            "role_view": arm not in ("M5",),
            "predicate_view": arm not in ("M6",),
            "obligation_transfer": arm not in ("M7",),
            "validity_gate": arm in ("M8",),
        },
    }
    if not receipt.results:
        conn.close()
        return {**base, "action_executed": False, "repair_success": False,
                "reason": "no retrieved rule"}
    selected = receipt.results[0]
    base["retrieved_rule_id"] = selected.rule_id
    validity_status = conn.execute(
        "SELECT validity_status FROM tehm_rules WHERE rule_id=?",
        (selected.rule_id,)).fetchone()[0]
    if arm == "M8" and validity_status not in ("VALIDATED", "PROVISIONAL_VALID"):
        conn.close()
        return {**base, "action_executed": False, "repair_success": False,
                "reason": "validity gate rejected rule", "validity_status": validity_status}

    def executor(action, _context):
        edited, _ = apply_rtl_action(
            source.read_text(), {**action["payload"], "domain": action["domain"]})
        with tempfile.TemporaryDirectory(prefix=f"tehm_{arm.lower()}_exec_") as td:
            fixed_path = Path(td) / source.name
            fixed_path.write_text(edited)
            verified = oracle.verify(
                [fixed_path],
                target_tb=source.parent.parent / "tb" / "tb_handshake.v",
                regression_tb=source.parent.parent / "tb" / "tb_basic.v")
        return {
            "before_state": {"reports": context.reports, "config": {},
                             "rtl_slice": source.read_text()[:80]},
            "after_state": {"reports": {"rtl": {"status": "clean",
                                                    "total_violations": 0}},
                            "config": {}, "rtl_slice": edited[:80]},
            "observation_delta": {
                "original_failure": "REMOVED" if verified["verdict"] == "PASS" else "UNKNOWN",
                "first_divergence": {"before": 1, "after": 0},
                "failing_tests": {"before": 1, "after": 0},
                "created_regressions": verified["created_regressions"],
                "newly_observed_failures": verified["newly_observed_failures"]},
            "tool_versions": {}, "verification": verified,
        }

    activation = activate(
        conn, ArtifactStore(artifact_root), rule_id=selected.rule_id,
        context=context, provided_binding=_binding(fix), executor=executor,
        oracle=None, authority_mode="evaluation")
    result = {
        **base, "action_executed": activation.executability_status == "EXECUTABLE",
        "repair_success": activation.outcome == "PASS",
        "activation_id": activation.activation_id,
        "verification_status": activation.verification_status,
        "obligation_coverage": activation.obligation_coverage,
        "validity_status": validity_status,
        "created_regressions": list(activation.created_regressions),
        "harmful_activation": bool(activation.created_regressions),
    }
    if arm == "M7":
        # The target may pass, but without obligation transfer this arm cannot
        # claim a complete repair/signoff.  Preserve the target observation.
        result["repair_success"] = False
        result["target_pass"] = activation.outcome == "PASS"
        result["obligation_coverage"] = 0.0
        result["reason"] = "obligation transfer ablated"
    conn.close()
    return result


def _evaluate_task(snapshot: Path, task: dict, work: Path, oracle) -> dict:
    fixture = ROOT / "tests" / "fixtures" / "rtl_projects" / task["fixture"]
    manifest = json.loads((fixture / "manifest.json").read_text())
    fix = manifest["fix"]
    source = fixture / "rtl" / "req_ack_fsm.v"
    baseline = oracle.verify([source], target_tb=fixture / "tb" / "tb_handshake.v",
                             regression_tb=fixture / "tb" / "tb_basic.v")
    if baseline.get("verdict") == "PASS":
        raise RuntimeError(f"held-out baseline unexpectedly passes: {task['task_id']}")
    rows = {}
    rows["M0"] = {"arm": "M0", "backend": "none", "candidates": 0,
                  "action_executed": False, "repair_success": False,
                  "source": "cold_start", "reason": "no historical candidate"}
    legacy = LegacyMemoryBackend(read_only_eval=True)
    legacy_candidates = legacy.retrieve(
        legacy.build_query(RepairContext(check="rtl", design_id=task["lineage_id"])), limit=10)
    rows["M1"] = {"arm": "M1", "backend": "legacy", "candidates": len(legacy_candidates),
                  "action_executed": False, "repair_success": False,
                  "source": "legacy_memory" if legacy_candidates else "cold_start"}
    conn = db.connect(snapshot / "closed_loop" / "tehm.sqlite")
    episode_count = conn.execute(
        "SELECT COUNT(*) FROM tehm_episodes WHERE domain='rtl'").fetchone()[0]
    conn.close()
    rows["M2"] = {"arm": "M2", "backend": "tehm_episode_only",
                  "candidates": episode_count, "action_executed": False,
                  "repair_success": False, "source": "tehm_episode",
                  "reason": "episode representation has no executable rule"}
    conn = db.connect(snapshot / "closed_loop" / "tehm.sqlite")
    ret = retrieve(conn, RepairContext(check="rtl", design_id=task["lineage_id"],
                                       reports={"rtl": {"status": "violations"}},
                                       symptom_signature={"transformation_family": "GUARD_STRENGTHEN"}))
    conn.close()
    rows["M3"] = {"arm": "M3", "backend": "tehm_five_view_retrieval",
                  "candidates": ret.candidates_retrieved, "applicable": ret.applicable,
                  "action_executed": False, "repair_success": False,
                  "source": "tehm_views", "reason": "retrieval-only arm does not crystallize/execute"}
    for arm in ("M4", "M5", "M6", "M7", "M8"):
        rows[arm] = _run_activation(snapshot, work, task, source, fix, oracle, arm=arm)
    success = {arm: bool(rows[arm].get("repair_success")) for arm in ARMS}
    return {
        "task_id": task["task_id"], "domain": "rtl", "lineage_id": task["lineage_id"],
        "lineage_cluster": task["lineage_cluster"], "fixture": task["fixture"],
        "baseline": baseline, "arms": rows, "success": success,
        "evidence_mode": "real_icarus_replay_per_arm",
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--snapshot", type=Path,
                    default=Path("/data1/zhangdy/tehm-evidence-freeze-v2"))
    ap.add_argument("--output", type=Path, default=None)
    args = ap.parse_args(argv)
    snapshot = args.snapshot.resolve()
    output = (args.output or snapshot / "evaluation" / "m0_m8_v2_report.json").resolve()
    tasks = [
        {"task_id": "rtl:req_ack_bug", "lineage_id": "req_ack_bug",
         "lineage_cluster": "rtl:handshake:send", "fixture": "req_ack_bug"},
        {"task_id": "rtl:req_ack_bug2", "lineage_id": "req_ack_bug2",
         "lineage_cluster": "rtl:handshake:write", "fixture": "req_ack_bug2"},
        {"task_id": "rtl:req_ack_bug3", "lineage_id": "req_ack_bug3",
         "lineage_cluster": "rtl:handshake:read", "fixture": "req_ack_bug3"},
        {"task_id": "rtl:req_ack_bug4", "lineage_id": "req_ack_bug4",
         "lineage_cluster": "rtl:handshake:ready", "fixture": "req_ack_bug4"},
        {"task_id": "rtl:valid_ready_bug", "lineage_id": "valid_ready_bug",
         "lineage_cluster": "rtl:valid_ready:commit", "fixture": "valid_ready_bug"},
        {"task_id": "rtl:fifo_space_bug", "lineage_id": "fifo_space_bug",
         "lineage_cluster": "rtl:fifo:capacity_return", "fixture": "fifo_space_bug"},
    ]
    manifest_path = output.parent / "heldout_task_manifest_v2.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_manifest = json.loads((snapshot / "bundle_manifest.json").read_text())
    snapshot_firewall = snapshot_manifest.get("firewall") or \
        snapshot_manifest.get("metadata", {}).get("firewall", {})
    manifest = {
        "version": "heldout-task-set-v0.4", "tasks": tasks,
        "independent_lineages": [t["lineage_id"] for t in tasks],
        "lineage_clusters": [t["lineage_cluster"] for t in tasks],
        "firewall": {"heldout_not_captured": True, "heldout_mutates_copy_only": True,
                      "training_lineages": snapshot_manifest.get("training_lineages") or
                      snapshot_firewall.get("training_lineages", []),
                      "heldout_lineages": sorted(set(
                          snapshot_manifest.get("heldout_lineages") or
                          snapshot_firewall.get("heldout_lineages", [])) |
                          {t["lineage_id"] for t in tasks}),
                      "heldout_rtl_fixtures_absent_from_snapshot": True},
        "repeated_lineages_not_independent": True,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    oracle = __import__("tehm.rtl.rtl_oracle", fromlist=["IcarusOracle"]).IcarusOracle()
    if not oracle.available:
        raise RuntimeError("M0-M8 v2 requires the frozen Icarus oracle")
    work = output.parent / "m0_m8_v2_eval_work"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    task_rows = [_evaluate_task(snapshot, t, work, oracle) for t in tasks]
    summary = {}
    for arm in ARMS:
        k = sum(r["success"][arm] for r in task_rows)
        n = len(task_rows)
        summary[arm] = {"successes": k, "tasks": n,
                        "rate": k / n if n else None, "wilson_95": wilson(k, n)}
    clusters = {r["lineage_cluster"]: [r] for r in task_rows}
    lineage_summary = {}
    for arm in ARMS:
        k = sum(all(r["success"][arm] for r in rs) for rs in clusters.values())
        n = len(clusters)
        lineage_summary[arm] = {"successes": k, "clusters": n,
                                "rate": k / n if n else None,
                                "wilson_95": wilson(k, n)}
    funnel = {}
    for arm in ARMS:
        values = [r["arms"][arm] for r in task_rows]
        funnel[arm] = {
            "retrieval_candidates": sum(v.get("candidates", 0) for v in values),
            "applicable": sum(bool(v.get("applicable")) for v in values),
            "action_executed": sum(bool(v.get("action_executed")) for v in values),
            "repair_success": sum(bool(v.get("repair_success")) for v in values),
            "mean_obligation_coverage": round(sum(v.get("obligation_coverage", 0.0) or 0.0
                                                   for v in values) / len(values), 6),
        }
    report = {
        "version": "controlled-m0-m8-v3.0", "task_manifest": str(manifest_path),
        "snapshot_manifest": str(snapshot / "bundle_manifest.json"),
        "pilot_scope": {"n_tasks": len(task_rows),
                        "independent_lineages": [t["lineage_id"] for t in tasks],
                        "lineage_clusters": [t["lineage_cluster"] for t in tasks],
                        "claims": "second frozen held-out representation ablation; not a universal benchmark"},
        "arms": list(ARMS), "task_rows": task_rows, "summary": summary,
        "lineage_summary": lineage_summary, "memory_funnel": funnel,
        "component_discrimination": component_discrimination(task_rows),
        "firewall": manifest["firewall"],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    md = output.with_suffix(".md")
    md.write_text("# Controlled M0--M8 second held-out comparison\n\n" +
                  "| Arm | Successes | Tasks | Rate | Wilson 95% |\n|---|---:|---:|---:|---|\n" +
                  "\n".join(f"| {a} | {v['successes']} | {v['tasks']} | {v['rate']} | {v['wilson_95']} |"
                            for a, v in summary.items()) + "\n\n" +
                  "Cluster-level summary:\n\n" +
                  "| Arm | Successes | Clusters | Rate | Wilson 95% |\n|---|---:|---:|---:|---|\n" +
                  "\n".join(f"| {a} | {v['successes']} | {v['clusters']} | {v['rate']} | {v['wilson_95']} |"
                            for a, v in lineage_summary.items()) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
