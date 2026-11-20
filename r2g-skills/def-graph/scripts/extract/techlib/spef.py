"""Shared SPEF (Standard Parasitic Exchange Format) parser for the RC label stage.

OpenRCX / ORFS `write_spef` output is parsed here ONCE for every consumer, so the
three former inline copies (extract_rcx.py summary, metadata.py C_total,
nodes_pin.py io-cap) and the new per-net RC *label* extractor (extract_rc.py) all
share one ground-truthed implementation instead of drifting apart.

What it extracts, per signal net, for the RC-label stage (see
references/label-extraction.md "RC parasitic labels"):

  * ground capacitance  -- Sum of the grounded (2-arg) ``*CAP`` entries in the
    net's block, in fF. A NET-node label.
  * coupling capacitance -- Sum of the cross-net coupling (3-arg) ``*CAP``
    entries between a pair of nets, in fF. A net-PAIR edge label.
  * equivalent resistance -- reduced resistance between two PINS of the same net,
    computed pure-Python from the net's ``*RES`` segment tree (path resistance;
    exact for the radial trees OpenRCX emits for signal nets). A pin-PAIR edge
    label. (No numpy: the label workers run under base python3, which has no
    numpy/scipy -- the same reason the congestion Gaussian is pure-Python.)

SPEF cross-platform gotchas this handles (verified on real nangate45 gcd +
sky130hd apb_master SPEFs, 2026-07-07):

  * Units: ``*C_UNIT 1 PF`` / ``*R_UNIT 1 OHM`` -> scale to fF / Ohm. FF/PF/NF/UF
    and OHM/KOHM/MOHM understood.
  * ``*NAME_MAP`` integer aliases (``*1141`` -> instance/net name). nangate45
    leaves top ports UNALIASED (bare ``clk``); sky130hd ALIASES the net that
    shares a port name (``*2 paddr[0]``) but still writes the port node bare
    (``paddr[0]``) inside ``*CAP``. So a node token is resolved three ways:
    ``*<netid>:sub`` (net-internal node) / ``*<instid>:pin`` (instance pin) /
    bare name (top port).
  * A node's owning net: grounded-cap and RES nodes are LOCAL to the block's net;
    a coupling partner may live on ANOTHER net -> resolved via net-id / pin->net /
    port->net maps built in a first pass.
"""
from __future__ import annotations

import os
import re


# --- unit scaling ----------------------------------------------------------

def _cap_scale_to_fF(mag: float, unit: str) -> float:
    u = unit.upper()
    if u in ("FF", "FEMTOFARAD", "FEMTOFARADS"):
        return mag
    if u in ("PF", "PICOFARAD", "PICOFARADS"):
        return mag * 1e3
    if u in ("NF", "NANOFARAD", "NANOFARADS"):
        return mag * 1e6
    if u in ("UF", "MICROFARAD", "MICROFARADS"):
        return mag * 1e9
    return mag  # unknown unit: treat as-is (already fF-like)


def _res_scale_to_ohm(mag: float, unit: str) -> float:
    u = unit.upper()
    if u in ("OHM", "OHMS"):
        return mag
    if u in ("KOHM", "KILOOHM", "KILOOHMS"):
        return mag * 1e3
    if u in ("MOHM", "MEGOHM", "MEGAOHM", "MEGAOHMS"):
        return mag * 1e6
    return mag


_CUNIT_RE = re.compile(r"^\*C_UNIT\s+([0-9eE+.\-]+)\s+(\S+)\s*$")
_RUNIT_RE = re.compile(r"^\*R_UNIT\s+([0-9eE+.\-]+)\s+(\S+)\s*$")

# SPEF (write_spef) escapes '.', '$', ':', etc. with a backslash; the DEF
# (write_def / techlib.def_parse) escapes the bus brackets '[' ']' AND a literal
# '/' inside a name ('\/', which distinguishes it from the hierarchy divider),
# leaving '.'/'$' bare. So a SPEF net/inst name like `a\.b\[0\]\$_DFF_` must be
# de-escaped to `a.b\[0\]$_DFF_` to JOIN the DEF-derived feature CSVs
# (nodes_net/nodes_pin). Verified: this drives the RC->feature-CSV join from
# ~79-92% to 100% on aes_core (sky130hd) — otherwise every hierarchical net +
# double-bus register silently loses its RC labels (2026-07-07, failure-patterns.md).
# 2026-11-07: '\/' is kept too (failure-patterns.md #66). De-escaping it to '/'
# dropped the RC labels of every net with a literal slash in its name (yosys
# `$func$/path/file.v:193$...` nets): 59 of 4,886 nets on pilot500 C00180.
_ESC_NONBRACKET = re.compile(r"\\([^\[\]/])")


def _deesc(name: str) -> str:
    """SPEF-escaped name -> DEF/def_parse convention (strip backslash except
    before bus brackets '[' ']' and a literal-slash escape '\\/')."""
    return _ESC_NONBRACKET.sub(r"\1", name) if "\\" in name else name


def _split_node(token: str):
    """A SPEF node token -> (base, sub). ``*1141:D`` -> (``*1141``, ``D``);
    bare ``paddr[0]`` -> (``paddr[0]``, None). Splits on the FIRST ':' only so a
    hierarchical ``:`` inside a name is preserved in ``sub``."""
    if ":" in token:
        base, sub = token.split(":", 1)
        return base, sub
    return token, None


class SpefData:
    """Parsed SPEF ready for the RC-label stage. Names are DEF-escaped exactly as
    the SPEF writes them (which is how nodes_net/nodes_pin key their CSV rows)."""

    def __init__(self):
        self.cap_scale_ff = 1.0
        self.res_scale_ohm = 1.0
        self.id2name: dict[str, str] = {}
        # aggregates keyed by resolved net name
        self.net_ground_cap_ff: dict[str, float] = {}
        self.coupling_cap_ff: dict[tuple[str, str], float] = {}  # (netA<netB) -> fF
        self.net_total_cap_ff: dict[str, float] = {}             # *D_NET header cap
        # per-net structure for equivalent-resistance reduction
        self.net_res_segments: dict[str, list[tuple[str, str, float]]] = {}
        self.net_pin_token2key: dict[str, dict[str, tuple[str, str]]] = {}
        self.net_driver: dict[str, tuple[str, str] | None] = {}
        self.nets: list[str] = []

    # -- equivalent resistance (pure-Python tree reduction) ------------------
    def equiv_res_pairs(self, net: str, max_fanout: int = 0):
        """All-pairs equivalent resistance (Ohm) between the net's PIN nodes.

        Returns a list of ``(keyA, keyB, ohm)`` with keyA < keyB (each key =
        (inst, pin), or ("PIN", port) for a top-level port). Effective resistance
        is the resistance along the unique path in the net's ``*RES`` tree; for the
        rare non-tree net (a ``*RES`` graph with cycles) it is the resistance along
        the traversal spanning tree (parallel paths ignored).

        ``max_fanout``>0 skips nets with more than that many pins (a runaway guard;
        signal nets are small because clock/PG nets are filtered from the graph).
        Returns the dict {"skipped": n_pins} (NOT a list) when a net is skipped, so
        the caller can log it -- a plain function, never a generator, precisely so
        this sentinel is not swallowed by StopIteration."""
        segs = self.net_res_segments.get(net, [])
        tok2key = self.net_pin_token2key.get(net, {})
        if not segs or len(tok2key) < 2:
            return []
        # adjacency over SPEF node tokens
        adj: dict[str, list[tuple[str, float]]] = {}
        for u, v, r in segs:
            adj.setdefault(u, []).append((v, r))
            adj.setdefault(v, []).append((u, r))
        pin_tokens = [t for t in tok2key if t in adj]
        if len(pin_tokens) < 2:
            return []
        if max_fanout and len(pin_tokens) > max_fanout:
            return {"skipped": len(pin_tokens)}
        # resistance from each pin token to every reachable node (tree path sum)
        out = []
        emitted = set()
        for src in pin_tokens:
            dist = {src: 0.0}
            stack = [(src, None)]
            while stack:
                node, parent = stack.pop()
                for nb, r in adj[node]:
                    if nb == parent or nb in dist:
                        continue  # cycle: keep the first (spanning-tree) path
                    dist[nb] = dist[node] + r
                    stack.append((nb, node))
            for dst in pin_tokens:
                if dst == src or dst not in dist:
                    continue
                a, b = sorted((tok2key[src], tok2key[dst]))
                if (a, b) in emitted:
                    continue
                emitted.add((a, b))
                out.append((a, b, dist[dst]))
        return out


def _pinkey_from_conn(tokens, id2name):
    """A ``*CONN`` line -> (pin_token, pinkey, direction) or None.

    ``*I *1141:D I *D DFF_X2`` -> ("*1141:D", (inst_name, "D"), "I")
    ``*P paddr[0] O``          -> ("paddr[0]", ("PIN", "paddr[0]"), "O")
    """
    if not tokens:
        return None
    kind = tokens[0]
    if kind == "*I" and len(tokens) >= 3:
        tok = tokens[1]
        base, sub = _split_node(tok)
        inst = id2name.get(base, _deesc(base.lstrip("*")))  # id2name already de-escaped
        return tok, (inst, _deesc(sub) if sub is not None else ""), tokens[2].upper()
    if kind == "*P" and len(tokens) >= 3:
        tok = tokens[1]
        name = id2name.get(tok, _deesc(tok))  # ports are usually bare, but be tolerant
        return tok, ("PIN", name), tokens[2].upper()
    return None


def _pick_driver(pins):
    """pins: list of (pinkey, dir, token). Driver = an instance OUTPUT pin (dir
    'O'), else a top INPUT port (a *P with dir 'I' drives the net internally),
    else the first pin. Returns pinkey or None."""
    inst_out = None
    port_in = None
    for pinkey, d, _tok in pins:
        if pinkey[0] != "PIN" and d == "O" and inst_out is None:
            inst_out = pinkey
        elif pinkey[0] == "PIN" and d == "I" and port_in is None:
            port_in = pinkey
    if inst_out is not None:
        return inst_out
    if port_in is not None:
        return port_in
    return pins[0][0] if pins else None


def parse_spef(spef_path: str) -> SpefData | None:
    """Parse a SPEF into aggregated RC-label data, or None if the file is absent.

    Two streamed passes (memory-light -- no full-file buffering): pass 1 builds
    the name map, net-id set, and pin/port->net maps; pass 2 aggregates the
    ground/coupling caps and RES segments using those maps."""
    if not spef_path or not os.path.isfile(spef_path):
        return None

    data = SpefData()

    # ---- pass 1: name map, id classification, connectivity ------------------
    net_ids: set[str] = set()
    inst_ids: set[str] = set()
    pin_to_net: dict[tuple[str, str], str] = {}
    port_to_net: dict[str, str] = {}
    net_pins: dict[str, list] = {}

    in_name_map = False
    current_net = None
    section = None  # 'CONN' | 'CAP' | 'RES' | None
    with open(spef_path, "r", encoding="utf-8", errors="ignore") as f:
        for raw in f:
            s = raw.strip()
            if not s:
                continue
            m = _CUNIT_RE.match(s)
            if m:
                data.cap_scale_ff = _cap_scale_to_fF(float(m.group(1)), m.group(2))
                continue
            m = _RUNIT_RE.match(s)
            if m:
                data.res_scale_ohm = _res_scale_to_ohm(float(m.group(1)), m.group(2))
                continue
            if s.startswith("*NAME_MAP"):
                in_name_map = True
                continue
            if s.startswith("*PORTS") or s.startswith("*DEFINE") or s.startswith("*POWER_NETS"):
                in_name_map = False
                continue
            if in_name_map:
                # entries: `*<id> <name>`
                if s.startswith("*") and not s.startswith("*D_NET") and not s.startswith("*R_NET"):
                    parts = s.split(None, 1)
                    if len(parts) == 2:
                        data.id2name[parts[0]] = _deesc(parts[1].strip())
                        continue
                in_name_map = False  # fell out of the map
            if s.startswith("*D_NET") or s.startswith("*R_NET"):
                parts = s.split()
                alias = parts[1] if len(parts) >= 2 else ""
                net_ids.add(alias)
                current_net = data.id2name.get(alias, _deesc(alias.lstrip("*")))
                net_pins.setdefault(current_net, [])
                section = None
                continue
            if s.startswith("*CONN"):
                section = "CONN"
                continue
            if s.startswith("*CAP"):
                section = "CAP"
                continue
            if s.startswith("*RES"):
                section = "RES"
                continue
            if s.startswith("*END"):
                section = None
                current_net = None
                continue
            if section == "CONN" and current_net is not None:
                parsed = _pinkey_from_conn(s.split(), data.id2name)
                if parsed:
                    tok, pinkey, d = parsed
                    net_pins[current_net].append((pinkey, d, tok))
                    if pinkey[0] == "PIN":
                        port_to_net[pinkey[1]] = current_net
                        base, _sub = _split_node(tok)
                        # a port node may itself be name-mapped in odd flows
                    else:
                        pin_to_net[pinkey] = current_net
                        base, _sub = _split_node(tok)
                        inst_ids.add(base)

    # driver per net + net order
    for net, pins in net_pins.items():
        data.net_driver[net] = _pick_driver(pins)
    data.nets = list(net_pins.keys())

    def resolve_net(token: str):
        base, sub = _split_node(token)
        if base in net_ids:  # a net-internal node like *547:20
            return data.id2name.get(base, _deesc(base.lstrip("*")))
        if sub is not None and base in inst_ids:  # an instance pin *945:A
            inst = data.id2name.get(base, _deesc(base.lstrip("*")))
            return pin_to_net.get((inst, _deesc(sub)))
        det = _deesc(token)
        if det in port_to_net:  # a bare top port
            return port_to_net[det]
        # a name-mapped port node, or an unmapped/renamed net node
        mapped = data.id2name.get(base)
        if mapped is not None and mapped in net_pins:
            return mapped
        return None

    # ---- pass 2: aggregate caps + RES segments ------------------------------
    current_net = None
    section = None
    in_name_map = False
    # Dedup coupling by the RAW node-token pair. write_spef emits each coupling
    # capacitor SYMMETRICALLY — once in EACH participating net's *CAP block. With
    # OpenROAD's consistent node order the mirror's node2 is the block-local node,
    # so `partner == current_net` already skips it; but that relies on the order
    # convention. Deduping the unordered (node1, node2) token pair makes each
    # physical coupling count exactly ONCE regardless of order — robust across
    # SPEF writer variants (2026-07-07 review; verified: header-ground == 2*coupling
    # holds on gcd/apb/DMA_top). A token pair uniquely identifies one capacitor.
    seen_coupling = set()
    with open(spef_path, "r", encoding="utf-8", errors="ignore") as f:
        for raw in f:
            s = raw.strip()
            if not s:
                continue
            if s.startswith("*NAME_MAP"):
                in_name_map = True
                continue
            if s.startswith("*PORTS") or s.startswith("*DEFINE") or s.startswith("*POWER_NETS"):
                in_name_map = False
                continue
            if in_name_map and not (s.startswith("*D_NET") or s.startswith("*R_NET")):
                continue
            if s.startswith("*D_NET") or s.startswith("*R_NET"):
                in_name_map = False
                parts = s.split()
                alias = parts[1] if len(parts) >= 2 else ""
                current_net = data.id2name.get(alias, _deesc(alias.lstrip("*")))
                data.net_ground_cap_ff.setdefault(current_net, 0.0)
                if len(parts) >= 3:
                    try:
                        data.net_total_cap_ff[current_net] = float(parts[2]) * data.cap_scale_ff
                    except ValueError:
                        pass
                section = None
                continue
            if s.startswith("*CONN"):
                section = "CONN"
                # record the token->pinkey map for this net's pins (for RES pins)
                t2k = data.net_pin_token2key.setdefault(current_net, {})
                for pinkey, _d, tok in net_pins.get(current_net, []):
                    t2k[tok] = pinkey
                continue
            if s.startswith("*CAP"):
                section = "CAP"
                continue
            if s.startswith("*RES"):
                section = "RES"
                continue
            if s.startswith("*END"):
                section = None
                current_net = None
                continue
            if current_net is None:
                continue
            if section == "CAP":
                parts = s.split()
                # `idx node cap` (grounded) | `idx node1 node2 cap` (coupling)
                if len(parts) == 3:
                    try:
                        data.net_ground_cap_ff[current_net] += float(parts[2]) * data.cap_scale_ff
                    except ValueError:
                        pass
                elif len(parts) == 4:
                    try:
                        cap = float(parts[3]) * data.cap_scale_ff
                    except ValueError:
                        continue
                    pair_tok = frozenset((parts[1], parts[2]))
                    if pair_tok in seen_coupling:
                        continue  # symmetric mirror already counted
                    partner = resolve_net(parts[2])
                    if partner and partner != current_net:
                        seen_coupling.add(pair_tok)
                        key = (current_net, partner) if current_net < partner else (partner, current_net)
                        data.coupling_cap_ff[key] = data.coupling_cap_ff.get(key, 0.0) + cap
            elif section == "RES":
                parts = s.split()
                if len(parts) >= 4:
                    try:
                        r = float(parts[3]) * data.res_scale_ohm
                    except ValueError:
                        continue
                    data.net_res_segments.setdefault(current_net, []).append(
                        (parts[1], parts[2], r))

    return data


def total_cap_ff(data: SpefData) -> float:
    """Design-wide total capacitance (sum of every net's *D_NET header cap), fF.
    Reproduces metadata.py::parse_spef_total_cap_fF."""
    return sum(data.net_total_cap_ff.values())
