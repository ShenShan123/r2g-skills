#!/usr/bin/env python3
"""Bounded campaign: add genuinely-new RTL designs to the training lineage.

Goal (2026-08-02): grow training RTL lineage diversity so the frozen held-out
SPI lineage's nearest graph-context distance falls inside the OOD ceiling (3.0),
then (once passing) add a second frozen held-out lineage for external review.

New training designs (aes / ibex / jpeg — architecturally distinct from the
existing gcd/riscv32i training and the frozen spi held-out) run on the three
platforms where the held-out distances were measured (sky130hs / sky130hd /
ihp-sg13g2). Each (design, platform) materializes a base + a DENSITY_RELIEF
after, producing one verified transition + physical effect with a def-graph
context. Eligible observations land only in a campaign-local staging store;
canonical import is a separate lifecycle-authority operation.

Reuses the diversity-campaign helpers (materialize / run / capture / graph).
Firewall: spi is never a training lineage here.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

MEMORY_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = MEMORY_ROOT.parent
sys.path.insert(0, str(MEMORY_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_orfs_diversity_campaign import (  # noqa: E402
    _load,
    _materialize,
    _write,
    attach_graph_contexts,
    capture_pairs,
    run_projects,
)

from tehm import db as tehm_db  # noqa: E402
from orfs_storage import default_work_root, enforce_work_root, storage_policy  # noqa: E402

VERSION = "orfs-add-designs-v0.1"

# Per-index knob schedules (mirror the v3-contexts campaign's scheme so new
# training contexts are comparable to the strata already in the store).
CORE_UTILS = (30, 35, 40)
ROUTE_AFTER = ("0.05", "0.10", "0.15")
PLACE_AFTER = ("0.50", "0.55", "0.60")

# family -> (knob, after-project slug, before_value_fn(index), after_value_fn(index)).
# The before arm is always the shared base project; the after arm applies the
# family knob on top of it. ROUTING/PLACEMENT leave the base at ORFS defaults,
# so their before_value is the honest label "default" (no knob value is set).
# PLACE_DENSITY is mutually exclusive with PLACE_DENSITY_LB_ADDON (util.tcl
# prefers LB_ADDON), so the PLACEMENT after edit deletes LB_ADDON via None.
FAMILY_SPECS = {
    "DENSITY_RELIEF": ("CORE_UTILIZATION", "density",
                       lambda i: str(CORE_UTILS[i]),
                       lambda i: str(CORE_UTILS[i] - 10)),
    "ROUTING_CAPACITY_RECOVERY": ("ROUTING_LAYER_ADJUSTMENT", "routing",
                                  lambda i: "default",
                                  lambda i: ROUTE_AFTER[i]),
    "PLACEMENT_DENSITY_RECOVERY": ("PLACE_DENSITY", "placement",
                                   lambda i: "default",
                                   lambda i: PLACE_AFTER[i]),
}
_FAMILY_AFTER_EDITS = {
    "DENSITY_RELIEF": lambda knob, value, i: {knob: value},
    "ROUTING_CAPACITY_RECOVERY": lambda knob, value, i: {knob: value},
    "PLACEMENT_DENSITY_RECOVERY": lambda knob, value, i: {
        knob: value, "PLACE_DENSITY_LB_ADDON": None},
}
DEFAULT_FAMILIES = tuple(FAMILY_SPECS)

# New designs genuinely absent from training AND != the frozen spi held-out.
# aes fails ABC on sky130hd; ibex_sv is SystemVerilog (ORFS Yosys rejects it);
# uart/fifo have no per-platform templates so they ride a platform template
# with a VERILOG_FILES override (the same mechanism the held-out SPI uses).
DEFAULT_DESIGNS = ("jpeg", "uart", "fifo")
# Platforms where the held-out SPI nearest distances were measured
# (sky130hs 3.66-4.65, sky130hd 7.4-7.85, ihp-sg13g2 431.7). ihp is skipped in
# the bounded batch: aes/jpeg time out there and the 431 gap is not bridged by
# a handful of contexts.
DEFAULT_PLATFORMS = ("sky130hs", "sky130hd")

# src-only designs -> the platform-template design whose config.mk we reuse.
# RTL comes from flow/designs/src/<design>/*.v (the held-out SPI mechanism).
_SRC_ONLY_DESIGNS = {
    "uart": "gcd",
    "fifo": "gcd",
}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path,
                    default=default_work_root("orfs-v4-add-designs"))
    ap.add_argument("--orfs-root", type=Path,
                    default=Path(os.environ.get("ORFS_ROOT", "/opt/EDA4AI/OpenROAD-flow-scripts")))
    ap.add_argument("--staging-db", type=Path, default=None,
                    help="required isolated destination; defaults below <root>/staging")
    ap.add_argument("--staging-artifacts", type=Path, default=None)
    ap.add_argument("--designs", nargs="+", default=list(DEFAULT_DESIGNS))
    ap.add_argument("--platforms", nargs="+", default=list(DEFAULT_PLATFORMS))
    ap.add_argument("--rtl-override", type=Path, default=None,
                    help="custom RTL file used for every requested design; the design is a new lineage")
    ap.add_argument("--sdc-override", type=Path, default=None,
                    help="SDC template paired with --rtl-override")
    ap.add_argument("--template-design", default="gcd",
                    help="platform design template for --rtl-override (default: gcd)")
    ap.add_argument("--families", nargs="+", default=list(DEFAULT_FAMILIES),
                    choices=list(FAMILY_SPECS))
    ap.add_argument("--indexes", type=int, default=1,
                    help="core-utilization indices per (design, platform); 1 = bounded")
    ap.add_argument("--core-utils", nargs="+", type=int, default=list(CORE_UTILS),
                    help="base CORE_UTILIZATION schedule (for example 20 25 30)")
    ap.add_argument("--lineage-prefix", default="orfs-v4",
                    help="prefix for training lineage IDs in this campaign")
    ap.add_argument("--phase", choices=("all", "prepare", "run", "capture", "graph", "report"),
                    default="all")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--cpus-per-run", type=int, default=6)
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--supervisor-grace", type=int, default=90)
    ap.add_argument("--projects", nargs="+", default=None,
                    help="optional absolute project paths to run")
    args = ap.parse_args(argv)
    if args.rtl_override and not args.sdc_override:
        ap.error("--rtl-override requires --sdc-override so current_design/clock provenance is explicit")
    root = enforce_work_root(args.root)
    root.mkdir(parents=True, exist_ok=True)
    staging_db = (args.staging_db or root / "staging" / "tehm.sqlite").resolve()
    staging_artifacts = (args.staging_artifacts or root / "staging" / "artifacts").resolve()
    manifest_path = root / "campaign_manifest.json"

    if args.phase in ("all", "prepare"):
        manifest = prepare(root, args.orfs_root.resolve(),
                           designs=tuple(args.designs),
                           platforms=tuple(args.platforms),
                           families=tuple(args.families),
                           indexes=args.indexes, core_utils=tuple(args.core_utils),
                           lineage_prefix=args.lineage_prefix,
                           rtl_override_path=(args.rtl_override.resolve()
                                              if args.rtl_override else None),
                           sdc_override_path=(args.sdc_override.resolve()
                                              if args.sdc_override else None),
                           template_design=args.template_design)
    else:
        manifest = _load(manifest_path)
        if not manifest:
            raise RuntimeError(f"manifest missing: {manifest_path}")
    if args.phase == "prepare":
        return 0
    if args.phase in ("all", "run"):
        run_projects(root, manifest, workers=max(1, args.workers),
                     cpus=max(1, args.cpus_per_run), timeout=args.timeout,
                     supervisor_grace=max(1, args.supervisor_grace),
                     project_allowlist={str(Path(p).resolve()) for p in args.projects}
                     if args.projects else None)
    if args.phase == "run":
        return 0
    if args.phase in ("all", "capture"):
        capture_pairs(manifest_path, manifest, staging_db, staging_artifacts)
        manifest = _load(manifest_path)
    if args.phase == "capture":
        return 0
    if args.phase in ("all", "graph"):
        attach_graph_contexts(root, manifest, staging_db)
    if args.phase == "graph":
        return 0
    report(root, manifest, staging_db)
    return 0


def prepare(root: Path, orfs_root: Path, *, designs, platforms,
            families, indexes: int = 1, core_utils=CORE_UTILS,
            lineage_prefix: str = "orfs-v4", rtl_override_path: Path | None = None,
            sdc_override_path: Path | None = None,
            template_design: str = "gcd") -> dict:
    core_utils = tuple(int(x) for x in core_utils)
    if not core_utils:
        raise ValueError("core_utils must contain at least one utilization")
    family_specs = {
        "DENSITY_RELIEF": ("CORE_UTILIZATION", "density",
                           lambda i: str(core_utils[i]),
                           lambda i: str(core_utils[i] - 10)),
        "ROUTING_CAPACITY_RECOVERY": FAMILY_SPECS["ROUTING_CAPACITY_RECOVERY"],
        "PLACEMENT_DENSITY_RECOVERY": FAMILY_SPECS["PLACEMENT_DENSITY_RECOVERY"],
    }
    items, baselines = [], []
    for platform in platforms:
        for design in designs:
            src_template = (template_design if rtl_override_path is not None
                            else _SRC_ONLY_DESIGNS.get(design))
            platform_design = src_template if src_template else design
            template = orfs_root / "flow" / "designs" / platform / platform_design
            cfg = template / "config.mk"
            sdc = (sdc_override_path if sdc_override_path is not None
                   else template / "constraint.sdc")
            if not cfg.is_file() or not sdc.is_file():
                print(f"[prepare] SKIP {platform}/{design}: template incomplete "
                      f"({template})", file=sys.stderr)
                continue
            rtl_override = None
            if rtl_override_path is not None:
                if not rtl_override_path.is_file():
                    raise FileNotFoundError(f"custom RTL override missing: {rtl_override_path}")
                rtl_override = str(rtl_override_path)
            elif src_template:
                src_dir = orfs_root / "flow" / "designs" / "src" / design
                src_rtl = sorted(src_dir.glob("*.v"))
                if not src_rtl:
                    print(f"[prepare] SKIP {platform}/{design}: no src RTL in "
                          f"{src_dir}", file=sys.stderr)
                    continue
                rtl_override = " ".join(str(p.resolve()) for p in src_rtl)
            for index in range(indexes):
                util = core_utils[index % len(core_utils)]
                common = {"CORE_UTILIZATION": str(util),
                          "PLACE_DENSITY_LB_ADDON": "0.25"}
                if rtl_override:
                    common["DESIGN_NAME"] = design
                    common["VERILOG_FILES"] = rtl_override
                    # Source-only designs must retain positive equivalence.
                    # An expensive or unproven EQY result is external evidence,
                    # never a reason to weaken the semantic admission gate.
                    common["EQUIVALENCE_CHECK"] = "1"
                base = _materialize(root / "cases" / f"{platform}_{design}_base_{index}",
                                    cfg, sdc, dict(common))
                baselines.append({"baseline_id": f"{platform}:{design}:base{index}",
                                  "platform": platform, "design": design,
                                  "index": index, "project": str(base)})
                for family in families:
                    knob, slug, before_fn, after_fn = family_specs[family]
                    before_value = before_fn(index)
                    after_value = after_fn(index)
                    after_edits = _FAMILY_AFTER_EDITS[family](knob, after_value, index)
                    after = _materialize(
                        root / "cases" / f"{platform}_{design}_{slug}_{index}",
                        cfg, sdc, {**common, **after_edits})
                    items.append({
                        "case_id": f"{platform}:{design}:{index}:{family}:"
                                   f"{before_value}->{after_value}",
                        "lineage_id": f"{lineage_prefix}:{platform}:{design}:base{index}",
                        "platform": platform, "design": design, "family": family,
                        "check": "route", "knob": knob,
                        "before_value": before_value, "after_value": after_value,
                        "config_edits": {knob: after_value},
                        "before_project": str(base), "after_project": str(after),
                        "role": "training",
                    })

    if not items:
        raise RuntimeError("no materialized items; check ORFS templates")
    heldout = {"lineage_id": "orfs-heldout-v3:ihp-sg13g2:spi",
               "platform": "ihp-sg13g2", "design": "spi",
               "role": "calibration_only", "capturable": False}
    manifest = {
        "campaign_version": VERSION, "lineage_prefix": lineage_prefix,
        "orfs_root": str(orfs_root),
        "items": items, "baselines": baselines, "core_utils": list(core_utils),
        "storage_policy": storage_policy(root),
        "families": list(families), "heldout": heldout,
        "firewall": {
            "training_lineages": sorted({x["lineage_id"] for x in items}),
            "heldout_lineages": [heldout["lineage_id"]],
            "disjoint": heldout["lineage_id"] not in {x["lineage_id"] for x in items},
            # spi must NEVER be a training lineage here.
            "spi_absent_from_training": "spi" not in {x["design"] for x in items},
        },
    }
    _write(root / "campaign_manifest.json", manifest)
    return manifest


def report(root: Path, manifest: dict, db_path: Path) -> dict:
    conn = tehm_db.connect(db_path)
    n_trans = conn.execute("SELECT COUNT(*) FROM tehm_transitions").fetchone()[0]
    n_effects = conn.execute("SELECT COUNT(*) FROM tehm_physical_effects").fetchone()[0]
    unique_ctx = conn.execute(
        "SELECT COUNT(DISTINCT graph_context_digest) FROM tehm_physical_effects "
        "WHERE graph_context_digest IS NOT NULL AND graph_context_digest != ''").fetchone()[0]
    conn.close()
    result = {
        "campaign_version": VERSION,
        "captured": len(manifest.get("captured", [])),
        "canonical_transition_total": n_trans,
        "physical_effect_total": n_effects,
        "unique_graph_contexts": unique_ctx,
        "firewall": manifest["firewall"],
    }
    _write(root / "add_designs_report.json", result)
    print(f"[add-designs] transitions={n_trans} effects={n_effects} "
          f"unique_contexts={unique_ctx}", flush=True)
    return result


if __name__ == "__main__":
    sys.exit(main())
