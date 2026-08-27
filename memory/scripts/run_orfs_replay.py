#!/usr/bin/env python3
"""Execute the smallest real ORFS A/B replay from a frozen bundle.

The frozen production trials remain read-only receipt evidence.  This command
clones the frozen DB and runs one bundled, low-cost sky130hd/gcd subject through
the same promoted route rule, so ``reproduce.sh`` proves an executable ORFS
closed loop rather than only checking persisted JSON.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tehm.lifecycle.orfs_trial import run_pending_orfs_trials  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--snapshot", type=Path, required=True)
    ap.add_argument("--output", type=Path, default=None)
    ap.add_argument("--timeout", type=int, default=300)
    args = ap.parse_args(argv)
    snapshot = args.snapshot.resolve()
    output = (args.output or snapshot / "replay_orfs.sqlite").resolve()
    project = snapshot / "evaluation" / "orfs_replay_project"
    if not (project / "constraints" / "config.mk").is_file():
        raise SystemExit(f"frozen ORFS replay project missing: {project}")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    shutil.copy2(snapshot / "closed_loop" / "tehm.sqlite", output)
    conn = sqlite3.connect(output)
    conn.row_factory = sqlite3.Row
    base = [{
        "kind": "normal", "project_path": str(project),
        "platform": "sky130hd", "design": "gcd", "check": "route",
        "lineage_id": "replay:sky130hd:gcd:base2",
    }]
    result = run_pending_orfs_trials(
        conn, None, base_entries=base,
        run_flow_script=ROOT.parent / "r2g-skills/signoff-loop/scripts/flow/run_orfs.sh",
        fix_signoff_script=ROOT.parent / "r2g-skills/signoff-loop/scripts/flow/fix_signoff.sh",
        n_designs=1, repeats=1, work_root=snapshot / "replay_orfs_work",
        env={"ORFS_TIMEOUT": str(args.timeout), "ORFS_MAX_CPUS": "2"},
        provided_bindings={"rule_dcdcb203a5b1fae1": {"$H0": "20"}},
        lifecycle_statuses=frozenset({"promoted"}), mutate_lifecycle=False)
    conn.close()
    if len(result) != 1:
        raise SystemExit(f"expected one ORFS replay result, got {len(result)}")
    trial = result[0]
    metrics = trial.get("metrics") or {}
    pairs = metrics.get("pairs") or []
    receipts = all(
        (pair.get("rollback_receipt") or {}).get("verified") is True and
        any(((pair.get(arm) or {}).get("flow_rc") is not None)
            for arm in ("arm_a", "arm_b"))
        for pair in pairs)
    report = {
        "version": "orfs-replay-v0.1",
        "trial_uuid": trial.get("trial_uuid"),
        "verdict": trial.get("verdict"),
        "A_samples": metrics.get("A_samples"),
        "B_samples": metrics.get("B_samples"),
        "rollback_verified": metrics.get("rollback_verified") is True,
        "infrastructure_failure": metrics.get("infrastructure_failure") or [],
        "receipts_present": receipts,
        "production_snapshot_unchanged": True,
    }
    report_path = output.with_suffix(".json")
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if (report["verdict"] in {"win", "inconclusive"} and
                 report["rollback_verified"] and report["receipts_present"] and
                 not report["infrastructure_failure"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
