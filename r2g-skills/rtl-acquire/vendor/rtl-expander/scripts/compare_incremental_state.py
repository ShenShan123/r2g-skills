#!/usr/bin/env python3
"""Read-only semantic shadow comparison for incremental finalization."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from corpus_state import CorpusState, canonical, digest_bytes


SCHEMA = "rtl_incremental_state_shadow_compare_v1"


def rows(path: Path, key: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                result[str(row[key])] = row
    return result


def projection(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row.get("family_id"), row.get("split_group_id"), row.get("split"),
        row.get("quality", {}).get("training_tier"),
        row.get("release", {}).get("license_status"),
        row.get("release", {}).get("release_policy"),
        bool(row.get("contamination", {}).get("benchmark_contaminated", False)),
    )


def digest_projection(values: dict[str, dict[str, Any]]) -> str:
    return digest_bytes(canonical([
        (design_id, *projection(row)) for design_id, row in sorted(values.items())
    ]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-root", type=Path, required=True)
    parser.add_argument("--round-id", required=True)
    args = parser.parse_args()
    manifests = args.corpus_root / "manifests"
    manifest_designs = rows(manifests / "all_designs.jsonl", "design_id")
    manifest_families = rows(manifests / "families.jsonl", "family_id")
    manifest_gold = rows(manifests / "training_gold.jsonl", "design_id")
    with CorpusState(args.corpus_root) as state:
        indexed_designs = {row["design_id"]: row for row in state.payloads()}
    design_ids_identical = set(indexed_designs) == set(manifest_designs)
    membership_mismatches = sorted(
        design_id for design_id in set(indexed_designs) & set(manifest_designs)
        if projection(indexed_designs[design_id]) != projection(manifest_designs[design_id])
    )
    derived_families: dict[str, set[str]] = {}
    for design_id, row in indexed_designs.items():
        derived_families.setdefault(str(row.get("family_id")), set()).add(design_id)
    family_memberships_identical = (
        set(derived_families) == set(manifest_families)
        and all(
            derived_families[family_id] == set(map(str, manifest_families[family_id].get("design_ids", [])))
            for family_id in set(derived_families) & set(manifest_families)
        )
    )
    derived_gold = {
        design_id for design_id, row in indexed_designs.items()
        if row.get("quality", {}).get("training_tier") == "TRAINING_GOLD"
        and row.get("source", {}).get("repository_revision_key")
    }
    checks = {
        "design_ids_identical": design_ids_identical,
        "family_memberships_identical": family_memberships_identical,
        "split_memberships_identical": not membership_mismatches,
        "gold_memberships_identical": derived_gold == set(manifest_gold),
        "license_state_identical": not membership_mismatches,
        "contamination_state_identical": not membership_mismatches,
        "logical_corpus_hash_identical": (
            digest_projection(indexed_designs) == digest_projection(manifest_designs)
        ),
    }
    failed = sorted(key for key, value in checks.items() if not value)
    report = {
        "schema": SCHEMA, "round_id": args.round_id,
        "checks": checks, "valid": not failed, "failed": failed,
        "indexed_designs": len(indexed_designs),
        "manifest_designs": len(manifest_designs),
        "membership_mismatch_sample": membership_mismatches[:20],
        "indexed_logical_sha256": digest_projection(indexed_designs),
        "manifest_logical_sha256": digest_projection(manifest_designs),
    }
    output = (
        args.corpus_root / "quality/phase2/rounds" / args.round_id
        / "incremental_shadow_compare.json"
    )
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
