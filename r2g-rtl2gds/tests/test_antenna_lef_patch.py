"""Tests for antenna_lef_patch.py: the nangate45 antenna-model LEF transforms.

These are pure text transforms (no ORFS needed).  They cover the three fixes that make
OpenROAD's repair_antennas functional on nangate45 — per-layer ratios in the tech LEF,
per-pin gate areas merged into the SC LEF, and a usable diode — plus idempotency.
"""
from __future__ import annotations

import antenna_lef_patch as a

# --- minimal LEF fixtures -------------------------------------------------------------

TECH = """\
LAYER metal1
  TYPE ROUTING ;
  WIDTH 0.07 ;
END metal1
LAYER via1
  TYPE CUT ;
END via1
LAYER metal2
  TYPE ROUTING ;
  WIDTH 0.07 ;
END metal2
"""

# Reference LEF with full per-pin antenna model (like NangateOpenCellLibrary.macro.lef).
REF = """\
MACRO INV_X1
  PIN A
    DIRECTION INPUT ;
    ANTENNAPARTIALMETALAREA 0.0184 LAYER metal1 ;
    ANTENNAGATEAREA 0.05225 ;
    PORT
      LAYER metal1 ;
    END
  END A
  PIN ZN
    DIRECTION OUTPUT ;
    ANTENNADIFFAREA 0.1097 ;
  END ZN
END INV_X1
MACRO ANTENNA_X1
  CLASS CORE ANTENNACELL ;
  PIN A
    DIRECTION INPUT ;
    ANTENNAGATEAREA 0.0162 ;
  END A
END ANTENNA_X1
"""

# SC LEF with antenna data stripped (like NangateOpenCellLibrary.macro.mod.lef).
SC = """\
MACRO INV_X1
  PIN A
    DIRECTION INPUT ;
    USE SIGNAL ;
    PORT
      LAYER metal1 ;
        RECT 0.06 0.525 0.165 0.7 ;
    END
  END A
  PIN ZN
    DIRECTION OUTPUT ;
    PORT
      LAYER metal1 ;
    END
  END ZN
END INV_X1
MACRO ANTENNA_X1
  CLASS CORE ANTENNACELL ;
  PIN A
    DIRECTION INPUT ;
    USE SIGNAL ;
    ANTENNADIFFAREA  0.0 ;
    PORT
      LAYER metal1 ;
    END
  END A
END ANTENNA_X1
"""


# --- tech LEF ---------------------------------------------------------------------------

def test_tech_adds_ratio_to_routing_layers_only():
    out = a.patch_tech_lef(TECH, ratio=300)
    assert a.tech_model_layers(out) == 2          # metal1 + metal2, NOT via1
    # the CUT layer must not receive a ratio
    via_block = out[out.index("LAYER via1"):out.index("END via1")]
    assert "ANTENNAAREARATIO" not in via_block
    assert "ANTENNAMODEL OXIDE1 ;" in out
    assert "ANTENNAAREARATIO 300 ;" in out


def test_tech_is_idempotent():
    once = a.patch_tech_lef(TECH, ratio=300)
    twice = a.patch_tech_lef(once, ratio=300)
    assert a.tech_model_layers(twice) == 2
    assert once == twice


def test_tech_ratio_value_respected():
    out = a.patch_tech_lef(TECH, ratio=200)
    assert "ANTENNAAREARATIO 200 ;" in out


# --- SC LEF pin merge -------------------------------------------------------------------

def test_merge_injects_gate_area_into_signal_pins():
    out = a.merge_pin_antenna(SC, REF)
    assert a.sc_gate_area_count(out) == 1          # INV_X1 pin A gets its gate area
    # the merged property sits before PORT, inside the pin
    inv = out[out.index("MACRO INV_X1"):out.index("END INV_X1")]
    assert "ANTENNAGATEAREA 0.05225 ;" in inv
    # output pin ZN gets its diff area too
    assert "ANTENNADIFFAREA 0.1097 ;" in inv


def test_merge_skips_pins_that_already_have_antenna_data():
    # ANTENNA_X1 pin A already has ANTENNADIFFAREA 0.0 → merge must NOT inject a gate area
    out = a.merge_pin_antenna(SC, REF)
    diode = out[out.index("MACRO ANTENNA_X1"):]
    assert "ANTENNAGATEAREA" not in diode


def test_merge_is_idempotent():
    once = a.merge_pin_antenna(SC, REF)
    twice = a.merge_pin_antenna(once, REF)
    assert once == twice


# --- diode fix --------------------------------------------------------------------------

def test_fix_diode_sets_positive_diffarea():
    out = a.fix_diode(SC, diff_area=0.1)
    assert a.diode_diff_area(out) == 0.1


def test_fix_diode_after_merge_drops_gate_area_single_diffarea():
    # full pipeline: merge then diode fix.  The diode must end with exactly one positive
    # ANTENNADIFFAREA and no ANTENNAGATEAREA.
    merged = a.merge_pin_antenna(SC, REF)
    out = a.fix_diode(merged, diff_area=0.1)
    diode = out[out.index("MACRO ANTENNA_X1"):]
    assert diode.count("ANTENNADIFFAREA") == 1
    assert "ANTENNAGATEAREA" not in diode
    assert a.diode_diff_area(out) == 0.1


def test_fix_diode_is_idempotent():
    once = a.fix_diode(SC, diff_area=0.1)
    twice = a.fix_diode(once, diff_area=0.1)
    assert once == twice


def test_patch_sc_lef_full_pipeline():
    out = a.patch_sc_lef(SC, diff_area=0.1, ref_text=REF)
    assert a.sc_gate_area_count(out) == 1
    assert a.diode_diff_area(out) == 0.1
    # PORT blocks survive the transform (structure intact)
    assert out.count("PORT") == SC.count("PORT")
    assert out.count("END INV_X1") == 1
