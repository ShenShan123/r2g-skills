"""diagnose_signoff_fix ranks strategies by learned recipes; safety unchanged."""
from __future__ import annotations
import json
from pathlib import Path

import diagnose_signoff_fix as dsf


def _make_project(tmp_path: Path, dir_basename: str, design_name: str,
                  platform: str = "nangate45") -> Path:
    """A project dir whose basename differs from DESIGN_NAME (the live case)."""
    proj = tmp_path / dir_basename
    (proj / "constraints").mkdir(parents=True)
    (proj / "constraints" / "config.mk").write_text(
        f"export DESIGN_NAME = {design_name}\nexport PLATFORM = {platform}\n",
        encoding="utf-8")
    return proj


def _write_heuristics(tmp_path: Path, fam: str, plat: str, recipes: dict) -> Path:
    hp = tmp_path / "heuristics.json"
    hp.write_text(json.dumps({"families": {fam: {"platforms": {plat: {
        "fix_recipes": recipes}}}}}), encoding="utf-8")
    return hp


def test_ranking_reorders_non_nangate_antenna_strategies():
    # sky130hd antenna -> catalog order is [diode_iters, density_relief].
    cfg = {"PLATFORM": "sky130hd", "CORE_UTILIZATION": "40"}
    drc = {"status": "fail", "total_violations": 10,
           "categories": {"M1_ANTENNA": {"count": 10}}}
    # Learned: density_relief is the proven winner here.
    recipes = {"strategies": {
        "antenna_density_relief": {"attempts": 8, "successes": 7, "failures": 1},
        "antenna_diode_iters":    {"attempts": 8, "successes": 1, "failures": 7},
    }, "n_sessions": 8}
    plan = dsf.build_plan(drc, {}, cfg, check="drc", recipes=recipes)
    ids = [s["id"] for s in plan["strategies"]]
    assert ids[0] == "antenna_density_relief"     # learned winner promoted
    assert "ranking" in plan and plan["ranking"][0]["strategy"] == "antenna_density_relief"


def test_cold_start_preserves_catalog_order():
    cfg = {"PLATFORM": "sky130hd", "CORE_UTILIZATION": "40"}
    drc = {"status": "fail", "total_violations": 10,
           "categories": {"M1_ANTENNA": {"count": 10}}}
    plan = dsf.build_plan(drc, {}, cfg, check="drc", recipes=None)
    ids = [s["id"] for s in plan["strategies"]]
    assert ids == ["antenna_diode_iters", "antenna_density_relief"]


def test_safety_density_addon_never_an_edit():
    # No strategy may ever edit PLACE_DENSITY_LB_ADDON (hard rule).
    cfg = {"PLATFORM": "sky130hd", "CORE_UTILIZATION": "40"}
    drc = {"status": "fail", "categories": {"M1_ANTENNA": {"count": 10}}}
    plan = dsf.build_plan(drc, {}, cfg, check="drc", recipes=None)
    for s in plan["strategies"]:
        assert "PLACE_DENSITY_LB_ADDON" not in s["config_edits"]


def test_load_recipes_keys_family_on_dir_basename_not_design_name(tmp_path):
    # Live case: dir basename iccad2015_unit18_in1 (-> family 'iccad2015')
    # while DESIGN_NAME is 'test' (-> would mis-key as family 'test').
    # The writer stores the recipe under the dir-basename family, so the
    # reader must look it up the same way (CANONICAL FAMILY RULE).
    proj = _make_project(tmp_path, "iccad2015_unit18_in1", "test")
    drc = {"status": "fail", "categories": {"MET3_SPACING": {"count": 5}}}
    recipe = {"strategies": {"antenna_diode_repair": {"attempts": 3,
              "successes": 3, "failures": 0}}, "n_sessions": 3}
    hp = _write_heuristics(tmp_path, "iccad2015", "nangate45",
                           {"drc": {"MET3_SPACING": recipe}})
    got = dsf._load_recipes(proj, check="drc", drc=drc, lvs={}, heuristics=hp)
    assert got == recipe


def test_load_recipes_explicit_family_wins_over_dir_basename(tmp_path):
    # DESIGN_NAME aes128_core maps (families.json) -> 'aes_xcrypt'; that
    # explicit mapping must win over the dir basename's split fallback.
    proj = _make_project(tmp_path, "secworks_aes128_core", "aes128_core")
    drc = {"status": "fail", "categories": {"MET3_SPACING": {"count": 5}}}
    recipe = {"strategies": {"antenna_diode_repair": {"attempts": 2,
              "successes": 2, "failures": 0}}, "n_sessions": 2}
    hp = _write_heuristics(tmp_path, "aes_xcrypt", "nangate45",
                           {"drc": {"MET3_SPACING": recipe}})
    got = dsf._load_recipes(proj, check="drc", drc=drc, lvs={}, heuristics=hp)
    assert got == recipe


def test_load_recipes_drc_coarse_antenna_fallback(tmp_path):
    # Dominant DRC category is a fine-grained *_ANTENNA class, but the only
    # stored DRC recipe is under the coarse 'antenna' bucket (backfill).
    # The reader must fall back to the coarse bucket.
    proj = _make_project(tmp_path, "fifo_basic", "fifo_basic")
    drc = {"status": "fail", "categories": {"METAL3_ANTENNA": {"count": 8}}}
    recipe = {"strategies": {"antenna_diode_repair": {"attempts": 4,
              "successes": 3, "failures": 1}}, "n_sessions": 4}
    hp = _write_heuristics(tmp_path, "fifo", "nangate45",
                           {"drc": {"antenna": recipe}})
    got = dsf._load_recipes(proj, check="drc", drc=drc, lvs={}, heuristics=hp)
    assert got == recipe


def test_load_recipes_exact_category_wins_over_coarse_bucket(tmp_path):
    # When the exact *_ANTENNA category recipe exists, it must be used —
    # the coarse fallback must NOT shadow it.
    proj = _make_project(tmp_path, "fifo_basic", "fifo_basic")
    drc = {"status": "fail", "categories": {"METAL3_ANTENNA": {"count": 8}}}
    exact = {"strategies": {"antenna_diode_repair": {"attempts": 9,
             "successes": 9, "failures": 0}}, "n_sessions": 9}
    coarse = {"strategies": {"antenna_density_relief": {"attempts": 1,
              "successes": 0, "failures": 1}}, "n_sessions": 1}
    hp = _write_heuristics(tmp_path, "fifo", "nangate45",
                           {"drc": {"METAL3_ANTENNA": exact, "antenna": coarse}})
    got = dsf._load_recipes(proj, check="drc", drc=drc, lvs={}, heuristics=hp)
    assert got == exact


def test_load_recipes_drc_coarse_beol_fallback(tmp_path):
    # A non-antenna dominant category falls back to the coarse 'beol' bucket.
    proj = _make_project(tmp_path, "fifo_basic", "fifo_basic")
    drc = {"status": "fail", "categories": {"MET3_SPACING": {"count": 8}}}
    recipe = {"strategies": {"route_effort": {"attempts": 2,
              "successes": 1, "failures": 1}}, "n_sessions": 2}
    hp = _write_heuristics(tmp_path, "fifo", "nangate45",
                           {"drc": {"beol": recipe}})
    got = dsf._load_recipes(proj, check="drc", drc=drc, lvs={}, heuristics=hp)
    assert got == recipe
