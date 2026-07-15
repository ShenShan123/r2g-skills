"""techlib.lef — the single home for tech-LEF routing-layer parsing.

This module unifies the TWO tech-LEF parsers that historically lived independently:

  * congestion's ``parse_tech_lef`` (``scripts/extract/labels/extract_congestion.py``)
    — the LAYER / TYPE ROUTING / PITCH / DIRECTION block parser that yields per-layer
    pitch + preferred direction, with the nangate45 ``DEFAULT_LAYER_INFO`` fallback.
  * def_parse's ``parse_routing_layers`` / ``routing_layer_regex``
    (``scripts/extract/features/def_parse.py``) — routing-layer NAMES (declaration
    order) and the compiled ``\\b(<layer>|...)\\b`` matcher ``nodes_net`` consumes.

Both are ported here **verbatim in behavior** (byte-for-byte identical CSV output is
gated by tests/test_techlib_crossplatform.py once the workers are re-pointed in Tasks
7/8). Two complementary views of the same tech LEF:

  * NAMES  — ``routing_layers`` + ``routing_layer_regex`` (what wires/nets traverse).
  * PITCH/DIRECTION — ``routing_layer_info`` (track capacity per layer).

Pure stdlib.
"""
import os
import re


# Task 5 moves this to ``techlib.profile.fallback_routing_layers``; kept here until then.
# Copied verbatim from scripts/extract/labels/extract_congestion.py (nangate45 metal1..metal10).
DEFAULT_LAYER_INFO = {
    "metal1": {"pitch": 0.14, "direction": "HORIZONTAL"},
    "metal2": {"pitch": 0.19, "direction": "VERTICAL"},
    "metal3": {"pitch": 0.14, "direction": "HORIZONTAL"},
    "metal4": {"pitch": 0.28, "direction": "VERTICAL"},
    "metal5": {"pitch": 0.28, "direction": "HORIZONTAL"},
    "metal6": {"pitch": 0.28, "direction": "VERTICAL"},
    "metal7": {"pitch": 0.8, "direction": "HORIZONTAL"},
    "metal8": {"pitch": 0.8, "direction": "VERTICAL"},
    "metal9": {"pitch": 1.6, "direction": "HORIZONTAL"},
    "metal10": {"pitch": 1.6, "direction": "VERTICAL"},
}


def routing_layers(tech_lef):
    """Routing-layer names from a tech LEF (``LAYER <n> ... TYPE ROUTING ;`` blocks).

    Used to count distinct routing layers a net traverses across any platform
    (nangate ``metal1..metal10``, sky130 ``li1``/``met1..met5``, asap7 ``M1..M9``).
    Returns a list in declaration order; empty if the LEF is missing/unparseable.

    Ported verbatim from ``scripts/extract/features/def_parse.py:parse_routing_layers``.
    """
    if not tech_lef or not os.path.isfile(tech_lef):
        return []
    layers = []
    cur = None
    with open(tech_lef, "r") as f:
        for raw in f:
            s = raw.strip()
            m = re.match(r"LAYER\s+(\S+)", s)
            if m:
                cur = m.group(1)
                continue
            if cur is not None and re.search(r"\bTYPE\s+ROUTING\b", s):
                if cur not in layers:
                    layers.append(cur)
            if s.startswith("END") and cur and s.split()[-1] == cur:
                cur = None
    return layers


def routing_layer_regex(tech_lef):
    """Compiled ``\\b(<layer>|...)\\b`` matcher from the tech LEF routing layers.

    Falls back to the platform-agnostic ``metal\\d+`` pattern when the LEF yields no
    routing layers (logged by the caller). Word boundaries make full-token matches exact
    (``metal1`` never matches inside ``metal10``); alternatives are sorted longest-first.
    Note: the no-layer FALLBACK pattern ``(metal\\d+)`` intentionally OMITS the ``\\b``
    word boundaries (it relies on ``\\d+`` greediness), faithfully matching the original.

    Returns ``(compiled_regex, from_lef_bool)``. Ported verbatim from
    ``scripts/extract/features/def_parse.py:routing_layer_regex`` (built on
    ``routing_layers`` here, which == that module's ``parse_routing_layers``).
    ``nodes_net`` consumes this in Task 7, so it must stay byte-faithful.
    """
    layers = routing_layers(tech_lef)
    if not layers:
        return re.compile(r"(metal\d+)", re.IGNORECASE), False
    alt = "|".join(re.escape(n) for n in sorted(layers, key=len, reverse=True))
    return re.compile(r"\b(" + alt + r")\b", re.IGNORECASE), True


def macro_sizes(lef_path):
    """Per-MACRO physical footprint ``SIZE`` from a cell/macro LEF.

    Returns ``{macro_name: (width_um, height_um)}`` for every
    ``MACRO <name> ... SIZE <w> BY <h> ; ... END <name>`` block (widths/heights in
    microns, exactly as the LEF declares them). Empty dict if the LEF is
    missing/unparseable; a MACRO without a parseable SIZE is simply omitted.

    The congestion label extractor uses these to build each placed instance's
    orientation-aware bounding box, so it can average the routing-congestion of
    **every GCell the cell footprint overlaps** (the new bbox mapping) rather than
    only the GCell under the placement origin. Pass the standard-cell LEF (ORFS
    ``SC_LEF``) plus any macro LEFs; call once per file and merge (later files win).

    Pure stdlib.
    """
    sizes = {}
    if not lef_path or not os.path.isfile(lef_path):
        return sizes
    current = None
    with open(lef_path, "r") as f:
        for raw in f:
            parts = raw.replace(";", " ").split()
            if not parts:
                continue
            if parts[0] == "MACRO" and len(parts) >= 2:
                current = parts[1]
                continue
            if current is None:
                continue
            if parts[0] == "SIZE":
                # "SIZE <w> BY <h>" — tolerate stray tokens by keying off BY.
                try:
                    by = parts.index("BY")
                    sizes[current] = (float(parts[by - 1]), float(parts[by + 1]))
                except (ValueError, IndexError):
                    pass
            elif parts[0] == "END" and len(parts) >= 2 and parts[1] == current:
                current = None
    return sizes


def merge_macro_sizes(lef_paths):
    """``macro_sizes`` unioned over several LEFs (SC_LEF + macro LEFs).

    Later paths override earlier ones on a name clash. Non-existent / empty
    entries are skipped silently. Returns ``{macro: (w_um, h_um)}``.
    """
    merged = {}
    for p in lef_paths:
        if not p:
            continue
        merged.update(macro_sizes(p))
    return merged


def _norm_lef_key(s):
    """Normalize a LEF MACRO/PIN token to its match key (strip escaping, upper)."""
    return (s or "").strip().lstrip("\\").upper()


def cell_lef_paths():
    """Cell/macro LEF paths (per-MACRO SIZE **and** PIN geometry) from the env.

    ``run_features.sh`` / ``run_labels.sh`` export ``SC_LEF`` (standard-cell LEF)
    and ``ADDITIONAL_LEFS`` (macro LEFs) straight from resolve_platform_paths.sh;
    ``CELL_LEFS`` is an explicit override. Whitespace-separated; the caller
    (``merge_macro_sizes`` / ``macro_pin_geometry``) existence-checks each path.
    Kept here (not inline in a worker) so the congestion label worker and the
    feature workers resolve the SAME cell LEFs — CLAUDE.md "fix a parse bug ONCE".
    """
    out = []
    for var in ("SC_LEF", "CELL_LEFS", "ADDITIONAL_LEFS"):
        val = os.environ.get(var, "")
        if val:
            out.extend(t for t in val.split() if t)
    # De-dup, preserving order.
    seen, uniq = set(), []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def apply_orient(px, py, orient, cell_w, cell_h):
    """Transform a LEF pin-relative coord ``(px, py)`` by a DEF placement
    ``orient`` (OpenDB 8-orientation set) and re-normalize to a (0,0) origin,
    given the cell footprint ``cell_w`` x ``cell_h``.

    Validated against OpenDB placed pin locations (cordic sky130hs: N + FS cover
    all 5105 insts; FS=MX matched 2488/2488 after fixing the FN/FS swap the
    RTL2Graph original shipped — its comments named MY/MX but the returned
    expressions were transposed). Correct map: N=R0, S=R180, W=R90, E=R270,
    FN=MY (reflect X), FS=MX (reflect Y), FW=MYR90 (y,x), FE=MXR90 (h-y,w-x).
    """
    o = (orient or "N").upper()
    if o == "N":      # R0
        return (px, py)
    if o == "S":      # R180
        return (cell_w - px, cell_h - py)
    if o == "W":      # R90
        return (cell_h - py, px)
    if o == "E":      # R270
        return (py, cell_w - px)
    if o == "FN":     # MY = mirror about Y-axis (reflect X)
        return (cell_w - px, py)
    if o == "FS":     # MX = mirror about X-axis (reflect Y)
        return (px, cell_h - py)
    if o == "FW":     # MYR90
        return (py, px)
    if o == "FE":     # MXR90
        return (cell_h - py, cell_w - px)
    return (px, py)


def macro_pin_geometry(lef_paths):
    """Per-MACRO footprint + pin-center geometry from cell/macro LEF(s).

    Returns ``{MACRO_UPPER: {"width": w_um, "height": h_um,
    "pins": {PIN_UPPER: (cx_um, cy_um)}}}`` where each pin center is the bbox
    centroid of that pin's ``RECT``/``POLYGON`` port geometry, in the cell's own
    (un-oriented) coordinate frame. Later paths override earlier ones on a name
    clash. Empty dict if no path is readable.

    In LEF every cell (std cell OR hard macro) is a ``MACRO`` block, so one parse
    over ``SC_LEF`` + macro LEFs yields pin offsets for both — which is what makes
    ``pin_x/y_std_um`` (a net's pin-position spread) real intra-cell geometry
    instead of collapsing every pin onto the instance origin (RTL2Graph
    ``lib_db._parse_lef_macros`` port, feature_test_v4). Pure stdlib.
    """
    geom = {}
    for path in lef_paths or []:
        if path and os.path.isfile(path):
            _parse_one_lef_geometry(path, geom)
    return geom


_RECT_RE = re.compile(r"\bRECT\b(.*?);")
_POLY_RE = re.compile(r"\bPOLYGON\b(.*?);")


def _floats(text):
    out = []
    for tok in text.replace(";", " ").split():
        try:
            out.append(float(tok))
        except ValueError:
            pass  # skip non-numeric tokens (e.g. a MASK keyword)
    return out


def _parse_one_lef_geometry(path, geom):
    current_macro = None
    current_pin = None
    xs, ys = [], []

    def _flush_pin():
        if current_macro is not None and current_pin is not None and xs:
            geom[current_macro]["pins"][current_pin] = (
                (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0)

    with open(path, "r") as f:
        for raw in f:
            s = raw.strip()
            if not s:
                continue
            m = re.match(r"MACRO\s+(\S+)", s)
            if m:
                _flush_pin()
                current_macro = _norm_lef_key(m.group(1))
                geom[current_macro] = {"width": 0.0, "height": 0.0, "pins": {}}
                current_pin, xs, ys = None, [], []
                continue
            if current_macro is None:
                continue
            m = re.search(r"SIZE\s+([0-9.eE+\-]+)\s+BY\s+([0-9.eE+\-]+)", s)
            if m:
                geom[current_macro]["width"] = float(m.group(1))
                geom[current_macro]["height"] = float(m.group(2))
                continue
            m = re.match(r"PIN\s+(\S+)", s)
            if m:
                _flush_pin()
                current_pin = _norm_lef_key(m.group(1))
                xs, ys = [], []
                continue
            if current_pin is not None:
                for rm in _RECT_RE.finditer(s):
                    nums = _floats(rm.group(1))
                    # RECT coords are the last 4 floats (tolerates a MASK prefix).
                    if len(nums) >= 4:
                        x1, y1, x2, y2 = nums[-4:]
                        xs.extend([x1, x2])
                        ys.extend([y1, y2])
                for pm in _POLY_RE.finditer(s):
                    nums = _floats(pm.group(1))
                    if len(nums) % 2:      # odd -> leading MASK id; polygon coords pair up
                        nums = nums[1:]
                    for k in range(0, len(nums) - 1, 2):
                        xs.append(nums[k])
                        ys.append(nums[k + 1])
                m = re.match(r"END\s+(\S+)", s)
                if m and _norm_lef_key(m.group(1)) == current_pin:
                    _flush_pin()
                    current_pin, xs, ys = None, [], []
                    continue
            m = re.match(r"END\s+(\S+)", s)
            if m and _norm_lef_key(m.group(1)) == current_macro:
                _flush_pin()
                current_macro, current_pin, xs, ys = None, None, [], []


def pin_abs_pos_um(geom, inst_x_um, inst_y_um, orient, master_name, pin_name):
    """Absolute pin position (um): instance origin + the pin's oriented in-cell
    offset from ``macro_pin_geometry``. Falls back to the instance origin when
    the geometry is absent (no cell LEF) or the master/pin is unknown."""
    if geom:
        mac = geom.get(_norm_lef_key(master_name))
        if mac:
            center = mac["pins"].get(_norm_lef_key(pin_name))
            if center:
                px, py = apply_orient(center[0], center[1], orient,
                                      mac["width"], mac["height"])
                return (inst_x_um + px, inst_y_um + py)
    return (inst_x_um, inst_y_um)


def routing_layer_info(tech_lef, fallback=None):
    """Parse routing-layer pitch/direction from a tech LEF.

    Recognizes any layer declared TYPE ROUTING (platform-agnostic — nangate
    metal*, sky130 met*/li1, asap7 M*). Falls back to ``fallback`` (defaulting to
    the module-level ``DEFAULT_LAYER_INFO``, i.e. the nangate45 layer table) with a
    warning when the LEF is absent or declares no routing layers.

    Ported verbatim from ``scripts/extract/labels/extract_congestion.py:parse_tech_lef``;
    the only addition is the injectable ``fallback`` (``None`` => ``DEFAULT_LAYER_INFO``)
    so Task 5 can swap in a platform-aware fallback without behavior drift.
    """
    if fallback is None:
        fallback = DEFAULT_LAYER_INFO

    # os.path.exists (not isfile) to match extract_congestion.parse_tech_lef verbatim
    if not tech_lef or not os.path.exists(tech_lef):
        print(f"WARNING: tech LEF not found ({tech_lef}); using nangate45 DEFAULT_LAYER_INFO")
        return fallback

    layers = {}
    current = None
    block = {}

    def _finalize():
        if block.get("type") == "ROUTING" and block.get("pitch_vals") and block.get("direction"):
            pv = block["pitch_vals"]
            direction = block["direction"]
            if len(pv) >= 2:
                pitch = pv[1] if direction == "HORIZONTAL" else pv[0]
            else:
                pitch = pv[0]
            if pitch > 0:
                layers[current] = {"pitch": pitch, "direction": direction}

    with open(tech_lef, "r") as f:
        for raw_line in f:
            parts = raw_line.replace(";", " ").split()
            if not parts:
                continue
            if parts[0] == "LAYER" and len(parts) >= 2:
                current = parts[1]
                block = {"pitch_vals": [], "direction": None, "type": None}
                continue
            if current is None:
                continue
            if parts[0] == "END":
                _finalize()
                current = None
                block = {}
                continue
            if parts[0] == "TYPE" and len(parts) >= 2:
                block["type"] = parts[1].upper()
            elif parts[0] == "PITCH":
                for tok in parts[1:]:
                    try:
                        block["pitch_vals"].append(float(tok))
                    except ValueError:
                        pass
            elif parts[0] == "DIRECTION" and len(parts) >= 2:
                block["direction"] = parts[1].upper()

    if not layers:
        print("WARNING: no TYPE ROUTING layers parsed; using nangate45 DEFAULT_LAYER_INFO")
        return fallback
    return layers
