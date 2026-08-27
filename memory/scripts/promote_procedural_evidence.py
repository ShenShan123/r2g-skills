#!/usr/bin/env python3
"""Promote compact procedural-memory evidence without promoting runtime state.

Procedural growth, stability, and component-ablation runs deliberately use
isolated writable copies.  This helper copies only the small, reviewable
reports and manifests into a durable evidence root; it never copies the
staging SQLite database, artifact store, Icarus work tree, or ORFS outputs.
The resulting binding is an evidence receipt, not lifecycle/promotion
authority.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from tehm.sync import canonical_json


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(path: Path) -> dict:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json(value))


def _copy(source: Path, destination: Path, copied: list[dict]) -> None:
    if not source.is_file():
        raise FileNotFoundError(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    copied.append({"path": destination.name, "sha256": _sha256(destination)})


def promote(growth: Path, stability: Path, runtime: Path, evidence: Path,
            canonical_bundle: Path, task_manifest: Path) -> dict:
    growth = growth.resolve()
    stability = stability.resolve()
    runtime = runtime.resolve()
    evidence = evidence.resolve()
    canonical_bundle = canonical_bundle.resolve()
    task_manifest = task_manifest.resolve()
    bundle_manifest = _read(canonical_bundle / "bundle_manifest.json")
    expected_bundle = bundle_manifest.get("bundle_digest")
    expected_manifest = bundle_manifest.get("manifest_digest")
    if not expected_bundle or not expected_manifest:
        raise ValueError("canonical bundle manifest lacks bundle/manifest digest")

    growth_report = _read(growth / "procedural_rule_growth_report.json")
    stability_report = _read(stability / "procedural_rule_stability_report.json")
    runtime_report = _read(runtime / "procedural_ablation_report.json")
    reports = (growth_report, stability_report, runtime_report)
    for report in reports:
        if report.get("bundle_digest") != expected_bundle:
            raise ValueError("procedural report bundle digest does not match canonical")
    # Growth/stability bind directly to the canonical freeze manifest.  The
    # runtime ablation additionally binds its task-manifest digest, so retain
    # both values instead of incorrectly treating them as interchangeable.
    if growth_report.get("manifest_digest") != expected_manifest:
        raise ValueError("growth report manifest digest does not match canonical")
    if stability_report.get("manifest_digest") != expected_manifest:
        raise ValueError("stability report manifest digest does not match canonical")
    if not growth_report.get("canonical_memory_unchanged"):
        raise ValueError("growth report is not a zero-mutation receipt")
    if not stability_report.get("canonical_memory_unchanged"):
        raise ValueError("stability report is not a zero-mutation receipt")
    if runtime_report.get("canonical_memory_mutation") != "none":
        raise ValueError("runtime report records canonical mutation")
    if not runtime_report.get("acceptance_passed"):
        raise ValueError("runtime component acceptance did not pass")

    evidence.mkdir(parents=True, exist_ok=True)
    copied: list[dict] = []
    for source_name, destination_name in (
        (growth / "procedural_rule_growth_report.json", "procedural_rule_growth_report.json"),
        (growth / "procedural_rule_growth_report.md", "procedural_rule_growth_report.md"),
        (stability / "procedural_rule_stability_report.json", "procedural_rule_stability_report.json"),
        (stability / "procedural_rule_stability_report.md", "procedural_rule_stability_report.md"),
        (runtime / "procedural_ablation_report.json", "procedural_ablation_report.json"),
        (runtime / "procedural_ablation_report.md", "procedural_ablation_report.md"),
        (runtime / "procedural_ablation_manifest.normalized.json",
         "procedural_ablation_manifest.normalized.json"),
        (task_manifest, task_manifest.name),
    ):
        _copy(source_name, evidence / destination_name, copied)

    fixtures = [row.get("fixture") for row in growth_report.get("fixtures", [])]
    binding = {
        "version": "procedural-evidence-binding-v1",
        "canonical_bundle": str(canonical_bundle),
        "bundle_digest": expected_bundle,
        "manifest_digest": expected_manifest,
        "report_manifest_digests": {
            "growth": growth_report.get("manifest_digest"),
            "stability": stability_report.get("manifest_digest"),
            "runtime_ablation_task_manifest": runtime_report.get("manifest_digest"),
        },
        "source_scratch_roots": {
            "growth": str(growth),
            "stability": str(stability),
            "runtime_ablation": str(runtime),
        },
        "fixtures": fixtures,
        "copied_files": copied,
        "staging_lifecycle_promoted": False,
        "orfs_run_trees_promoted": False,
        "canonical_memory_mutation": "none",
        "promotion_eligible": False,
        "parametric_view_status": "NOT_IMPLEMENTED",
        "runtime_authority": "staging_only",
        "note": "compact reports only; source SQLite/artifacts/eval_work remain scratch",
    }
    _write(evidence / "promotion_report.json", binding)
    return binding


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--growth-source", type=Path, required=True)
    parser.add_argument("--stability-source", type=Path, required=True)
    parser.add_argument("--runtime-source", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--canonical-bundle", type=Path, required=True)
    parser.add_argument("--task-manifest", type=Path, required=True)
    args = parser.parse_args(argv)
    result = promote(
        args.growth_source, args.stability_source, args.runtime_source,
        args.evidence_root, args.canonical_bundle, args.task_manifest,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
