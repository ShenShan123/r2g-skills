#!/usr/bin/env python3
"""Ground-truth verifier for one design's PyG graph dataset (variants b..f).

Independently re-derives every structural and label expectation from the
feature/label CSVs (separate pandas code, NOT graph_lib) and compares against
the shipped tensors:

  * per-variant node counts by type (block order gate,net,iopin,pin)
  * per-variant expected EDGE counts — b/c by row counting, d/e/f by the
    clique formula sum C(k,2) over per-net/per-gate unique endpoints
  * c-variant edge_attr == folded pin's features (unambiguous samples)
  * f-variant edge_attr == the connecting net's features (unambiguous samples)
  * EXACT expected non-NaN count per y slot per variant (label joins), plus
    sampled value equality against the label CSVs
  * node_name positional integrity; x1 graph_id uniform; y0 == node type
  * global_feat == metadata.csv row (METADATA_SCHEMA order)
  * netlist_graph.pt: cell count vs an independent master-regex count; nets
    and sampled connectivity vs the statement parser
  * manifest consistency (variant stats == tensors; label_health all ok)
  * value sanity: sum_pin_cap_fF p50 within physical range, hpwl >= 0

Wide-coverage extension (2026-07-06 nangate45 round) — extended_checks() re-parses the
RAW liberty/LEF/DEF with independent local parsers (never techlib) and verifies:
  * X values: gate area/leakage vs liberty, x/y/orientation vs DEF (dbu-aware),
    cell_type_id injectivity + the dedicated shared MACRO id, macro bus-pin
    classification (liberty bus() regression), sum_pin_cap_fF vs Σ liberty load caps,
    net pin_count/num_drivers/num_sinks/hpwl/connects_macro_flag vs DEF+liberty+LEF
    BLOCK truth, iopin x/y/direction vs DEF PINS, metadata section counts/dbu/die/
    tracks_per_layer (numeric regression)/V_nom, global_feat[12] nonzero.
  * Y values: congestion via a FULL independent demand/capacity/gaussian recompute
    (catches transposes/dbu/gcell errors on any platform), label==sqrt, wirelength vs
    an independent DEF route walk, timing covers every sequential-master instance,
    irdrop canonical header (raw-PDNSim-dump regression) + physical range.
  * Structure: edge symmetry, self-loop ban, node_name uniqueness.
  * netlist_graph: platform-GENERIC independent instance count (the old regex was
    hardcoded to the sky130 master prefix and counted 0 on every other platform).

Usage: $R2G_GRAPH_PYTHON tools/verify_graph_dataset.py <case_dir> [--design NAME] [--json OUT]
       $R2G_GRAPH_PYTHON tools/verify_graph_dataset.py --batch <root> [--json OUT]
Needs torch + pandas (the graph stage's venv — see run_graphs.sh / graph-dataset.md).
Exit 0 = all checks pass; 1 = at least one FAIL (details on stdout / --json).

Proven baseline (2026-07-06): 54/54 checks on 9 sky130hd designs spanning
159..190K cells (FSM, UART, AXI register/CDMA, CPU, USB, combinational S-box,
SHA-256, AES) — see docs/superpowers/plans/rtl2graph-integration-audit-2026-07-05.md.
For CSV-level (extractor-truth) spot checks, additionally compare sampled nets
against OpenROAD: `report_wire_length -net <net> -detailed_route` on the run's
6_final.odb emits `[INFO GRT-0240] Net <n> ... length: <x>um` lines to diff
against labels/wirelength.csv (patch/RECT metal excluded → CSV reads ~0.2um low
on RECT-bearing sky130 nets).
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import re
import sys

import pandas as pd
import torch

GATE_SCHEMA = ["cell_type_id", "cell_area", "cell_power", "x_um", "y_um",
               "orientation_id", "placement_status_id"]
NET_SCHEMA = ["net_type_id", "fanout", "pin_count", "num_drivers", "num_sinks",
              "connects_macro_flag", "num_layer", "hpwl_um"]
IOPIN_SCHEMA = ["pin_x_um", "pin_y_um", "nearest_tap_distance_um", "pin_direction_id"]
PIN_SCHEMA = ["pin_type_id", "sum_pin_cap_fF"]
METADATA_SCHEMA = ["num_cells", "num_nets", "num_ios", "avg_fanout", "die_width",
                   "die_height", "core_area", "dbu_unit", "PLACE_DENSITY",
                   "CORE_UTILIZATION", "ABC_AREA", "C_total", "tracks_per_layer",
                   "V_nom", "freq_Hz"]

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append({"check": name, "ok": bool(ok), "detail": str(detail)[:300]})
    tag = "PASS" if ok else "FAIL"
    print(f"  [{tag}] {name}" + (f" — {detail}" if (detail and not ok) else ""))
    return ok


def c2(k):
    return k * (k - 1) // 2


_VERILOG_KEYWORDS = {
    "module", "endmodule", "input", "output", "inout", "wire", "reg", "assign",
    "always", "initial", "begin", "end", "if", "else", "case", "endcase",
    "function", "endfunction", "parameter", "localparam", "supply0", "supply1",
    "tri", "genvar", "generate", "endgenerate", "specify", "endspecify", "defparam",
}


# ===========================================================================
# Independent ground-truth readers (deliberately SEPARATE implementations from
# scripts/extract/techlib — same spec, different code, so a parser bug on either
# side shows up as a mismatch instead of agreeing with itself).
# ===========================================================================

def read_liberty_truth(paths):
    """{CELL: {area, is_seq, pins: {name|bus_base: (direction, cap_ff)}}} via a
    brace walker. Bus/bundle groups are stored under their base name."""
    cells = {}
    cap_scale = 1.0
    for path in paths:
        if not path or not os.path.isfile(path):
            continue
        opener = __import__("gzip").open if path.endswith(".gz") else open
        with opener(path, "rt", errors="ignore") as fh:
            text = fh.read()
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        m_u = re.search(r'capacitive_load_unit\s*\(\s*([\d.eE+-]+)\s*,\s*"?(\w+)"?\s*\)', text)
        if m_u:
            cap_scale = float(m_u.group(1)) * ({"ff": 1.0, "pf": 1e3, "nf": 1e6}
                                               .get(m_u.group(2).lower(), 1.0))
        v_nom = None
        m_v = re.search(r"\bnom_voltage\s*:\s*([\d.eE+-]+)", text)
        if m_v:
            v_nom = float(m_v.group(1))
        depth = 0
        cell = None
        cell_depth = -1
        pin = None
        pin_depth = -1
        leak_depth = -1   # inside a block-form leakage_power(){...} group
        for line in text.splitlines():
            s = line.strip()
            if not s:
                continue
            opens, closes = s.count("{"), s.count("}")
            m = re.match(r'cell\s*\(\s*"?([^")]+?)"?\s*\)\s*\{', s)
            if m:
                cell = cells.setdefault(m.group(1).strip().upper(),
                                        {"area": None, "is_seq": False, "pins": {},
                                         "v_nom": v_nom, "power": None})
                cell_depth = depth + opens
                pin = None
            if cell is not None:
                if pin is None:
                    m = re.match(r"area\s*:\s*([\d.eE+-]+)", s)
                    if m:
                        cell["area"] = float(m.group(1))
                    m = re.match(r"cell_leakage_power\s*:\s*([\d.eE+-]+)", s)
                    if m:
                        cell["power"] = float(m.group(1))
                    # Block-form leakage: asap7/gf180 write leakage_power(){value:X}
                    # with NO scalar cell_leakage_power. Matching only the scalar left
                    # power=None on those platforms, so ext.gate power passed vacuously
                    # (BUG-2). Capture the first `value` inside a leakage_power group.
                    if re.match(r"leakage_power\s*\(", s):
                        leak_depth = depth + opens
                    if leak_depth >= 0 and cell["power"] is None:
                        m = re.match(r'value\s*:\s*"?([\d.eE+-]+)"?\s*;', s)
                        if m:
                            cell["power"] = float(m.group(1))
                if re.match(r"(ff|latch|statetable)\s*\(", s):
                    cell["is_seq"] = True
                m = re.match(r'(?:pin|bus|bundle)\s*\(\s*"?([^")]+?)"?\s*\)\s*\{', s)
                if m:
                    pin = cell["pins"].setdefault(m.group(1).strip(), ["", None])
                    pin_depth = depth + opens
                if pin is not None:
                    m = re.match(r'direction\s*:\s*"?(\w+)"?\s*;', s)
                    if m:
                        pin[0] = m.group(1).upper()
                    m = re.match(r'capacitance\s*:\s*"?([\d.eE+-]+)"?\s*;', s)
                    if m:
                        pin[1] = float(m.group(1)) * cap_scale
            depth += opens - closes
            if leak_depth >= 0 and depth < leak_depth:
                leak_depth = -1
            if pin is not None and depth < pin_depth:
                pin = None
            if cell is not None and depth < cell_depth:
                cell = None
                pin = None
    return cells


def lib_pin_truth(cells, master, pin_name):
    """(direction, cap_ff) with bus-base fallback for per-bit names."""
    c = cells.get((master or "").upper())
    if not c:
        return ("", None)
    if pin_name in c["pins"]:
        return tuple(c["pins"][pin_name])
    m = re.match(r"^(.*)\[\d+\]$", pin_name)
    if m and m.group(1) in c["pins"]:
        return tuple(c["pins"][m.group(1)])
    return ("", None)


def read_lef_truth(tech_lef, extra_lefs=()):
    """(routing_layers {name:(pitch, dir)}, block_masters set) — independent parse."""
    layers = {}
    if tech_lef and os.path.isfile(tech_lef):
        cur = None
        for line in open(tech_lef, errors="ignore"):
            s = line.strip()
            m = re.match(r"LAYER\s+(\S+)\s*$", s)
            if m:
                cur = {"name": m.group(1), "type": "", "pitch": 0.0, "dir": ""}
                continue
            if cur is None:
                continue
            if s.startswith("TYPE"):
                cur["type"] = s.split()[1].rstrip(";").strip()
            elif s.startswith("PITCH"):
                cur["pitch"] = float(s.split()[1].rstrip(";"))
            elif s.startswith("DIRECTION"):
                cur["dir"] = s.split()[1].rstrip(";").strip()
            elif s.startswith("END") and cur["name"] in s:
                if cur["type"] == "ROUTING":
                    layers[cur["name"]] = (cur["pitch"], cur["dir"])
                cur = None
    blocks = set()
    for lef in extra_lefs or ():
        if not lef or not os.path.isfile(lef):
            continue
        text = open(lef, errors="ignore").read()
        for m in re.finditer(r"MACRO\s+(\S+)(.*?)END\s+\1", text, re.S):
            if re.search(r"\bCLASS\s+BLOCK\b", m.group(2)):
                blocks.add(m.group(1).upper())
    return layers, blocks


def read_def_truth(def_path):
    """Independent DEF facts: units/diearea/gcell/tracks + components/pins/nets
    (+ per-net routed length in um and per-gcell H/V demand)."""
    dbu = 1000.0
    diearea = None
    gstep = [None, None]
    tracks = {}
    comps = {}
    pins = {}
    nets = {}
    section = None
    cur_net = None
    pt_re = re.compile(r"\(\s*(-?\d+|\*)\s+(-?\d+|\*)(?:\s+-?\d+)?\s*\)")
    conn_re = re.compile(r"\(\s*([^\s()]+)\s+([^\s()]+)\s*\)")
    demand_h = {}
    demand_v = {}
    net_len = {}

    def _add_seg(x1, y1, x2, y2, net):
        if x1 == x2 and y1 == y2:
            return
        net_len[net] = net_len.get(net, 0.0) + (abs(x2 - x1) + abs(y2 - y1)) / dbu
        if gstep[0] and gstep[1]:
            if y1 == y2:  # horizontal — walk x, fixed y
                lo, hi = sorted((x1, x2))
                fixed = y1 // gstep[1]
                cur = lo
                while cur < hi:
                    g = cur // gstep[0]
                    nxt = min(hi, (g + 1) * gstep[0])
                    demand_h[(g, fixed)] = demand_h.get((g, fixed), 0.0) + (nxt - cur) / dbu
                    cur = nxt
            elif x1 == x2:  # vertical — walk y, fixed x; key stays (x, y)
                lo, hi = sorted((y1, y2))
                fixed = x1 // gstep[0]
                cur = lo
                while cur < hi:
                    g = cur // gstep[1]
                    nxt = min(hi, (g + 1) * gstep[1])
                    demand_v[(fixed, g)] = demand_v.get((fixed, g), 0.0) + (nxt - cur) / dbu
                    cur = nxt

    with open(def_path, errors="ignore") as fh:
        for raw in fh:
            s = raw.strip()
            if s.startswith("UNITS DISTANCE MICRONS"):
                dbu = float(s.split()[3])
            elif s.startswith("DIEAREA"):
                nums = [int(t) for t in re.findall(r"-?\d+", s)]
                if len(nums) >= 4:
                    diearea = nums[:4]
            elif s.startswith("GCELLGRID"):
                parts = s.split()
                try:
                    step = int(parts[parts.index("STEP") + 1])
                    if parts[1] == "X":
                        gstep[0] = step
                    else:
                        gstep[1] = step
                except (ValueError, IndexError):
                    pass
            elif s.startswith("TRACKS"):
                m = re.search(r"\bDO\s+(\d+)\s+STEP\s+\S+\s+LAYER\s+(\S+)", s)
                if m:
                    tracks[m.group(2)] = tracks.get(m.group(2), 0) + int(m.group(1))
            elif s.startswith("COMPONENTS"):
                section = "comps"
            elif s.startswith("END COMPONENTS"):
                section = None
            elif s.startswith("PINS"):
                section = "pins"
                cur_pin = None
            elif s.startswith("END PINS"):
                section = None
            elif s.startswith("NETS") and not s.startswith("SPECIALNETS"):
                section = "nets"
            elif s.startswith("END NETS"):
                section = None
            elif s.startswith("SPECIALNETS"):
                section = "snets"
            elif s.startswith("END SPECIALNETS"):
                section = None
            elif section == "comps" and s.startswith("-"):
                t = s.split()
                if len(t) >= 3:
                    m = re.search(r"\+\s+(?:PLACED|FIXED)\s+\(\s*(-?\d+)\s+(-?\d+)\s*\)\s+(\w+)", s)
                    comps[t[1]] = {"master": t[2],
                                   "x": int(m.group(1)) if m else None,
                                   "y": int(m.group(2)) if m else None,
                                   "orient": m.group(3) if m else None}
            elif section == "pins":
                if s.startswith("-"):
                    t = s.split()
                    cur_pin = t[1]
                    pins[cur_pin] = {"dir": "", "x": None, "y": None}
                if cur_pin:  # DIRECTION rides on the dash line itself in ORFS DEFs
                    if "+ DIRECTION" in s:
                        pins[cur_pin]["dir"] = s.split("+ DIRECTION")[1].split()[0].rstrip(";")
                    m = re.search(r"\+\s+(?:PLACED|FIXED)\s+\(\s*(-?\d+)\s+(-?\d+)\s*\)", s)
                    if m:
                        pins[cur_pin]["x"] = int(m.group(1))
                        pins[cur_pin]["y"] = int(m.group(2))
            elif section == "nets":
                if s.startswith("-"):
                    cur_net = s.split()[1]
                    nets.setdefault(cur_net, [])
                if cur_net:
                    if "ROUTED" in s or s.startswith("NEW"):
                        # strip RECT(...) patches, then walk the (*-relative) chain
                        body = re.sub(r"RECT\s*\(\s*-?\d+\s+-?\d+\s+-?\d+\s+-?\d+\s*\)", " ", s)
                        pts = pt_re.findall(body)
                        px = py = None
                        for xs, ys in pts:
                            x = px if xs == "*" else int(xs)
                            y = py if ys == "*" else int(ys)
                            if px is not None and py is not None and x is not None and y is not None:
                                _add_seg(px, py, x, y, cur_net)
                            px, py = x, y
                    elif not s.startswith("+") and "(" in s:
                        for inst, pn in conn_re.findall(s):
                            nets[cur_net].append((inst, pn))
    return {"dbu": dbu, "diearea": diearea, "gstep": gstep, "tracks": tracks,
            "comps": comps, "pins": pins, "nets": nets, "net_len": net_len,
            "demand_h": demand_h, "demand_v": demand_v}


def resolve_platform_files(case_dir):
    """Platform file PATHS via the production resolver (values re-derived here)."""
    import subprocess
    cfg = os.path.join(case_dir, "constraints", "config.mk")
    resolver = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                            "r2g-skills/def-graph", "scripts", "flow", "resolve_platform_paths.sh")
    platform = ""
    if os.path.isfile(cfg):
        m = re.search(r"^\s*(?:export\s+)?PLATFORM\s*=\s*(\S+)", open(cfg).read(), re.M)
        if m:
            platform = m.group(1)
    out = {}
    if os.path.isfile(resolver):
        try:
            txt = subprocess.run(["bash", resolver, cfg, platform],
                                 capture_output=True, text=True, timeout=60).stdout
            for line in txt.splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    out[k.strip()] = v.strip()
        except Exception:
            pass
    out["PLATFORM"] = platform
    return out


def _lef_macro_sizes(lef_paths):
    """``{macro_name: (w_um, h_um)}`` from LEF ``SIZE w BY h`` lines — an
    independent (non-techlib) parse of the std-cell + macro LEFs, so the
    congestion recompute below can reproduce the extractor's orientation-aware
    bbox mapping. Keyed by the RAW master name (matches the DEF master lookup)."""
    sizes = {}
    for lef in lef_paths:
        if not lef or not os.path.isfile(lef):
            continue
        cur = None
        for line in open(lef, errors="ignore"):
            t = line.replace(";", " ").split()
            if not t:
                continue
            if t[0] == "MACRO" and len(t) >= 2:
                cur = t[1]
            elif cur and t[0] == "SIZE":
                try:
                    by = t.index("BY")
                    sizes[cur] = (float(t[by - 1]), float(t[by + 1]))
                except (ValueError, IndexError):
                    pass
            elif cur and t[0] == "END" and len(t) >= 2 and t[1] == cur:
                cur = None
    return sizes


def _reflect(p, n):
    """scipy 'reflect' boundary index: (d c b a | a b c d | d c b a)."""
    if n == 1:
        return 0
    period = 2 * n
    r = p % period
    if r < 0:
        r += period
    return r if r < n else period - 1 - r


def dense_gaussian_r4(util, gxn, gyn, sigma=1.0, truncate=4.0):
    """Independent radius-4 separable REFLECT-boundary Gaussian over a dense
    ``gxn x gyn`` grid — a clean-room reimplementation of the scipy
    ``gaussian_filter(sigma=1.0)`` that ``extract_congestion.py`` ports
    (radius = int(4*sigma+0.5) = 4, axis-0 then axis-1). Used ONLY to VERIFY that
    extractor; the demand/util grid it smooths is re-derived independently in
    read_def_truth, so a transpose/dbu/capacity bug still shows as a mismatch."""
    radius = int(truncate * sigma + 0.5)
    w = [math.exp(-0.5 * (k * k) / (sigma * sigma)) for k in range(-radius, radius + 1)]
    s = sum(w)
    w = [x / s for x in w]
    tmp = [[0.0] * gyn for _ in range(gxn)]
    for y in range(gyn):
        for x in range(gxn):
            acc = 0.0
            for k, wk in enumerate(w):
                acc += wk * util[_reflect(x + k - radius, gxn)][y]
            tmp[x][y] = acc
    out = [[0.0] * gyn for _ in range(gxn)]
    for x in range(gxn):
        row = tmp[x]
        orow = out[x]
        for y in range(gyn):
            acc = 0.0
            for k, wk in enumerate(w):
                acc += wk * row[_reflect(y + k - radius, gyn)]
            orow[y] = acc
    return out


def irdrop_label_ok(ir):
    """Mirror extract_irdrop.tcl's noise-floor gate when verifying the irdrop label.

    The extractor sets label = log1p(IR_Drop_mV / P95_mV) ONLY where `has_irdrop` (i.e.
    P95_mV >= 0.05, the PDN-noise floor) AND P95 > 0; BELOW the floor it forces label = 0
    (extract_irdrop.tcl:208-224). A verifier that asserts log1p on EVERY row therefore
    false-fails every low-IR design (e.g. iir P95=0.044mV -> all 95 labels legitimately 0
    yet log1p(IR/P95) != 0). Gate the check on the same `has_irdrop` column (deriving the
    floor from P95 for legacy CSVs that lack it). Returns (ok: bool, detail: str).
    """
    mv = pd.to_numeric(ir["IR_Drop_mV"], errors="coerce")
    p95 = pd.to_numeric(ir["P95_mV"], errors="coerce")
    lab = pd.to_numeric(ir["label"], errors="coerce")
    if "has_irdrop" in ir.columns:
        has_ir = ir["has_irdrop"].astype(str).str.strip().str.lower().eq("true")
    else:                                     # legacy CSV: re-derive the noise floor
        has_ir = p95 >= 0.05
    active = has_ir & (p95 > 0)               # rows carrying a real log1p label
    # active rows: label == log1p(IR/P95) (columns are %.6f-rounded vs unrounded compute)
    d_active = (float((lab - (mv / p95).apply(math.log1p)).abs()[active].max())
                if bool(active.any()) else 0.0)
    # floored rows (below noise floor): label must be exactly 0
    d_floor = float(lab[~active].abs().max()) if bool((~active).any()) else 0.0
    ok = (d_active <= 1e-3) and (d_floor <= 1e-9)
    return ok, f"active={int(active.sum())} active_maxdiff={d_active} floored_maxlabel={d_floor}"


def _spef_deesc(name):
    # DEF convention: strip backslash EXCEPT before bus brackets (independent copy
    # of the spec that scripts/extract/techlib/spef.py implements).
    return re.sub(r"\\([^\[\]])", r"\1", name) if "\\" in name else name


def read_spef_truth(case):
    """Independent SPEF re-derivation (SEPARATE code from techlib.spef): per-net
    ground cap (fF) and per-cross-net-pair coupling cap (fF), names de-escaped to
    DEF convention. Returns {"present": False} when no SPEF (RC labels absent)."""
    cands = sorted(glob.glob(case + "/backend/RUN_*/rcx/6_final.spef")
                   + glob.glob(case + "/backend/RUN_*/results/6_final.spef"))
    if os.path.isfile(case + "/rcx/6_final.spef"):
        cands.append(case + "/rcx/6_final.spef")
    if not cands:
        return {"present": False}
    spef = cands[-1]
    id2name, net_ids, inst_ids = {}, set(), set()
    pin_to_net, port_to_net = {}, {}
    cap_scale = 1.0

    def _nm(base):
        return id2name.get(base, _spef_deesc(base.lstrip("*")))

    # pass 1: name map + connectivity
    in_nm = cur = sec = None
    in_nm = False
    with open(spef, errors="ignore") as fh:
        for raw in fh:
            s = raw.strip()
            if not s:
                continue
            if s.startswith("*C_UNIT"):
                p = s.split()
                if len(p) >= 3:
                    v, u = float(p[1]), p[2].upper()
                    cap_scale = (v * 1e3 if u.startswith("PF") else v * 1e6 if u.startswith("NF")
                                 else v * 1e9 if u.startswith("UF") else v)
                continue
            if s.startswith("*NAME_MAP"):
                in_nm = True; continue
            if s.startswith(("*PORTS", "*DEFINE", "*POWER_NETS")):
                in_nm = False; continue
            if in_nm and s.startswith("*") and not s.startswith(("*D_NET", "*R_NET")):
                pp = s.split(None, 1)
                if len(pp) == 2:
                    id2name[pp[0]] = _spef_deesc(pp[1].strip()); continue
                in_nm = False
            if s.startswith(("*D_NET", "*R_NET")):
                in_nm = False
                a = s.split()[1] if len(s.split()) >= 2 else ""
                net_ids.add(a); cur = _nm(a); sec = None; continue
            if s.startswith("*CONN"):
                sec = "CONN"; continue
            if s.startswith(("*CAP", "*RES")):
                sec = "SKIP"; continue
            if s.startswith("*END"):
                sec = None; cur = None; continue
            if sec == "CONN" and cur is not None:
                t = s.split()
                if t and t[0] == "*I" and len(t) >= 2:
                    base, _, sub = t[1].partition(":")
                    inst_ids.add(base)
                    pin_to_net[(_nm(base), _spef_deesc(sub))] = cur
                elif t and t[0] == "*P" and len(t) >= 2:
                    port_to_net[id2name.get(t[1], _spef_deesc(t[1]))] = cur

    def net_of(tok):
        base, _, sub = tok.partition(":")
        if base in net_ids:
            return _nm(base)
        if ":" in tok and base in inst_ids:
            return pin_to_net.get((_nm(base), _spef_deesc(sub)))
        return port_to_net.get(_spef_deesc(tok))

    # pass 2: aggregate ground + coupling
    ground, coupling = {}, {}
    cur = None; sec = None; in_nm = False
    with open(spef, errors="ignore") as fh:
        for raw in fh:
            s = raw.strip()
            if not s:
                continue
            if s.startswith("*NAME_MAP"):
                in_nm = True; continue
            if s.startswith(("*PORTS", "*DEFINE", "*POWER_NETS")):
                in_nm = False; continue
            if in_nm and not s.startswith(("*D_NET", "*R_NET")):
                continue
            if s.startswith(("*D_NET", "*R_NET")):
                in_nm = False
                a = s.split()[1] if len(s.split()) >= 2 else ""
                cur = _nm(a); ground.setdefault(cur, 0.0); sec = None; continue
            if s.startswith("*CAP"):
                sec = "CAP"; continue
            if s.startswith("*RES"):
                sec = "RES"; continue
            if s.startswith("*CONN"):
                sec = "CONN"; continue
            if s.startswith("*END"):
                sec = None; cur = None; continue
            if cur is None or sec != "CAP":
                continue
            p = s.split()
            if len(p) == 3:
                try:
                    ground[cur] += float(p[2]) * cap_scale
                except ValueError:
                    pass
            elif len(p) == 4:
                try:
                    cap = float(p[3]) * cap_scale
                except ValueError:
                    continue
                m = net_of(p[2])
                if m and m != cur:
                    k = (cur, m) if cur < m else (m, cur)
                    coupling[k] = coupling.get(k, 0.0) + cap
    return {"present": True, "ground": ground, "coupling": coupling}


def extended_checks(case, design, feat, labs, views, b):
    """Wide-coverage X/Y/structure checks vs independently re-parsed raw files."""
    gate, net, iopin, pin, egp, epn, ein = views
    plat = resolve_platform_files(case)
    lib_paths = (plat.get("LIB_FILES", "") + " " + plat.get("ADDITIONAL_LIBS", "")).split()
    lib = read_liberty_truth(lib_paths)
    layers, blocks = read_lef_truth(plat.get("TECH_LEF", ""),
                                    plat.get("ADDITIONAL_LEFS", "").split())
    runs = sorted((r for r in os.listdir(case + "/backend") if r.startswith("RUN_")),
                  reverse=True)
    def_path = None
    for r in runs:
        for sub in ("final", "results"):
            p = f"{case}/backend/{r}/{sub}/6_final.def"
            if os.path.isfile(p):
                def_path = p
                break
        if def_path:
            break
    if not def_path:
        check("ext: 6_final.def present", False, "no DEF — extended checks skipped")
        return
    dt = read_def_truth(def_path)

    # ---- X gate: area/power/placement/orientation vs liberty + DEF ----
    full_gate = pd.read_csv(os.path.join(feat, "nodes_gate.csv"))
    if "graph_id" in full_gate.columns:
        full_gate = full_gate[full_gate["graph_id"].astype(str) == design]
    # Truncation guard: a cleanly-truncated CSV (fewer COMPLETE rows) passes the
    # stats gate ('ok') and every count check re-derives from the same short CSV.
    # Nothing else compares the node-CSV row count to the DEF — so do it here.
    # (BUG-3, verifier-silent-lies-audit-2026-07-07.md.)
    check("ext.nodes_gate rows == DEF COMPONENTS (truncation guard)",
          len(full_gate) == len(dt["comps"]),
          f"csv={len(full_gate)} def={len(dt['comps'])}")
    sample = full_gate.groupby("master", sort=False).head(2)
    bad_area = bad_pos = bad_orient = bad_power = checked = 0
    area_checked = power_checked = 0   # rows where liberty actually supplied a value
    for _, row in sample.iterrows():
        c = dt["comps"].get(row["inst_name"])
        lc = lib.get(str(row["master"]).upper())
        if not c or c["x"] is None:
            continue
        checked += 1
        if lc and lc.get("area") is not None:
            area_checked += 1
            if abs(row["cell_area"] - lc["area"]) > 1e-3:
                bad_area += 1
        if lc and lc.get("power") is not None:
            power_checked += 1
            if abs(row["cell_power"] - lc["power"]) > max(1e-3, 1e-4 * abs(lc["power"])):
                bad_power += 1
        if abs(row["x_um"] - c["x"] / dt["dbu"]) > 1e-3 or abs(row["y_um"] - c["y"] / dt["dbu"]) > 1e-3:
            bad_pos += 1
        if str(row["orientation"]) != str(c["orient"]):
            bad_orient += 1
    # Require a liberty value to have actually been compared — else the check "passes"
    # having validated NOTHING. area_checked/power_checked == 0 means no sampled gate's
    # master resolved a liberty area/leakage (liberty unresolved, or a leakage form the
    # parser misses, e.g. block-form on asap7/gf180) — that is a FAIL, not a silent pass.
    # (BUG-2, verifier-silent-lies-audit-2026-07-07.md.)
    check("ext.gate area == liberty area", area_checked > 0 and bad_area == 0,
          f"{bad_area}/{area_checked} mismatched (of {checked} sampled)")
    check("ext.gate power == liberty leakage", power_checked > 0 and bad_power == 0,
          f"{bad_power}/{power_checked} mismatched (of {checked} sampled)")
    check("ext.gate x/y == DEF PLACED / dbu", checked > 0 and bad_pos == 0,
          f"{bad_pos}/{checked} mismatched (dbu={dt['dbu']})")
    check("ext.gate orientation == DEF", checked > 0 and bad_orient == 0,
          f"{bad_orient}/{checked} mismatched")

    # ---- X cell_type_id: injective per master; macros share one non-std id ----
    by_master = full_gate.groupby("master")["cell_type_id"].nunique()
    check("ext.cell_type_id single id per master", bool((by_master == 1).all()),
          by_master[by_master > 1].to_dict())
    id_of = full_gate.groupby("master")["cell_type_id"].first()
    std_ids = {int(v) for m, v in id_of.items()
               if str(m).upper() not in blocks and "FILL" not in str(m).upper()
               and "TAP" not in str(m).upper()}
    macro_masters = [m for m in id_of.index if str(m).upper() in blocks]
    if macro_masters:
        macro_ids = {int(id_of[m]) for m in macro_masters}
        check("ext.macro masters share one dedicated id",
              len(macro_ids) == 1 and not (macro_ids & std_ids),
              f"macro ids {macro_ids} std overlap {macro_ids & std_ids}")
    # Injectivity applies only to masters PRESENT in the liberty — physical-only
    # cells absent from the timing liberty (sky130 decap/fakediode/…) legitimately
    # collapse onto the shared UNKNOWN id (documented modeling choice).
    std_masters = [m for m in id_of.index
                   if str(m).upper() not in blocks and str(m).upper() in lib]
    dup = len(std_masters) - len({int(id_of[m]) for m in std_masters})
    check("ext.distinct liberty masters get distinct ids", dup == 0, f"{dup} collisions")

    # ---- X pins: macro pins classified + net-load-sum vs liberty ----
    full_pin = pd.read_csv(os.path.join(feat, "nodes_pin.csv"))
    if "graph_id" in full_pin.columns:
        full_pin = full_pin[full_pin["graph_id"].astype(str) == design]
    if macro_masters:
        macro_insts = set(full_gate[full_gate["master"].isin(macro_masters)]["inst_name"])
        mp = full_pin[full_pin["inst_name"].isin(macro_insts)]
        n14 = int((mp["pin_type_id"].astype(int) == 14).sum())
        check("ext.macro pins classified (no type-14 bus fallout)",
              len(mp) > 0 and n14 == 0, f"{n14}/{len(mp)} unclassified")
    # sampled sum_pin_cap_fF: recompute the net load sum for nets w/o io conns
    spef = any(os.path.isfile(f"{case}/backend/{r}/{sub}/6_final.spef")
               for r in runs for sub in ("rcx", "results"))
    bad_cap = checked_cap = 0
    for net_name, conns in list(dt["nets"].items()):
        if checked_cap >= 25:
            break
        if any(i == "PIN" for i, _ in conns) and spef:
            continue  # io cap comes from SPEF — not re-derived here
        keys = {(i, p) for i, p in conns if i != "PIN"}
        rows = full_pin[[tuple(t) in keys for t in
                         zip(full_pin["inst_name"], full_pin["pin_name"])]]
        if rows.empty:
            continue
        exp = 0.0
        for i, p in conns:
            if i == "PIN":
                continue
            master = dt["comps"].get(i, {}).get("master", "")
            cap = lib_pin_truth(lib, master, p)[1]
            exp += cap or 0.0
        got = float(rows.iloc[0]["sum_pin_cap_fF"])
        checked_cap += 1
        if abs(got - exp) > max(0.05, 0.01 * exp):
            bad_cap += 1
    check("ext.sum_pin_cap_fF == Σ liberty load caps (sampled nets)",
          checked_cap > 0 and bad_cap == 0, f"{bad_cap}/{checked_cap} mismatched")

    # ---- X nets: counts/drivers/sinks/hpwl/connects_macro_flag ----
    full_net = pd.read_csv(os.path.join(feat, "nodes_net.csv"))
    if "graph_id" in full_net.columns:
        full_net = full_net[full_net["graph_id"].astype(str) == design]
    check("ext.nodes_net rows == DEF NETS (truncation guard)",
          len(full_net) == len(dt["nets"]),
          f"csv={len(full_net)} def={len(dt['nets'])}")
    bad = {"pin_count": 0, "drivers": 0, "sinks": 0, "hpwl": 0, "macro": 0}
    checked_net = 0
    net_rows = full_net.set_index("net_name")
    for net_name, conns in dt["nets"].items():
        if net_name not in net_rows.index:
            continue
        row = net_rows.loc[net_name]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]
        checked_net += 1
        if checked_net > 200:
            break
        if int(row["pin_count"]) != len(conns):
            bad["pin_count"] += 1
        drv = snk = 0
        pts = []
        is_macro = 0
        for i, p in conns:
            if i == "PIN":
                d = dt["pins"].get(p, {}).get("dir", "")
                drv += d == "INPUT"
                snk += d == "OUTPUT"
                if dt["pins"].get(p, {}).get("x") is not None:
                    pts.append((dt["pins"][p]["x"] / dt["dbu"], dt["pins"][p]["y"] / dt["dbu"]))
                continue
            comp = dt["comps"].get(i)
            if not comp:
                continue
            master = comp["master"]
            if master.upper() in blocks:
                is_macro = 1
            d = lib_pin_truth(lib, master, p)[0]
            drv += d == "OUTPUT"
            snk += d == "INPUT"
            if comp["x"] is not None:
                pts.append((comp["x"] / dt["dbu"], comp["y"] / dt["dbu"]))
        if int(row["num_drivers"]) != drv:
            bad["drivers"] += 1
        if int(row["num_sinks"]) != snk:
            bad["sinks"] += 1
        if pts:
            hp = (max(x for x, _ in pts) - min(x for x, _ in pts)
                  + max(y for _, y in pts) - min(y for _, y in pts))
            if abs(float(row["hpwl_um"]) - hp) > 0.05:
                bad["hpwl"] += 1
        if int(row["connects_macro_flag"]) != is_macro:
            bad["macro"] += 1
    for k, label in (("pin_count", "pin_count == DEF conns"),
                     ("drivers", "num_drivers vs liberty/DEF dirs"),
                     ("sinks", "num_sinks vs liberty/DEF dirs"),
                     ("hpwl", "hpwl == recomputed cell-origin HPWL"),
                     ("macro", "connects_macro_flag == DEF∩LEF-BLOCK truth")):
        check(f"ext.net {label}", checked_net > 0 and bad[k] == 0,
              f"{bad[k]}/{checked_net} mismatched")

    # ---- X iopins vs DEF PINS ----
    full_io = pd.read_csv(os.path.join(feat, "nodes_iopin.csv"))
    if "graph_id" in full_io.columns:
        full_io = full_io[full_io["graph_id"].astype(str) == design]
    check("ext.nodes_iopin rows == DEF PINS (truncation guard)",
          len(full_io) == len(dt["pins"]),
          f"csv={len(full_io)} def={len(dt['pins'])}")
    dir_map = {"INPUT": 0, "OUTPUT": 1, "INOUT": 2, "FEEDTHRU": 3}
    bad_io = checked_io = 0
    for _, row in full_io.head(50).iterrows():
        p = dt["pins"].get(row["iopin_name"])
        if not p or p["x"] is None:
            continue
        checked_io += 1
        ok = (abs(row["pin_x_um"] - p["x"] / dt["dbu"]) <= 1e-3
              and abs(row["pin_y_um"] - p["y"] / dt["dbu"]) <= 1e-3
              and int(row["pin_direction_id"]) == dir_map.get(p["dir"], -1))
        bad_io += not ok
    check("ext.iopin x/y/direction == DEF PINS", checked_io > 0 and bad_io == 0,
          f"{bad_io}/{checked_io} mismatched")

    # ---- metadata + global_feat ----
    md = pd.read_csv(os.path.join(feat, "metadata.csv"))
    if "graph_id" in md.columns:
        md = md[md["graph_id"].astype(str) == design]
    if len(md):
        row = md.iloc[0]
        check("ext.metadata num_cells/num_nets/num_ios == DEF section counts",
              int(row["num_cells"]) == len(dt["comps"])
              and int(row["num_nets"]) == len(dt["nets"])
              and int(row["num_ios"]) == len(dt["pins"]),
              f"csv=({row['num_cells']},{row['num_nets']},{row['num_ios']}) "
              f"def=({len(dt['comps'])},{len(dt['nets'])},{len(dt['pins'])})")
        check("ext.metadata dbu == DEF UNITS", float(row["dbu_unit"]) == dt["dbu"])
        if dt["diearea"]:
            w = (dt["diearea"][2] - dt["diearea"][0]) / dt["dbu"]
            h = (dt["diearea"][3] - dt["diearea"][1]) / dt["dbu"]
            check("ext.metadata die w/h == DEF DIEAREA",
                  abs(float(row["die_width"]) - w) <= 0.01
                  and abs(float(row["die_height"]) - h) <= 0.01)
        if dt["tracks"]:
            exp_mean = sum(dt["tracks"].values()) / len(dt["tracks"])
            v = pd.to_numeric(row["tracks_per_layer"], errors="coerce")
            check("ext.metadata tracks_per_layer numeric mean (was string→0 bug)",
                  pd.notna(v) and v > 0 and abs(float(v) - exp_mean) <= 0.51,
                  f"csv={row['tracks_per_layer']} expected≈{exp_mean:.1f}")
            if hasattr(b, "global_feat"):
                check("ext.global_feat[12] tracks nonzero",
                      float(b.global_feat[12]) > 0, float(b.global_feat[12]))
        lib_vnoms = [c["v_nom"] for c in lib.values() if c.get("v_nom")]
        if lib_vnoms:
            check("ext.metadata V_nom == liberty nom_voltage",
                  abs(float(row["V_nom"]) - lib_vnoms[0]) <= 1e-6,
                  f"csv={row['V_nom']} lib={lib_vnoms[0]}")

    # ---- Y congestion: full independent recompute matching extract_congestion's
    # PORTED 2-vector method (commit c9b9e3a) — a radius-4 separable REFLECT
    # Gaussian over the dense util grid, averaged over each cell's orientation-aware
    # bbox GCells (origin-GCell fallback when the master SIZE is unknown). Verifies
    # ALL THREE emitted columns: cell_congestion = mean(gaussian_util),
    # label = mean(sqrt(gaussian_util)), label_raw = mean(sqrt(util)). demand_h/v are
    # re-derived in read_def_truth, so a transpose/dbu/gcell/capacity bug still shows.
    # (The pre-2026-07-07 check used a radius-1 single-origin-GCell gaussian and a
    # universal label==sqrt(cell_congestion) identity — both false under the new
    # bbox averaging (mean(sqrt) != sqrt(mean)); see failure-patterns.md #19.) ----
    gs_x, gs_y = dt["gstep"]
    die = dt["diearea"]
    if gs_x and gs_y and layers and die:
        gw, gh = gs_x / dt["dbu"], gs_y / dt["dbu"]
        cap_h = sum(gw * (gh / p) for p, d in layers.values() if d == "HORIZONTAL" and p > 0)
        cap_v = sum(gh * (gw / p) for p, d in layers.values() if d == "VERTICAL" and p > 0)
        gxn = max(1, math.ceil((die[2] - die[0]) / gs_x))
        gyn = max(1, math.ceil((die[3] - die[1]) / gs_y))
        udense = [[0.0] * gyn for _ in range(gxn)]
        for g in set(dt["demand_h"]) | set(dt["demand_v"]):
            gx, gy = g
            if 0 <= gx < gxn and 0 <= gy < gyn:
                hu = dt["demand_h"].get(g, 0.0) / cap_h if cap_h else 0.0
                vu = dt["demand_v"].get(g, 0.0) / cap_v if cap_v else 0.0
                udense[gx][gy] = max(hu, vu)
        gdense = dense_gaussian_r4(udense, gxn, gyn)
        sizes = _lef_macro_sizes([plat.get("SC_LEF", "")] + plat.get("ADDITIONAL_LEFS", "").split())
        cong = pd.read_csv(os.path.join(labs, "cell_congestion.csv"))
        if "Design" in cong.columns:
            cong = cong[cong["Design"] == design]
        has_raw = "label_raw" in cong.columns
        bad_c = bad_l = bad_r = checked_c = 0
        for _, row in cong.head(400).iterrows():
            comp = dt["comps"].get(row["Cell"])
            if not comp or comp["x"] is None:
                continue
            ox, oy = comp["x"], comp["y"]
            wh = sizes.get(str(comp["master"]))
            if wh:
                o = (comp["orient"] or "N").upper()
                if "N" in o or "S" in o:      # mirror extract_congestion.cell_bbox_dbu
                    bw, bh = wh[0], wh[1]
                elif "W" in o or "E" in o:
                    bw, bh = wh[1], wh[0]
                else:
                    bw, bh = wh[0], wh[1]
                gx0 = min(max(int(ox) // gs_x, 0), gxn - 1)
                gx1 = min(max(int(ox + bw * dt["dbu"]) // gs_x, 0), gxn - 1)
                gy0 = min(max(int(oy) // gs_y, 0), gyn - 1)
                gy1 = min(max(int(oy + bh * dt["dbu"]) // gs_y, 0), gyn - 1)
            else:
                gx0 = gx1 = min(max(ox // gs_x, 0), gxn - 1)
                gy0 = gy1 = min(max(oy // gs_y, 0), gyn - 1)
            sg = ssg = ssu = 0.0
            cnt = 0
            for gx in range(gx0, gx1 + 1):
                for gy in range(gy0, gy1 + 1):
                    u = udense[gx][gy]
                    g = gdense[gx][gy]
                    sg += g
                    ssg += math.sqrt(g) if g > 0 else 0.0
                    ssu += math.sqrt(u) if u > 0 else 0.0
                    cnt += 1
            if not cnt:
                continue
            checked_c += 1
            tol = lambda e: max(1e-6, 0.003 * e)
            # NaN-safe: the recompute always yields a real number, so a NaN in the
            # shipped column is a defect. abs(recompute - NaN) > tol is ALWAYS False,
            # which let an all-NaN/partial-NaN congestion label pass this recompute
            # vacuously. (BUG-1, verifier-silent-lies-audit-2026-07-07.md.)
            cc, lb = float(row["cell_congestion"]), float(row["label"])
            if math.isnan(cc) or abs(sg / cnt - cc) > tol(sg / cnt):
                bad_c += 1
            if math.isnan(lb) or abs(ssg / cnt - lb) > tol(ssg / cnt):
                bad_l += 1
            if has_raw:
                lr = float(row["label_raw"])
                if math.isnan(lr) or abs(ssu / cnt - lr) > tol(ssu / cnt):
                    bad_r += 1
        check("ext.congestion cell_congestion == independent radius-4 bbox recompute",
              checked_c > 0 and bad_c == 0, f"{bad_c}/{checked_c} mismatched")
        check("ext.congestion label == mean sqrt(gaussian_util) over bbox",
              checked_c > 0 and bad_l == 0, f"{bad_l}/{checked_c} mismatched")
        if has_raw:
            check("ext.congestion label_raw == mean sqrt(util) over bbox",
                  checked_c > 0 and bad_r == 0, f"{bad_r}/{checked_c} mismatched")
    else:
        # No silent skip: a design missing GCELLGRID / routing layers / DIEAREA means
        # congestion values are NEVER validated against the DEF, yet the run would still
        # report all-green. Flag it loudly instead. (L1, verifier-silent-lies-audit-
        # 2026-07-07.md.)
        check("ext.congestion inputs resolved (GCELLGRID + routing layers + DIEAREA)",
              False,
              f"gstep={dt['gstep']} routing_layers={len(layers)} "
              f"diearea={'yes' if die else 'no'} — congestion value-vs-DEF NOT checked")

    # ---- Y wirelength: sampled nets vs independent DEF route walk.
    # Raw um lives in WireLength_um; label is the log1p transform of it. ----
    wl = pd.read_csv(os.path.join(labs, "wirelength.csv"))
    if "Design" in wl.columns:
        wl = wl[wl["Design"] == design]
    wmap = dict(zip(wl["Net"], pd.to_numeric(wl["WireLength_um"], errors="coerce"))) \
        if "WireLength_um" in wl.columns else {}
    bad_w = checked_w = 0
    routed = [(n, l) for n, l in dt["net_len"].items() if n in wmap]
    routed.sort(key=lambda t: -t[1])
    for n, exp in routed[:10] + routed[len(routed) // 2:len(routed) // 2 + 10]:
        checked_w += 1
        if abs(wmap[n] - exp) > max(0.3, 0.01 * exp):
            bad_w += 1
    check("ext.wirelength um == independent DEF route length (sampled)",
          checked_w > 0 and bad_w == 0, f"{bad_w}/{checked_w} mismatched")
    if {"WireLength_um", "label"} <= set(wl.columns) and len(wl):
        d_log = (pd.to_numeric(wl["label"], errors="coerce")
                 - (pd.to_numeric(wl["WireLength_um"], errors="coerce")).apply(math.log1p)).abs().max()
        check("ext.wirelength label == log1p(um)", d_log <= 1e-6, d_log)

    # ---- Y timing: every sequential-master instance is in the timing CSV ----
    tim = pd.read_csv(os.path.join(labs, "timing_features.csv"))
    if "Design" in tim.columns:
        tim = tim[tim["Design"] == design]
    seq_insts = {i for i, c in dt["comps"].items()
                 if lib.get(c["master"].upper(), {}).get("is_seq")}
    if seq_insts:
        covered = seq_insts & set(tim["Cell"])
        check("ext.timing covers every sequential instance",
              covered == seq_insts,
              f"{len(covered)}/{len(seq_insts)} registers in timing CSV")

    # ---- Y irdrop: canonical header + physical mV range + label transform.
    # label = log1p(IR_Drop_mV / P95_mV) ONLY above the has_irdrop noise floor
    # (P95_mV >= 0.05); below it the extractor forces label = 0 — see irdrop_label_ok()
    # and extract_irdrop.tcl:208-224. ----
    irp = os.path.join(labs, "ir_drop.csv")
    if os.path.isfile(irp):
        ir = pd.read_csv(irp)
        check("ext.irdrop canonical header (not a raw PDNSim dump)",
              {"Cell", "label", "IR_Drop_mV"} <= set(ir.columns), list(ir.columns)[:6])
        if {"Cell", "label", "IR_Drop_mV", "P95_mV"} <= set(ir.columns) and len(ir):
            mv = pd.to_numeric(ir["IR_Drop_mV"], errors="coerce")
            vnom = [c["v_nom"] for c in lib.values() if c.get("v_nom")]
            cap_mv = 0.2 * (vnom[0] if vnom else 1.0) * 1000.0
            check("ext.irdrop IR_Drop_mV physical (0..20% of supply)",
                  mv.notna().any() and float(mv.min()) >= 0 and float(mv.max()) < cap_mv,
                  f"min={mv.min()} max={mv.max()} cap={cap_mv}")
            ok_ir, det_ir = irdrop_label_ok(ir)
            check("ext.irdrop label == log1p(IR/P95) above noise floor, else 0",
                  ok_ir, det_ir)

    # ---- structural: symmetry / self-loops / name uniqueness ----
    ei = b.edge_index
    pairs = {}
    for k in range(ei.shape[1]):
        u, v = int(ei[0, k]), int(ei[1, k])
        pairs[(u, v)] = pairs.get((u, v), 0) + 1
    asym = sum(1 for (u, v), n in pairs.items() if pairs.get((v, u), 0) != n)
    loops = sum(n for (u, v), n in pairs.items() if u == v)
    check("ext.b edges symmetric (undirected both ways)", asym == 0, f"{asym} asym")
    check("ext.b no self-loops", loops == 0, f"{loops} loops")
    # Names may legitimately collide ACROSS blocks (a net and an instance can share
    # a name) — uniqueness is required per node-type block only.
    nt = b.x[:, 0].long()
    dup_blocks = []
    for t in (0, 1, 2):
        blk = [b.node_name[i] for i in range(len(b.node_name)) if int(nt[i]) == t]
        if len(set(blk)) != len(blk):
            dup_blocks.append(t)
    check("ext.b node_name unique within each type block", not dup_blocks,
          f"dup in type blocks {dup_blocks}")


def build_views(feat_dir, design):
    """Independent re-application of build_feature_views' documented filters."""
    def load(name):
        df = pd.read_csv(os.path.join(feat_dir, name))
        if "graph_id" in df.columns:
            df = df[df["graph_id"].astype(str) == design]
        return df.reset_index(drop=True)

    gate = load("nodes_gate.csv")
    net = load("nodes_net.csv")
    iopin = load("nodes_iopin.csv")
    pin = load("nodes_pin.csv")
    egp = load("edges_gate_pin.csv")
    epn = load("edges_pin_net.csv")
    ein = load("edges_iopin_net.csv")

    gate = gate[~gate["master"].str.contains("FILL|TAP", case=False, na=False)]
    net = net[net["net_type_id"] == 0]
    iopin = iopin[iopin["net_type_id"] == 0]
    epn = epn[epn["net_type_id"] == 0]
    ein = ein[ein["net_type_id"] == 0]
    pin = pin[pin["inst_name"] != "PIN"].merge(
        epn[["inst_name", "pin_name"]].drop_duplicates(),
        on=["inst_name", "pin_name"], how="inner")
    gate = gate[gate["inst_name"].isin(set(pin["inst_name"]))]
    net = net[net["net_name"].isin(set(epn["net_name"]) | set(ein["net_name"]))]
    iopin = iopin[iopin["iopin_name"].isin(set(ein["iopin_name"]))]

    pin_keys = pin[["inst_name", "pin_name"]].drop_duplicates()
    egp = egp.merge(pin_keys, on=["inst_name", "pin_name"], how="inner")
    egp = egp[egp["inst_name"].isin(set(gate["inst_name"]))].drop_duplicates()
    epn = epn.merge(pin_keys, on=["inst_name", "pin_name"], how="inner")
    epn = epn[epn["net_name"].isin(set(net["net_name"]))].drop_duplicates()
    ein = ein[ein["iopin_name"].isin(set(iopin["iopin_name"]))
              & ein["net_name"].isin(set(net["net_name"]))].drop_duplicates()

    gate = gate.sort_values("inst_name", kind="mergesort").reset_index(drop=True)
    net = net.sort_values("net_name", kind="mergesort").reset_index(drop=True)
    iopin = iopin.sort_values("iopin_name", kind="mergesort").reset_index(drop=True)
    pin = pin.sort_values(["inst_name", "pin_name"], kind="mergesort").reset_index(drop=True)
    return gate, net, iopin, pin, egp, epn, ein


def expected_label_series(views, labels_dir, design):
    """Per entity type, the expected label value keyed by name (NaN = no join)."""
    gate, net, iopin, pin, *_ = views

    def lab(fname):
        p = os.path.join(labels_dir, fname)
        df = pd.read_csv(p)
        if "Design" in df.columns:
            df = df[df["Design"] == design]
        return df

    out = {}
    cong = lab("cell_congestion.csv")
    m = dict(zip(cong["Cell"], pd.to_numeric(cong["label"], errors="coerce"))) \
        if {"Cell", "label"} <= set(cong.columns) else {}
    out["gate", 0] = gate["inst_name"].map(m)

    ir = lab("ir_drop.csv")
    if {"Cell", "label"} <= set(ir.columns):
        g = ir.assign(label=pd.to_numeric(ir["label"], errors="coerce")) \
              .groupby("Cell")["label"].max()
        out["gate", 1] = gate["inst_name"].map(g)
    else:
        out["gate", 1] = pd.Series([float("nan")] * len(gate))

    tim = lab("timing_features.csv")
    tm = dict(zip(tim["Cell"], pd.to_numeric(tim["label"], errors="coerce"))) \
        if {"Cell", "label"} <= set(tim.columns) else {}
    out["pin", 2] = pin["inst_name"].map(tm)

    wl = lab("wirelength.csv")
    wm = dict(zip(wl["Net"], pd.to_numeric(wl["label"], errors="coerce"))) \
        if {"Net", "label"} <= set(wl.columns) else {}
    out["net", 3] = net["net_name"].map(wm)
    return out


def verify_y(vname, data, blocks, labels, sample_n=10):
    """blocks: list of (type_name, df, start, end). labels: expected_label_series."""
    y = data.y
    slot_of = {"gate": [(0, 1), (1, 2)], "pin": [(2, 3)], "net": [(3, 4)]}
    for tname, df, s, e in blocks:
        for order, col in slot_of.get(tname, []):
            exp = labels[tname, order].reset_index(drop=True)
            got = y[s:e, 1 + order]
            exp_nn = int(exp.notna().sum())
            got_nn = int((~torch.isnan(got)).sum())
            check(f"{vname}.y{1+order}[{tname}] non-NaN count",
                  exp_nn == got_nn, f"expected {exp_nn} got {got_nn}")
            idx = exp.dropna().index[:sample_n]
            # NaN-safe: a tensor slot that is NaN where the CSV has a value is a
            # dropped label (join loss at the graph stage). abs(NaN - x) > tol is
            # ALWAYS False, so the old check silently passed it — flag isnan
            # explicitly. (BUG-1, verifier-silent-lies-audit-2026-07-07.md.)
            bad = sum(1 for i in idx
                      if math.isnan(float(got[int(i)]))
                      or abs(float(got[int(i)]) - float(exp[i])) > 1e-4)
            if len(idx):
                check(f"{vname}.y{1+order}[{tname}] sampled values", bad == 0,
                      f"{bad}/{len(idx)} mismatched")


def verify_case(case, design=None, json_out=None):
    case = case.rstrip("/")
    feat, labs, ds = case + "/features", case + "/labels", case + "/dataset"
    man = json.load(open(ds + "/graph_manifest.json"))
    design = design or man["design"]
    print(f"== {design} ({case}) ==")

    views = build_views(feat, design)
    gate, net, iopin, pin, egp, epn, ein = views
    ng, nn, ni, npn = len(gate), len(net), len(iopin), len(pin)

    check("manifest.status ok", man["status"] == "ok", man["status"])
    lh = man.get("label_health", {})
    check("manifest.label_health all ok",
          lh and all(v["status"] == "ok" for v in lh.values()),
          {k: v["status"] for k, v in lh.items()})

    labels = expected_label_series(views, labs, design)

    # per-net endpoint sets (for clique formulas)
    pin_idx_keys = set(map(tuple, pin[["inst_name", "pin_name"]].itertuples(index=False, name=None)))
    net_pins = epn[["inst_name", "pin_name", "net_name"]].drop_duplicates()
    net_pin_sets = net_pins.groupby("net_name").apply(
        lambda d: {(i, p) for i, p in zip(d["inst_name"], d["pin_name"]) if (i, p) in pin_idx_keys},
        include_groups=False)
    net_io_sets = ein.groupby("net_name")["iopin_name"].apply(set)
    gate_set = set(gate["inst_name"])

    def net_k(fn_pin, fn_io):
        tot = 0
        for n in net["net_name"]:
            eps = set()
            for ip in net_pin_sets.get(n, set()):
                v = fn_pin(ip)
                if v is not None:
                    eps.add(v)
            for io in net_io_sets.get(n, set()):
                v = fn_io(io)
                if v is not None:
                    eps.add(v)
            tot += c2(len(eps))
        return tot

    # ---- variant b ----
    b = torch.load(ds + "/b_graph.pt", weights_only=False)
    ntb = b.x[:, 0].long()
    check("b node counts",
          [int((ntb == t).sum()) for t in (0, 1, 2, 3)] == [ng, nn, ni, npn],
          f"got {[int((ntb == t).sum()) for t in (0,1,2,3)]} want {[ng,nn,ni,npn]}")
    exp_b = 2 * (len(egp[["inst_name", "pin_name"]].drop_duplicates())
                 + len(net_pins) + len(ein[["iopin_name", "net_name"]].drop_duplicates()))
    check("b edge count", b.edge_index.shape[1] == exp_b,
          f"got {b.edge_index.shape[1]} want {exp_b}")
    check("b x1 uniform graph_id", bool((b.x[:, 1] == b.x[0, 1]).all()))
    check("b y0 == node_type", bool((b.y[:, 0] == b.x[:, 0]).all()))
    names = b.node_name
    ok_names = (names[:ng] == gate["inst_name"].tolist()
                and names[ng:ng + nn] == net["net_name"].tolist()
                and names[ng + nn:ng + nn + ni] == iopin["iopin_name"].tolist())
    check("b node_name block order", ok_names)
    verify_y("b", b, [("gate", gate, 0, ng), ("net", net, ng, ng + nn),
                      ("pin", pin, ng + nn + ni, ng + nn + ni + npn)], labels)

    # ---- variant c ----
    c = torch.load(ds + "/c_graph.pt", weights_only=False)
    ntc = c.x[:, 0].long()
    check("c node counts",
          [int((ntc == t).sum()) for t in (0, 1, 2)] == [ng, nn, ni])
    kept_pin_rows = net_pins[net_pins["inst_name"].isin(gate_set)]
    exp_c = 2 * (len(kept_pin_rows) + len(ein[["iopin_name", "net_name"]].drop_duplicates()))
    check("c edge count", c.edge_index.shape[1] == exp_c,
          f"got {c.edge_index.shape[1]} want {exp_c}")
    # c edge_attr alignment on unambiguous (gate, net) pairs
    pin_feat = pin.set_index(["inst_name", "pin_name"])[PIN_SCHEMA]
    cnames = c.node_name
    uniq = net_pins.groupby(["inst_name", "net_name"]).size()
    uniq = set(uniq[uniq == 1].index)
    checked = bad = 0
    for k in range(c.edge_index.shape[1]):
        if checked >= 400:
            break
        if int(c.edge_type[k]) != 0:
            continue
        u, v = int(c.edge_index[0, k]), int(c.edge_index[1, k])
        gn, nn_ = (cnames[u], cnames[v]) if int(ntc[u]) == 0 else (cnames[v], cnames[u])
        if (gn, nn_) not in uniq:
            continue
        row = net_pins[(net_pins["inst_name"] == gn) & (net_pins["net_name"] == nn_)]
        exp0, exp1 = pin_feat.loc[(row.iloc[0]["inst_name"], row.iloc[0]["pin_name"])]
        checked += 1
        if abs(float(c.edge_attr[k, 0]) - exp0) > 1e-4 or abs(float(c.edge_attr[k, 1]) - exp1) > 1e-3:
            bad += 1
    check("c edge_attr == folded pin features", bad == 0 and checked > 0,
          f"{bad}/{checked} mismatched")
    verify_y("c", c, [("gate", gate, 0, ng), ("net", net, ng, ng + nn)], labels)

    # ---- variant d ----
    d = torch.load(ds + "/d_graph.pt", weights_only=False)
    ntd = d.x[:, 0].long()
    check("d node counts",
          [int((ntd == t).sum()) for t in (0, 2, 3)] == [ng, ni, npn])
    exp_d = 2 * (len(egp[["inst_name", "pin_name"]].drop_duplicates())
                 + net_k(lambda ip: ip, lambda io: ("io", io)))
    check("d edge count (clique formula)", d.edge_index.shape[1] == exp_d,
          f"got {d.edge_index.shape[1]} want {exp_d}")
    verify_y("d", d, [("gate", gate, 0, ng),
                      ("pin", pin, ng + ni, ng + ni + npn)], labels)

    # ---- variant e ----
    e = torch.load(ds + "/e_graph.pt", weights_only=False)
    nte = e.x[:, 0].long()
    check("e node counts",
          [int((nte == t).sum()) for t in (2, 3)] == [ni, npn])
    gate_pin_counts = egp[["inst_name", "pin_name"]].drop_duplicates().groupby(
        egp[["inst_name", "pin_name"]].drop_duplicates()["inst_name"]).size()
    exp_e = 2 * (int(sum(c2(int(k)) for k in gate_pin_counts))
                 + net_k(lambda ip: ip, lambda io: ("io", io)))
    check("e edge count (clique formula)", e.edge_index.shape[1] == exp_e,
          f"got {e.edge_index.shape[1]} want {exp_e}")
    verify_y("e", e, [("pin", pin, ni, ni + npn)], labels)

    # ---- variant f ----
    f = torch.load(ds + "/f_graph.pt", weights_only=False)
    ntf = f.x[:, 0].long()
    check("f node counts",
          [int((ntf == t).sum()) for t in (0, 2)] == [ng, ni])
    exp_f = 2 * net_k(lambda ip: ip[0] if ip[0] in gate_set else None,
                      lambda io: ("io", io))
    check("f edge count (clique formula)", f.edge_index.shape[1] == exp_f,
          f"got {f.edge_index.shape[1]} want {exp_f}")
    # f edge_attr: for sampled edges whose endpoints share exactly one net,
    # edge_attr must equal that net's NET_SCHEMA features
    fnames = f.node_name
    nets_of = {}
    for n in net["net_name"]:
        members = {ip[0] for ip in net_pin_sets.get(n, set()) if ip[0] in gate_set} \
                  | set(net_io_sets.get(n, set()))
        for mname in members:
            nets_of.setdefault(mname, set()).add(n)
    net_feat = net.set_index("net_name")[NET_SCHEMA]
    checked = bad = 0
    for k in range(0, f.edge_index.shape[1], max(1, f.edge_index.shape[1] // 300)):
        u, v = int(f.edge_index[0, k]), int(f.edge_index[1, k])
        shared = nets_of.get(fnames[u], set()) & nets_of.get(fnames[v], set())
        if len(shared) != 1:
            continue
        expv = net_feat.loc[next(iter(shared))].to_numpy(dtype=float)
        gotv = f.edge_attr[k, :len(NET_SCHEMA)].numpy()
        checked += 1
        if any(abs(a - b) > max(1e-3, 1e-3 * abs(a)) for a, b in zip(expv, gotv)):
            bad += 1
    check("f edge_attr == connecting net features", bad == 0 and checked > 0,
          f"{bad}/{checked} mismatched")
    verify_y("f", f, [("gate", gate, 0, ng)], labels)

    # ---- manifest stats vs tensors ----
    for vname, data in (("b", b), ("c", c), ("d", d), ("e", e), ("f", f)):
        st = man["variants"][vname]
        check(f"manifest[{vname}] nodes/edges match tensors",
              st["nodes"] == data.x.shape[0] and st["edges"] == data.edge_index.shape[1])

    # ---- RC parasitic labels (independent SPEF re-derivation) ----
    rc = read_spef_truth(case)
    rc_h = man.get("rc_health", {})
    check("manifest has rc_health", bool(rc_h), rc_h)
    for vname, data in (("b", b), ("c", c), ("d", d), ("e", e), ("f", f)):
        ok = (hasattr(data, "rc_edge_index") and hasattr(data, "rc_edge_type")
              and hasattr(data, "rc_edge_y") and data.rc_edge_y.shape[1] == 3
              and data.rc_edge_index.shape[1] == data.rc_edge_type.numel()
              == data.rc_edge_y.shape[0])
        check(f"{vname} rc_edge_* present + consistent shapes", ok)
        check(f"{vname} manifest rc_edges == tensor",
              man["variants"][vname].get("rc_edges") == int(data.rc_edge_index.shape[1]))
    net_name_set = set(net["net_name"].tolist())
    # name -> set(nets) for pin/iopin membership (resistance intra-net + d broadcast)
    memnet = {}
    for n in net["net_name"]:
        for (ii, pp) in net_pin_sets.get(n, set()):
            memnet.setdefault(f"{ii}/{pp}", set()).add(n)
        for io in net_io_sets.get(n, set()):
            memnet.setdefault(io, set()).add(n)
    if not rc.get("present"):
        check("rc: no SPEF -> rc_health=no_rc_labels + all rc edges empty",
              rc_h.get("status") == "no_rc_labels"
              and all(int(man["variants"][v].get("rc_edges", 0)) == 0 for v in "bcdef"))
    else:
        gt, ct = rc["ground"], rc["coupling"]
        bnames, ntb2 = b.node_name, b.x[:, 0].long()
        # (A0) SPEF<->DEF de-escape join floor. The oracle's `gt` is keyed by the SAME
        # de-escape the extractor uses, so a two-sided escaping-join regression (bug #20
        # dropped "every hierarchical net and double-bus register") would key both sides
        # wrong and check (A) would simply `continue` past every dropped net. Escape-
        # SENSITIVE DEF nets (names carrying '.', '$', or escaped brackets) are exactly
        # the subset that regression drops to ~0% join while flat nets stay fine — assert
        # a high join rate for that subset. Skips (no false-fail) on flattened designs
        # with too few such nets. (BUG-4, verifier-silent-lies-audit-2026-07-07.md.)
        esc = [nm for nm in net["net_name"].tolist()
               if "." in nm or "$" in nm or "\\[" in nm or "\\]" in nm]
        if len(esc) >= 20:
            em = sum(1 for nm in esc if nm in gt)
            check("rc: SPEF de-escape join rate on escape-sensitive nets >= 0.8",
                  em / len(esc) >= 0.8, f"{em}/{len(esc)} joined ({em / len(esc):.1%})")
        # (A) ground cap on b net nodes: y5 == log1p(SPEF ground)
        bad = chk = 0
        for i, nm in enumerate(net["net_name"].tolist()):
            if nm not in gt:
                continue
            got, exp = float(b.y[ng + i, 5]), math.log1p(gt[nm])
            chk += 1
            if math.isnan(got) or abs(got - exp) > 1e-4:
                bad += 1
            if chk >= 400:
                break
        check("b ground cap y5 == log1p(SPEF ground) on net nodes", bad == 0 and chk > 0,
              f"{bad}/{chk} mismatched")
        # (B) coupling edge count == cross-net pairs among signal nets (x2 directed)
        exp_coup = 2 * sum(1 for (a, bb) in ct if a in net_name_set and bb in net_name_set)
        got_coup = int((b.rc_edge_type == 0).sum())
        check("b coupling edge count == SPEF cross-net signal pairs",
              got_coup == exp_coup, f"got {got_coup} want {exp_coup}")
        # (C) sampled coupling edges: cross-net net-node pair + label == log1p(SPEF)
        ci = (b.rc_edge_type == 0).nonzero().view(-1).tolist()
        bad = chk = 0
        for k in ci[::max(1, len(ci) // 200)]:
            u, v = int(b.rc_edge_index[0, k]), int(b.rc_edge_index[1, k])
            chk += 1
            if int(ntb2[u]) != 1 or int(ntb2[v]) != 1 or u == v:
                bad += 1; continue
            a, bb = bnames[u], bnames[v]
            key = (a, bb) if a < bb else (bb, a)
            if key in ct and abs(float(b.rc_edge_y[k, 1]) - math.log1p(ct[key])) > 1e-4:
                bad += 1
        check("b coupling edges cross-net + label==log1p(SPEF coupling)", bad == 0 and chk > 0,
              f"{bad}/{chk}")
        # (D) resistance edges intra-net (endpoints share a net) + positive label
        ri = (b.rc_edge_type == 1).nonzero().view(-1).tolist()
        bad = chk = 0
        for k in ri[::max(1, len(ri) // 300)]:
            u, v = int(b.rc_edge_index[0, k]), int(b.rc_edge_index[1, k])
            chk += 1
            if not (memnet.get(bnames[u], set()) & memnet.get(bnames[v], set())):
                bad += 1
            lab = float(b.rc_edge_y[k, 2])
            if math.isnan(lab) or lab < 0:
                bad += 1
        check("b resistance edges intra-net + non-negative label", bad == 0 and chk > 0,
              f"{bad}/{chk}")
        # (E) rc_edge_y type/column separation (coupling->col1, resistance->col2)
        cm, rm = (b.rc_edge_type == 0), (b.rc_edge_type == 1)
        check("b rc_edge_y type/col separation",
              bool(torch.isnan(b.rc_edge_y[cm, 2]).all())
              and bool(torch.isnan(b.rc_edge_y[rm, 1]).all()))
        # (F) d ground cap broadcast to pin nodes == owning net's ground cap
        ntd2, dnames = d.x[:, 0].long(), d.node_name
        pin_pos = (ntd2 == 3).nonzero().view(-1).tolist()
        bad = chk = 0
        for idx in pin_pos[::max(1, len(pin_pos) // 300)]:
            nets = memnet.get(dnames[idx], set())
            if len(nets) != 1:
                continue
            n = next(iter(nets))
            if n not in gt:
                continue
            chk += 1
            if abs(float(d.y[idx, 5]) - math.log1p(gt[n])) > 1e-4:
                bad += 1
        check("d ground cap y5 broadcast to pins == net ground cap", bad == 0 and chk > 0,
              f"{bad}/{chk}")

    # ---- global_feat vs metadata ----
    md = pd.read_csv(feat + "/metadata.csv")
    md = md[md["graph_id"].astype(str) == design] if "graph_id" in md.columns else md
    if hasattr(b, "global_feat") and len(md):
        row = md.iloc[0]
        exp = [float(0 if pd.isna(pd.to_numeric(row.get(k), errors="coerce"))
                     else pd.to_numeric(row.get(k), errors="coerce")) for k in METADATA_SCHEMA]
        got = [float(x) for x in b.global_feat]
        check("global_feat == metadata row",
              all(abs(a - g) <= max(1e-6, 1e-6 * abs(a)) for a, g in zip(exp, got)))

    # ---- netlist graph ----
    npt = ds + "/netlist_graph.pt"
    if os.path.isfile(npt):
        g = torch.load(npt, weights_only=False)
        runs = sorted(
            (r for r in os.listdir(case + "/backend") if r.startswith("RUN_")), reverse=True)
        yos = None
        for r in runs:
            p = f"{case}/backend/{r}/results/1_2_yosys.v"
            if os.path.isfile(p):
                yos = p
                break
        if yos:
            # Platform-generic independent instance count: any statement-leading
            # identifier that isn't a Verilog keyword and is followed by an
            # instance name + '(' is a cell instantiation in a yosys structural
            # netlist. (The old regex hardcoded the sky130 master prefix and
            # counted 0 on every other platform — 2026-07-06 nangate45 round.)
            text = re.sub(r"//.*", "", open(yos).read())
            text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
            inst = [m for m in re.findall(
                r"^\s*([A-Za-z_\\][\w$\\\[\]]*)\s+(\\?[^\s()]+)\s*\(", text, re.M)
                if m[0] not in _VERILOG_KEYWORDS]
            check("netlist_graph cell count vs independent regex",
                  len(g.cell_names) == len(inst),
                  f"pt {len(g.cell_names)} regex {len(inst)}")
            check("netlist_graph bipartite symmetric",
                  g.edge_index.shape[1] % 2 == 0 and g.x.shape[0]
                  == len(g.cell_names) + len(g.net_names))
            # sampled connectivity: an instance's .port(net) connections in the
            # yosys netlist must appear as cell<->net edges in the tensor
            def _norm(n):
                return n.lstrip("\\").strip()
            cell_idx = {_norm(n): i for i, n in enumerate(g.cell_names)}
            net_idx = {_norm(n): i + len(g.cell_names)
                       for i, n in enumerate(g.net_names)}
            edges = set(zip(g.edge_index[0].tolist(), g.edge_index[1].tolist()))
            stmts = re.findall(
                r"^\s*([A-Za-z_][\w$]*)\s+(\\?[^\s()]+)\s*\((.*?)\)\s*;",
                text, re.M | re.S)
            miss = checked_conn = 0
            for master, iname, body in stmts[:40]:
                if master in _VERILOG_KEYWORDS or _norm(iname) not in cell_idx:
                    continue
                ci = cell_idx[_norm(iname)]
                for pnet in re.findall(r"\.\s*[\w$]+\s*\(\s*([^){},]+?)\s*\)", body):
                    pn = _norm(pnet)
                    if not pn or pn.startswith("1'") or pn not in net_idx:
                        continue
                    checked_conn += 1
                    if (ci, net_idx[pn]) not in edges and (net_idx[pn], ci) not in edges:
                        miss += 1
            check("netlist_graph sampled port connectivity",
                  checked_conn > 0 and miss == 0,
                  f"{miss}/{checked_conn} missing edges")

    # ---- value sanity ----
    p50 = pin["sum_pin_cap_fF"].median() if npn else 0
    check("sum_pin_cap_fF p50 in physical range (0.3..100 fF)",
          npn == 0 or 0.3 <= p50 <= 100, f"p50={p50:.3f} fF")
    check("hpwl_um >= 0", bool((net["hpwl_um"].astype(float) >= 0).all()))
    check("num_drivers >= 1 on all signal nets",
          bool((net["num_drivers"].astype(int) >= 1).all()))

    # ---- wide-coverage extension: X/Y values vs independently re-parsed
    # liberty/LEF/DEF truth, structural gates, regression probes ----
    extended_checks(case, design, feat, labs, views, b)

    n_fail = sum(1 for r in RESULTS if not r["ok"])
    print(f"== {design}: {len(RESULTS) - n_fail}/{len(RESULTS)} checks passed ==")
    if json_out:
        with open(json_out, "w") as fh:
            json.dump({"design": design, "results": RESULTS,
                       "passed": len(RESULTS) - n_fail, "failed": n_fail}, fh, indent=1)
    return n_fail


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("case_dir", nargs="?", default=None)
    ap.add_argument("--design", default=None)
    ap.add_argument("--json", default=None)
    ap.add_argument("--batch", default=None, metavar="ROOT",
                    help="verify every ROOT subdir containing dataset/graph_manifest.json")
    args = ap.parse_args()

    if args.batch:
        root = args.batch.rstrip("/")
        cases = sorted(
            os.path.join(root, d) for d in os.listdir(root)
            if os.path.isfile(os.path.join(root, d, "dataset", "graph_manifest.json")))
        if not cases:
            print(f"no cases with dataset/graph_manifest.json under {root}")
            sys.exit(1)
        summary, total_fail = [], 0
        for case in cases:
            RESULTS.clear()
            try:
                nf = verify_case(case)
            except Exception as e:  # a verifier crash is a FAIL, never a skip
                RESULTS.append({"check": "verifier completed", "ok": False,
                                "detail": repr(e)[:300]})
                print(f"== {case}: VERIFIER ERROR {e!r}")
                nf = 1
            summary.append({"case": case,
                            "passed": sum(1 for r in RESULTS if r["ok"]),
                            "failed": nf, "results": list(RESULTS)})
            total_fail += nf
        print("\n== batch summary ==")
        for s in summary:
            tag = "PASS" if not s["failed"] else "FAIL"
            print(f"  [{tag}] {os.path.basename(s['case'])}: "
                  f"{s['passed']}/{s['passed'] + s['failed']}")
        if args.json:
            with open(args.json, "w") as fh:
                json.dump({"cases": summary,
                           "total_failed": total_fail}, fh, indent=1)
        sys.exit(1 if total_fail else 0)

    if not args.case_dir:
        ap.error("case_dir or --batch required")
    n_fail = verify_case(args.case_dir, args.design, args.json)
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()
