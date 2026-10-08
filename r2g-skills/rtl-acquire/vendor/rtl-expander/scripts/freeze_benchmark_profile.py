#!/usr/bin/env python3
"""Freeze a versioned benchmark profile without redefining older Gold semantics."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


SCHEMA = "rtl_benchmark_profile_v1"


def names(value: str) -> list[str]:
    return sorted({item.strip().lower() for item in value.split(",") if item.strip()})


def atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-root", type=Path, default=Path.home() / "work/data/rtl_corpus")
    parser.add_argument("--profile", default="rtl_benchmark_profile_v1")
    parser.add_argument("--active", default="verilog_eval,rtllm")
    parser.add_argument("--task-spec-only", default="hdlbits")
    parser.add_argument("--not-applicable", default="internal_reserved")
    parser.add_argument("--ambiguous-source", default="verilogbench")
    args = parser.parse_args()
    root = args.corpus_root / "benchmark_registry"
    catalog_path = root / "registry_catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    active = names(args.active)
    entries: dict[str, Any] = {}
    for name in active:
        source = catalog.get("entries", {}).get(name, {})
        entries[name] = {**source, "profile_status": "ACTIVE", "required_for_audit": True}
    for name in names(args.task_spec_only):
        entries[name] = {**catalog.get("entries", {}).get(name, {}), "profile_status": "TASK_SPEC_ONLY", "required_for_audit": False, "reason": "no authoritative bulk reference-solution corpus"}
    for name in names(args.not_applicable):
        entries[name] = {**catalog.get("entries", {}).get(name, {}), "profile_status": "NOT_APPLICABLE_TO_PROFILE", "required_for_audit": False, "reason": "no independent reserved benchmark exists for this profile"}
    for name in names(args.ambiguous_source):
        entries[name] = {**catalog.get("entries", {}).get(name, {}), "profile_status": "AMBIGUOUS_SOURCE", "required_for_audit": False, "reason": "no authoritative source selected for this profile"}
    ready = all(
        entries[name].get("status") == "ACTIVE"
        and bool(entries[name].get("snapshot_hash"))
        and int(entries[name].get("source_artifact_count", entries[name].get("fingerprints", 0))) > 0
        for name in active
    )
    profile = {
        "schema": SCHEMA, "profile_id": args.profile, "ready": ready,
        "active_benchmarks": active, "entries": entries,
        "gold_semantics": "PASS contamination audit against this immutable profile",
    }
    atomic(root / "profiles" / f"{args.profile}.json", profile)
    catalog["active_profile"] = args.profile
    catalog["active_profile_ready"] = ready
    atomic(catalog_path, catalog)
    print(json.dumps(profile, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
