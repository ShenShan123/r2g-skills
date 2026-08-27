#!/usr/bin/env python3
"""Calibrate physical-graph retrieval on H9-firewalled physical lineages.

The same unseen RTL lineage is realized independently on each training
platform.  Its observations are used only to fit retrieval gates; this script
never calls canonical capture, ``PhysicalEffectMemory.record`` or lifecycle
mutation.  Optional ``--extra-samples`` are precomputed, read-only samples
from an independent physical lineage and are merged into the calibration set
without touching TEHM.
"""
from __future__ import annotations

import argparse
import json
import os
import re
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
from tehm import db as tehm_db  # noqa: E402
from tehm.adapters.orfs_pair import build_orfs_pair_record  # noqa: E402
from tehm.physical.calibration import calibrate_retrieval  # noqa: E402
from tehm.physical.effects import extract_deltas  # noqa: E402
from tehm.physical.graph_context import load_defgraph_context  # noqa: E402
from tehm.physical.memory import PhysicalEffectMemory  # noqa: E402


VERSION = "orfs-heldout-physical-calibration-v0.2"
LINEAGE = "orfs-heldout-v3:spi"
FAMILIES = (
    "DENSITY_RELIEF",
    "ROUTING_CAPACITY_RECOVERY",
    "PLACEMENT_DENSITY_RECOVERY",
)
PLATFORMS = {
    "sky130hd": {"tier": "strict_clean"},
    "sky130hs": {"tier": "research"},
    "ihp-sg13g2": {"tier": "research"},
}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path,
                    default=Path("/data1/zhangdy/tehm-campaigns/orfs-v3-heldout-calibration"))
    ap.add_argument("--training-manifest", type=Path,
                    default=Path("/data1/zhangdy/tehm-campaigns/orfs-v3-contexts/campaign_manifest.json"))
    ap.add_argument("--extra-training-manifest", type=Path, action="append", default=[],
                    help="additional disjoint training campaign manifest; may be repeated")
    ap.add_argument("--orfs-root", type=Path,
                    default=Path(os.environ.get("ORFS_ROOT", "/opt/EDA4AI/OpenROAD-flow-scripts")))
    ap.add_argument("--db", type=Path,
                    default=Path("/data1/zhangdy/tehm-campaigns/orfs-v1/tehm.sqlite"))
    ap.add_argument("--extra-samples", type=Path, default=None,
                    help="precomputed read-only calibration_samples.json from an additional physical lineage")
    ap.add_argument("--phase", choices=("all", "prepare", "run", "features", "calibrate"),
                    default="all")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--cpus-per-run", type=int, default=6)
    ap.add_argument("--timeout", type=int, default=900)
    args = ap.parse_args(argv)
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "campaign_manifest.json"
    manifest = (prepare(root, args.orfs_root.resolve(), args.training_manifest.resolve(),
                        extra_training_manifests=tuple(p.resolve() for p in args.extra_training_manifest))
                if args.phase in ("all", "prepare") else _load(manifest_path))
    if not manifest:
        raise RuntimeError(f"manifest missing: {manifest_path}")
    if args.phase == "prepare":
        return 0
    if args.phase in ("all", "run"):
        run_projects(root, manifest, workers=max(1, args.workers),
                     cpus=max(1, args.cpus_per_run), timeout=args.timeout)
    if args.phase == "run":
        return 0
    if args.phase in ("all", "features"):
        build_contexts(root, manifest, timeout=args.timeout)
    if args.phase == "features":
        return 0
    extra_samples = None
    if args.extra_samples is not None:
        extra_samples = _load(args.extra_samples.resolve())
        if not extra_samples or not isinstance(extra_samples.get("samples"), list):
            raise RuntimeError(f"invalid extra calibration samples: {args.extra_samples}")
    report = calibrate(root, manifest, args.db.resolve(), extra_samples=extra_samples)
    return 0 if report["all_policies_ready"] else 2


def prepare(root: Path, orfs_root: Path, training_manifest_path: Path,
            *, extra_training_manifests=()) -> dict:
    manifests = [training_manifest_path, *extra_training_manifests]
    loaded = []
    for path in manifests:
        manifest = _load(path)
        if not manifest:
            raise RuntimeError(f"training manifest missing: {path}")
        loaded.append(manifest)
    training_lineages = sorted(set(
        lineage
        for manifest in loaded
        for lineage in ((manifest.get("firewall") or {}).get("training_lineages") or [])))
    if LINEAGE in training_lineages:
        raise RuntimeError(f"held-out lineage leaked into training: {LINEAGE}")

    spi_rtl = orfs_root / "flow" / "designs" / "src" / "spi" / "spi.v"
    spi_sdc = orfs_root / "flow" / "designs" / "ihp-sg13g2" / "spi" / "constraint.sdc"
    items, baselines = [], []
    for platform, spec in PLATFORMS.items():
        config = orfs_root / "flow" / "designs" / platform / "gcd" / "config.mk"
        for index, util in enumerate((20, 25, 30)):
            common = {
                "DESIGN_NAME": "spi", "VERILOG_FILES": str(spi_rtl),
                "CORE_UTILIZATION": str(util), "PLACE_DENSITY_LB_ADDON": "0.25",
                "ROUTING_LAYER_ADJUSTMENT": ("0.15", "0.20", "0.25")[index],
                "EQUIVALENCE_CHECK": "0", "REMOVE_CELLS_FOR_EQY": "",
            }
            before = _project(
                root / "projects" / f"{platform}_spi_base_{index}",
                config, spi_sdc, common)
            baseline_id = f"{platform}:spi:base{index}"
            baselines.append({
                "baseline_id": baseline_id, "platform": platform,
                "index": index, "project": str(before),
                "expected_tier": spec["tier"],
            })
            actions = (
                ("DENSITY_RELIEF", "CORE_UTILIZATION", str(util - 10)),
                ("ROUTING_CAPACITY_RECOVERY", "ROUTING_LAYER_ADJUSTMENT",
                 ("0.05", "0.10", "0.15")[index]),
                ("PLACEMENT_DENSITY_RECOVERY", "PLACE_DENSITY",
                 ("0.50", "0.55", "0.60")[index]),
            )
            for family, knob, value in actions:
                after = _project(
                    root / "projects" / f"{platform}_spi_base_{index}_{family.lower()}_after",
                    config, spi_sdc, {**common, knob: value})
                items.append({
                    "case_id": f"{platform}:spi:{index}:{family}",
                    "lineage_id": LINEAGE, "platform": platform,
                    "design": "spi", "family": family, "check": "route",
                    "config_edits": {knob: value},
                    "before_project": str(before), "after_project": str(after),
                    "expected_tier": spec["tier"], "role": "calibration_only",
                    "capturable": False, "baseline_id": baseline_id,
                })
    manifest = {
        "version": VERSION, "items": items, "baselines": baselines,
        "families": list(FAMILIES),
        "firewall": {
            "training_lineages": training_lineages,
            "heldout_lineages": [LINEAGE],
            "disjoint": LINEAGE not in training_lineages,
        },
        "mutation_policy": "no capture, no record, no crystallization, no lifecycle mutation",
        "training_manifest_sources": [str(path) for path in manifests],
    }
    _write(root / "campaign_manifest.json", manifest)
    return manifest


def _project(path: Path, config: Path, sdc: Path, edits: dict[str, str]) -> Path:
    project = _materialize(path, config, sdc, edits)
    target = project / "constraints" / "constraint.sdc"
    text, count = re.subn(
        r"(?m)^(\s*set\s+clk_period\s+).*$", r"\g<1>5.0", target.read_text())
    if count != 1:
        raise RuntimeError(f"cannot stamp held-out clock in {target}")
    target.write_text(text)
    return project


def build_contexts(root: Path, manifest: dict, *, timeout: int) -> None:
    strict = REPO_ROOT / "r2g-skills/signoff-loop/scripts/flow/run_strict_signoff.sh"
    features = REPO_ROOT / "r2g-skills/def-graph/scripts/flow/run_features.sh"
    results = []
    for base in manifest["baselines"]:
        project = Path(base["project"])
        final_def = _latest_successful_final_def(project)
        if final_def is None:
            results.append({**base, "status": "missing_successful_def"})
            continue
        signoff_rc = None
        if base["expected_tier"] == "strict_clean":
            with (project / "strict_signoff.log").open("w") as out:
                proc = subprocess.run(
                    ["bash", str(strict), str(project), base["platform"], project.name],
                    stdout=out, stderr=subprocess.STDOUT,
                    env=dict(os.environ, NETGEN_TIMEOUT=str(timeout)))
            signoff_rc = proc.returncode
            gate = "strict"
        else:
            gate = "warn"
        with (project / "def_graph_features.log").open("w") as out:
            proc = subprocess.run(
                ["bash", str(features), str(project), base["platform"], project.name],
                stdout=out, stderr=subprocess.STDOUT,
                env=dict(os.environ, R2G_SIGNOFF_GATE=gate))
        results.append({
            **base, "def": str(final_def), "signoff_rc": signoff_rc,
            "features_rc": proc.returncode,
            "status": "features_complete" if proc.returncode == 0 else "features_failed",
        })
    _write(root / "features_report.json", {"version": VERSION, "results": results})


def calibrate(root: Path, manifest: dict, db_path: Path,
              *, extra_samples: dict | None = None) -> dict:
    if not (manifest.get("firewall") or {}).get("disjoint"):
        raise RuntimeError("H9 training/held-out firewall is not disjoint")
    contexts = {}
    for base in manifest["baselines"]:
        project = Path(base["project"])
        final_def = _latest_successful_final_def(project)
        if final_def is None:
            continue
        try:
            contexts[base["baseline_id"]] = load_defgraph_context(
                project, def_path=final_def).to_dict()
        except (OSError, ValueError):
            continue

    conn = tehm_db.connect(db_path)
    tehm_db.ensure_schema(conn)
    memory = PhysicalEffectMemory(conn)
    count_before = memory.count()
    samples = {}
    evidence = []
    for item in manifest["items"]:
        context = contexts.get(item["baseline_id"])
        if context is None:
            evidence.append({"case_id": item["case_id"], "status": "missing_context"})
            continue
        try:
            record = build_orfs_pair_record(
                Path(item["before_project"]), Path(item["after_project"]),
                lineage_id=LINEAGE, target_check="route",
                config_edits=item["config_edits"],
                transformation_family=item["family"])
        except (OSError, ValueError, RuntimeError) as exc:
            evidence.append({
                "case_id": item["case_id"], "status": "pair_unavailable",
                "error": str(exc),
            })
            continue
        observed = extract_deltas(
            record.before.get("reports", {}).get("ppa") or {},
            record.after.get("reports", {}).get("ppa") or {})
        key = (item["platform"], item["family"], item["expected_tier"])
        samples.setdefault(key, []).append({
            "lineage_id": LINEAGE, "family": item["family"],
            "graph_context": context, "observed_deltas": observed,
        })
        evidence.append({
            "case_id": item["case_id"], "status": "evaluatable",
            "context_digest": context.get("digest"), "observed_deltas": observed,
        })

    # Additional physical lineages are supplied as already-evaluated, read-only
    # samples.  They are deliberately not rebuilt from projects here: doing so
    # could accidentally turn calibration into a capture path.  Keep their
    # lineage IDs in the same firewall as the primary manifest.
    extra_lineages = set()
    if extra_samples:
        for index, sample in enumerate(extra_samples.get("samples", [])):
            try:
                lineage_id = str(sample["lineage_id"])
                platform = str(sample["platform"])
                family = str(sample["family"])
                context = dict(sample["graph_context"])
                observed = dict(sample["observed_deltas"])
                tier = str(sample.get("expected_tier") or
                           context.get("dataset_tier") or
                           PLATFORMS[platform]["tier"])
            except (KeyError, TypeError, ValueError) as exc:
                raise RuntimeError(
                    f"invalid extra calibration sample at index {index}: {exc}") from exc
            if platform not in PLATFORMS:
                raise RuntimeError(f"extra sample platform is not calibratable: {platform}")
            if family not in FAMILIES:
                raise RuntimeError(f"extra sample family is not calibratable: {family}")
            extra_lineages.add(lineage_id)
            key = (platform, family, tier)
            samples.setdefault(key, []).append({
                "lineage_id": lineage_id, "family": family,
                "graph_context": context, "observed_deltas": observed,
            })
            evidence.append({
                "case_id": sample.get("case_id", f"extra:{index}"),
                "status": "evaluatable",
                "source": "external_read_only_calibration_samples",
                "lineage_id": lineage_id,
                "context_digest": context.get("digest"),
                "observed_deltas": observed,
            })

    training_lineages = set(manifest["firewall"]["training_lineages"])
    overlap = sorted(training_lineages & extra_lineages)
    if overlap:
        raise RuntimeError(f"extra physical held-out lineage leaked into training: {overlap}")

    policies = {}
    gate_audit = {}
    for platform in PLATFORMS:
        tier = PLATFORMS[platform]["tier"]
        for family in FAMILIES:
            key = (platform, family, tier)
            policy_key = "|".join(key)
            policies[policy_key] = calibrate_retrieval(
                memory, family=family, heldout_samples=samples.get(key, []),
                training_lineages=manifest["firewall"]["training_lineages"],
                min_samples=3, min_unique_contexts=3, target_coverage=0.80)
            queries = samples.get(key, [])
            if queries:
                gated = memory.predict(
                    family=family, graph_context=queries[0]["graph_context"],
                    calibration_policy=policies[policy_key])
                gate_audit[policy_key] = {
                    "abstained": gated.get("abstained"),
                    "abstain_reasons": gated.get("abstain_reasons"),
                    "calibration_status": gated.get("calibration_status"),
                }
    count_after = memory.count()
    conn.close()
    if count_after != count_before:
        raise RuntimeError("held-out calibration mutated physical memory")

    all_ready = bool(policies) and all(
        policy["status"] == "ready" for policy in policies.values())
    heldout_lineages = set(manifest["firewall"]["heldout_lineages"]) | extra_lineages
    firewall = dict(manifest["firewall"])
    firewall["heldout_lineages"] = sorted(heldout_lineages)
    firewall["physical_heldout_lineage_count"] = len(heldout_lineages)
    firewall["additional_physical_heldout_lineages"] = sorted(extra_lineages)
    firewall["disjoint"] = not bool(set(firewall["training_lineages"]) & heldout_lineages)
    policy_failures = {key: value["status"] for key, value in policies.items()
                       if value["status"] != "ready"}
    def _distance_max(value):
        observed = value.get("calibration", {}).get("observed_distance_range")
        # Empty/partial ranges mean that no valid held-out geometry was
        # observed.  Treat that as an evidence failure, never as a passing
        # distance gate or an indexing error.
        if not isinstance(observed, (list, tuple)) or len(observed) < 2:
            return float("inf")
        return observed[1]

    distance_gate = bool(policies) and all(
        _distance_max(value)
        <= value.get("thresholds", {}).get("distance_ceiling", float("inf"))
        for value in policies.values())
    coverage_gate = bool(policies) and all(
        value.get("calibration", {}).get("empirical_coverage", 0.0)
        >= value.get("thresholds", {}).get("required_coverage", 1.0)
        for value in policies.values())
    uncertainty_gate = bool(policies) and all(
        all(metric.get("max_interval_width", float("inf"))
            <= value.get("thresholds", {}).get("max_uncertainty_widths", {}).get(name, float("inf"))
            for name, metric in (value.get("calibration", {}).get("per_metric", {}) or {}).items())
        for value in policies.values())
    lineage_diversity = len(heldout_lineages) >= 2
    # Parametric implementation is downstream of every explicit evidence gate;
    # do not infer readiness from policy labels alone.
    parametric_ready = (all_ready and distance_gate and coverage_gate and
                        uncertainty_gate and lineage_diversity)
    failure_detail = "; ".join(f"{key}={status}" for key, status in sorted(policy_failures.items()))
    readiness = {
        "status": "READY_FOR_IMPLEMENTATION" if parametric_ready
                  else "DEFERRED_INSUFFICIENT_EVIDENCE",
        "parametric_view_status": "NOT_IMPLEMENTED",
        "criteria": {
            "all_retrieval_policies_ready": all_ready,
            "distance_gate_satisfied": distance_gate,
            "coverage_gate_satisfied": coverage_gate,
            "uncertainty_gate_satisfied": uncertainty_gate,
            "minimum_independent_heldout_lineages": 2,
            "observed_independent_heldout_lineages": len(heldout_lineages),
            "lineage_diversity_satisfied": lineage_diversity,
        },
        "physical_heldout_lineages": sorted(heldout_lineages),
        "failed_policy_gates": policy_failures,
        "reason": ("retrieval calibration and held-out lineage diversity are sufficient"
                   if parametric_ready else
                   f"physical held-out lineage diversity={len(heldout_lineages)}/2; "
                   f"policy gates remain fail-closed ({failure_detail or 'no evaluable policies'}); "
                   "Parametric View remains NOT_IMPLEMENTED"),
    }
    report = {
        "version": VERSION, "all_policies_ready": all_ready,
        "policies": policies, "evidence": evidence,
        "prediction_gate_audit": gate_audit,
        "firewall": firewall,
        "physical_memory_count_before": count_before,
        "physical_memory_count_after": count_after,
        "heldout_memory_mutation": count_after - count_before,
        "parametric_readiness": readiness,
    }
    _write(root / "calibration_report.json", report)
    _write(root / "parametric_readiness.json", readiness)
    print(json.dumps({
        "all_policies_ready": all_ready,
        "policy_statuses": {key: value["status"] for key, value in policies.items()},
        "heldout_memory_mutation": 0,
        "parametric_readiness": readiness["status"],
    }, indent=2, sort_keys=True))
    return report


if __name__ == "__main__":
    raise SystemExit(main())
