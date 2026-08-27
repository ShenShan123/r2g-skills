#!/usr/bin/env python3
"""Run leave-one-cluster-out procedural M1/M8 replay on real RTL fixtures.

Each fold captures every listed fixture into a disposable TEHM store, marks one
whole mechanism cluster held-out before crystallization, and evaluates that
cluster with a rule learned only from the remaining clusters.  The canonical
freeze and its SQLite file are read-only throughout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tehm import db  # noqa: E402
from tehm.artifact_store import ArtifactStore  # noqa: E402
from tehm.crystallization.build_rules import crystallize_all  # noqa: E402
from tehm.dataset import assign_transition  # noqa: E402
from tehm.rtl.rtl_evidence import capture_rtl_fix  # noqa: E402
from tehm.rtl.rtl_oracle import IcarusOracle  # noqa: E402
from tehm.sync import canonical_json, sha256_file, verify_bundle  # noqa: E402

from run_procedural_ab_v4_dev import (  # noqa: E402
    _fixture_paths, _m1, _m8, _read, _wilson,
)


def _family(manifest: dict) -> str:
    fix = manifest.get("fix") or {}
    return str(fix.get("transformation_family") or
               ("GUARD_STRENGTHEN" if fix.get("domain") ==
                "rtl.GUARD_STRENGTHEN" else manifest.get("mechanism_family")))


def _rule_map(conn) -> dict[tuple[str, str], str]:
    result = {}
    for row in conn.execute(
            "SELECT rule_id, before_pattern_json FROM tehm_rules "
            "WHERE domain='rtl' AND validity_status IN "
            "('PROVISIONAL_VALID','VALIDATED') ORDER BY rule_id"):
        pattern = db.read_json(row["before_pattern_json"])
        if isinstance(pattern, dict) and pattern.get("type"):
            profile = pattern.get("compatibility_profile")
            if profile:
                result.setdefault((str(pattern["type"]), str(profile)),
                                  str(row["rule_id"]))
    return result


def _rule_diagnostics(conn, family: str) -> list[dict]:
    """Return every crystallized candidate for a family, including rejects.

    LOCO must distinguish “no learned rule” from a rule rejected by a
    particular validity gate.  The latter is a structural compatibility signal
    and is useful evidence for the next fixture/partition design.
    """
    rows = conn.execute(
        "SELECT rule_id, validity_status, validity_profile_json, "
        "before_pattern_json FROM tehm_rules WHERE domain='rtl' "
        "ORDER BY rule_id").fetchall()
    result = []
    for row in rows:
        pattern = db.read_json(row["before_pattern_json"])
        if not isinstance(pattern, dict) or str(pattern.get("type")) != family:
            continue
        profile = db.read_json(row["validity_profile_json"], default={})
        failed = []
        for gate in (profile.get("gates") or []):
            if gate.get("ok") is False:
                failed.append({"name": gate.get("name"),
                               "detail": gate.get("detail") or {}})
        result.append({"rule_id": str(row["rule_id"]),
                       "validity_status": str(row["validity_status"]),
                       "failed_gates": failed})
    return result


def _capture_fold(fold_root: Path, heldout: set[str], fixtures: list[str]) -> Path:
    closed = fold_root / "closed_loop"
    closed.mkdir(parents=True, exist_ok=True)
    conn = db.connect(closed / "tehm.sqlite")
    db.ensure_schema(conn)
    store = ArtifactStore(closed / "artifacts")
    oracle = IcarusOracle()
    if not oracle.available:
        raise RuntimeError("leave-one-cluster-out requires real Icarus")
    receipts = {}
    for name in fixtures:
        receipt = capture_rtl_fix(
            conn, store, ROOT / "tests" / "fixtures" / "rtl_projects" / name,
            oracle=oracle)
        receipts[name] = receipt.transition_id
    for name in sorted(heldout):
        assign_transition(conn, transition_id=receipts[name], campaign_id="live",
                          split="heldout", learner_eligible=False)
    conn.commit()
    crystallize_all(conn, campaign_id="live")
    conn.commit()
    conn.close()
    return closed


def _source_excludes(conn, rule_id: str, heldout: set[str]) -> bool:
    rows = conn.execute(
        "SELECT lineage_id FROM tehm_rule_sources WHERE rule_id=?", (rule_id,))
    return not (set(str(row["lineage_id"]) for row in rows) & heldout)


def run(*, bundle: Path, manifest_path: Path, output: Path) -> dict:
    checked = verify_bundle(bundle)
    if not checked.get("ok"):
        raise ValueError(f"invalid freeze: {checked.get('detail')}")
    manifest = _read(manifest_path)
    if manifest.get("version") != "procedural-loco-v1":
        raise ValueError("wrong leave-one-cluster-out manifest")
    oracle = IcarusOracle()
    if not oracle.available:
        raise RuntimeError("leave-one-cluster-out requires real Icarus")
    ab_manifest = _read(ROOT / "evaluation" /
                        "procedural_ab_v4_dev_manifest.json")
    task_by_lineage = {task["lineage_id"]: task
                       for task in ab_manifest["tasks"]}
    fixture_names = list(manifest["lineages"])
    fixture_manifests = {
        name: _read(ROOT / "tests" / "fixtures" / "rtl_projects" /
                    name / "manifest.json") for name in fixture_names}
    clusters = {
        task["lineage_cluster"]: {task["lineage_id"]}
        for task in ab_manifest["tasks"]
    }
    canonical_before = sha256_file(bundle / "closed_loop" / "tehm.sqlite")
    folds = []
    with tempfile.TemporaryDirectory(prefix="tehm-loco-") as td:
        scratch = Path(td)
        for cluster, heldout in sorted(clusters.items()):
            fold_root = scratch / cluster.replace(":", "_")
            closed = _capture_fold(fold_root, heldout, fixture_names)
            conn = db.connect_read_only(closed / "tehm.sqlite")
            try:
                rule_ids = _rule_map(conn)
                heldout_family = _family(fixture_manifests[next(iter(heldout))])
                heldout_profile = str(
                    (fixture_manifests[next(iter(heldout))].get("fix") or {})
                    .get("compatibility_profile") or "")
                rule_id = rule_ids.get((heldout_family, heldout_profile))
                source_excluded = (_source_excludes(conn, rule_id, heldout)
                                   if rule_id else False)
                diagnostics = _rule_diagnostics(conn, heldout_family)
            finally:
                conn.close()
            task = task_by_lineage[next(iter(heldout))]
            fixture = (REPO / task["fixture"]).resolve()
            task_manifest = _read(fixture / "manifest.json")
            source, target_tb, regression_tb = _fixture_paths(fixture, task_manifest)
            baseline = oracle.verify([source], target_tb=target_tb,
                                     regression_tb=regression_tb)
            if baseline.get("verdict") != "FAIL":
                raise RuntimeError(f"baseline did not fail: {task['task_id']}")
            eval_work = fold_root / "eval_work"
            eval_work.mkdir(parents=True, exist_ok=True)
            if rule_id:
                m8 = _m8(fold_root, eval_work, task, rule_id, oracle)
            else:
                # A fold without an admissible rule is an observed, auditable
                # abstention—not a fabricated failure or a promotion signal.
                m8 = {
                    "arm": "M8", "backend": "tehm",
                    "status": "ABSTAINED_NO_ADMISSIBLE_RULE",
                    "repair_success": False, "action_executed": False,
                    "harmful_activation": False,
                    "obligation_coverage": 0.0,
                    "obligation_statuses": [],
                }
            row = {
                "cluster": cluster,
                "heldout_lineages": sorted(heldout),
                "training_lineages": sorted(set(fixture_names) - heldout),
                "rule_id": rule_id,
                "rule_diagnostics": diagnostics,
                "transformation_family": heldout_family,
                "compatibility_profile": heldout_profile,
                "heldout_source_excluded": source_excluded,
                "baseline_verdict": baseline["verdict"],
                "arms": {
                    "M1": _m1(task),
                    "M8": m8,
                },
            }
            if rule_id is None:
                unstable = [item for item in diagnostics
                             if item["validity_status"] == "UNSTABLE_CANDIDATE"]
                if unstable:
                    row["abstention"] = {
                        "status": "ABSTAINED_UNSTABLE_CANDIDATE",
                        "reason": "no_admissible_rule_after_validity_gate",
                        "candidates": unstable,
                    }
                else:
                    row["abstention"] = {
                        "status": "ABSTAINED_NO_ADMISSIBLE_RULE",
                        "reason": "no_admissible_rule_crystallized",
                        "candidates": diagnostics,
                    }
            folds.append(row)
    canonical_after = sha256_file(bundle / "closed_loop" / "tehm.sqlite")
    if canonical_before != canonical_after:
        raise RuntimeError("canonical freeze SQLite changed during LOCO")
    m8_rows = [row["arms"]["M8"] for row in folds]
    m8_successes = sum(bool(row.get("repair_success")) for row in m8_rows)
    m8_exec = sum(bool(row.get("action_executed")) for row in m8_rows)
    m8_harm = sum(bool(row.get("harmful_activation")) for row in m8_rows)
    complete = all(
        row.get("obligation_coverage") == 1.0 and
        all(s == "PASS" for s in row.get("obligation_statuses", []))
        for row in m8_rows)
    acceptance = manifest["acceptance"]
    checks = {
        "min_folds": len(folds) >= acceptance["min_folds"],
        "heldout_source_exclusion": all(
            row["heldout_source_excluded"] for row in folds),
        "complete_obligation_coverage": complete,
        "m8_successes": m8_successes == len(folds),
        "m8_harmful_rate": (m8_harm / m8_exec if m8_exec else 0.0) <=
                           acceptance["max_m8_harmful_rate"],
        "canonical_memory_mutation": canonical_before == canonical_after,
    }
    report = {
        "version": "procedural-loco-v1",
        "manifest": str(manifest_path.resolve()),
        "manifest_digest": hashlib.sha256(canonical_json(manifest)).hexdigest(),
        "bundle_digest": checked["manifest"].get("bundle_digest"),
        "oracle": {"available": oracle.available, "version": "icarus-oracle-v0.1"},
        "canonical_db_sha256_before": canonical_before,
        "canonical_db_sha256_after": canonical_after,
        "folds": folds,
        "summary": {
            "folds": len(folds), "m1_successes": 0,
            "m8_successes": m8_successes,
            "m8_rate": m8_successes / len(folds) if folds else 0.0,
            "m8_wilson_95": _wilson(m8_successes, len(folds)),
            "m8_harmful_rate": m8_harm / m8_exec if m8_exec else 0.0,
        },
        "acceptance_checks": checks,
        "acceptance_passed": all(checks.values()),
        "evidence_mode": "real_icarus_leave_one_cluster_out_m1_m8",
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "procedural_loco_v1_manifest.json").write_bytes(canonical_json(manifest))
    (output / "procedural_loco_v1_report.json").write_bytes(canonical_json(report))
    (output / "procedural_loco_v1_report.md").write_text(
        "# Procedural leave-one-cluster-out v1\n\n"
        f"Acceptance passed: **{report['acceptance_passed']}**\n\n"
        "| Held-out cluster | Family | M1 | M8 | Source excluded | Coverage |\n"
        "|---|---|---:|---:|---|---:|\n" + "\n".join(
            f"| {row['cluster']} | {row['transformation_family']} | "
            f"{int(row['arms']['M1']['repair_success'])} | "
            f"{int(row['arms']['M8']['repair_success'])} | "
            f"{row['heldout_source_excluded']} | "
            f"{row['arms']['M8'].get('obligation_coverage')} |"
            for row in folds) + "\n\n"
        "## Fail-closed classifications\n\n" + "\n".join(
            f"- `{row['cluster']}`: "
            f"{(row.get('abstention') or {}).get('status', 'EXECUTED')}"
            + (f"; candidates={len(row.get('rule_diagnostics') or [])}"
               if row.get('abstention') else "")
            for row in folds) + "\n")
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bundle", type=Path, required=True)
    ap.add_argument("--manifest", type=Path,
                    default=ROOT / "evaluation" /
                    "procedural_loco_v1_manifest.json")
    ap.add_argument("--output", type=Path,
                    default=REPO / "evidence" / "tehm-procedural-loco-v1")
    args = ap.parse_args(argv)
    report = run(bundle=args.bundle.resolve(), manifest_path=args.manifest.resolve(),
                 output=args.output.resolve())
    print(json.dumps({"ok": True, "acceptance_passed": report["acceptance_passed"],
                      "summary": report["summary"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
