#!/usr/bin/env python3
"""Run a deterministic stratified Nangate45 mapping validation cohort."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import time
import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from functional_ontology import ONTOLOGY_SCHEMA, classify as classify_function


COHORT_SCHEMA = "rtl_mapping_cohort_v1"
MAPPING_SCHEMA = "rtl_nangate45_mapping_v2"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return rows


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_key(record: dict[str, Any], seed: str) -> str:
    return hashlib.sha256((seed + "\0" + record["family_id"] + "\0" + record["design_id"]).encode()).hexdigest()


def representative_by_family(designs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    families: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in designs:
        if row.get("synthesis", {}).get("generic_pass") and Path(row.get("synthesis", {}).get("generic_netlist", "")).is_file():
            families[row["family_id"]].append(row)
    representatives: list[dict[str, Any]] = []
    for rows in families.values():
        rows.sort(key=lambda row: (
            -int(row.get("rtl_semantics", {}).get("module_count", 0)),
            -int(row.get("rtl_semantics", {}).get("hierarchy_edge_count", 0)), row["design_id"],
        ))
        representatives.append(rows[0])
    return representatives


def select_cohort(records: list[dict[str, Any]], count: int, seed: str) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        languages = "+".join(sorted(row.get("source", {}).get("source_languages", []))) or "unknown"
        bucket = (row.get("resource", {}).get("class", "UNKNOWN"), languages, classify_function(row)["label"])
        buckets[bucket].append(row)
    for rows in buckets.values():
        rows.sort(key=lambda row: stable_key(row, seed))
    per_resource: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for resource in ("TINY", "SMALL", "MEDIUM", "LARGE", "XLARGE"):
        active = [key for key in sorted(buckets) if key[0] == resource]
        while any(buckets[key] for key in active):
            for key in active:
                if buckets[key]:
                    per_resource[resource].append(buckets[key].pop(0))
    resources = ("TINY", "SMALL", "MEDIUM", "LARGE", "XLARGE")
    target = min(count, len(records))
    weights = {"TINY": 0.25, "SMALL": 0.25, "MEDIUM": 0.25, "LARGE": 0.15, "XLARGE": 0.10}
    quotas = {resource: min(len(per_resource[resource]), int(target * weights[resource])) for resource in resources}
    while sum(quotas.values()) < target:
        progressed = False
        for resource in resources:
            if quotas[resource] < len(per_resource[resource]):
                quotas[resource] += 1
                progressed = True
                if sum(quotas.values()) >= target:
                    break
        if not progressed:
            break
    queues = {resource: per_resource[resource][:quotas[resource]] for resource in resources}
    selected: list[dict[str, Any]] = []
    while len(selected) < target:
        for resource in resources:
            if queues[resource]:
                selected.append(queues[resource].pop(0))
    return selected


def liberty_cells(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    return set(re.findall(r"(?m)^\s*cell\s*\(\s*([^\s)]+)", text))


def run_one(
    record: dict[str, Any], args: argparse.Namespace, lib_cells: set[str], liberty_hash: str,
    yosys_version: str, *, attempt_label: str | None = None, timeout_override: int | None = None,
) -> dict[str, Any]:
    design_id = record["design_id"]
    family_id = record["family_id"]
    top = record.get("build", {}).get("top_module", "")
    generic = Path(record.get("synthesis", {}).get("generic_netlist", ""))
    out_dir = args.corpus_root / "synthesis/mapped/cohort" / design_id
    if attempt_label:
        out_dir = out_dir / attempt_label
    out_dir.mkdir(parents=True, exist_ok=True)
    mapped = out_dir / "mapped.v"
    structural = out_dir / "mapped.json"
    log = out_dir / "yosys.log"
    script = out_dir / "map.ys"
    script_text = "\n".join([
        f"read_liberty -lib {args.liberty}", f"read_verilog -sv {generic}",
        f"hierarchy -check -top {top}", "flatten", "proc", "opt", "memory", "opt", "techmap", "opt",
        f"dfflibmap -liberty {args.liberty}", f"abc -liberty {args.liberty}", "clean -purge", "check",
        f"write_verilog -noattr -noexpr -nodec {mapped}", f"write_json {structural}",
        f"stat -liberty {args.liberty}", "",
    ])
    script.write_text(script_text, encoding="utf-8")
    timeout = timeout_override or {"TINY": 45, "SMALL": 75, "MEDIUM": 120, "LARGE": 180, "XLARGE": 240}.get(record.get("resource", {}).get("class"), 120)
    start = time.monotonic()
    process = subprocess.Popen(
        [args.yosys, "-s", str(script)], text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
    )
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(process.pid, signal.SIGTERM)
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
    runtime = round(time.monotonic() - start, 3)
    log.write_text(stdout + "\n" + stderr, encoding="utf-8")
    internal_cells = unknown_cells = total_cells = 0
    cell_types: Counter[str] = Counter()
    if structural.is_file():
        try:
            payload = json.loads(structural.read_text(encoding="utf-8"))
            module = payload.get("modules", {}).get(top, {})
            for cell in module.get("cells", {}).values():
                cell_type = str(cell.get("type", ""))
                cell_types[cell_type] += 1
            total_cells = sum(cell_types.values())
            internal_cells = sum(
                count for name, count in cell_types.items()
                if (name.startswith("$") or name.startswith("$_")) and name not in {"$scopeinfo"}
            )
            unknown_cells = sum(count for name, count in cell_types.items() if name not in lib_cells and not name.startswith("$") and not name.startswith("$_"))
        except (OSError, json.JSONDecodeError):
            pass
    passed = process.returncode == 0 and mapped.is_file() and structural.is_file() and internal_cells == 0
    status = "SYNTH_COMPLETE" if passed and unknown_cells == 0 else "SYNTH_MACRO_PRESERVED" if passed else "MAPPING_FAIL"
    reason = "TIMEOUT" if timed_out else "PASS" if passed else "YOSYS_ERROR" if process.returncode else "INTERNAL_CELLS_REMAIN"
    return {
        "schema": MAPPING_SCHEMA, "cohort_schema": COHORT_SCHEMA,
        "design_id": design_id, "family_id": family_id, "top_module": top,
        "resource_class": record.get("resource", {}).get("class"),
        "source_languages": record.get("source", {}).get("source_languages", []),
        "functional_ontology": classify_function(record), "status": status, "reason": reason,
        "mapping_pass": passed, "runtime_seconds": runtime, "timeout_seconds": timeout,
        "mapped_netlist": str(mapped) if mapped.is_file() else None,
        "mapped_netlist_sha256": sha256(mapped) if mapped.is_file() else None,
        "mapped_json": str(structural) if structural.is_file() else None,
        "mapped_cell_count": total_cells, "internal_cell_count": internal_cells,
        "unknown_or_macro_cell_count": unknown_cells, "cell_types": dict(cell_types),
        "yosys": args.yosys, "yosys_version": yosys_version,
        "liberty": str(args.liberty), "liberty_sha256": liberty_hash,
        "script_sha256": hashlib.sha256(script_text.encode()).hexdigest(), "log_path": str(log),
        "attempt_label": attempt_label or "initial",
    }


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-root", type=Path, default=Path.home() / "work/data/rtl_corpus")
    parser.add_argument("--liberty", type=Path, default=Path.home() / "work/openroad/OpenROAD-flow-scripts/flow/platforms/nangate45/lib/NangateOpenCellLibrary_typical.lib")
    parser.add_argument("--yosys", default="/opt/OpenROAD/oss-cad-suite/bin/yosys")
    parser.add_argument("--count", type=int, default=150)
    parser.add_argument("--seed", default="rtl_phase1_5_mapping_cohort_v1")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--large-workers", type=int, default=2)
    args = parser.parse_args()
    if not args.liberty.is_file():
        raise SystemExit(f"liberty not found: {args.liberty}")
    designs = read_jsonl(args.corpus_root / "manifests/all_designs.jsonl")
    selected = select_cohort(representative_by_family(designs), args.count, args.seed)
    selected_ids = {row["design_id"] for row in selected}
    output = args.corpus_root / "quality/phase1_5/mapping_cohort.jsonl"
    existing = {
        row["design_id"]: row for row in read_jsonl(output)
        if row.get("schema") == MAPPING_SCHEMA and row.get("design_id") in selected_ids
    } if args.resume and output.exists() else {}
    lib_hash = sha256(args.liberty)
    cells = liberty_cells(args.liberty)
    version = subprocess.run([args.yosys, "-V"], text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=10).stdout.strip()
    pending = [(index, record) for index, record in enumerate(selected, 1) if record["design_id"] not in existing]
    output_lock = threading.Lock()
    def execute(item: tuple[int, dict[str, Any]]) -> tuple[str, dict[str, Any]]:
        index, record = item
        print(f"[map {index}/{len(selected)}] {record['design_id']} {record['family_id']}", flush=True)
        result = run_one(record, args, cells, lib_hash, version)
        return record["design_id"], result

    regular = [item for item in pending if item[1].get("resource", {}).get("class") not in {"LARGE", "XLARGE"}]
    large = [item for item in pending if item[1].get("resource", {}).get("class") in {"LARGE", "XLARGE"}]
    with ThreadPoolExecutor(max_workers=max(1, args.workers - args.large_workers)) as regular_executor, ThreadPoolExecutor(max_workers=max(1, args.large_workers)) as large_executor:
        futures = [regular_executor.submit(execute, item) for item in regular]
        futures.extend(large_executor.submit(execute, item) for item in large)
        for future in as_completed(futures):
            design_id, result = future.result()
            with output_lock:
                existing[design_id] = result
                atomic_write(output, "".join(json.dumps(row, sort_keys=True) + "\n" for row in sorted(existing.values(), key=lambda row: row["design_id"])))
    rows = [existing[row["design_id"]] for row in selected if row["design_id"] in existing]
    summary = {
        "schema": COHORT_SCHEMA, "seed": args.seed, "selected_families": len(selected),
        "completed": len(rows), "status": dict(Counter(row["status"] for row in rows)),
        "reasons": dict(Counter(row["reason"] for row in rows)),
        "resource_classes": dict(Counter(row["resource_class"] for row in rows)),
        "languages": dict(Counter("+".join(row["source_languages"]) for row in rows)),
        "functional_categories": dict(Counter(row["functional_ontology"]["label"] for row in rows)),
        "initial_mapping_pass_rate": round(sum(row["mapping_pass"] for row in rows) / max(1, len(rows)), 6),
        "final_mapping_pass_rate": None,
        "mapped_cells": sum(row["mapped_cell_count"] for row in rows),
        "ontology_schema": ONTOLOGY_SCHEMA, "liberty_sha256": lib_hash, "yosys_version": version,
    }
    atomic_write(args.corpus_root / "quality/phase1_5/mapping_cohort_summary.json", json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
