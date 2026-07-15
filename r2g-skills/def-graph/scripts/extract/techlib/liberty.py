"""Liberty (.lib/.lib.gz) parser + cell/pin/net classifiers — techlib single source of truth.

Moved from ``features/lib_db.py`` and promoted to the consolidated techlib package as the
single source of truth for liberty parse and cell/pin/net classifiers. The feature workers
and label extractors will import from here once the switchover (Task 7) is complete;
``features/lib_db.py`` is retained unchanged until that task to keep the gate green.

Behavior of every getter and classifier (areas, power, pin caps, direction/pin-type/
net-type IDs) is preserved verbatim from the original feature_test_v2 ``lib_db.py`` so
the per-row feature output is unchanged. The only edits vs. the original:

  * ``load_liberty_db`` takes the resolved liberty paths (from ``resolve_platform_paths.sh``
    via ``R2G_LIB_FILES``) instead of probing a hardcoded ``NangateOpenCellLibrary_*`` file
    next to a per-case input dir — this is what makes the stage platform-agnostic.
  * ``.lib.gz`` liberty (asap7 / gf180) is decompressed transparently.
  * a "no liberty found" warning is emitted (a silent miss would zero every area/power/cap).
  * tap detection is overridable via ``R2G_TAP_PATTERNS`` (default == original ``"TAP"``).

Pure stdlib.
"""
import gzip
import math
import os
import re
import sys

POWER_PIN_NAMES = {"VDD", "VPWR", "VCC", "VCCA", "VCCD"}
GROUND_PIN_NAMES = {"VSS", "VGND", "GND", "VSSA", "VSSD"}


def _strip_name_token(s):
    # Strip backslashes, surrounding whitespace, AND surrounding double-quotes.
    # sky130 liberty quotes cell/pin names (`cell ("sky130_fd_sc_hd__...")`); without
    # stripping the quotes the cells-dict keys retain them and never match the unquoted
    # DEF master (`master.upper()`), zeroing cell_area/power/pin-cap and collapsing
    # cell_type_id to UNKNOWN on every sky130 cell. Unquoted-name platforms
    # (nangate45/asap7/gf180/ihp-sg13g2) have no surrounding quotes, so this is a no-op there.
    return (s or "").replace("\\", "").strip().strip('"').strip()


def _norm_key(s):
    return _strip_name_token(s).upper()


def norm_cell_key(s):
    """Public master-name canonicalizer (upper-cased, quote/backslash-stripped).

    The same normalization the cells dict is keyed on — use this when comparing a
    DEF master name against key sets returned by ``macro_cell_keys``.
    """
    return _norm_key(s)


def macro_cell_keys(lib_files, sc_lib_files):
    """Normalized cell keys that come from macro/extra liberty files ONLY.

    ``lib_files`` is the full resolved liberty list; ``sc_lib_files`` the std-cell
    subset (run_features.sh exports both as R2G_LIB_FILES / R2G_SC_LIB_FILES).
    The difference is the per-design macro libs (e.g. fakeram45_*), whose cells
    are the design's macros — the source for ``connects_macro_flag``. Returns an
    empty set for pure std-cell designs (no extra libs), where flag 0 is correct.
    """
    def _as_list(paths):
        if paths is None:
            return []
        if isinstance(paths, str):
            return [t for t in paths.replace(":", " ").split() if t]
        return [p for p in paths if p]

    sc = {os.path.abspath(p) for p in _as_list(sc_lib_files)}
    extra = [p for p in _as_list(lib_files) if os.path.abspath(p) not in sc]
    if not extra:
        return set()
    return set(load_liberty_db(extra).get("cells", {}).keys())


def _sample_std(vals):
    if len(vals) < 2:
        return 0.0
    mean = sum(vals) / float(len(vals))
    var = sum((v - mean) ** 2 for v in vals) / float(len(vals) - 1)
    return math.sqrt(max(var, 0.0))


def _cap_scale_to_ff(unit_mag, unit_name):
    u = (unit_name or "").strip().lower()
    if u == "ff":
        return unit_mag
    if u == "pf":
        return unit_mag * 1e3
    if u == "nf":
        return unit_mag * 1e6
    if u == "uf":
        return unit_mag * 1e9
    return unit_mag


def load_liberty_db(lib_paths=None):
    """Parse the resolved liberty files into a cell/pin attribute DB.

    ``lib_paths`` may be a list of paths or a space/colon-separated string; ``None``
    falls back to the ``R2G_LIB_FILES`` environment variable.
    """
    if lib_paths is None:
        lib_paths = os.environ.get("R2G_LIB_FILES", "")
    if isinstance(lib_paths, str):
        lib_paths = [t for t in lib_paths.replace(":", " ").split() if t]

    db = {
        "v_nom": None,
        "cap_scale_ff": 1.0,
        "cells": {},
        "sources": {"lib": []},
    }
    for lib_path in lib_paths:
        if lib_path and os.path.isfile(lib_path):
            _merge_liberty_file(lib_path, db)
            db["sources"]["lib"].append(lib_path)
    if not db["sources"]["lib"]:
        sys.stderr.write(
            "WARN: no liberty file found (R2G_LIB_FILES) — cell area/power/cap will be 0\n"
        )
    return db


def _merge_liberty_file(lib_path, db):
    opener = gzip.open if lib_path.endswith(".gz") else open
    with opener(lib_path, "rt") as f:
        text = f.read()
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    brace_depth = 0
    current_cell = None
    current_cell_depth = -1
    current_pin = None
    current_pin_depth = -1
    in_leakage = False        # inside a cell-level `leakage_power () { ... }` block
    leak_depth = -1

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue

        # The unit token may be QUOTED — sky130 writes
        # `capacitive_load_unit(1.0000000000, "pf");`, nangate45 bare `(1,ff)`.
        # Missing the quoted form left cap_scale_ff at 1.0 (no pf->ff scaling):
        # every sky130 pin capacitance landed 1000x too small — the sibling of
        # the direction/clock quoted-value bug (failure-patterns.md #5).
        m_cap_unit = re.search(r"capacitive_load_unit\s*\(\s*([0-9eE+.\-]+)\s*,\s*\"?([A-Za-z]+)\"?\s*\)", line)
        if m_cap_unit:
            try:
                mag = float(m_cap_unit.group(1))
            except Exception:
                mag = 1.0
            db["cap_scale_ff"] = _cap_scale_to_ff(mag, m_cap_unit.group(2))

        if db.get("v_nom") is None:
            m_nom = re.search(r"\bnom_voltage\s*:\s*([0-9eE+.\-]+)\s*;", line)
            if m_nom:
                try:
                    db["v_nom"] = float(m_nom.group(1))
                except Exception:
                    pass

        open_count = line.count("{")
        close_count = line.count("}")

        m_cell = re.match(r"cell\s*\(\s*([^)]+?)\s*\)\s*\{", line)
        if m_cell:
            cell_name = _strip_name_token(m_cell.group(1))
            key = _norm_key(cell_name)
            current_cell = db["cells"].setdefault(
                key,
                {
                    "name": cell_name,
                    "area": None,
                    "power": None,
                    "is_sequential": False,
                    "pins": {},
                    # which liberty defined this cell — lets the cell-type map be built
                    # from the standard-cell lib only, so per-design macro libs don't
                    # reshuffle std-cell ids across a platform dataset.
                    "source_lib": lib_path,
                },
            )
            current_cell_depth = brace_depth + open_count
            current_pin = None
            current_pin_depth = -1

        if current_cell is not None:
            # statetable(): clock-gate cells (nangate45 CLKGATE*/CLKGATETST*) hold
            # state via a statetable group, not ff()/latch() (2026-07-06 audit F4).
            # ff_bank()/latch_bank(): MULTIBIT sequential cells (asap7 ships 27 such
            # libs, e.g. DFFHQNx*_ASAP7 -> ``ff_bank(...)``) — the plain ``ff|latch``
            # regex missed them because ``ff`` is not followed by ``(`` in ``ff_bank(``.
            # Longer alternatives listed first so the match is unambiguous
            # (2026-07-06 nangate45 verification round, failure-patterns.md #15).
            if re.match(r"(ff_bank|ff|latch_bank|latch|statetable)\s*\(", line):
                current_cell["is_sequential"] = True

            if current_pin is None:
                # area: trailing ';' optional — asap7 writes `area : 0.20412` without one.
                m_area = re.match(r"area\s*:\s*([0-9eE+.\-]+)\s*;?", line)
                if m_area:
                    try:
                        current_cell["area"] = float(m_area.group(1))
                    except Exception:
                        pass
                # Scalar leakage (nangate/sky130/gf180/ihp). The scalar always wins over the
                # block-form below (it overwrites a block value if it appears).
                m_power = re.match(r"cell_leakage_power\s*:\s*([0-9eE+.\-]+)\s*;?", line)
                if m_power:
                    try:
                        current_cell["power"] = float(m_power.group(1))
                    except Exception:
                        pass
                # Block-form leakage: asap7 has `leakage_power () { ... value : X ; ... }`
                # blocks and NO scalar cell_leakage_power. Enter the block; only if no scalar
                # power was found do we adopt the FIRST block's `value` as the representative
                # leakage. Additive — platforms that carry a scalar are unaffected.
                if re.match(r"leakage_power\s*\(", line):
                    in_leakage = True
                    leak_depth = brace_depth + open_count
                if in_leakage and current_cell.get("power") is None:
                    # The value may be bare (asap7: `value : 549.659;`) or quoted
                    # (gf180: `value : "0.00029065" ;`) — strip optional quotes, same
                    # class as the sky130 quoted-cell-name bug (commit 363a8b2).
                    m_lv = re.search(r'\bvalue\s*:\s*"?\s*([0-9eE+.\-]+)\s*"?', line)
                    if m_lv:
                        try:
                            current_cell["power"] = float(m_lv.group(1))
                        except Exception:
                            pass

            # bus()/bundle() groups are parsed exactly like pin() groups: macro
            # liberty (fakeram45_*) declares direction/capacitance at the BUS
            # level with NO per-bit pin() members, while the DEF connects per-bit
            # (addr_in[3]). Storing the bus base name + the [idx]-fallback in
            # get_pin_info make those lookups resolve; without this every macro
            # bus pin classified 14/cap 0 (2026-07-06 nangate45 fakeram audit;
            # failure-patterns.md "Dataset-Extraction Silent-Value Defects" #11).
            m_pin = re.match(r"(?:pin|bus|bundle)\s*\(\s*([^)]+?)\s*\)\s*\{", line)
            if m_pin:
                pin_name = _strip_name_token(m_pin.group(1))
                current_pin = current_cell["pins"].setdefault(
                    pin_name,
                    {
                        "direction": "",
                        "capacitance": None,
                        "max_capacitance": None,
                        "clock": False,
                        "function": "",
                    },
                )
                current_pin_depth = brace_depth + open_count

            if current_pin is not None:
                # Simple attribute values may be QUOTED (sky130hd/hs write
                # `direction : "input";` and `clock : "true";`; ihp macro libs
                # `clock : "true" ;`) — same class as the sky130 quoted-value
                # bug (commit 363a8b2). The unquoted-only regexes here silently
                # dropped direction on every sky130 std-cell pin (pin_type_id
                # collapse + num_drivers/num_sinks undercount) and the clock
                # flag on every sky130 DFF — see failure-patterns.md
                # ("Label/feature extraction pitfalls", 2026-07-05).
                m_dir = re.match(r'direction\s*:\s*"?([A-Za-z_]+)"?\s*;', line)
                if m_dir:
                    current_pin["direction"] = m_dir.group(1).upper()
                m_cap = re.match(r'capacitance\s*:\s*"?([0-9eE+.\-]+)"?\s*;', line)
                if m_cap:
                    try:
                        current_pin["capacitance"] = float(m_cap.group(1)) * db["cap_scale_ff"]
                    except Exception:
                        pass
                m_max = re.match(r'max_capacitance\s*:\s*"?([0-9eE+.\-]+)"?\s*;', line)
                if m_max:
                    try:
                        current_pin["max_capacitance"] = float(m_max.group(1)) * db["cap_scale_ff"]
                    except Exception:
                        pass
                m_fn = re.match(r'function\s*:\s*"([^"]*)"\s*;', line)
                if m_fn:
                    current_pin["function"] = m_fn.group(1)
                if re.match(r'clock\s*:\s*"?true"?\s*;', line, flags=re.I):
                    current_pin["clock"] = True

        brace_depth += open_count - close_count

        if in_leakage and brace_depth < leak_depth:
            in_leakage = False
            leak_depth = -1
        if current_pin is not None and brace_depth < current_pin_depth:
            current_pin = None
            current_pin_depth = -1
        if current_cell is not None and brace_depth < current_cell_depth:
            current_cell = None
            current_cell_depth = -1
            current_pin = None
            current_pin_depth = -1
            in_leakage = False
            leak_depth = -1


def get_cell_area(master_name, lib_db):
    cell = lib_db.get("cells", {}).get(_norm_key(master_name))
    if cell and cell.get("area") is not None:
        return cell["area"]
    return 0.0


def get_cell_power(master_name, lib_db):
    cell = lib_db.get("cells", {}).get(_norm_key(master_name))
    if cell and cell.get("power") is not None:
        return cell["power"]
    return 0.0


def get_pin_info(master_name, pin_name, lib_db):
    pkey = _strip_name_token(pin_name)
    cell = lib_db.get("cells", {}).get(_norm_key(master_name), {})
    pins = cell.get("pins", {})
    info = pins.get(pkey)
    if info is not None:
        return info
    # Bus-member fallback: DEF/netlist pins are per-bit (`addr_in[3]`) while
    # macro liberty declares attributes once at the bus() level (`bus(addr_in)`)
    # — resolve the member to its bus base entry.
    m_bus = re.match(r"^(.*)\[\d+\]$", pkey)
    if m_bus:
        info = pins.get(m_bus.group(1))
        if info is not None:
            return info
    return {}


def get_pin_direction(master_name, pin_name, lib_db):
    info = get_pin_info(master_name, pin_name, lib_db)
    return (info.get("direction") or "").upper()


def get_pin_cap_fF(master_name, pin_name, lib_db):
    info = get_pin_info(master_name, pin_name, lib_db)
    cap = info.get("capacitance")
    if cap is not None:
        return float(cap)
    max_cap = info.get("max_capacitance")
    if max_cap is not None and get_pin_direction(master_name, pin_name, lib_db) == "OUTPUT":
        return float(max_cap)
    return 0.0


def get_pin_load_cap_fF(master_name, pin_name, lib_db):
    """Pin *load* capacitance only — 0.0 for pins without a liberty ``capacitance``.

    Unlike ``get_pin_cap_fF`` this NEVER falls back to an output pin's
    ``max_capacitance`` (a drive *limit*, not a load). Summed per net for
    ``sum_pin_cap_fF``, the fallback dominated the feature: measured on cordic
    nangate45 net _0062_, the true load is 3.19 fF but the summed value came out
    62.54 fF because NAND2_X1/ZN's max_capacitance (59.36 fF) was added in
    (2026-07-05 fix).
    """
    cap = get_pin_info(master_name, pin_name, lib_db).get("capacitance")
    return float(cap) if cap is not None else 0.0


# Physical-only / well-tap cell detection. The "TAP" substring matches Nangate
# (TAPCELL_X1), sky130 (...tapvpwrvgnd...), and asap7 (TAPCELL_ASAP7_...). Platforms
# whose well-tap/endcap masters don't contain "TAP" (gf180 uses __filltie / __endcap)
# get per-platform extras; R2G_TAP_PATTERNS (comma-separated) appends more.
_PLATFORM_TAP_EXTRA = {
    "gf180": ["FILLTIE", "ENDCAP"],
}


def _tap_patterns():
    pats = ["TAP"]
    plat = os.environ.get("R2G_PLATFORM", "").lower()
    pats += _PLATFORM_TAP_EXTRA.get(plat, [])
    extra = os.environ.get("R2G_TAP_PATTERNS", "")
    pats += [p.strip().upper() for p in extra.split(",") if p.strip()]
    return pats


def is_tap_master(master_name):
    key = _norm_key(master_name)
    return any(p in key for p in _tap_patterns())


def direction_id(s):
    t = (s or "").upper()
    if t == "INPUT":
        return 0
    if t == "OUTPUT":
        return 1
    if t == "INOUT":
        return 2
    if t == "FEEDTHRU":
        return 3
    return -1


def _looks_like_reset(name):
    n = _norm_key(name)
    return n.startswith("RST") or "RESET" in n or n in {"RN", "RESETN", "RSTN"}


def _looks_like_set(name):
    n = _norm_key(name)
    return n.startswith("SET") or n in {"SN", "SETN"}


def _looks_like_clock(name):
    n = _norm_key(name)
    return "CLK" in n or n in {"CK", "CP", "CLOCK"}


def _looks_like_scan(name):
    n = _norm_key(name)
    return n.startswith("SCAN") or n in {"SE", "SI", "SO"}


def _looks_like_select(name):
    n = _norm_key(name)
    return n in {"S", "S0", "S1", "SEL", "SELECT"}


def _looks_like_enable(name):
    n = _norm_key(name)
    return n in {"E", "EN", "OE", "TE", "GATE"}


def classify_pin_type(master_name, pin_name, lib_db, is_io=False):
    if is_io:
        return 14
    pname = _strip_name_token(pin_name)
    n = _norm_key(pname)
    info = get_pin_info(master_name, pin_name, lib_db)
    direction = (info.get("direction") or "").upper()
    if n in POWER_PIN_NAMES:
        return 12
    if n in GROUND_PIN_NAMES:
        return 13
    if direction in {"INOUT", "FEEDTHRU"}:
        return 11
    if info.get("clock") or _looks_like_clock(n):
        return 5
    if _looks_like_reset(n):
        return 6
    if _looks_like_set(n):
        return 7
    if _looks_like_enable(n):
        return 8
    if _looks_like_scan(n):
        return 9
    # Select is an INPUT concept: nangate45 FA_X1/HA_X1 declare the SUM output as
    # `pin (S) { direction : output }` — without the direction guard it lands on
    # id 10 instead of 4 (2026-07-06 audit F3). MUX2_X1's S (input) still gets 10.
    if _looks_like_select(n) and direction != "OUTPUT":
        return 10
    if direction == "INPUT":
        if n.startswith("A"):
            return 0
        if n.startswith("B"):
            return 1
        if n.startswith("C"):
            return 2
        return 3
    if direction == "OUTPUT":
        return 4
    return 14


def get_pin_abs_pos_um(inst_x_um, inst_y_um, orient, master_name, pin_name, geom=None):
    """Absolute pin position (um).

    With ``geom`` (from ``techlib.lef.macro_pin_geometry`` over the cell/macro
    LEFs) the pin is placed at the instance origin plus its orientation-aware
    in-cell offset — true intra-cell pin geometry (matters for macros and for a
    net's pin-position spread ``pin_x/y_std_um``). Without ``geom`` (no cell LEF
    resolvable) it falls back to the instance origin, the historical cell-origin
    approximation. See techlib.lef.pin_abs_pos_um.
    """
    if geom:
        from techlib import lef  # local import avoids any techlib import-order coupling
        return lef.pin_abs_pos_um(geom, inst_x_um, inst_y_um, orient, master_name, pin_name)
    return inst_x_um, inst_y_um


def infer_net_type_id(net_name, net_use="", is_clock=False):
    use = (net_use or "").upper()
    name = (net_name or "").lower()
    tokens = [t for t in re.split(r"[^a-z0-9]+", name) if t]
    if use == "POWER":
        return 1
    if use == "GROUND":
        return 2
    if use == "CLOCK" or is_clock:
        return 3
    if any("clk" in t or t == "clock" for t in tokens):
        return 3
    if "reset" in name or name.startswith("rst") or any(t.startswith("rst") or "reset" in t for t in tokens):
        return 4
    if "scan" in name or any(t.startswith("scan") or t in {"se", "si", "so", "test", "testmode"} for t in tokens):
        return 5
    return 0


def build_net_pin_stats(pin_points, pin_caps):
    xs = [p[0] for p in pin_points]
    ys = [p[1] for p in pin_points]
    return {
        "sum_cap_fF": sum(pin_caps),
        "x_std_um": _sample_std(xs),
        "y_std_um": _sample_std(ys),
    }
