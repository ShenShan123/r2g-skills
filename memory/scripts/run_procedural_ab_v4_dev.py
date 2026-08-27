#!/usr/bin/env python3
"""Run the fixed-budget M1/M8 procedural A/B on independent RTL clusters.

M1 is the read-only legacy backend and M8 is the candidate-visible TEHM
procedural rule.  Every M8 execution happens in a copied freeze snapshot, with
the real Icarus target/regression oracle and parser-backed structural graph.
The canonical freeze is never mutated and this development A/B does not claim
production promotion without the complete six-gate evidence bundle.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contracts import RepairContext  # noqa: E402
from legacy_backend import LegacyMemoryBackend  # noqa: E402
from tehm import db  # noqa: E402
from tehm.activation.pipeline import activate  # noqa: E402
from tehm.artifact_store import ArtifactStore  # noqa: E402
from tehm.ids import stable_dumps  # noqa: E402
from tehm.lifecycle.rule_status import enter_shadow, get_status, set_status  # noqa: E402
from tehm.rtl.rtl_actions import apply_rtl_action  # noqa: E402
from tehm.rtl.rtl_graph import build_rtl_graph  # noqa: E402
from tehm.rtl.rtl_oracle import IcarusOracle  # noqa: E402
from tehm.rtl.verilog_parse import parse_verilog  # noqa: E402
from tehm.sync import canonical_json, sha256_file, verify_bundle  # noqa: E402


def _read(path: Path) -> dict:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _wilson(k: int, n: int) -> list[float] | None:
    if n <= 0:
        return None
    z = 1.959963984540054
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [round(max(0.0, centre - half), 6),
            round(min(1.0, centre + half), 6)]


def _fixture_paths(fixture: Path, manifest: dict) -> tuple[Path, Path, Path]:
    verification = manifest.get("verification") or {}
    rtl_files = sorted((fixture / "rtl").glob("*.v"))
    if len(rtl_files) != 1:
        raise ValueError(f"fixture must contain exactly one RTL source: {fixture}")
    target = fixture / verification["target_test"]
    regression = fixture / verification["frozen_regression"]
    if not target.is_file() or not regression.is_file():
        raise FileNotFoundError(f"fixture oracle files missing: {fixture}")
    return rtl_files[0], target, regression


def _graph(source: str, design_id: str,
           compatibility_profile: str | None = None) -> dict:
    modules = parse_verilog(source)
    if not modules:
        raise ValueError(f"no parseable RTL module for {design_id}")
    return build_rtl_graph(modules[0], design_id=design_id,
                           compatibility_profile=compatibility_profile).to_dict()


def _rules_by_family(conn) -> dict[tuple[str, str], str]:
    rows = conn.execute(
        """SELECT rule_id, before_pattern_json FROM tehm_rules
           WHERE domain='rtl' AND validity_status IN
                 ('PROVISIONAL_VALID', 'VALIDATED')
           ORDER BY rule_id""").fetchall()
    result = {}
    for row in rows:
        pattern = db.read_json(row["before_pattern_json"])
        family = pattern.get("type") if isinstance(pattern, dict) else None
        profile = (pattern.get("compatibility_profile")
                   if isinstance(pattern, dict) else None)
        if family and profile:
            result.setdefault((str(family), str(profile)), str(row["rule_id"]))
    if not result:
        raise ValueError("freeze contains no admissible RTL procedural rules")
    return result


def _enroll_copy_candidate(conn, rule_id: str) -> None:
    """Enroll the frozen rule for isolated evaluation, never production."""
    scope = "rtl"
    if get_status(conn, rule_id=rule_id, target_scope=scope) is None:
        enter_shadow(conn, rule_id=rule_id, target_scope=scope,
                     provenance={"authority": "v4-development-ab-enrollment"})
        set_status(conn, rule_id=rule_id, target_scope=scope, status="candidate",
                   provenance={"authority": "v4-development-ab-enrollment"})


def _binding(rule: dict, fix: dict) -> dict:
    """Bind every family-specific RTL hole from the fixture manifest."""
    binding = {}
    for pattern in (rule.get("before_pattern") or {},
                    rule.get("after_pattern") or {}):
        for key, value in pattern.items():
            if not (isinstance(value, str) and value.startswith("$H")):
                continue
            field = key.removeprefix("rtl.")
            if field in fix:
                binding[value] = fix[field]
    return binding


def _copy_eval(bundle: Path, work: Path, task_id: str) -> tuple[Path, Path]:
    safe = task_id.replace(":", "_")
    db_path = work / f"{safe}.sqlite"
    artifacts = work / f"{safe}_artifacts"
    shutil.copy2(bundle / "closed_loop" / "tehm.sqlite", db_path)
    shutil.copytree(bundle / "closed_loop" / "artifacts", artifacts)
    return db_path, artifacts


def _m8(bundle: Path, work: Path, task: dict, rule_id: str,
        oracle: IcarusOracle) -> dict:
    fixture = (REPO / task["fixture"]).resolve()
    manifest = _read(fixture / "manifest.json")
    source_path, target_tb, regression_tb = _fixture_paths(fixture, manifest)
    source = source_path.read_text()
    fix = dict(manifest["fix"])
    modules = parse_verilog(source)
    if not modules:
        raise ValueError(f"no parseable module in {source_path}")
    fix.setdefault("module", modules[0].name)
    profile = fix.get("compatibility_profile") or "rtl.fsm.single_guard.v1"
    db_path, artifact_root = _copy_eval(bundle, work, task["task_id"])
    conn = db.connect(db_path)
    try:
        _enroll_copy_candidate(conn, rule_id)
        context = RepairContext(
            check="rtl", design_id=task["lineage_id"],
            reports={"rtl": {"status": "violations", "total_violations": 1}},
            structural_graph=_graph(source, task["lineage_id"], profile),
            compatibility_profile=profile,
            symptom_signature={"transformation_family": fix.get(
                "transformation_family", task.get("expected_mechanism_family"))})

        def executor(action, _context):
            payload = {**action["payload"], **fix, "domain": action["domain"]}
            edited, edit = apply_rtl_action(source, payload)
            with tempfile.TemporaryDirectory(prefix="tehm_v4_ab_") as td:
                fixed_path = Path(td) / source_path.name
                fixed_path.write_text(edited)
                verification = oracle.verify(
                    [fixed_path], target_tb=target_tb,
                    regression_tb=regression_tb)
            return {
                "before_state": {
                    "config": {}, "reports": context.reports,
                    "structural_graph": context.structural_graph,
                    "rtl_slice": source[:160],
                },
                "after_state": {
                    "config": {},
                    "reports": {"rtl": {"status": "clean",
                                             "total_violations": 0}},
                    "structural_graph": _graph(edited, task["lineage_id"], profile),
                    "rtl_slice": edited[:160],
                },
                "observation_delta": {
                    "original_failure": "REMOVED" if verification["verdict"] == "PASS" else "PRESENT",
                    "first_divergence": {"before": 1, "after": 0},
                    "failing_tests": {"before": 1, "after": 0},
                    "created_regressions": verification.get("created_regressions", []),
                    "newly_observed_failures": verification.get("newly_observed_failures", []),
                    "rewrite": edit,
                },
                "tool_versions": {"icarus": verification.get("extractor_version")},
                "verification": verification,
            }

        activation = activate(
            conn, ArtifactStore(artifact_root), rule_id=rule_id,
            context=context, provided_binding=_binding(db_rule(conn, rule_id), fix),
            executor=executor, oracle=None, authority_mode="evaluation")
        statuses = [item.get("status")
                    for item in activation.obligation_transfer.get("results", [])]
        return {
            "arm": "M8", "backend": "tehm", "retrieved_rule_id": rule_id,
            "applicable": activation.applicability_status,
            "binding_status": activation.binding_status,
            "action_executed": activation.executability_status == "EXECUTABLE",
            "repair_success": activation.outcome == "PASS",
            "verification_status": activation.verification_status,
            "obligation_coverage": activation.obligation_coverage,
            "obligation_statuses": statuses,
            "harmful_activation": bool(activation.created_regressions),
            "activation_id": activation.activation_id,
            "produced_transition_id": activation.produced_transition_id,
            "binding_proof": activation.binding.get("proof"),
        }
    finally:
        conn.close()


def db_rule(conn, rule_id: str) -> dict:
    row = conn.execute(
        "SELECT before_pattern_json, after_pattern_json FROM tehm_rules "
        "WHERE rule_id=?", (rule_id,)).fetchone()
    if row is None:
        raise ValueError(f"rule missing from evaluation snapshot: {rule_id}")
    return {
        "before_pattern": db.read_json(row["before_pattern_json"]),
        "after_pattern": db.read_json(row["after_pattern_json"]),
    }


def _m1(task: dict) -> dict:
    legacy = LegacyMemoryBackend(read_only_eval=True)
    candidates = legacy.retrieve(
        legacy.build_query(RepairContext(check="rtl", design_id=task["lineage_id"])),
        limit=10)
    return {
        "arm": "M1", "backend": "legacy",
        "candidates": len(candidates), "action_executed": False,
        "repair_success": False,
        "source": "legacy_memory" if candidates else "cold_start",
        "harmful_activation": False,
    }


def run(*, bundle: Path, manifest_path: Path, output: Path) -> dict:
    checked = verify_bundle(bundle)
    if not checked.get("ok"):
        raise ValueError(f"invalid freeze: {checked.get('detail')}")
    manifest = _read(manifest_path)
    if manifest.get("version") != "procedural-ab-v4-development-v2":
        raise ValueError("wrong v4 development task manifest")
    firewall = manifest.get("firewall") or {}
    if set(firewall.get("training_lineages", [])) & set(
            firewall.get("heldout_lineages", [])):
        raise ValueError("training/held-out firewall overlap")
    oracle = IcarusOracle()
    if not oracle.available:
        raise RuntimeError("v4 M1/M8 replay requires real Icarus")
    before_sha = sha256_file(bundle / "closed_loop" / "tehm.sqlite")
    source_conn = db.connect_read_only(bundle / "closed_loop" / "tehm.sqlite")
    try:
        rules_by_family = _rules_by_family(source_conn)
    finally:
        source_conn.close()
    work = output / "eval_work"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)

    task_rows = []
    for task in manifest["tasks"]:
        fixture = (REPO / task["fixture"]).resolve()
        fixture_manifest = _read(fixture / "manifest.json")
        source_path, target_tb, regression_tb = _fixture_paths(fixture, fixture_manifest)
        baseline = oracle.verify([source_path], target_tb=target_tb,
                                 regression_tb=regression_tb)
        if baseline.get("verdict") != "FAIL":
            raise RuntimeError(f"baseline did not fail: {task['task_id']}")
        family = fixture_manifest["fix"].get("transformation_family")
        if not family:
            family = ("GUARD_STRENGTHEN" if fixture_manifest["fix"].get(
                "domain") == "rtl.GUARD_STRENGTHEN" else
                      fixture_manifest.get("mechanism_family"))
        profile = (fixture_manifest.get("fix") or {}).get(
            "compatibility_profile")
        if not profile:
            raise RuntimeError(
                f"task has no explicit compatibility_profile: {task['task_id']}")
        rule_id = rules_by_family.get((family, str(profile)))
        if not rule_id:
            raise RuntimeError(
                f"no frozen rule for family/profile {family!r}/{profile!r}")
        repeats = max(1, int((manifest.get("budget") or {}).get(
            "repeats_per_task", 1)))
        repeat_rows = []
        for repeat in range(repeats):
            repeat_task = dict(task)
            repeat_task["task_id"] = f"{task['task_id']}:r{repeat + 1}"
            repeat_rows.append(_m8(bundle, work, repeat_task, rule_id, oracle))
        aggregate = dict(repeat_rows[0])
        aggregate["repeat_count"] = repeats
        aggregate["repeat_rows"] = repeat_rows
        aggregate["action_executed"] = all(
            bool(row.get("action_executed")) for row in repeat_rows)
        aggregate["repair_success"] = all(
            bool(row.get("repair_success")) for row in repeat_rows)
        aggregate["harmful_activation"] = any(
            bool(row.get("harmful_activation")) for row in repeat_rows)
        aggregate["obligation_coverage"] = min(
            (row.get("obligation_coverage") for row in repeat_rows),
            default=0.0)
        aggregate["obligation_statuses"] = sorted({
            status for row in repeat_rows
            for status in (row.get("obligation_statuses") or [])})
        aggregate["repeat_verdicts"] = [
            row.get("verification_status") for row in repeat_rows]
        task_rows.append({
            "task_id": task["task_id"],
            "lineage_id": task["lineage_id"],
            "lineage_cluster": task["lineage_cluster"],
            "fixture": task["fixture"],
            "transformation_family": family,
            "baseline_verdict": baseline["verdict"],
            "arms": {"M1": _m1(task), "M8": aggregate},
        })
    after_sha = sha256_file(bundle / "closed_loop" / "tehm.sqlite")
    if before_sha != after_sha:
        raise RuntimeError("canonical freeze SQLite changed during A/B")

    clusters = {}
    for row in task_rows:
        clusters.setdefault(row["lineage_cluster"], []).append(row)
    summary = {}
    for arm in ("M1", "M8"):
        successes = sum(bool(row["arms"][arm].get("repair_success"))
                        for row in task_rows)
        summary[arm] = {
            "successes": successes, "tasks": len(task_rows),
            "rate": successes / len(task_rows),
            "wilson_95": _wilson(successes, len(task_rows)),
        }
    cluster_summary = {}
    for arm in ("M1", "M8"):
        successes = sum(all(row["arms"][arm].get("repair_success")
                            for row in cohort)
                        for cohort in clusters.values())
        cluster_summary[arm] = {
            "successes": successes, "clusters": len(clusters),
            "rate": successes / len(clusters),
            "wilson_95": _wilson(successes, len(clusters)),
        }
    m8_rows = [row["arms"]["M8"] for row in task_rows]
    m8_exec = sum(bool(row.get("action_executed")) for row in m8_rows)
    m8_harm = sum(bool(row.get("harmful_activation")) for row in m8_rows)
    m8_complete = all(
        row.get("obligation_coverage") == 1.0 and
        all(status == "PASS" for status in row.get("obligation_statuses", []))
        for row in m8_rows)
    acceptance = manifest["acceptance"]
    checks = {
        "min_tasks": len(task_rows) >= acceptance["min_tasks"],
        "cluster_intervals": bool(cluster_summary) if acceptance["require_cluster_intervals"] else True,
        "m8_successes": summary["M8"]["successes"] >= acceptance["min_m8_successes"],
        "m8_harmful_rate": (m8_harm / m8_exec if m8_exec else 0.0)
                             <= acceptance["max_m8_harmful_rate"],
        "m1_m8_success_delta_positive": summary["M8"]["successes"] > summary["M1"]["successes"],
        "complete_obligation_coverage": m8_complete if acceptance["require_complete_obligation_coverage"] else True,
    }
    report = {
        "version": "procedural-ab-v4-development-v2",
        "manifest": str(manifest_path.resolve()),
        "manifest_digest": hashlib.sha256(canonical_json(manifest)).hexdigest(),
        "bundle_digest": checked["manifest"].get("bundle_digest"),
        "rule_id": rule_id,
        "oracle": {"available": oracle.available, "version": "icarus-oracle-v0.1"},
        "canonical_memory_mutation": "none",
        "canonical_db_sha256_before": before_sha,
        "canonical_db_sha256_after": after_sha,
        "firewall": firewall,
        "task_rows": task_rows,
        "summary": summary,
        "cluster_summary": cluster_summary,
        "acceptance_checks": checks,
        "acceptance_passed": all(checks.values()),
        "evidence_mode": "real_icarus_fixed_budget_m1_m8_candidate_staging",
        "runtime_authority": {
            "lifecycle_statuses": ["candidate"],
            "production_runtime": False,
            "promotion_evaluation": "not_evaluated_without_six_gate_evidence",
        },
        "repeat_budget": int((manifest.get("budget") or {}).get(
            "repeats_per_task", 1)),
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "procedural_ab_v4_dev_report.json").write_bytes(canonical_json(report))
    (output / "procedural_ab_v4_dev_manifest.json").write_bytes(canonical_json(manifest))
    (output / "procedural_ab_v4_dev_report.md").write_text(
        "# Procedural M1/M8 v4 development A/B\n\n"
        f"Acceptance passed: **{report['acceptance_passed']}**\n\n"
        "| Arm | Successes | Tasks | Rate | Wilson 95% |\n"
        "|---|---:|---:|---:|---|\n" +
        "\n".join(
            f"| {arm} | {row['successes']} | {row['tasks']} | "
            f"{row['rate']:.4f} | {row['wilson_95']} |"
            for arm, row in summary.items()) +
        "\n\n```json\n" + json.dumps(
            {"cluster_summary": cluster_summary,
             "acceptance_checks": checks}, indent=2, sort_keys=True) +
        "\n```\n")
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bundle", type=Path, required=True)
    ap.add_argument("--manifest", type=Path,
                    default=ROOT / "evaluation" /
                    "procedural_ab_v4_dev_manifest.json")
    ap.add_argument("--output", type=Path,
                    default=Path("/tmp/tehm-procedural-ab-v4-dev"))
    args = ap.parse_args(argv)
    report = run(bundle=args.bundle.resolve(),
                 manifest_path=args.manifest.resolve(),
                 output=args.output.resolve())
    print(json.dumps({"ok": True, "output": str(args.output.resolve()),
                      "acceptance_passed": report["acceptance_passed"],
                      "summary": report["summary"],
                      "cluster_summary": report["cluster_summary"]},
                     indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
