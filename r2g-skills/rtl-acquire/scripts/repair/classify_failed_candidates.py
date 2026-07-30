#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path


import sys

_SKILL_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SKILL_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS_DIR))
from skill_env import (
    out_root_path,
    workspace_path,
)
from common.rtl_risk import ram_macro_risk_tokens

INDEX = out_root_path("index.csv")
OUT_RETRY = workspace_path("failures/failed_candidates_retry.csv")
OUT_EXCLUDE = workspace_path("failures/failed_candidates_exclude.csv")
OUT_RETRY_CANDIDATES = workspace_path("failures/failed_candidates_retry_candidates.csv")
# Deferred candidates are NOT excluded: discovery reads only the exclude list, so
# a deferred source stays eligible once the missing capability is installed
# (RMD-HO-P1-03). They are not auto-retried either — retrying the same missing
# tool would just burn the attempt budget.
OUT_DEFER = workspace_path("failures/failed_candidates_defer.csv")


def is_high_value_retry(source_path: str, notes: str) -> bool:
    source = (source_path or "").lower()
    text = (notes or "").lower()
    if "hdl-benchmarks-min/iscas89/verilog/s38584a.v" in source and "module `\\delta'" in text:
        return True
    if source.endswith("/common/adder.v") and "module `\\add_add_module'" in text:
        return True
    if source.endswith("/common/dff.v") and "module `top' not found" in text:
        return True
    return False


# --- terminal-class detectors (held-out V3 RMD-HO-P1-01 / -P1-03) ------------
#
# Before this, EVERY unrecognised synth failure fell through to
# `exclude, low_value_failure` — a PERMANENT verdict that also writes the source
# path into failed_candidates_exclude.csv, so normal acquisition never sees the
# design again. Three whole classes were being burned that way:
#
#   * memory_limit  — Yosys refused to infer a memory larger than
#     SYNTH_MEMORY_MAX_BITS. Mechanically recoverable by raising the cap
#     (auto_fix_failures already knows how); the held-out cohort proved mor1kx /
#     JPEG / audio all synthesize at a raised cap. RETRY.
#   * frontend_tool_unavailable — the SELECTED synthesis frontend is not
#     installed (no slang.so). A tool-capability gap says nothing about the RTL.
#     DEFER: keep the candidate, emit no negative source-quality evidence.
#   * tool_compatibility — the installed frontend rejects language-valid RTL
#     (Yosys treating the Verilog-2001 port name `int` as a keyword; an sv2v
#     rewrite changing constant-function semantics). DEFER, same reasoning.
#
# `diagnostic_incomplete` is the fourth: the expander could not resolve a log
# carrying the terminal error, so there is no evidence to classify ON. Retrying
# is honest; a permanent low-value verdict from absent evidence is not.
MEMORY_LIMIT_RE = re.compile(
    r"(?i)synthesized memory size\s+\d+\s+exceeds|exceeds\s+synth_memory_max_bits"
    r"|\bmemory_limit\s+observed_bits=")
# A missing plugin surfaces as a yosys `plugin -i` / command-not-found failure.
FRONTEND_UNAVAILABLE_RE = re.compile(
    r"(?i)can't load module [`']?[^`' ]*slang"
    r"|unable to load plugin"
    r"|plugin .*slang.* not found"
    r"|no such command: read_slang"
    r"|frontend_tool_unavailable")
# Language-valid RTL the installed frontend cannot digest. Kept NARROW on
# purpose: a generic "syntax error" stays a source failure, because most of them
# genuinely are. Only signatures an independent standards-aware frontend was
# observed to accept are listed (held-out V3 P1-HO-03: WB DMA's `int` port,
# APB4 GPIO's sv2v-mangled constant function).
TOOL_COMPATIBILITY_RE = re.compile(
    r"(?i)non-constant expression in constant function"
    r"|syntax error, unexpected TOK_INT"
    r"|unexpected TOK_(?:INT|LOGIC|BIT|STRING)\b"
    r"|tool_compatibility")
DIAGNOSTIC_INCOMPLETE_RE = re.compile(r"(?i)\bdiagnostic_incomplete\b")


def classify(source_path: str, notes: str) -> tuple[str, str]:
    text = (notes or "").lower()
    # Evidence-absent guard FIRST: with no terminal-error log there is nothing to
    # classify on, so no terminal verdict may be issued (RMD-HO-P1-01 part A).
    if DIAGNOSTIC_INCOMPLETE_RE.search(text):
        return "retry", "diagnostic_incomplete"
    # Recoverable resource guard — a raised cap is a mechanical fix, never a
    # statement about source quality.
    if MEMORY_LIMIT_RE.search(text):
        return "retry", "memory_limit"
    # Tool-capability gaps: DEFER (candidate preserved, no negative learning).
    if FRONTEND_UNAVAILABLE_RE.search(text):
        return "defer", "frontend_tool_unavailable"
    if TOOL_COMPATIBILITY_RE.search(text):
        return "defer", "tool_compatibility"
    # Truncated-closure retry (2026-07-16 full-pipeline issue 10): a candidate
    # whose discovery notes carry the bundle_incomplete marker failed synthesis
    # on a module that EXISTS in its own repo but was cut by the closure cap —
    # a pipeline artifact, not a low-value design. Retry (the emitter records
    # the unresolved list; re-discovery/expansion can rebuild the closure),
    # never a permanent low_value_failure exclusion.
    if "bundle_incomplete=" in text and (
            "not found" in text or "not part of the design" in text
            or "module `" in text):
        return "retry", "missing_local_module"
    # Same reasoning for a macro-indirect closure gap (RMD-FE-P1-01): discovery
    # already declared the closure incomplete, so a missing-module failure is a
    # KNOWN pipeline gap, not evidence about the source.
    if "closure_incomplete=" in text and (
            "not found" in text or "not part of the design" in text
            or "module `" in text):
        return "retry", "closure_incomplete"
    # Tokenized match on the FAILURE evidence (shared with discovery's risk
    # flagging — common/rtl_risk.py): the raw-substring version of this test
    # was the same false-positive bug that hard-rejected picorv32 upstream.
    # Memory tokens only — "blackbox" appears in benign yosys diagnostics.
    if ram_macro_risk_tokens(text, strip_comments=False,
                             tokens=("single_port_ram", "dual_port_ram",
                                     "fakeram", "sram")):
        return "exclude", "ram_or_macro_dependency"
    source = (source_path or "").lower()
    if ("invalid name for macro definition" in text or "%%" in text) and "vtr-verilog-to-routing-min/vtr_flow/benchmarks/arithmetic/adder_trees/verilog/adder_tree.v" in source:
        return "retry", "template_materialization_candidate"
    if "invalid name for macro definition" in text or "%%" in text:
        return "exclude", "template_placeholder"
    if "async reset" in text:
        return "exclude", "semantic_reset_issue"
    if "re-definition of module `$abstract" in text:
        return "retry", "frontend_sv2v_abstract_redefinition"
    if "unrecognized format character" in text or ("system task" in text and "$display" in text):
        return "retry", "simulation_system_task_sanitize"
    if is_high_value_retry(source_path, notes):
        if "module `" in text and "not part of the design" in text:
            return "retry", "missing_module_or_top_mismatch"
        if "not found" in text or "top" in text:
            return "retry", "possible_top_issue"
    if "module `" in text and "not part of the design" in text:
        return "exclude", "low_value_failure"
    if "syntax error" in text or "frontend" in text or "slang" in text:
        return "exclude", "low_value_failure"
    if "not found" in text or "top" in text:
        return "exclude", "low_value_failure"
    return "exclude", "low_value_failure"


def infer_source(source_path: str) -> str:
    text = (source_path or "").lower()
    if "hdl-benchmarks" in text:
        return "downloads_hdl_benchmarks"
    if "vtr-verilog-to-routing" in text:
        return "downloads_vtr"
    if "openroad-flow-scripts" in text:
        return "orfs_local"
    return "retry"


def retry_rank(design: str) -> tuple[int, int, str]:
    lowered = (design or "").lower()
    is_long_prefix = int(lowered.startswith("hdl_benchmarks_min_") or lowered.startswith("vtr_verilog_to_routing_min_"))
    return (is_long_prefix, len(design or ""), design or "")


def main() -> None:
    parser = argparse.ArgumentParser(description="Split failed external candidates into retry vs exclude lists.")
    parser.add_argument("--index", type=Path, default=INDEX)
    parser.add_argument("--out-retry", type=Path, default=OUT_RETRY)
    parser.add_argument("--out-exclude", type=Path, default=OUT_EXCLUDE)
    parser.add_argument("--out-retry-candidates", type=Path, default=OUT_RETRY_CANDIDATES)
    parser.add_argument("--out-defer", type=Path, default=OUT_DEFER)
    args = parser.parse_args()

    rows = list(csv.DictReader(args.index.open()))
    success_sources = {
        row.get("source_path", "")
        for row in rows
        if row.get("status") == "success" and row.get("source_path", "")
    }
    retry_rows_by_source: dict[str, dict] = {}
    retry_candidate_rows_by_source: dict[str, dict] = {}
    exclude_rows = []
    defer_rows = []

    for row in rows:
        if row["status"] == "success":
            continue
        source_path = row.get("source_path", "")
        if source_path in success_sources:
            exclude_rows.append(
                {
                    "design": row["design"],
                    "status": row["status"],
                    "source_path": source_path,
                    "classification": "exclude",
                    "reason": "duplicate_of_success_source",
                    "notes": row.get("notes", ""),
                }
            )
            continue
        bucket, reason = classify(row.get("source_path", ""), row.get("notes", ""))
        out_row = {
            "design": row["design"],
            "status": row["status"],
            "source_path": source_path,
            "classification": bucket,
            "reason": reason,
            "notes": row.get("notes", ""),
        }
        if bucket == "defer":
            defer_rows.append(out_row)
        elif bucket == "retry":
            existing = retry_rows_by_source.get(source_path)
            if existing and retry_rank(existing["design"]) <= retry_rank(row["design"]):
                continue
            retry_rows_by_source[source_path] = out_row
            retry_candidate_rows_by_source[source_path] = {
                "source": infer_source(source_path),
                "design": row["design"],
                "priority": "high",
                "expected_top": row.get("top", "") or "top",
                "source_path": source_path,
                "notes": f"retry:{reason}; {row.get('notes', '')}".strip(),
            }
        else:
            exclude_rows.append(out_row)

    retry_rows = sorted(retry_rows_by_source.values(), key=lambda row: row["design"])
    retry_candidate_rows = sorted(retry_candidate_rows_by_source.values(), key=lambda row: row["design"])

    for path, data in ((args.out_retry, retry_rows), (args.out_exclude, exclude_rows),
                       (args.out_defer, defer_rows)):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["design", "status", "source_path", "classification", "reason", "notes"])
            writer.writeheader()
            writer.writerows(data)
        print(f"wrote {path} rows={len(data)}")

    args.out_retry_candidates.parent.mkdir(parents=True, exist_ok=True)
    with args.out_retry_candidates.open("w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["source", "design", "priority", "expected_top", "source_path", "notes"],
        )
        writer.writeheader()
        writer.writerows(retry_candidate_rows)
    print(f"wrote {args.out_retry_candidates} rows={len(retry_candidate_rows)}")


if __name__ == "__main__":
    main()
