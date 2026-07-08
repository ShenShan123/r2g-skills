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
