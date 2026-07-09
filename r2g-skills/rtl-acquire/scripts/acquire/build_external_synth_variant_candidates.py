#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


import sys

_SKILL_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SKILL_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS_DIR))
from skill_env import (
    default_out_root,
    downloads_path,
    skill_reference_path,
    work_path,
    workspace_path,
)

DEFAULT_SOURCE_ROOT = default_out_root()
DEFAULT_SOURCE_INDEX = DEFAULT_SOURCE_ROOT / "index.csv"
DEFAULT_OUT_CSV = workspace_path("candidates/external_success_area_variant_candidates_2026-04-13.csv")
DEFAULT_OUT_MD = workspace_path("candidates/external_success_area_variant_candidates_2026-04-13.md")
DEFAULT_POLICY_JSON = skill_reference_path("synth_variant_policy.json")


def remap_legacy_source_path(path: Path) -> Path:
    text = str(path)
    replacements = (
        (
            str(work_path("vtr-verilog-to-routing-min")) + "/",
            str(downloads_path("vtr-verilog-to-routing-min")) + "/",
        ),
        (
            str(work_path("hdl-benchmarks-min")) + "/",
            str(downloads_path("hdl-benchmarks-min")) + "/",
        ),
    )
    for old, new in replacements:
        if text.startswith(old):
            return Path(new + text[len(old) :])
    return path


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def pick_priority(cells: str) -> str:
    try:
        count = int(cells)
    except Exception:
        return "medium"
    if count >= 10_000:
        return "high"
    if count >= 1_000:
        return "medium"
    return "low"


def load_policy(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def row_matches_strategy(row: dict[str, str], strategy: dict, suffix_mix: set[str], notes: str) -> bool:
    if not bool(strategy.get("enabled", True)):
        return False
    required_suffixes = set(strategy.get("require_suffix_any") or [])
    if required_suffixes and not (suffix_mix & required_suffixes):
        return False
    source_path = str(row.get("source_path", "") or "")
    source_regex = str(strategy.get("source_path_regex") or "").strip()
    if source_regex:
        import re
        if not re.search(source_regex, source_path, flags=re.IGNORECASE):
            return False
    notes_regex = str(strategy.get("notes_regex") or "").strip()
    if notes_regex:
        import re
        if not re.search(notes_regex, notes or "", flags=re.IGNORECASE):
            return False
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--source-index", type=Path, default=DEFAULT_SOURCE_INDEX)
    parser.add_argument("--out-csv", type=Path, default=DEFAULT_OUT_CSV)
    parser.add_argument("--out-md", type=Path, default=DEFAULT_OUT_MD)
    parser.add_argument("--source-name", default="external_success_variant")
    parser.add_argument("--synth-variant", default="yosys_abc_area1")
    parser.add_argument("--design-suffix", default="__area1")
    parser.add_argument("--variant-policy-json", type=Path, default=DEFAULT_POLICY_JSON)
    args = parser.parse_args()

    policy = load_policy(args.variant_policy_json)
    strategies = policy.get("strategies") or [
        {
            "name": "single",
            "synth_variant": args.synth_variant,
            "design_suffix": args.design_suffix,
            "notes_tag": "",
            "frontend": "",
            "enabled": True,
        }
    ]

    rows_out: list[dict[str, str]] = []
    missing_rtl = 0
    success_rows = [row for row in csv.DictReader(args.source_index.open(encoding="utf-8")) if row.get("status") == "success"]
    for row in success_rows:
        base_design = row["design"]
        design_dir = args.source_root / base_design
        meta = load_json(design_dir / "design_meta.json")
        rtl_files = [remap_legacy_source_path(Path(p)) for p in meta.get("rtl_files", [])]
        if not rtl_files or any(not p.exists() for p in rtl_files):
            missing_rtl += 1
            continue
        top = str(meta.get("top") or row.get("top") or base_design)
        notes = str(meta.get("notes") or row.get("notes") or "")
        suffix_mix = {p.suffix.lower() for p in rtl_files}
        for strategy in strategies:
            if not row_matches_strategy(row, strategy, suffix_mix, notes):
                continue
            synth_variant = str(strategy.get("synth_variant") or args.synth_variant)
            design_suffix = str(strategy.get("design_suffix") or args.design_suffix)
            variant_design = f"{base_design}{design_suffix}"
            frontend = str(strategy.get("frontend") or "")
            notes_tag = str(strategy.get("notes_tag") or "")
            variant_notes = "; ".join(
                part
                for part in [
                    notes,
                    f"base_design={base_design}",
                    f"synth_variant={synth_variant}",
                    f"variant_strategy={strategy.get('name', '')}",
                    notes_tag,
                ]
                if part
            )
            rows_out.append(
                {
                    "source": args.source_name,
                    "design": variant_design,
                    "priority": pick_priority(row.get("cells", "")),
                    "expected_top": top,
                    "source_path": str(rtl_files[0]),
                    "rtl_files": ";".join(str(p) for p in rtl_files),
                    "notes": variant_notes,
                    "synth_variant": synth_variant,
                    "synth_frontend": frontend,
                    "base_design": base_design,
                    "equiv_class": base_design,
                    "variant_strategy": str(strategy.get("name") or ""),
                }
            )

    fieldnames = ["source", "design", "priority", "expected_top", "source_path", "rtl_files", "notes", "synth_variant", "synth_frontend", "base_design", "equiv_class", "variant_strategy"]
    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.out_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows_out)

    with args.out_md.open("w", encoding="utf-8") as fh:
        fh.write("# External Synth Variant Candidates\n\n")
        fh.write(f"- source_root: {args.source_root}\n")
        fh.write(f"- source_index: {args.source_index}\n")
        fh.write(f"- variant_policy_json: {args.variant_policy_json}\n")
        fh.write(f"- strategy_count: {len(strategies)}\n")
        fh.write(f"- candidate_count: {len(rows_out)}\n")
        fh.write(f"- missing_rtl_count: {missing_rtl}\n\n")
        fh.write("| design | base_design | priority | expected_top | synth_variant | variant_strategy |\n")
        fh.write("|---|---|---|---|---|---|\n")
        for row in rows_out[:50]:
            fh.write(f"| {row['design']} | {row['base_design']} | {row['priority']} | {row['expected_top']} | {row['synth_variant']} | {row['variant_strategy']} |\n")
        if len(rows_out) > 50:
            fh.write(f"\n... truncated, total {len(rows_out)} candidates ...\n")

    print(f"wrote {args.out_csv}")
    print(f"wrote {args.out_md}")
    print(f"candidate_count {len(rows_out)}")
    print(f"missing_rtl_count {missing_rtl}")


if __name__ == "__main__":
    main()
