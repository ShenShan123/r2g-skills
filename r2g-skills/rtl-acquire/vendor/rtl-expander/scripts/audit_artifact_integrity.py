#!/usr/bin/env python3
"""Explicit metadata, sampled-byte, or full-byte integrity audit.

This tool is intentionally outside the normal round critical path. Immutable
artifacts are hashed once at admission; byte rehash is maintenance/forensics.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from frontier import utc_now


def rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def selected(identity: str, percent: float) -> bool:
    threshold = int((percent / 100.0) * (1 << 64))
    return int(hashlib.sha256(identity.encode()).hexdigest()[:16], 16) < threshold


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-root", type=Path, required=True)
    parser.add_argument("--mode", choices=("metadata", "sample", "full-rehash"), default="metadata")
    parser.add_argument("--sample-percent", type=float, default=1.0)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 0 < args.sample_percent <= 100:
        raise SystemExit("--sample-percent must be in (0,100]")
    metadata_checked = byte_checked = mismatches = rehash_required = 0
    for design in rows(args.corpus_root / "manifests/all_designs.jsonl"):
        root_text = design.get("storage", {}).get("repository_source_path") or design.get("source", {}).get("original_root")
        if not root_text:
            continue
        root = Path(root_text)
        for unit in design.get("source", {}).get("source_units", []):
            relative = str(unit.get("path") or "")
            expected = str(unit.get("sha256") or "").lower()
            path = root / relative
            metadata_checked += 1
            if len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected) or not path.is_file():
                rehash_required += 1
                continue
            do_hash = args.mode == "full-rehash" or (
                args.mode == "sample" and selected(f"{design.get('design_id')}\0{relative}", args.sample_percent)
            )
            if do_hash:
                actual = hashlib.sha256(path.read_bytes()).hexdigest()
                byte_checked += 1
                mismatches += actual != expected
    report = {
        "schema": "rtl_artifact_integrity_audit_v1",
        "mode": args.mode,
        "sample_percent": args.sample_percent if args.mode == "sample" else None,
        "generated_at": utc_now(),
        "metadata_checked": metadata_checked,
        "byte_rehashed": byte_checked,
        "digest_mismatches": mismatches,
        "rehash_required": rehash_required,
        "status": "PASS" if mismatches == 0 and rehash_required == 0 else "FAIL",
    }
    output = args.output or args.corpus_root / "quality/artifact_integrity_audit.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    temporary.replace(output)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
