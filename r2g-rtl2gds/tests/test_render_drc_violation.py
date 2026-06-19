"""Tests for the Win 4 vision-assisted DRC renderer (render_drc_violation.py).

Exercises the PURE core (lyrdb parse, crop_regions clustering / margin math, honest
no-coordinate degradation) and the env-gated, off-by-default escalation hook. KLayout is
NOT required: the only test that touches the subprocess monkeypatches it.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# conftest.py does not add scripts/dashboard/ to sys.path — add it here (tests import by
# plain module name, matching the repo convention).
DASHBOARD_DIR = Path(__file__).resolve().parents[1] / "scripts" / "dashboard"
if str(DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_DIR))

import render_drc_violation as rv  # noqa: E402


# --------------------------------------------------------------------------- #
# Sample lyrdb fixtures (mirror the real sky130hd 6_drc.lyrdb item format).
# --------------------------------------------------------------------------- #

LYRDB_WITH_COORDS = """<?xml version="1.0" encoding="utf-8"?>
<report-database>
 <items>
  <item>
   <category>'m3.2'</category>
   <cell>axis_switch</cell>
   <values>
    <value>edge-pair: (191.596,92.645;192.15,92.645)|(192.449,92.67;191.895,92.67)</value>
   </values>
  </item>
  <item>
   <category>'m3.2'</category>
   <cell>axis_switch</cell>
   <values>
    <value>edge-pair: (191.6,92.7;192.2,92.7)|(192.5,92.8;191.9,92.8)</value>
   </values>
  </item>
  <item>
   <category>'m3.2'</category>
   <cell>axis_switch</cell>
   <values>
    <value>edge-pair: (10.0,10.0;10.5,10.0)|(10.6,10.1;10.1,10.1)</value>
   </values>
  </item>
  <item>
   <category>'poly.9'</category>
   <cell>axis_switch</cell>
   <values>
    <value>polygon: (0,24.08;0,24.22;14.445,24.22;14.445,24.08)</value>
   </values>
  </item>
 </items>
</report-database>
"""

# Antenna-style lyrdb: items carry only float/text annotations, no coordinate geometry.
LYRDB_ANNOTATION_ONLY = """<?xml version="1.0" encoding="utf-8"?>
<report-database>
 <items>
  <item>
   <category>'METAL3_ANTENNA'</category>
   <cell>top</cell>
   <values>
    <value>[#agate] float: 0.02625</value>
    <value>[#ratio] float: 307.013333333</value>
    <value>[#adiodes] text: '(0)'</value>
   </values>
  </item>
 </items>
</report-database>
"""


@pytest.fixture
def lyrdb_coords(tmp_path: Path) -> Path:
    p = tmp_path / "6_drc.lyrdb"
    p.write_text(LYRDB_WITH_COORDS, encoding="utf-8")
    return p


@pytest.fixture
def lyrdb_annot(tmp_path: Path) -> Path:
    p = tmp_path / "6_drc.lyrdb"
    p.write_text(LYRDB_ANNOTATION_ONLY, encoding="utf-8")
    return p


# --------------------------------------------------------------------------- #
# Pure core: lyrdb parsing.
# --------------------------------------------------------------------------- #

def test_value_bbox_edge_pair():
    bb = rv._value_bbox("edge-pair: (191.596,92.645;192.15,92.645)|(192.449,92.67;191.895,92.67)")
    assert bb == (191.596, 92.645, 192.449, 92.67)


def test_value_bbox_polygon():
    bb = rv._value_bbox("polygon: (0,24.08;0,24.22;14.445,24.22;14.445,24.08)")
    assert bb == (0.0, 24.08, 14.445, 24.22)


def test_value_bbox_annotation_returns_none():
    # Antenna float/text annotations carry no layout coordinates.
    assert rv._value_bbox("[#ratio] float: 307.013333333") is None
    assert rv._value_bbox("[#adiodes] text: '(0)'") is None
    assert rv._value_bbox(None) is None


def test_parse_lyrdb_collects_coordinate_violations(lyrdb_coords):
    vios = rv.parse_lyrdb_violations(lyrdb_coords)
    assert len(vios) == 4
    cats = {v["category"] for v in vios}
    assert cats == {"'m3.2'", "'poly.9'"}
    for v in vios:
        assert len(v["bbox"]) == 4
        assert v["cell"] == "axis_switch"


def test_parse_lyrdb_skips_annotation_only(lyrdb_annot):
    # An antenna lyrdb has items but no coordinate geometry -> no violations.
    assert rv.parse_lyrdb_violations(lyrdb_annot) == []


def test_parse_lyrdb_missing_file(tmp_path):
    assert rv.parse_lyrdb_violations(tmp_path / "nope.lyrdb") == []


# --------------------------------------------------------------------------- #
# Pure core: crop_regions clustering + margin math.
# --------------------------------------------------------------------------- #

def test_crop_regions_clusters_nearby_and_separates_far(lyrdb_coords):
    vios = rv.parse_lyrdb_violations(lyrdb_coords)
    regions = rv.crop_regions(vios, margin_um=2.0)
    # m3.2: two markers near (~191) cluster into one; the (10,10) one is separate -> 2.
    # poly.9: one marker -> 1. Total 3 clusters.
    assert len(regions) == 3
    m32 = [r for r in regions if r["category"] == "'m3.2'"]
    assert len(m32) == 2
    big = max(m32, key=lambda r: r["n_violations"])
    assert big["n_violations"] == 2
    # Every cluster has a stable slug filename and a 4-tuple bbox.
    slugs = [r["cluster"] for r in regions]
    assert len(slugs) == len(set(slugs))
    for r in regions:
        assert len(r["bbox"]) == 4


def test_crop_regions_margin_expands_bbox():
    vios = [{"category": "m1.1", "cell": "c", "bbox": (10.0, 10.0, 12.0, 12.0)}]
    r0 = rv.crop_regions(vios, margin_um=0.0)[0]
    r2 = rv.crop_regions(vios, margin_um=2.0)[0]
    assert r0["bbox"] == (10.0, 10.0, 12.0, 12.0)
    assert r2["bbox"] == (8.0, 8.0, 14.0, 14.0)


def test_crop_regions_larger_margin_merges_clusters():
    # Two markers 5um apart: separate at margin 1, merged at margin 3.
    vios = [
        {"category": "m1.1", "cell": "c", "bbox": (0.0, 0.0, 1.0, 1.0)},
        {"category": "m1.1", "cell": "c", "bbox": (6.0, 0.0, 7.0, 1.0)},
    ]
    assert len(rv.crop_regions(vios, margin_um=1.0)) == 2
    assert len(rv.crop_regions(vios, margin_um=3.0)) == 1


def test_crop_regions_groups_by_category():
    # Spatially coincident but different categories must NOT merge (fix is per-rule).
    vios = [
        {"category": "m1.1", "cell": "c", "bbox": (0.0, 0.0, 1.0, 1.0)},
        {"category": "m2.1", "cell": "c", "bbox": (0.0, 0.0, 1.0, 1.0)},
    ]
    regions = rv.crop_regions(vios, margin_um=2.0)
    assert len(regions) == 2
    assert {r["category"] for r in regions} == {"m1.1", "m2.1"}


def test_crop_regions_max_clusters_cap():
    vios = [{"category": f"r{i}", "cell": "c", "bbox": (i * 100.0, 0.0, i * 100.0 + 1, 1.0)}
            for i in range(40)]
    regions = rv.crop_regions(vios, margin_um=1.0, max_clusters=5)
    assert len(regions) == 5


def test_crop_regions_empty_and_bad_input():
    assert rv.crop_regions([], margin_um=2.0) == []
    assert rv.crop_regions(None, margin_um=2.0) == []
    # Violations without a valid 4-tuple bbox are skipped, not crashed on.
    assert rv.crop_regions([{"category": "x", "bbox": (1, 2)}], margin_um=2.0) == []


def test_crop_regions_rejects_negative_margin():
    with pytest.raises(ValueError):
        rv.crop_regions([], margin_um=-1.0)


def test_slug_is_filesystem_safe():
    assert rv._slug("'m3.2'") == "m3_2"
    assert rv._slug("METAL3_ANTENNA") == "metal3_antenna"
    assert rv._slug("") == "drc"


# --------------------------------------------------------------------------- #
# Honest no-coordinate degradation.
# --------------------------------------------------------------------------- #

def test_coordinate_status_available(lyrdb_coords):
    cov = rv.coordinate_status(lyrdb_coords)
    assert cov["available"] is True
    assert cov["n_coordinate_violations"] == 4


def test_coordinate_status_annotation_only(lyrdb_annot):
    cov = rv.coordinate_status(lyrdb_annot)
    assert cov["available"] is False
    assert cov["n_coordinate_violations"] == 0
    assert "no coordinate-bearing" in cov["reason"]


def test_coordinate_status_missing_lyrdb(tmp_path):
    cov = rv.coordinate_status(tmp_path / "nope.lyrdb")
    assert cov["available"] is False
    assert "no lyrdb" in cov["reason"]


# --------------------------------------------------------------------------- #
# KLayout soft dependency (no real tool needed).
# --------------------------------------------------------------------------- #

def test_klayout_cmd_none_when_absent(monkeypatch):
    monkeypatch.delenv("KLAYOUT_CMD", raising=False)
    monkeypatch.setattr(rv.shutil, "which", lambda *_a, **_k: None)
    monkeypatch.setattr(rv.Path, "is_file", lambda self: False)
    assert rv.klayout_cmd() is None


def test_render_regions_skips_when_klayout_absent(tmp_path, monkeypatch):
    gds = tmp_path / "6_final.gds"
    gds.write_text("dummy", encoding="utf-8")
    monkeypatch.setattr(rv, "klayout_cmd", lambda: None)
    regions = [{"category": "m1.1", "cluster": "m1_1_000", "bbox": (0, 0, 5, 5),
                "n_violations": 1, "cells": ["c"]}]
    res = rv.render_regions(gds, regions, tmp_path / "out")
    assert res["rendered"] == []
    assert res["skipped"] == "klayout_not_installed"


def test_render_regions_no_gds(tmp_path):
    res = rv.render_regions(tmp_path / "missing.gds", [{"cluster": "x", "bbox": (0, 0, 1, 1)}],
                            tmp_path / "out")
    assert res["skipped"] == "no_gds"


def test_render_regions_invokes_klayout_when_present(tmp_path, monkeypatch):
    gds = tmp_path / "6_final.gds"
    gds.write_text("dummy", encoding="utf-8")
    out_dir = tmp_path / "out"
    regions = [{"category": "'m3.2'", "cluster": "m3_2_000", "bbox": (0, 0, 5, 5),
                "n_violations": 2, "cells": ["c"]}]

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        # Emulate KLayout writing the PNG the script asked for.
        (out_dir / "m3_2_000.png").write_bytes(b"\x89PNG")
        class R:  # noqa: D401 - minimal CompletedProcess stand-in
            returncode = 0
        return R()

    monkeypatch.setattr(rv, "klayout_cmd", lambda: "/fake/klayout")
    monkeypatch.setattr(rv.subprocess, "run", fake_run)
    res = rv.render_regions(gds, regions, out_dir)
    assert captured["cmd"][0] == "/fake/klayout"
    assert "-b" in captured["cmd"] and "-nc" in captured["cmd"]
    assert res["skipped"] is None
    assert len(res["rendered"]) == 1


# --------------------------------------------------------------------------- #
# Escalation hook: off-by-default, env-gated, additive, fail-soft.
# --------------------------------------------------------------------------- #

def _drc_residual_plan():
    return {"check": "drc", "status": "residual",
            "residual_reason": "antenna: all real-fix strategies exhausted", "strategies": []}


def test_vision_enabled_gate(monkeypatch):
    monkeypatch.delenv("R2G_VISION_DRC", raising=False)
    assert rv.vision_enabled() is False
    monkeypatch.setenv("R2G_VISION_DRC", "1")
    assert rv.vision_enabled() is True
    monkeypatch.setenv("R2G_VISION_DRC", "0")
    assert rv.vision_enabled() is False


def test_attach_vision_noop_when_disabled(tmp_path, monkeypatch):
    monkeypatch.delenv("R2G_VISION_DRC", raising=False)
    plan = _drc_residual_plan()
    before = dict(plan)
    out = rv.attach_vision_artifacts(plan, tmp_path)
    # Off by default: plan unchanged, no "vision" key added (text path byte-identical).
    assert out is plan
    assert "vision" not in plan
    assert plan == before


def test_attach_vision_noop_when_not_drc_residual(monkeypatch, tmp_path):
    monkeypatch.setenv("R2G_VISION_DRC", "1")
    # A clean DRC plan, or one with remaining strategies, must NOT trigger rendering.
    clean = {"check": "drc", "status": "clean", "strategies": []}
    rv.attach_vision_artifacts(clean, tmp_path)
    assert "vision" not in clean

    has_fix = {"check": "drc", "status": "fail",
               "strategies": [{"id": "antenna_diode_iters"}], "residual_reason": None}
    rv.attach_vision_artifacts(has_fix, tmp_path)
    assert "vision" not in has_fix

    lvs_plan = {"check": "lvs", "status": "residual", "residual_reason": "x", "strategies": []}
    rv.attach_vision_artifacts(lvs_plan, tmp_path)
    assert "vision" not in lvs_plan


def test_attach_vision_fires_on_residual_with_coords(tmp_path, monkeypatch):
    monkeypatch.setenv("R2G_VISION_DRC", "1")
    proj = tmp_path / "proj"
    (proj / "drc").mkdir(parents=True)
    (proj / "drc" / "6_drc.lyrdb").write_text(LYRDB_WITH_COORDS, encoding="utf-8")
    gds = proj / "backend" / "RUN_x" / "results"
    gds.mkdir(parents=True)
    (gds / "6_final.gds").write_text("dummy", encoding="utf-8")

    # Stub the actual KLayout render so the test needs no tool.
    monkeypatch.setattr(rv, "render_regions",
                        lambda *a, **k: {"rendered": ["a.png", "b.png"], "skipped": None})
    plan = _drc_residual_plan()
    rv.attach_vision_artifacts(plan, proj)
    assert plan["vision"]["enabled"] is True
    assert plan["vision"]["coordinate_status"]["available"] is True
    assert len(plan["vision"]["clusters"]) == 3
    assert plan["vision"]["rendered"] == ["a.png", "b.png"]


def test_attach_vision_degrades_when_no_coordinates(tmp_path, monkeypatch):
    monkeypatch.setenv("R2G_VISION_DRC", "1")
    proj = tmp_path / "proj"
    (proj / "drc").mkdir(parents=True)
    (proj / "drc" / "6_drc.lyrdb").write_text(LYRDB_ANNOTATION_ONLY, encoding="utf-8")
    gds = proj / "backend" / "RUN_x" / "results"
    gds.mkdir(parents=True)
    (gds / "6_final.gds").write_text("dummy", encoding="utf-8")

    plan = _drc_residual_plan()
    rv.attach_vision_artifacts(plan, proj)
    v = plan["vision"]
    assert v["coordinate_status"]["available"] is False
    assert v["skipped"] == "no_coordinates_full_gds_fallback"
    # Honest degradation: falls back to the full-GDS preview path.
    assert v["fallback_full_gds"].endswith("6_final.gds")


def test_attach_vision_failsoft_on_error(tmp_path, monkeypatch):
    monkeypatch.setenv("R2G_VISION_DRC", "1")
    # Force an internal error; the hook must catch it and never break diagnosis.
    monkeypatch.setattr(rv, "find_lyrdb", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")))
    plan = _drc_residual_plan()
    rv.attach_vision_artifacts(plan, tmp_path)
    assert plan["vision"]["enabled"] is True
    assert "error" in plan["vision"]
    assert plan["vision"]["rendered"] == []


# --------------------------------------------------------------------------- #
# Artifact location helpers.
# --------------------------------------------------------------------------- #

def test_find_final_gds(tmp_path):
    proj = tmp_path / "proj"
    r1 = proj / "backend" / "RUN_2026-06-14" / "results"
    r2 = proj / "backend" / "RUN_2026-06-15" / "results"
    r1.mkdir(parents=True)
    r2.mkdir(parents=True)
    (r1 / "6_final.gds").write_text("old", encoding="utf-8")
    (r2 / "6_final.gds").write_text("new", encoding="utf-8")
    # Picks the latest RUN dir (reverse-sorted timestamp).
    assert rv.find_final_gds(proj) == r2 / "6_final.gds"


def test_find_final_gds_none(tmp_path):
    assert rv.find_final_gds(tmp_path / "empty") is None
