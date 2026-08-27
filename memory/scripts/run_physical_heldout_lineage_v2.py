#!/usr/bin/env python3
"""Materialize and run a second physical held-out lineage without capture.

This campaign uses the previously unseen GF180 ``uart-blocks`` flow.  It is
deliberately separate from the TEHM DB: ORFS receipts, graph contexts and
pair-derived calibration samples are written to the campaign directory only.
The companion calibration step can consume ``calibration_samples.json`` while
preserving the held-out no-mutation firewall.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

MEMORY_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = MEMORY_ROOT.parent
sys.path.insert(0, str(MEMORY_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_orfs_diversity_campaign import (  # noqa: E402
    _latest_successful_final_def, _load, _materialize, _write, run_projects,
)
from tehm.adapters.orfs_pair import build_orfs_pair_record  # noqa: E402
from tehm.physical.effects import extract_deltas  # noqa: E402
from tehm.physical.graph_context import load_defgraph_context  # noqa: E402


VERSION = "orfs-heldout-physical-lineage-v2"
LINEAGE = "orfs-heldout-v5:gf180:uart-blocks"
PLATFORM = "gf180"
DESIGN = "uart"
TEMPLATE = "uart-blocks"
EXPECTED_TIER = "research"
FAMILIES = (
    "DENSITY_RELIEF", "ROUTING_CAPACITY_RECOVERY", "PLACEMENT_DENSITY_RECOVERY",
)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path,
                    default=Path("/data1/zhangdy/tehm-campaigns/orfs-v5-heldout-gf180-uart"))
    ap.add_argument("--orfs-root", type=Path,
                    default=Path(os.environ.get("ORFS_ROOT", "/opt/EDA4AI/OpenROAD-flow-scripts")))
    ap.add_argument("--phase", choices=("prepare", "run", "features", "pairs", "report", "all"),
                    default="all")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--cpus-per-run", type=int, default=6)
    ap.add_argument("--projects", nargs="+", default=None)
    ap.add_argument("--platform", default=PLATFORM)
    ap.add_argument("--design", default=DESIGN)
    ap.add_argument("--template", default=TEMPLATE,
                    help="ORFS design template directory name")
    ap.add_argument("--lineage", default=LINEAGE)
    ap.add_argument("--tier", default=EXPECTED_TIER)
    args = ap.parse_args(argv)
    # Keep the campaign implementation reusable while retaining a frozen,
    # explicit lineage in each generated manifest.
    globals().update({"PLATFORM": args.platform, "DESIGN": args.design,
                      "TEMPLATE": args.template, "LINEAGE": args.lineage,
                      "EXPECTED_TIER": args.tier})
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "campaign_manifest.json"
    manifest = prepare(root, args.orfs_root.resolve()) if args.phase in ("prepare", "all") else _load(manifest_path)
    if not manifest:
        raise RuntimeError(f"manifest missing: {manifest_path}")
    if args.phase == "prepare":
        return 0
    if args.phase in ("run", "all"):
        run_projects(root, manifest, workers=max(1, args.workers),
                     cpus=max(1, args.cpus_per_run), timeout=args.timeout,
                     project_allowlist={str(Path(p).resolve()) for p in args.projects}
                     if args.projects else None)
    if args.phase == "run":
        return 0
    if args.phase in ("features", "all"):
        build_features(root, manifest, timeout=args.timeout)
    if args.phase == "features":
        return 0
    if args.phase in ("pairs", "all"):
        build_pairs(root, manifest)
    if args.phase == "pairs":
        return 0
    report(root, manifest)
    return 0


def prepare(root: Path, orfs_root: Path) -> dict:
    template = orfs_root / "flow" / "designs" / PLATFORM / TEMPLATE
    if not (template / "config.mk").is_file() or not (template / "constraint.sdc").is_file():
        raise FileNotFoundError(f"ORFS template incomplete: {template}")
    items, baselines = [], []
    empty_io = root / "empty_io.tcl"
    empty_io.write_text("# held-out calibration: use ordinary pin placement\n")
    # Keep the relief action above zero (the GF180/sky130 PDN scripts reject a
    # zero core utilization) while retaining three distinct held-out contexts.
    core_utils = (20, 25, 30)
    # Match the successful context campaign's normal routing envelope; the
    # held-out lineage is not a routing-stress probe.
    route_before = ("0.20", "0.25", "0.30")
    route_after = ("0.05", "0.10", "0.15")
    place_after = ("0.50", "0.55", "0.60")
    for index, util in enumerate(core_utils):
        common = {
            "CORE_UTILIZATION": str(util),
            "PLACE_DENSITY_LB_ADDON": "0.25",
            "ROUTING_LAYER_ADJUSTMENT": route_before[index],
        }
        if PLATFORM == "gf180":
            # The installed GF180 IO constraint references an unavailable
            # exclude_io_pin_region path; use ordinary pin placement explicitly.
            common["IO_CONSTRAINTS"] = str(empty_io)
        base = _materialize(root / "projects" / f"{PLATFORM}_{TEMPLATE}_base_{index}",
                            template / "config.mk", template / "constraint.sdc", common)
        baseline_id = f"{PLATFORM}:{TEMPLATE}:base{index}"
        baselines.append({"baseline_id": baseline_id, "platform": PLATFORM,
                          "design": DESIGN, "index": index, "project": str(base),
                          "expected_tier": EXPECTED_TIER})
        actions = (
            ("DENSITY_RELIEF", "CORE_UTILIZATION", str(util - 10)),
            ("ROUTING_CAPACITY_RECOVERY", "ROUTING_LAYER_ADJUSTMENT", route_after[index]),
            ("PLACEMENT_DENSITY_RECOVERY", "PLACE_DENSITY", place_after[index]),
        )
        for family, knob, value in actions:
            after = _materialize(
                root / "projects" / f"{PLATFORM}_{TEMPLATE}_base_{index}_{family.lower()}_after",
                template / "config.mk", template / "constraint.sdc",
                {**common, knob: value})
            items.append({
                "case_id": f"{PLATFORM}:{TEMPLATE}:{index}:{family}",
                "lineage_id": LINEAGE, "platform": PLATFORM, "design": DESIGN,
                "family": family, "check": "route", "config_edits": {knob: value},
                "before_project": str(base), "after_project": str(after),
                "expected_tier": EXPECTED_TIER, "role": "calibration_only",
                "capturable": False, "baseline_id": baseline_id,
            })
    manifest = {
        "version": VERSION, "orfs_root": str(orfs_root), "items": items,
        "baselines": baselines, "families": list(FAMILIES),
        "firewall": {"training_lineages": [], "heldout_lineages": [LINEAGE],
                      "disjoint": True},
        "mutation_policy": "no capture, no record, no crystallization, no lifecycle mutation",
    }
    _write(root / "campaign_manifest.json", manifest)
    return manifest


def build_features(root: Path, manifest: dict, *, timeout: int) -> None:
    features = REPO_ROOT / "r2g-skills/def-graph/scripts/flow/run_features.sh"
    results = []
    for base in manifest["baselines"]:
        project = Path(base["project"])
        final_def = _latest_successful_final_def(project)
        if final_def is None:
            results.append({**base, "status": "missing_successful_def"})
            continue
        log = project / "def_graph_features.log"
        with log.open("w") as out:
            proc = subprocess.run(
                ["bash", str(features), str(project), base["platform"], project.name],
                stdout=out, stderr=subprocess.STDOUT,
                env=dict(os.environ, R2G_SIGNOFF_GATE="warn"), check=False)
        results.append({**base, "def": str(final_def), "features_rc": proc.returncode,
                        "status": "features_complete" if proc.returncode == 0 else "features_failed"})
    _write(root / "features_report.json", {"version": VERSION, "results": results})


def build_pairs(root: Path, manifest: dict) -> None:
    contexts = {}
    feature_results = _load(root / "features_report.json") or {}
    for base in manifest["baselines"]:
        project = Path(base["project"])
        final_def = _latest_successful_final_def(project)
        if final_def is None:
            continue
        try:
            context = load_defgraph_context(project, def_path=final_def)
            contexts[base["baseline_id"]] = context.to_dict()
        except (OSError, ValueError):
            continue
    samples, evidence = [], []
    for item in manifest["items"]:
        context = contexts.get(item["baseline_id"])
        if context is None:
            evidence.append({"case_id": item["case_id"], "status": "missing_context"})
            continue
        try:
            record = build_orfs_pair_record(
                Path(item["before_project"]), Path(item["after_project"]),
                lineage_id=LINEAGE, target_check="route",
                config_edits=item["config_edits"], transformation_family=item["family"])
            observed = extract_deltas(record.before.get("reports", {}).get("ppa") or {},
                                       record.after.get("reports", {}).get("ppa") or {})
        except (OSError, ValueError, RuntimeError) as exc:
            evidence.append({"case_id": item["case_id"], "status": "pair_unavailable",
                             "error": str(exc)})
            continue
        samples.append({"case_id": item["case_id"], "lineage_id": LINEAGE,
                        "platform": PLATFORM, "family": item["family"],
                        "baseline_id": item["baseline_id"],
                        "expected_tier": item["expected_tier"],
                        "graph_context": context, "observed_deltas": observed})
        evidence.append({"case_id": item["case_id"], "status": "evaluatable",
                         "context_digest": context.get("digest"),
                         "observed_deltas": observed})
    _write(root / "calibration_samples.json", {
        "version": VERSION, "lineage_id": LINEAGE, "platform": PLATFORM,
        "samples": samples, "evidence": evidence,
        "mutation": "none",
    })


def report(root: Path, manifest: dict) -> None:
    state = _load(root / "campaign_state.json") or {}
    features = _load(root / "features_report.json") or {}
    samples = _load(root / "calibration_samples.json") or {}
    result = {
        "version": VERSION, "lineage_id": LINEAGE, "platform": PLATFORM,
        "flow_projects": len({x["before_project"] for x in manifest["items"]} |
                              {x["after_project"] for x in manifest["items"]}),
        "latest_run_count": len(state.get("runs", {})),
        "feature_statuses": {x["baseline_id"]: x["status"] for x in features.get("results", [])},
        "evaluatable_pair_count": len(samples.get("samples", [])),
        "firewall": manifest["firewall"],
        "mutation": "none",
    }
    _write(root / "heldout_lineage_report.json", result)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
