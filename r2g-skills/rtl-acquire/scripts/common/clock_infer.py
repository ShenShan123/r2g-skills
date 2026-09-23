"""Event-control clock inference (2026-07-16 full-pipeline issue 5).

The fixed CLOCK_PORT_CANDIDATES name list missed every non-standard clock port
(the real miss: ethmac's case-sensitive ``Clk`` — 13 top-level ``posedge MTxClk``
blocks, 119 sequential cells, silently promoted under a VIRTUAL clock, so every
setup/hold label downstream was meaningless). This module infers clock candidates
from the top module's OWN edge-triggered event controls: a top-level input that
appears under ``posedge``/``negedge`` in the top body, reset-like names excluded,
ranked by occurrence count.

Shared by promote_candidates (detection + the seq-cells virtual-clock gate) and
expand_candidates' make_minimal_sdc (synth-only SDC) — one copy, per the techlib
lesson: a worker-local patch fixes one consumer and silently leaves the other wrong.
"""
from __future__ import annotations

import re

RESET_LIKE = re.compile(r"(rst|reset|clear|clr)", re.I)
_IDENT = r"[A-Za-z_][A-Za-z0-9_$]*"


def infer_clock_ports(top: str, texts: list[str]) -> list[str]:
    """Ranked clock-port candidates for ``top`` from its body's edge-triggered
    event controls. Only TOP-LEVEL INPUT ports count (an internal divided clock
    is not a constrainable port); reset-like names are excluded. Returns the
    ranked list — the caller decides what an ambiguous (>1) result means
    (promotion requires an explicit operator choice; multi-clock designs are
    out of scope per the hard rules). When the top body has no edge events on
    its inputs, falls back to inputs that drive a submodule clock through named
    port connections (any depth). Empty for combinational / self-timed tops, or
    when the clock is connected only positionally."""
    mod_re = re.compile(r"(?ms)^\s*module\s+" + re.escape(top) + r"\b[^;]*?\((.*?)\)\s*;")
    for text in texts:
        text_nc = re.sub(r"//.*", "", text)
        text_nc = re.sub(r"(?s)/\*.*?\*/", " ", text_nc)
        m = mod_re.search(text_nc)
        if not m:
            continue
        endm = text_nc.find("endmodule", m.end())
        body = text_nc[m.start(): endm if endm != -1 else len(text_nc)]
        header = re.sub(r"\[[^\]]*\]", " ", m.group(1))
        ports = {t for t in re.split(r"[,\s()]+", header)
                 if t and re.fullmatch(_IDENT, t)}
        for dm in re.finditer(
                rf"(?m)^\s*input\s+(?:wire\s+|logic\s+|reg\s+)?(?:\[[^\]]*\]\s*)?"
                rf"({_IDENT}(?:\s*,\s*{_IDENT})*)", body):
            for name in re.split(r"\s*,\s*", dm.group(1)):
                ports.add(name.strip())
        counts: dict[str, int] = {}
        for em in re.finditer(rf"(?:posedge|negedge)\s+({_IDENT})", body):
            name = em.group(1)
            if name in ports and not RESET_LIKE.search(name):
                counts[name] = counts.get(name, 0) + 1
        if not counts:
            # Wave-3 E5L: a wrapper whose clock only feeds submodules (e.g. an AXI
            # top passing s_axi_aclk to its core) has no top-body edge event. Follow
            # the hierarchy instead of rejecting a sequential design as unclocked.
            counts = {n: c for n, c in _hierarchical_clock_ports(texts).get(top, {}).items()
                      if n in ports}
        return [n for n, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    return []


def _strip_comments(text: str) -> str:
    return re.sub(r"(?s)/\*.*?\*/", " ", re.sub(r"//.*", "", text))


def _balanced(text: str, start: int) -> int:
    """Index just past the ')' matching the '(' at ``start``; -1 if unbalanced."""
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return i + 1
    return -1


_MODULE_RE = re.compile(rf"(?ms)^\s*module\s+({_IDENT})\b(.*?)\bendmodule\b")
_NAMED_CONN_RE = re.compile(
    rf"\.\s*({_IDENT})\s*(?:\(\s*({_IDENT})\s*(?:\[[^\]]*\])?\s*\)|(?=\s*[,)]))")


def _hierarchical_clock_ports(texts: list[str]) -> dict[str, dict[str, int]]:
    """Per module: its ports that act as clocks, directly (edge event in its own
    body) or transitively (connected by name to a clock port of an instance).
    Only named connections are followed (``.clk(sig)`` and SV implicit ``.clk``);
    positional connections are not guessed. Reset-like names are excluded."""
    modules: dict[str, tuple[set[str], str]] = {}
    for text in texts:
        for m in _MODULE_RE.finditer(_strip_comments(text)):
            body = m.group(2)
            ports = {t for t in re.findall(_IDENT, re.sub(r"\[[^\]]*\]", " ",
                                                          body.split(";", 1)[0]))}
            ports |= {n.strip() for dm in re.finditer(
                rf"(?m)^\s*input\b[^;]*?({_IDENT}(?:\s*,\s*{_IDENT})*)\s*[;,)]", body)
                for n in dm.group(1).split(",")}
            modules.setdefault(m.group(1), (ports, body))

    clocked: dict[str, dict[str, int]] = {name: {} for name in modules}
    for name, (ports, body) in modules.items():
        for em in re.finditer(rf"(?:posedge|negedge)\s+({_IDENT})", body):
            sig = em.group(1)
            if sig in ports and not RESET_LIKE.search(sig):
                clocked[name][sig] = clocked[name].get(sig, 0) + 1

    # Instance connections per parent: (child module, {child_port: parent_signal}).
    instances: dict[str, list[tuple[str, dict[str, str]]]] = {n: [] for n in modules}
    for parent, (_ports, body) in modules.items():
        for child in modules:
            if child == parent:
                continue
            for im in re.finditer(rf"\b{re.escape(child)}\s*(#\s*\()?", body):
                pos = im.end()
                if im.group(1):
                    pos = _balanced(body, pos - 1)
                    if pos < 0:
                        continue
                inst = re.match(rf"\s*{_IDENT}\s*(?:\[[^\]]*\]\s*)?\(", body[pos:])
                if not inst:
                    continue
                open_at = pos + inst.end() - 1
                close_at = _balanced(body, open_at)
                if close_at < 0:
                    continue
                conns = {c.group(1): (c.group(2) or c.group(1))
                         for c in _NAMED_CONN_RE.finditer(body[open_at:close_at])}
                instances[parent].append((child, conns))

    changed = True
    while changed:
        changed = False
        for parent, (ports, _body) in modules.items():
            for child, conns in instances[parent]:
                for child_port in clocked[child]:
                    sig = conns.get(child_port)
                    if sig and sig in ports and not RESET_LIKE.search(sig) \
                            and sig not in clocked[parent]:
                        clocked[parent][sig] = 1
                        changed = True
    return clocked
