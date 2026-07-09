#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import re
from collections import Counter, defaultdict
from pathlib import Path


import sys

_SKILL_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SKILL_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS_DIR))
from skill_env import (
    out_root_path,
    workspace_path,
)

DEFAULT_INDEX = out_root_path("index.csv")
DEFAULT_OUT_CSV = workspace_path("failures/failure_knowledge_base_candidates.csv")
DEFAULT_OUT_MD = workspace_path("failures/failure_knowledge_base_candidates.md")


def note_signature(notes: str) -> tuple[str, str, str]:
    text = (notes or "").strip()
    lower = text.lower()

    patterns: list[tuple[str, str, str]] = [
        ("memory_limit", r"synthesized memory size \d+ exceeds synth_memory_max_bits", "raise or propagate SYNTH_MEMORY_MAX_BITS and retry scoped high-value designs"),
        ("missing_module", r"module `\\[^`]+'? referenced .* not part of the design", "preserve canonical rtl_files/include_dirs bundle and retry"),
        ("missing_include", r"can't open include file [`'][^`']+[`']!?", "preserve include_dirs and required config/header files in bundle-aware candidates or skip front-end-heavy designs"),
        ("helper_collision", r"re-definition of module `\$abstract\\dff'", "skip low-value helper-collision cases or de-duplicate helper generation"),
        ("undefined_macro", r"unimplemented compiler directive or undefined macro", "exclude placeholder/preprocessor-heavy RTL unless worth custom front-end handling"),
        ("top_not_found", r"module `\\[^`]+'? .* not found|top.*not found", "fix expected_top or multi-file bundle before retry"),
        ("graph_empty", r"no graph nodes were created from mapped netlist", "check mapped netlist validity and skip trivial/invalid designs"),
    ]
    for category, pattern, repair in patterns:
        if re.search(pattern, lower):
            return category, re.search(pattern, lower).group(0), repair

    if "synthesis_failed" in lower:
        return "generic_synth_failed", "synthesis_failed", "inspect synth.log and classify before retry"
    if "graph_failed" in lower:
        return "generic_graph_failed", "graph_failed", "inspect graph conversion log and mapped netlist"
    interesting_lines = []
    for line in text.split("|"):
        candidate = line.strip()
        candidate_lower = candidate.lower()
        if not candidate:
            continue
        if any(token in candidate_lower for token in ("error:", "runtimeerror", "traceback", "not part of the design", "not found", "failed", "undefined macro")):
            interesting_lines.append(candidate_lower)
    short = (interesting_lines[0] if interesting_lines else lower.split("|", 1)[0].strip())[:160] or "unknown_failure"
    return "unclassified", short, "manual review needed"


def source_family(source_path: str) -> str:
    text = (source_path or "").replace("\\", "/")
    parts = [p for p in text.split("/") if p]
    if "_downloads" in parts:
        idx = parts.index("_downloads")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return parts[-2] if len(parts) >= 2 else "unknown"


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract recurring failure patterns into reviewable knowledge-base candidates.")
    parser.add_argument("--index-csv", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--out-csv", type=Path, default=DEFAULT_OUT_CSV)
    parser.add_argument("--out-md", type=Path, default=DEFAULT_OUT_MD)
    args = parser.parse_args()

    rows = list(csv.DictReader(args.index_csv.open(newline="", encoding="utf-8")))
    failed = [r for r in rows if r.get("status") != "success"]

    grouped: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in failed:
        category, signature, repair = note_signature(row.get("notes", ""))
        item = dict(row)
        item["_category"] = category
        item["_signature"] = signature
        item["_repair"] = repair
        grouped[(category, signature)].append(item)

    out_rows: list[dict[str, str]] = []
    for (category, signature), items in sorted(grouped.items(), key=lambda kv: (-len(kv[1]), kv[0][0], kv[0][1])):
        families = Counter(source_family(i.get("source_path", "")) for i in items)
        example = items[0]
        out_rows.append(
            {
                "category": category,
                "signature": signature,
                "count": str(len(items)),
                "example_design": example.get("design", ""),
                "example_status": example.get("status", ""),
                "example_source_path": example.get("source_path", ""),
                "top_families": "; ".join(f"{name}:{count}" for name, count in families.most_common(4)),
                "suggested_repair": example["_repair"],
                "promote_to_kb": "review" if len(items) >= 2 else "maybe",
            }
        )

    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.out_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "category",
                "signature",
                "count",
                "example_design",
                "example_status",
                "example_source_path",
                "top_families",
                "suggested_repair",
                "promote_to_kb",
            ],
        )
        writer.writeheader()
        writer.writerows(out_rows)

    with args.out_md.open("w", encoding="utf-8") as fh:
        fh.write("# Failure Knowledge Base Candidates\n\n")
        fh.write(f"- source_index: {args.index_csv}\n")
        fh.write(f"- failed_rows: {len(failed)}\n")
        fh.write(f"- distinct_patterns: {len(out_rows)}\n\n")
        fh.write("| category | count | top_families | example_design | suggested_repair | promote_to_kb |\n")
        fh.write("|---|---:|---|---|---|---|\n")
        for row in out_rows[:50]:
            fh.write(
                f"| {row['category']} | {row['count']} | {row['top_families']} | {row['example_design']} | {row['suggested_repair']} | {row['promote_to_kb']} |\n"
            )

    print(f"wrote {args.out_csv} rows={len(out_rows)}")
    print(f"wrote {args.out_md}")


if __name__ == "__main__":
    main()
