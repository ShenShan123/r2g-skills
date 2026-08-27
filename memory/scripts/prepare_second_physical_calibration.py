#!/usr/bin/env python3
"""Prepare a post-WP2, read-only physical calibration manifest.

The existing SPI projects are reused as already-produced held-out evidence;
optional additional physical samples are appended as read-only evidence.  This
script does not run ORFS and never records calibration samples.  Training
lineages are read from the expanded physical DB, so an RTL held-out lineage is
not accidentally counted as physical evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source-manifest", type=Path,
                    default=Path("/data1/zhangdy/tehm-campaigns/orfs-v3-heldout-calibration/campaign_manifest.json"))
    ap.add_argument("--db", type=Path,
                    default=Path("/data1/zhangdy/r2g-skills/memory/tehm.sqlite"))
    ap.add_argument("--heldout-manifest", type=Path,
                    default=Path("/data1/zhangdy/tehm-evidence-freeze-v2/evaluation/heldout_task_manifest_v2.json"))
    ap.add_argument("--extra-samples", type=Path, default=None,
                    help="calibration_samples.json from an additional physical held-out lineage")
    ap.add_argument("--output-root", type=Path,
                    default=Path("/data1/zhangdy/tehm-campaigns/orfs-v4-heldout-calibration"))
    args = ap.parse_args(argv)
    manifest = json.loads(args.source_manifest.read_text())
    conn = sqlite3.connect(args.db)
    training = sorted({r[0] for r in conn.execute(
        "SELECT DISTINCT lineage_id FROM tehm_episodes "
        "WHERE domain='flow.signoff' AND lineage_id IS NOT NULL")})
    conn.close()
    heldout = sorted(set((manifest.get("firewall") or {}).get("heldout_lineages") or []))
    additional = []
    extra_digest = None
    if args.extra_samples is not None:
        raw_extra = args.extra_samples.read_bytes()
        extra_digest = hashlib.sha256(raw_extra).hexdigest()
        extra = json.loads(raw_extra)
        if not isinstance(extra.get("samples"), list) or not extra["samples"]:
            raise RuntimeError(f"extra calibration samples are empty or invalid: {args.extra_samples}")
        additional = sorted({str(sample["lineage_id"]) for sample in extra["samples"]})
        heldout = sorted(set(heldout) | set(additional))
    overlap = sorted(set(training) & set(heldout))
    if overlap:
        raise RuntimeError(f"physical training/held-out overlap: {overlap}")
    heldout_eval = json.loads(args.heldout_manifest.read_text())
    manifest["version"] = "orfs-heldout-physical-calibration-v2"
    manifest["firewall"] = {
        "training_lineages": training,
        "heldout_lineages": heldout,
        "disjoint": not overlap,
        "physical_heldout_lineage_count": len(heldout),
        "additional_physical_heldout_lineages": additional,
        "rtl_heldout_lineages_not_counted": [
            x for x in heldout_eval.get("firewall", {}).get("heldout_lineages", [])
            if x not in heldout],
    }
    manifest["post_wp2_source_db"] = str(args.db.resolve())
    manifest["heldout_evaluation_manifest"] = str(args.heldout_manifest.resolve())
    if args.extra_samples is not None:
        manifest["additional_physical_samples_source"] = str(args.extra_samples.resolve())
        manifest["additional_physical_samples_sha256"] = extra_digest
    manifest["mutation_policy"] = "no capture, no record, no crystallization, no lifecycle mutation"
    args.output_root.mkdir(parents=True, exist_ok=True)
    out = args.output_root / "campaign_manifest.json"
    out.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"manifest": str(out), "training_lineage_count": len(training),
                      "physical_heldout_lineages": heldout,
                      "rtl_heldout_not_counted": manifest["firewall"]["rtl_heldout_lineages_not_counted"],
                      "disjoint": manifest["firewall"]["disjoint"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
