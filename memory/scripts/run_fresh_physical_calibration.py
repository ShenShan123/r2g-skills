#!/usr/bin/env python3
"""Run a firewall-bound, read-only physical calibration campaign.

The campaign creates fresh RTL lineages, executes only reproducible ORFS work
under ``/tmp``, and emits external calibration samples.  It never opens a TEHM
database for capture or recording.  A small promoted evidence directory keeps
receipts, reports, graph contexts, final DEFs, and hashes while leaving RUN/
logs/results/objects in the scratch root.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

MEMORY_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = MEMORY_ROOT.parent
sys.path.insert(0, str(MEMORY_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_orfs_diversity_campaign import (  # noqa: E402
    _latest_successful_final_def,
    _load,
    _materialize,
    _write,
    run_projects,
)
from tehm.adapters.orfs_pair import build_orfs_pair_record  # noqa: E402
from tehm.physical.effects import extract_deltas  # noqa: E402
from tehm.physical.graph_context import load_defgraph_context  # noqa: E402
from tehm.sync import canonical_json  # noqa: E402
from evaluation.freeze_pointer import resolve_bundle  # noqa: E402
from orfs_storage import enforce_work_root, storage_policy  # noqa: E402


VERSION = "fresh-physical-calibration-v1"
SCRATCH_DEFAULT = Path("/tmp/tehm-p2-fresh-calibration-v10v11")
EVIDENCE_DEFAULT = Path(
    "/data1/zhangdy/tehm-campaigns/tehm-p2-fresh-calibration-v10v11"
)
ORFS_DEFAULT = Path(
    os.environ.get("ORFS_ROOT", "/opt/EDA4AI/OpenROAD-flow-scripts")
)
PLATFORMS = ("sky130hs", "sky130hd")
LINEAGES = (
    {
        "lineage_id": "future-parametric-v10:sky130hs:calibration_logic_v10:base0",
        "design": "future_calibration_logic_v10",
        "fixture": "future_calibration_logic_v10",
        "platform": "sky130hs",
        "base": "34",
        "after": "24",
    },
    {
        "lineage_id": "future-parametric-v10:sky130hd:calibration_logic_v10:base0",
        "design": "future_calibration_logic_v10",
        "fixture": "future_calibration_logic_v10",
        "platform": "sky130hd",
        "base": "34",
        "after": "24",
    },
    {
        "lineage_id": "future-parametric-v11:sky130hs:calibration_logic_v11:base0",
        "design": "future_calibration_logic_v11",
        "fixture": "future_calibration_logic_v11",
        "platform": "sky130hs",
        "base": "36",
        "after": "26",
    },
    {
        "lineage_id": "future-parametric-v11:sky130hd:calibration_logic_v11:base0",
        "design": "future_calibration_logic_v11",
        "fixture": "future_calibration_logic_v11",
        "platform": "sky130hd",
        "base": "36",
        "after": "26",
    },
)


def _sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _collect_lineages(value, key: str = "") -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for name, child in value.items():
            found.update(_collect_lineages(child, str(name)))
    elif isinstance(value, list):
        for child in value:
            found.update(_collect_lineages(child, key))
    elif isinstance(value, str):
        if key in {
            "lineage_id", "training_lineages", "calibration_lineages",
            "heldout_lineages", "ab_lineages", "future_lineages",
            "source_lineages", "additional_physical_heldout_lineages",
        }:
            # Lists are traversed with the same key, so a string is enough.
            if value.strip():
                found.add(value.strip())
    return found


def collect_protected(root: Path) -> tuple[set[str], list[str]]:
    """Collect a conservative, auditable lineage firewall from JSON evidence."""
    # Do not recurse through ORFS RUN trees or every historical report: those
    # directories contain many large generated JSON files.  Campaign manifests
    # and calibration sample bundles are the authoritative lineage sources.
    paths = []
    if root.exists():
        for campaign in sorted(path for path in root.iterdir() if path.is_dir()):
            for pattern in (
                "campaign_manifest.json", "prospective_manifest*.json",
                "*prospective*manifest*.json", "calibration_samples.json",
            ):
                paths.extend(sorted(campaign.glob(pattern)))
            paths.extend(sorted((campaign / "calibration").glob("calibration_samples.json")))
    canonical = resolve_bundle(require_exists=True)
    if canonical.exists():
        paths.extend(sorted((canonical / "evidence" / "physical").glob("*manifest*.json")))
        paths.extend(sorted((canonical / "evidence" / "physical").glob("*readiness*.json")))
    unique = []
    seen = set()
    for path in paths:
        resolved = str(path.resolve())
        if resolved in seen or path.stat().st_size > 4_000_000:
            continue
        seen.add(resolved)
        unique.append(resolved)
    protected = set()
    for name in unique:
        try:
            protected.update(_collect_lineages(json.loads(Path(name).read_text())))
        except (OSError, json.JSONDecodeError):
            continue
    return protected, unique


def prepare(root: Path, orfs_root: Path, protected_root: Path) -> dict:
    protected, sources = collect_protected(protected_root)
    fresh = {item["lineage_id"] for item in LINEAGES}
    overlap = sorted(fresh & protected)
    if overlap:
        raise RuntimeError(f"fresh lineage firewall overlap: {overlap}")
    items = []
    for spec in LINEAGES:
        template = orfs_root / "flow" / "designs" / spec["platform"] / "gcd"
        cfg, template_sdc = template / "config.mk", template / "constraint.sdc"
        fixture = MEMORY_ROOT / "fixtures" / "physical_rtl" / f"{spec['fixture']}.v"
        fixture_sdc = MEMORY_ROOT / "fixtures" / "physical_rtl" / f"{spec['fixture']}.sdc"
        if not cfg.is_file() or not template_sdc.is_file():
            raise FileNotFoundError(f"ORFS template incomplete: {template}")
        if not fixture.is_file() or not fixture_sdc.is_file():
            raise FileNotFoundError(f"fresh fixture incomplete: {fixture}")
        slug = f"{spec['platform']}_{spec['fixture']}_base0"
        common = {
            "DESIGN_NAME": spec["design"],
            "VERILOG_FILES": str(fixture.resolve()),
            "PLACE_DENSITY_LB_ADDON": "0.25",
            "EQUIVALENCE_CHECK": "0",
            "REMOVE_CELLS_FOR_EQY": "",
        }
        before = _materialize(root / "cases" / f"{slug}_before", cfg, fixture_sdc,
                              {**common, "CORE_UTILIZATION": spec["base"]})
        after = _materialize(root / "cases" / f"{slug}_density_after", cfg, fixture_sdc,
                             {**common, "CORE_UTILIZATION": spec["after"]})
        items.append({
            "case_id": f"{spec['lineage_id']}:DENSITY_RELIEF:{spec['base']}->{spec['after']}",
            "lineage_id": spec["lineage_id"], "platform": spec["platform"],
            "design": spec["design"], "family": "DENSITY_RELIEF", "check": "route",
            "knob": "CORE_UTILIZATION", "before_value": spec["base"],
            "after_value": spec["after"], "config_edits": {"CORE_UTILIZATION": spec["after"]},
            "before_project": str(before), "after_project": str(after),
            "role": "calibration_only", "capturable": False,
        })
    manifest = {
        "version": VERSION,
        "orfs_root": str(orfs_root.resolve()),
        "items": items,
        "transition_target": 0,
        "storage_policy": storage_policy(root),
        "firewall": {
            "protected_lineages": sorted(protected),
            "fresh_lineages": sorted(fresh),
            "disjoint": not overlap,
            "source_files": sources,
        },
        "mutation_policy": "no TEHM capture, record, crystallization, lifecycle, or activation",
    }
    _write(root / "campaign_manifest.json", manifest)
    return manifest


def _run_features(project: Path, platform: str) -> int:
    runner = REPO_ROOT / "r2g-skills/def-graph/scripts/flow/run_features.sh"
    log = project / "def_graph_features.log"
    env = dict(os.environ, R2G_SIGNOFF_GATE="warn")
    with log.open("w") as stream:
        proc = __import__("subprocess").run(
            ["bash", str(runner), str(project), platform, project.name],
            stdout=stream, stderr=__import__("subprocess").STDOUT, env=env)
    return proc.returncode


def build_samples(root: Path, manifest: dict) -> dict:
    samples, evidence, contexts = [], [], []
    for item in manifest["items"]:
        before = Path(item["before_project"])
        after = Path(item["after_project"])
        final_def = _latest_successful_final_def(before)
        if final_def is None:
            evidence.append({"case_id": item["case_id"], "status": "missing_successful_def"})
            continue
        feature_rc = _run_features(before, item["platform"])
        try:
            context = load_defgraph_context(before, def_path=final_def).to_dict()
            record = build_orfs_pair_record(
                before, after, lineage_id=item["lineage_id"], target_check=item["check"],
                config_edits=item["config_edits"], transformation_family=item["family"])
        except (OSError, ValueError, RuntimeError) as exc:
            evidence.append({"case_id": item["case_id"], "status": "pair_unavailable",
                             "feature_rc": feature_rc, "error": str(exc)})
            continue
        observed = extract_deltas(
            record.before.get("reports", {}).get("ppa") or {},
            record.after.get("reports", {}).get("ppa") or {})
        sample = {
            "case_id": item["case_id"], "lineage_id": item["lineage_id"],
            "platform": item["platform"], "family": item["family"],
            "expected_tier": context.get("dataset_tier"),
            "graph_context": context, "action": record.action,
            "observed_deltas": observed,
        }
        samples.append(sample)
        contexts.append({"case_id": item["case_id"], "lineage_id": item["lineage_id"],
                         "platform": item["platform"], "context_digest": context.get("digest"),
                         "def": str(final_def), "feature_rc": feature_rc,
                         "def_sha256": _sha(final_def),
                         "features_stats_sha256": _sha(before / "reports" / "features_stats.json")})
        evidence.append({"case_id": item["case_id"], "status": "evaluatable",
                         "context_digest": context.get("digest"),
                         "observed_deltas": observed, "feature_rc": feature_rc})
    result = {
        "version": VERSION,
        "samples": samples,
        "evidence": evidence,
        "source_lineages": sorted({x["lineage_id"] for x in samples}),
        "platforms": sorted({x["platform"] for x in samples}),
        "mutation": "none; external read-only samples only",
    }
    _write(root / "calibration_samples.json", result)
    _write(root / "physical_graph_contexts.json", {"version": VERSION, "contexts": contexts})
    return result


def promote(root: Path, evidence_root: Path, manifest: dict, samples: dict) -> dict:
    evidence_root.mkdir(parents=True, exist_ok=True)
    for name in ("campaign_manifest.json", "campaign_recovery_report.json",
                 "calibration_samples.json", "physical_graph_contexts.json"):
        source = root / name
        if source.is_file():
            shutil.copy2(source, evidence_root / name)
    promoted = []
    for item in manifest["items"]:
        case_dir = evidence_root / "cases" / item["case_id"].replace(":", "_")
        case_dir.mkdir(parents=True, exist_ok=True)
        for side in ("before", "after"):
            project = Path(item[f"{side}_project"])
            final_def = _latest_successful_final_def(project)
            for rel in ("campaign-run-receipt.json", "reports/ppa.json",
                        "reports/route.json", "reports/features_stats.json",
                        "reports/signoff_gate.json"):
                src = project / rel
                if src.is_file():
                    dst = case_dir / f"{side}_{Path(rel).name}"
                    shutil.copy2(src, dst)
            if final_def is not None:
                shutil.copy2(final_def, case_dir / f"{side}_final.def")
                promoted.append({"case_id": item["case_id"], "side": side,
                                 "def": str(case_dir / f"{side}_final.def"),
                                 "def_sha256": _sha(final_def)})
    report = {
        "version": VERSION, "scratch_root": str(root),
        "evidence_root": str(evidence_root),
        "reproducible_scratch": True,
        "promoted_files": promoted,
        "sample_count": len(samples.get("samples", [])),
        "source_lineages": samples.get("source_lineages", []),
        "mutation": "none; no canonical database was opened for writing",
    }
    (evidence_root / "promotion_report.json").write_bytes(canonical_json(report))
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, default=SCRATCH_DEFAULT)
    ap.add_argument("--evidence-root", type=Path, default=EVIDENCE_DEFAULT)
    ap.add_argument("--orfs-root", type=Path, default=ORFS_DEFAULT)
    ap.add_argument("--protected-root", type=Path,
                    default=Path("/data1/zhangdy/tehm-campaigns"))
    ap.add_argument("--phase", choices=("prepare", "run", "samples", "promote", "all"), default="all")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--cpus-per-run", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=1200)
    args = ap.parse_args(argv)
    root = enforce_work_root(args.root.resolve())
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "campaign_manifest.json"
    manifest = prepare(root, args.orfs_root.resolve(), args.protected_root.resolve()) \
        if args.phase in {"prepare", "all"} else _load(manifest_path)
    if not manifest:
        raise RuntimeError(f"campaign manifest missing: {manifest_path}")
    if args.phase == "prepare":
        return 0
    if args.phase in {"run", "all"}:
        run_projects(root, manifest, workers=max(1, args.workers),
                     cpus=max(1, args.cpus_per_run), timeout=max(1, args.timeout))
    if args.phase == "run":
        return 0
    samples = build_samples(root, manifest) if args.phase in {"samples", "all"} \
        else _load(root / "calibration_samples.json")
    if args.phase == "samples":
        return 0
    report = promote(root, args.evidence_root.resolve(), manifest, samples)
    print(json.dumps({"ok": True, "phase": args.phase, "sample_count": report["sample_count"],
                      "scratch_root": str(root), "evidence_root": str(args.evidence_root.resolve())},
                     indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
