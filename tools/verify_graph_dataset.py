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

try:  # torch_geometric is needed to torch.load the .pt anyway; guard defensively
    from torch_geometric.data import Data as _PygData, HeteroData as _PygHetero
except Exception:  # noqa: BLE001
    _PygData = _PygHetero = None

# Hetero vocab — INTENTIONALLY re-declared here (independent of graph_lib) so that
# a bug in graph_lib.homo_to_hetero surfaces when this verifier's own hetero->homo
# reconstruction feeds the full homogeneous check surface (2026-07-16 hetero default).
_HNAME2ID = {"gate": 0, "net": 1, "iopin": 2, "pin": 3}
_RC_RELS = {"rc_coupling": 0, "rc_resistance": 1}

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
SKIPPED = []
SIGNOFF_RECHECK = False   # set by --signoff-recheck: enable OpenROAD PDNSim re-run


def check(name, ok, detail=""):
    RESULTS.append({"check": name, "ok": bool(ok), "detail": str(detail)[:300]})
    tag = "PASS" if ok else "FAIL"
    print(f"  [{tag}] {name}" + (f" — {detail}" if (detail and not ok) else ""))
    return ok


def skip(name, reason=""):
    """Record an honestly-skipped check (an unavailable dependency), NOT a pass.
    Never counted toward passed/failed — a skip that masqueraded as a pass would
    be the very silent lie this verifier exists to prevent."""
    SKIPPED.append({"check": name, "reason": str(reason)[:300]})
    print(f"  [SKIP] {name}" + (f" — {reason}" if reason else ""))


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
                    m = re.search(r"\+\s+(PLACED|FIXED)\s+\(\s*(-?\d+)\s+(-?\d+)\s*\)\s+(\w+)", s)
                    comps[t[1]] = {"master": t[2],
                                   "status": m.group(1) if m else None,
                                   "x": int(m.group(2)) if m else None,
                                   "y": int(m.group(3)) if m else None,
                                   "orient": m.group(4) if m else None}
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


def _platform_provenance(case_dir):
    """The platform this case's DATASET was actually built on (failure-patterns.md #30).

    constraints/config.mk is MUTABLE round state: a campaign re-point
    (setup_rtl_designs.py --platform X --force) rewrites it for the WHOLE corpus,
    including designs whose backend/dataset were built on the PRIOR platform.
    cell_type_id and every *_type_id vocabulary are per-platform, so resolving
    libs from the re-pointed config.mk would verify a dataset against another
    platform's ground truth — wrong either way it lands (false FAIL, or a subtler
    vacuous pass). Authority order:
      1. dataset/graph_manifest.json "platform"  (stamped at build by build_graphs.py)
      2. newest backend/RUN_*/run-meta.json "platform"  (the DEF's build record)
      3. config.mk PLATFORM  (fresh case / explicit-override reference builds)
    """
    man_p = os.path.join(case_dir, "dataset", "graph_manifest.json")
    if os.path.isfile(man_p):
        try:
            p = json.load(open(man_p)).get("platform")
            if p:
                return p
        except Exception:
            pass
    bdir = os.path.join(case_dir, "backend")
    runs = sorted((r for r in os.listdir(bdir) if r.startswith("RUN_")),
                  reverse=True) if os.path.isdir(bdir) else []
    for r in runs:
        mp = os.path.join(bdir, r, "run-meta.json")
        if os.path.isfile(mp):
            try:
                p = json.load(open(mp)).get("platform")
                if p:
                    return p
            except Exception:
                pass
    cfg = os.path.join(case_dir, "constraints", "config.mk")
    if os.path.isfile(cfg):
        m = re.search(r"^\s*(?:export\s+)?PLATFORM\s*=\s*(\S+)", open(cfg).read(), re.M)
        if m:
            return m.group(1)
    return ""


def resolve_platform_files(case_dir):
    """Platform file PATHS via the production resolver (values re-derived here).
    The platform itself comes from build provenance, NOT the mutable config.mk
    (_platform_provenance, failure-patterns.md #30); the resolver receives it as
    a make command-line var, which overrides config.mk's export inside ORFS."""
    import subprocess
    cfg = os.path.join(case_dir, "constraints", "config.mk")
    resolver = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                            "r2g-skills/def-graph", "scripts", "flow", "resolve_platform_paths.sh")
    platform = _platform_provenance(case_dir)
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


def _v_apply_orient(px, py, orient, w, h):
    """Independent (non-techlib) re-implementation of the DEF-orientation pin
    transform, so the HPWL recompute reproduces the extractor's pin-center HPWL
    without sharing its code."""
    o = (orient or "N").upper()
    # FN=MY (reflect X), FS=MX (reflect Y) — validated against OpenDB placed pins.
    # Independent of techlib.lef.apply_orient (the firewall) but the SAME map.
    return {
        "N": (px, py), "S": (w - px, h - py), "W": (h - py, px), "E": (py, w - px),
        "FN": (w - px, py), "FS": (px, h - py), "FW": (py, px), "FE": (h - py, w - px),
    }.get(o, (px, py))


def _lef_pin_geometry(lef_paths):
    """``{MASTER_UPPER: {"w","h","pins":{PIN_UPPER:(cx,cy)}}}`` — an INDEPENDENT
    parse of MACRO SIZE + per-PIN RECT/POLYGON bbox centers (um), used to
    reproduce the extractor's pin-center HPWL. Separate code from techlib.lef so
    a shared parse bug can't hide (the verifier's firewall principle)."""
    geom = {}
    for lef in lef_paths:
        if not lef or not os.path.isfile(lef):
            continue
        cur = pin = None
        xs, ys = [], []

        def flush():
            if cur is not None and pin is not None and xs:
                geom[cur]["pins"][pin] = ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0)

        for line in open(lef, errors="ignore"):
            s = line.strip()
            tok = s.replace(";", " ").split()
            if not tok:
                continue
            if tok[0] == "MACRO" and len(tok) >= 2:
                flush(); cur = tok[1].lstrip("\\").upper()
                geom[cur] = {"w": 0.0, "h": 0.0, "pins": {}}
                pin, xs, ys = None, [], []
            elif cur is None:
                continue
            elif tok[0] == "SIZE":
                try:
                    by = tok.index("BY")
                    geom[cur]["w"], geom[cur]["h"] = float(tok[by - 1]), float(tok[by + 1])
                except (ValueError, IndexError):
                    pass
            elif tok[0] == "PIN" and len(tok) >= 2:
                flush(); pin = tok[1].lstrip("\\").upper(); xs, ys = [], []
            elif pin is not None and tok[0] == "RECT":
                nums = [float(x) for x in tok[1:] if _isfloat(x)]
                if len(nums) >= 4:
                    x1, y1, x2, y2 = nums[-4:]
                    xs += [x1, x2]; ys += [y1, y2]
            elif pin is not None and tok[0] == "POLYGON":
                nums = [float(x) for x in tok[1:] if _isfloat(x)]
                if len(nums) % 2:      # odd -> leading MASK id
                    nums = nums[1:]
                for k in range(0, len(nums) - 1, 2):
                    xs.append(nums[k]); ys.append(nums[k + 1])
            elif tok[0] == "END" and len(tok) >= 2:
                key = tok[1].lstrip("\\").upper()
                if pin is not None and key == pin:
                    flush(); pin, xs, ys = None, [], []
                elif key == cur:
                    flush(); cur, pin, xs, ys = None, None, [], []
    return geom


def _isfloat(s):
    try:
        float(s)
        return True
    except ValueError:
        return False


def _v_pin_abs(geom, x_um, y_um, orient, master, pin):
    """Absolute pin position (um): instance origin + oriented in-cell offset;
    falls back to the instance origin when geometry is absent/unknown."""
    if geom:
        mac = geom.get((master or "").lstrip("\\").upper())
        if mac:
            c = mac["pins"].get((pin or "").lstrip("\\").upper())
            if c:
                px, py = _v_apply_orient(c[0], c[1], orient, mac["w"], mac["h"])
                return (x_um + px, y_um + py)
    return (x_um, y_um)


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


def _find_spef(case):
    """The SPEF the dataset was actually built from: the R2G_SPEF override first
    (so an override / no-backend verification project matches how run_labels /
    run_features located it), then the backend/rcx discovery. '' when none exists.
    Without honoring R2G_SPEF the RC checks read the SPEF from a place an override
    build never wrote, and a legitimately RC-populated override dataset false-FAILs
    "no SPEF -> rc_health=no_rc_labels" (2026-07-08 verifier override-support fix)."""
    env = os.environ.get("R2G_SPEF") or ""
    if os.path.isfile(env):
        return env
    cands = sorted(glob.glob(case + "/backend/RUN_*/rcx/6_final.spef")
                   + glob.glob(case + "/backend/RUN_*/results/6_final.spef"))
    if os.path.isfile(case + "/rcx/6_final.spef"):
        cands.append(case + "/rcx/6_final.spef")
    return cands[-1] if cands else ""


def read_spef_truth(case):
    """Independent SPEF re-derivation (SEPARATE code from techlib.spef): per-net
    ground cap (fF) and per-cross-net-pair coupling cap (fF), names de-escaped to
    DEF convention. Returns {"present": False} when no SPEF (RC labels absent)."""
    spef = _find_spef(case)
    if not spef:
        return {"present": False}
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
    # Independent LEF pin geometry -> reproduce the extractor's pin-center HPWL.
    # Resolve the SAME cell LEFs as techlib.lef.cell_lef_paths (whitespace-split
    # each var, honor CELL_LEFS) so a multi-path SC_LEF can't false-fail the HPWL
    # check. Empty -> both extractor and verifier fall back to the instance origin.
    pin_geom = _lef_pin_geometry(plat.get("SC_LEF", "").split()
                                 + plat.get("CELL_LEFS", "").split()
                                 + plat.get("ADDITIONAL_LEFS", "").split())
    backend_dir = case + "/backend"
    runs = sorted((r for r in os.listdir(backend_dir) if r.startswith("RUN_")),
                  reverse=True) if os.path.isdir(backend_dir) else []
    # Honor the same R2G_DEF override the build (run_features/run_labels) used, so an
    # override / no-backend verification project (a nangate45 reference-DEF build,
    # Step 5c) still gets the independent raw DEF re-parse rather than a bare
    # os.listdir crash on a missing backend/ dir (2026-07-08; the isdir guard was
    # already applied at the other two os.listdir sites but missed here).
    def_path = os.environ.get("R2G_DEF") or None
    if def_path and not os.path.isfile(def_path):
        def_path = None
    for r in runs:
        if def_path:
            break
        for sub in ("final", "results"):
            p = f"{case}/backend/{r}/{sub}/6_final.def"
            if os.path.isfile(p):
                def_path = p
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
                pts.append(_v_pin_abs(pin_geom, comp["x"] / dt["dbu"], comp["y"] / dt["dbu"],
                                      comp.get("orient"), master, p))
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
                     ("hpwl", "hpwl == recomputed pin-center HPWL (LEF geometry; cell-origin fallback)"),
                     ("macro", "connects_macro_flag == DEF∩LEF-BLOCK truth")):
        check(f"ext.net {label}", checked_net > 0 and bad[k] == 0,
              f"{bad[k]}/{checked_net} mismatched")

    # No-fill honesty (2026-07-14): the extractor no longer fabricates a driver, so
    # EVERY net the CSV marks num_drivers==0 must independently recompute to 0 (a
    # genuinely undriven / parse-miss net) — not a net whose real driver we failed to
    # count. This targets the 0-driver nets specifically, covering those beyond the
    # 200-net sample cap above.
    bad_zero = chk_zero = 0
    for net_name in full_net[full_net["num_drivers"].astype(int) == 0]["net_name"].tolist()[:400]:
        conns = dt["nets"].get(net_name)
        if conns is None:
            continue
        drv = 0
        for i, p in conns:
            if i == "PIN":
                drv += dt["pins"].get(p, {}).get("dir", "") == "INPUT"
            else:
                comp = dt["comps"].get(i)
                if comp:
                    drv += lib_pin_truth(lib, comp["master"], p)[0] == "OUTPUT"
        chk_zero += 1
        if drv != 0:
            bad_zero += 1
    check("ext.net num_drivers==0 nets genuinely have no driver (no-fill honesty)",
          bad_zero == 0, f"{bad_zero}/{chk_zero} CSV-zero nets actually have a driver")

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


# ===========================================================================
# COMPREHENSIVE VERIFICATION — GROUP A: TOPOLOGY (all five views b-f)
# ---------------------------------------------------------------------------
# The historical verifier ran symmetry / self-loop / name-uniqueness / block
# ordering on variant b ONLY; c/d/e/f got node+edge counts (folding) but no
# structural verification. Node layout is "block-positional" (a fixed type-block
# order per view, mergesort within each block); every y-slice + name lookup
# assumes that exact order, so a wrong sort key silently misaligns labels with
# NO error. build_directed_edges / build_parasitic_edges lay directed edges out
# INTERLEAVED [fwd0,rev0,fwd1,rev1,...] with repeat_interleave'd attrs — audit
# bug #5 concatenated [all-fwd|all-rev] while still pairwise-repeating attrs,
# misaligning attr/type/y with edge_index for every edge past the first. These
# checks make that guard cover every view. See failure-patterns.md #19 and
# references/graph-dataset.md ("block-positional", "interleaved").
# ===========================================================================

# Per-view block order (node-type id per block, in positional order). Must match
# build_graphs.py: b gate/net/iopin/pin; c gate/net/iopin; d gate/iopin/pin;
# e iopin/pin; f gate/iopin. Type ids: 0 gate, 1 net, 2 iopin, 3 pin.
_VIEW_BLOCK_ORDER = {
    "b": [("gate", 0), ("net", 1), ("iopin", 2), ("pin", 3)],
    "c": [("gate", 0), ("net", 1), ("iopin", 2)],
    "d": [("gate", 0), ("iopin", 2), ("pin", 3)],
    "e": [("iopin", 2), ("pin", 3)],
    "f": [("gate", 0), ("iopin", 2)],
}


def _edge_symmetry_stats(edge_index):
    """(num directed pairs whose reverse count differs, num self-loops)."""
    pairs = {}
    ei = edge_index
    for k in range(ei.shape[1]):
        u, v = int(ei[0, k]), int(ei[1, k])
        pairs[(u, v)] = pairs.get((u, v), 0) + 1
    asym = sum(1 for (u, v), n in pairs.items() if pairs.get((v, u), 0) != n)
    loops = sum(n for (u, v), n in pairs.items() if u == v)
    return asym, loops


def _reverse_pairs_bad(ei):
    """Count base edges k where cols 2k,2k+1 are NOT (s,t),(t,s) reverses."""
    bad = 0
    for k in range(0, ei.shape[1] - 1, 2):
        s0, t0 = int(ei[0, k]), int(ei[1, k])
        s1, t1 = int(ei[0, k + 1]), int(ei[1, k + 1])
        if not (s0 == t1 and t0 == s1):
            bad += 1
    return bad


def _paired_rows_bad(t):
    """Count base rows k where rows 2k,2k+1 differ (NaN==NaN treated equal)."""
    a = t if t.dim() > 1 else t.reshape(-1, 1)
    bad = 0
    for k in range(0, a.shape[0] - 1, 2):
        r0, r1 = a[k].float(), a[k + 1].float()
        eq = torch.logical_or(r0 == r1,
                              torch.logical_and(torch.isnan(r0), torch.isnan(r1)))
        if not bool(eq.all()):
            bad += 1
    return bad


def _sample_edge_attr(data, edge_type_id, entity_of, feat_index, width, cap=400):
    """For sampled edges of `edge_type_id` whose two endpoints resolve (via
    `entity_of`: node_name -> entity key) to the SAME entity, compare the first
    `width` edge_attr columns to that entity's feature row (`feat_index.loc`).
    Returns (checked, bad)."""
    names = list(data.node_name)
    et = data.edge_type
    ei = data.edge_index
    E = ei.shape[1]
    step = max(1, E // (cap * 4)) if E else 1
    checked = bad = 0
    for k in range(0, E, step):
        if checked >= cap:
            break
        if int(et[k]) != edge_type_id:
            continue
        u, v = int(ei[0, k]), int(ei[1, k])
        eu, ev = entity_of.get(names[u]), entity_of.get(names[v])
        if eu is None or eu != ev or eu not in feat_index.index:
            continue
        exp = feat_index.loc[eu].to_numpy(dtype=float)
        got = data.edge_attr[k, :width].numpy()
        checked += 1
        if any(abs(a - b) > max(1e-3, 1e-3 * abs(a)) for a, b in zip(exp, got)):
            bad += 1
    return checked, bad


# ===========================================================================
# HETERO SUPPORT (2026-07-16) — the dataset default graph_kind is HeteroData.
# A HeteroData {v}_graph.pt is reconstructed to the homogeneous Data the historical
# verifier expects (INDEPENDENTLY of graph_lib.hetero_to_homo — a second
# implementation, so a conversion bug fails a homo check), then the raw HeteroData
# is additionally checked structurally. hetero_to_homo does NOT preserve the homo
# [fwd0,rev0,...] edge ORDER (edges are regrouped by relation), so topology_checks
# swaps the homo-layout interleaving guard for a hetero-native alignment+symmetry
# guard; every other homo check keys on node position + per-edge endpoints and is
# order-independent. See references/graph-dataset.md ("Heterogeneous graphs").
# ===========================================================================


def _hetero_to_homo(h):
    """Independently reassemble the block-positional homogeneous Data from a
    HeteroData: node stores concatenated in canonical type order gate,net,iopin,
    pin (== the homo block order); node_type re-inserted as x0/y0; edge_type as
    edge_y col0. Edge order is regrouped-by-relation (not preserved)."""
    order = [n for n in ("gate", "net", "iopin", "pin") if n in h.node_types]
    base, off = {}, 0
    xs, ys, yrs, names = [], [], [], []
    have_y = have_yr = True
    for name in order:
        st = h[name]
        n = int(st.x.shape[0])
        base[name] = off
        off += n
        c0 = torch.full((n, 1), float(_HNAME2ID[name]))
        xs.append(torch.cat([c0, st.x.float()], 1))
        if getattr(st, "y", None) is not None:
            ys.append(torch.cat([c0, st.y.float()], 1))
        else:
            have_y = False
        if getattr(st, "y_raw", None) is not None:
            yrs.append(torch.cat([c0, st.y_raw.float()], 1))
        else:
            have_yr = False
        if getattr(st, "node_name", None) is not None:
            names += list(st.node_name)
    d = _PygData(x=torch.cat(xs, 0))
    if have_y:
        d.y = torch.cat(ys, 0)
    if have_yr:
        d.y_raw = torch.cat(yrs, 0)
    if names:
        d.node_name = names

    es, ed, ea, et, ey, eyr = [], [], [], [], [], []
    ha = ht = hy = hyr = False
    rs, rd, rt, ry, ryr = [], [], [], [], []
    hry = hryr = False
    for (s, rel, dd) in h.edge_types:
        st = h[s, rel, dd]
        ei = getattr(st, "edge_index", None)
        if ei is None or ei.shape[1] == 0:
            continue
        n = int(ei.shape[1])
        gs = ei[0] + base[s]
        gd = ei[1] + base[dd]
        if rel in _RC_RELS:
            rct = getattr(st, "rc_edge_type", None)
            if rct is None:
                rct = torch.full((n,), _RC_RELS[rel], dtype=torch.long)
            c0 = rct.view(-1, 1).float()
            rs.append(gs)
            rd.append(gd)
            rt.append(rct)
            r_y = getattr(st, "rc_edge_y", None)
            if r_y is not None:
                hry = True
                ry.append(torch.cat([c0, r_y.float()], 1))
            r_yr = getattr(st, "rc_edge_y_raw", None)
            if r_yr is not None:
                hryr = True
                ryr.append(torch.cat([c0, r_yr.float()], 1))
        else:
            e_t = getattr(st, "edge_type", None)
            c0 = e_t.view(-1, 1).float() if e_t is not None else torch.zeros((n, 1))
            es.append(gs)
            ed.append(gd)
            a = getattr(st, "edge_attr", None)
            if a is not None:
                ha = True
                ea.append(a.float())
            else:
                ea.append(torch.zeros((n, 8)))
            if e_t is not None:
                ht = True
                et.append(e_t)
            else:
                et.append(torch.zeros((n,), dtype=torch.long))
            e_y = getattr(st, "edge_y", None)
            if e_y is not None:
                hy = True
                ey.append(torch.cat([c0, e_y.float()], 1))
            e_yr = getattr(st, "edge_y_raw", None)
            if e_yr is not None:
                hyr = True
                eyr.append(torch.cat([c0, e_yr.float()], 1))
    if es:
        d.edge_index = torch.stack([torch.cat(es), torch.cat(ed)], 0)
        if ha:
            d.edge_attr = torch.cat(ea, 0)
        if ht:
            d.edge_type = torch.cat(et, 0)
        if hy:
            d.edge_y = torch.cat(ey, 0)
        if hyr:
            d.edge_y_raw = torch.cat(eyr, 0)
    else:
        d.edge_index = torch.empty((2, 0), dtype=torch.long)
    if rs:
        d.rc_edge_index = torch.stack([torch.cat(rs), torch.cat(rd)], 0)
        d.rc_edge_type = torch.cat(rt)
        if hry:
            d.rc_edge_y = torch.cat(ry, 0)
        if hryr:
            d.rc_edge_y_raw = torch.cat(ryr, 0)
    else:
        d.rc_edge_index = torch.empty((2, 0), dtype=torch.long)
        d.rc_edge_type = torch.empty((0,), dtype=torch.long)
        d.rc_edge_y = torch.zeros((0, 3))
        d.rc_edge_y_raw = torch.zeros((0, 3))
    for a in ("global_feat", "x_schema", "y_schema", "y_raw_schema",
              "edge_schema", "rc_edge_schema"):
        v = getattr(h, a, None)
        if v is not None:
            setattr(d, a, v)
    return d


def _load_graph(path):
    """Load a {v}_graph.pt -> (homo_Data, hetero_or_None). The hetero default is
    reconstructed to the homogeneous Data the homo verifier consumes; the raw
    HeteroData is returned too for hetero_checks + the hetero-native topology guard."""
    obj = torch.load(path, weights_only=False)
    if _PygHetero is not None and isinstance(obj, _PygHetero):
        return _hetero_to_homo(obj), obj
    return obj, None


def _hetero_store_align_bad(h, rc=False):
    """Count edge stores whose edge_index / edge_attr / edge_type / edge_y row
    counts disagree — the hetero analogue of the homo [fwd,rev] alignment guard
    (homo_to_hetero slices index + attrs with ONE column tensor, so a mismatch is
    a conversion bug)."""
    bad = 0
    for et in h.edge_types:
        is_rc = et[1] in _RC_RELS
        if rc != is_rc:
            continue
        st = h[et]
        n = st.edge_index.shape[1]
        attrs = (("rc_edge_type", "rc_edge_y", "rc_edge_y_raw") if rc
                 else ("edge_attr", "edge_type", "edge_y", "edge_y_raw"))
        for a in attrs:
            t = getattr(st, a, None)
            if t is not None and t.shape[0] != n:
                bad += 1
                break
    return bad


def _hetero_symmetry_bad(h, rc=False):
    """Undirected layout: every relation (a, rel, b) must have its reverse
    (b, rel, a) with an equal edge count."""
    counts = {tuple(et): h[et].edge_index.shape[1] for et in h.edge_types}
    bad = 0
    for (s, rel, d), n in counts.items():
        if rc != (rel in _RC_RELS):
            continue
        if counts.get((d, rel, s)) != n:
            bad += 1
    return bad


def hetero_checks(hetero, views, man):
    """GROUP D — the shipped HeteroData structure (the default graph_kind):
    per-view node types + counts, per-type tensor widths, edge relations over
    present node types only, and manifest graph_kind / hetero breakdown parity."""
    gate, net, iopin, pin, egp, epn, ein = views
    exp_counts = {"gate": len(gate), "net": len(net), "iopin": len(iopin), "pin": len(pin)}
    check("hetero manifest graph_kind == hetero", man.get("graph_kind") == "hetero",
          man.get("graph_kind"))
    for v in ("b", "c", "d", "e", "f"):
        h = hetero[v]
        exp_types = [n for n, _ in _VIEW_BLOCK_ORDER[v]]
        got_types = list(h.node_types)
        check(f"hetero.{v} node types == view blocks",
              sorted(got_types) == sorted(exp_types), f"got {got_types} want {exp_types}")
        for nt in got_types:
            st = h[nt]
            check(f"hetero.{v} {nt} node count", st.x.shape[0] == exp_counts[nt],
                  f"got {st.x.shape[0]} want {exp_counts[nt]}")
            check(f"hetero.{v} {nt} x width==9 (graph_id+8 feats)", st.x.shape[1] == 9,
                  str(tuple(st.x.shape)))
            if hasattr(st, "y"):
                check(f"hetero.{v} {nt} y width==5", st.y.shape[1] == 5, str(tuple(st.y.shape)))
            if hasattr(st, "y_raw"):
                check(f"hetero.{v} {nt} y_raw width==5", st.y_raw.shape[1] == 5)
        bad = [tuple(et) for et in h.edge_types
               if et[0] not in got_types or et[2] not in got_types]
        check(f"hetero.{v} edge relations reference present node types", not bad, str(bad[:3]))
        het_stats = man["variants"][v].get("hetero", {})
        check(f"hetero.{v} manifest node_types match tensor",
              het_stats.get("node_types", {}) == {n: int(h[n].x.shape[0]) for n in got_types})
        check(f"hetero.{v} manifest edge_types match tensor",
              het_stats.get("edge_types", {})
              == {"__".join(et): int(h[et].edge_index.shape[1]) for et in h.edge_types})


def topology_checks(views, tensors, hetero=None):
    """GROUP A — structural correctness of all five views b-f."""
    gate, net, iopin, pin, egp, epn, ein = views
    name_lists = {
        "gate": gate["inst_name"].tolist(),
        "net": net["net_name"].tolist(),
        "iopin": iopin["iopin_name"].tolist(),
        "pin": [f"{i}/{p}" for i, p in zip(pin["inst_name"], pin["pin_name"])],
    }

    # --- symmetry, self-loops, block-positional ordering, uniqueness (all views)
    for v, data in tensors.items():
        asym, loops = _edge_symmetry_stats(data.edge_index)
        check(f"top.{v} edges symmetric (undirected both ways)", asym == 0, f"{asym} asym")
        check(f"top.{v} no self-loops", loops == 0, f"{loops} loops")

        got_names = list(data.node_name)
        exp_names = []
        for tname, _ in _VIEW_BLOCK_ORDER[v]:
            exp_names.extend(name_lists[tname])
        # The single strongest guard that labels align by position: the emitted
        # node_name vector must equal the concatenation of the per-block sorted
        # name lists in the view's block order (pin block included).
        first_diff = next((j for j in range(min(len(got_names), len(exp_names)))
                           if got_names[j] != exp_names[j]), None)
        check(f"top.{v} node_name block-positional order",
              got_names == exp_names,
              f"len got {len(got_names)} exp {len(exp_names)} first_diff@{first_diff}")

        nt = data.x[:, 0].long()
        dup = []
        for tname, tid in _VIEW_BLOCK_ORDER[v]:
            blk = [got_names[i] for i in range(len(got_names)) if int(nt[i]) == tid]
            if len(set(blk)) != len(blk):
                dup.append(tname)
        check(f"top.{v} node_name unique within each type block", not dup, f"dups {dup}")

    if hetero is None:
        # --- HOMO layout: fwd/rev interleaving invariant (c/d/e/f) -------------
        for v in ("c", "d", "e", "f"):
            data = tensors[v]
            ei = data.edge_index
            E = ei.shape[1]
            even = (E % 2 == 0)
            rev_bad = _reverse_pairs_bad(ei) if even else -1
            attr_bad = 0
            raw_tw = (data.edge_y_raw,) if hasattr(data, "edge_y_raw") else ()
            for t in (data.edge_attr, data.edge_type, data.edge_y) + raw_tw:
                attr_bad += _paired_rows_bad(t) if even else 1
            check(f"top.{v} edges interleaved [fwd0,rev0,...] (index+attr+type+y+y_raw aligned)",
                  even and rev_bad == 0 and attr_bad == 0,
                  f"even={even} rev_bad={rev_bad} paired_bad={attr_bad}")
            check(f"top.{v} edge_y0 == edge_type",
                  bool((data.edge_y[:, 0] == data.edge_type.float()).all()))
            # Current schema is edge_y[E,6] with y5 (ground cap) never an edge label.
            # A stale pre-RC dataset (edge_y width 5, no rc tensors) FAILS here loudly
            # instead of IndexError-ing; the `and` short-circuits the [:,5] access.
            check(f"top.{v} edge_y width==6 & edge_y5 all-NaN (ground cap never an edge label)",
                  data.edge_y.shape[1] == 6 and bool(torch.isnan(data.edge_y[:, 5]).all()),
                  f"edge_y width={data.edge_y.shape[1]} (want 6 — stale dataset if 5)")

        # --- rc_edge_* interleaving (build_parasitic_edges, all views) --------
        for v, data in tensors.items():
            if not hasattr(data, "rc_edge_index"):
                check(f"top.{v} rc_edge_* present (current schema)", False,
                      "missing rc_edge_* tensors — stale pre-RC dataset, regenerate")
                continue
            ei = data.rc_edge_index
            E = ei.shape[1]
            even = (E % 2 == 0)
            rev_bad = _reverse_pairs_bad(ei) if even else -1
            pair_bad = 0
            raw_tw = (data.rc_edge_y_raw,) if hasattr(data, "rc_edge_y_raw") else ()
            for t in (data.rc_edge_type, data.rc_edge_y) + raw_tw:
                pair_bad += _paired_rows_bad(t) if even else 1
            check(f"top.{v} rc_edges interleaved [fwd0,rev0,...]",
                  even and rev_bad == 0 and pair_bad == 0,
                  f"even={even} rev_bad={rev_bad} paired_bad={pair_bad}")
    else:
        # --- HETERO layout: the [fwd,rev] interleaving is replaced by relation
        # stores; the equivalent guard is per-store tensor-row alignment (index +
        # attr + type + y sliced by ONE column tensor in homo_to_hetero) and
        # reverse-relation symmetry. Deep undirected symmetry + edge_attr==folded-
        # features are still checked on the reconstructed homo above/below.
        for v in ("c", "d", "e", "f"):
            h = hetero[v]
            check(f"top.{v} hetero edge stores aligned (index/attr/type/y/y_raw rows equal)",
                  _hetero_store_align_bad(h, rc=False) == 0)
            check(f"top.{v} hetero relations symmetric ((a,rel,b)==(b,rel,a) count)",
                  _hetero_symmetry_bad(h, rc=False) == 0)
            wbad = gbad = 0
            for et in h.edge_types:
                if et[1] in _RC_RELS:
                    continue
                ey = getattr(h[et], "edge_y", None)
                if ey is None:
                    continue
                if ey.shape[1] != 5:
                    wbad += 1
                elif not bool(torch.isnan(ey[:, 4]).all()):
                    gbad += 1
            check(f"top.{v} hetero edge_y width==5 & ground-cap col all-NaN",
                  wbad == 0 and gbad == 0, f"width_bad={wbad} gcap_bad={gbad}")
        for v in ("b", "c", "d", "e", "f"):
            h = hetero[v]
            check(f"top.{v} hetero rc stores aligned + symmetric",
                  _hetero_store_align_bad(h, rc=True) == 0
                  and _hetero_symmetry_bad(h, rc=True) == 0)

    # --- edge_attr content for d and e (c and f already covered above) --------
    # Resolvers: pin node "inst/pin" and iopin node -> their (single) signal net;
    # pin node -> its owning gate instance.
    node_net = {}
    for i, p, n in epn[["inst_name", "pin_name", "net_name"]].drop_duplicates().itertuples(index=False):
        node_net[f"{i}/{p}"] = n
    for io, n in ein[["iopin_name", "net_name"]].drop_duplicates().itertuples(index=False):
        node_net[io] = n
    pin_node_gate = {f"{i}/{p}": i for i, p in zip(pin["inst_name"], pin["pin_name"])}
    net_feat = net.set_index("net_name")[NET_SCHEMA]
    gate_feat = gate.set_index("inst_name")[GATE_SCHEMA]

    # d: type 1 = net-clique edges carry NET features; type 0 = gate_pin = zeros.
    d = tensors["d"]
    chk, bad = _sample_edge_attr(d, 1, node_net, net_feat, len(NET_SCHEMA))
    check("top.d net-edge edge_attr == connecting net features", bad == 0 and chk > 0,
          f"{bad}/{chk} mismatched")
    z_chk = z_bad = 0
    for k in range(0, d.edge_index.shape[1], max(1, d.edge_index.shape[1] // 400 or 1)):
        if int(d.edge_type[k]) != 0:
            continue
        z_chk += 1
        if float(d.edge_attr[k].abs().max()) > 1e-9:
            z_bad += 1
    check("top.d gate_pin-edge edge_attr == zeros", z_bad == 0 and z_chk > 0,
          f"{z_bad}/{z_chk} nonzero")

    # e: type 0 = gate-clique edges carry GATE features; type 1 = net = NET feats.
    e = tensors["e"]
    chk, bad = _sample_edge_attr(e, 0, pin_node_gate, gate_feat, len(GATE_SCHEMA))
    check("top.e gate-edge edge_attr == owning gate features", bad == 0 and chk > 0,
          f"{bad}/{chk} mismatched")
    chk, bad = _sample_edge_attr(e, 1, node_net, net_feat, len(NET_SCHEMA))
    check("top.e net-edge edge_attr == connecting net features", bad == 0 and chk > 0,
          f"{bad}/{chk} mismatched")


# ===========================================================================
# COMPREHENSIVE VERIFICATION — GROUP B: FEATURE STATISTICS
# ---------------------------------------------------------------------------
# (1) Re-derive feature columns the historical verifier never checked
#     (placement_status_id, fanout exactly; num_layer + nearest_tap bounded —
#     their exact values, whose worker semantics are quirky, are pinned on the
#     synthetic corner-case fixture instead of risking a false-fail here).
# (2) Independently recompute every per-column distribution summary and confirm
#     features_stats.json / labels_stats.json actually reflect the CURRENT CSVs.
#     Those JSONs are shipped artifacts consumers trust; a stale or hand-edited
#     one is a silent lie that no existing check catches.
# (3) Categorical *_type_id vocabulary coverage + enum-range on the tensors:
#     net nodes are signal-only, no leaked -1 / out-of-vocab ids.
# ===========================================================================

_FEATURE_SUMMARY_COLS = {   # mirrors compute_feature_stats.SUMMARY_COLS
    "nodes_gate": ["cell_area", "cell_power"],
    "nodes_net": ["fanout", "pin_count", "num_layer", "hpwl_um"],
    "nodes_iopin": ["nearest_tap_distance_um"],
    "nodes_pin": ["sum_pin_cap_fF"],
}
_LABEL_SUMMARY = {   # csv -> (label_col, raw_metric_col == raw block key, stats key)
    "cell_congestion.csv": ("label", "cell_congestion", "congestion"),
    "wirelength.csv": ("label", "WireLength_um", "wirelength"),
    "timing_features.csv": ("label", "Path_Delay_ns", "timing"),
    "ir_drop.csv": ("label", "IR_Drop_mV", "irdrop"),
    "net_ground_cap.csv": ("label", "ground_cap_fF", "ground_cap"),
    "coupling_cap.csv": ("label", "coupling_cap_fF", "coupling_cap"),
    "equiv_res.csv": ("label", "equiv_res_ohm", "equiv_res"),
}


def _find_final_def(case):
    runs = sorted((r for r in os.listdir(case + "/backend") if r.startswith("RUN_")),
                  reverse=True) if os.path.isdir(case + "/backend") else []
    for r in runs:
        for sub in ("final", "results"):
            p = f"{case}/backend/{r}/{sub}/6_final.def"
            if os.path.isfile(p):
                return p
    return None


def _pctile(sorted_vals, q):
    """Replicates compute_{label,feature}_stats._percentile (numpy 'linear')."""
    n = len(sorted_vals)
    if n == 0:
        return None
    if n == 1:
        return sorted_vals[0]
    idx = q * (n - 1)
    lo, hi = math.floor(idx), math.ceil(idx)
    if lo == hi:
        return sorted_vals[lo]
    return sorted_vals[lo] * (hi - idx) + sorted_vals[hi] * (idx - lo)


def _numsummary(values):
    import statistics
    vals = sorted(v for v in values if v is not None and v == v)
    if not vals:
        return None
    return {"min": vals[0], "max": vals[-1], "mean": statistics.fmean(vals),
            "p50": _pctile(vals, .50), "p90": _pctile(vals, .90),
            "p95": _pctile(vals, .95), "p99": _pctile(vals, .99)}


def _summ_close(got, exp, rel=1e-6, absol=1e-9):
    if got is None and exp is None:
        return True
    if got is None or exp is None:
        return False
    for k in ("min", "max", "mean", "p50", "p90", "p95", "p99"):
        a, b = got.get(k), exp.get(k)
        if a is None and b is None:
            continue
        if a is None or b is None or abs(a - b) > max(absol, rel * abs(b)):
            return False
    return True


def _read_design_csv(dirpath, fname, design):
    p = os.path.join(dirpath, fname)
    if not os.path.isfile(p):
        return None
    df = pd.read_csv(p)
    for col in ("graph_id", "Design"):
        if col in df.columns:
            df = df[df[col].astype(str) == str(design)]
    return df.reset_index(drop=True)


def feature_stat_checks(case, design, feat, labs, tensors):
    """GROUP B — feature column re-derivation, distribution honesty, vocab."""
    x = tensors["b"].x
    nt = x[:, 0].long()

    def col(type_id, xcol):
        return x[nt == type_id, xcol]

    # --- categorical enum-range + vocab coverage (variant b carries all types) -
    net_tt = col(1, 2)
    check("feat.net nodes all signal (net_type_id==0)",
          (net_tt.numel() == 0) or bool((net_tt == 0).all()),
          f"nonzero={int((net_tt != 0).sum())}")
    orient = col(0, 7)
    check("feat.gate orientation_id in [0,7]",
          (orient.numel() == 0) or bool(((orient >= 0) & (orient <= 7)).all()),
          f"oob={int(((orient < 0) | (orient > 7)).sum())}")
    status = col(0, 8)
    check("feat.gate placement_status_id in {0,1} (final DEF placed/fixed)",
          (status.numel() == 0) or bool(((status == 0) | (status == 1)).all()),
          f"other={int(((status != 0) & (status != 1)).sum())}")
    ctid = col(0, 2)
    check("feat.gate cell_type_id >= 0 (no unmapped -1)",
          (ctid.numel() == 0) or bool((ctid >= 0).all()),
          f"neg={int((ctid < 0).sum())}")
    iod = col(2, 5)
    check("feat.iopin pin_direction_id in [0,3]",
          (iod.numel() == 0) or bool(((iod >= 0) & (iod <= 3)).all()),
          f"oob={int(((iod < 0) | (iod > 3)).sum())}")
    ptid = col(3, 2)
    check("feat.pin pin_type_id in [0,14]",
          (ptid.numel() == 0) or bool(((ptid >= 0) & (ptid <= 14)).all()),
          f"oob={int(((ptid < 0) | (ptid > 14)).sum())}")

    def_path = _find_final_def(case)
    dt = read_def_truth(def_path) if def_path else None

    # --- placement_status_id exactly vs DEF ------------------------------------
    if dt:
        gdf = _read_design_csv(feat, "nodes_gate.csv", design)
        smap = {"PLACED": 0, "FIXED": 1}
        bad = chk = 0
        for _, r in gdf.iterrows():
            c = dt["comps"].get(r["inst_name"])
            exp = smap.get(c["status"]) if c else None
            if exp is None:
                continue
            chk += 1
            if int(r["placement_status_id"]) != exp:
                bad += 1
        check("feat.gate placement_status_id == DEF PLACED/FIXED",
              bad == 0 and chk > 0, f"{bad}/{chk}")

    # --- fanout == max(0, pin_count-1) exactly ---------------------------------
    ndf = _read_design_csv(feat, "nodes_net.csv", design)
    if ndf is not None and len(ndf):
        fbad = int((ndf["fanout"] != (ndf["pin_count"] - 1).clip(lower=0)).sum())
        check("feat.net fanout == max(0, pin_count-1)", fbad == 0, f"{fbad} rows differ")

    # --- num_layer bounded by routing-layer count ------------------------------
    plat = resolve_platform_files(case)
    layers, _ = read_lef_truth(plat.get("TECH_LEF", ""),
                               plat.get("ADDITIONAL_LEFS", "").split())
    if ndf is not None and len(ndf) and layers:
        nl = ndf["num_layer"]
        oob = int(((nl < 0) | (nl > len(layers))).sum())
        routed0 = int((ndf[ndf["hpwl_um"] > 0]["num_layer"] < 1).sum())
        check("feat.net num_layer in [0,|routing layers|]; routed nets >= 1",
              oob == 0 and routed0 == 0, f"oob={oob} routed0={routed0} L={len(layers)}")

    # --- nearest_tap_distance bounded (>=0, <=die diag, >= nearest cell) --------
    if dt:
        idf = _read_design_csv(feat, "nodes_iopin.csv", design)
        dbu, da = dt["dbu"], dt["diearea"]
        diag = (((da[2] - da[0]) ** 2 + (da[3] - da[1]) ** 2) ** 0.5) / dbu if da else 1e12
        comp_xy = [((c["x"] or 0) / dbu, (c["y"] or 0) / dbu)
                   for c in dt["comps"].values() if c["x"] is not None]
        bad = 0
        for _, r in (idf.iterrows() if idf is not None else []):
            ntd = float(r["nearest_tap_distance_um"])
            if ntd < -1e-9 or ntd > diag + 1e-6:
                bad += 1
                continue
            if comp_xy and ntd > 0:   # a tap is one of the comps -> can't be closer
                px, py = float(r["pin_x_um"]), float(r["pin_y_um"])
                nany = min(((px - cx) ** 2 + (py - cy) ** 2) ** 0.5 for cx, cy in comp_xy)
                if ntd < nany - 1e-3:
                    bad += 1
        check("feat.iopin nearest_tap_distance bounded (>= nearest cell, <= die diag)",
              bad == 0, f"{bad} out of bound")

    # --- stats-gate honesty: JSON summaries reflect the CURRENT CSVs -----------
    fj = os.path.join(case, "reports", "features_stats.json")
    if os.path.isfile(fj):
        rep = json.load(open(fj)).get("features", {})
        mism = []
        for csvname, cols in _FEATURE_SUMMARY_COLS.items():
            df = _read_design_csv(feat, csvname + ".csv", design)
            if df is None:
                continue
            entry = rep.get(csvname, {})
            if entry.get("rows") not in (None, len(df)):
                mism.append(f"{csvname}.rows:{entry.get('rows')}!={len(df)}")
            for c in cols:
                got = _numsummary(pd.to_numeric(df[c], errors="coerce").tolist()) if c in df else None
                if not _summ_close(got, entry.get(c)):
                    mism.append(f"{csvname}.{c}")
        check("feat.features_stats.json matches recomputed CSV distributions",
              not mism, f"mismatch {mism[:6]}")
    lj = os.path.join(case, "reports", "labels_stats.json")
    if os.path.isfile(lj):
        rep = json.load(open(lj)).get("labels", {})
        mism = []
        for csvname, (lcol, rcol, key) in _LABEL_SUMMARY.items():
            entry = rep.get(key, {})
            if entry.get("status") != "ok":
                continue
            df = _read_design_csv(labs, csvname, design)
            if df is None:
                mism.append(f"{key}:csv-missing")
                continue
            for c, sub in ((lcol, "label"), (rcol, rcol)):
                got = _numsummary(pd.to_numeric(df[c], errors="coerce").tolist()) if c in df else None
                if not _summ_close(got, entry.get(sub)):
                    mism.append(f"{key}.{sub}")
        check("feat.labels_stats.json matches recomputed CSV distributions",
              not mism, f"mismatch {mism[:6]}")


# ===========================================================================
# COMPREHENSIVE VERIFICATION — GROUP C: LABELS <-> SIGN-OFF REPORTS
# ---------------------------------------------------------------------------
# The label extractors leave almost no report behind (timing report_checks ->
# /dev/null; PDNSim raw voltage dump deleted on success), so "label matches
# sign-off report" is verified against the artifacts that DO survive:
#   * reports/drc.json + reports/lvs.json  -> the dataset must be built from a
#     signed-off (clean) design; a dataset over a DRC/LVS-dirty run is invalid.
#   * reports/ppa.json geometry            -> io_count / macro_count /
#     sequential_count re-derived from DEF+liberty (the EXACT ones; the fill-
#     inflated instance_count is deliberately NOT asserted as an identity).
#   * 6_final.sdc clock period             -> the timing LABEL is exactly
#     max(0, period - worst_cell_slack); label == log1p(path_delay). This ties
#     the timing target to the sign-off timing CONSTRAINT file.
#   * 6_final.spef                         -> C_total feature and equiv_res
#     labels bounded by an independent SPEF re-parse.
# An OPT-IN --signoff-recheck additionally re-runs OpenROAD PDNSim to re-derive
# the IR-drop label (the one label whose tool report is deleted), diffing the
# CSV; it SKIPs (never FAILs) when OpenROAD is absent.
# ===========================================================================


def _find_backend_file(case, *relnames):
    runs = sorted((r for r in os.listdir(case + "/backend") if r.startswith("RUN_")),
                  reverse=True) if os.path.isdir(case + "/backend") else []
    for r in runs:
        for rel in relnames:
            p = f"{case}/backend/{r}/{rel}"
            if os.path.isfile(p):
                return p
    return None


def _sdc_clock_period(case):
    # Prefer 6_final.sdc across ALL runs before falling back to updated_clks.sdc.
    # _find_backend_file is newest-run-major, so a newer INCOMPLETE run that holds
    # only updated_clks.sdc (an Fmax-search intermediate carrying a different, tighter
    # period) would otherwise win over an older run's authoritative 6_final.sdc --
    # reading the wrong clock period and false-failing EVERY in-path timing label.
    # The timing LABELS anchor to the run that actually has 6_final (run_labels picks
    # the newest run with a 6_final.odb/def), so the period source must too. Surfaced
    # 2026-07-08 on wb2axip_axivfifo: 6_final.sdc=10.0 but a newer probe run's
    # updated_clks.sdc=6.2416 -> 76986/76986 in-path labels reported "bad". Clean
    # designs are exposed too (iir/aes_core/bm_sfifo all carry an updated_clks.sdc;
    # they pass only because their newest run also has 6_final.sdc). failure-patterns #25.
    sdc = (_find_backend_file(case, "results/6_final.sdc")
           or _find_backend_file(case, "results/updated_clks.sdc"))
    if not sdc:
        return None
    periods = []
    for line in open(sdc, errors="ignore"):
        m = re.search(r"create_clock.*-period\s+([0-9.eE+-]+)", line)
        if m:
            try:
                periods.append(float(m.group(1)))
            except ValueError:
                pass
    return min(periods) if periods else None   # single-clock scope; tightest if >1


def _spef_resistances(case):
    """Independent flat list of all *RES values (ohms, R_UNIT-scaled)."""
    spef = _find_spef(case)
    if not spef:
        return None
    rscale = 1.0
    vals = []
    insec = False
    for raw in open(spef, errors="ignore"):
        s = raw.strip()
        if s.startswith("*R_UNIT"):
            p = s.split()
            if len(p) >= 3:
                v, u = float(p[1]), p[2].upper()
                rscale = (v * 1e3 if u.startswith("KOHM") else v * 1e6 if u.startswith("MOHM") else v)
            continue
        if s.startswith("*RES"):
            insec = True
            continue
        if s.startswith(("*CAP", "*END", "*CONN", "*D_NET", "*R_NET", "*DRIVER", "*LOAD", "*C ")):
            insec = False
        if insec and s and s[0].isdigit():
            t = s.split()
            if len(t) >= 4:
                try:
                    vals.append(float(t[3]) * rscale)
                except ValueError:
                    pass
    return vals or None


def signoff_report_checks(case, design, feat, labs, tensors):
    """GROUP C — cross-check labels/dataset against surviving sign-off reports."""
    reports = os.path.join(case, "reports")

    # --- DRC / LVS clean provenance gate (fail-closed) -------------------------
    # Old trap: a design that never ran DRC/LVS passed VACUOUSLY (no report ->
    # no check -> "clean"). Now the absence of provenance is itself a failure:
    # either the report files exist, or the manifest carries the flow gate's
    # signoff_health verdict (signoff_gate.py, failure-patterns.md #34).
    # LVS `skipped` is acceptable — an EXPLICIT skip recorded by the signoff
    # step (portless design / no platform deck), unlike a missing report.
    man_p = os.path.join(case, "dataset", "graph_manifest.json")
    sh = {}
    if os.path.isfile(man_p):
        try:
            sh = json.load(open(man_p)).get("signoff_health") or {}
        except Exception:
            sh = {}
    seen = 0
    for tool, fn, ok_states in (("drc", "drc.json", {"clean", "clean_beol"}),
                                ("lvs", "lvs.json", {"clean", "skipped"})):
        p = os.path.join(reports, fn)
        if os.path.isfile(p):
            seen += 1
            st = json.load(open(p)).get("status", "")
            check(f"signoff.{tool} clean (dataset built on a signed-off design)",
                  st in ok_states, f"status={st!r}")
    gate_recorded = bool(sh.get("checks"))
    check("signoff.provenance recorded (drc/lvs reports or manifest signoff_health)",
          seen > 0 or gate_recorded,
          "no reports/{drc,lvs}.json and no signoff gate verdict in the manifest — "
          "sign-off provenance unknown (fail-closed, failure-patterns.md #34)")
    if gate_recorded:
        check("signoff.gate verdict pass (manifest signoff_health)",
              sh.get("status") in {"pass", "pass_with_caveats"},
              f"signoff_health.status={sh.get('status')!r} blockers={sh.get('blockers')}")
        # The DEF binding must survive into the manifest (agent-logic #5, 2026-07-16):
        # the extractor sub-stages once re-gated WITHOUT --def, overwriting
        # run_graphs.sh's binding=bound verdict (with a def_fingerprint) down to
        # 'unknown' and dropping the fingerprint. A DEF-aware gate must read 'bound';
        # a deliberate R2G_DEF/R2G_ODB override (def_overridden) legitimately records
        # 'unknown'. Older manifests with NO binding key are grandfathered (pre-P0-17).
        binding = (sh.get("checks") or {}).get("binding")
        if binding is not None:
            bstatus = binding.get("status")
            overridden = bool(sh.get("def_overridden"))
            benign = {"bound", "unknown"} if overridden else {"bound"}
            check("signoff.binding bound (DEF binding not lost by the extractor)",
                  bstatus in benign,
                  f"binding status={bstatus!r} def_overridden={overridden} — "
                  "unknown/unbound means the DEF binding + fingerprint were dropped "
                  "(extractor re-gated without --def?)")

    def_path = _find_final_def(case)
    dt = read_def_truth(def_path) if def_path else None
    plat = resolve_platform_files(case)

    # --- geometry vs ppa.json (the exact identities only) ---------------------
    ppaf = os.path.join(reports, "ppa.json")
    if os.path.isfile(ppaf) and dt:
        geo = json.load(open(ppaf)).get("geometry", {})
        idf = _read_design_csv(feat, "nodes_iopin.csv", design)
        if idf is not None and geo.get("io_count") is not None:
            check("signoff.geometry io_count == nodes_iopin rows",
                  int(geo["io_count"]) == len(idf), f"ppa={geo['io_count']} csv={len(idf)}")
        _, blocks = read_lef_truth(plat.get("TECH_LEF", ""),
                                   plat.get("ADDITIONAL_LEFS", "").split())
        block_up = {b.upper() for b in blocks}
        macros = sum(1 for c in dt["comps"].values()
                     if str(c["master"]).upper() in block_up)
        if geo.get("macro_count") is not None:
            check("signoff.geometry macro_count == DEF BLOCK-class instances",
                  int(geo["macro_count"]) == macros, f"ppa={geo['macro_count']} def={macros}")
        lib = read_liberty_truth((plat.get("LIB_FILES", "") + " "
                                  + plat.get("ADDITIONAL_LIBS", "")).split())
        seq = sum(1 for c in dt["comps"].values()
                  if (lib.get(str(c["master"]).upper()) or {}).get("is_seq"))
        if geo.get("sequential_count") is not None:
            check("signoff.geometry sequential_count == liberty-sequential instances",
                  int(geo["sequential_count"]) == seq, f"ppa={geo['sequential_count']} lib={seq}")

    # --- timing LABEL <-> SDC clock-period constraint (exact transform) --------
    period = _sdc_clock_period(case)
    tdf = _read_design_csv(labs, "timing_features.csv", design)
    if period and tdf is not None and len(tdf):
        bad = off_bad = chk = 0
        for _, r in tdf.iterrows():
            pd_ = float(r["Path_Delay_ns"])
            lab = float(r["label"])
            if abs(lab - math.log1p(pd_)) > 1e-3:      # label == log1p(path_delay), always
                bad += 1
                continue
            if str(r.get("in_sta_path", "")).lower() == "true":
                try:
                    slack = float(r["Cell_Slack_ns"])
                except (ValueError, TypeError):
                    continue
                chk += 1
                if abs(pd_ - max(0.0, period - slack)) > 1e-3:
                    bad += 1
            elif abs(pd_) > 1e-9 or abs(lab) > 1e-9:
                off_bad += 1
        check("signoff.timing Path_Delay==clk_period-slack (6_final.sdc) & label==log1p",
              bad == 0 and off_bad == 0 and chk > 0,
              f"bad={bad} off_bad={off_bad} inpath={chk} period={period}")

    # --- C_total feature bounded by RC labels (SPEF ground/coupling) ----------
    mdf = _read_design_csv(feat, "metadata.csv", design)
    gdf = _read_design_csv(labs, "net_ground_cap.csv", design)
    cdf = _read_design_csv(labs, "coupling_cap.csv", design)
    if mdf is not None and len(mdf) and gdf is not None and cdf is not None:
        ct = float(mdf.iloc[0]["C_total"])
        g = float(pd.to_numeric(gdf["ground_cap_fF"], errors="coerce").sum()) if len(gdf) else 0.0
        cpl = float(pd.to_numeric(cdf["coupling_cap_fF"], errors="coerce").sum()) if len(cdf) else 0.0
        if ct > 0:   # C_total>0 => SPEF present
            # coupling may be written once (asymmetric SPEF) or twice (symmetric);
            # C_total counts every *CAP -> Σg+Σc <= C_total <= Σg+2Σc.
            lo, hi = g + cpl, g + 2 * cpl
            tol = max(1e-2, 0.02 * hi)
            check("signoff.metadata C_total within [Σground+Σcoupling, Σground+2Σcoupling]",
                  lo - tol <= ct <= hi + tol,
                  f"C_total={ct:.3f} lo={lo:.3f} hi={hi:.3f}")

    # --- equiv_res LABEL magnitude bounded by independent SPEF *RES ------------
    rvals = _spef_resistances(case)
    edf = _read_design_csv(labs, "equiv_res.csv", design)
    if rvals and edf is not None and len(edf):
        total, mn = sum(rvals), min(rvals)
        er = pd.to_numeric(edf["equiv_res_ohm"], errors="coerce").dropna()
        if len(er):
            oob = int(((er <= 0) | (er > total * 1.01)).sum())
            # scale sanity: a reduced pin-pair R is on the SPEF's ohm scale, not
            # 1000x off (unit bug) -> its max sits between one resistor and ΣR.
            scale_ok = (float(er.max()) >= mn * 0.5) and (float(er.max()) <= total * 1.01)
            check("signoff.equiv_res label bounded by SPEF *RES (0<r<=ΣR, scale sane)",
                  oob == 0 and scale_ok,
                  f"oob={oob} er_max={float(er.max()):.3f} ΣR={total:.3f} minR={mn:.3f}")

    # --- OPT-IN: re-run PDNSim to re-derive the IR-drop LABEL independently -----
    if SIGNOFF_RECHECK:
        _signoff_recheck_irdrop(case, design, feat, labs)


def _signoff_recheck_irdrop(case, design, feat, labs):
    """OPT-IN (--signoff-recheck): re-run OpenROAD PDNSim on 6_final.odb to
    re-derive the IR-drop label. IR drop is the ONE label whose tool artifact
    is deleted on success (extract_irdrop.tcl renames the processed CSV in and
    `file delete`s the raw PDNSim dump), so it is the only label with no
    surviving report to diff — a re-run is the sole independent value check.
    Parsing mirrors extract_irdrop.tcl:161-190 exactly (6 comma-fields, skip
    header, field[0]=inst, field[5]=voltage, same instance filter). SKIPs
    (never FAILs) on any missing dependency or tool error."""
    import shutil
    import shlex
    import subprocess
    import tempfile
    name = "signoff.irdrop label == re-run PDNSim analyze_power_grid (per-cell)"
    orx = os.environ.get("OPENROAD_EXE") or shutil.which("openroad")
    if not orx or not os.path.isfile(orx):
        skip(name, "openroad not found (set OPENROAD_EXE)")
        return
    odb = _find_backend_file(case, "final/6_final.odb", "results/6_final.odb")
    irdf = _read_design_csv(labs, "ir_drop.csv", design)
    if not odb:
        skip(name, "no 6_final.odb")
        return
    if irdf is None or not len(irdf) or "IR_Drop_mV" not in irdf.columns:
        skip(name, "no ir_drop.csv")
        return
    plat = resolve_platform_files(case)
    libs = [l for l in (plat.get("LIB_FILES", "") + " "
                        + plat.get("ADDITIONAL_LIBS", "")).split() if l]
    mdf = _read_design_csv(feat, "metadata.csv", design)
    try:
        supply = float(mdf.iloc[0]["V_nom"]) if mdf is not None and len(mdf) else 0.0
    except (ValueError, TypeError, KeyError):
        supply = 0.0
    if supply <= 0:
        skip(name, "no supply voltage (metadata V_nom)")
        return
    try:
        with tempfile.TemporaryDirectory() as tmp:
            raw = os.path.join(tmp, "ir.raw")
            tcl = os.path.join(tmp, "ir.tcl")
            lib_lines = "\n".join(f"read_liberty {shlex.quote(l)}" for l in libs)
            script = f"""read_db {shlex.quote(odb)}
{lib_lines}
set target ""
foreach n {{VDD VPWR vdd vpwr}} {{ if {{[get_nets -quiet $n] != ""}} {{ set target $n; break }} }}
if {{$target eq ""}} {{ puts "R2G_NO_NET"; exit 0 }}
catch {{set_pdnsim_net_voltage -net $target -voltage {supply}}}
foreach g {{VSS VGND vss vgnd}} {{ if {{[get_nets -quiet $g] != ""}} {{ catch {{set_pdnsim_net_voltage -net $g -voltage 0.0}}; break }} }}
if {{[catch {{analyze_power_grid -net $target -voltage_file {shlex.quote(raw)}}} e]}} {{ puts "R2G_PDN_ERR $e"; exit 0 }}
puts "R2G_OK"
exit 0
"""
            with open(tcl, "w") as fh:
                fh.write(script)
            r = subprocess.run([orx, "-exit", tcl], capture_output=True,
                               text=True, timeout=900)
            if not os.path.isfile(raw):
                tail = (r.stdout or "").strip().splitlines()[-1:] or [""]
                skip(name, f"PDNSim produced no voltage file ({tail[0][:80]})")
                return
            # Independent parse (mirrors extract_irdrop.tcl exactly).
            volt = {}
            with open(raw, errors="ignore") as fh:
                for i, line in enumerate(fh):
                    if i == 0:
                        continue
                    fields = line.split(",")
                    if len(fields) != 6:
                        continue
                    inst = fields[0].strip()
                    if re.match(r"(?i)^(wire|FILLER_|PHY_EDGE|TAPCELL|ENDCAP)", inst):
                        continue
                    try:
                        v = float(fields[5].strip())
                    except ValueError:
                        continue
                    ir = max(0.0, (supply - v) * 1000.0)
                    if inst not in volt or ir > volt[inst]:   # worst per instance
                        volt[inst] = ir
            if not volt:
                skip(name, "no parseable PDNSim rows")
                return
            csvmap = {}
            for _, row in irdf.iterrows():
                k = str(row["Cell"])
                try:
                    ir = float(row["IR_Drop_mV"])
                except (ValueError, TypeError):
                    continue
                csvmap[k] = max(csvmap.get(k, 0.0), ir)   # groupby-Cell max (graph stage)
            common = [k for k in csvmap if k in volt]
            if len(common) < 0.5 * max(1, len(csvmap)):
                skip(name, f"join too small ({len(common)}/{len(csvmap)})")
                return
            bad = sum(1 for k in common
                      if abs(csvmap[k] - volt[k]) > max(0.05, 0.05 * volt[k]))
            check(name, bad == 0, f"{bad}/{len(common)} cells differ >5% (or 0.05mV)")
    except subprocess.TimeoutExpired:
        skip(name, "openroad timed out (900s)")
    except Exception as e:   # a re-run harness error must never masquerade as FAIL
        skip(name, f"re-run error: {e!r}"[:120])


def verify_case(case, design=None, json_out=None):
    case = case.rstrip("/")
    SKIPPED.clear()   # per-case; RESULTS is cleared by the batch loop / fresh proc
    feat, labs, ds = case + "/features", case + "/labels", case + "/dataset"
    man = json.load(open(ds + "/graph_manifest.json"))
    design = design or man.get("design") or os.path.basename(case)
    print(f"== {design} ({case}) ==")

    # A signoff-gate BLOCK superseded a previously-green dataset (full-pipeline #6,
    # 2026-07-16): the .pt files on disk are stale and must NOT be certified. Fail
    # fast and cleanly instead of re-deriving checks against orphaned tensors.
    if man.get("status") == "blocked_unsigned":
        check("manifest.status not blocked_unsigned (design signed off for this DEF)",
              False, f"dataset superseded: reason={man.get('reason')!r} — rebuild from "
                     "a signed-off run (the .pt files on disk are stale)")
        n_fail = sum(1 for r in RESULTS if not r["ok"])
        print(f"== {design}: {len(RESULTS) - n_fail}/{len(RESULTS)} checks passed "
              f"(blocked_unsigned) ==")
        if json_out:
            with open(json_out, "w") as fh:
                json.dump({"design": design, "results": RESULTS, "skipped": SKIPPED,
                           "passed": len(RESULTS) - n_fail, "failed": n_fail}, fh, indent=1)
        return n_fail

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
    # _load_graph reconstructs the homogeneous Data the checks below expect from a
    # HeteroData (the default graph_kind); hb is the raw HeteroData (None if homo).
    b, hb = _load_graph(ds + "/b_graph.pt")
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
    c, hc = _load_graph(ds + "/c_graph.pt")
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
    d, hd = _load_graph(ds + "/d_graph.pt")
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
    e, he = _load_graph(ds + "/e_graph.pt")
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
    f, hf = _load_graph(ds + "/f_graph.pt")
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
    tensors = {"b": b, "c": c, "d": d, "e": e, "f": f}
    # Raw HeteroData per view (None when the dataset is homogeneous). manifest
    # 'nodes'/'edges' stay the homo totals (== reconstructed), so the parity check
    # below is valid for both kinds; the per-type/per-relation breakdown lands in
    # the manifest's per-variant 'hetero' block and is checked by hetero_checks.
    hetero_tensors = {"b": hb, "c": hc, "d": hd, "e": he, "f": hf}
    is_hetero = all(v is not None for v in hetero_tensors.values())
    check("graph_kind consistent across views (all hetero or all homo)",
          is_hetero or all(v is None for v in hetero_tensors.values()),
          f"hetero-per-view={ {k: v is not None for k, v in hetero_tensors.items()} }")
    for vname, data in tensors.items():
        st = man["variants"][vname]
        check(f"manifest[{vname}] nodes/edges match tensors",
              st["nodes"] == data.x.shape[0] and st["edges"] == data.edge_index.shape[1])

    # ---- GROUP A: full topology of all five views (not just b) ----
    topology_checks(views, tensors, hetero=hetero_tensors if is_hetero else None)

    # ---- GROUP D: HeteroData structure (default graph_kind) ----
    if is_hetero:
        hetero_checks(hetero_tensors, views, man)

    # ---- GROUP B: feature column re-derivation + distribution honesty ----
    feature_stat_checks(case, design, feat, labs, tensors)

    # ---- GROUP C: labels <-> surviving sign-off reports ----
    signoff_report_checks(case, design, feat, labs, tensors)

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

    # ---- RAW label twins (data.y_raw / edge_y_raw / rc_edge_y_raw) ----
    # Each carries the raw physical value; must be present, same shape, NaN-parity
    # with the normalized twin per slot (so a raw slot can't silently go all-NaN),
    # and where the transform is a clean log1p (wirelength y4, ground cap y5, and
    # the coupling/resistance edge columns) satisfy y == log1p(y_raw).
    def _nan_parity(a, bb):
        return all(bool((torch.isnan(a[:, s]) == torch.isnan(bb[:, s])).all())
                   for s in range(1, a.shape[1])) if a.shape == bb.shape else False

    def _log1p_identity(vname, tag, yt, yr, cols):
        for s in cols:
            m = ~torch.isnan(yt[:, s]) & ~torch.isnan(yr[:, s])
            if int(m.sum()) == 0:
                continue
            d_id = float((yt[m, s] - torch.log1p(yr[m, s].clamp(min=0))).abs().max())
            check(f"{vname} {tag}[:,{s}] == log1p({tag}_raw)", d_id <= 1e-4, d_id)

    for vname, data in tensors.items():
        okr = hasattr(data, "y_raw") and data.y_raw.shape == data.y.shape
        check(f"{vname} y_raw present + shape==y", okr)
        if okr:
            check(f"{vname} y_raw NaN-parity with y (all slots)", _nan_parity(data.y, data.y_raw))
            # timing (y3, raw=Path_Delay_ns), wirelength (y4), ground cap (y5) are
            # clean log1p identities; congestion(y1)/irdrop(y2) use a different base.
            _log1p_identity(vname, "y", data.y, data.y_raw, (3, 4, 5))
        if hasattr(data, "edge_y"):
            oke = hasattr(data, "edge_y_raw") and data.edge_y_raw.shape == data.edge_y.shape
            check(f"{vname} edge_y_raw present + shape==edge_y", oke)
            if oke:
                check(f"{vname} edge_y_raw NaN-parity with edge_y", _nan_parity(data.edge_y, data.edge_y_raw))
                _log1p_identity(vname, "edge_y", data.edge_y, data.edge_y_raw, (3, 4))  # folded timing/wirelength
        if hasattr(data, "rc_edge_y"):
            okc = hasattr(data, "rc_edge_y_raw") and data.rc_edge_y_raw.shape == data.rc_edge_y.shape
            check(f"{vname} rc_edge_y_raw present + shape==rc_edge_y", okc)
            if okc and data.rc_edge_y.shape[0]:
                _log1p_identity(vname, "rc_edge_y", data.rc_edge_y, data.rc_edge_y_raw, (1, 2))
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
        # (G) RAW twins carry the raw SPEF physical value (fF), not the log1p label.
        # Guarded: a stale post-RC/pre-raw-twin dataset (has rc_edge_* but no y_raw)
        # must fail the earlier '{v} y_raw present' check cleanly, not crash here.
        if hasattr(b, "y_raw") and hasattr(b, "rc_edge_y_raw"):
            badg = chkg = 0
            for i, nm in enumerate(net["net_name"].tolist()):
                if nm not in gt:
                    continue
                gr = float(b.y_raw[ng + i, 5])
                chkg += 1
                if math.isnan(gr) or abs(gr - gt[nm]) > max(1e-4, 1e-3 * gt[nm]):
                    badg += 1
                if chkg >= 400:
                    break
            check("b ground cap y_raw == raw SPEF ground fF", badg == 0 and chkg > 0, f"{badg}/{chkg}")
            badc = chkc = 0
            for k in ci[::max(1, len(ci) // 200)]:
                u, v = int(b.rc_edge_index[0, k]), int(b.rc_edge_index[1, k])
                a, bb = bnames[u], bnames[v]
                key = (a, bb) if a < bb else (bb, a)
                if key in ct:
                    chkc += 1
                    if abs(float(b.rc_edge_y_raw[k, 1]) - ct[key]) > max(1e-4, 1e-3 * ct[key]):
                        badc += 1
            check("b coupling rc_edge_y_raw == raw SPEF coupling fF", badc == 0 and chkc > 0, f"{badc}/{chkc}")

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
        _bk = case + "/backend"
        runs = sorted(
            (r for r in os.listdir(_bk) if r.startswith("RUN_")), reverse=True) \
            if os.path.isdir(_bk) else []
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
    # The extractor no longer fabricates a driver (2026-07-14), so an individual
    # signal net may legitimately read num_drivers==0 (undriven / a liberty
    # parse-miss surfaced honestly). Each net's value is already validated against
    # the independent DEF+liberty recompute ("ext.net num_drivers vs liberty/DEF
    # dirs"); here just guard against a wholly-broken (all-zero) column. Dropped
    # the old `>= 1 on ALL nets` assert, which relied on the removed force-fill.
    check("num_drivers column not all-zero (>=1 on some signal net)",
          bool((net["num_drivers"].astype(int) >= 1).any()))

    # ---- wide-coverage extension: X/Y values vs independently re-parsed
    # liberty/LEF/DEF truth, structural gates, regression probes ----
    extended_checks(case, design, feat, labs, views, b)

    n_fail = sum(1 for r in RESULTS if not r["ok"])
    skip_note = f" ({len(SKIPPED)} skipped)" if SKIPPED else ""
    print(f"== {design}: {len(RESULTS) - n_fail}/{len(RESULTS)} checks passed{skip_note} ==")
    if json_out:
        with open(json_out, "w") as fh:
            json.dump({"design": design, "results": RESULTS, "skipped": SKIPPED,
                       "passed": len(RESULTS) - n_fail, "failed": n_fail}, fh, indent=1)
    return n_fail


def main():
    # Line-buffer stdout so `--batch` shows live per-check progress instead of
    # block-buffering the whole run. On a large design (e.g. aes_core ~190K cells /
    # ~600K RC-edge checks → 15+ min) block buffering makes a healthy verify look
    # identical to a hang, which has repeatedly wasted operator/loop-tick time.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("case_dir", nargs="?", default=None)
    ap.add_argument("--design", default=None)
    ap.add_argument("--json", default=None)
    ap.add_argument("--batch", default=None, metavar="ROOT",
                    help="verify every ROOT subdir containing dataset/graph_manifest.json")
    ap.add_argument("--signoff-recheck", action="store_true",
                    help="opt-in: re-run OpenROAD PDNSim to independently re-derive "
                         "the IR-drop label (needs OPENROAD_EXE; SKIPs if absent)")
    args = ap.parse_args()

    global SIGNOFF_RECHECK
    SIGNOFF_RECHECK = args.signoff_recheck

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
