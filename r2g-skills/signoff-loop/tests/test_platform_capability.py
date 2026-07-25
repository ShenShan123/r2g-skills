"""platform_capability.py — strict per-platform signoff capability (pilot P0-3).

The round-2 pilot's ENV gate passed while nangate45 had no LVS rule deck and an
unusable zero-diff-area antenna diode, so strict signoff was impossible after
multi-hour flows. The probe must (a) read the platform's decks/LEFs, (b) call a
0-area diode UNUSABLE, (c) accept the sky130-style ANTENNADIFF*AREARATIO rule
family, and (d) fail open (None) from antenna_repair_usable when the environment
cannot be inspected.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import platform_capability as pc

MOD = Path(pc.__file__).resolve()

TECH_WITH_MODEL = """\
LAYER metal1
  TYPE ROUTING ;
  ANTENNAMODEL OXIDE1 ;
  ANTENNAAREARATIO 300 ;
END metal1
"""
TECH_SKY_STYLE = """\
LAYER met1
  TYPE ROUTING ;
  ANTENNAMODEL OXIDE1 ;
  ANTENNADIFFAREARATIO 400 ;
  ANTENNADIFFSIDEAREARATIO 200 ;
END met1
"""
TECH_NO_MODEL = """\
LAYER metal1
  TYPE ROUTING ;
END metal1
"""


def _sc_lef(diff_area):
    return (
        "MACRO ANTENNA_X1\n"
        "  CLASS CORE ANTENNACELL ;\n"
        f"  ANTENNADIFFAREA {diff_area} ;\n"
        "END ANTENNA_X1\n"
        "MACRO INV_X1\n"
        "  CLASS CORE ;\n"
        "END INV_X1\n"
    )


def _mk_platform(tmp_path, name, *, tech, sc, lvs_rule=True, drc_deck=True,
                 rcx=True, lib=True):
    pdir = tmp_path / "flow" / "platforms" / name
    pdir.mkdir(parents=True)
    (pdir / "tech.lef").write_text(tech)
    (pdir / "sc.lef").write_text(sc)
    cfg = [
        "export TECH_LEF = $(PLATFORM_DIR)/tech.lef",
        "export SC_LEF = $(PLATFORM_DIR)/sc.lef",
    ]
    if drc_deck:
        (pdir / "drc").mkdir()
        (pdir / "drc" / f"{name}.lydrc").write_text("# deck\n")
        cfg.append("export KLAYOUT_DRC_FILE = $(PLATFORM_DIR)/drc/$(PLATFORM).lydrc")
    if lvs_rule:
        (pdir / "lvs").mkdir()
        (pdir / "lvs" / f"{name}.lylvs").write_text("# rule\n")
        cfg.append("export KLAYOUT_LVS_FILE = $(PLATFORM_DIR)/lvs/$(PLATFORM).lylvs")
    if rcx:
        (pdir / "rcx_patterns.rules").write_text("# rcx\n")
        cfg.append("export RCX_RULES = $(PLATFORM_DIR)/rcx_patterns.rules")
    if lib:
        (pdir / "lib").mkdir()
        (pdir / "lib" / "typ.lib").write_text("library(typ){}\n")
        cfg.append("export LIB_FILES = $(PLATFORM_DIR)/lib/typ.lib")
    (pdir / "config.mk").write_text("\n".join(cfg) + "\n")
    return str(tmp_path / "flow")


def test_fully_capable_platform_is_strict_ready(tmp_path):
    flow = _mk_platform(tmp_path, "np45", tech=TECH_WITH_MODEL, sc=_sc_lef(0.1))
    caps = pc.probe_platform(flow, "np45")
    assert caps["strict_signoff_ready"], caps
    assert caps["antenna"]["usable_diodes"] == ["ANTENNA_X1"]


def test_zero_area_diode_is_unusable(tmp_path):
    """The pilot's exact nangate45 state: diode present but ANTENNADIFFAREA 0.0
    (GRT-0246) — antenna capability must read MISS."""
    flow = _mk_platform(tmp_path, "np45", tech=TECH_WITH_MODEL, sc=_sc_lef(0.0))
    caps = pc.probe_platform(flow, "np45")
    assert not caps["antenna"]["ok"]
    assert "antenna" in caps["missing"] and not caps["strict_signoff_ready"]


def test_missing_tech_model_is_unusable(tmp_path):
    flow = _mk_platform(tmp_path, "np45", tech=TECH_NO_MODEL, sc=_sc_lef(0.1))
    caps = pc.probe_platform(flow, "np45")
    assert caps["antenna"]["ratio_layers"] == 0 and not caps["antenna"]["ok"]


def test_sky130_style_ratio_family_counts(tmp_path):
    """sky130 ships ANTENNADIFFAREARATIO / ANTENNADIFFSIDEAREARATIO, not plain
    ANTENNAAREARATIO — the model-presence probe must accept the whole family."""
    flow = _mk_platform(tmp_path, "skyX", tech=TECH_SKY_STYLE, sc=_sc_lef(0.43))
    caps = pc.probe_platform(flow, "skyX")
    assert caps["antenna"]["ratio_layers"] > 0 and caps["antenna"]["ok"], caps


def test_missing_lvs_rule_blocks_strict(tmp_path):
    flow = _mk_platform(tmp_path, "np45", tech=TECH_WITH_MODEL, sc=_sc_lef(0.1),
                        lvs_rule=False)
    caps = pc.probe_platform(flow, "np45")
    assert not caps["lvs"]["ok"] and "lvs" in caps["missing"]


def test_antenna_repair_usable_fails_open_without_env(tmp_path, monkeypatch):
    """No discoverable flow dir -> (None, reason): callers must FAIL OPEN, never
    block a repair on missing introspection."""
    for var in ("FLOW_DIR", "ORFS_ROOT"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(pc, "find_flow_dir", lambda explicit=None: None)
    usable, reason = pc.antenna_repair_usable("nangate45")
    assert usable is None and "flow dir" in reason


def test_antenna_repair_usable_verdicts(tmp_path):
    flow = _mk_platform(tmp_path, "np45", tech=TECH_WITH_MODEL, sc=_sc_lef(0.0))
    usable, reason = pc.antenna_repair_usable("np45", flow)
    assert usable is False and "ANTENNADIFFAREA" in reason
    flow2 = _mk_platform(tmp_path / "b", "np45", tech=TECH_WITH_MODEL, sc=_sc_lef(0.1))
    usable, reason = pc.antenna_repair_usable("np45", flow2)
    assert usable is True and "ANTENNA_X1" in reason


def test_cli_strict_exit(tmp_path):
    flow_bad = _mk_platform(tmp_path, "np45", tech=TECH_WITH_MODEL, sc=_sc_lef(0.0))
    r = subprocess.run([sys.executable, str(MOD), "--flow-dir", flow_bad,
                        "--platform", "np45", "--strict"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 1
    manifest = json.loads(r.stdout)
    assert manifest["platforms"]["np45"]["strict_signoff_ready"] is False
    flow_good = _mk_platform(tmp_path / "g", "np45", tech=TECH_WITH_MODEL,
                             sc=_sc_lef(0.1))
    r = subprocess.run([sys.executable, str(MOD), "--flow-dir", flow_good,
                        "--platform", "np45", "--strict"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr


# --------------------------------------------------------------------------- #
# product scope: asap7 is not supported in this version                       #
# --------------------------------------------------------------------------- #
# 2026-07-25. This is a SCOPE decision, not a capability gap — asap7's platform dir is
# present and its DRC deck resolves, so every installed-capability check would award it
# `research_ready` and happily let a campaign spend hours on it. It can never read
# DRC-clean (irreducible false-violation floor), has no LVS deck, and its authoritative
# Calibre deck is not installed, so it can never promote a recipe.
def test_unsupported_platform_is_never_strict_ready(tmp_path):
    caps = pc.probe_platform(str(tmp_path), "asap7")
    assert caps["supported"] is False
    assert caps["tier"] == "unsupported"
    assert caps["strict_signoff_ready"] is False
    assert caps["unsupported_reason"]


def test_unsupported_verdict_does_not_depend_on_what_is_installed(tmp_path):
    """Even a FULLY provisioned asap7 stays unsupported — scope outranks capability."""
    pdir = tmp_path / "platforms" / "asap7"
    (pdir / "drc").mkdir(parents=True)
    (pdir / "drc" / "asap7.lydrc").write_text("# deck\n")
    (pdir / "config.mk").write_text("export RCX_RULES = rules\nexport LIB_FILES = x.lib\n")
    caps = pc.probe_platform(str(tmp_path), "asap7")
    assert caps["tier"] == "unsupported"
    assert caps["strict_signoff_ready"] is False


def test_supported_platforms_are_unaffected(tmp_path):
    # tmp_path has no platforms/ dir, so this takes the pre-existing 'missing_platform'
    # early return — which carries no `tier` and must keep not carrying one. The point
    # here is only that the SCOPE gate does not fire on a supported platform.
    caps = pc.probe_platform(str(tmp_path), "sky130hd")
    assert caps.get("supported") is not False
    assert caps.get("tier") != "unsupported"
    assert "unsupported_reason" not in caps


def test_unsupported_reason_reads_as_the_gate_answer(monkeypatch):
    monkeypatch.delenv("R2G_ALLOW_UNSUPPORTED_PLATFORM", raising=False)
    assert pc.unsupported_reason("asap7")
    assert pc.unsupported_reason("sky130hd") is None


def test_explicit_override_lets_a_deliberate_experiment_through(monkeypatch):
    """The escape hatch clears the gate — but the probe still reports the truth."""
    monkeypatch.setenv("R2G_ALLOW_UNSUPPORTED_PLATFORM", "1")
    assert pc.unsupported_reason("asap7") is None


def test_summary_line_says_unsupported(tmp_path):
    line = pc._summary_line(pc.probe_platform(str(tmp_path), "asap7"))
    assert "UNSUPPORTED" in line
