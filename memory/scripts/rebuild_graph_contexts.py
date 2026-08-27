#!/usr/bin/env python3
"""Rebuild def-graph contexts onto a rebuilt canonical TEHM store.

After the canonical store was rebuilt from preserved campaign evidence, the
graph-context attachments are restored from the PRESERVED def-graph feature
outputs (features/ + reports/features_stats.json + reports/signoff_gate.json) —
no re-extraction, no re-run of the ORFS flow.

Uses the campaign manifests' captured transition IDs (content-addressed, so the
rebuilt IDs match what the audits referenced).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

MEMORY_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = MEMORY_ROOT.parent
sys.path.insert(0, str(MEMORY_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_orfs_diversity_campaign import _latest_successful_final_def, _load  # noqa: E402
from tehm import db as tehm_db  # noqa: E402
from tehm.physical.graph_context import load_defgraph_context  # noqa: E402
from tehm.physical.memory import PhysicalEffectMemory  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path,
                    default=MEMORY_ROOT / "tehm.sqlite")
    ap.add_argument("--campaigns", nargs="+",
                    default=["/data1/zhangdy/tehm-campaigns/orfs-v3-contexts",
                             "/data1/zhangdy/tehm-campaigns/orfs-v2-diversity"])
    args = ap.parse_args(argv)

    conn = tehm_db.connect(args.db)
    tehm_db.ensure_schema(conn)
    physical = PhysicalEffectMemory(conn)
    total_attached, total_unique = 0, set()
    for campaign in args.campaigns:
        root = Path(campaign)
        manifest = _load(root / "campaign_manifest.json")
        captured = {row["case_id"]: row for row in manifest.get("captured", [])}
        # v2 manifest items carry before_project; v3 items carry before_project
        # + baseline linkage. Rebuild a per-baseline project map from baselines.
        baseline_projects = {}
        for base in manifest.get("baselines", []):
            baseline_projects[base["baseline_id"]] = Path(base["project"])
        attached = 0
        for item in manifest.get("items", []):
            row = captured.get(item["case_id"])
            if not row:
                continue
            project = Path(item["before_project"])
            final_def = _latest_successful_final_def(project)
            if final_def is None:
                continue
            try:
                context = load_defgraph_context(project, def_path=final_def)
                digest = physical.attach_graph_context(
                    row["transition_id"], context, replace=True)
                total_unique.add(digest)
                attached += 1
            except (OSError, ValueError) as exc:
                print(f"[rebuild-graph] {campaign}:{item['case_id']}: {exc}",
                      file=sys.stderr)
        print(f"[rebuild-graph] {Path(campaign).name}: attached {attached} "
              f"graph contexts")
        total_attached += attached
    conn.close()
    print(f"[rebuild-graph] total attached={total_attached} "
          f"unique_contexts={len(total_unique)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
