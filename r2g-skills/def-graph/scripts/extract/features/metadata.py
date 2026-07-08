"""Graph-level (per-design) feature row -> metadata.csv.

Refactored from feature_test_v2/py/metadata.py: shared parsers from def_parse, liberty
from the resolved R2G_LIB_FILES, SPEF absence degrades C_total to 0. Output columns and
per-field semantics are unchanged.
"""
import csv
import os
import re
import sys
from collections import defaultdict

from case_paths import resolve_case_paths
from techlib.def_parse import parse_units
from techlib.liberty import load_liberty_db


def _strip_inline_comment(s):
    if "#" in s:
        return s.split("#", 1)[0].strip()
    return s.strip()


def parse_config_mk(mk_path):
    vals = {}
    if not mk_path or not os.path.isfile(mk_path):
        return vals
    with open(mk_path, "r") as f:
        for raw in f:
            line = _strip_inline_comment(raw)
            if not line:
                continue
            m = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*[:?+]?=\s*(.*)\s*$", line)
            if not m:
                continue
            k = m.group(1).strip()
            v = m.group(2).strip()
            if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                v = v[1:-1]
            vals[k] = v
    return vals


def parse_sdc_period_ns(sdc_path):
    if not sdc_path or not os.path.isfile(sdc_path):
        return None
    vars_ns = {}
    periods = []
    with open(sdc_path, "r") as f:
        for raw in f:
            line = _strip_inline_comment(raw)
            if not line:
                continue
            m_set = re.match(r"^\s*set\s+([A-Za-z_][A-Za-z0-9_]*)\s+([0-9]*\.?[0-9]+)\s*$", line)
            if m_set:
                vars_ns[m_set.group(1)] = float(m_set.group(2))
                continue
            m_clk = re.search(r"\bcreate_clock\b.*?\s-period\s+([^\s\]]+)", line)
            if not m_clk:
                continue
            tok = m_clk.group(1).strip()
            if tok.startswith("$"):
                tok = tok[1:]
                if tok in vars_ns:
                    periods.append(vars_ns[tok])
            else:
                try:
                    periods.append(float(tok))
                except ValueError:
                    pass
    if not periods:
        return None
    return min(periods)


def parse_spef_total_cap_fF(spef_path):
    if not spef_path or not os.path.isfile(spef_path):
        return None

    unit_scale_to_fF = None
    total_cap_fF = 0.0
    with open(spef_path, "r") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            if unit_scale_to_fF is None and line.startswith("*C_UNIT"):
                m = re.match(r"^\*C_UNIT\s+([0-9eE+.-]+)\s+(\S+)\s*$", line)
                if m:
                    try:
                        mag = float(m.group(1))
                    except Exception:
                        mag = 1.0
                    unit = m.group(2).upper()
                    if unit in ["FF", "FEMTOFARAD", "FEMTOFARADS"]:
                        unit_scale_to_fF = mag
                    elif unit in ["PF", "PICOFARAD", "PICOFARADS"]:
                        unit_scale_to_fF = mag * 1e3
                    elif unit in ["NF", "NANOFARAD", "NANOFARADS"]:
                        unit_scale_to_fF = mag * 1e6
                    elif unit in ["UF", "MICROFARAD", "MICROFARADS"]:
                        unit_scale_to_fF = mag * 1e9
                continue

            if line.startswith("*D_NET"):
                m = re.match(r"^\*D_NET\s+\S+\s+([0-9eE+.-]+)\s*$", line)
                if not m:
                    continue
                try:
                    cap = float(m.group(1))
                except Exception:
                    continue
                if unit_scale_to_fF is None:
                    unit_scale_to_fF = 1.0
                total_cap_fF += cap * unit_scale_to_fF
                continue

    return total_cap_fF


def parse_diearea(def_path):
    area = None
    with open(def_path, "r") as f:
        for line in f:
            s = line.strip()
            if s.startswith("DIEAREA"):
                nums = list(map(int, re.findall(r"-?\d+", s)))
                if len(nums) >= 4:
                    area = nums[:4]
                break
    return area


def count_sections(def_path):
    comps = 0
    nets = 0
    ios = 0
    with open(def_path, "r") as f:
        for line in f:
            s = line.strip()
            if s.startswith("COMPONENTS"):
                m = re.search(r"COMPONENTS\s+(\d+)", s)
                if m:
                    comps = int(m.group(1))
            elif s.startswith("NETS"):
                m = re.search(r"NETS\s+(\d+)", s)
                if m:
                    nets = int(m.group(1))
            elif s.startswith("PINS"):
                m = re.search(r"PINS\s+(\d+)", s)
                if m:
                    ios = int(m.group(1))
    return comps, nets, ios


def parse_tracks(def_path):
    counts = defaultdict(int)
    with open(def_path, "r") as f:
        for line in f:
            s = line.strip()
            if s.startswith("TRACKS"):
                m = re.search(r"\bDO\s+(\d+)\s+STEP\s+\S+\s+LAYER\s+(\S+)", s)
                if m:
                    counts[m.group(2)] += int(m.group(1))
    return counts


def avg_fanout_v2(def_path):
    nets = 0
    fanout_sum = 0
    in_nets = False
    pair_re = re.compile(r"\(\s*([^\s()]+)\s+([^\s()]+)\s*\)")
    int_re = re.compile(r"^-?\d+$")
    in_conn_list = False
    cur_pin_count = 0
    with open(def_path, "r") as f:
        for raw in f:
            s = raw.strip()
            if s.startswith("NETS"):
                in_nets = True
                continue
            if in_nets and s.startswith("END NETS"):
                if nets > 0 or cur_pin_count > 0:
                    fanout_sum += max(0, cur_pin_count - 1)
                break
            if not in_nets:
                continue
            if s.startswith("-"):
                if nets > 0:
                    fanout_sum += max(0, cur_pin_count - 1)
                nets += 1
                in_conn_list = True
                cur_pin_count = 0
                for a, b in pair_re.findall(s):
                    if int_re.match(a) and int_re.match(b):
                        continue
                    cur_pin_count += 1
                continue
            if s.startswith("+") or s.startswith("NEW") or s.startswith("ROUTED") or s.startswith("FIXED"):
                in_conn_list = False
            if in_conn_list and "(" in s and ")" in s:
                for a, b in pair_re.findall(s):
                    if int_re.match(a) and int_re.match(b):
                        continue
                    cur_pin_count += 1
    if nets <= 0:
        return 0.0
    return float(fanout_sum) / float(nets)


def main():
    ctx = resolve_case_paths(__file__, "metadata.csv")
    def_path = ctx["def_path"]
    graph_id = ctx["graph_id"]
    out_csv = ctx["out_csv"]
    idx = ctx["extra_arg_start"]

    config_vals = parse_config_mk(ctx["config_path"])
    sdc_path = ctx["sdc_path"] if os.path.isfile(ctx["sdc_path"]) else ""
    place_density = sys.argv[idx] if len(sys.argv) > idx else config_vals.get("PLACE_DENSITY", "Default")
    core_util = sys.argv[idx + 1] if len(sys.argv) > (idx + 1) else config_vals.get("CORE_UTILIZATION", "0")
    abc_area = sys.argv[idx + 2] if len(sys.argv) > (idx + 2) else config_vals.get("ABC_AREA", "0")
    lib_db = load_liberty_db(ctx["lib_files"])
    v_nom = sys.argv[idx + 3] if len(sys.argv) > (idx + 3) else config_vals.get("V_nom", config_vals.get("V_NOM", ""))
    if not v_nom and lib_db.get("v_nom") is not None:
        v_nom = f"{lib_db['v_nom']:.2f}"
    if not v_nom:
        v_nom = "1.10"
    freq_hz = sys.argv[idx + 4] if len(sys.argv) > (idx + 4) else "0"
    if freq_hz == "0":
        period_ns = parse_sdc_period_ns(sdc_path)
        if period_ns and period_ns > 0:
            freq_hz = str(int(round(1e9 / period_ns)))

    spef_path = ctx["spef_path"] if os.path.isfile(ctx["spef_path"]) else ""
    c_total_fF = parse_spef_total_cap_fF(spef_path) if spef_path else None
    if c_total_fF is None:
        c_total_fF = 0.0

    dbu = parse_units(def_path)
    area = parse_diearea(def_path) or [0, 0, 0, 0]
    comps, nets, ios = count_sections(def_path)
    w_um = (area[2] - area[0]) / dbu if dbu else 0
    h_um = (area[3] - area[1]) / dbu if dbu else 0
    core_area = w_um * h_um
    tr = parse_tracks(def_path)
    # tracks_per_layer feeds global_feat[12] and MUST be numeric — the old pipe-joined
    # "metal1:228|..." string was to_numeric-coerced to NaN -> 0.0 by load_global_feat
    # on EVERY platform (2026-07-06 audit). Emit the mean per-layer track count there
    # and keep the per-layer detail in a separate trailing column (loaders read
    # metadata.csv by column name, so the extra column is inert).
    tr_mean = (sum(tr.values()) / len(tr)) if tr else 0.0
    tr_str = "|".join([f"{k}:{tr[k]}" for k in sorted(tr.keys())])
    af = avg_fanout_v2(def_path)
    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["graph_id", "num_cells", "num_nets", "num_ios", "avg_fanout", "die_width", "die_height", "core_area", "dbu_unit", "PLACE_DENSITY", "CORE_UTILIZATION", "ABC_AREA", "C_total", "tracks_per_layer", "V_nom", "freq_Hz", "tracks_detail"])
        w.writerow([graph_id, comps, nets, ios, af, f"{w_um:.3f}", f"{h_um:.3f}", f"{core_area}", dbu, place_density, core_util, abc_area, f"{c_total_fF:.6f}", f"{tr_mean:.3f}", v_nom, freq_hz, tr_str])


if __name__ == "__main__":
    main()
