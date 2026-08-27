#!/usr/bin/env python3
"""Execute one real ORFS TEHM route trial with an auditable rollback receipt.

The input database is copied before crystallization/lifecycle mutation.  A
validated flow.signoff rule is admitted to candidate, then the production
``run_pending_orfs_trials`` executor runs control and TEHM arms in isolated
copies.  A/B outcomes are stored only in trial/activation tables; the subject
lineage is explicitly classified as an A/B lineage in the emitted firewall.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from tehm import db  # noqa: E402
from tehm.artifact_store import ArtifactStore  # noqa: E402
from tehm.crystallization.build_rules import crystallize_all  # noqa: E402
from tehm.honesty import h10_rollback_authority  # noqa: E402
from tehm.lifecycle.orfs_trial import run_pending_orfs_trials  # noqa: E402
from tehm.lifecycle.rule_status import enter_shadow, set_status  # noqa: E402
from orfs_storage import default_work_root, enforce_work_root  # noqa: E402


def _copy_receipts(root: Path, trial: dict) -> list[dict]:
    copied = []
    metrics = trial.get("metrics") or {}
    for pair in metrics.get("pairs") or []:
        sandbox = Path((pair.get("rollback_receipt") or {}).get("sandbox_root", ""))
        for arm in ("arm_a", "arm_b"):
            for source in sorted((sandbox / arm / "backend").glob("RUN_*/run-meta.json")):
                rel = Path("receipts") / f"repeat_{pair.get('repeat', 0)}" / arm / "run-meta.json"
                destination = root / rel
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                copied.append({"path": rel.as_posix(), "source": str(source)})
            for source in sorted((sandbox / arm / "backend").glob("RUN_*/stage_log.jsonl")):
                rel = Path("receipts") / f"repeat_{pair.get('repeat', 0)}" / arm / "stage_log.jsonl"
                destination = root / rel
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                copied.append({"path": rel.as_posix(), "source": str(source)})
    return copied


def _merge_training_snapshot(conn: sqlite3.Connection, source_db: Path) -> int:
    """Merge newly captured physical training rows into the trial snapshot.

    The frozen v2 DB carries the audited RTL/rule surface; the live canonical
    physical store carries later, independently captured ORFS lineages.  Only
    append-only evidence tables are merged, with primary-key idempotency.  No
    rules, lifecycle rows, activations, or trials are imported from the live
    store, so the trial remains the sole authority for its A/B mutation.
    """
    if not source_db.is_file():
        return 0
    tables = (
        "tehm_states", "tehm_transitions", "tehm_episodes",
        "tehm_episode_steps", "tehm_views", "tehm_physical_effects",
        "tehm_edges",
    )
    before = conn.execute("SELECT COUNT(*) FROM tehm_transitions").fetchone()[0]
    conn.execute("ATTACH DATABASE ? AS training_src", (str(source_db),))
    try:
        for table in tables:
            cols = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
            src_cols = [row[1] for row in conn.execute(
                f"PRAGMA training_src.table_info({table})")]
            if cols != src_cols:
                raise RuntimeError(f"training snapshot schema mismatch: {table}")
            quoted = ",".join('"' + col + '"' for col in cols)
            conn.execute(
                f"INSERT OR IGNORE INTO {table} ({quoted}) "
                f"SELECT {quoted} FROM training_src.{table}")
        conn.commit()
    finally:
        conn.execute("DETACH DATABASE training_src")
    conn.commit()
    return conn.execute("SELECT COUNT(*) FROM tehm_transitions").fetchone()[0] - before


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source-db", type=Path,
                    default=Path("/data1/zhangdy/tehm-evidence-freeze-v2/closed_loop/tehm.sqlite"))
    ap.add_argument("--source-artifacts", type=Path,
                    default=Path("/data1/zhangdy/tehm-evidence-freeze-v2/closed_loop/artifacts"))
    ap.add_argument("--training-db", type=Path,
                    default=Path("/data1/zhangdy/r2g-skills/memory/tehm.sqlite"),
                    help="live append-only physical training store to merge into the trial copy")
    ap.add_argument("--training-artifacts", type=Path,
                    default=Path("/data1/zhangdy/r2g-skills/memory/artifacts"),
                    help="content-addressed blobs for the live physical training store")
    ap.add_argument("--subject", type=Path,
                    default=Path("/data1/zhangdy/tehm-campaigns/orfs-v3-contexts/projects/sky130hd_gcd_base_2"))
    ap.add_argument("--output-root", type=Path,
                    default=default_work_root("orfs-v7-tehm-trial"),
                    help="scratch output root; promote durable receipts/reports explicitly")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--cpus", type=int, default=4)
    args = ap.parse_args(argv)

    root = enforce_work_root(args.output_root)
    root.mkdir(parents=True, exist_ok=True)
    trial_db = root / "tehm.sqlite"
    if trial_db.exists():
        trial_db.unlink()
    shutil.copy2(args.source_db.resolve(), trial_db)
    artifacts = root / "artifacts"
    if artifacts.exists():
        shutil.rmtree(artifacts)
    shutil.copytree(args.source_artifacts.resolve(), artifacts)
    training_artifacts = args.training_artifacts.resolve()
    if training_artifacts.is_dir():
        # Preserve the frozen v2 blobs and add only content-addressed training
        # blobs referenced by the merged physical rows.
        shutil.copytree(training_artifacts, artifacts, dirs_exist_ok=True)
    subject = args.subject.resolve()
    if not (subject / "constraints" / "config.mk").is_file():
        raise FileNotFoundError(f"ORFS subject config missing: {subject}")

    conn = db.connect(trial_db)
    db.ensure_schema(conn)
    merged_transitions = _merge_training_snapshot(conn, args.training_db.resolve())
    rules = crystallize_all(conn)
    # A real trial must exercise an executable, positive signoff rule.  A
    # validated FAIL witness can be useful evidence, but it is not an
    # admissible candidate for a TEHM activation trial and may bind to an
    # empty rewrite on a clean subject.
    candidates = []
    for candidate in rules:
        if (candidate.get("domain") != "flow.signoff" or
                candidate.get("transformation_family") != "DENSITY_RELIEF" or
                candidate.get("validity_status") not in
                ("VALIDATED", "PROVISIONAL_VALID")):
            continue
        after = candidate.get("after_pattern")
        if not isinstance(after, dict):
            try:
                after = json.loads(candidate.get("after_pattern_json") or "{}")
            except (TypeError, ValueError):
                continue
        if after.get("verification.verdict") != "PASS":
            continue
        candidates.append(candidate)
    if not candidates:
        raise RuntimeError("no admissible flow.signoff DENSITY_RELIEF rule")
    rule = sorted(candidates, key=lambda r: r["rule_id"])[0]
    rule_id = rule["rule_id"]
    # Keep the trial snapshot audit-clean: crystallization may emit unrelated
    # rejected/context-dependent flow rules.  They are not part of this
    # activation and must not enter the frozen authority surface merely
    # because the one-click trial materialized them.
    for candidate in rules:
        if (candidate.get("domain") == "flow.signoff" and
                candidate.get("rule_id") != rule_id):
            conn.execute("DELETE FROM tehm_rule_status WHERE rule_id=?",
                         (candidate["rule_id"],))
            conn.execute("DELETE FROM tehm_rule_sources WHERE rule_id=?",
                         (candidate["rule_id"],))
            conn.execute("DELETE FROM tehm_rules WHERE rule_id=?",
                         (candidate["rule_id"],))
    conn.commit()
    enter_shadow(conn, rule_id=rule_id, target_scope="route",
                 provenance={"source": "real_orfs_tehm_trial"})
    set_status(conn, rule_id=rule_id, target_scope="route", status="candidate",
               provenance={"source": "real_orfs_tehm_trial"})
    subject_lineage = "orfs-ab:sky130hd:gcd:base2"
    # The crystallized rule intentionally leaves the target value as $H0.
    # For this controlled subject, bind it to the independently observed
    # CORE_UTILIZATION relief 40 -> 30; the binding is recorded in the
    # activation and is never fed back as learner support.
    provided_bindings = {rule_id: {"$H0": "30"}}
    result = run_pending_orfs_trials(
        conn, ArtifactStore(artifacts),
        base_entries=[{
            "kind": "normal", "project_path": str(subject),
            "platform": "sky130hd", "design": "gcd", "check": "route",
            "lineage_id": subject_lineage,
        }],
        # The repository keeps the executable signoff-loop under the nested
        # ``r2g-skills`` package directory (ROOT.parent is the checkout root).
        run_flow_script=ROOT.parent / "r2g-skills/signoff-loop/scripts/flow/run_orfs.sh",
        fix_signoff_script=ROOT.parent / "r2g-skills/signoff-loop/scripts/flow/fix_signoff.sh",
        n_designs=1, repeats=1, work_root=root / "orfs_ab",
        env={"ORFS_TIMEOUT": str(args.timeout), "ORFS_MAX_CPUS": str(args.cpus)},
        provided_bindings=provided_bindings,
        lifecycle_statuses=frozenset({"candidate"}), mutate_lifecycle=True,
        production_authority=True,
        # This one-click path has no independent conformal/PPA cohort.  Keep
        # the trial evidence, but strict authority must refuse promotion until
        # those gates are supplied by a separate calibration report.
        promotion_gate_inputs={rule_id: {
            "cross_lineage_te": 0.0,
            "harmful_rate": 1.0,
            "conformal_coverage": 0.0,
        }})
    if len(result) != 1:
        raise RuntimeError(f"expected one real ORFS trial, got {len(result)}")
    trial = result[0]
    h10_ok, h10_detail = h10_rollback_authority(conn)
    conn.close()
    receipts = _copy_receipts(root, trial)
    report = {
        "version": "real-orfs-tehm-trial-v0.1",
        "trial": trial,
        "rule_id": rule_id,
        "rule_validity": rule.get("validity_status"),
        "subject": str(subject),
        "subject_lineage": subject_lineage,
        "firewall": {
            "training_lineages_unchanged": True,
            "ab_lineages": [subject_lineage],
            "ab_outcomes_not_learner_support": True,
        },
        "rollback_receipts_copied": receipts,
        "h10": {"ok": h10_ok, "detail": h10_detail},
        "training_snapshot": {
            "source_db": str(args.training_db.resolve()),
            "merged_transition_count": merged_transitions,
        },
    }
    (root / "trial_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "trial_uuid": trial.get("trial_uuid"), "verdict": trial.get("verdict"),
        "rule_id": rule_id, "h10": h10_ok, "h10_detail": h10_detail,
        "rollback_verified": (trial.get("metrics") or {}).get("rollback_verified"),
        "obligation_coverage": (trial.get("metrics") or {}).get("obligation_coverage"),
    }, indent=2, sort_keys=True))
    return 0 if h10_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
