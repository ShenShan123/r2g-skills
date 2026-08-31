#!/usr/bin/env python3
"""Build a replayable P9 production-gate report from explicit evidence.

The manifest is an operator-owned index of already-produced oracle reports; it
is not a place to invent aggregate metrics.  This script resolves each local
evidence reference, computes its digest, rejects stale expected digests, and
passes the resulting content-bound map to the pure P9 evaluator.  It never
opens SQLite, promotes lifecycle objects, or enables production routing.
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

from tehm.ids import stable_dumps  # noqa: E402
from tehm.retrieval.production_gate import (  # noqa: E402
    ProductionGateError,
    evaluate_production_gate,
)


MANIFEST_VERSION = "production-gate-manifest-v1"
REPORT_VERSION = "production-gate-report-v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _manifest_digest(payload: Mapping) -> str:
    return "sha256:" + hashlib.sha256(
        stable_dumps(dict(payload)).encode()).hexdigest()


def _load_manifest(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ProductionGateError(f"cannot read manifest: {path}") from exc
    if (not isinstance(payload, dict) or
            payload.get("version") != MANIFEST_VERSION):
        raise ProductionGateError("production gate manifest version mismatch")
    if not isinstance(payload.get("metrics"), Mapping):
        raise ProductionGateError("production gate manifest metrics must be an object")
    if isinstance(payload.get("evidence_refs"), (str, bytes)) or not isinstance(
            payload.get("evidence_refs"), Sequence):
        raise ProductionGateError("production gate manifest evidence_refs must be a sequence")
    return payload


def _bind_evidence_refs(manifest: Mapping, manifest_path: Path) -> tuple[dict, ...]:
    refs: list[dict] = []
    seen: set[str] = set()
    for item in manifest["evidence_refs"]:
        if not isinstance(item, Mapping):
            raise ProductionGateError("each manifest evidence_ref must be an object")
        raw_path = item.get("path") or item.get("file")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ProductionGateError("manifest evidence_ref requires path")
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = manifest_path.parent / path
        path = path.resolve()
        if not path.is_file():
            raise ProductionGateError(f"evidence reference is not a file: {path}")
        digest = _sha256(path)
        expected = item.get("sha256") or item.get("digest")
        if expected is not None and expected != digest:
            raise ProductionGateError(f"evidence digest mismatch: {path}")
        ref = dict(item)
        ref["path"] = str(path)
        ref["sha256"] = digest
        key = stable_dumps(ref)
        if key in seen:
            raise ProductionGateError("manifest evidence_refs contain duplicates")
        seen.add(key)
        refs.append(ref)
    return tuple(refs)


def build_production_gate_report(
        manifest: Path | str, *, output: Path | str) -> dict:
    """Build one P9 report, returning the exact serialised payload."""
    manifest_path = Path(manifest).expanduser().resolve()
    payload = _load_manifest(manifest_path)
    refs = _bind_evidence_refs(payload, manifest_path)
    evidence = dict(payload["metrics"])
    # Authority/rollback are kept as explicit top-level manifest sections so
    # an operator cannot accidentally bury them in an unrecognised metric.
    for name in ("authority", "rollback"):
        section = payload.get(name)
        if isinstance(section, Mapping):
            for key, value in section.items():
                evidence.setdefault(key, value)
    evidence["evidence_refs"] = list(refs)
    receipt = evaluate_production_gate(evidence)
    report = {
        "version": REPORT_VERSION,
        "manifest": str(manifest_path),
        "manifest_sha256": _sha256(manifest_path),
        "manifest_digest": _manifest_digest(payload),
        "evidence_refs": list(refs),
        "receipt": {**receipt.to_dict(),
                    "receipt_id": receipt.receipt_id,
                    "receipt_digest": receipt.receipt_digest},
        "canonical_memory_mutation": "none",
        "promotion_attempted": False,
        "production_promotion_eligible": receipt.eligible,
        "production_integration": receipt.production_integration,
        "authority_note": (
            "P9 is an evaluation-only report; this artifact cannot promote "
            "memory or enable production routing"),
    }
    output_path = Path(output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = build_production_gate_report(args.manifest, output=args.output)
    except (OSError, ProductionGateError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({
        "output": str(Path(args.output).expanduser().resolve()),
        "eligible": bool(report["receipt"]["eligible"]),
        "gate_status": report["receipt"]["gate_status"],
        "production_integration": report["production_integration"],
    }, indent=2, sort_keys=True))
    return 0 if report["receipt"]["eligible"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
