"""Graded severity (knowledge/severity.py, R2G memory Phase G0)."""
from __future__ import annotations

import json

import severity

GRT = """[INFO GRT-0096] Final congestion report:
Layer         Resource        Demand        Usage (%)    Max H / Max V / Total Congestion
met1             38778         34430           88.79%             4 /  1 / 368
Total           120661        108272           89.73%            12 / 12 / 2474
[ERROR GRT-0116] Global routing finished with congestion."""


def test_parsers():
    assert severity.grt_total_congestion(GRT) == 2474.0
    flw = "[INFO IFP-0104] Effective utilization:                0.879\n[ERROR FLW-0024] Place density"
    assert severity.placement_density_excess(flw, 0.2) == round(1.14 * 0.879 + 0.2 - 1.0, 4)
    placed = flw + "\nPlacement density is 0.578, computed from PLACE_DENSITY_LB_ADDON  0.2 and lower bound 0.46"
    assert severity.placement_density_excess(placed, 0.2) == round(0.578 - 1.0, 4)   # placer wins
    ppl = "Increase the die perimeter from 444.62um to 980.56um."
    assert severity.perimeter_excess(ppl) == round(980.56 / 444.62 - 1, 4)
    assert severity.grt_total_congestion("nothing") is None


def test_from_project(tmp_path):
    p = tmp_path / "p"
    (p / "reports").mkdir(parents=True)
    (p / "constraints").mkdir()
    (p / "constraints" / "config.mk").write_text(
        "export PLACE_DENSITY_LB_ADDON = 0.2\n# >>> r2g signoff-fix (auto) >>>\n"
        "export PLACE_DENSITY_LB_ADDON = 0.1\n# <<< r2g signoff-fix (auto) <<<\n")
    run = p / "backend" / "RUN_1"
    run.mkdir(parents=True)
    (run / "flow.log").write_text("[INFO IFP-0104] Effective utilization:   0.879\n" + GRT)
    assert severity.from_project(p, "orfs_stage", "place") == round(1.14 * 0.879 + 0.1 - 1.0, 4)
    assert severity.from_project(p, "orfs_stage", "route") == 2474.0
    (p / "reports" / "drc.json").write_text(json.dumps({"total_violations": 6}))
    assert severity.from_project(p, "drc", "li.3") == 6.0
    assert severity.from_project(tmp_path / "missing", "orfs_stage", "place") is None
