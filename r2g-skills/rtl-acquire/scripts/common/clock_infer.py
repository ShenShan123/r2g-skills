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
    out of scope per the hard rules). Empty when the top has no edge events
    on its inputs (combinational / self-timed / clock only fanned to
    submodules without a top-level always block)."""
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
        return [n for n, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    return []
