#!/usr/bin/env python3
"""Create a small, hash-addressed TEHM evidence freeze bundle.

The bundle deliberately contains the executable snapshot and JSON/log evidence,
not regenerated ORFS build trees.  ``reproduce.sh`` checks the frozen database,
artifact digests, RTL campaign, and the source/report hashes recorded here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import subprocess
from pathlib import Path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def copy_file(src: Path, dst: Path) -> dict:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.resolve() != dst.resolve():
        shutil.copy2(src, dst)
    return {"path": str(dst), "sha256": sha256(dst), "bytes": dst.stat().st_size}


def counts(db_path: Path) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    tables = (
        "tehm_states", "tehm_transitions", "tehm_episodes", "tehm_views",
        "tehm_rules", "tehm_rule_status", "tehm_activations", "tehm_trials",
        "tehm_physical_effects",
    )
    result = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in tables}
    result["lifecycle"] = [dict(r) for r in conn.execute(
        "SELECT rule_id,target_scope,status,status_version "
        "FROM tehm_rule_status ORDER BY rule_id,target_scope")]
    result["trials"] = [dict(r) for r in conn.execute(
        "SELECT trial_uuid,rule_id,target_scope,verdict,status_version "
        "FROM tehm_trials ORDER BY trial_uuid")]
    conn.close()
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", type=Path,
                    default=Path("/data1/zhangdy/tehm-campaigns/tehm-evidence-freeze-v1"))
    ap.add_argument("--db", type=Path,
                    default=Path("/data1/zhangdy/tehm-campaigns/tehm-v1-closed-loop/tehm.sqlite"))
    ap.add_argument("--artifacts", type=Path,
                    default=Path("/data1/zhangdy/tehm-campaigns/tehm-v1-closed-loop/artifacts"))
    args = ap.parse_args(argv)
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    # SQLite may keep the logical snapshot in a WAL while the main image is
    # tiny.  Checkpoint before copying so the bundle is self-contained and
    # never depends on an unbundled ``-wal`` sidecar.
    source_conn = sqlite3.connect(args.db.resolve())
    source_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    source_conn.close()
    frozen_db = out / "closed_loop" / "tehm.sqlite"
    db_entry = copy_file(args.db.resolve(), frozen_db)
    artifact_dst = out / "closed_loop" / "artifacts"
    if artifact_dst.exists():
        shutil.rmtree(artifact_dst)
    shutil.copytree(args.artifacts.resolve(), artifact_dst)
    artifacts = []
    for path in sorted(p for p in artifact_dst.rglob("*") if p.is_file()):
        artifacts.append({"path": str(path.relative_to(out)),
                          "sha256": sha256(path), "bytes": path.stat().st_size})

    evidence = [
        (Path("/data1/zhangdy/tehm-campaigns/tehm-v1-closed-loop/rtl_campaign.log"),
         "closed_loop/rtl_campaign.log"),
        (Path("/data1/zhangdy/tehm-campaigns/tehm-v1-closed-loop/orfs_capture.log"),
         "closed_loop/orfs_capture.log"),
        (Path("/data1/zhangdy/tehm-campaigns/orfs-v2-diversity/ab_result.json"),
         "orfs_history/orfs-v2-ab_result.json"),
        (Path("/data1/zhangdy/tehm-campaigns/orfs-v3-heldout-calibration/calibration_report.json"),
         "orfs_history/orfs-v3-calibration_report.json"),
        (Path("/data1/zhangdy/tehm-campaigns/orfs-v3-heldout-calibration/parametric_readiness.json"),
         "orfs_history/orfs-v3-parametric_readiness.json"),
        (Path("/data1/zhangdy/tehm-campaigns/orfs-v4-add-designs/campaign_manifest.json"),
         "orfs_history/orfs-v4-campaign_manifest.json"),
        (Path("/data1/zhangdy/tehm-campaigns/orfs-v4-add-designs/campaign_state.json"),
         "orfs_history/orfs-v4-campaign_state.json"),
        (Path("/data1/zhangdy/tehm-campaigns/orfs-v4-add-designs/sky130hs_batch_run.log"),
         "orfs_history/orfs-v4-sky130hs_batch_run.log"),
        (Path("/data1/zhangdy/tehm-evidence-freeze-v1/m0_m1_m8_report.json"),
         "evaluation/m0_m1_m8_report.json"),
        (Path("/data1/zhangdy/tehm-evidence-freeze-v1/m0_m1_m8_report.md"),
         "evaluation/m0_m1_m8_report.md"),
        (Path("/data1/zhangdy/tehm-evidence-freeze-v1/evaluation/heldout_task_manifest.json"),
         "evaluation/heldout_task_manifest.json"),
        (Path("/data1/zhangdy/tehm-evidence-freeze-v1/evaluation/task_selection.json"),
         "evaluation/task_selection.json"),
        (Path("/data1/zhangdy/tehm-evidence-freeze-v1/evaluation/orfs_replay_project/constraints/config.mk"),
         "evaluation/orfs_replay_project/constraints/config.mk"),
        (Path("/data1/zhangdy/tehm-evidence-freeze-v1/evaluation/orfs_replay_project/constraints/constraint.sdc"),
         "evaluation/orfs_replay_project/constraints/constraint.sdc"),
        (Path("/data1/zhangdy/tehm-evidence-freeze-v1/evaluation/orfs_snapshot_verification.json"),
         "evaluation/orfs_snapshot_verification.json"),
        (Path("/data1/zhangdy/r2g-skills/memory/tests/fixtures/rtl_projects/req_ack_bug3/manifest.json"),
         "evaluation/task/manifest.json"),
        (Path("/data1/zhangdy/r2g-skills/memory/tests/fixtures/rtl_projects/req_ack_bug3/rtl/req_ack_fsm.v"),
         "evaluation/task/rtl/req_ack_fsm.v"),
        (Path("/data1/zhangdy/r2g-skills/memory/tests/fixtures/rtl_projects/req_ack_bug3/tb/tb_handshake.v"),
         "evaluation/task/tb/tb_handshake.v"),
        (Path("/data1/zhangdy/r2g-skills/memory/tests/fixtures/rtl_projects/req_ack_bug3/tb/tb_basic.v"),
         "evaluation/task/tb/tb_basic.v"),
    ]
    evidence_entries = []
    for src, rel in evidence:
        if src.is_file():
            evidence_entries.append(copy_file(src, out / rel))

    # Preserve the small, decisive receipts for every winning ORFS trial.  The
    # full PnR trees remain outside the hash-addressed bundle, but run-meta and
    # stage-log receipts are cheap to carry and make the A/B evidence auditable
    # without relying on an unlisted external directory.
    receipt_conn = sqlite3.connect(frozen_db)
    receipt_conn.row_factory = sqlite3.Row
    for trial in receipt_conn.execute(
            "SELECT trial_uuid,metrics_json FROM tehm_trials "
            "WHERE target_scope='route' AND verdict IN ('win','inconclusive') "
            "ORDER BY trial_uuid"):
        metrics = json.loads(trial["metrics_json"])
        for pair in metrics.get("pairs") or []:
            sandbox = Path((pair.get("rollback_receipt") or {}).get("sandbox_root", ""))
            for arm in ("arm_a", "arm_b"):
                arm_root = sandbox / arm / "backend"
                for name in ("run-meta.json", "stage_log.jsonl"):
                    candidates = sorted(arm_root.glob(f"RUN_*/{name}"))
                    if not candidates:
                        continue
                    src = candidates[-1]
                    rel = Path("orfs_receipts") / trial["trial_uuid"] / f"repeat_{pair.get('repeat', 0)}" / arm / name
                    evidence_entries.append(copy_file(src, out / rel))
    receipt_conn.close()

    source_paths = [
        Path("memory/scripts/freeze_evidence.py"),
        Path("memory/scripts/run_rtl_campaign.py"),
        Path("memory/scripts/run_orfs_diversity_campaign.py"),
        Path("memory/tehm/activation/pipeline.py"),
        Path("memory/tehm/crystallization/build_rules.py"),
        Path("memory/tehm/lifecycle/orfs_trial.py"),
        Path("memory/tehm/lifecycle/authority.py"),
        Path("memory/scripts/run_controlled_m0_m1_m8.py"),
        Path("memory/scripts/verify_orfs_snapshot.py"),
        Path("memory/scripts/run_orfs_replay.py"),
    ]
    source_entries = []
    repo = Path(__file__).resolve().parents[2]
    for rel in source_paths:
        src = repo / rel
        source_entries.append({"path": str(rel), "sha256": sha256(src),
                               "bytes": src.stat().st_size})

    reproduce = out / "reproduce.sh"
    reproduce.write_text("""#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd \"$(dirname \"$0\")\" && pwd)
REPO=/data1/zhangdy/r2g-skills
python3 -m pytest \"$REPO/memory/tests\" -q
python3 \"$REPO/memory/tehm/cli.py\" --db \"$ROOT/closed_loop/tehm.sqlite\" --artifacts \"$ROOT/closed_loop/artifacts\" health
python3 \"$REPO/memory/tehm/cli.py\" --db \"$ROOT/closed_loop/tehm.sqlite\" --artifacts \"$ROOT/closed_loop/artifacts\" honesty
python3 \"$REPO/memory/scripts/run_rtl_campaign.py\" --db \"$ROOT/replay_rtl.sqlite\" --artifacts \"$ROOT/replay_rtl_artifacts\"
python3 \"$REPO/memory/scripts/run_controlled_m0_m1_m8.py\" --snapshot \"$ROOT\" --output \"$ROOT/replay_m0_m1_m8_report.json\"
python3 \"$REPO/memory/scripts/verify_orfs_snapshot.py\" --db \"$ROOT/closed_loop/tehm.sqlite\" --output \"$ROOT/replay_orfs_snapshot_verification.json\"
python3 \"$REPO/memory/scripts/run_orfs_replay.py\" --snapshot \"$ROOT\" --output \"$ROOT/replay_orfs.sqlite\"
""")
    reproduce.chmod(0o755)

    manifest = {
        "bundle_version": "tehm-evidence-freeze-v1",
        "source_repo": str(repo),
        "source_head": subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip(),
        "snapshot": {"database": db_entry, "counts": counts(frozen_db),
                      "artifacts": artifacts},
        "evidence_files": evidence_entries,
        "source_files": source_entries,
        "reproduce_command": "./reproduce.sh",
        "claims": {
            "rtl_closed_loop": True,
            "orfs_trial_in_snapshot": counts(frozen_db)["tehm_trials"] >= 2,
            "legacy_authority_in_bundle": False,
            "v4_add_designs_report_present": any(
                e["path"].endswith("orfs-v4-add_designs_report.json")
                for e in evidence_entries),
            "orfs_receipts_hashed": any(
                str(Path(e["path"]).resolve()).startswith(
                    str((out / "orfs_receipts").resolve()))
                for e in evidence_entries),
        },
    }
    (out / "bundle_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"bundle": str(out), "counts": manifest["snapshot"]["counts"],
                      "manifest": str(out / "bundle_manifest.json")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
