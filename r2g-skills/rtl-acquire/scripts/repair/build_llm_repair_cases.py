#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from pathlib import Path
import sys

import sys

_SKILL_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SKILL_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS_DIR))
from skill_env import (
    out_root_path,
    skill_reference_path,
    workspace_path,
)

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPTS_ROOT = SCRIPT_DIR.parent
for path in (SCRIPTS_ROOT, SCRIPT_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from common.io_utils import load_json, load_rows, now_iso, write_json


DEFAULT_INDEX = out_root_path("index.csv")
DEFAULT_SIGNATURES = workspace_path("failures/failure_signatures.json")
DEFAULT_REPAIR_LOG = workspace_path("failures/repair_action_log.json")
DEFAULT_DIAGNOSIS = workspace_path("failures/failure_diagnosis.json")
DEFAULT_DESIGN_SCORES = workspace_path("quality/design_quality_scores.csv")
DEFAULT_POLICY = skill_reference_path("llm_repair_policy.json")
DEFAULT_OUT_JSON = workspace_path("failures/llm_repair_cases.json")
DEFAULT_OUT_JSONL = workspace_path("failures/llm_repair_cases.jsonl")
DEFAULT_OUT_MD = workspace_path("failures/llm_repair_cases.md")
DEFAULT_DEBUG_JSON = workspace_path("failures/llm_repair_cases_debug_pool.json")
DEFAULT_PUBLISH_JSON = workspace_path("failures/llm_repair_cases_publish_pool.json")


def load_design_scores(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as fh:
        return {row.get("design", ""): row for row in csv.DictReader(fh) if row.get("design")}


def read_file_context(path: Path, max_lines: int) -> str:
    try:
        with path.open(encoding="utf-8", errors="ignore") as fh:
            return "".join(fh.readlines()[:max_lines]).strip()
    except Exception:
        return ""


def load_diagnosis(path: Path) -> dict[str, dict]:
    payload = load_json(path)
    diagnoses = payload.get("diagnoses") or []
    return {item.get("design", ""): item for item in diagnoses if item.get("design")}


def action_specific_guidance(next_best_action: str) -> list[str]:
    if next_best_action == "unexpected_token_frontend_patch":
        return [
            "Focus on narrow parser fixes around the reported token location.",
            "Prefer fixes like missing separators, misplaced keywords, malformed declarations, or local syntax normalization.",
            "Do not rewrite module boundaries, port lists, or large behavioral regions unless the parser error clearly requires it.",
        ]
    if next_best_action == "hierarchy_derive_frontend_patch":
        return [
            "Focus on hierarchy/derive-stage frontend issues after parsing has already started.",
            "Prefer fixes that preserve module structure while resolving parameterization, generate syntax, or local declaration issues.",
            "Do not introduce blackbox stubs or broad architectural rewrites in this mode.",
        ]
    if next_best_action == "sanitize_macro_definition":
        return [
            "Only legalize macro identifiers or obviously malformed preprocessor symbols.",
            "Do not substitute semantic values unless they are already concretely implied by the source.",
        ]
    if next_best_action == "template_materialization":
        return [
            "This case should normally be handled by deterministic template materialization, not by free-form LLM repair.",
            "Only return a patch if the concrete variant is explicitly derivable from a co-located generated benchmark family.",
            "If that deterministic source is not evident, return diagnosis_only or reject.",
        ]
    if next_best_action == "rewrite_nonconstant_procedural_loop":
        return [
            "Target only the specific procedural loop construct that is rejected by the frontend.",
            "Prefer a semantics-preserving local rewrite using flags, bounded indices, or elaboration-safe forms.",
        ]
    if next_best_action == "sv2v":
        return [
            "Assume the main issue is SystemVerilog frontend compatibility.",
            "Prefer a translation-compatible patch that reduces unsupported SystemVerilog syntax while preserving intent.",
        ]
    return [
        "Keep the patch local and parser-focused.",
        "Prefer the smallest change that allows frontend progress.",
    ]


def build_prompt(case: dict) -> str:
    extra_guidance = action_specific_guidance(str(case.get("next_best_action") or ""))
    return (
        "Repair this RTL frontend failure conservatively.\n"
        "Goals:\n"
        "1. Preserve circuit semantics.\n"
        "2. Fix only parser/frontend issues.\n"
        "3. Do not rewrite architecture.\n"
        "4. If confidence is low, return diagnosis only.\n\n"
        f"Design: {case['design']}\n"
        f"Failure class: {case['failure_class']}\n"
        f"Failure stage: {case['failure_stage']}\n"
        f"Likely cause: {case['likely_cause']}\n"
        f"Attempted repair: {', '.join(case['attempted_repair']) if case['attempted_repair'] else 'none'}\n"
        f"Next best action from diagnosis: {case['next_best_action']}\n"
        f"Source path: {case['source_path']}\n"
        "Action-specific guidance:\n- " + "\n- ".join(extra_guidance) + "\n\n"
        f"Notes:\n{case['notes']}\n\n"
        f"Required post-repair checks:\n- " + "\n- ".join(case["post_repair_checks"]) + "\n\n"
        f"Evidence:\n{case['diagnosis_evidence']}\n\n"
        f"Source context:\n{case['source_context']}\n"
    )


def recommend_pool(case: dict) -> str:
    score = float(case.get("design_quality_score", 0.0) or 0.0)
    failure_class = str(case.get("failure_class") or "")
    next_best_action = str(case.get("next_best_action") or "")
    if score >= 0.5 and failure_class == "parse_error" and next_best_action not in {"sv2v", "vhd2vl"}:
        return "publish_pool_candidate"
    return "debug_pool"


def compute_llm_priority(case: dict) -> float:
    score = float(case.get("design_quality_score", 0.0) or 0.0)
    next_best_action = str(case.get("next_best_action") or "")
    attempted = set(case.get("attempted_repair") or [])
    priority = score
    if next_best_action in {
        "frontend_manual_patch_candidate",
        "unexpected_token_frontend_patch",
        "legacy_syntax_frontend_patch",
        "hierarchy_derive_frontend_patch",
        "rewrite_nonconstant_procedural_loop",
        "sanitize_macro_definition",
    }:
        priority += 0.40
    elif next_best_action == "template_materialization":
        priority -= 0.50
    elif next_best_action in {"retry_next_best_action", "manual_or_exclude"}:
        priority += 0.35
    elif next_best_action in {"conflicting_driver_review", "memory_lowering_review", "retry_metadata_audit"}:
        priority += 0.15
    elif next_best_action.startswith("set_frontend:"):
        priority += 0.15
    elif next_best_action in {"sv2v", "vhd2vl", "resolve_include", "stub_module"}:
        priority -= 0.20
    if attempted:
        priority += 0.10
    if str(case.get("failure_class") or "") == "parse_error":
        priority += 0.10
    return round(priority, 4)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build prompt-ready long-tail LLM repair cases from failed designs.")
    parser.add_argument("--index-csv", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--signatures-json", type=Path, default=DEFAULT_SIGNATURES)
    parser.add_argument("--repair-log-json", type=Path, default=DEFAULT_REPAIR_LOG)
    parser.add_argument("--diagnosis-json", type=Path, default=DEFAULT_DIAGNOSIS)
    parser.add_argument("--design-scores-csv", type=Path, default=DEFAULT_DESIGN_SCORES)
    parser.add_argument("--policy-json", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--out-json", type=Path, default=DEFAULT_OUT_JSON)
    parser.add_argument("--out-jsonl", type=Path, default=DEFAULT_OUT_JSONL)
    parser.add_argument("--out-md", type=Path, default=DEFAULT_OUT_MD)
    parser.add_argument("--debug-json", type=Path, default=DEFAULT_DEBUG_JSON)
    parser.add_argument("--publish-json", type=Path, default=DEFAULT_PUBLISH_JSON)
    args = parser.parse_args()

    policy = load_json(args.policy_json)
    signatures = load_json(args.signatures_json)
    repair_log = load_json(args.repair_log_json)
    diagnosis_by_design = load_diagnosis(args.diagnosis_json)
    design_scores = load_design_scores(args.design_scores_csv)
    index_rows = [row for row in load_rows(args.index_csv) if row.get("status") and row.get("status") != "success"]

    allowed_classes = set(policy.get("allowed_failure_classes") or [])
    forbid_actions = set(policy.get("forbid_if_auto_fix_action_present") or [])
    min_score = float(policy.get("require_min_design_quality_score", 0.0) or 0.0)
    require_frontend = bool(policy.get("require_frontend_or_parse_signal", True))
    max_cases = int(policy.get("max_cases_per_run", 100) or 100)
    context_lines = int(policy.get("context_lines_per_file", 80) or 80)

    cases: list[dict] = []
    for row in index_rows:
        design = row.get("design", "")
        notes = row.get("notes", "") or ""
        signature = None
        semantic = {}
        for sig_hash, payload in signatures.items():
            if payload.get("example_design") == design:
                signature = sig_hash
                semantic = payload.get("semantic_signature") or {}
                break
        failure_class = str(semantic.get("failure_class") or diagnosis_by_design.get(design, {}).get("failure_class") or "unknown")
        if allowed_classes and failure_class not in allowed_classes:
            continue
        try:
            score = float(design_scores.get(design, {}).get("design_quality_score", 0.0) or 0.0)
        except Exception:
            score = 0.0
        if score < min_score:
            continue
        actions = set((repair_log.get(design, {}) or {}).get("last_actions") or [])
        if forbid_actions & actions:
            continue
        if require_frontend:
            diagnosis = diagnosis_by_design.get(design, {})
            tool_name = str(semantic.get("tool_name") or diagnosis.get("tool_name") or "")
            stage = str(semantic.get("failure_stage") or diagnosis.get("failure_stage") or "")
            if tool_name not in {"yosys", "slang", "sv2v", "vhd2vl"} and stage != "synth":
                continue
            if "syntax error" not in notes.lower() and "parse error" not in notes.lower() and failure_class != "parse_error":
                continue
        source_path = Path(row.get("source_path", "") or "")
        source_context = read_file_context(source_path, context_lines) if source_path.exists() else ""
        diagnosis = diagnosis_by_design.get(design, {})
        attempted_repair = list(diagnosis.get("attempted_repair") or [])
        next_best_action = str(diagnosis.get("next_best_action") or "retry_next_best_action")
        if next_best_action in {"template_materialization", "exclude"}:
            continue
        case = {
            "design": design,
            "source_path": str(source_path),
            "failure_signature": signature or "",
            "failure_class": failure_class,
            "failure_stage": str(semantic.get("failure_stage") or diagnosis.get("failure_stage") or ""),
            "tool_name": str(semantic.get("tool_name") or diagnosis.get("tool_name") or ""),
            "design_quality_score": score,
            "notes": notes,
            "source_context": source_context,
            "likely_cause": str(diagnosis.get("likely_cause") or ""),
            "attempted_repair": attempted_repair,
            "diagnosis_evidence": diagnosis.get("evidence") or {},
            "next_best_action": next_best_action,
            "recommended_mode": "diagnose_then_patch",
            "post_repair_checks": [
                "read_verilog / hierarchy / check must pass",
                "graph conversion must produce non-empty graph",
                "design_quality_score should not collapse materially",
                "if transformation is semantic, require LEC-lite or equivalent structural sanity",
            ],
            "created_at": now_iso(),
        }
        case["llm_priority_score"] = compute_llm_priority(case)
        case["pool_recommendation"] = recommend_pool(case)
        case["prompt"] = build_prompt(case)
        cases.append(case)

    cases.sort(key=lambda item: (-float(item.get("llm_priority_score", 0.0) or 0.0), -float(item.get("design_quality_score", 0.0) or 0.0), item.get("design", "")))
    cases = cases[:max_cases]

    write_json(args.out_json, {"generated_at": now_iso(), "count": len(cases), "cases": cases})
    write_json(args.debug_json, {"generated_at": now_iso(), "count": sum(1 for c in cases if c["pool_recommendation"] == "debug_pool"), "cases": [c for c in cases if c["pool_recommendation"] == "debug_pool"]})
    write_json(args.publish_json, {"generated_at": now_iso(), "count": sum(1 for c in cases if c["pool_recommendation"] == "publish_pool_candidate"), "cases": [c for c in cases if c["pool_recommendation"] == "publish_pool_candidate"]})
    args.out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with args.out_jsonl.open("w", encoding="utf-8") as fh:
        for case in cases:
            fh.write(__import__("json").dumps(case, ensure_ascii=False) + "\n")
    with args.out_md.open("w", encoding="utf-8") as fh:
        fh.write("# LLM Repair Cases\n\n")
        fh.write(f"- generated_at: {now_iso()}\n")
        fh.write(f"- count: {len(cases)}\n")
        fh.write(f"- debug_pool_count: {sum(1 for c in cases if c['pool_recommendation'] == 'debug_pool')}\n")
        fh.write(f"- publish_pool_candidate_count: {sum(1 for c in cases if c['pool_recommendation'] == 'publish_pool_candidate')}\n")
        fh.write(f"- policy: {args.policy_json}\n\n")
        fh.write("| design | failure_class | stage | tool | score | next_best_action | llm_priority | pool |\n")
        fh.write("|---|---|---|---|---|---|---|---|\n")
        for case in cases[:50]:
            fh.write(f"| {case['design']} | {case['failure_class']} | {case['failure_stage']} | {case['tool_name']} | {case['design_quality_score']:.3f} | {case['next_best_action']} | {case['llm_priority_score']:.3f} | {case['pool_recommendation']} |\n")
        if len(cases) > 50:
            fh.write(f"\n... truncated, total {len(cases)} cases ...\n")

    print(f"wrote {args.out_json}")
    print(f"wrote {args.out_jsonl}")
    print(f"wrote {args.out_md}")


if __name__ == "__main__":
    main()
