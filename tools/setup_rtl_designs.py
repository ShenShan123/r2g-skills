#!/usr/bin/env python3
"""
Set up design_cases/<name>/ from rtl_designs/<name>/ for batch ORFS flow.

For each design in rtl_designs/:
  1. Read design_meta.json for top module name and platform
  2. Auto-detect clock port from RTL (posedge/negedge analysis + naming heuristics)
  3. Create design_cases/<name>/ with proper directory structure
  4. Copy RTL files
  5. Generate config.mk and constraint.sdc

Usage:
  python3 tools/setup_rtl_designs.py [--designs design1,design2,...] [--force]
  python3 tools/setup_rtl_designs.py --rtl-dir=/path/to/alt_rtl_collection --designs-file=list.txt

Options:
  --rtl-dir=<dir>        Source RTL directory (default: rtl_designs). Absolute
                        path or name relative to the repo root.
  --designs=a,b,c       Comma-separated design names to set up.
  --designs-file=<f>    File with one design name per line (# comments allowed).
  --force               Re-generate config even if the project already exists.
"""

import json
import os
import re
import shutil
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
RTL_DESIGNS_DIR = BASE_DIR / "rtl_designs"
DESIGN_CASES_DIR = BASE_DIR / "design_cases"

TEMPLATE_DIRS = [
    "input", "rtl", "tb", "constraints", "lint", "sim",
    "synth", "backend", "drc", "lvs", "rcx", "reports"
]

# Clock port detection priority (ordered by specificity)
CLOCK_PORT_PATTERNS = [
    # Exact common names
    r'\bclk\b', r'\bclock\b', r'\bCLK\b', r'\bCLOCK\b',
    # Suffixed/prefixed
    r'\bclk_i\b', r'\bi_clk\b', r'\bsys_clk\b', r'\bclk_in\b',
    # AXI
    r'\bS_AXI_ACLK\b', r'\bACLK\b', r'\baclk\b', r'\bs_axi_aclk\b',
    # Wishbone
    r'\bwb_clk_i\b', r'\bi_wb_clk\b', r'\bwbm_clk_i\b', r'\bwbs_clk_i\b',
    # Other
    r'\bclock_c\b', r'\bCK\b', r'\bcp\b', r'\bclk_100\b',
    r'\bclk_ref\b', r'\bcore_clk\b', r'\bmclk\b', r'\bpclk\b',
    r'\bhclk\b', r'\bfclk\b', r'\bsclk\b',
]


def detect_clock_port(rtl_files, top_module):
    """Detect the clock port name from RTL files.

    Strategy:
    1. Find signals used with posedge/negedge in the top module
    2. Among those, pick the one matching common clock naming patterns
    3. If no posedge/negedge found, search module port declarations for clock-like names
    4. If still nothing, return None (combinational design → virtual clock)
    """
    all_content = ""
    for f in rtl_files:
        try:
            all_content += Path(f).read_text(errors='replace') + "\n"
        except Exception:
            continue

    if not all_content.strip():
        return None

    # Strategy 1: Find posedge/negedge signals
    edge_signals = set()
    for m in re.finditer(r'(?:posedge|negedge)\s+(\w+)', all_content):
        sig = m.group(1)
        # Filter out reset-like signals
        if sig.lower() not in ('rst', 'reset', 'rst_n', 'reset_n', 'areset',
                                'areset_n', 'rst_i', 'i_rst', 'rstn', 'resetn',
                                'nreset', 'nrst', 'aresetn', 'async_rst',
                                's_axi_aresetn', 'aresetn_i'):
            edge_signals.add(sig)

    # Strategy 2: Check edge signals against clock naming patterns
    if edge_signals:
        for pattern in CLOCK_PORT_PATTERNS:
            for sig in edge_signals:
                if re.fullmatch(pattern.strip(r'\b'), sig):
                    return sig
        # If edge signals exist but none match patterns, pick the most clock-like one
        # Prefer shorter names that contain 'cl' or 'ck'
        clock_like = [s for s in edge_signals
                      if re.search(r'cl[ok]|ck|CLK|CK', s, re.IGNORECASE)]
        if clock_like:
            return min(clock_like, key=len)
        # Just pick the first edge signal (likely the clock)
        return sorted(edge_signals)[0]

    # Strategy 3: Search port declarations for clock-like names
    # Find top module's port list
    top_pattern = re.compile(
        r'module\s+' + re.escape(top_module) + r'\s*(?:#\s*\([^)]*\)\s*)?\(([^;]*?)\)\s*;',
        re.DOTALL
    )
    top_match = top_pattern.search(all_content)
    if top_match:
        port_text = top_match.group(1)
        # Look for input ports with clock-like names
        for pattern in CLOCK_PORT_PATTERNS:
            m = re.search(r'input\s+(?:wire\s+|reg\s+|logic\s+)?(?:\[.*?\]\s*)?' + pattern, port_text)
            if m:
                # Extract the actual port name
                name_match = re.search(pattern, m.group(0))
                if name_match:
                    return name_match.group(0)

    # Strategy 4: Broader search - any input with clock-like name
    for pattern in CLOCK_PORT_PATTERNS:
        m = re.search(r'input\s+(?:wire\s+|reg\s+|logic\s+)?(?:\[.*?\]\s*)?' + pattern, all_content)
        if m:
            name_match = re.search(pattern, m.group(0))
            if name_match:
                return name_match.group(0)

    return None


def estimate_rtl_complexity(rtl_files):
    """Estimate design complexity from RTL line count (non-comment, non-blank)."""
    total_lines = 0
    for f in rtl_files:
        try:
            for line in Path(f).read_text(errors='replace').splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith('//'):
                    total_lines += 1
        except Exception:
            continue
    return total_lines


PORT_DECL_RE = re.compile(
    r'\b(input|output|inout)\b(?:\s+(?:wire|reg|logic))?'
    r'(?:\s*\[[^\]]+\])?\s*([A-Za-z_][\w,\s]*?)\s*[,;)]',
    re.MULTILINE,
)


def scan_port_pin_count(rtl_files, top_module):
    """Rough estimate of total scalar IO pins on the top module.

    Parses the module ... endmodule block for the top, counts each port name
    plus its bit-vector width. Over-estimates are OK; the goal is to bump
    tiny floorplans up when the pin count is clearly too high for a 50x50 die.
    """
    module_re = re.compile(
        r'module\s+' + re.escape(top_module) + r'\b.*?endmodule',
        re.DOTALL,
    )
    total = 0
    for f in rtl_files:
        try:
            txt = Path(f).read_text(errors='replace')
        except Exception:
            continue
        m = module_re.search(txt)
        if not m:
            continue
        body = m.group(0)
        for _, names in re.findall(
            r'\b(input|output|inout)\b(?:\s+(?:wire|reg|logic))?'
            r'(\s*\[[^\]]+\])?\s*([A-Za-z_][A-Za-z0-9_,\s]*?)\s*[;,)]',
            body,
        ) if False else []:  # placeholder; real parse below
            pass
        for match in re.finditer(
            r'\b(input|output|inout)\b(?:\s+(?:wire|reg|logic))?'
            r'\s*(\[[^\]]+\])?\s*([A-Za-z_][A-Za-z0-9_,\s]*?)\s*[;,)]',
            body,
        ):
            width_spec = match.group(2) or ''
            names = match.group(3)
            names = [n.strip() for n in names.split(',') if n.strip()]
            if not names:
                continue
            bits = 1
            wm = re.match(r'\[\s*(\d+)\s*:\s*(\d+)\s*\]', width_spec)
            if wm:
                bits = abs(int(wm.group(1)) - int(wm.group(2))) + 1
            total += bits * len(names)
        break  # top module found
    return total


def scan_memory_bits(rtl_files):
    """Detect the largest inferred memory (reg [W:0] mem [D:0]) across RTL."""
    largest = 0
    mem_re = re.compile(
        r'reg(?:\s+(?:wire|signed))?'
        r'\s*\[\s*(\d+)\s*:\s*(\d+)\s*\]'
        r'\s*[A-Za-z_]\w*'
        r'\s*\[\s*(\d+)\s*:\s*(\d+)\s*\]'
    )
    for f in rtl_files:
        try:
            txt = Path(f).read_text(errors='replace')
        except Exception:
            continue
        for m in mem_re.finditer(txt):
            w = abs(int(m.group(1)) - int(m.group(2))) + 1
            d = abs(int(m.group(3)) - int(m.group(4))) + 1
            bits = w * d
            if bits > largest:
                largest = bits
    return largest


def validate_top_module(rtl_files, top_module):
    """Check if top_module is likely the correct top for multi-module RTL files.

    Returns (validated_top, clock_hint) where validated_top may differ from
    top_module if a better candidate is found. clock_hint is non-None when the
    validated top uses a non-standard clock (e.g., ap_clk for HLS designs).

    Heuristic: if the file has many modules and the selected top is a small
    leaf (few ports, few lines), pick the module that is last in the file,
    has the most ports, or matches the filename stem.
    """
    module_re = re.compile(r'^module\s+(\w+)', re.MULTILINE)
    all_modules = []

    for f in rtl_files:
        try:
            txt = Path(f).read_text(errors='replace')
        except Exception:
            continue
        file_stem = Path(f).stem
        for m in module_re.finditer(txt):
            name = m.group(1)
            start = m.start()
            end_m = re.search(r'\bendmodule\b', txt[start:])
            length = end_m.start() if end_m else 0
            port_m = re.search(r'\(([^)]*)\)', txt[start:start + min(2000, len(txt) - start)])
            port_count = len(port_m.group(1).split(',')) if port_m else 0
            all_modules.append({
                'name': name,
                'length': length,
                'ports': port_count,
                'file': f,
                'file_stem': file_stem,
                'offset': start,
            })

    if len(all_modules) <= 1:
        return top_module, None

    selected = next((m for m in all_modules if m['name'] == top_module), None)
    if not selected:
        return top_module, None

    largest = max(all_modules, key=lambda m: m['length'])
    most_ports = max(all_modules, key=lambda m: m['ports'])
    last_module = max(all_modules, key=lambda m: m['offset'])
    stem_match = next((m for m in all_modules if m['name'] == m['file_stem']), None)

    if len(all_modules) >= 5 and selected['length'] < largest['length'] * 0.1:
        candidates = [stem_match, most_ports, last_module, largest]
        for c in candidates:
            if c and c['name'] != top_module and c['length'] > selected['length'] * 3:
                clock_hint = None
                if c['name'] == 'myproject':
                    clock_hint = 'ap_clk'
                return c['name'], clock_hint

    return top_module, None


def scan_unresolved_includes(rtl_files):
    """Return set of `include targets not present in the RTL directories."""
    referenced = set()
    present = set()
    for f in rtl_files:
        try:
            txt = Path(f).read_text(errors='replace')
        except Exception:
            continue
        for m in re.finditer(r'`include\s+"([^"]+)"', txt):
            referenced.add(m.group(1))
        present.add(Path(f).name)
    return referenced - present


_HEADER_INDEX = None

# Marker left in agent-authored design-specific empty stubs. Such a stub is
# verified safe for *one* design only ("can_btl uses no macros") and must NOT
# be propagated to siblings -- they may genuinely depend on the real header.
_STUB_MARKER = "minimal stub for ORFS"


def _index_header_dir(idx, root, rglob_root):
    """Add every header-ish file under rglob_root to idx, keyed by basename.

    Each entry is (path, design_root) so the harvester can recover the owning
    design name regardless of which pool the file came from.
    """
    for ext in ("*.v", "*.vh", "*.svh", "*.h", "*.inc"):
        for p in rglob_root.rglob(ext):
            try:
                if _STUB_MARKER in p.read_text(errors="replace")[:400]:
                    continue  # design-specific empty stub -- not family-shareable
            except Exception:
                pass
            idx.setdefault(p.name, []).append((p, root))


def _build_header_index():
    """Index every header-ish file by basename from two pools.

    Pool 1 -- RTL_DESIGNS_DIR: the v2 packer drops shared `include headers from
    sub-module extractions, but a sibling bundle from the same source repo
    often still ships them.

    Pool 2 -- design_cases/*/rtl: a header that was reconstructed or recovered
    on one family member (and proved correct because that sibling ran) is the
    best possible source for the rest of the family. This makes header recovery
    compounding -- fix one RISC_V module, the other four inherit the header.
    """
    idx = {}
    _index_header_dir(idx, RTL_DESIGNS_DIR, RTL_DESIGNS_DIR)
    if DESIGN_CASES_DIR.is_dir():
        for case in DESIGN_CASES_DIR.iterdir():
            rtl = case / "rtl"
            if rtl.is_dir():
                _index_header_dir(idx, DESIGN_CASES_DIR, rtl)
    return idx


def _common_prefix_len(a, b):
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def find_header_in_siblings(basename, current_design, min_prefix=4):
    """Harvest a missing `include header from a sibling design.

    Returns (path, confidence) or (None, None). confidence is "exact" when two
    or more independent bundles ship a byte-identical copy (unambiguous), else
    "family" when the chosen file comes from a same-repo-family design (shared
    name prefix). A single candidate is only accepted when it is a genuine
    family sibling -- a lone unrelated copy is rejected to avoid a wrong header.
    """
    global _HEADER_INDEX
    if _HEADER_INDEX is None:
        _HEADER_INDEX = _build_header_index()
    cands = _HEADER_INDEX.get(basename, [])
    if not cands:
        return None, None

    def design_of(p, root):
        try:
            return p.relative_to(root).parts[0]
        except Exception:
            return ""

    # Two-plus independent bundles agreeing byte-for-byte -> unambiguous.
    contents = set()
    for p, _ in cands:
        try:
            contents.add(p.read_bytes())
        except Exception:
            contents.add(None)
    if len(cands) >= 2 and len(contents) == 1:
        return cands[0][0], "exact"

    # Otherwise require a same-family design (shared design-name prefix). This
    # also gates the single-candidate case: a reconstructed RISC_V header only
    # flows to RISC_V_* siblings, never to an unrelated design.
    best = min(cands, key=lambda c: (-_common_prefix_len(design_of(*c), current_design),
                                     len(str(c[0]))))
    if _common_prefix_len(design_of(*best), current_design) >= min_prefix:
        return best[0], "family"
    return None, None


def generate_config_mk(project_dir, design_name, platform, rtl_files, sdc_path,
                       place_density=0.20, top_module=None):
    """Generate config.mk for ORFS.

    Sizing policy (CORE_UTILIZATION everywhere so ORFS auto-sizes a die that FITS
    the synthesized cells -- line count is only a coarse util tier, never a fixed
    die; see the FLW-0024 note at the bucket below):
    - Tiny (< 100 lines): CORE_UTILIZATION=20
    - Small (100-500 lines): CORE_UTILIZATION=25
    - Medium (500-5000 lines): CORE_UTILIZATION=25
    - Large (> 5000 lines): CORE_UTILIZATION=20
    - Many IO pins / wide buses: CORE_UTILIZATION=15-20 (pin_forces_util)

    Also injects SYNTH_MEMORY_MAX_BITS when RTL infers memories >4 Kbit so
    Yosys doesn't reject with "Synthesized memory size exceeds ...".
    """
    verilog_files = " \\\n    ".join(str(f) for f in rtl_files)
    complexity = estimate_rtl_complexity(rtl_files)

    pin_count = scan_port_pin_count(rtl_files, top_module or design_name)
    max_mem_bits = scan_memory_bits(rtl_files)
    unresolved = scan_unresolved_includes(rtl_files)

    # Per-perimeter rule: nangate45 IO pins fit roughly 1 per micron. A 50x50
    # die has ~200 micron perimeter → ~200 pins max; 120x120 → ~480. Bump any
    # design with significantly more pins up to CORE_UTILIZATION.
    pin_forces_util = pin_count > 180

    # 2026-06-23: do NOT pin a fixed DIE_AREA for tiny/small designs. RTL line count
    # is a terrible proxy for gate count -- a <100-line design (wide multiplier, FFT
    # butterfly, DMA datapath) can synthesize to thousands of cells that do not fit a
    # hardcoded 50x50/120x120 die -> [ERROR FLW-0024] place density > 1.0 at global
    # placement. Use CORE_UTILIZATION (like medium/large already do) so ORFS
    # auto-sizes a die that FITS the synthesized cells. The pin-perimeter case is
    # still handled by pin_forces_util above; a looser util on the smallest designs
    # keeps the die generous enough for boundary pins. (Same class as the sky130
    # mk_*_project sizing bug; validated on dma_controller: 50x50 die held 6442um^2
    # of cells -> FLW-0024; CORE_UTILIZATION=30 auto-sized to 31% util -> placed.)
    if complexity < 100 and not pin_forces_util:
        sizing = "export CORE_UTILIZATION = 20"
        size_cat = "tiny"
    elif complexity < 500 and not pin_forces_util:
        sizing = "export CORE_UTILIZATION = 25"
        size_cat = "small"
    elif complexity < 5000:
        sizing = "export CORE_UTILIZATION = 25"
        size_cat = "medium"
    else:
        sizing = "export CORE_UTILIZATION = 20"
        size_cat = "large"

    if pin_forces_util:
        # High IO count → let ORFS pick die area. Low util keeps perimeter large.
        util = 15 if pin_count > 500 else 20
        sizing = f"export CORE_UTILIZATION = {util}"
        size_cat = f"pin_heavy_{pin_count}"

    # Memory policy: default ORFS SYNTH_MEMORY_MAX_BITS=4096 is too tight
    # for register files / FIFOs. Lift to 128 Kbit whenever RTL infers >1 Kbit.
    mem_line = ""
    if max_mem_bits > 1024:
        mem_line = "\nexport SYNTH_MEMORY_MAX_BITS = 131072"

    # Include-dir policy: if any `include target isn't a sibling .v file, expose
    # the rtl dir as a search path so ORFS can find headers sitting alongside.
    include_line = ""
    if unresolved:
        include_line = f"\nexport VERILOG_INCLUDE_DIRS = {project_dir / 'rtl'}"

    # Port-feedthrough policy (sky130 Netgen LVS only, 2026-07-01 parity fix): ORFS
    # global_place runs `remove_buffers`, merging `assign out = in` port-feedthrough nets
    # onto ONE net. SPICE cannot express two top-level ports on one node, so Magic keeps
    # only one name and Netgen reports "Top level cell failed pin matching"
    # (mismatch_class=top_pin_mismatch) even though every device/net matches uniquely --
    # picorv32_mem_adapter / sirv_gnrl_icb_arbt this round. buffer_port_feedthroughs.tcl
    # (POST_GLOBAL_PLACE_TCL hook, no-op for feedthrough-free designs) splits them.
    # mk_sky130_project.py already wires this; the setup_rtl_designs.py re-point path did
    # NOT -> a whole re-pointed sky130 round's feedthrough designs top_pin_mismatch with no
    # hook to prevent it (same class as the known PDN-floor parity gap). KLayout-LVS
    # platforms (nangate45/asap7/gf180/ihp) don't need it. See references/failure-patterns.md
    # "sky130 Netgen LVS: top-level pin-matching residuals".
    fdbuf_line = ""
    if platform in ("sky130hd", "sky130hs"):
        _fdbuf_hook = (Path(__file__).resolve().parent.parent
                       / "r2g-skills/signoff-loop" / "scripts" / "flow" / "orfs_hooks"
                       / "buffer_port_feedthroughs.tcl")
        fdbuf_line = f"\nexport POST_GLOBAL_PLACE_TCL = {_fdbuf_hook}"

    content = f"""export DESIGN_NAME = {design_name}
export PLATFORM    = {platform}

export VERILOG_FILES = {verilog_files}
export SDC_FILE      = {sdc_path}

{sizing}
export PLACE_DENSITY_LB_ADDON = {place_density}

export ABC_AREA = 1{mem_line}{include_line}{fdbuf_line}
"""
    config_path = project_dir / "constraints" / "config.mk"
    config_path.write_text(content, encoding="utf-8")
    return config_path, complexity, size_cat


def generate_sdc(project_dir, design_name, clock_port, clock_period=10.0):
    """Generate constraint.sdc.

    If clock_port is None (combinational design), use a virtual clock.
    """
    sdc_path = project_dir / "constraints" / "constraint.sdc"

    if clock_port:
        content = f"""current_design {design_name}

set clk_name  core_clock
set clk_port_name {clock_port}
set clk_period {clock_period}
set clk_io_pct 0.2

set clk_port [get_ports $clk_port_name]
create_clock -name $clk_name -period $clk_period $clk_port

set non_clock_inputs [all_inputs -no_clocks]
set_input_delay  [expr $clk_period * $clk_io_pct] -clock $clk_name $non_clock_inputs
set_output_delay [expr $clk_period * $clk_io_pct] -clock $clk_name [all_outputs]
"""
    else:
        # Combinational design: use a virtual clock for timing constraints
        content = f"""current_design {design_name}

set clk_name  virtual_clock
set clk_period {clock_period}
set clk_io_pct 0.2

create_clock -name $clk_name -period $clk_period

set_input_delay  [expr $clk_period * $clk_io_pct] -clock $clk_name [all_inputs]
set_output_delay [expr $clk_period * $clk_io_pct] -clock $clk_name [all_outputs]
"""

    sdc_path.write_text(content, encoding="utf-8")
    return sdc_path


def _is_vhdl_only(src_dir):
    """True if the design ships VHDL with no synthesizable Verilog top.

    This toolchain has no GHDL/VHDL Yosys frontend (Yosys reads Verilog/SV only),
    so a VHDL source tree is *unsupported*, not merely unconfigured. Two signals:
    (1) a legacy config.tcl declaring FILE_FORMAT "vhdl"; (2) the rtl/ dir is
    dominated by .vhd files. Counting files (not just "any .v present") matters
    because a VHDL SoC often ships a few Verilog leaf peripherals (e.g. an
    OpenCores Ethernet MAC) that are NOT a Verilog version of the design.
    See references/failure-patterns.md "VHDL-only design (no Verilog frontend)".
    """
    cfg = src_dir / "config.tcl"
    if cfg.exists():
        try:
            txt = cfg.read_text(errors="ignore").lower()
            if "file_format" in txt and "vhdl" in txt:
                return True
        except OSError:
            pass
    search = src_dir / "rtl" if (src_dir / "rtl").exists() else src_dir
    vhd = len(list(search.rglob("*.vhd"))) + len(list(search.rglob("*.vhdl")))
    ver = len(list(search.rglob("*.v"))) + len(list(search.rglob("*.sv")))
    return vhd > 0 and vhd > ver


def setup_one_design(design_name, force=False, platform_override=None):
    """Set up a single design from rtl_designs/ into design_cases/.

    ``platform_override`` (e.g. "asap7") forces the target PDK for THIS run,
    ignoring the per-design ``design_meta.json`` platform. This is how a whole-
    corpus technology re-target (a "new round" on a different node) is driven:
    every project's config.mk is regenerated with ``export PLATFORM = <override>``.
    Sizing is CORE_UTILIZATION-based (ORFS auto-sizes the die), so it is platform-
    agnostic and safe to re-point. Re-pointing ONLY the campaign ledger would be a
    lie: run_orfs.sh builds against config.mk's PLATFORM, not the ledger field.
    """
    src_dir = RTL_DESIGNS_DIR / design_name
    meta_path = src_dir / "design_meta.json"

    # VHDL designs are unsynthesizable on this Verilog/SV-only toolchain. Report
    # the real root cause instead of the generic "no design_meta.json" skip.
    if _is_vhdl_only(src_dir):
        return {"design": design_name, "status": "skip",
                "reason": "VHDL design — unsupported (no GHDL/Verilog frontend)"}

    if not meta_path.exists():
        return {"design": design_name, "status": "skip", "reason": "no design_meta.json"}

    with open(meta_path) as f:
        meta = json.load(f)

    top_module = meta.get("top", design_name)
    platform = platform_override or meta.get("platform", "nangate45")

    project_dir = DESIGN_CASES_DIR / design_name

    # Skip if already set up (unless --force)
    if (project_dir / "constraints" / "config.mk").exists() and not force:
        return {"design": design_name, "status": "skip", "reason": "already exists"}

    # Create directory structure
    project_dir.mkdir(parents=True, exist_ok=True)
    for d in TEMPLATE_DIRS:
        (project_dir / d).mkdir(parents=True, exist_ok=True)

    # Copy RTL files
    src_rtl_dir = src_dir / "rtl"
    dst_rtl_dir = project_dir / "rtl"
    rtl_files = []

    if src_rtl_dir.exists():
        for vf in sorted(src_rtl_dir.glob("*.v")):
            dst = dst_rtl_dir / vf.name
            shutil.copy2(vf, dst)
            rtl_files.append(dst.resolve())
        # Also copy .sv files if present
        for vf in sorted(src_rtl_dir.glob("*.sv")):
            dst = dst_rtl_dir / vf.name
            shutil.copy2(vf, dst)
            rtl_files.append(dst.resolve())

        # Copy header/include files. These are NOT added to VERILOG_FILES
        # (they must not be compiled directly); they are resolved via
        # VERILOG_INCLUDE_DIRS. Copying them keeps `include directives valid.
        for ext in ("*.vh", "*.svh", "*.h", "*.inc"):
            for hf in sorted(src_rtl_dir.glob(ext)):
                shutil.copy2(hf, dst_rtl_dir / hf.name)

        # Recursive fallback: some v2 bundles keep RTL in nested subdirs
        # (rtl/_downloads/..., rtl/rtl/..., rtl/lib/...) instead of flat.
        # Collect everything, then de-duplicate by module name so a
        # _tmp_cfg "sanitized" copy wins over the original _downloads copy.
        if not rtl_files:
            nested = sorted(src_rtl_dir.rglob("*.v")) + sorted(src_rtl_dir.rglob("*.sv"))
            module_re = re.compile(r'^\s*module\s+(\w+)', re.MULTILINE)
            chosen = {}  # module_name -> source Path
            extra = []   # files with no detectable module (headers, etc.)
            for vf in nested:
                try:
                    txt = vf.read_text(errors='replace')
                except Exception:
                    continue
                mods = module_re.findall(txt)
                if not mods:
                    extra.append(vf)
                    continue
                prefer = "_tmp_cfg" in str(vf)
                for mod in mods:
                    if mod not in chosen or (prefer and "_tmp_cfg" not in str(chosen[mod])):
                        chosen[mod] = vf
            picked = sorted(set(chosen.values()) | set(extra))
            used_names = set()
            for vf in picked:
                # Flatten into dst rtl/, disambiguating basename collisions.
                name = vf.name
                if name in used_names:
                    name = f"{vf.parent.name}__{vf.name}"
                used_names.add(name)
                dst = dst_rtl_dir / name
                shutil.copy2(vf, dst)
                rtl_files.append(dst.resolve())

    if not rtl_files:
        return {"design": design_name, "status": "error", "reason": "no RTL files found"}

    # Header resolution: v2 bundles frequently omit `include-d headers.
    # Generate a safe stub for timescale headers (a pure `timescale directive)
    # and for *undefines* headers (a list of harmless `undef directives) so
    # synthesis is not blocked at yosys-canonicalize. Genuine content headers
    # (*_defines.v, *_header.vh, config.vh, ...) carry real `define / parameter
    # values and CANNOT be stubbed -- those designs are recorded as incomplete.
    present = {p.name for p in dst_rtl_dir.iterdir()}
    referenced = set()
    for rf in rtl_files:
        try:
            txt = Path(rf).read_text(errors='replace')
        except Exception:
            continue
        referenced |= set(re.findall(r'`include\s+"([^"]+)"', txt))
    missing_headers = []
    harvested_headers = []
    for inc in sorted(referenced):
        base = os.path.basename(inc)
        if base in present or inc in present:
            continue
        low = base.lower()
        if low.startswith("timescale"):
            (dst_rtl_dir / base).write_text("`timescale 1ns / 1ps\n", encoding="utf-8")
            present.add(base)
        elif "undefine" in low:
            (dst_rtl_dir / base).write_text(
                "// auto-stub: original undefines header absent from bundle\n",
                encoding="utf-8")
            present.add(base)
        else:
            # Content header: try to harvest it from a sibling design bundle
            # before giving up (the v2 packer drops shared headers).
            sib, conf = find_header_in_siblings(base, design_name)
            if sib is not None:
                shutil.copy2(sib, dst_rtl_dir / base)
                present.add(base)
                src_name = base
                for root in (RTL_DESIGNS_DIR, DESIGN_CASES_DIR):
                    try:
                        src_name = sib.relative_to(root).parts[0]
                        break
                    except Exception:
                        continue
                harvested_headers.append(f"{base} <- {src_name} ({conf})")
            else:
                missing_headers.append(inc)

    # Validate top module for multi-module files (HLS, VTR benchmarks)
    validated_top, clock_hint = validate_top_module(rtl_files, top_module)
    if validated_top != top_module:
        print(f"  WARNING: {design_name}: top module changed from '{top_module}' to '{validated_top}' (auto-detected)")
        top_module = validated_top

    # Detect clock port
    clock_port = clock_hint or detect_clock_port(rtl_files, top_module)

    # Generate SDC
    sdc_path = generate_sdc(project_dir, top_module, clock_port)

    # Generate config.mk
    _, complexity, size_cat = generate_config_mk(
        project_dir, top_module, platform, rtl_files,
        str(sdc_path.resolve()),
        place_density=0.20,
        top_module=top_module,
    )

    # Write metadata
    setup_meta = {
        "design_name": design_name,
        "top_module": top_module,
        "platform": platform,
        "clock_port": clock_port,
        "clock_type": "real" if clock_port else "virtual",
        "rtl_file_count": len(rtl_files),
        "rtl_complexity": complexity,
        "size_category": size_cat,
        # Non-stubbable `include headers absent from the bundle. A non-empty
        # list means synthesis WILL fail at yosys-canonicalize -- the design
        # is incomplete and should be skipped, not retried.
        "missing_headers": missing_headers,
        # Headers recovered from a sibling design bundle ("<- <src> (exact|family)").
        "harvested_headers": harvested_headers,
        "status": "incomplete_missing_headers" if missing_headers else "setup_complete",
        "source": str(src_dir)
    }
    (project_dir / "metadata.json").write_text(
        json.dumps(setup_meta, indent=2), encoding="utf-8"
    )

    return {
        "design": design_name,
        "status": "ok",
        "top": top_module,
        "clock_port": clock_port or "virtual",
        "rtl_files": len(rtl_files),
        "size_cat": size_cat,
        "complexity": complexity
    }


# Value-taking CLI flags accept BOTH `--flag value` (space) and `--flag=value` forms. The
# hand-rolled parser below historically understood only the `=` form, but the documented
# invocations (SKILL Step 1b, build_pending_ledger.py's header, /r2g-debug) use the SPACE
# form -- so a bare `--platform asap7` fell through to the positional-design branch, left
# platform_override=None, and the whole-corpus PDK re-target became a SILENT no-op
# (config.mk stayed on the old platform; exit 0). See references/failure-patterns.md
# "Platform re-target CLI mismatch (silent no-op)".
_VALUE_FLAGS = ("--designs", "--designs-file", "--platform", "--rtl-dir")


def _normalize_value_flags(argv, value_flags=_VALUE_FLAGS):
    """Rewrite `--flag value` to `--flag=value` for the given value-taking flags so the
    parser accepts both the space-separated and `=` forms. A trailing flag with no
    following value is left unchanged (the parser then ignores it, exactly as before)."""
    out, i = [], 0
    while i < len(argv):
        a = argv[i]
        if a in value_flags and i + 1 < len(argv):
            out.append(f"{a}={argv[i + 1]}")
            i += 2
        else:
            out.append(a)
            i += 1
    return out


def parse_setup_args(argv):
    """Parse setup_rtl_designs CLI args (both `--flag value` and `--flag=value` forms).

    Returns ``(force, selected, platform_override)``. Mutates the module global
    RTL_DESIGNS_DIR for ``--rtl-dir`` (preserving historical behavior). Split out of
    main() so the space/equals parsing is unit-testable without filesystem side effects.
    """
    global RTL_DESIGNS_DIR
    args = _normalize_value_flags(argv)
    force = "--force" in args
    selected = None
    designs_file = None
    platform_override = None

    for arg in args:
        if arg.startswith("--designs="):
            selected = arg.split("=", 1)[1].split(",")
        elif arg.startswith("--designs-file="):
            designs_file = arg.split("=", 1)[1]
        elif arg.startswith("--platform="):
            # Force this PDK for the whole corpus (a technology re-target / "new
            # round"), overriding each design_meta.json platform. config.mk gets
            # `export PLATFORM = <this>`. Pair with --force to regenerate.
            platform_override = arg.split("=", 1)[1].strip()
        elif arg.startswith("--rtl-dir="):
            # Source RTL directory override (default is the unified rtl_designs/).
            # Accepts an absolute path or a name relative to the repo root.
            rd = arg.split("=", 1)[1]
            rd_path = Path(rd)
            RTL_DESIGNS_DIR = rd_path if rd_path.is_absolute() else BASE_DIR / rd
        elif arg == "--force":
            pass
        elif not arg.startswith("--"):
            selected = arg.split(",")

    # A designs-file is one design name per line (blank lines / # comments ok).
    if designs_file:
        names = []
        for line in Path(designs_file).read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                names.append(line)
        selected = names

    return force, selected, platform_override


def main():
    force, selected, platform_override = parse_setup_args(sys.argv[1:])

    DESIGN_CASES_DIR.mkdir(parents=True, exist_ok=True)

    # Enumerate designs
    if selected:
        designs = selected
    else:
        designs = sorted([
            d.name for d in RTL_DESIGNS_DIR.iterdir()
            if d.is_dir() and (d / "design_meta.json").exists()
        ])

    plat_note = f", platform={platform_override} (forced)" if platform_override else ""
    print(f"Setting up {len(designs)} designs (force={force}{plat_note})...")

    results = {"ok": 0, "skip": 0, "error": 0}
    errors = []
    clock_stats = {"real": 0, "virtual": 0}
    # size_cat can be tiny/small/medium/large or a dynamic "pin_heavy_<N>"
    # label, so count into a defaultdict instead of a fixed-key dict.
    from collections import defaultdict
    size_stats = defaultdict(int)

    for i, name in enumerate(designs):
        r = setup_one_design(name, force=force, platform_override=platform_override)
        results[r["status"]] += 1

        if r["status"] == "ok":
            clk_type = "virtual" if r["clock_port"] == "virtual" else "real"
            clock_stats[clk_type] += 1
            # Collapse pin_heavy_<N> into a single bucket for the summary.
            cat = r["size_cat"]
            size_stats["pin_heavy" if cat.startswith("pin_heavy") else cat] += 1
            if (i + 1) % 50 == 0 or i == 0:
                print(f"  [{i+1}/{len(designs)}] {name}: top={r['top']}, "
                      f"clock={r['clock_port']}, size={r['size_cat']}({r['complexity']})")
        elif r["status"] == "error":
            errors.append(r)
            print(f"  [{i+1}/{len(designs)}] ERROR {name}: {r['reason']}")

    print(f"\nDone: {results['ok']} set up, {results['skip']} skipped, "
          f"{results['error']} errors")
    print(f"Clock types: {clock_stats['real']} real, {clock_stats['virtual']} virtual")
    print(f"Size categories: tiny={size_stats['tiny']}, small={size_stats['small']}, "
          f"medium={size_stats['medium']}, large={size_stats['large']}")

    if errors:
        print(f"\nErrors ({len(errors)}):")
        for e in errors:
            print(f"  {e['design']}: {e['reason']}")

    # Write summary
    summary_path = DESIGN_CASES_DIR / "_setup_summary.json"
    summary_path.write_text(json.dumps({
        "total": len(designs),
        "results": results,
        "clock_stats": clock_stats,
        "size_stats": size_stats,
        "errors": errors
    }, indent=2), encoding="utf-8")
    print(f"\nSummary written to {summary_path}")


if __name__ == "__main__":
    main()
