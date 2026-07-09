#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import hashlib
import re
import random
from pathlib import Path


import sys

_SKILL_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SKILL_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS_DIR))
from skill_env import (
    default_out_root,
    out_root_path,
    skill_reference_path,
    workspace_path,
)

DEFAULT_INDEX = out_root_path("index.csv")
DEFAULT_EXCLUDE = workspace_path("failures/failed_candidates_exclude.csv")
DEFAULT_PLAN = workspace_path("failures/auto_fix_plan.json")
DEFAULT_RETRY = workspace_path("failures/failed_candidates_retry_candidates.csv")
DEFAULT_RETRY_AUTOFIX = workspace_path("failures/failed_candidates_retry_candidates_autofix.csv")
DEFAULT_OUT_ROOT = default_out_root()
DEFAULT_STUB_DIR = workspace_path("failures/auto_fix_stubs")
DEFAULT_STRATEGY = skill_reference_path("failure_strategy.json")
DEFAULT_DENY_POLICY = skill_reference_path("repair_deny_policy.json")
DEFAULT_REPAIR_LOG = workspace_path("failures/repair_action_log.json")
DEFAULT_DESIGN_SCORES = workspace_path("quality/design_quality_scores.csv")
DEFAULT_SCAN_STATE = workspace_path("scan_state/downloads_scan_state.json")
DEFAULT_SIGNATURES = workspace_path("failures/failure_signatures.json")
DEFAULT_SIGNATURE_ACTIONS = workspace_path("failures/failure_signature_actions.json")
DEFAULT_FAILURE_FAMILIES = workspace_path("failures/failure_families.json")
DEFAULT_CANDIDATES_DIR = workspace_path("candidates")


AUTO_EXCLUDE_PATTERNS = [
    ("helper_collision", re.compile(r"re-definition of module `\$abstract\\dff'", re.IGNORECASE)),
    ("undefined_macro", re.compile(r"unimplemented compiler directive or undefined macro", re.IGNORECASE)),
    ("graph_empty", re.compile(r"no graph nodes were created from mapped netlist", re.IGNORECASE)),
]

MISSING_INCLUDE_RE = re.compile(r"can't open include file [`']([^`']+)[`']!?|cannot open include file [`']([^`']+)[`']?", re.IGNORECASE)
MISSING_MODULE_RE = re.compile(r"module `([^`]+)` (?:not part of the design|not found)", re.IGNORECASE)
MEMORY_LIMIT_RE = re.compile(r"synth_memory_max_bits|synthesized memory size", re.IGNORECASE)
OOM_RE = re.compile(r"out of memory|oom|killed process|killed signal|std::bad_alloc", re.IGNORECASE)
INVALID_MACRO_DEF_RE = re.compile(r"invalid name for macro definition", re.IGNORECASE)
SIMULATION_SYSTEM_TASK_RE = re.compile(
    r"unrecognized format character|system task [`']?\$(?:display|monitor|strobe|write|error|warning|fatal|stop|finish)",
    re.IGNORECASE,
)
ABSTRACT_MODULE_REDEF_RE = re.compile(r"re-definition of module `\$abstract\\", re.IGNORECASE)
STUB_PORT_RE = re.compile(r"\.(\w+)\s*\(")

DEFAULT_MEM_LIMIT = "131072"
MAX_REPAIR_ATTEMPTS = 2
EXPLORATION_RATE = 0.2
DIGIT_WORDS = {
    "0": "ZERO",
    "1": "ONE",
    "2": "TWO",
    "3": "THREE",
    "4": "FOUR",
    "5": "FIVE",
    "6": "SIX",
    "7": "SEVEN",
    "8": "EIGHT",
    "9": "NINE",
}
KNOWN_TEMPLATE_MATERIALIZATIONS = {
    "vtr_flow/benchmarks/arithmetic/adder_trees/verilog/adder_tree.v": (
        "vtr_flow/benchmarks/arithmetic/generated_circuits/adder_trees/verilog/adder_tree_3L_028bits.v"
    ),
}


def read_index_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def load_excludes(path: Path) -> tuple[list[dict[str, str]], set[tuple[str, str]]]:
    if not path.exists():
        return [], set()
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    seen = {(row.get("design", ""), row.get("source_path", "")) for row in rows}
    return rows, seen


def load_strategy(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_deny_policy(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_repair_log(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_repair_log(path: Path, payload: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def load_design_scores(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    rows = list(csv.DictReader(path.open(newline="", encoding="utf-8")))
    return {row.get("design", ""): row for row in rows if row.get("design")}


def load_scan_state(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    repos = payload.get("repos", {})
    return repos if isinstance(repos, dict) else {}


def load_failure_families(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_failure_families(path: Path, payload: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def classify_failure_family(notes: str) -> dict[str, str]:
    lower = (notes or "").lower()
    failure_class = "unknown"
    if "synth_memory_max_bits" in lower or "synthesized memory size" in lower:
        failure_class = "memory_limit"
    elif "can't open include file" in lower or "cannot open include file" in lower:
        failure_class = "missing_include"
    elif "module `" in lower and ("not part of the design" in lower or "not found" in lower):
        failure_class = "missing_module"
    elif "unimplemented compiler directive" in lower or "undefined macro" in lower:
        failure_class = "undefined_macro"
    elif "re-definition of module" in lower and "$abstract\\dff" in lower:
        failure_class = "helper_collision"
    elif "syntax error" in lower or "parse error" in lower or "front-end" in lower or "frontend" in lower:
        failure_class = "parse_error"
    elif "no graph nodes were created from mapped netlist" in lower:
        failure_class = "graph_empty"
    return {"failure_class": failure_class}


def infer_failure_stage(status: str) -> str:
    lowered = (status or "").lower()
    if "graph" in lowered:
        return "graph_convert"
    if "synth" in lowered:
        return "synth"
    return "finalize"


def infer_tool_name(notes: str) -> str:
    lowered = (notes or "").lower()
    if "slang" in lowered:
        return "slang"
    if "sv2v" in lowered:
        return "sv2v"
    if "vhd2vl" in lowered or "vhdl" in lowered:
        return "vhd2vl"
    if "no graph nodes were created from mapped netlist" in lowered:
        return "graph_builder"
    return "yosys"


def infer_source_family(source_path: str) -> str:
    lowered = (source_path or "").lower()
    if "hdl-benchmarks" in lowered:
        return "hdl_benchmarks"
    if "vtr-verilog-to-routing" in lowered:
        return "vtr"
    if "openroad-flow-scripts" in lowered:
        return "orfs"
    if "_downloads" in lowered:
        return "downloads_other"
    return "unknown"


def semantic_signature_payload(row: dict[str, str], notes: str, rtl_files: list[Path], family: dict[str, str]) -> dict:
    unresolved = sorted(set(MISSING_MODULE_RE.findall(notes or "")))
    suffix_mix = sorted({path.suffix.lower() for path in rtl_files if path.suffix})
    return {
        "failure_stage": infer_failure_stage(row.get("status", "")),
        "tool_name": infer_tool_name(notes),
        "failure_class": family.get("failure_class", "unknown"),
        "source_family": infer_source_family(row.get("source_path", "")),
        "bundle_size": len(rtl_files),
        "file_suffix_mix": suffix_mix,
        "top_present": bool(row.get("top")),
        "unresolved_modules": unresolved,
        "has_include_error": bool(MISSING_INCLUDE_RE.search(notes or "")),
        "has_memory_limit_error": bool(MEMORY_LIMIT_RE.search(notes or "")),
        "has_oom_signal": bool(OOM_RE.search(notes or "")),
    }


def action_expected_reward(strategy: dict[str, dict], action: str) -> float:
    for item in strategy.values():
        stats = item.get("action_stats") or []
        for stat in stats:
            if stat.get("action") == action:
                try:
                    return float(stat.get("avg_reward", 0.0) or 0.0)
                except Exception:
                    return 0.0
    return 0.0


def select_action(candidates: list[str], strategy: dict[str, dict], signature_hash: str) -> str | None:
    if not candidates:
        return None
    ranked = sorted(candidates, key=lambda a: action_expected_reward(strategy, a), reverse=True)
    seed = int(signature_hash[:8], 16) if signature_hash else 0
    random.seed(seed)
    if len(ranked) >= 2 and random.random() < EXPLORATION_RATE:
        return ranked[1]
    return ranked[0]


def guess_repo_root(source_path: Path) -> Path:
    for parent in source_path.parents:
        if (parent / ".git").exists():
            return parent
        if parent.name == "_downloads":
            return source_path
    return source_path.parent


def find_missing_include(source_path: Path, include_name: str) -> Path | None:
    repo_root = guess_repo_root(source_path)
    for path in repo_root.rglob(include_name):
        if path.is_file():
            return path
    return None


def load_design_meta(out_root: Path, design: str) -> dict:
    meta_path = out_root / design / "design_meta.json"
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def normalize_rtl_files(meta: dict, source_path: Path) -> list[Path]:
    rtl_files = meta.get("rtl_files", [])
    if isinstance(rtl_files, list) and rtl_files:
        return [Path(path) for path in rtl_files]
    return [source_path]


def source_path_is_derived_frontend_artifact(source_path: Path) -> bool:
    lowered = source_path.name.lower()
    return (
        "_sv2v" in lowered
        or "_vhd2vl" in lowered
        or "_simtask_sanitized_" in lowered
        or "_macro_sanitized" in lowered
        or "_materialized" in lowered
    )


def load_candidate_catalog(candidates_dir: Path) -> dict[str, dict[str, str]]:
    catalog: dict[str, tuple[int, float, dict[str, str]]] = {}
    if not candidates_dir.exists():
        return {}
    for path in candidates_dir.glob("*.csv"):
        try:
            mtime = path.stat().st_mtime
            rows = list(csv.DictReader(path.open(newline="", encoding="utf-8")))
        except Exception:
            continue
        for row in rows:
            design = (row.get("design") or "").strip()
            if not design:
                continue
            source_path = (row.get("source_path") or "").strip()
            rtl_files = (row.get("rtl_files") or "").strip()
            if not source_path or not rtl_files:
                continue
            derived_rank = 1 if source_path_is_derived_frontend_artifact(Path(source_path)) else 0
            existing = catalog.get(design)
            if existing is None or (derived_rank, -mtime) < (existing[0], -existing[1]):
                catalog[design] = (derived_rank, mtime, row)
    return {design: row for design, (_rank, _mtime, row) in catalog.items()}


def recover_original_candidate_bundle(
    design: str,
    source_path: Path,
    retry_base: dict[str, str],
    rtl_files: list[Path],
    include_dirs: list[Path],
    candidate_catalog: dict[str, dict[str, str]],
) -> tuple[Path, list[Path], list[Path]]:
    if not source_path_is_derived_frontend_artifact(source_path) and not any(
        source_path_is_derived_frontend_artifact(path) for path in rtl_files
    ):
        return source_path, rtl_files, include_dirs
    row = candidate_catalog.get(design) or {}
    candidate_source = Path((row.get("source_path") or "").strip()) if row.get("source_path") else None
    candidate_rtl_files = [
        Path(part.strip())
        for part in (row.get("rtl_files") or "").split(";")
        if part.strip()
    ]
    candidate_include_dirs = [
        Path(part.strip())
        for part in (row.get("include_dirs") or "").split(";")
        if part.strip()
    ]
    if candidate_source and candidate_rtl_files and not source_path_is_derived_frontend_artifact(candidate_source):
        return candidate_source, candidate_rtl_files, candidate_include_dirs or include_dirs
    return source_path, rtl_files, include_dirs


def extract_stub_ports(module_name: str, rtl_files: list[Path]) -> list[str]:
    module_token = re.escape(module_name)
    inst_re = re.compile(rf"\\b{module_token}\\b\\s*(?:#\\s*\\([^;]*?\\)\\s*)?\\w+\\s*\\(([^;]*?)\\)\\s*;", re.DOTALL)
    for path in rtl_files:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        match = inst_re.search(text)
        if not match:
            continue
        block = match.group(1)
        port_names = STUB_PORT_RE.findall(block)
        if port_names:
            return list(dict.fromkeys(port_names))
        # Fallback: positional count.
        depth = 0
        count = 0
        has_token = False
        for ch in block:
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth = max(depth - 1, 0)
            elif ch == "," and depth == 0:
                count += 1
            elif not ch.isspace():
                has_token = True
        if has_token:
            return [f"p{idx}" for idx in range(count + 1)]
        return []
    return []


def extract_module_port_widths(module_name: str, rtl_files: list[Path]) -> tuple[dict[str, str], dict[str, str]]:
    widths: dict[str, str] = {}
    directions: dict[str, str] = {}
    module_re = re.compile(rf"\\bmodule\\s+{re.escape(module_name)}\\b.*?endmodule", re.S)
    for path in rtl_files:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        match = module_re.search(text)
        if not match:
            continue
        block = match.group(0)
        for decl in re.finditer(r"\\b(input|output|inout)\\b\\s*(\\[[^\\]]+\\])?\\s*([^;]+);", block):
            direction = decl.group(1)
            width = (decl.group(2) or "").strip()
            names = decl.group(3)
            for name in re.split(r"[\\s,]+", names.strip()):
                if not name or name == ",":
                    continue
                widths[name] = width
                directions[name] = direction
        if widths:
            return widths, directions
    return widths, directions


def write_stub_module(stub_dir: Path, design: str, module_name: str, ports: list[str]) -> Path:
    stub_dir.mkdir(parents=True, exist_ok=True)
    safe_module = re.sub(r"[^A-Za-z0-9_]+", "_", module_name)
    stub_path = stub_dir / f"{design}__{safe_module}.v"
    port_list = ", ".join(ports) if ports else ""
    lines = [f"(* blackbox *) module {module_name}({port_list});"]
    for port in ports:
        attr = ""
        lower = port.lower()
        if lower in {"clk", "clock"} or lower.endswith("_clk") or lower.startswith("clk_"):
            attr = "(* clock *) "
        elif lower in {"rst", "reset"} or lower.endswith("_rst") or lower.endswith("_reset"):
            attr = "(* reset *) "
        lines.append(f"  {attr}inout {port};")
    lines.append("endmodule")
    stub_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return stub_path


def write_stub_module_with_widths(
    stub_dir: Path,
    design: str,
    module_name: str,
    ports: list[str],
    widths: dict[str, str],
    directions: dict[str, str],
) -> Path:
    safe_module = re.sub(r"[^A-Za-z0-9_]+", "_", module_name)
    stub_path = stub_dir / f"{design}__{safe_module}.v"
    port_list = ", ".join(ports) if ports else ""
    inputs = [p for p in ports if directions.get(p) == "input"]
    outputs = [p for p in ports if directions.get(p) == "output"]
    can_inline = len(inputs) == 1 and len(outputs) == 1
    lines = [f"module {module_name}({port_list});"] if can_inline else [f"(* blackbox *) module {module_name}({port_list});"]
    for port in ports:
        width = widths.get(port, "")
        attr = ""
        lower = port.lower()
        if lower in {"clk", "clock"} or lower.endswith("_clk") or lower.startswith("clk_"):
            attr = "(* clock *) "
        elif lower in {"rst", "reset"} or lower.endswith("_rst") or lower.endswith("_reset"):
            attr = "(* reset *) "
        width_text = f"{width} " if width else ""
        direction = directions.get(port, "inout")
        lines.append(f"  {attr}{direction} {width_text}{port};")
    if can_inline:
        out_port = outputs[0]
        in_port = inputs[0]
        lines.append(f"  (* keep *) assign {out_port} = {in_port};")
    lines.append("endmodule")
    stub_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return stub_path


def normalize_macro_identifier(token: str) -> str | None:
    token = (token or "").strip()
    if not token:
        return None
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", token):
        return token
    if not re.fullmatch(r"[A-Za-z0-9_]+", token):
        return None
    match = re.match(r"(\d+)(.*)", token)
    if not match:
        return None
    digits, tail = match.groups()
    prefix = "_".join(DIGIT_WORDS.get(ch, ch) for ch in digits)
    tail = tail.lstrip("_")
    return f"{prefix}_{tail}" if tail else prefix


def write_sanitized_macro_copy(out_root: Path, design: str, source_path: Path) -> Path | None:
    if not source_path.exists():
        return None
    try:
        text = source_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None

    replacements: dict[str, str] = {}
    for match in re.finditer(r"(?m)^(\s*`define\s+)([A-Za-z0-9_]+)\b", text):
        original = match.group(2)
        normalized = normalize_macro_identifier(original)
        if normalized and normalized != original:
            replacements[original] = normalized

    if not replacements:
        return None

    updated = text
    for original, normalized in sorted(replacements.items(), key=lambda item: -len(item[0])):
        updated = re.sub(rf"`{re.escape(original)}\b", f"`{normalized}", updated)

    if updated == text:
        return None

    temp_dir = out_root / "_tmp_cfg"
    temp_dir.mkdir(parents=True, exist_ok=True)
    out_path = temp_dir / f"{design}_macro_sanitized.v"
    out_path.write_text(updated, encoding="utf-8")
    return out_path


def materialize_known_template(out_root: Path, design: str, source_path: Path) -> Path | None:
    source_text = str(source_path).replace("\\", "/")
    matched_target: Path | None = None
    for suffix, target_suffix in KNOWN_TEMPLATE_MATERIALIZATIONS.items():
        if source_text.endswith(suffix):
            anchor = source_text[: -len(suffix)]
            matched_target = Path(anchor + target_suffix)
            break
    if matched_target is None or not matched_target.exists():
        return None
    try:
        materialized_text = matched_target.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None
    temp_dir = out_root / "_tmp_cfg"
    temp_dir.mkdir(parents=True, exist_ok=True)
    out_path = temp_dir / f"{design}_materialized.v"
    out_path.write_text(materialized_text, encoding="utf-8")
    return out_path


def strip_simulation_system_tasks(text: str) -> str:
    updated = text
    task_call_re = re.compile(
        r"\$(?:display|monitor|strobe|write|error|warning|fatal)\b.*?;",
        re.DOTALL,
    )
    stop_call_re = re.compile(r"\$(?:stop|finish)\b\s*;", re.DOTALL)
    while True:
        next_text = task_call_re.sub("", updated)
        next_text = stop_call_re.sub("", next_text)
        if next_text == updated:
            break
        updated = next_text
    return updated


def write_simulation_sanitized_bundle(out_root: Path, design: str, rtl_files: list[Path]) -> list[Path] | None:
    temp_dir = out_root / "_tmp_cfg"
    temp_dir.mkdir(parents=True, exist_ok=True)

    updated_bundle: list[Path] = []
    modified = 0
    for idx, path in enumerate(rtl_files):
        try:
            original = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            updated_bundle.append(path)
            continue
        sanitized = strip_simulation_system_tasks(original)
        if sanitized == original:
            updated_bundle.append(path)
            continue
        out_path = temp_dir / f"{design}_simtask_sanitized_{idx}_{path.stem}{path.suffix}"
        out_path.write_text(sanitized, encoding="utf-8")
        updated_bundle.append(out_path)
        modified += 1
    if modified == 0:
        return None
    return updated_bundle


def main() -> None:
    parser = argparse.ArgumentParser(description="Auto-apply safe failure fixes based on known signatures.")
    parser.add_argument("--index-csv", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--exclude-csv", type=Path, default=DEFAULT_EXCLUDE)
    parser.add_argument("--plan-json", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--retry-csv", type=Path, default=DEFAULT_RETRY)
    parser.add_argument("--retry-autofix-csv", type=Path, default=DEFAULT_RETRY_AUTOFIX)
    parser.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT)
    parser.add_argument("--stub-dir", type=Path, default=DEFAULT_STUB_DIR)
    parser.add_argument("--strategy-json", type=Path, default=DEFAULT_STRATEGY)
    parser.add_argument("--deny-policy-json", type=Path, default=DEFAULT_DENY_POLICY)
    parser.add_argument("--repair-log-json", type=Path, default=DEFAULT_REPAIR_LOG)
    parser.add_argument("--design-scores-csv", type=Path, default=DEFAULT_DESIGN_SCORES)
    parser.add_argument("--scan-state-json", type=Path, default=DEFAULT_SCAN_STATE)
    parser.add_argument("--signatures-json", type=Path, default=DEFAULT_SIGNATURES)
    parser.add_argument("--signature-actions-json", type=Path, default=DEFAULT_SIGNATURE_ACTIONS)
    parser.add_argument("--failure-families-json", type=Path, default=DEFAULT_FAILURE_FAMILIES)
    parser.add_argument("--candidates-dir", type=Path, default=DEFAULT_CANDIDATES_DIR)
    args = parser.parse_args()

    index_rows = [row for row in read_index_rows(args.index_csv) if row.get("status") and row.get("status") != "success"]
    exclude_rows, exclude_seen = load_excludes(args.exclude_csv)
    strategy = load_strategy(args.strategy_json)
    deny_policy = load_deny_policy(args.deny_policy_json)
    repair_log = load_repair_log(args.repair_log_json)
    design_scores = load_design_scores(args.design_scores_csv)
    scan_state = load_scan_state(args.scan_state_json)
    failure_families = load_failure_families(args.failure_families_json)
    signature_stats: dict[str, dict] = {}
    if args.signatures_json.exists():
        try:
            signature_stats = json.loads(args.signatures_json.read_text(encoding="utf-8"))
        except Exception:
            signature_stats = {}
    signature_actions: dict[str, dict] = {}
    if args.signature_actions_json.exists():
        try:
            signature_actions = json.loads(args.signature_actions_json.read_text(encoding="utf-8"))
        except Exception:
            signature_actions = {}
    retry_rows = []
    if args.retry_csv.exists():
        retry_rows = list(csv.DictReader(args.retry_csv.open(newline="", encoding="utf-8")))
    retry_by_design = {row.get("design", ""): row for row in retry_rows}
    retry_by_source = {row.get("source_path", ""): row for row in retry_rows}
    candidate_catalog = load_candidate_catalog(args.candidates_dir)

    auto_excluded = []
    auto_fixed_rows: list[dict[str, str]] = []
    stub_count = 0
    include_fix_count = 0
    memory_fix_count = 0
    for row in index_rows:
        design = row.get("design", "")
        source_path = row.get("source_path", "")
        if (design, source_path) in exclude_seen:
            continue
        notes = row.get("notes", "") or ""
        signature_source = "\n".join(notes.splitlines()[-10:])
        signature_hash = hashlib.md5(signature_source.encode("utf-8", errors="ignore")).hexdigest()
        entry = signature_stats.get(signature_hash, {"count": 0, "example_design": design, "example_notes": notes[:200]})
        entry["count"] = int(entry.get("count", 0)) + 1
        family = classify_failure_family(notes)
        design_meta = load_design_meta(args.out_root, design)
        rtl_files = normalize_rtl_files(design_meta, Path(source_path))
        entry["semantic_signature"] = semantic_signature_payload(row, notes, rtl_files, family)
        signature_stats[signature_hash] = entry
        family_key = family.get("failure_class", "unknown")
        fam_entry = failure_families.get(family_key, {"count": 0, "examples": []})
        fam_entry["count"] = int(fam_entry.get("count", 0)) + 1
        if len(fam_entry.get("examples", [])) < 5:
            fam_entry.setdefault("examples", []).append({"design": design, "signature": signature_hash})
        failure_families[family_key] = fam_entry
        design_score = design_scores.get(design, {}).get("design_quality_score", "")
        try:
            score_value = float(design_score) if design_score != "" else None
        except Exception:
            score_value = None
        if score_value is not None and score_value < 0.2:
            auto_excluded.append(
                {
                    "design": design,
                    "status": row.get("status", "synth_failed"),
                    "source_path": source_path,
                    "classification": "exclude",
                    "reason": "auto_exclude_low_design_score",
                    "notes": "auto-fix executor excluded low-contribution design",
                }
            )
            continue
        attempts = int(repair_log.get(design, {}).get("attempts", 0))
        deny_entry = (deny_policy.get("deny_classes") or {}).get(family_key, {})
        if deny_entry:
            auto_excluded.append(
                {
                    "design": design,
                    "status": row.get("status", "synth_failed"),
                    "source_path": source_path,
                    "classification": "exclude",
                    "reason": f"deny_policy_{family_key}",
                    "notes": deny_entry.get("reason", "repair denied by policy"),
                }
            )
            repair_log[design] = {
                "attempts": attempts,
                "last_notes": notes,
                "last_actions": [],
                "signature": signature_hash,
                "deny_policy": family_key,
            }
            continue
        reason = None
        for label, pattern in AUTO_EXCLUDE_PATTERNS:
            if pattern.search(notes):
                reason = label
                break
        if reason:
            auto_excluded.append(
                {
                    "design": design,
                    "status": row.get("status", "synth_failed"),
                    "source_path": source_path,
                    "classification": "exclude",
                    "reason": f"auto_exclude_{reason}",
                    "notes": "auto-fix executor excluded low-value or front-end-heavy failure to keep closed-loop throughput",
                }
            )
            continue

        missing_include_match = MISSING_INCLUDE_RE.search(notes)
        missing_module_match = MISSING_MODULE_RE.search(notes)
        memory_limit_hit = bool(MEMORY_LIMIT_RE.search(notes))
        oom_hit = bool(OOM_RE.search(notes))
        simulation_system_task_hit = bool(SIMULATION_SYSTEM_TASK_RE.search(notes))
        abstract_module_redef_hit = bool(ABSTRACT_MODULE_REDEF_RE.search(notes))

        action_hits = []
        if signature_hash in signature_actions:
            action_hits.extend(signature_actions[signature_hash].get("actions", []))
        for name, item in strategy.items():
            pattern = item.get("pattern", "")
            if not pattern:
                continue
            if re.search(pattern, notes, flags=re.IGNORECASE):
                action_hits.extend(item.get("actions", []))
        if simulation_system_task_hit:
            action_hits.append("sanitize_simulation_system_tasks")
            action_hits = [action for action in action_hits if action != "sv2v"]
        if abstract_module_redef_hit:
            action_hits.append("recover_original_bundle_and_sanitize_simtasks")

        prior_signature = str(repair_log.get(design, {}).get("signature", ""))
        prior_actions = set(repair_log.get(design, {}).get("last_actions", []) or [])
        current_actions = set(action_hits)
        if attempts >= MAX_REPAIR_ATTEMPTS and prior_signature == signature_hash and current_actions.issubset(prior_actions):
            auto_excluded.append(
                {
                    "design": design,
                    "status": row.get("status", "synth_failed"),
                    "source_path": source_path,
                    "classification": "exclude",
                    "reason": "auto_exclude_retry_budget",
                    "notes": "auto-fix executor skipped design due to retry budget",
                }
            )
            continue

        if not (missing_include_match or missing_module_match or memory_limit_hit or simulation_system_task_hit or abstract_module_redef_hit or action_hits):
            continue

        retry_base = retry_by_design.get(design) or retry_by_source.get(source_path) or {}
        retry_row = {
            "source": retry_base.get("source") or retry_base.get("source_group") or "retry",
            "design": design,
            "priority": retry_base.get("priority") or "high",
            "expected_top": retry_base.get("expected_top") or row.get("top") or "top",
            "source_path": source_path,
            "rtl_files": retry_base.get("rtl_files", ""),
            "include_dirs": retry_base.get("include_dirs", ""),
            "synth_variant": retry_base.get("synth_variant", ""),
            "synth_memory_max_bits": retry_base.get("synth_memory_max_bits", ""),
            "synth_frontend": retry_base.get("synth_frontend", ""),
            "notes": retry_base.get("notes", ""),
        }

        include_dirs: list[Path] = []
        include_dirs_text = retry_row.get("include_dirs") or ""
        if include_dirs_text:
            include_dirs.extend(Path(part.strip()) for part in re.split(r"[;|]", include_dirs_text) if part.strip())
        original_failed_source_path = Path(source_path)
        current_source_path = original_failed_source_path
        current_source_path, rtl_files, include_dirs = recover_original_candidate_bundle(
            design,
            current_source_path,
            retry_base,
            rtl_files,
            include_dirs,
            candidate_catalog,
        )
        recovered_from_derived_frontend = current_source_path != original_failed_source_path
        retry_row["source_path"] = str(current_source_path)
        applied_any_fix = False

        if "exclude" in action_hits:
            auto_excluded.append(
                {
                    "design": design,
                    "status": row.get("status", "synth_failed"),
                    "source_path": source_path,
                    "classification": "exclude",
                    "reason": "auto_exclude_strategy",
                    "notes": "auto-fix executor excluded per failure strategy",
                }
            )
            continue

        if missing_include_match or "resolve_include" in action_hits:
            include_name = missing_include_match.group(1) or missing_include_match.group(2) or ""
            include_path = find_missing_include(current_source_path, include_name) if include_name else None
            if include_path:
                include_dirs.append(include_path.parent)
                include_fix_count += 1
                applied_any_fix = True
                retry_row["notes"] = f"auto_fix:missing_include:{include_name}; {notes}".strip()
            else:
                auto_excluded.append(
                    {
                        "design": design,
                        "status": row.get("status", "synth_failed"),
                        "source_path": source_path,
                        "classification": "exclude",
                        "reason": "auto_exclude_missing_include_unresolved",
                        "notes": "auto-fix executor could not locate include file; excluded to avoid retry stall",
                    }
                )
                continue

        if missing_module_match or "stub_module" in action_hits:
            module_name = missing_module_match.group(1)
            ports = extract_stub_ports(module_name, rtl_files)
            widths, directions = extract_module_port_widths(module_name, rtl_files)
            if widths:
                stub_path = write_stub_module_with_widths(args.stub_dir, design, module_name, ports, widths, directions)
            else:
                stub_path = write_stub_module(args.stub_dir, design, module_name, ports)
            rtl_files.append(stub_path)
            stub_count += 1
            applied_any_fix = True
            try:
                retry_row["fix_stub_count"] = str(int(retry_row.get("fix_stub_count", "0") or 0) + 1)
            except Exception:
                retry_row["fix_stub_count"] = "1"
            retry_row["notes"] = f"auto_fix:stub_missing_module:{module_name}; {notes}".strip()

        if "sanitize_macro_definition" in action_hits:
            sanitized_source = write_sanitized_macro_copy(args.out_root, design, Path(source_path))
            if sanitized_source:
                rtl_files = [sanitized_source if path == current_source_path else path for path in rtl_files]
                if current_source_path in normalize_rtl_files(design_meta, current_source_path):
                    retry_row["source_path"] = str(sanitized_source)
                    current_source_path = sanitized_source
                applied_any_fix = True
                retry_row["notes"] = f"auto_fix:sanitize_macro_definition; {notes}".strip()

        if "template_materialization" in action_hits:
            materialized_source = materialize_known_template(args.out_root, design, current_source_path)
            if materialized_source:
                rtl_files = [materialized_source if path == current_source_path else path for path in rtl_files]
                retry_row["source_path"] = str(materialized_source)
                current_source_path = materialized_source
                applied_any_fix = True
                retry_row["notes"] = f"auto_fix:template_materialization:{materialized_source.name}; {notes}".strip()

        if "sanitize_simulation_system_tasks" in action_hits:
            sanitized_bundle = write_simulation_sanitized_bundle(args.out_root, design, rtl_files)
            if sanitized_bundle:
                original_bundle = list(rtl_files)
                rtl_files = sanitized_bundle
                if current_source_path in original_bundle:
                    source_idx = original_bundle.index(current_source_path)
                    retry_row["source_path"] = str(sanitized_bundle[source_idx])
                    current_source_path = sanitized_bundle[source_idx]
                applied_any_fix = True
                retry_row["notes"] = f"auto_fix:sanitize_simulation_system_tasks; {notes}".strip()

        if "recover_original_bundle_and_sanitize_simtasks" in action_hits and recovered_from_derived_frontend:
            sanitized_bundle = write_simulation_sanitized_bundle(args.out_root, design, rtl_files)
            if sanitized_bundle:
                original_bundle = list(rtl_files)
                rtl_files = sanitized_bundle
                if current_source_path in original_bundle:
                    source_idx = original_bundle.index(current_source_path)
                    retry_row["source_path"] = str(sanitized_bundle[source_idx])
                    current_source_path = sanitized_bundle[source_idx]
                applied_any_fix = True
                retry_row["notes"] = f"auto_fix:recover_original_bundle_and_sanitize_simtasks; {notes}".strip()
            # Do not loop back into the same sv2v artifact path for this family.
            action_hits = [action for action in action_hits if action not in {"sv2v", "set_frontend:slang"}]
            if retry_row.get("synth_frontend") == "slang":
                retry_row["synth_frontend"] = ""

        if (memory_limit_hit or any(action.startswith("set_mem_limit") for action in action_hits)) and not retry_row.get("synth_memory_max_bits"):
            retry_row["synth_memory_max_bits"] = DEFAULT_MEM_LIMIT
            memory_fix_count += 1
            applied_any_fix = True
            retry_row["notes"] = f"auto_fix:raise_synth_memory_max_bits:{DEFAULT_MEM_LIMIT}; {notes}".strip()
        if oom_hit:
            retry_row["resource_tier"] = "high"
            applied_any_fix = True
            retry_row["notes"] = f"auto_fix:resource_tier_high; {notes}".strip()

        frontend_candidates = [a for a in action_hits if a in {"sv2v", "vhd2vl"} or a.startswith("set_frontend:")]
        chosen_frontend = select_action(frontend_candidates, strategy, signature_hash)
        if chosen_frontend:
            if chosen_frontend.startswith("set_frontend:"):
                retry_row["synth_frontend"] = chosen_frontend.split(":", 1)[1].strip()
                applied_any_fix = True
                retry_row["notes"] = f"auto_fix:set_frontend:{retry_row['synth_frontend']}; {notes}".strip()
            elif chosen_frontend == "sv2v":
                applied_any_fix = True
                retry_row["notes"] = f"auto_fix:prefer_sv2v; {notes}".strip()
            elif chosen_frontend == "vhd2vl":
                applied_any_fix = True
                retry_row["notes"] = f"auto_fix:prefer_vhd2vl; {notes}".strip()
        for action in action_hits:
            if action.startswith("set_frontend:"):
                retry_row["synth_frontend"] = action.split(":", 1)[1].strip()
                applied_any_fix = True
                retry_row["notes"] = f"auto_fix:set_frontend:{retry_row['synth_frontend']}; {notes}".strip()

        if not applied_any_fix:
            continue

        retry_row["rtl_files"] = ";".join(str(path) for path in rtl_files)
        if include_dirs:
            retry_row["include_dirs"] = ";".join(str(path.resolve()) for path in sorted(set(include_dirs)))

        auto_fixed_rows.append(retry_row)
        repair_log[design] = {
            "attempts": attempts + 1,
            "last_notes": retry_row.get("notes", ""),
            "last_actions": sorted(set(action_hits)),
            "signature": signature_hash,
        }

    if auto_excluded:
        fieldnames = ["design", "status", "source_path", "classification", "reason", "notes"]
        args.exclude_csv.parent.mkdir(parents=True, exist_ok=True)
        with args.exclude_csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(exclude_rows + auto_excluded)

    if auto_fixed_rows:
        fieldnames = [
            "source",
            "design",
            "priority",
            "expected_top",
            "source_path",
            "rtl_files",
            "include_dirs",
            "synth_variant",
            "synth_memory_max_bits",
            "synth_frontend",
            "notes",
        ]
        args.retry_autofix_csv.parent.mkdir(parents=True, exist_ok=True)
        with args.retry_autofix_csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(auto_fixed_rows)

    plan = {
        "auto_excluded": len(auto_excluded),
        "patterns": [name for name, _ in AUTO_EXCLUDE_PATTERNS],
        "excluded_designs": [row["design"] for row in auto_excluded],
        "auto_fixed": len(auto_fixed_rows),
        "stub_modules": stub_count,
        "missing_include_fixes": include_fix_count,
        "memory_limit_fixes": memory_fix_count,
        "retry_candidates_csv": str(args.retry_autofix_csv) if auto_fixed_rows else "",
    }
    args.plan_json.parent.mkdir(parents=True, exist_ok=True)
    args.plan_json.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    write_repair_log(args.repair_log_json, repair_log)
    args.signatures_json.write_text(json.dumps(signature_stats, indent=2, ensure_ascii=False), encoding="utf-8")
    save_failure_families(args.failure_families_json, failure_families)
    print(args.plan_json)


if __name__ == "__main__":
    main()
