"""The one DEF / SDC parser for the extract stage (techlib).

This module is the consolidation target for the DEF/SDC parsing logic that was
historically duplicated between the feature workers
(``scripts/extract/features/def_parse.py``) and the label extractors
(``scripts/extract/labels/extract_{wirelength,congestion}.py``).

Everything below ``route_segments`` is copied **verbatim (behavior byte-for-byte
identical)** from ``scripts/extract/features/def_parse.py`` — the feature workers
and label extractors are re-pointed here in a later task, and any drift would
break the cross-platform byte-for-byte gate (tests/test_techlib_crossplatform.py).

``route_segments`` is the new dedup target: a single coordinate-chain walker that
reproduces the ``*``-relative semantics that both the wirelength extractor
(token-walk in ``parse_def_wirelength``) and the congestion extractor
(regex + walk in ``extract_grid_demand``) currently re-implement independently.

Hand-rolled line parsers (no DEF grammar library) keyed to ORFS ``write_def``
output. Pure stdlib.
"""
import os
import re

_PLACE_RE = re.compile(
    r"\+\s*(PLACED|FIXED)\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)\s*(N|S|E|W|FN|FS|FE|FW)"
)
_PAIR_RE = re.compile(r"\(\s*([^\s()]+)\s+([^\s()]+)\s*\)")
_INT_RE = re.compile(r"^-?\d+$")

# Point extraction inside a DEF routing statement: the FIRST TWO tokens inside
# each `( ... )`, ignoring any trailing via/layer token (e.g. `( x y via12 )`).
# This is the regex congestion's extract_grid_demand uses; wirelength's
# token-walk ("2 tokens after each `(`") yields the identical point sequence.
_ROUTE_POINT_RE = re.compile(r"\(\s*([^\s\)]+)\s+([^\s\)]+)(?:\s+[^\)]*)?\s*\)")


def parse_units(def_path):
    """Database units per micron from the DEF ``UNITS DISTANCE MICRONS`` line."""
    dbu = 1
    with open(def_path, "r") as f:
        for line in f:
            if "UNITS" in line and "MICRONS" in line:
                nums = re.findall(r"\d+", line)
                if nums:
                    dbu = int(nums[-1])
                break
    return dbu


def parse_design_name(def_path):
    """Top-level design name from the DEF ``DESIGN <name> ;`` line."""
    with open(def_path, "r") as f:
        for line in f:
            s = line.strip()
            m = re.match(r"DESIGN\s+(\S+)\s*;", s)
            if m:
                return m.group(1)
    return "TOP"


def _apply_place_info(dst, text):
    if "+ UNPLACED" in text:
        dst["status"] = "UNPLACED"
        return
    m = _PLACE_RE.search(text)
    if m:
        dst["status"] = m.group(1).upper()
        dst["x"] = int(m.group(2))
        dst["y"] = int(m.group(3))
        dst["orient"] = m.group(4).upper()


def parse_components(def_path):
    """Ordered ``{inst: {master, status, orient, x, y}}`` over the COMPONENTS section.

    Insertion order == DEF declaration order (nodes_gate emits one row per component in
    that order). ``x``/``y`` default to ``None`` and ``status``/``orient`` to ``""`` —
    callers coerce ``None`` to ``0`` (``(x or 0) / dbu``).
    """
    comps = {}
    in_comps = False
    cur_inst = None
    with open(def_path, "r") as f:
        for raw in f:
            s = raw.strip()
            if s.startswith("COMPONENTS"):
                in_comps = True
                continue
            if in_comps and s.startswith("END COMPONENTS"):
                break
            if not in_comps:
                continue
            if s.startswith("-"):
                parts = s.split()
                if len(parts) >= 3:
                    cur_inst = parts[1]
                    comps[cur_inst] = {
                        "master": parts[2], "status": "", "orient": "",
                        "x": None, "y": None,
                    }
                    _apply_place_info(comps[cur_inst], s)
                else:
                    cur_inst = None
                continue
            if cur_inst and ("+ PLACED" in s or "+ FIXED" in s or "+ UNPLACED" in s):
                _apply_place_info(comps[cur_inst], s)
    return comps


def parse_components_master(def_path):
    """Lightweight ``{inst: master}`` view (edge workers only need the master)."""
    masters = {}
    in_comps = False
    with open(def_path, "r") as f:
        for raw in f:
            s = raw.strip()
            if s.startswith("COMPONENTS"):
                in_comps = True
                continue
            if in_comps and s.startswith("END COMPONENTS"):
                break
            if not in_comps:
                continue
            if s.startswith("-"):
                parts = s.split()
                if len(parts) >= 3:
                    masters[parts[1]] = parts[2]
    return masters


def parse_nets(def_path):
    """Ordered ``{name: {name, conns, routes, use}}`` over the NETS section.

    ``conns`` is the list of ``(inst, pin)`` instance-pin pairs (top-level ports use the
    sentinel inst ``PIN``); integer coordinate pairs are filtered out. ``routes`` holds
    the raw routing lines, ``use`` the ``+ USE`` value. Reproduces the original
    nodes_net parser; edge/pin workers consume only ``conns``.
    """
    nets = {}
    in_nets = False
    cur = None
    in_conn_list = False
    with open(def_path, "r") as f:
        for raw in f:
            s = raw.strip()
            if s.startswith("NETS"):
                in_nets = True
                continue
            if in_nets and s.startswith("END NETS"):
                if cur:
                    nets[cur["name"]] = cur
                break
            if not in_nets:
                continue
            if s.startswith("-"):
                if cur:
                    nets[cur["name"]] = cur
                parts = s.split()
                name = parts[1] if len(parts) >= 2 else ""
                cur = {"name": name, "conns": [], "routes": [], "use": ""}
                in_conn_list = True
                for a, b in _PAIR_RE.findall(s):
                    if _INT_RE.match(a) and _INT_RE.match(b):
                        continue
                    cur["conns"].append((a, b))
                continue
            if cur is None:
                continue
            if s.startswith("+") or s.startswith("NEW") or s.startswith("ROUTED") or s.startswith("FIXED"):
                in_conn_list = False
            m_use = re.search(r"\+\s*USE\s+(\S+)", s)
            if m_use:
                cur["use"] = m_use.group(1).upper().rstrip(";")
            # Capture routing-statement lines (for layer extraction) by a leading routing
            # KEYWORD, not by a substring: a wrapped connection line's instance/pin name
            # may itself contain "ROUTED" (e.g. `( u_ROUTED_x B )`), and a substring match
            # would divert that connection line to routes and silently drop its pairs.
            toks = s.split()
            kw = toks[1] if (len(toks) > 1 and toks[0] == "+") else (toks[0] if toks else "")
            if kw in ("ROUTED", "NEW", "FIXED", "COVER"):
                cur["routes"].append(s)
                continue
            if in_conn_list and "(" in s and ")" in s:
                for a, b in _PAIR_RE.findall(s):
                    if _INT_RE.match(a) and _INT_RE.match(b):
                        continue
                    cur["conns"].append((a, b))
    return nets


def parse_routing_layers(tech_lef):
    """Routing-layer names from a tech LEF (``LAYER <n> ... TYPE ROUTING ;`` blocks).

    Used to count distinct routing layers a net traverses across any platform
    (nangate ``metal1..metal10``, sky130 ``li1``/``met1..met5``, asap7 ``M1..M9``).
    Returns a list in declaration order; empty if the LEF is missing/unparseable.
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
    """
    layers = parse_routing_layers(tech_lef)
    if not layers:
        return re.compile(r"(metal\d+)", re.IGNORECASE), False
    alt = "|".join(re.escape(n) for n in sorted(layers, key=len, reverse=True))
    return re.compile(r"\b(" + alt + r")\b", re.IGNORECASE), True


def _strip_inline_comment(s):
    if "#" in s:
        return s.split("#", 1)[0].strip()
    return s.strip()


def parse_sdc_clock_port_names(sdc_path):
    """Set of clock port names from ``create_clock ... [get_ports <port>]`` in the SDC.

    Resolves ``$var`` references defined via a preceding ``set <var> <value>``.
    """
    if not sdc_path or not os.path.isfile(sdc_path):
        return set()
    vars_str = {}
    ports = set()
    with open(sdc_path, "r") as f:
        for raw in f:
            line = _strip_inline_comment(raw)
            if not line:
                continue
            m_set = re.match(r"^\s*set\s+([A-Za-z_][A-Za-z0-9_]*)\s+(\S+)\s*$", line)
            if m_set:
                var = m_set.group(1)
                val = m_set.group(2).strip().strip('"').strip("'")
                vars_str[var] = val
                continue
            if "create_clock" not in line:
                continue
            m_gp = re.search(r"get_ports\s+([^\]\s]+)", line)
            if not m_gp:
                continue
            tok = m_gp.group(1).strip()
            if tok.startswith("$"):
                tok = vars_str.get(tok[1:], "")
            tok = tok.strip("{}").strip()
            if tok:
                ports.add(tok)
    return ports


# --------------------------------------------------------------------------- #
# route_segments — the single coordinate-chain walker (dedup target).         #
# --------------------------------------------------------------------------- #
def route_segments(route_line):
    """Yield consecutive integer ``(x1, y1, x2, y2)`` segments for one DEF route line.

    Reproduces the ``*``-relative coordinate-chain semantics that both label
    extractors currently re-implement independently:

      * wirelength (``parse_def_wirelength``): walk the points, ``*`` means
        "unchanged from previous"; the FIRST point must be explicit — if it is
        non-integer (e.g. ``*``) the whole route line is skipped
        (``try: curr_x=int(p0[0]) ... except ValueError: continue``).
      * congestion (``extract_grid_demand``): same regex point extraction
        (first two tokens inside each ``( ... )``, trailing via/layer ignored)
        and the same ``*``-chain walk feeding ``add_route_segment``.

    Behavior contract (matched exactly against both originals):
      * Points = first two tokens inside each ``( ... )`` (``_ROUTE_POINT_RE``);
        any trailing via/layer token is ignored.
      * Fewer than 2 points  -> yields nothing (single-point or point-less line).
      * First point non-integer (``*`` chain start, or garbage) -> yields nothing
        for the whole line (matches wirelength's ``continue``).
      * For each subsequent point, ``*`` carries the previous coordinate forward;
        a non-integer non-``*`` token also carries the previous value forward
        (matches wirelength's ``try: next_x=int(...) except ValueError: pass``).
      * Segments are emitted even when zero-length (x1==x2 and y1==y2); callers
        that care (congestion's ``add_route_segment``) already guard on
        ``x1!=x2`` / ``y1!=y2`` themselves, so emitting them is faithful to the
        per-point walk and lets wirelength sum ``abs(dx)+abs(dy)`` over every step.

    Coordinates are returned as ``int`` (DBU); callers apply the dbu division.
    """
    points = _ROUTE_POINT_RE.findall(route_line)
    if len(points) < 2:
        return

    # First point must be explicit (matches wirelength's int()-or-skip on p0).
    try:
        curr_x = int(points[0][0])
        curr_y = int(points[0][1])
    except ValueError:
        return

    for x_str, y_str in points[1:]:
        next_x = curr_x
        next_y = curr_y
        if x_str != "*":
            try:
                next_x = int(x_str)
            except ValueError:
                pass
        if y_str != "*":
            try:
                next_y = int(y_str)
            except ValueError:
                pass
        yield (curr_x, curr_y, next_x, next_y)
        curr_x = next_x
        curr_y = next_y


def iter_route_segments(routes):
    """Flatten ``route_segments`` over an iterable of route lines.

    Convenience over ``parse_nets(...)[net]["routes"]`` (or any list of raw
    routing-statement strings): yields every ``(x1, y1, x2, y2)`` segment across
    all lines, in order.
    """
    for line in routes:
        yield from route_segments(line)
