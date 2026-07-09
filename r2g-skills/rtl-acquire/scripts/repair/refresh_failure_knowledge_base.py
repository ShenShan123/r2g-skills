#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path


import sys

_SKILL_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SKILL_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS_DIR))
from skill_env import (
    default_downloads_root,
    out_root_path,
    skill_reference_path,
    workspace_path,
)

DEFAULT_CANDIDATES = workspace_path("failures/failure_knowledge_base_candidates.csv")
DEFAULT_SIGNATURES = workspace_path("failures/failure_signatures.json")
DEFAULT_INDEX = out_root_path("index.csv")
DEFAULT_SCAN_STATE = workspace_path("scan_state/downloads_scan_state.json")
DEFAULT_KB = skill_reference_path("failure_knowledge_base.md")
DEFAULT_STRATEGY = skill_reference_path("failure_strategy.json")
DEFAULT_SIGNATURE_ACTIONS = workspace_path("failures/failure_signature_actions.json")
DEFAULT_LLM_PATCH_RULES = workspace_path("failures/llm_patch_rule_candidates.json")
CORE_START = "<!-- CORE SIGNATURES START -->"
CORE_END = "<!-- CORE SIGNATURES END -->"
AUTO_START = "<!-- AUTO-GENERATED FAILURE PATTERNS START -->"
AUTO_END = "<!-- AUTO-GENERATED FAILURE PATTERNS END -->"
LLM_RULES_START = "<!-- LLM PATCH RULE CANDIDATES START -->"
LLM_RULES_END = "<!-- LLM PATCH RULE CANDIDATES END -->"
AUTO_ALWAYS_CATEGORIES = {"memory_limit", "helper_collision", "undefined_macro", "graph_empty"}


def load_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    return list(csv.DictReader(path.open(newline="", encoding="utf-8")))


def load_signatures(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    rows = []
    for sig, entry in payload.items():
        rows.append(
            {
                "signature": sig,
                "count": str(entry.get("count", 0)),
                "example_design": entry.get("example_design", ""),
                "example_notes": entry.get("example_notes", ""),
            }
        )
    return rows


def load_signature_dict(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_index(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as fh:
        return {row["design"]: row for row in csv.DictReader(fh)}


def load_scan_state(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    repos = payload.get("repos", {})
    return repos if isinstance(repos, dict) else {}


def load_strategy(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def should_promote(row: dict[str, str]) -> bool:
    try:
        count = int(row.get("count", "0") or 0)
    except Exception:
        count = 0
    category = row.get("category", "")
    if count >= 2:
        return True
    return category in AUTO_ALWAYS_CATEGORIES and count >= 1


def build_auto_section(rows: list[dict[str, str]]) -> str:
    promoted = [row for row in rows if should_promote(row)]
    promoted.sort(key=lambda r: (-int(r.get("count", "0") or 0), r.get("category", ""), r.get("signature", "")))

    lines: list[str] = [
        "## Auto-Discovered Patterns",
        "",
        "This section is refreshed automatically from `failure_knowledge_base_candidates.csv` after each expansion round.",
        "It is intended to keep the formal knowledge base in sync with recurring failures without discarding the curated manual entries above.",
        "",
    ]
    if not promoted:
        lines.extend(
            [
                "- no auto-promoted patterns were found in the current candidate table",
                "",
            ]
        )
        return "\n".join(lines).rstrip() + "\n"

    for idx, row in enumerate(promoted, start=1):
        lines.extend(
            [
                f"### A{idx}. `{row.get('category', 'unknown')}`",
                "",
                f"- recurring_count: `{row.get('count', '0')}`",
                f"- signature: `{row.get('signature', '')}`",
                f"- top_families: `{row.get('top_families', '')}`",
                f"- example_design: `{row.get('example_design', '')}`",
                f"- suggested_repair: {row.get('suggested_repair', '')}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def build_core_section(rows: list[dict[str, str]]) -> str:
    try:
        promoted = [row for row in rows if int(row.get("count", "0") or 0) >= 5]
    except Exception:
        promoted = []
    promoted.sort(key=lambda r: -int(r.get("count", "0") or 0))
    lines: list[str] = [
        "## Core Failure Signatures",
        "",
        "This section is refreshed automatically from `failure_signatures.json`.",
        "It promotes high-frequency failure fingerprints to core entries.",
        "",
    ]
    if not promoted:
        lines.extend(["- no core signatures promoted in the current snapshot", ""])
        return "\n".join(lines).rstrip() + "\n"
    for idx, row in enumerate(promoted, start=1):
        lines.extend(
            [
                f"### C{idx}. `{row.get('signature', '')}`",
                "",
                f"- count: `{row.get('count', '0')}`",
                f"- example_design: `{row.get('example_design', '')}`",
                f"- example_notes: `{row.get('example_notes', '')}`",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def build_llm_rule_section(path: Path) -> str:
    lines: list[str] = [
        "## LLM Patch Rule Candidates",
        "",
        "This section is refreshed automatically from `llm_patch_rule_candidates.json`.",
        "These entries are mined from validated successful LLM patch runs and should be treated as promotion candidates, not unconditional rules.",
        "",
    ]
    if not path.exists():
        lines.extend(["- no LLM-derived rule candidates are available in the current snapshot", ""])
        return "\n".join(lines).rstrip() + "\n"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        lines.extend(["- LLM-derived rule candidates could not be parsed", ""])
        return "\n".join(lines).rstrip() + "\n"
    candidates = payload.get("candidates") or []
    if not candidates:
        lines.extend(["- no LLM-derived rule candidates are available in the current snapshot", ""])
        return "\n".join(lines).rstrip() + "\n"
    candidates = sorted(candidates, key=lambda row: (-int(row.get("support_count", 0) or 0), row.get("patch_feature", ""), row.get("design", "")))
    for idx, row in enumerate(candidates[:50], start=1):
        lines.extend(
            [
                f"### L{idx}. `{row.get('patch_feature', 'frontend_patch_other')}`",
                "",
                f"- support_count: `{row.get('support_count', 0)}`",
                f"- design: `{row.get('design', '')}`",
                f"- failure_class: `{row.get('failure_class', '')}`",
                f"- next_best_action: `{row.get('next_best_action', '')}`",
                f"- symptom_regex_hint: `{row.get('symptom_regex_hint', '')}`",
                f"- suggested_repair: {row.get('suggested_repair', '')}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _inject_block(text: str, start: str, end: str, body: str) -> str:
    block = f"{start}\n{body}{end}"
    if start in text and end in text:
        prefix, rest = text.split(start, 1)
        _, suffix = rest.split(end, 1)
        return prefix.rstrip() + "\n\n" + block + suffix
    return text.rstrip() + "\n\n" + block + "\n"


def refresh_kb_text(text: str, core_section: str, auto_section: str, llm_rule_section: str) -> str:
    merged = _inject_block(text, CORE_START, CORE_END, core_section)
    merged = _inject_block(merged, AUTO_START, AUTO_END, auto_section)
    merged = _inject_block(merged, LLM_RULES_START, LLM_RULES_END, llm_rule_section)
    return merged.rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh the formal failure knowledge base with an auto-generated recurring-pattern section.")
    parser.add_argument("--candidates-csv", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--signatures-json", type=Path, default=DEFAULT_SIGNATURES)
    parser.add_argument("--index-csv", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--scan-state-json", type=Path, default=DEFAULT_SCAN_STATE)
    parser.add_argument("--kb-md", type=Path, default=DEFAULT_KB)
    parser.add_argument("--strategy-json", type=Path, default=DEFAULT_STRATEGY)
    parser.add_argument("--signature-actions-json", type=Path, default=DEFAULT_SIGNATURE_ACTIONS)
    parser.add_argument("--llm-patch-rules-json", type=Path, default=DEFAULT_LLM_PATCH_RULES)
    args = parser.parse_args()

    rows = load_rows(args.candidates_csv)
    sig_dict = load_signature_dict(args.signatures_json)
    sig_rows = load_signatures(args.signatures_json)
    index_rows = load_index(args.index_csv)
    scan_state = load_scan_state(args.scan_state_json)
    strategy = load_strategy(args.strategy_json)
    original = args.kb_md.read_text(encoding="utf-8")
    updated = refresh_kb_text(
        original,
        build_core_section(sig_rows),
        build_auto_section(rows),
        build_llm_rule_section(args.llm_patch_rules_json),
    )
    args.kb_md.write_text(updated, encoding="utf-8")

    core_actions: dict[str, dict] = {}
    for row in sig_rows:
        try:
            count = int(row.get("count", "0") or 0)
        except Exception:
            count = 0
        if count < 5:
            continue
        notes = row.get("example_notes", "")
        actions: list[str] = []
        for name, item in strategy.items():
            pattern = item.get("pattern", "")
            if not pattern:
                continue
            if re.search(pattern, notes, flags=re.IGNORECASE):
                actions.extend(item.get("actions", []))
        if actions:
            core_actions[row.get("signature", "")] = {
                "count": count,
                "actions": sorted(set(actions)),
                "example_design": row.get("example_design", ""),
            }
    if core_actions:
        args.signature_actions_json.write_text(
            json.dumps(core_actions, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    # Prune one-off signatures tied to rejected repos to keep the signature store light.
    pruned = {}
    for sig, entry in sig_dict.items():
        count = int(entry.get("count", 0) or 0)
        if count > 1:
            pruned[sig] = entry
            continue
        design = entry.get("example_design", "")
        source_path = index_rows.get(design, {}).get("source_path", "")
        repo_name = ""
        downloads_root = default_downloads_root()
        try:
            source = Path(source_path).resolve()
            repo_name = source.relative_to(downloads_root.resolve()).parts[0]
        except (OSError, ValueError, IndexError):
            repo_name = ""
        repo_state = scan_state.get(repo_name, {}) if repo_name else {}
        if repo_state.get("repo_decision") == "reject":
            continue
        pruned[sig] = entry
    if pruned:
        args.signatures_json.write_text(json.dumps(pruned, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {args.kb_md}")
    print(f"candidate_rows {len(rows)}")


if __name__ == "__main__":
    main()
