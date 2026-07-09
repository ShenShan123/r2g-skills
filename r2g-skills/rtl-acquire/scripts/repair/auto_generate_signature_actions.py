#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path


import sys

_SKILL_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SKILL_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS_DIR))
from skill_env import (
    out_root_path,
    workspace_path,
)

DEFAULT_SIGNATURES = workspace_path("failures/failure_signatures.json")
DEFAULT_INDEX = out_root_path("index.csv")
DEFAULT_OUT_CSV = workspace_path("failures/failure_signature_action_candidates.csv")
DEFAULT_OUT_MD = workspace_path("failures/failure_signature_action_candidates.md")
DEFAULT_ACTIONS = workspace_path("failures/failure_signature_actions.json")


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_index_notes(path: Path) -> dict[str, dict]:
    rows = {}
    if not path.exists():
        return rows
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row.get("status") == "success":
                continue
            design = row.get("design", "")
            if design:
                rows[design] = row
    return rows


def infer_actions(notes: str) -> tuple[list[str], float, str]:
    lower = notes.lower()
    actions: list[str] = []
    reason = ""
    has_simtask = "unrecognized format character" in lower or ("system task" in lower and "$display" in lower)
    has_abstract_redef = "re-definition of module `$abstract" in lower
    if "vhdl" in lower or "entity" in lower or "architecture" in lower:
        actions.append("vhd2vl")
        reason = "vhdl signature"
    if "/vtr-verilog-to-routing-min/odin_ii/regression_test/benchmark/verilog/" in lower and any(
        token in lower for token in ("/keywords/", "/preprocessor/", "/syntax/")
    ):
        actions.append("exclude")
        reason = reason or "vtr invalid regression benchmark"
    if ("systemverilog" in lower or "syntax error" in lower or "front-end" in lower) and not (has_simtask or has_abstract_redef):
        actions.append("sv2v")
        reason = reason or "sv frontend signature"
    if "can't open include file" in lower or "cannot open include file" in lower:
        actions.append("resolve_include")
        reason = reason or "missing include"
    if "%%" in notes or "invalid name for macro definition" in lower:
        actions.append("template_materialization")
        reason = reason or "template placeholder materialization"
    if "invalid name for macro definition" in lower:
        actions.append("sanitize_macro_definition")
        reason = reason or "illegal macro identifier"
    if has_simtask:
        actions.append("sanitize_simulation_system_tasks")
        reason = reason or "simulation-only system task formatting"
    if "can't resolve task name `$" in lower:
        actions.append("sanitize_simulation_system_tasks")
        reason = reason or "unresolved simulation-only system task"
    if has_abstract_redef:
        actions.append("recover_original_bundle_and_sanitize_simtasks")
        reason = reason or "recover original bundle from sv2v-derived artifact"
    if "procedural for-loop" in lower and "not constant" in lower or "2nd expression of procedura" in lower:
        actions.append("rewrite_nonconstant_procedural_loop")
        reason = reason or "non-constant procedural loop"
    if "multiple edge sensitive events found for this signal" in lower:
        actions.append("exclude")
        reason = reason or "non-synthesizable multi-edge event control"
    if "does not have a port named" in lower:
        actions.append("exclude")
        reason = reason or "invalid named-port instantiation"
    if "is connected to constants" in lower:
        actions.append("exclude")
        reason = reason or "invalid output-to-constant connection"
    if "multiple conflicting drivers" in lower or "found 1 problems in 'check -assert'" in lower:
        actions.append("exclude")
        reason = reason or "structural driver conflict"
    if "synth_memory_max_bits" in lower or "synthesized memory size" in lower:
        actions.append("set_mem_limit:131072")
        reason = reason or "memory limit"
    if "module `" in lower and ("not part of the design" in lower or "not found" in lower):
        actions.append("stub_module")
        reason = reason or "missing module"
    if "unimplemented compiler directive" in lower or "undefined macro" in lower:
        actions.append("exclude")
        reason = reason or "macro/pp failure"
    if "re-definition of module" in lower and "$abstract\\dff" in lower:
        actions.append("exclude")
        reason = reason or "helper collision"
    confidence = 0.8 if actions else 0.0
    return list(dict.fromkeys(actions)), confidence, reason


def main() -> None:
    parser = argparse.ArgumentParser(description="Auto-generate signature actions for high-frequency failures.")
    parser.add_argument("--signatures-json", type=Path, default=DEFAULT_SIGNATURES)
    parser.add_argument("--index-csv", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--out-csv", type=Path, default=DEFAULT_OUT_CSV)
    parser.add_argument("--out-md", type=Path, default=DEFAULT_OUT_MD)
    parser.add_argument("--actions-json", type=Path, default=DEFAULT_ACTIONS)
    parser.add_argument("--min-count", type=int, default=5)
    parser.add_argument("--apply", action="store_true", help="Write inferred actions into failure_signature_actions.json.")
    args = parser.parse_args()

    signatures = load_json(args.signatures_json)
    index_rows = load_index_notes(args.index_csv)
    existing_actions = load_json(args.actions_json)
    candidates = []

    for sig, entry in signatures.items():
        count = int(entry.get("count", 0) or 0)
        if count < args.min_count:
            continue
        if sig in existing_actions:
            continue
        design = entry.get("example_design", "")
        notes = entry.get("example_notes", "")
        if design and design in index_rows:
            notes = index_rows[design].get("notes", notes)
        actions, confidence, reason = infer_actions(notes or "")
        candidates.append(
            {
                "signature": sig,
                "count": count,
                "example_design": design,
                "actions": ";".join(actions),
                "confidence": f"{confidence:.2f}",
                "reason": reason,
            }
        )
        if args.apply and actions:
            existing_actions[sig] = {
                "count": count,
                "actions": actions,
                "example_design": design,
                "reason": reason,
            }

    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.out_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["signature", "count", "example_design", "actions", "confidence", "reason"],
        )
        writer.writeheader()
        writer.writerows(candidates)

    with args.out_md.open("w", encoding="utf-8") as fh:
        fh.write("# Failure Signature Action Candidates\n\n")
        fh.write(f"- generated_at: {now_iso()}\n")
        fh.write(f"- candidate_count: {len(candidates)}\n\n")
        fh.write("| signature | count | example_design | actions | confidence | reason |\n")
        fh.write("|---|---:|---|---|---:|---|\n")
        for row in candidates:
            fh.write(
                f"| {row['signature']} | {row['count']} | {row['example_design']} | {row['actions']} | "
                f"{row['confidence']} | {row['reason']} |\n"
            )

    if args.apply:
        args.actions_json.write_text(json.dumps(existing_actions, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"wrote {args.out_csv}")
    print(f"wrote {args.out_md}")
    if args.apply:
        print(f"updated {args.actions_json}")


if __name__ == "__main__":
    main()
