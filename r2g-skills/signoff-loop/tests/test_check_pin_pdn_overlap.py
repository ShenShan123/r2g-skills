"""Geometric pin-vs-PDN short diagnosis (failure-patterns.md #58, ROM_16).

A met-layer IO pin placed on a same-layer power stripe is a REAL short that
geometry-only DRC decks cannot see; it surfaces as an opaque Netgen
`top_pin_mismatch`. check_pin_pdn_overlap.py proves it from the DEF alone
(exit 4 + named pins), so run_netgen_lvs.sh can classify `pin_pdn_short`."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
TOOL = SKILL / "scripts" / "extract" / "check_pin_pdn_overlap.py"

DEF_TMPL = """VERSION 5.8 ;
DESIGN demo ;
UNITS DISTANCE MICRONS 1000 ;
PINS 2 ;
- out1 + NET out1 + DIRECTION OUTPUT + USE SIGNAL
  + PORT
    + LAYER met3 ( -400 -150 ) ( 400 150 )
    + PLACED ( {x1} 1000 ) N ;
- ok + NET ok + DIRECTION OUTPUT + USE SIGNAL
  + PORT
    + LAYER met3 ( -400 -150 ) ( 400 150 )
    + PLACED ( 9000 9000 ) N ;
END PINS
SPECIALNETS 1 ;
- VSS ( * VSS ) + USE GROUND
  + ROUTED met3 330 + SHAPE STRIPE ( 0 1000 ) ( 5000 * )
  ;
END SPECIALNETS
END DESIGN
"""


def _run(def_path):
    return subprocess.run([sys.executable, str(TOOL), str(def_path), "--json"],
                          capture_output=True, text=True, timeout=60)


def test_detects_pin_on_stripe(tmp_path):
    d = tmp_path / "a.def"
    d.write_text(DEF_TMPL.format(x1=1000))       # pin bbox overlaps the stripe
    r = _run(d)
    assert r.returncode == 4, (r.stdout, r.stderr)
    shorts = json.loads(r.stdout)["shorts"]
    assert [s["pin"] for s in shorts] == ["out1"]
    assert shorts[0]["power_net"] == "VSS" and shorts[0]["layer"] == "met3"


def test_clean_when_pin_clear_of_stripes(tmp_path):
    d = tmp_path / "b.def"
    d.write_text(DEF_TMPL.format(x1=7000))       # pin sits past the stripe end
    r = _run(d)
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert json.loads(r.stdout)["shorts"] == []


def test_same_net_overlap_is_not_a_short(tmp_path):
    d = tmp_path / "c.def"
    d.write_text(DEF_TMPL.format(x1=1000).replace("+ NET out1", "+ NET VSS"))
    r = _run(d)
    assert r.returncode == 0, (r.stdout, r.stderr)
