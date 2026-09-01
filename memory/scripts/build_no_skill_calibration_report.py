#!/usr/bin/env python3
"""Build a content-bound P15 reason-aware NO_SKILL calibration report.

The manifest must contain explicit predictions and independent oracle labels.
This builder never reads P12 outcome fields and never infers a label from
repair, harm, or pass/fail results.  Evidence references are resolved and
hashed before the pure evaluation receipt is written.  The resulting report
is evaluation-only and cannot promote memory or enable production routing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tehm.evaluation.no_skill_calibration import (  # noqa: E402
    NoSkillCalibrationError, evaluate_no_skill_calibration,
)
from tehm.ids import stable_dumps  # noqa: E402


MANIFEST_VERSION = "no-skill-calibration-manifest-v1"
REPORT_VERSION = "no-skill-calibration-report-v1"
_FORBIDDEN_KEYS = frozenset({
    "fix", "gold_patch", "repaired_rtl", "heldout_answer", "gold",
    "outcome", "baseline_outcome", "memory_outcome", "paired_outcomes",
    "candidate_outcome", "repair_result", "harm_result",
})


class CalibrationReportError(ValueError):
    """Manifest or evidence cannot safely become a P15 report."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _manifest_digest(payload: Mapping) -> str:
    return "sha256:" + hashlib.sha256(
        stable_dumps(dict(payload)).encode()).hexdigest()


def _nonempty_text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise CalibrationReportError(f"{name} must be a non-empty string")
    return value.strip()


def _contains_forbidden(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(key in _FORBIDDEN_KEYS or _contains_forbidden(item)
                   for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_forbidden(item) for item in value)
    return False


def _load_manifest(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise CalibrationReportError(f"cannot read calibration manifest: {path}") from exc
    if not isinstance(payload, dict) or payload.get("version") != MANIFEST_VERSION:
        raise CalibrationReportError("calibration manifest version mismatch")
    samples = payload.get("samples")
    if (isinstance(samples, (str, bytes)) or not isinstance(samples, Sequence) or
            not samples):
        raise CalibrationReportError(
            "calibration manifest requires a non-empty explicit samples sequence")
    if _contains_forbidden(samples):
        raise CalibrationReportError(
            "calibration samples contain outcome or gold-answer fields")
    _nonempty_text(payload.get("oracle_label_source"), "oracle_label_source")
    refs = payload.get("evidence_refs")
    if (isinstance(refs, (str, bytes)) or not isinstance(refs, Sequence) or not refs):
        raise CalibrationReportError(
            "calibration manifest requires immutable evidence_refs")
    return payload


def _bind_evidence_refs(manifest: Mapping, manifest_path: Path) -> tuple[dict, ...]:
    refs: list[dict] = []
    seen: set[str] = set()
    for item in manifest["evidence_refs"]:
        if not isinstance(item, Mapping):
            raise CalibrationReportError("each calibration evidence_ref must be an object")
        raw_path = item.get("path") or item.get("file")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise CalibrationReportError("calibration evidence_ref requires path")
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = manifest_path.parent / path
        path = path.resolve()
        if not path.is_file():
            raise CalibrationReportError(f"calibration evidence is not a file: {path}")
        digest = _sha256(path)
        expected = item.get("sha256") or item.get("digest")
        if expected is not None and expected != digest:
            raise CalibrationReportError(f"calibration evidence digest mismatch: {path}")
        ref = dict(item)
        ref["path"] = str(path)
        ref["sha256"] = digest
        key = stable_dumps(ref)
        if key in seen:
            raise CalibrationReportError("calibration evidence_refs contain duplicates")
        seen.add(key)
        refs.append(ref)
    return tuple(refs)


def build_no_skill_calibration_report(
        manifest: Path | str, *, output: Path | str,
        minimum_sample_count: int = 20,
        minimum_reason_cases: int = 1,
        calibration_bins: int = 10) -> dict:
    """Build one P15 report from explicit labels and immutable references."""
    manifest_path = Path(manifest).expanduser().resolve()
    payload = _load_manifest(manifest_path)
    refs = _bind_evidence_refs(payload, manifest_path)
    receipt = evaluate_no_skill_calibration(
        payload["samples"], minimum_sample_count=minimum_sample_count,
        minimum_reason_cases=minimum_reason_cases, calibration_bins=calibration_bins)
    report = {
        "version": REPORT_VERSION,
        "manifest": str(manifest_path),
        "manifest_sha256": _sha256(manifest_path),
        "manifest_digest": _manifest_digest(payload),
        "oracle_label_source": payload["oracle_label_source"],
        "evidence_refs": list(refs),
        # Keep the receipt at a stable top-level key so callers can pass the
        # whole report through an evidence flattener without hand-editing it.
        "no_skill_calibration": receipt.to_dict(),
        "receipt": {**receipt.to_dict(), "receipt_id": receipt.receipt_id,
                    "receipt_digest": receipt.receipt_digest},
        "canonical_memory_mutation": "none",
        "promotion_attempted": False,
        "production_promotion_eligible": receipt.eligible,
        "production_integration": "not_attempted",
    }
    output_path = Path(output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--minimum-sample-count", type=int, default=20)
    parser.add_argument("--minimum-reason-cases", type=int, default=1)
    parser.add_argument("--calibration-bins", type=int, default=10)
    args = parser.parse_args(argv)
    try:
        report = build_no_skill_calibration_report(
            args.manifest, output=args.output,
            minimum_sample_count=args.minimum_sample_count,
            minimum_reason_cases=args.minimum_reason_cases,
            calibration_bins=args.calibration_bins)
    except (OSError, CalibrationReportError, NoSkillCalibrationError,
            TypeError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({
        "output": str(Path(args.output).expanduser().resolve()),
        "eligible": bool(report["receipt"]["eligible"]),
        "status": report["receipt"]["status"],
        "production_integration": report["production_integration"],
    }, indent=2, sort_keys=True))
    return 0 if report["receipt"]["eligible"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
