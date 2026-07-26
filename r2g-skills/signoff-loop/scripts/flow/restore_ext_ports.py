#!/usr/bin/env python3
"""Restore top-level ports that Magic's ext2spice dropped to an internal alias
(failure-patterns.md #58, "Magic ext2spice port-name loss").

Observed 2026-07-26 (sky130hs r2 wave 1, A_Single_Path_Delay_32_Point_FFT
ROM_16): the DEF and GDS are healthy (KLayout DRC clean, the port net routed
buffer→met3 pin), the .ext file explicitly declares `port "w_r[12]"` — yet the
extracted .subckt line lists 46 of 48 ports. In the .ext merge chains those two
nets carry a leading ANONYMOUS route fragment (`m2_19132_13949#`), and
ext2spice picked an internal alias (`sky130_fd_sc_hs__buf_1_50/A`) as the
canonical net name instead of the port label. Netgen then honestly reports
`Top level cell failed pin matching` (net counts 201 vs 203) on a perfectly
good layout — a FALSE design `mismatch` that teaches the learner a lie and
burns a fix session.

This step runs after extraction (next to normalize_diode_spice.py): it reads
the top cell's .ext as ground truth, unifies the merge chains, and for every
declared-but-missing port renames the alias back to the port name inside the
top .subckt (header + body). SAFETY: a merge class containing TWO OR MORE
declared ports is a genuine short — never restored, Netgen must keep reporting
it. Idempotent; a parse problem warns and leaves the netlist untouched (the
downstream verdict stays the honest mismatch).

usage: restore_ext_ports.py <extracted.spice> <top_cell> <top.ext>
exit 0 always (fail-open helper — the caller's netgen run is the judge).
"""
from __future__ import annotations

import re
import sys


class _UF:
    def __init__(self):
        self.p: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: str, b: str):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


def parse_ext(path: str) -> tuple[set[str], _UF]:
    ports: set[str] = set()
    uf = _UF()
    port_re = re.compile(r'^port\s+"([^"]+)"')
    merge_re = re.compile(r'^merge\s+"([^"]+)"\s+"([^"]+)"')
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = port_re.match(line)
            if m:
                ports.add(m.group(1))
                continue
            m = merge_re.match(line)
            if m:
                uf.union(m.group(1), m.group(2))
    return ports, uf


def top_subckt_span(lines: list[str], top: str) -> tuple[int, int, int] | None:
    """(header_start, body_start, end) line indices of the top .subckt: header
    includes `+` continuations; end is the .ends line (exclusive of it)."""
    start = None
    for i, ln in enumerate(lines):
        t = ln.split()
        if len(t) >= 2 and t[0].lower() == ".subckt" and t[1] == top:
            start = i
            break
    if start is None:
        return None
    body = start + 1
    while body < len(lines) and lines[body].startswith("+"):
        body += 1
    end = body
    while end < len(lines) and not lines[end].lower().startswith(".ends"):
        end += 1
    return start, body, end


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(__doc__.strip().splitlines()[-2], file=sys.stderr)
        return 0
    spice_path, top, ext_path = argv[1:4]
    try:
        declared, uf = parse_ext(ext_path)
        with open(spice_path, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
        span = top_subckt_span(lines, top)
        if span is None or not declared:
            return 0
        hdr, body, end = span
        header_tokens: list[str] = []
        for i in range(hdr, body):
            toks = lines[i].split()
            header_tokens += toks[2:] if i == hdr else toks[1:]
        present = set(header_tokens)
        missing = sorted(declared - present)
        if not missing:
            return 0
        # Group declared ports by merge class to detect genuine shorts.
        by_class: dict[str, set[str]] = {}
        for p in declared:
            by_class.setdefault(uf.find(p), set()).add(p)
        body_text = "\n".join(lines[body:end])
        restored, skipped = [], []
        for port in missing:
            cls = by_class.get(uf.find(port), {port})
            if len(cls) > 1:
                skipped.append((port, f"merge class holds {len(cls)} ports "
                                      f"({', '.join(sorted(cls))}) — genuine short"))
                continue
            # The alias ext2spice chose: the merge-class member actually used
            # as a net token in the top body.
            members = [n for n in uf.p if uf.find(n) == uf.find(port) and n != port]
            body_tokens = set(body_text.split())
            alias = next((n for n in members if n in body_tokens), None)
            if alias is None:
                skipped.append((port, "no merge-class alias appears in the top "
                                      "subckt body — cannot attribute"))
                continue
            if alias in declared or alias in present:
                skipped.append((port, f"alias {alias!r} is itself a port — refusing"))
                continue
            # Rename alias -> port in the top body (exact token), append to header.
            pat = re.compile(r"(?<!\S)" + re.escape(alias) + r"(?!\S)")
            body_text = pat.sub(port, body_text)
            lines.insert(body, "+ " + port)
            body += 1
            end += 1
            restored.append((port, alias))
        if restored:
            new_lines = lines[:body] + body_text.splitlines() + lines[end:]
            with open(spice_path, "w", encoding="utf-8") as f:
                f.write("\n".join(new_lines) + "\n")
            for port, alias in restored:
                print(f"[restore_ext_ports] restored port {port!r} "
                      f"(ext2spice had renamed its net to internal alias {alias!r})")
        for port, why in skipped:
            print(f"[restore_ext_ports] NOT restoring {port!r}: {why}", file=sys.stderr)
    except Exception as exc:   # fail-open: the honest mismatch downstream remains
        print(f"[restore_ext_ports] WARNING: skipped ({type(exc).__name__}: {exc})",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
