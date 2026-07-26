"""Magic ext2spice port-name loss (failure-patterns.md #58, found live in the
2026-07-26 sky130hs r2 wave on FFT ROM_16).

ext2spice can pick an INTERNAL alias as the canonical net name for a port net
(when an anonymous route fragment precedes the port label in the .ext merge
order), silently dropping the port from the extracted .subckt — Netgen then
reports a FALSE design `top_pin_mismatch` on a healthy, DRC-clean layout.
restore_ext_ports.py renames the alias back using the .ext `port` declarations
as ground truth; a merge class holding 2+ ports (a genuine short) is NEVER
restored."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
SCRIPT = SKILL / "scripts" / "flow" / "restore_ext_ports.py"


def _write_fixture(tmp_path, *, short=False):
    """A 3-port top cell where port `b` lost its name to alias `buf_1/A`.
    With short=True, ports b and c are merged (a genuine short)."""
    ext = tmp_path / "TOP.ext"
    merges = ['merge "m2_1_1#" "VIA_1/m2#"',
              'merge "VIA_1/m2#" "b"',
              'merge "b" "buf_1/A"']
    if short:
        merges.append('merge "buf_1/A" "c"')
    ext.write_text("\n".join(
        ['port "a" 1 0 0 10 10 m3',
         'port "b" 2 0 20 10 30 m3',
         'port "c" 3 0 40 10 50 m3'] + merges) + "\n")
    spice = tmp_path / "extracted.spice"
    body_net_c = "buf_1/A" if short else "c"
    spice.write_text("\n".join([
        "* extracted",
        ".subckt buf VDD VSS A X",
        ".ends",
        # port b missing from the header — its net rides the alias buf_1/A
        ".subckt TOP a",
        "+ " + body_net_c if short else "+ c",
        "Xbuf_1 VDD VSS a buf_1/A buf",
        f"Xbuf_2 VDD VSS {body_net_c} n1 buf",
        ".ends TOP",
    ]) + "\n")
    return spice, ext


def _run(spice, ext):
    return subprocess.run([sys.executable, str(SCRIPT), str(spice), "TOP", str(ext)],
                          capture_output=True, text=True, timeout=60)


def _top_ports(spice):
    lines = spice.read_text().splitlines()
    toks, grab = [], False
    for ln in lines:
        t = ln.split()
        if t and t[0].lower() == ".subckt" and t[1] == "TOP":
            grab = True
            toks += t[2:]
            continue
        if grab and ln.startswith("+"):
            toks += t[1:]
            continue
        if grab:
            break
    return toks


def test_restores_dropped_port_and_renames_alias(tmp_path):
    spice, ext = _write_fixture(tmp_path)
    r = _run(spice, ext)
    assert r.returncode == 0
    assert "restored port 'b'" in r.stdout
    text = spice.read_text()
    assert "b" in _top_ports(spice)
    assert "buf_1/A" not in text          # alias fully renamed
    assert "Xbuf_1 VDD VSS a b buf" in text
    # Idempotent: a second run is a no-op.
    r2 = _run(spice, ext)
    assert r2.returncode == 0 and "restored" not in r2.stdout


def test_genuine_short_is_never_restored(tmp_path):
    spice, ext = _write_fixture(tmp_path, short=True)
    before = spice.read_text()
    r = _run(spice, ext)
    assert r.returncode == 0
    assert "genuine short" in r.stderr
    assert spice.read_text() == before    # untouched — Netgen stays the judge


def test_fail_open_on_missing_ext(tmp_path):
    spice, _ = _write_fixture(tmp_path)
    before = spice.read_text()
    r = subprocess.run([sys.executable, str(SCRIPT), str(spice), "TOP",
                        str(tmp_path / "nope.ext")],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0
    assert spice.read_text() == before
