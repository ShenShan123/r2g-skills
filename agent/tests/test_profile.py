"""The agent's defaults ARE the frozen baseline -- checked against the source of truth."""
import importlib.util
from pathlib import Path

import pytest

from r2g_agent import platforms, profile, project

EXP = Path(__file__).resolve().parents[2] / "experiments"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, EXP / (name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture(scope="module")
def frozen():
    if not (EXP / "make_spec_table.py").is_file():
        pytest.skip("experiments/ not in this checkout")
    return _load("make_spec_table"), _load("conformance_check")


@pytest.mark.parametrize("platform", platforms.PLATFORMS)
def test_defaults_equal_the_frozen_spec(frozen, platform):
    mst, cc = frozen
    d = profile.defaults(platform)
    assert [tuple(t) for t in d["period_tiers"]] == [tuple(t) for t in mst.PERIOD_NS[platform]]
    assert d["utilization"] == mst.UTILIZATION == cc.FROZEN["utilization"]
    assert d["area_allowance"] == mst.AREA_ALLOWANCE_DEFAULT[platform]
    assert d["pdn_floor_um"] == mst.PDN_FLOOR_UM[platform]
    assert d["io_pct"] == cc.FROZEN["io_pct"] and d["setup_uncertainty_pct"] == cc.FROZEN["uncertainty_pct"]
    p = platforms.get(platform)
    assert p.io_pos_per_um == mst.IO_POS_PER_UM[platform] and p.core_inset_um == mst.CORE_INSET_UM
    assert p.port_buffer_um2 == mst.PORT_BUFFER_UM2.get(platform, 0.0)


@pytest.mark.parametrize("area,pins", [(50, 4), (900, 20), (1234.5, 64), (40000, 120), (250000, 900), (8000, 1700)])
@pytest.mark.parametrize("platform", platforms.PLATFORMS)
def test_die_equals_the_frozen_formula(frozen, area, pins, monkeypatch, platform):
    mst, _ = frozen
    monkeypatch.setattr(mst, "area_allowance", lambda plat: mst.AREA_ALLOWANCE_DEFAULT[plat])
    die, core, _ = mst.die_for(platform, area, pins)
    ours = platforms.die_for(platforms.get(platform), area, pins)
    assert (ours["die_side_um"], ours["core_side_um"]) == (die, core)


@pytest.mark.parametrize("cells", [1, 99, 100, 999, 1000, 4999, 5000, 10 ** 6])
@pytest.mark.parametrize("platform", platforms.PLATFORMS)
def test_period_equals_the_frozen_tiers(frozen, cells, platform):
    mst, _ = frozen
    assert profile.period(profile.defaults(platform), cells) == mst.period_for(platform, cells)


def test_sdc_carries_the_frozen_margins(tmp_path):
    src = tmp_path / "s"
    src.mkdir()
    (src / "t.v").write_text("module t(input clk, output q); assign q = clk; endmodule\n")
    project.create(src, tmp_path / "p", "nangate45")
    sdc = (tmp_path / "p" / "constraints" / "constraint.sdc").read_text()
    assert "set_clock_uncertainty -setup [expr {$_per * 0.05}]" in sdc
    assert "set_clock_uncertainty -hold 0.02 " in sdc and "set _io [expr {$_per * 0.15}]" in sdc
    assert "set _per 2.0" in sdc                                # sizing synthesis at the pilot's period
    cfg = (tmp_path / "p" / "constraints" / "config.mk").read_text()
    assert "PLACE_DENSITY_LB_ADDON" not in cfg                  # the baseline never set it


def test_gf180_uses_reviewed_apparatus_and_preserves_rtl(tmp_path):
    import hashlib
    src = tmp_path / "src"; src.mkdir()
    rtl = "module t(input clk, output q); assign q = clk; endmodule\n"
    (src / "t.v").write_text(rtl)
    target = tmp_path / "project"
    meta = project.create(src, target, "gf180")
    cfg = (target / "constraints/config.mk").read_text()
    assert "export PDN_TCL = " in cfg and "export TAPCELL_TCL = " in cfg
    assert (target / "rtl/t.v").read_text() == rtl
    for name, expected in meta["platform_apparatus"]["files"].items():
        raw = (target / "constraints" / ("gf180_" + name)).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == expected["sha256"]


def test_overrides_are_validated_and_recorded(tmp_path):
    prof, changed = profile.merge("nangate45", {"period_ns": 2.5, "utilization": "0.4", "io_pct": None})
    assert prof["period_ns"] == 2.5 and prof["utilization"] == 0.4 and sorted(changed) == ["period_ns", "utilization"]
    for bad in ({"utilization": 0.95}, {"place_density_lb_addon": 0.05}, {"nope": 1}, {"period_ns": "fast"},
                {"period_tiers": [[100, 1.0], [50, 2.0], [None, 3.0]]}, {"period_tiers": [[100, 1.0]]}):
        with pytest.raises(ValueError):
            profile.merge("nangate45", bad)
    assert profile.period(profile.merge("nangate45", {"period_tiers": [[10, 0.7], [None, 4.0]]})[0], 50) == 4.0


def test_custom_params_reach_config_sdc_and_sizing(tmp_path):
    src = tmp_path / "s"
    src.mkdir()
    (src / "t.v").write_text("module t(input clk, output q); assign q = clk; endmodule\n")
    meta = project.create(src, tmp_path / "p", "nangate45",
                          params={"period_ns": 2.5, "die_side_um": 30, "io_pct": 20, "place_density_lb_addon": 0.2})
    assert sorted(meta["profile_changed"]) == ["die_side_um", "io_pct", "period_ns", "place_density_lb_addon"]
    (tmp_path / "st.txt").write_text("=== t ===\n   500  4000.0   500  4000.0 cells\n   Chip area for module '\\\\t': 4000.000000\n")
    (tmp_path / "n.v").write_text("module t(clk, q);\n input clk;\n output q;\nendmodule\n")
    s = project.apply_sizing(tmp_path / "p", tmp_path / "st.txt", tmp_path / "n.v")
    assert s["die_side_um"] == 30 and s["binding"] == "fixed" and s["rule_die_side_um"] > 30
    assert s["period_ns"] == 2.5 and s["period_source"] == "fixed"
    cfg = (tmp_path / "p" / "constraints" / "config.mk").read_text()
    assert "DIE_AREA  = 0 0 30 30" in cfg and "PLACE_DENSITY_LB_ADDON = 0.2" in cfg
    assert "set _io [expr {$_per * 0.2}]" in (tmp_path / "p" / "constraints" / "constraint.sdc").read_text()
