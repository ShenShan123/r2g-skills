#!/usr/bin/env python3
"""Build a formal Experiment-1 submission from R2G-qualified Expander RTL."""

from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

ACQUIRE_DIR = Path(__file__).resolve().parent
if str(ACQUIRE_DIR) not in sys.path:
    sys.path.insert(0, str(ACQUIRE_DIR))
from import_expander_snapshot import _verified_bridge  # noqa: E402


SCHEMA = "r2g_expander_qualified_selection_v2"


class SelectionError(ValueError):
    """The requested selection cannot be reproduced safely."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise SelectionError(f"CSV has no header: {path}")
        return list(reader.fieldnames), list(reader)


def write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({key: row.get(key, "") for key in fields} for row in rows)
    os.replace(temporary, path)


def load_bridge(rows: list[dict[str, str]]) -> tuple[Path, dict[str, dict[str, Any]]]:
    paths = {
        Path(os.path.expanduser(os.path.expandvars(row.get("expander_bridge_manifest", "")))).resolve()
        for row in rows if row.get("expander_bridge_manifest")
    }
    if len(paths) != 1:
        raise SelectionError("candidate CSV must bind exactly one Expander bridge")
    path = next(iter(paths))
    payload = verify_bridge_path(path)
    candidates = payload.get("candidates") or []
    by_design = {str(item.get("design") or ""): item for item in candidates}
    if not by_design or len(by_design) != len(candidates):
        raise SelectionError("Expander bridge has empty or duplicate design identities")
    return path, by_design


def verify_bridge_path(path: Path) -> dict[str, Any]:
    """Rebind the bridge to its certified release at selection time."""
    return _verified_bridge(str(path), sha256_file(path))


def load_index(path: Path) -> dict[str, dict[str, str]]:
    _fields, rows = read_csv(path)
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        design = row.get("design", "")
        if not design or design in result:
            raise SelectionError("qualification index has empty or duplicate design identity")
        result[design] = row
    return result


def normalized_repo(value: str) -> str:
    return value.strip().rstrip("/").removesuffix(".git").lower()


def effective_count(counts: Counter[str]) -> float:
    total = sum(counts.values())
    if not total:
        return 0.0
    hhi = sum((count / total) ** 2 for count in counts.values())
    return 1.0 / hhi if hhi else 0.0


def size_bucket(cells: int) -> str:
    if cells < 1000:
        return "small_100_999"
    if cells < 10000:
        return "medium_1000_9999"
    return "large_10000_99999"


FUNCTION_PATTERNS = (
    ("crypto", r"sha|aes|crypto|cipher|chacha|keccak|ntt|huffman"),
    ("communication", r"ethernet|eth|uart|spi|i2c|icmp|ipv4|tcp|phy|jesd"),
    ("dsp", r"fir|cordic|fft|filter|mac|mult|accum|dsp"),
    ("ai_accelerator", r"systolic|conv|pool|dense|tensor|neural|accelerator"),
    ("memory", r"ram|sram|fifo|cache|memory|mem_"),
    ("bus_interface", r"axi|apb|wishbone|wb_|bus|bridge"),
    ("control", r"controller|control|ctrl|fsm|timer|pwm"),
    ("processor", r"cpu|risc|processor|pipeline|\balu\b|decode|execute"),
)


def functional_category(row: dict[str, str], bound: dict[str, Any]) -> str:
    # The digest-bound bridge is authoritative. The CSV field is only a
    # human-readable projection and must not be able to game selection.
    declared = (str(bound.get("function_category") or "") or
                row.get("function_category") or "").strip()
    if declared:
        return re.sub(r"[^a-z0-9_]+", "_", declared.lower()).strip("_") or "other"
    text = " ".join((row.get("expected_top", ""), row.get("notes", ""),
                     str(bound.get("repository_url") or ""))).lower()
    for category, pattern in FUNCTION_PATTERNS:
        if re.search(pattern, text):
            return category
    return "other"


def selection_key(row: dict[str, Any], repo_counts: Counter[str],
                  category_counts: Counter[str], size_counts: Counter[str]) -> tuple[Any, ...]:
    priority = {"high": 0, "medium": 1, "low": 2}.get(str(row.get("priority", "")), 3)
    return (
        repo_counts[row["_repository"]],
        category_counts[row["_function_category"]],
        size_counts[row["_size_bucket"]],
        priority,
        row["design"],
    )


def select(
    candidate_csv: Path,
    qualification_index: Path,
    out_root: Path,
    output_csv: Path,
    manifest_path: Path,
    *,
    target: int,
    minimum_cells: int,
    maximum_cells_exclusive: int,
    max_per_repository: int,
    platform: str,
    large_design_csv: Path | None = None,
) -> dict[str, Any]:
    fields, rows = read_csv(candidate_csv)
    bridge_path, bridge = load_bridge(rows)
    index = load_index(qualification_index)
    eligible: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    large_rows: list[dict[str, str]] = []
    seen_families: set[str] = set()

    for row in rows:
        design = row.get("design", "")
        evidence = index.get(design)
        bound = bridge.get(design)
        reason: str | None = None
        cells: int | None = None
        if bound is None:
            raise SelectionError(f"candidate is absent from Expander bridge: {design}")
        if evidence is None:
            reason = "qualification_missing"
        elif evidence.get("status") != "success":
            reason = f"qualification_{evidence.get('status') or 'unknown'}"
        else:
            try:
                cells = int(evidence.get("cells") or "")
            except ValueError:
                reason = "mapped_cells_invalid"
        meta: dict[str, Any] = {}
        if reason is None:
            try:
                meta = json.loads((out_root / design / "design_meta.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                reason = "qualification_metadata_missing"
        unresolved = ((meta.get("compile_manifest") or {}).get("unresolved_collateral") or [])
        if reason is None and str(meta.get("platform") or "") != platform:
            reason = "qualification_platform_mismatch"
        if reason is None and unresolved:
            reason = "compilation_closure_incomplete"
        if reason is None and cells is not None and cells < minimum_cells:
            reason = "trivial_design"
        if reason is None and cells is not None and cells >= maximum_cells_exclusive:
            reason = "oversize_design"
            large_rows.append(row)

        family = str(bound.get("family_id") or "")
        if reason is None and (not family or family in seen_families):
            reason = "family_duplicate" if family else "family_identity_missing"
        if reason is not None:
            excluded.append({"design": design, "reason": reason, "mapped_cells": cells})
            continue
        seen_families.add(family)
        enriched: dict[str, Any] = dict(row)
        enriched["_repository"] = normalized_repo(str(bound.get("repository_url") or ""))
        enriched["_mapped_cells"] = cells
        enriched["_size_bucket"] = size_bucket(cells or 0)
        enriched["_function_category"] = functional_category(row, bound)
        if not enriched["_repository"]:
            excluded.append({"design": design, "reason": "repository_identity_missing",
                             "mapped_cells": cells})
            continue
        eligible.append(enriched)

    selected: list[dict[str, Any]] = []
    remaining = list(eligible)
    repo_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    size_counts: Counter[str] = Counter()
    while remaining and len(selected) < target:
        allowed = [row for row in remaining
                   if repo_counts[row["_repository"]] < max_per_repository]
        if not allowed:
            break
        chosen = min(allowed, key=lambda row: selection_key(
            row, repo_counts, category_counts, size_counts))
        selected.append(chosen)
        remaining.remove(chosen)
        repo_counts[chosen["_repository"]] += 1
        category_counts[chosen["_function_category"]] += 1
        size_counts[chosen["_size_bucket"]] += 1

    for row in remaining:
        excluded.append({
            "design": row["design"],
            "reason": ("repository_cap" if repo_counts[row["_repository"]] >= max_per_repository
                       else "target_capacity"),
            "mapped_cells": row["_mapped_cells"],
        })

    write_csv(output_csv, fields, selected)
    if large_design_csv is not None:
        write_csv(large_design_csv, fields, large_rows)
    reason_counts = Counter(str(item["reason"]) for item in excluded)
    payload = {
        "schema": SCHEMA,
        "candidate_csv": str(candidate_csv.resolve()),
        "candidate_csv_sha256": sha256_file(candidate_csv),
        "expander_bridge": str(bridge_path),
        "expander_bridge_sha256": sha256_file(bridge_path),
        "qualification_index": str(qualification_index.resolve()),
        "qualification_index_sha256": sha256_file(qualification_index),
        "out_root": str(out_root.resolve()),
        "policy": {
            "target": target,
            "minimum_mapped_cells": minimum_cells,
            "maximum_mapped_cells_exclusive": maximum_cells_exclusive,
            "max_candidates_per_repository": max_per_repository,
            "platform": platform,
            "require_status": "success",
            "require_complete_compilation_collateral": True,
            "selection": "deterministic_greedy_repository_function_size_balance",
        },
        "eligible_before_diversity_selection": len(eligible),
        "selected_count": len(selected),
        "target_met": len(selected) == target,
        "selected_designs": [row["design"] for row in selected],
        "selected_repository_counts": dict(sorted(repo_counts.items())),
        "selected_function_category_counts": dict(sorted(category_counts.items())),
        "selected_size_bucket_counts": dict(sorted(size_counts.items())),
        "selected_effective_repository_count": effective_count(repo_counts),
        "excluded_reason_counts": dict(sorted(reason_counts.items())),
        "excluded": excluded,
        "output_csv": str(output_csv.resolve()),
        "output_csv_sha256": sha256_file(output_csv),
        "large_design_csv": str(large_design_csv.resolve()) if large_design_csv else None,
        "large_design_count": len(large_rows),
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = manifest_path.with_name(f".{manifest_path.name}.tmp.{os.getpid()}")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, manifest_path)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-csv", type=Path, required=True)
    parser.add_argument("--qualification-index", type=Path, required=True)
    parser.add_argument("--out-root", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--selection-manifest", type=Path, required=True)
    parser.add_argument("--large-design-csv", type=Path)
    parser.add_argument("--target", type=int, default=25)
    parser.add_argument("--minimum-mapped-cells", type=int, default=100)
    parser.add_argument("--maximum-mapped-cells-exclusive", type=int, default=100000)
    parser.add_argument("--max-candidates-per-repository", type=int, default=4)
    parser.add_argument("--platform", default="sky130hd")
    parser.add_argument("--require-target", action="store_true")
    args = parser.parse_args()
    try:
        if args.target < 1 or args.minimum_mapped_cells < 1:
            raise SelectionError("target and minimum mapped cells must be positive")
        if args.maximum_mapped_cells_exclusive <= args.minimum_mapped_cells:
            raise SelectionError("maximum mapped cells must exceed the minimum")
        if args.max_candidates_per_repository < 1:
            raise SelectionError("repository cap must be positive")
        result = select(
            args.candidate_csv.resolve(), args.qualification_index.resolve(),
            args.out_root.resolve(), args.output_csv.resolve(),
            args.selection_manifest.resolve(), target=args.target,
            minimum_cells=args.minimum_mapped_cells,
            maximum_cells_exclusive=args.maximum_mapped_cells_exclusive,
            max_per_repository=args.max_candidates_per_repository,
            platform=args.platform,
            large_design_csv=(args.large_design_csv.resolve()
                              if args.large_design_csv else None),
        )
    except (OSError, SelectionError, json.JSONDecodeError) as exc:
        print(f"Expander qualification selection rejected: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    if args.require_target and not result["target_met"]:
        print(f"formal target not met: {result['selected_count']}/{args.target}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
