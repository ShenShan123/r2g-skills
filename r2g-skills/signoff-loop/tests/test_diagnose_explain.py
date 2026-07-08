"""--explain mode: human rationale for WHY each recipe ranked (evidence count +
cross-platform corroboration + A/B provenance). Serves 'transfer' — the engineer
sees that a fix is trusted because it carried across N platforms, not one fluke."""
from __future__ import annotations
import json

import diagnose_signoff_fix as dsf


def _make_project(tmp_path, dir_basename, design_name, platform="sky130hd"):
    proj = tmp_path / dir_basename
    (proj / "constraints").mkdir(parents=True)
    (proj / "constraints" / "config.mk").write_text(
        f"export DESIGN_NAME = {design_name}\nexport PLATFORM = {platform}\n"
        "export CORE_UTILIZATION = 40\n", encoding="utf-8")
    (proj / "reports").mkdir(parents=True)
    return proj


def test_explain_field_reports_evidence_corroboration_and_provenance():
    # A recipe corroborated across 3 platforms (platform_count=3) must surface
    # an --explain rationale carrying its evidence count, the corroboration, and
    # the provenance string.
    cfg = {"PLATFORM": "sky130hd", "CORE_UTILIZATION": "40"}
    drc = {"status": "fail", "total_violations": 10,
           "categories": {"M1_ANTENNA": {"count": 10}}}
    recipes = {"strategies": {
        "antenna_density_relief": {"attempts": 8, "successes": 7, "failures": 1,
                                   "wins": 0, "platform_count": 3},
        "antenna_diode_iters":    {"attempts": 8, "successes": 1, "failures": 7,
                                   "wins": 0, "platform_count": 1},
    }, "n_sessions": 8}
    plan = dsf.build_plan(drc, {}, cfg, check="drc", recipes=recipes)
    lines = dsf.explain_ranking(plan)
    assert lines, "explain_ranking should emit at least one line"
    top = lines[0]
    assert "antenna_density_relief" in top
    assert "7/8" in top                     # successes/attempts evidence
    assert "3 platform" in top              # cross-platform corroboration
    assert "learned" in top                 # provenance surfaced
    # The corroboration boost should be called out for the corroborated recipe.
    assert "corroborat" in top.lower()


def test_explain_main_prints_rationale(tmp_path, capsys):
    proj = _make_project(tmp_path, "myantenna_top", "antenna_top")
    (proj / "reports" / "drc.json").write_text(json.dumps({
        "status": "fail", "total_violations": 6,
        "categories": {"M1_ANTENNA": {"count": 6}}}), encoding="utf-8")
    rc = dsf.main([str(proj), "--check", "drc", "--explain"])
    assert rc == 0
    out = capsys.readouterr().out
    # Cold start: rationale still prints with the catalog strategies + evidence.
    assert "antenna_diode_iters" in out or "antenna_density_relief" in out
    assert "platform" in out.lower()


def test_explain_includes_provenance_for_pooled_prior():
    # A pooled-only strategy's rationale carries its prior provenance + count.
    cfg = {"PLATFORM": "sky130hd", "CORE_UTILIZATION": "40"}
    drc = {"status": "fail", "total_violations": 4,
           "categories": {"M1_ANTENNA": {"count": 4}}}
    pooled = {"antenna_density_relief": {"attempts": 6, "successes": 5,
              "wins": 0, "platform_count": 2}}
    plan = dsf.build_plan(drc, {}, cfg, check="drc", recipes=None)
    # Re-rank with a pooled prior threaded in (mirrors the live --explain path).
    dsf._rank_plan_strategies(plan, None, pooled=pooled)
    lines = dsf.explain_ranking(plan)
    relief = next(l for l in lines if "antenna_density_relief" in l)
    assert "prior" in relief
    assert "2 platform" in relief
