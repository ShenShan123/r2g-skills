#!/usr/bin/env python3
"""Expand screened RTL candidates into netlist graphs — the converged backend.

Replaces the source skill's bundled `expand_external_benchmark_dataset.py`
(30pt era). The acquire front-end (RTL sanitization, helper-module injection,
top detection, sv2v/vhd2vl fallbacks, LEC-lite, signature dedup) and the
bookkeeping contract (`_design_status/`, `index.csv`, `design_meta.json`) are
ported intact; the two backends are converged onto sibling r2g sub-skills:

  * synthesis  -> signoff-loop scripts/flow/run_orfs.sh with ORFS_STAGES=synth.
    Each candidate becomes a mini project dir (constraints/config.mk +
    constraint.sdc) under <workspace>/synth_projects/<design>/, so the r2g Hard
    Rules hold by construction: FLOW_VARIANT = the candidate's unique design id
    (derived from the project dir basename), one config per DESIGN_NAME+variant.
  * graph      -> def-graph scripts/extract/graph/netlist_graph.py producing
    netlist_graph.pt (the shared pre-layout netlist graph format — the 30pt
    converter is retired, see rtl-acquire-ingestion-2026-07-09.md amendment).
    Needs $R2G_GRAPH_PYTHON (torch venv); SKIPs with a HINT when absent — the
    design is then recorded as graph_skipped, NEVER success.
  * learning   -> every candidate whose flow RAN (pass or fail) is ingested into
    signoff-loop knowledge.sqlite via knowledge/ingest_run.py (honesty
    invariant: ingest after EVERY flow). config.mk carries
    `export R2G_FLOW_SCOPE = synth_only` so a synth-only pass ingests as a pass
    within its declared scope instead of a misleading 'partial'.

Env knobs (all optional; resolution via skill_env -> shared _env.sh):
  R2G_ACQUIRE_PLATFORM       target ORFS platform (default nangate45)
  R2G_ACQUIRE_SYNTH_TIMEOUT  per-candidate synth timeout seconds (default 3600)
  R2G_ACQUIRE_NUM_CORES      cap ORFS NUM_CORES per flow (Hard Rule: flows x cores ~ machine)
  R2G_ACQUIRE_SKIP_INGEST    "1" disables the knowledge ingest (unit tests)
  R2G_GRAPH_PYTHON           torch venv python for the graph stage
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = SCRIPT_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from skill_env import (  # noqa: E402
    default_flow_dir,
    default_out_root,
    default_workspace_root,
    default_yosys,
    graph_python,
    knowledge_dir,
    netlist_graph_script,
    resolve_platform_paths_script,
    resolve_str_env,
    run_orfs_script,
)

GRAPH_FORMAT = "netlist_graph_v1"
INDEX_FIELDS = [
    "design",
    "top",
    "synth_variant",
    "status",
    "cells",
    "comb_cells",
    "seq_cells",
    "nets",
    "source_path",
    "graph_format",
    "duplicate_reason",
    "notes",
]

# Corpus knowledge from the source skill: designs whose RTL infers memories
# beyond the yosys default limit. Candidates may also set synth_memory_max_bits.
MEMORY_LIMIT_DESIGNS_64K = {
    "arm_core", "core_audio_top", "zipcpu_wbdmac", "zipcpu_zipdma",
    "wbscope_wishbone", "wbscope_axil", "wbscope_avalon", "wbscope_wb_compressed",
    "verilog_ethernet_arp", "verilog_ethernet_ip_complete",
    "verilog_ethernet_ip_complete_64", "verilog_ethernet_udp_core",
    "verilog_ethernet_udp_complete", "verilog_ethernet_udp_complete_64",
    "verilog_ethernet_udp64_core", "verilog_ethernet_udp_64",
    "verilog_ethernet_eth_mac_1g_fifo", "verilog_ethernet_eth_mac_1g_gmii_fifo",
    "verilog_ethernet_eth_mac_1g_rgmii_fifo", "verilog_ethernet_eth_mac_mii_fifo",
    "verilog_axis_frame_length_adjust_fifo",
    "verilog_axis_axis_frame_length_adjust_fifo", "verilog_axis_axis_async_fifo",
    "verilog_axis_axis_fifo", "verilog_axis_axis_fifo_adapter",
    "verilog_axis_axis_async_fifo_adapter", "wb2axip_axisgdma", "wb2axip_axidma",
    "wb2axip_aximm2s", "wb2axip_axis2mm", "wb2axip_axivfifo",
    "wb2axip_axivdisplay", "wb2axip_axivcamera",
}
MEMORY_LIMIT_DESIGNS_128K = {"verilog_axis_axis_ram_switch"}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_netlist_text(text: str) -> str:
    text = re.sub(r"//.*", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def run(
    cmd: list[str],
    cwd: Path | None = None,
    capture: bool = False,
    extra_env: dict[str, str] | None = None,
    timeout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        check=False,
        text=True,
        capture_output=capture,
        env=env,
        timeout=timeout,
    )


# --- bookkeeping (contract unchanged from the source skill) -----------------

def design_status_paths(out_root: Path, design: str) -> tuple[Path, Path]:
    status_root = out_root / "_design_status"
    status_root.mkdir(parents=True, exist_ok=True)
    return status_root / f"{design}.json", status_root / f"{design}.jsonl"


def write_design_status(out_root: Path, design: str, *, stage: str, state: str,
                        details: dict | None = None) -> None:
    status_path, _ = design_status_paths(out_root, design)
    payload = {"design": design, "stage": stage, "state": state, "updated_at": now_iso()}
    if details:
        payload.update(details)
    status_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def append_design_stage(out_root: Path, design: str, *, stage: str, state: str,
                        details: dict | None = None) -> None:
    _, stage_log = design_status_paths(out_root, design)
    event = {"design": design, "stage": stage, "state": state, "timestamp": now_iso()}
    if details:
        event.update(details)
    with stage_log.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")


def write_index(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=INDEX_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def load_existing_index(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    with path.open("r", newline="", encoding="utf-8") as fh:
        return {row["design"]: row for row in csv.DictReader(fh)}


def restore_rows_from_output_root(out_root: Path) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    for meta_path in sorted(out_root.glob("*/design_meta.json")):
        design = meta_path.parent.name
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        stats_path = meta_path.parent / "cell_stats.json"
        stats = {}
        if stats_path.exists():
            try:
                stats = json.loads(stats_path.read_text(encoding="utf-8"))
            except Exception:
                stats = {}
        rows[design] = {
            "design": design,
            "top": str(meta.get("top", "")),
            "synth_variant": str(meta.get("synth_variant", "")),
            "status": str(meta.get("status", "")),
            "cells": str(stats.get("cells", "")),
            "comb_cells": str(stats.get("comb_cells", "")),
            "seq_cells": str(stats.get("seq_cells", "")),
            "nets": str(stats.get("nets", "")),
            "source_path": str(meta.get("rtl_files", [""])[0] if meta.get("rtl_files") else ""),
            "graph_format": str(meta.get("graph_schema_version") or GRAPH_FORMAT),
            "duplicate_reason": str(meta.get("duplicate_reason", "")),
            "notes": str(meta.get("notes", "")),
        }
    return rows


# --- RTL front-end (ported intact from the source skill) --------------------

def make_minimal_sdc(out_path: Path) -> None:
    text = """set candidates [get_ports -quiet {clk clock i_clk i_clock clock_i clk_i wb_clk_i wb_clk clock_in core_clk CK}]
if {[llength $candidates] > 0} {
  create_clock -name core_clk -period 10 [lindex $candidates 0]
} else {
  create_clock -name virtual_clk -period 10
}
"""
    out_path.write_text(text, encoding="utf-8")


def extract_module_names(source_text: str) -> list[str]:
    return re.findall(r"(?m)^\s*module\s+([A-Za-z_][A-Za-z0-9_$]*)\b", source_text)


def choose_top_name(source_paths: list[Path], expected_top: str) -> str:
    module_names: list[str] = []
    for source_path in source_paths:
        text = source_path.read_text(encoding="utf-8", errors="ignore")
        module_names.extend(extract_module_names(text))
    if expected_top in module_names:
        return expected_top
    if len(module_names) == 1:
        return module_names[0]
    for name in module_names:
        if name != "dff":
            return name
    return expected_top


def rewrite_iscas89_legacy_dff(source_text: str) -> str:
    pattern = re.compile(r"module\s+dff\b.*?endmodule", flags=re.S)
    match = pattern.search(source_text)
    if not match:
        return source_text
    dff_block = match.group(0)
    if "trireg" not in dff_block and "nmos" not in dff_block:
        return source_text
    replacement = """module dff (CK,Q,D);
input CK,D;
output reg Q;
always @ (posedge CK)
  Q <= D;
endmodule"""
    return source_text[: match.start()] + replacement + source_text[match.end():]


def _strip_control_chars(text: str) -> tuple[str, bool]:
    cleaned = []
    changed = False
    for ch in text:
        code = ord(ch)
        if code == 0:
            changed = True
            continue
        if code < 32 and ch not in ("\n", "\r", "\t"):
            changed = True
            continue
        cleaned.append(ch)
    return "".join(cleaned), changed


def read_and_sanitize_rtl(path: Path) -> tuple[str, bool]:
    data = path.read_bytes()
    changed = False
    text: str | None = None
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        try:
            text = data.decode("utf-16")
            changed = True
        except Exception:
            text = None
    if text is None:
        try:
            text = data.decode("utf-8-sig")
            if data.startswith(b"\xef\xbb\xbf"):
                changed = True
        except Exception:
            try:
                text = data.decode("utf-8", errors="replace")
                changed = True
            except Exception:
                text = data.decode("latin-1", errors="replace")
                changed = True
    text, stripped = _strip_control_chars(text)
    return text, changed or stripped


def sv2v_path() -> str | None:
    configured = resolve_str_env("R2G_ACQUIRE_SV2V_BIN", "")
    if configured and Path(configured).expanduser().exists():
        return str(Path(configured).expanduser())
    return shutil.which("sv2v")


def vhd2vl_path() -> str | None:
    configured = resolve_str_env("R2G_ACQUIRE_VHD2VL_BIN", "")
    if configured and Path(configured).expanduser().exists():
        return str(Path(configured).expanduser())
    return shutil.which("vhd2vl")


def run_sv2v(out_root: Path, design: str, source_files: list[Path],
             include_dirs: list[Path], top: str | None) -> Path | None:
    exe = sv2v_path()
    if not exe:
        return None
    temp_dir = out_root / "_tmp_cfg"
    temp_dir.mkdir(parents=True, exist_ok=True)
    out_path = temp_dir / f"{design}_sv2v.v"
    cmd = [exe]
    for inc in include_dirs:
        cmd.append(f"-I{inc}")
    if top:
        cmd.append(f"--top={top}")
    cmd.extend(str(path) for path in source_files)
    result = run(cmd, cwd=out_root, capture=True)
    if result.returncode != 0:
        out_path.write_text(f"// sv2v failed: {result.stdout}\n// {result.stderr}\n", encoding="utf-8")
        return None
    out_path.write_text(result.stdout, encoding="utf-8")
    return out_path


def run_vhd2vl(out_root: Path, design: str, source_files: list[Path]) -> Path | None:
    exe = vhd2vl_path()
    if not exe:
        return None
    temp_dir = out_root / "_tmp_cfg"
    temp_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    for idx, path in enumerate(source_files):
        if path.suffix.lower() not in {".vhd", ".vhdl"}:
            continue
        out_path = temp_dir / f"{design}_vhd2vl_{idx}.v"
        result = run([exe, str(path), str(out_path)], cwd=out_root, capture=True)
        if result.returncode != 0 or not out_path.exists():
            return None
        outputs.append(out_path)
    if not outputs:
        return None
    combined = temp_dir / f"{design}_vhd2vl.v"
    combined.write_text(
        "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in outputs) + "\n",
        encoding="utf-8",
    )
    return combined


def looks_like_frontend_failure(text: str) -> bool:
    lower = (text or "").lower()
    keywords = ["syntax error", "parse error", "parser", "unexpected", "front-end",
                "frontend", "sv", "systemverilog", "error: found"]
    return any(key in lower for key in keywords)


def looks_like_vhdl_failure(text: str) -> bool:
    lower = (text or "").lower()
    return any(key in lower for key in ("vhdl", "entity", "architecture", "library ieee"))


def run_lec_lite(out_root: Path, design: str, original_files: list[Path],
                 converted_file: Path, top: str, *, max_cells: int) -> tuple[str, str]:
    if not original_files:
        return "skipped", "no_original_files"
    if max_cells > 20000:
        return "skipped", "too_large_for_lec_lite"
    temp_dir = out_root / "_tmp_cfg"
    temp_dir.mkdir(parents=True, exist_ok=True)
    script = temp_dir / f"{design}_lec_lite.ys"
    gold = temp_dir / f"{design}_lec_gold.v"
    gate = temp_dir / f"{design}_lec_gate.v"
    script.write_text(
        "\n".join(
            [
                "read_verilog -sv " + " ".join(str(p) for p in original_files),
                f"prep -top {top} -flatten",
                f"rename {top} gold",
                f"write_verilog {gold}",
                "design -reset",
                f"read_verilog -sv {converted_file}",
                f"prep -top {top} -flatten",
                f"rename {top} gate",
                f"write_verilog {gate}",
                "design -reset",
                f"read_verilog {gold}",
                f"read_verilog {gate}",
                "equiv_make gold gate equiv",
                "equiv_simple",
                "equiv_status -assert",
            ]
        ),
        encoding="utf-8",
    )
    result = run([default_yosys(), "-q", "-s", str(script)], cwd=out_root, capture=True)
    if result.returncode == 0:
        return "pass", ""
    return "fail", (result.stdout + "\n" + result.stderr).strip()[:2000]


# helper-module injection (iscas89 dff, delta, add_add_module, RAM stubs, altpll)

def needs_external_dff_helper(source_path: Path) -> bool:
    if "hdl-benchmarks-min/iscas89/verilog" not in str(source_path):
        return False
    text = source_path.read_text(encoding="utf-8", errors="ignore")
    return "dff " in text and "module dff" not in text


def _write_helper(out_root: Path, name: str, body: str) -> Path:
    temp_dir = out_root / "_tmp_cfg"
    temp_dir.mkdir(parents=True, exist_ok=True)
    helper_path = temp_dir / name
    helper_path.write_text(body, encoding="utf-8")
    return helper_path


HELPERS: list[tuple[str, str, str]] = [
    # (trigger substring, module name, verilog body)
    (" delta ", "delta", "module delta (D, Q);\ninput D;\noutput Q;\nassign Q = D;\nendmodule\n"),
    ("altpll", "altpll",
     "module altpll (inclk, clk, locked);\ninput [1:0] inclk;\noutput [4:0] clk;\noutput locked;\n"
     "assign clk = {5{inclk[0]}};\nassign locked = 1'b1;\nendmodule\n"),
    ("single_port_ram", "single_port_ram",
     "module single_port_ram (clk, we, data, out, addr);\n"
     "parameter ADDR_WIDTH = 8;\nparameter DATA_WIDTH = 32;\n"
     "input clk;\ninput we;\ninput [DATA_WIDTH-1:0] data;\ninput [ADDR_WIDTH-1:0] addr;\n"
     "output [DATA_WIDTH-1:0] out;\n"
     "reg [DATA_WIDTH-1:0] mem [0:(1<<ADDR_WIDTH)-1];\nreg [DATA_WIDTH-1:0] out_reg;\n"
     "assign out = out_reg;\nalways @(posedge clk) begin\n  if (we)\n    mem[addr] <= data;\n"
     "  out_reg <= mem[addr];\nend\nendmodule\n"),
    ("dual_port_ram", "dual_port_ram",
     "module dual_port_ram (clk, we1, we2, data1, data2, out1, out2, addr1, addr2);\n"
     "parameter ADDR_WIDTH = 8;\nparameter DATA_WIDTH = 32;\n"
     "input clk;\ninput we1, we2;\ninput [DATA_WIDTH-1:0] data1, data2;\n"
     "input [ADDR_WIDTH-1:0] addr1, addr2;\noutput [DATA_WIDTH-1:0] out1, out2;\n"
     "reg [DATA_WIDTH-1:0] mem [0:(1<<ADDR_WIDTH)-1];\n"
     "reg [DATA_WIDTH-1:0] out1_reg, out2_reg;\nassign out1 = out1_reg;\nassign out2 = out2_reg;\n"
     "always @(posedge clk) begin\n  if (we1)\n    mem[addr1] <= data1;\n"
     "  if (we2)\n    mem[addr2] <= data2;\n  out1_reg <= mem[addr1];\n  out2_reg <= mem[addr2];\n"
     "end\nendmodule\n"),
]

DFF_HELPER_BODY = (
    "module dff (CK,Q,D);\ninput CK,D;\noutput Q;\nreg Q;\n"
    "always @ (posedge CK)\n  Q <= D;\nendmodule\n"
)
ADD_ADD_MODULE_BODY = (
    "module add_add_module (x_top, y_top, q_top);\ninput [1:0] x_top;\ninput [1:0] y_top;\n"
    "output [1:0] q_top;\nadd_module impl (.x_add2(x_top), .y_add2(y_top), .q_add2(q_top));\nendmodule\n"
)


def build_source_files(out_root: Path, design: str, source_paths: list[Path]) -> list[Path]:
    source_files: list[Path] = []
    combined_source_texts: list[str] = []
    for idx, source_path in enumerate(source_paths):
        source_text, sanitized = read_and_sanitize_rtl(source_path)
        combined_source_texts.append(source_text)
        rewritten_text = rewrite_iscas89_legacy_dff(source_text)
        if rewritten_text != source_text or sanitized:
            sanitized_path = _write_helper(out_root, f"{design}_sanitized_{idx}.v", rewritten_text)
            source_files.append(sanitized_path)
        else:
            source_files.append(source_path)
    combined = "\n".join(combined_source_texts)
    if any(needs_external_dff_helper(p) for p in source_paths):
        source_files.append(_write_helper(out_root, f"{design}_dff_helper.v", DFF_HELPER_BODY))
    if ("add_add_module" in combined and "module add_add_module" not in combined
            and "module add_module" in combined):
        source_files.append(_write_helper(out_root, f"{design}_add_add_module_helper.v",
                                          ADD_ADD_MODULE_BODY))
    for trigger, module, body in HELPERS:
        if trigger in combined and f"module {module}" not in combined:
            source_files.append(_write_helper(out_root, f"{design}_{module}_helper.v", body))
    return source_files


def parse_candidate_source_paths(candidate: dict[str, str]) -> list[Path]:
    rtl_files_raw = (candidate.get("rtl_files") or "").strip()
    if rtl_files_raw:
        raw_parts = re.split(r"[;|]", rtl_files_raw)
        ordered: list[Path] = []
        primary = (candidate.get("source_path") or "").strip()
        if primary:
            ordered.append(Path(primary))
        for part in raw_parts:
            part = part.strip()
            if part and Path(part) not in ordered:
                ordered.append(Path(part))
        return ordered
    return [Path(candidate["source_path"])]


def parse_candidate_include_dirs(candidate: dict[str, str]) -> list[Path]:
    raw = (candidate.get("include_dirs") or "").strip()
    if not raw:
        return []
    return [Path(p.strip()) for p in re.split(r"[;|]", raw) if p.strip()]


# --- converged synthesis: per-candidate project + run_orfs.sh ---------------

def write_project(
    projects_root: Path,
    design: str,
    top: str,
    synth_variant: str,
    source_files: list[Path],
    source_dir: Path,
    extra_include_dirs: list[Path],
    notes: str,
    synth_memory_max_bits: str | None,
    synth_frontend: str | None,
    top_parameters: dict[str, str] | None,
) -> Path:
    """Write <projects_root>/<design>/constraints/{config.mk,constraint.sdc}."""
    project = projects_root / design
    constraints = project / "constraints"
    constraints.mkdir(parents=True, exist_ok=True)
    (project / "reports").mkdir(parents=True, exist_ok=True)
    sdc_path = constraints / "constraint.sdc"
    make_minimal_sdc(sdc_path)

    variant = (synth_variant or "yosys_abc_area0").strip()
    abc_area = 1 if variant in {"area", "abc_area1", "yosys_abc_area1"} else 0
    lines = [
        f"export DESIGN_NAME = {top}",
        f"export PLATFORM = {acquire_platform()}",
        f"export SDC_FILE = {sdc_path}",
        f"export ABC_AREA = {abc_area}",
        f"export SYNTH_VARIANT = {variant}",
        # Scope marker: ingest_run.py derives pass/fail against synth only
        # (a synth-only pass must not ingest as a misleading 'partial').
        "export R2G_FLOW_SCOPE = synth_only",
    ]
    base_design = design.split("__", 1)[0]
    if synth_memory_max_bits:
        lines.append(f"export SYNTH_MEMORY_MAX_BITS = {synth_memory_max_bits}")
    elif base_design in MEMORY_LIMIT_DESIGNS_64K:
        lines.append("export SYNTH_MEMORY_MAX_BITS = 65536")
    elif base_design in MEMORY_LIMIT_DESIGNS_128K:
        lines.append("export SYNTH_MEMORY_MAX_BITS = 131072")
    if synth_frontend:
        lines.append(f"export SYNTH_HDL_FRONTEND = {synth_frontend}")
    elif any(path.suffix.lower() == ".sv" for path in source_files):
        lines.append("export SYNTH_HDL_FRONTEND = slang")
    elif notes and "flattened systemverilog design" in notes:
        lines.append("export SYNTH_HDL_FRONTEND = slang")
    if top_parameters:
        ordered = " ".join(f"{k} {v}" for k, v in top_parameters.items())
        lines.append(f"export VERILOG_TOP_PARAMS = {{{ordered}}}")
    lines.append("export VERILOG_FILES = " + " ".join(str(p) for p in source_files))
    include_dirs = sorted(
        {str(p.parent.resolve()) for p in source_files}
        | {str(source_dir.resolve())}
        | {str(p.resolve()) for p in extra_include_dirs}
    )
    lines.append("export VERILOG_INCLUDE_DIRS = " + " ".join(include_dirs))
    (constraints / "config.mk").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return project


def acquire_platform() -> str:
    return resolve_str_env("R2G_ACQUIRE_PLATFORM", "nangate45")


def synthesize(project: Path, design: str, top: str) -> tuple[int, Path | None, Path | None]:
    """Run synth-only ORFS via signoff-loop run_orfs.sh.

    Returns (rc, netlist_path_or_None, backend_run_dir_or_None).
    """
    platform = acquire_platform()
    extra_env = {
        "ORFS_STAGES": "synth",
        "ORFS_TIMEOUT": resolve_str_env("R2G_ACQUIRE_SYNTH_TIMEOUT", "3600"),
    }
    num_cores = resolve_str_env("R2G_ACQUIRE_NUM_CORES", "")
    if num_cores:
        extra_env["NUM_CORES"] = num_cores
    result = run(
        ["bash", str(run_orfs_script()), str(project), platform, design],
        capture=True,
        extra_env=extra_env,
    )
    # Newest backend run dir holds flow.log + stage_log.jsonl (+ collected results).
    backend = project / "backend"
    run_dirs = sorted(
        (d for d in backend.iterdir() if d.is_dir() and d.name.startswith("RUN_")),
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    ) if backend.is_dir() else []
    run_dir = run_dirs[0] if run_dirs else None

    # Current ORFS emits the canonicalized mapped netlist as 1_2_yosys.v; older
    # checkouts used 1_1_yosys.v. Prefer the flow results dir, fall back to the
    # copy run_orfs.sh collected into backend/RUN_*/results/.
    results_dir = default_flow_dir() / "results" / platform / top / design
    search_dirs = [results_dir] + ([run_dir / "results"] if run_dir is not None else [])
    netlist: Path | None = None
    for d in search_dirs:
        for name in ("1_2_yosys.v", "1_1_yosys.v"):
            candidate = d / name
            if candidate.exists():
                netlist = candidate
                break
        if netlist is not None:
            break
    return result.returncode, netlist, run_dir


def synth_log_from(run_dir: Path | None, dest: Path) -> None:
    candidates = []
    if run_dir is not None:
        candidates = [
            run_dir / "logs" / "1_1_yosys.log",
            run_dir / "logs" / "1_1_yosys_canonicalize.log",
            run_dir / "flow.log",
        ]
    for path in candidates:
        if path.exists():
            shutil.copyfile(path, dest)
            return
    dest.write_text("", encoding="utf-8")


def summarize_synth_failure(synth_log_path: Path) -> str:
    if not synth_log_path.exists():
        return "synthesis_failed"
    lines = synth_log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    tail = " | ".join(lines[-8:]).strip()
    return tail[:1000] if tail else "synthesis_failed"


def cleanup_orfs_design_artifacts(design: str, top: str) -> None:
    # The canonical corpus artifacts are copied under out_root; ORFS work dirs
    # are reproducible and accumulate many GB over closed-loop rounds.
    flow_dir = default_flow_dir()
    platform = acquire_platform()
    for subdir in ("results", "logs", "reports", "objects"):
        path = flow_dir / subdir / platform / top / design
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
    cfg = flow_dir / "designs" / platform / top / design
    if cfg.exists():
        shutil.rmtree(cfg, ignore_errors=True)


# --- converged graph stage: def-graph netlist_graph.py ----------------------

def _resolve_lib_env(config_mk: Path) -> dict[str, str]:
    """LIB env for netlist_graph.py — the same R2G_LIB_FILES / R2G_SC_LIB_FILES
    split def-graph's run_graphs.sh exports (std-cell vocabulary keyed on the
    std-only subset; full liberty for the lib_db)."""
    platform = acquire_platform()
    result = run(
        ["bash", str(resolve_platform_paths_script()), str(config_mk), platform],
        capture=True,
    )
    lib_files, additional = "", ""
    for line in (result.stdout or "").splitlines():
        if line.startswith("LIB_FILES="):
            lib_files = line[len("LIB_FILES="):]
        elif line.startswith("ADDITIONAL_LIBS="):
            additional = line[len("ADDITIONAL_LIBS="):]
    additional_set = set(additional.split())
    sc_lib = " ".join(t for t in lib_files.split() if t not in additional_set)
    return {
        "R2G_PLATFORM": platform,
        "R2G_LIB_FILES": (lib_files + " " + additional).strip(),
        "R2G_SC_LIB_FILES": sc_lib,
    }


def graph_convert(netlist: Path, out_pt: Path, design: str, config_mk: Path,
                  cell_stats_json: Path) -> tuple[str, str]:
    """Returns (state, log): state in {ok, skipped, failed}."""
    gpython = graph_python()
    if not gpython:
        return "skipped", (
            "HINT: R2G_GRAPH_PYTHON is not set — netlist_graph.pt needs the torch venv. "
            "Provision with eda-install (bootstrap.sh) or point R2G_GRAPH_PYTHON at a "
            "python with torch+torch_geometric. The design is recorded as graph_skipped."
        )
    lib_env = _resolve_lib_env(config_mk)
    result = run(
        [gpython, str(netlist_graph_script()), str(netlist), str(out_pt), design],
        capture=True,
        extra_env=lib_env,
    )
    if result.returncode != 0 or not out_pt.exists():
        return "failed", (result.stdout + "\n" + result.stderr).strip()[-2000:]
    stats_result = run(
        [gpython, str(SCRIPT_DIR / "graph_stats.py"), "--pt", str(out_pt),
         "--netlist", str(netlist), "--out", str(cell_stats_json)],
        capture=True,
        extra_env=lib_env,
    )
    if stats_result.returncode != 0 or not cell_stats_json.exists():
        return "failed", ("graph_stats failed: "
                          + (stats_result.stdout + "\n" + stats_result.stderr).strip()[-1500:])
    return "ok", ""


# --- converged learning: ingest every flow into knowledge.sqlite ------------

def ingest_project(project: Path) -> str:
    """Honesty invariant: ingest after EVERY flow (pass or fail). Non-fatal."""
    if resolve_str_env("R2G_ACQUIRE_SKIP_INGEST", "") == "1":
        return "skipped_by_env"
    ingest = knowledge_dir() / "ingest_run.py"
    if not ingest.exists():
        return f"missing_ingest_script:{ingest}"
    db = resolve_str_env("R2G_KNOWLEDGE_DB", "")
    cmd = [sys.executable, str(ingest), str(project)]
    if db:
        cmd += ["--db", db]
    result = run(cmd, capture=True)
    if result.returncode != 0:
        return "failed: " + (result.stderr or result.stdout or "").strip()[-500:]
    return "ok"


def load_cell_stats(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def parse_candidate_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Synthesize screened RTL candidates (ORFS synth-only) and "
                    "convert to netlist_graph.pt corpus entries.")
    parser.add_argument("--candidate-csv", type=Path, required=True)
    parser.add_argument("--out-root", type=Path, default=None)
    parser.add_argument("--projects-root", type=Path, default=None,
                        help="Where per-candidate ORFS project dirs are staged "
                             "(default <workspace>/synth_projects)")
    parser.add_argument("--priorities", nargs="*", default=["high"])
    parser.add_argument("--candidate-names", nargs="*")
    parser.add_argument("--allow-signature-duplicates", action="store_true")
    args = parser.parse_args()

    out_root = args.out_root or default_out_root()
    projects_root = args.projects_root or (default_workspace_root() / "synth_projects")
    out_root.mkdir(parents=True, exist_ok=True)
    projects_root.mkdir(parents=True, exist_ok=True)

    candidates = parse_candidate_csv(args.candidate_csv)
    if args.priorities:
        allowed = set(args.priorities)
        candidates = [row for row in candidates if row.get("priority") in allowed]
    if args.candidate_names:
        order = {name: idx for idx, name in enumerate(args.candidate_names)}
        candidates = [row for row in candidates if row.get("design") in order]
        candidates.sort(key=lambda row: order[row["design"]])

    index_path = out_root / "index.csv"
    rows_by_design = restore_rows_from_output_root(out_root)
    rows_by_design.update(load_existing_index(index_path))
    seen_rtl_sigs: dict[str, str] = {}
    seen_netlist_sigs: dict[str, str] = {}
    candidate_order = {row["design"]: idx
                       for idx, row in enumerate(parse_candidate_csv(args.candidate_csv))}

    def flush_index() -> None:
        ordered = sorted(rows_by_design,
                         key=lambda d: (candidate_order.get(d, 10**9), d))
        write_index(index_path, [rows_by_design[d] for d in ordered])

    for candidate in candidates:
        design = candidate["design"]
        print(f"[rtl-acquire] design={design} stage=queued", flush=True)
        write_design_status(out_root, design, stage="queued", state="running",
                            details={"graph_format": GRAPH_FORMAT})
        append_design_stage(out_root, design, stage="queued", state="start",
                            details={"source_path": candidate.get("source_path", "")})
        synth_variant = (candidate.get("synth_variant") or "yosys_abc_area0").strip()
        synth_memory_max_bits = (candidate.get("synth_memory_max_bits") or "").strip() or None
        synth_frontend = (candidate.get("synth_frontend") or "").strip() or None
        top_params_raw = (candidate.get("top_parameters") or "").strip()
        top_parameters: dict[str, str] = {}
        for part in re.split(r"[;,|]", top_params_raw):
            part = part.strip()
            if part and "=" in part:
                key, value = part.split("=", 1)
                if key.strip():
                    top_parameters[key.strip()] = value.strip()

        existing_row = rows_by_design.get(design)
        if existing_row and existing_row.get("status") == "success":
            write_design_status(out_root, design, stage="skip_existing_success",
                                state="completed", details={"status": "success"})
            append_design_stage(out_root, design, stage="skip_existing_success", state="completed")
            continue

        source_paths = parse_candidate_source_paths(candidate)
        include_dirs = parse_candidate_include_dirs(candidate)
        source_path = source_paths[0]
        notes = candidate.get("notes", "")
        if any(not path.exists() for path in source_paths):
            write_design_status(out_root, design, stage="source_check", state="failed",
                                details={"status": "unsupported", "notes": "missing_source_file"})
            append_design_stage(out_root, design, stage="source_check", state="failed",
                                details={"notes": "missing_source_file"})
            rows_by_design[design] = {
                "design": design, "top": candidate.get("expected_top", ""),
                "synth_variant": synth_variant, "status": "unsupported",
                "cells": "", "comb_cells": "", "seq_cells": "", "nets": "",
                "source_path": str(source_path), "graph_format": GRAPH_FORMAT,
                "duplicate_reason": "", "notes": "missing_source_file",
            }
            flush_index()
            continue

        top = choose_top_name(source_paths, candidate.get("expected_top", ""))
        source_files = build_source_files(out_root, design, source_paths)

        design_out = out_root / design
        design_out.mkdir(parents=True, exist_ok=True)
        meta_path = design_out / "design_meta.json"
        src_manifest = design_out / "src_manifest.txt"
        synth_log_path = design_out / "synth.log"
        cell_stats_json = design_out / "cell_stats.json"
        mapped_netlist = design_out / "mapped_netlist.v"
        out_pt = design_out / "netlist_graph.pt"

        row = {
            "design": design, "top": top, "synth_variant": synth_variant,
            "status": "", "cells": "", "comb_cells": "", "seq_cells": "", "nets": "",
            "source_path": str(source_path), "graph_format": GRAPH_FORMAT,
            "duplicate_reason": "", "notes": notes,
        }
        meta: dict[str, object] = {
            "design": design, "top": top, "synth_variant": synth_variant,
            "synth_memory_max_bits": synth_memory_max_bits,
            "synth_frontend": synth_frontend, "top_parameters": top_parameters,
            "platform": acquire_platform(), "graph_schema_version": GRAPH_FORMAT,
        }
        flow_ran = False
        project: Path | None = None
        try:
            append_design_stage(out_root, design, stage="config", state="start",
                                details={"top": top, "synth_variant": synth_variant})
            project = write_project(
                projects_root, design, top, synth_variant, source_files,
                source_path.parent, include_dirs, notes,
                synth_memory_max_bits, synth_frontend, top_parameters or None,
            )
            rtl_files = [str(p) for p in source_files]
            src_manifest.write_text("\n".join(rtl_files) + "\n", encoding="utf-8")
            meta["design_config"] = str(project / "constraints" / "config.mk")
            meta["rtl_files"] = rtl_files
            write_design_status(out_root, design, stage="config", state="completed",
                                details={"top": top, "rtl_file_count": len(rtl_files),
                                         "synth_variant": synth_variant})
            append_design_stage(out_root, design, stage="config", state="completed",
                                details={"rtl_file_count": len(rtl_files)})

            original_source_files = list(source_files)
            fallback_used: str | None = None
            fallback_converted: Path | None = None
            # Multi-top repositories (e.g. picorv32) contribute distinct samples
            # from the same RTL bundle; exact same-(top,bundle) candidates dedup.
            rtl_sig = sha256_text(top + "\n" + "\n".join(sorted(rtl_files)))
            meta["rtl_signature"] = rtl_sig
            if not args.allow_signature_duplicates and rtl_sig in seen_rtl_sigs:
                row["status"] = "duplicate"
                row["duplicate_reason"] = f"rtl_signature_matches:{seen_rtl_sigs[rtl_sig]}"
                write_design_status(out_root, design, stage="dedup_rtl_signature",
                                    state="completed",
                                    details={"status": "duplicate",
                                             "duplicate_reason": row["duplicate_reason"]})
                append_design_stage(out_root, design, stage="dedup_rtl_signature",
                                    state="completed",
                                    details={"matched_design": seen_rtl_sigs[rtl_sig]})
                meta.update({"status": "duplicate", "reason": row["duplicate_reason"]})
                meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
                rows_by_design[design] = row
                flush_index()
                continue

            write_design_status(out_root, design, stage="synthesize", state="running",
                                details={"top": top, "synth_variant": synth_variant})
            append_design_stage(out_root, design, stage="synthesize", state="start")
            flow_ran = True
            code, netlist, run_dir = synthesize(project, design, top)
            synth_log_from(run_dir, synth_log_path)
            if netlist is None:
                failure_note = summarize_synth_failure(synth_log_path)
                has_vhdl = any(p.suffix.lower() in {".vhd", ".vhdl"} for p in source_files)
                if has_vhdl or looks_like_vhdl_failure(failure_note):
                    vhd2vl_out = run_vhd2vl(out_root, design, source_files)
                    if vhd2vl_out:
                        source_files = [vhd2vl_out]
                        project = write_project(
                            projects_root, design, top, synth_variant, source_files,
                            source_path.parent, include_dirs,
                            f"{notes}; vhd2vl_fallback".strip("; "),
                            synth_memory_max_bits, synth_frontend, top_parameters or None)
                        rtl_files = [str(p) for p in source_files]
                        src_manifest.write_text("\n".join(rtl_files) + "\n", encoding="utf-8")
                        code, netlist, run_dir = synthesize(project, design, top)
                        synth_log_from(run_dir, synth_log_path)
                        if netlist is not None:
                            row["notes"] = f"{notes}; vhd2vl_fallback".strip("; ")
                            fallback_used = "vhd2vl"
                            fallback_converted = vhd2vl_out
                            append_design_stage(out_root, design, stage="synthesize",
                                                state="fallback_vhd2vl_success")
                        else:
                            failure_note = summarize_synth_failure(synth_log_path)
                    else:
                        failure_note = f"{failure_note} | vhd2vl_fallback_failed"

                has_sv = any(p.suffix.lower() == ".sv" for p in source_files)
                if netlist is None and (has_sv or looks_like_frontend_failure(failure_note)):
                    sv2v_out = run_sv2v(out_root, design, source_files, include_dirs, top)
                    if sv2v_out:
                        source_files = [sv2v_out]
                        project = write_project(
                            projects_root, design, top, synth_variant, source_files,
                            source_path.parent, include_dirs,
                            f"{notes}; sv2v_fallback".strip("; "),
                            synth_memory_max_bits, synth_frontend, top_parameters or None)
                        rtl_files = [str(p) for p in source_files]
                        src_manifest.write_text("\n".join(rtl_files) + "\n", encoding="utf-8")
                        code, netlist, run_dir = synthesize(project, design, top)
                        synth_log_from(run_dir, synth_log_path)
                        if netlist is not None:
                            row["notes"] = f"{notes}; sv2v_fallback".strip("; ")
                            fallback_used = "sv2v"
                            fallback_converted = sv2v_out
                            append_design_stage(out_root, design, stage="synthesize",
                                                state="fallback_sv2v_success")
                        else:
                            failure_note = summarize_synth_failure(synth_log_path)
                    else:
                        failure_note = f"{failure_note} | sv2v_fallback_failed"

                if netlist is None:
                    row["status"] = "synth_failed"
                    row["notes"] = failure_note
                    print(f"[rtl-acquire] design={design} stage=synthesize status=synth_failed",
                          flush=True)
                    write_design_status(out_root, design, stage="synthesize", state="failed",
                                        details={"status": "synth_failed",
                                                 "notes": row["notes"][:500]})
                    append_design_stage(out_root, design, stage="synthesize", state="failed",
                                        details={"notes": row["notes"][:500]})
                    meta.update({"status": "synth_failed", "notes": row["notes"],
                                 "rtl_files": rtl_files,
                                 "sv2v_fallback_used": "sv2v_fallback" in row.get("notes", ""),
                                 "vhd2vl_fallback_used": "vhd2vl_fallback" in row.get("notes", "")})
                    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
                    rows_by_design[design] = row
                    flush_index()
                    continue

            write_design_status(out_root, design, stage="synthesize", state="completed",
                                details={"status": "synth_ok"})
            append_design_stage(out_root, design, stage="synthesize", state="completed")

            shutil.copyfile(netlist, mapped_netlist)
            net_sig = sha256_text(
                normalize_netlist_text(mapped_netlist.read_text(encoding="utf-8", errors="ignore")))
            meta["mapped_netlist_signature"] = net_sig
            if not args.allow_signature_duplicates and net_sig in seen_netlist_sigs:
                row["status"] = "duplicate"
                row["duplicate_reason"] = f"netlist_signature_matches:{seen_netlist_sigs[net_sig]}"
                write_design_status(out_root, design, stage="dedup_netlist_signature",
                                    state="completed",
                                    details={"status": "duplicate",
                                             "duplicate_reason": row["duplicate_reason"]})
                append_design_stage(out_root, design, stage="dedup_netlist_signature",
                                    state="completed",
                                    details={"matched_design": seen_netlist_sigs[net_sig]})
                meta.update({"status": "duplicate", "duplicate_reason": row["duplicate_reason"]})
                meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
                rows_by_design[design] = row
                flush_index()
                continue

            write_design_status(out_root, design, stage="graph_convert", state="running",
                                details={"mapped_netlist": str(mapped_netlist)})
            append_design_stage(out_root, design, stage="graph_convert", state="start")
            config_mk = project / "constraints" / "config.mk"
            graph_state, graph_log = graph_convert(mapped_netlist, out_pt, design,
                                                   config_mk, cell_stats_json)
            if graph_state == "skipped":
                row["status"] = "graph_skipped"
                row["notes"] = graph_log[:500]
                print(f"[rtl-acquire] design={design} stage=graph_convert status=graph_skipped",
                      flush=True)
                print(graph_log, flush=True)
                write_design_status(out_root, design, stage="graph_convert", state="skipped",
                                    details={"status": "graph_skipped", "notes": graph_log[:300]})
                append_design_stage(out_root, design, stage="graph_convert", state="skipped")
                meta.update({"status": "graph_skipped", "notes": graph_log[:500]})
                meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
                rows_by_design[design] = row
                flush_index()
                continue
            if graph_state == "failed":
                row["status"] = "graph_failed"
                row["notes"] = graph_log[-1500:]
                print(f"[rtl-acquire] design={design} stage=graph_convert status=graph_failed",
                      flush=True)
                write_design_status(out_root, design, stage="graph_convert", state="failed",
                                    details={"status": "graph_failed", "notes": row["notes"][:500]})
                append_design_stage(out_root, design, stage="graph_convert", state="failed",
                                    details={"notes": row["notes"][:500]})
                meta.update({"status": "graph_failed", "notes": row["notes"]})
                meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
                rows_by_design[design] = row
                flush_index()
                continue
            write_design_status(out_root, design, stage="graph_convert", state="completed",
                                details={"status": "graph_ok", "graph_format": GRAPH_FORMAT})
            append_design_stage(out_root, design, stage="graph_convert", state="completed",
                                details={"graph_format": GRAPH_FORMAT})

            stats = load_cell_stats(cell_stats_json)
            lec_status, lec_notes = "skipped", ""
            if fallback_used == "sv2v" and fallback_converted:
                lec_files = [p for p in original_source_files
                             if p.suffix.lower() in {".v", ".sv"}]
                lec_status, lec_notes = run_lec_lite(
                    out_root, design, lec_files, fallback_converted, top,
                    max_cells=int(stats.get("cells", 0) or 0))
                stats["lec_lite_status"] = lec_status
                if lec_notes:
                    stats["lec_lite_notes"] = lec_notes[:500]
                if lec_status == "fail":
                    stats["degraded_quality"] = True
                cell_stats_json.write_text(json.dumps(stats, indent=2), encoding="utf-8")

            for key in ("cells", "comb_cells", "seq_cells", "nets"):
                row[key] = str(stats.get(key, ""))
            row["status"] = "success"
            meta.update({
                "status": "success", "duplicate_reason": "", "notes": notes,
                "rtl_files": rtl_files,
                "sv2v_fallback_used": fallback_used == "sv2v",
                "vhd2vl_fallback_used": fallback_used == "vhd2vl",
                "lec_lite_status": lec_status,
                "lec_lite_notes": lec_notes[:500] if lec_notes else "",
                "degraded_quality": bool(stats.get("degraded_quality", False)),
                "graph_file": out_pt.name,
                "graph_quality_metrics": {k: v for k, v in stats.items()
                                          if k.startswith("graph_")},
            })
            meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
            if not args.allow_signature_duplicates:
                seen_rtl_sigs[rtl_sig] = design
                seen_netlist_sigs[net_sig] = design
            rows_by_design[design] = row
            print(f"[rtl-acquire] design={design} stage=finalize status=success "
                  f"cells={stats.get('cells', 0)}", flush=True)
            write_design_status(out_root, design, stage="finalize", state="completed",
                                details={"status": "success", "cells": stats.get("cells", 0),
                                         "graph_format": GRAPH_FORMAT})
            append_design_stage(out_root, design, stage="finalize", state="completed",
                                details={"status": "success", "cells": stats.get("cells", 0)})
            flush_index()
        except Exception as exc:  # noqa: BLE001
            row["status"] = "synth_failed"
            row["notes"] = f"{type(exc).__name__}: {exc}"
            print(f"[rtl-acquire] design={design} stage=exception status=synth_failed "
                  f"error={type(exc).__name__}", flush=True)
            write_design_status(out_root, design, stage="exception", state="failed",
                                details={"status": "synth_failed", "notes": row["notes"][:500]})
            append_design_stage(out_root, design, stage="exception", state="failed",
                                details={"notes": row["notes"][:500]})
            meta.update({"status": "synth_failed", "notes": row["notes"]})
            meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
            rows_by_design[design] = row
            flush_index()
        finally:
            if flow_ran and project is not None:
                ingest_state = ingest_project(project)
                append_design_stage(out_root, design, stage="knowledge_ingest",
                                    state="completed" if ingest_state == "ok" else "degraded",
                                    details={"ingest": ingest_state[:300]})
                if ingest_state not in ("ok", "skipped_by_env"):
                    print(f"[rtl-acquire] design={design} stage=knowledge_ingest "
                          f"WARNING: {ingest_state}", flush=True)
            cleanup_orfs_design_artifacts(design, top)

    print(index_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
