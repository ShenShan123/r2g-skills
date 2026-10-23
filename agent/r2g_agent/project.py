"""Turn a folder of RTL into an r2g/ORFS project the skills can run.

Layout produced (the one signoff-loop's scripts expect):

  <project>/rtl/*.v             copies of the user's sources
  <project>/constraints/config.mk
  <project>/constraints/constraint.sdc
  <project>/metadata.json

Two passes. `create()` writes a synthesis-only config (no die yet). After
synthesis `apply_sizing()` reads the cell count and area yosys reports and
writes the explicit die and the binned clock period -- a tiny design sized by
CORE_UTILIZATION alone gets a core too narrow for the power grid (PDN-0185).
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from . import platforms

HDL_SUFFIXES = (".v", ".sv", ".vh", ".svh")

# Candidate clock port names, tried in order before a name-pattern fallback.
# Same list and SDC as the corpus driver the knowledge base was built on.
CLOCK_PORTS = ("clk clock i_clk clk_i core_clk CLK CLOCK HCLK clock_i sys_clk "
               "clk_in clkin clk_sys aclk ACLK wb_clk_i clock_in pclk PCLK "
               "clock0 clk0 clkA clk_a ref_clk refclk mclk MCLK fclk sclk")

SDC_TEMPLATE = """current_design {top}
set cand [get_ports -quiet {{{ports}}}]
if {{[llength $cand] == 0}} {{
  foreach p [all_inputs] {{
    if {{[regexp -nocase {{(^|_)(clk|clock)([0-9_]*$|_)}} [get_name $p]]}} {{
      lappend cand $p
    }}
  }}
}}
set _per {period}
if {{[llength $cand] > 0}} {{
  create_clock -name core_clk -period $_per [lindex $cand 0]
}} else {{
  create_clock -name virtual_clk -period $_per
}}
# setup uncertainty 5 % of the period; hold a fixed 20 ps (skew does not scale
# with the period)
set_clock_uncertainty -setup [expr {{$_per * 0.05}}] [get_clocks *]
set_clock_uncertainty -hold 0.020 [get_clocks *]
# I/O delay 15 % of the period so port paths are constrained too
set _io [expr {{$_per * 0.15}}]
set _clks [get_clocks *]
set _in [all_inputs]
foreach _c $cand {{
  set _in [lsearch -all -inline -not -exact $_in $_c]
}}
if {{[llength $_in] > 0}} {{
  set_input_delay  $_io -clock [lindex $_clks 0] $_in
}}
if {{[llength [all_outputs]] > 0}} {{
  set_output_delay $_io -clock [lindex $_clks 0] [all_outputs]
}}
"""

_MODULE_RE = re.compile(r"^\s*module\s+([A-Za-z_][\w$]*)", re.M)
_INST_RE = re.compile(r"^\s*([A-Za-z_][\w$]*)\s+(?:#\s*\(.*?\)\s*)?([A-Za-z_][\w$]*)\s*\(", re.M | re.S)
_KEYWORDS = {"module", "input", "output", "inout", "wire", "reg", "logic", "assign", "always",
             "if", "else", "case", "for", "function", "task", "begin", "end", "initial",
             "generate", "localparam", "parameter", "integer", "genvar", "return"}


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def find_hdl(src: Path) -> list[Path]:
    if src.is_file():
        return [src]
    return sorted(p for p in src.rglob("*") if p.suffix.lower() in HDL_SUFFIXES and p.is_file())


def _strip_param_overrides(text: str) -> str:
    """Remove every `#( ... )` group, parentheses balanced.

    A non-greedy regex stops at the first `)`, so `sub #(.A(A), .B(B)) u0 (`
    did not read as an instantiation and `sub` looked like a top module -- the
    wrong top was then synthesised on its own and passed sign-off."""
    out, i, n = [], 0, len(text)
    while i < n:
        if text[i] == "#":
            j = i + 1
            while j < n and text[j].isspace():
                j += 1
            if j < n and text[j] == "(":
                depth, k = 0, j
                while k < n:
                    if text[k] == "(":
                        depth += 1
                    elif text[k] == ")":
                        depth -= 1
                        if depth == 0:
                            break
                    k += 1
                out.append(" ")
                i = k + 1
                continue
        out.append(text[i])
        i += 1
    return "".join(out)


def infer_top(files: list[Path], hint: str | None = None) -> tuple[str, str]:
    """(top, how). The module no other module instantiates.

    If several are uninstantiated (e.g. an unused helper module in a library
    file) and exactly one is named like `hint` (the folder), take it and say
    so. Otherwise ambiguity is an error, never a guess."""
    defined, used = [], set()
    for f in files:
        if f.suffix.lower() in (".vh", ".svh"):
            continue
        t = _strip_param_overrides(_strip_comments(f.read_text(errors="replace")))
        defined += _MODULE_RE.findall(t)
        for body in re.split(r"\bmodule\b", t)[1:]:
            for typ, _inst in _INST_RE.findall(body.split("endmodule")[0]):
                if typ not in _KEYWORDS:
                    used.add(typ)
    tops = [m for m in dict.fromkeys(defined) if m not in used]
    if len(tops) == 1:
        return tops[0], "自动识别（唯一未被例化的模块）"
    if hint:
        named = [m for m in tops if m.lower() == hint.lower()]
        if len(named) == 1:
            return named[0], "按文件夹名在候选 %s 中选定" % ", ".join(tops)
    raise ValueError("cannot infer the top module (candidates: %s); give it explicitly"
                     % (", ".join(tops) or "none"))


def create(src: Path, project: Path, platform: str, top: str | None = None,
           name: str | None = None) -> dict:
    """Copy the RTL in and write the synthesis-stage config. Returns metadata."""
    platforms.get(platform)
    files = find_hdl(src)
    if not files:
        raise ValueError("no Verilog/SystemVerilog files under %s" % src)
    meta_file = src / "design.json" if src.is_dir() else None
    how = "用户指定" if top else ""
    if top is None and meta_file and meta_file.is_file():
        top = json.loads(meta_file.read_text()).get("top")
        how = "design.json 指定" if top else ""
    if not top:
        top, how = infer_top(files, hint=(src.name if src.is_dir() else src.stem))
    rtl = project / "rtl"
    if project.exists():
        shutil.rmtree(project)
    (project / "constraints").mkdir(parents=True)
    rtl.mkdir()
    copied = []
    base = src if src.is_dir() else src.parent
    for f in files:
        dst = rtl / f.relative_to(base)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dst)
        copied.append(dst)
    sources = [p for p in copied if p.suffix.lower() in (".v", ".sv")]
    inc_dirs = sorted({str(p.parent) for p in copied})
    meta = {
        "design_name": name or top,
        "top": top,
        "top_source": how,
        "platform": platform,
        "source": str(src),
        "files": [str(p) for p in sources],
        "include_dirs": inc_dirs,
        "status": "created",
    }
    _write_config(project, meta, sizing=None)
    (project / "metadata.json").write_text(json.dumps(meta, indent=2))
    return meta


def _write_config(project: Path, meta: dict, sizing: dict | None) -> None:
    p = platforms.get(meta["platform"])
    lines = [
        "export DESIGN_NAME = %s" % meta["top"],
        "export PLATFORM    = %s" % meta["platform"],
        "export VERILOG_FILES = %s" % " ".join(meta["files"]),
        "export VERILOG_INCLUDE_DIRS = %s" % " ".join(meta["include_dirs"]),
        "export SDC_FILE = %s" % (project / "constraints" / "constraint.sdc"),
        "export PLACE_DENSITY_LB_ADDON = 0.20",
        "export ABC_AREA = 1",
        "export SYNTH_MEMORY_MAX_BITS = 131072",
    ]
    if sizing:
        d, i = sizing["die_side_um"], sizing["core_inset_um"]
        lines += ["export DIE_AREA  = 0 0 %g %g" % (d, d),
                  "export CORE_AREA = %g %g %g %g" % (i, i, d - i, d - i)]
        period = sizing["period_ns"]
    else:
        lines.append("export CORE_UTILIZATION = %d" % round(p.utilization * 100))
        period = p.period_bins[-1][1]
    (project / "constraints" / "config.mk").write_text("\n".join(lines) + "\n")
    (project / "constraints" / "constraint.sdc").write_text(
        SDC_TEMPLATE.format(top=meta["top"], ports=CLOCK_PORTS, period=period))


_NUM = r"[\d.]+(?:[eE][+-]?\d+)?"
# yosys prints large areas in scientific notation ("3359 6.25E+03 3359 6.25E+03 cells")
_STAT_RE = re.compile(r"^\s*(\d+)\s+(%s|-)\s+\d+\s+(?:%s|-)\s+cells\s*$" % (_NUM, _NUM), re.M)


def synth_size(synth_stat: Path, top: str) -> tuple[int, float]:
    """(cells, area um2) of the TOP module from yosys' synth_stat.txt.

    A hierarchical report has one section per module and the first is a
    submodule; sizing from it gave dies 1/1.5-1/1.9 of the real area in the
    campaign. Read the top's own section when there are several."""
    t = synth_stat.read_text(errors="replace")
    start = 0
    if t.count("=== ") >= 2:
        i = t.find("=== %s ===" % top)
        start = i if i >= 0 else 0
    m = _STAT_RE.search(t, start)
    # the exact area is on the "Chip area for module" line; the table rounds it
    exact = re.search(r"Chip area for (?:top )?module '\\?%s':\s*(%s)" % (re.escape(top), _NUM), t)
    if m and exact:
        return int(m.group(1)), float(exact.group(1))
    if m and m.group(2) != "-":
        return int(m.group(1)), float(m.group(2))
    if not m:
        m2 = re.search(r"Chip area for (?:top )?module '\\?%s':\s*([\d.]+)" % re.escape(top), t)
        cells = re.search(r"Number of cells:\s*(\d+)", t[start:])
        if not (m2 and cells):
            raise ValueError("cannot read cells/area from %s" % synth_stat)
        return int(cells.group(1)), float(m2.group(1))
    return int(m.group(1)), float(m.group(2))


_PORT_RE = re.compile(r"^\s*(input|output|inout)\s+(?:wire\s+|reg\s+)?(?:\[(\d+):(\d+)\]\s*)?([^;]+);", re.M)


def count_io_bits(netlist: Path, top: str) -> int:
    """IO pin count of the top module in the synthesized netlist (bits, not ports)."""
    t = netlist.read_text(errors="replace")
    m = re.search(r"\bmodule\s+\\?%s\b" % re.escape(top), t)
    if not m:
        raise ValueError("top %s not in %s" % (top, netlist))
    body = t[m.start():t.find("endmodule", m.start())]
    n = 0
    for _dir, hi, lo, names in _PORT_RE.findall(body):
        width = abs(int(hi) - int(lo)) + 1 if hi else 1
        n += width * len([x for x in names.split(",") if x.strip()])
    return n


def apply_sizing(project: Path, synth_stat: Path, netlist: Path) -> dict:
    meta = json.loads((project / "metadata.json").read_text())
    p = platforms.get(meta["platform"])
    cells, area = synth_size(synth_stat, meta["top"])
    pins = count_io_bits(netlist, meta["top"])
    sizing = platforms.die_for(p, area, pins)
    sizing.update(cells=cells, area_um2=area, pins=pins,
                  period_ns=platforms.period_for(p, cells))
    _write_config(project, meta, sizing)
    meta["sizing"] = sizing
    meta["status"] = "sized"
    (project / "metadata.json").write_text(json.dumps(meta, indent=2))
    return sizing
