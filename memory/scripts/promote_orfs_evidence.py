#!/usr/bin/env python3
"""Copy only durable ORFS evidence from a scratch campaign root.

The command never deletes scratch data and refuses to overwrite an existing
evidence root unless ``--overwrite`` is explicit.  RUN result/object trees are
not copied; final DEF/GDS/JSON files and run receipts are flattened below the
case's ``final/`` and ``receipts/`` paths so the evidence root never contains a
regenerable ``RUN_*`` directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path


KEEP_ROOT_FILES = {"campaign_manifest.json", "campaign_state.json",
                   "campaign_metrics.json", "campaign_metrics.md",
                   "trial_report.json", "report.json", "add_designs_report.json",
                   "campaign_recovery_report.json"}
KEEP_DIRS = {"reports", "features", "drc", "lvs", "rcx", "receipts"}
KEEP_RUN_FILES = {"stage_log.jsonl", "run-meta.json"}
FINAL_SUFFIXES = {".def", ".gds", ".json"}


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _keep(rel: Path) -> bool:
    if len(rel.parts) == 1 and rel.name in KEEP_ROOT_FILES:
        return True
    # The ORFS working tree is fully regenerable, including its reports and
    # image previews.  Do not let a nested directory name promote it.
    if ".orfs-work" in rel.parts:
        return False
    # RUN trees are regenerable.  Retain only their explicit receipt/final
    # evidence; a nested directory named ``reports`` or ``features`` under a
    # RUN must not accidentally pull large tool databases into evidence.
    if any(part.startswith("RUN_") for part in rel.parts):
        if rel.name in KEEP_RUN_FILES:
            return True
        if "receipts" in rel.parts:
            return True
        return "final" in rel.parts and rel.suffix.lower() in FINAL_SUFFIXES
    if rel.name in KEEP_RUN_FILES:
        return True
    if "receipts" in rel.parts or any(part in KEEP_DIRS for part in rel.parts):
        return True
    if "final" in rel.parts and rel.suffix.lower() in FINAL_SUFFIXES:
        return True
    return False


def _target_rel(rel: Path) -> Path:
    """Map selected files out of ``backend/RUN_*`` without losing provenance."""
    parts = list(rel.parts)
    run_index = next((i for i, part in enumerate(parts)
                      if part.startswith("RUN_")), None)
    if run_index is None:
        return rel
    run_id = parts[run_index][len("RUN_"):]
    suffix = parts[run_index + 1:]
    case_parts = parts[:run_index]
    if case_parts and case_parts[-1] == "backend":
        case_parts.pop()
    if suffix and suffix[0] == "final":
        return Path(*case_parts, "final", run_id, *suffix[1:])
    if rel.name in KEEP_RUN_FILES:
        return Path(*case_parts, "receipts", run_id, rel.name)
    if "receipts" in suffix:
        return Path(*case_parts, "receipts", run_id, *suffix)
    raise ValueError(f"selected RUN file has no durable target: {rel}")


def promote(scratch: Path, evidence: Path, *, overwrite: bool = False) -> dict:
    scratch, evidence = scratch.resolve(), evidence.resolve()
    if not scratch.is_dir():
        raise FileNotFoundError(f"scratch root not found: {scratch}")
    if evidence.exists() and any(evidence.iterdir()) and not overwrite:
        raise FileExistsError(f"refusing to overwrite non-empty evidence root: {evidence}")
    evidence.mkdir(parents=True, exist_ok=True)
    copied = []
    skipped = []
    for source in sorted(p for p in scratch.rglob("*") if p.is_file()):
        rel = source.relative_to(scratch)
        if not _keep(rel):
            skipped.append(rel.as_posix())
            continue
        target_rel = _target_rel(rel)
        target = evidence / target_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append({"path": target_rel.as_posix(), "source_path": rel.as_posix(),
                       "sha256": _sha(target),
                       "size": target.stat().st_size})
    result = {
        "version": "orfs-evidence-promotion-v1",
        "scratch_root": str(scratch), "evidence_root": str(evidence),
        "copied_count": len(copied), "skipped_reproducible_count": len(skipped),
        "copied": copied,
        "retention_policy": {
            "skipped_patterns": ["**/RUN_*/logs", "**/RUN_*/results",
                                  "**/RUN_*/objects", "**/.orfs-work/**"],
            "flattened_run_evidence": ["final/<run-id>/", "receipts/<run-id>/"],
            "scratch_not_deleted": True,
        },
    }
    (evidence / "promotion_manifest.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scratch-root", type=Path, required=True)
    ap.add_argument("--evidence-root", type=Path, required=True)
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args(argv)
    result = promote(args.scratch_root, args.evidence_root, overwrite=args.overwrite)
    print(json.dumps({key: result[key] for key in (
        "evidence_root", "copied_count", "skipped_reproducible_count")},
        indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
