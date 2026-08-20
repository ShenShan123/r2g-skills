#!/usr/bin/env python3
"""Audit all published source units against the versioned benchmark registry."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from build_benchmark_registry import COMMENT_RE, TOKEN_RE, sha
from corpus_state import CorpusState, digest_tree
from run_expansion_round import load_jsonl, quality_scores, validate_publish_invariants, write_manifests


AUDIT_SCHEMA = "rtl_contamination_audit_v1"


def registry_hash(root: Path) -> str:
    return digest_tree(root)


def source_fingerprints(path: Path) -> dict[str, str]:
    raw = path.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    clean = COMMENT_RE.sub(" ", text)
    normalized = re.sub(r"\s+", " ", clean).strip().lower()
    tokens = TOKEN_RE.findall(clean.lower())
    return {
        "raw_hash": sha(raw), "normalized_hash": sha(normalized.encode()),
        "ast_token_fingerprint": sha("\0".join(tokens).encode()),
    }


def registry_index(root: Path) -> tuple[dict[str, set[tuple[str, str, str]]], dict[str, Any]]:
    catalog = json.loads((root / "registry_catalog.json").read_text(encoding="utf-8"))
    profile: dict[str, Any] | None = None
    if catalog.get("active_profile"):
        profile = json.loads((root / "profiles" / f"{catalog['active_profile']}.json").read_text(encoding="utf-8"))
    active_names = set(profile.get("active_benchmarks", [])) if profile else None
    index: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
    for name, entry in catalog.get("entries", {}).items():
        if active_names is not None and name not in active_names:
            continue
        if entry.get("status") != "ACTIVE":
            continue
        path = root / name / "fingerprints.jsonl"
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            row = json.loads(line)
            for kind in ("raw_hash", "normalized_hash", "ast_token_fingerprint"):
                index[row[kind]].add((name, kind, row.get("path", "")))
    catalog["audit_ready"] = bool(profile.get("ready")) if profile else bool(catalog.get("ready"))
    catalog["audit_profile"] = profile.get("profile_id") if profile else None
    catalog["audit_active_benchmarks"] = sorted(active_names) if active_names is not None else sorted(name for name, entry in catalog.get("entries", {}).items() if entry.get("status") == "ACTIVE")
    return index, catalog


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-root", type=Path, default=Path.home() / "work/data/rtl_corpus")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    registry_root = args.corpus_root / "benchmark_registry"
    if not (registry_root / "registry_catalog.json").is_file():
        raise SystemExit("registry catalog unavailable")
    index, catalog = registry_index(registry_root)
    designs = load_jsonl(args.corpus_root / "manifests/all_designs.jsonl", "design_id")
    matches: list[dict[str, Any]] = []
    checked_units = 0
    for record in designs.values():
        root = Path(record.get("storage", {}).get("repository_source_path") or record.get("source", {}).get("original_root", ""))
        design_matches: set[tuple[str, str, str, str]] = set()
        for unit in record.get("source", {}).get("source_units", []):
            path = root / unit["path"]
            if not path.is_file():
                continue
            checked_units += 1
            for digest in source_fingerprints(path).values():
                for benchmark, kind, benchmark_path in index.get(digest, set()):
                    design_matches.add((unit["path"], benchmark, kind, benchmark_path))
        match_rows = [
            {"source_path": source, "benchmark": benchmark, "match_type": kind, "benchmark_path": benchmark_path}
            for source, benchmark, kind, benchmark_path in sorted(design_matches)
        ]
        if match_rows:
            matches.append({"design_id": record["design_id"], "family_id": record["family_id"], "matches": match_rows})
        if args.apply:
            record["contamination"] = {
                "audit_schema": AUDIT_SCHEMA, "audit_status": "FAIL" if match_rows else "PASS" if catalog.get("audit_ready") else "NOT_RUN",
                "benchmark_contaminated": bool(match_rows),
                "benchmark_name": sorted({row["benchmark"] for row in match_rows}) or None,
                "matches": match_rows, "registry_ready": bool(catalog.get("audit_ready")),
                "benchmark_profile": catalog.get("audit_profile"),
                "active_benchmarks": catalog.get("audit_active_benchmarks", []),
                "pending_benchmarks": sorted(name for name, entry in catalog.get("entries", {}).items() if entry.get("status") != "ACTIVE"),
            }
            if match_rows:
                record["quality"]["training_tier"] = "TRAINING_EXCLUDED"
                flags = set(record["quality"].get("quality_flags", []))
                flags.add("BENCHMARK_CONTAMINATED")
                record["quality"]["quality_flags"] = sorted(flags)
            else:
                eq, grade, tv, tier, eq_components, tv_components = quality_scores(record)
                release_policy = record.get("release", {}).get("release_policy")
                repair = record.get("repair", {})
                repair_level = repair.get("level", "R0")
                repair_equivalence = repair.get("equivalence", {}).get("result")
                if tier == "TRAINING_GOLD" and release_policy not in {"PUBLIC_EXPORT_ALLOWED", "INTERNAL_TRAINING_ONLY"}:
                    tier = "TRAINING_SILVER"
                if tier == "TRAINING_GOLD" and repair_level in {"R2", "R3", "R4"} and repair_equivalence != "PASS":
                    tier = "TRAINING_SILVER"
                if "LEGACY_PROVENANCE_UNRESOLVED" in record.get("quality", {}).get("quality_flags", []):
                    tier = "TRAINING_AUXILIARY"
                quality = record.setdefault("quality", {})
                quality.update({
                    "engineering_quality": eq, "engineering_grade": grade,
                    "engineering_quality_components": eq_components,
                    "training_value": tv, "training_value_components": tv_components,
                    "training_tier": tier,
                })
                flags = set(quality.get("quality_flags", []))
                flags.discard("BENCHMARK_AUDIT_NOT_RUN")
                quality["quality_flags"] = sorted(flags)
    if args.apply:
        validate_publish_invariants(args.corpus_root, designs)
        write_manifests(args.corpus_root, designs)
        with CorpusState(args.corpus_root) as state:
            if not state.populated():
                state.sync_materialized_views()
            else:
                state.apply_incremental(designs=designs.values())
    summary = {
        "schema": AUDIT_SCHEMA, "apply": args.apply, "registry_ready": bool(catalog.get("audit_ready")),
        "benchmark_profile": catalog.get("audit_profile"),
        "benchmark_registry_hash": registry_hash(registry_root),
        "active_benchmarks": catalog.get("audit_active_benchmarks", []),
        "pending_benchmarks": sorted(name for name, entry in catalog.get("entries", {}).items() if entry.get("status") != "ACTIVE"),
        "designs_checked": len(designs), "source_units_checked": checked_units,
        "matched_designs": len(matches), "matched_families": len({row["family_id"] for row in matches}),
        "matches_by_benchmark": dict(Counter(match["benchmark"] for row in matches for match in row["matches"])),
        "matches": matches,
    }
    target = args.corpus_root / "quality/phase1_5/benchmark_contamination_audit.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in summary.items() if key != "matches"}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
