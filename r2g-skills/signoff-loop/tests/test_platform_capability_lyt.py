"""sky130hs .lyt postcondition + capability tiers (RMD-P0-03/P0-04, 2026-07-22).

Tool presence is NOT a sufficient readiness oracle: the three-platform pilot's
sky130hs probe passed on Magic+Netgen+PDK files while the installed .lyt still
carried legacy lefdef reader options — every GDS lost its DEF geometry and all
four LVS verdicts were invalid. The probe must verify the modern-.lyt
postcondition, and capability must be expressed as explicit tiers
(installed / research_ready / strict_signoff_ready).
"""
import importlib.util
import os
import stat
import sys

_FLOW = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "scripts", "flow")
_spec = importlib.util.spec_from_file_location(
    "platform_capability_lyt_mod", os.path.join(_FLOW, "platform_capability.py"))
pc = importlib.util.module_from_spec(_spec)
sys.modules["platform_capability_lyt_mod"] = pc
_spec.loader.exec_module(pc)

LEGACY_LYT = """<technology>
 <reader-options>
  <lefdef>
   <routing-suffix></routing-suffix>
   <routing-datatype>0</routing-datatype>
   <pins-suffix>.PIN</pins-suffix>
   <pins-datatype>2</pins-datatype>
   <layer-map>layer_map(1 : 'met1')</layer-map>
  </lefdef>
 </reader-options>
</technology>
"""

MODERN_LYT = """<technology>
 <reader-options>
  <lefdef>
   <routing-suffix-string>.drawing</routing-suffix-string>
   <routing-datatype-string>20</routing-datatype-string>
   <produce-special-routing>true</produce-special-routing>
   <special-routing-suffix-string>.drawing</special-routing-suffix-string>
   <layer-map>layer_map(68/20 : 'met1.drawing')</layer-map>
  </lefdef>
 </reader-options>
</technology>
"""


def _mk_exec(path):
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def _flow(tmp_path, lyt_text):
    flow = tmp_path / "flow"
    pdir = flow / "platforms" / "sky130hs"
    (pdir / "drc").mkdir(parents=True)
    (pdir / "config.mk").write_text("export PLATFORM = sky130hs\n")
    (pdir / "sky130hs.lyt").write_text(lyt_text)
    # sibling deck so drc_deck resolves
    sib = flow / "platforms" / "sky130hd" / "drc"
    sib.mkdir(parents=True)
    (sib / "sky130hd.lydrc").write_text("FEOL = true\n")
    return str(flow)


def _sky130_env(tmp_path, monkeypatch):
    """Magic+Netgen+PDK all present — the pre-fix probe's whole world."""
    pdk = tmp_path / "pdk"
    magic_dir = pdk / "sky130A" / "libs.tech" / "magic"
    netgen_dir = pdk / "sky130A" / "libs.tech" / "netgen"
    magic_dir.mkdir(parents=True)
    netgen_dir.mkdir(parents=True)
    (magic_dir / "sky130A.tech").write_text("tech sky130A\n")
    (netgen_dir / "sky130A_setup.tcl").write_text("# setup\n")
    monkeypatch.setenv("PDK_ROOT", str(pdk))
    monkeypatch.setenv("MAGIC_EXE", _mk_exec(tmp_path / "magic"))
    monkeypatch.setenv("NETGEN_EXE", _mk_exec(tmp_path / "netgen"))


def test_legacy_lyt_fails_lvs_capability(tmp_path, monkeypatch):
    _sky130_env(tmp_path, monkeypatch)
    caps = pc.probe_platform(_flow(tmp_path, LEGACY_LYT), "sky130hs")
    assert caps["lvs"]["lyt_modern"] is False
    assert caps["lvs"]["ok"] is False, \
        "tool presence must not certify LVS while the .lyt is legacy (RMD-P0-04)"
    assert "patch_sky130hs_lyt" in caps["lvs"]["hint"]
    assert caps["strict_signoff_ready"] is False
    assert "lvs" in caps["missing"]


def test_modern_lyt_passes_lvs_capability(tmp_path, monkeypatch):
    _sky130_env(tmp_path, monkeypatch)
    caps = pc.probe_platform(_flow(tmp_path, MODERN_LYT), "sky130hs")
    assert caps["lvs"]["lyt_modern"] is True
    assert caps["lvs"]["ok"] is True


def test_missing_lyt_fails_closed(tmp_path, monkeypatch):
    _sky130_env(tmp_path, monkeypatch)
    flow = _flow(tmp_path, MODERN_LYT)
    os.unlink(os.path.join(flow, "platforms", "sky130hs", "sky130hs.lyt"))
    caps = pc.probe_platform(flow, "sky130hs")
    assert caps["lvs"]["lyt_modern"] is None
    assert caps["lvs"]["ok"] is False


def test_tiers_reported(tmp_path, monkeypatch):
    _sky130_env(tmp_path, monkeypatch)
    caps = pc.probe_platform(_flow(tmp_path, LEGACY_LYT), "sky130hs")
    # drc_deck ok (sibling), timing missing -> installed; lvs broken by .lyt.
    assert caps["tier"] in ("installed", "research_ready")
    assert caps["tier"] != "strict_signoff_ready"


def test_non_sky130hs_platform_unaffected(tmp_path, monkeypatch):
    """The .lyt postcondition is a deliberate sky130hs-scoped check, not a
    generic cross-platform rule."""
    _sky130_env(tmp_path, monkeypatch)
    flow = _flow(tmp_path, LEGACY_LYT)
    pdir = os.path.join(flow, "platforms", "sky130hd")
    with open(os.path.join(pdir, "config.mk"), "w") as f:
        f.write("export PLATFORM = sky130hd\n")
    caps = pc.probe_platform(flow, "sky130hd")
    assert "lyt_modern" not in caps["lvs"]
    assert caps["lvs"]["ok"] is True
