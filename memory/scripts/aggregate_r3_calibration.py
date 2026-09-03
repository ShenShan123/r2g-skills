#!/usr/bin/env python3
"""Aggregate source-backed P15 calibration manifests across backends.

The input manifests are already evaluation-only and contain typed routing
predictions plus independent oracle labels.  This command only combines their
normalized samples after checking unique case IDs and disjoint campaign
identities; it never reuses calibration outcomes as learner memory and never
changes a production-readiness gate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
from collections.abc import Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_no_skill_calibration_report import (  # noqa: E402
    MANIFEST_VERSION, build_no_skill_calibration_report,
)
from tehm.evaluation.no_skill_calibration import (  # noqa: E402
    build_no_skill_calibration_samples,
)
from tehm.ids import stable_dumps  # noqa: E402


VERSION = "tehm-r3-p15-calibration-aggregate-v0.1"


class CalibrationAggregateError(ValueError):
    """Input calibration manifests cannot be combined safely."""


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _load_samples(path: Path) -> tuple[str, tuple[dict, ...], tuple[dict, ...]]:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise CalibrationAggregateError(f"cannot read calibration manifest: {path}") from exc
    if not isinstance(payload, dict) or payload.get("version") != MANIFEST_VERSION:
        raise CalibrationAggregateError(f"manifest version mismatch: {path}")
    if payload.get("split") != "calibration":
        raise CalibrationAggregateError(f"manifest is not calibration split: {path}")
    if not all(isinstance(payload.get(key), dict) for key in (
            "paired_routing_index", "routing_decisions", "oracle_labels")):
        raise CalibrationAggregateError(
            f"aggregate input must use typed routing inputs: {path}")
    try:
        samples = build_no_skill_calibration_samples(
            payload["paired_routing_index"], payload["routing_decisions"],
            payload["oracle_labels"])
    except (TypeError, ValueError) as exc:
        raise CalibrationAggregateError(
            f"typed sample replay failed for {path}: {exc}") from exc
    sample_payloads = tuple(sample.to_dict() for sample in samples)
    return str(payload.get("campaign_id") or path.parent.name), sample_payloads, tuple(
        dict(item) for item in payload.get("evidence_refs", ())
        if isinstance(item, dict))


def run(*, manifests: Sequence[Path | str], artifacts: Path | str,
        force: bool = False, minimum_sample_count: int | None = None,
        minimum_reason_cases: int = 2) -> dict:
    if len(manifests) < 2:
        raise CalibrationAggregateError("at least two calibration manifests are required")
    if type(minimum_reason_cases) is not int or minimum_reason_cases < 1:
        raise CalibrationAggregateError("minimum_reason_cases must be positive")
    output = Path(artifacts).expanduser().resolve()
    if output.exists():
        if not force:
            raise CalibrationAggregateError(
                f"output exists; pass --force to replace it: {output}")
        shutil.rmtree(output)
    output.mkdir(parents=True)
    receipts = output / "receipts"
    receipts.mkdir()

    normalized = []
    campaigns: list[str] = []
    all_samples: list[dict] = []
    source_refs: list[dict] = []
    seen_ids: set[str] = set()
    for raw_path in manifests:
        path = Path(raw_path).expanduser().resolve()
        if not path.is_file():
            raise CalibrationAggregateError(f"calibration manifest is missing: {path}")
        campaign, samples, refs = _load_samples(path)
        if campaign in campaigns:
            raise CalibrationAggregateError(
                f"calibration campaign is duplicated: {campaign}")
        campaigns.append(campaign)
        sample_ids = {item["case_id"] for item in samples}
        if seen_ids & sample_ids:
            raise CalibrationAggregateError(
                "calibration case IDs overlap across input manifests")
        seen_ids.update(sample_ids)
        all_samples.extend(samples)
        normalized.append({
            "manifest": str(path), "sha256": _file_digest(path),
            "campaign_id": campaign, "sample_count": len(samples),
            "sample_ids": sorted(sample_ids),
        })
        source_refs.append({"id": f"manifest:{campaign}", "path": str(path),
                            "sha256": _file_digest(path)})
        # Preserve each producer's own evidence references so the aggregate
        # remains auditable without copying external artifacts into the repo.
        for ref in refs:
            if isinstance(ref.get("path"), str):
                source_refs.append({
                    "id": f"{campaign}:{ref.get('id', 'evidence')}",
                    "path": ref["path"],
                    "sha256": ref.get("sha256") or ref.get("digest"),
                })

    if minimum_sample_count is None:
        minimum_sample_count = len(all_samples)
    if type(minimum_sample_count) is not int or minimum_sample_count < 1:
        raise CalibrationAggregateError("minimum_sample_count must be positive")
    # Avoid duplicate reference rows while retaining deterministic order.
    unique_refs = []
    seen_refs = set()
    for ref in source_refs:
        key = stable_dumps(ref)
        if key not in seen_refs:
            seen_refs.add(key)
            unique_refs.append(ref)
    manifest = {
        "version": MANIFEST_VERSION,
        "campaign_id": VERSION,
        "split": "calibration",
        "oracle_label_source": "typed-paired-oracle-cross-backend-v1",
        "input_campaigns": normalized,
        "samples": all_samples,
        "evidence_refs": unique_refs,
        "evaluation_only": True,
        "canonical_memory_mutation": "none",
        "production_authority_changed": False,
    }
    manifest_path = receipts / "calibration_manifest.json"
    _write_json(manifest_path, manifest)
    report_path = receipts / "calibration_report.json"
    report = build_no_skill_calibration_report(
        manifest_path, output=report_path,
        minimum_sample_count=minimum_sample_count,
        minimum_reason_cases=minimum_reason_cases, calibration_bins=10)
    receipt = report["receipt"]
    summary = {
        "version": VERSION,
        "campaign_id": VERSION,
        "split": "calibration",
        "backend_scope": "cross_backend",
        "input_campaigns": normalized,
        "sample_count": len(all_samples),
        "campaign_count": len(campaigns),
        "case_id_disjoint": len(seen_ids) == len(all_samples),
        "calibration_report": str(report_path),
        "calibration_receipt": receipt,
        "interpretation": (
            "This aggregate improves reason-stratified sample coverage by combining "
            "independent RTL and external ORFS calibration manifests. The ORFS slice "
            "retains its observed false-negative CONSIDER predictions; no metric is "
            "repaired or relabeled. This is statistical evidence only and does not "
            "satisfy MIR, candidate-pool, authority, or production gates."),
        "evaluation_only": True,
        "canonical_memory_mutation": "none",
        "production_authority_changed": False,
        "production_promotion_eligible": False,
        "memory_docs_submitted": False,
    }
    _write_json(output / "summary.json", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", type=Path, action="append", required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--minimum-sample-count", type=int)
    parser.add_argument("--minimum-reason-cases", type=int, default=2)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        summary = run(manifests=args.manifest, artifacts=args.artifacts,
                      force=args.force,
                      minimum_sample_count=args.minimum_sample_count,
                      minimum_reason_cases=args.minimum_reason_cases)
    except (OSError, TypeError, ValueError, CalibrationAggregateError) as exc:
        print(f"calibration aggregate failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["calibration_receipt"]["eligible"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
