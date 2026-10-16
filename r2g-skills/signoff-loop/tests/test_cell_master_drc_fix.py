"""dont_use_violating_master: ban the cell master whose own layout fails the deck.

The gate is geometric, so these tests build DEF/LEF text instead of naming a
platform: every refusal below must come from the measurement, not an allowlist.
Each positive assertion is paired with a corruption that must flip it, because a
gate that only ever says yes on the happy fixture is not a gate.
"""
import sys
from pathlib import Path

import pytest

import attribute_drc_to_cells as attrib
import diagnose_signoff_fix as dsf


CELL_W, CELL_H = 10.0, 5.04
BAD = "pdk_fd_sc__aoi221_2"
GOOD = "pdk_fd_sc__aoi221_4"
OTHER = "pdk_fd_sc__nand2_1"


def lef_text(masters=(BAD, GOOD, OTHER)):
    return "\n".join(
        f"MACRO {m}\n  CLASS CORE ;\n  SIZE {CELL_W} BY {CELL_H} ;\nEND {m}"
        for m in masters) + "\n"


def def_text(rows):
    """rows: list of (inst, master, x_um, y_um, orient)."""
    body = "\n".join(
        f"- {n} {m}\n  + PLACED ( {int(x * 1000)} {int(y * 1000)} ) {o} ;"
        for n, m, x, y, o in rows)
    return (f"UNITS DISTANCE MICRONS 1000 ;\nDIEAREA ( 0 0 ) ( 500000 500000 ) ;\n"
            f"COMPONENTS {len(rows)} ;\n{body}\nEND COMPONENTS\n")


def vio(x, y, cat="CO.6a"):
    return {"category": cat, "cell": "top", "bbox": (x - 0.005, y - 0.005,
                                                     x + 0.005, y + 0.005)}


def placed(n, master, col, row, orient="N"):
    return (n, master, col * CELL_W, row * CELL_H, orient)


# ---------------------------------------------------------------- parsing

def test_def_components_parses_placement_in_microns():
    comps, scale = attrib.parse_def_components(
        def_text([placed("x1", BAD, 3, 2, "FS")]))
    assert scale == 1000.0
    assert comps == [{"name": "x1", "master": BAD, "x": 30.0, "y": 10.08,
                      "orient": "FS"}]


def test_unplaced_component_is_dropped_not_placed_at_the_origin():
    text = ("UNITS DISTANCE MICRONS 1000 ;\nCOMPONENTS 2 ;\n"
            f"- a {BAD}\n  + UNPLACED ;\n"
            f"- b {BAD}\n  + PLACED ( 0 0 ) N ;\nEND COMPONENTS\n")
    comps, _ = attrib.parse_def_components(text)
    assert [c["name"] for c in comps] == ["b"]


def test_lef_size_conflict_drops_the_master_rather_than_guessing(tmp_path):
    a, b = tmp_path / "a.lef", tmp_path / "b.lef"
    a.write_text(f"MACRO {BAD}\n  SIZE 10 BY 5.04 ;\nEND {BAD}\n")
    b.write_text(f"MACRO {BAD}\n  SIZE 12 BY 5.04 ;\nEND {BAD}\n")
    sizes, conflicts = attrib.parse_lef_macro_sizes([a, b])
    assert conflicts == [BAD] and BAD not in sizes


@pytest.mark.parametrize("orient", ["N", "S", "FN", "FS"])
def test_cell_frame_offset_is_orientation_invariant(orient):
    """The same defect inside the master must read the SAME cell-frame offset in
    every orientation -- otherwise a design whose rows alternate N/FS fails the
    cluster test and the real master is never found (the first attempt at this
    attributed to the wrong cell for exactly that reason)."""
    comp = {"x": 100.0, "y": 50.0, "orient": orient}
    want = (6.6675, 4.2)
    # Where the defect lands in ABSOLUTE coordinates for this orientation.
    ox, oy = want
    abs_xy = {
        "N": (100.0 + ox, 50.0 + oy),
        "FS": (100.0 + ox, 50.0 + CELL_H - oy),
        "FN": (100.0 + CELL_W - ox, 50.0 + oy),
        "S": (100.0 + CELL_W - ox, 50.0 + CELL_H - oy),
    }[orient]
    got = attrib.cell_frame_offset(*abs_xy, comp, (CELL_W, CELL_H))
    assert got == pytest.approx(want, abs=1e-9)


# ---------------------------------------------------------------- the gate

def one_master_fixture(n=8, orients=("N", "FS")):
    """n instances of BAD, alternating orientation, each with the defect."""
    rows, vios = [], []
    sizes = {BAD: (CELL_W, CELL_H), GOOD: (CELL_W, CELL_H), OTHER: (CELL_W, CELL_H)}
    for i in range(n):
        orient = orients[i % len(orients)]
        rows.append(placed(f"u{i}", BAD, i, i, orient))
        comp = {"x": i * CELL_W, "y": i * CELL_H, "orient": orient}
        # Place the violation so its CELL-FRAME offset is (6.6675, 4.2).
        if orient == "N":
            vios.append(vio(comp["x"] + 6.6675, comp["y"] + 4.2))
        else:
            vios.append(vio(comp["x"] + 6.6675, comp["y"] + CELL_H - 4.2))
    return vios, rows, sizes


def test_one_master_one_offset_is_eligible():
    vios, rows, sizes = one_master_fixture()
    comps, _ = attrib.parse_def_components(def_text(rows))
    r = attrib.attribute(vios, comps, sizes, dont_use_patterns=["*_1"])
    assert r["eligible"], r["reason"]
    assert r["dominant_master"] == BAD
    assert r["attributed"] == len(vios)
    assert r["per_master"][BAD]["offset_median_um"] == pytest.approx([6.6675, 4.2])
    assert r["per_master"][BAD]["offset_spread_um"] == [0.0, 0.0]
    assert r["surviving_siblings"] == [GOOD]


def test_router_geometry_between_cells_is_refused():
    """A violation in the routing channel above the rows belongs to no cell. This
    is the whole non-eligible population measured on gf180 (M4.2a, M3.2a, M4.1,
    ANT.16) -- 38 designs the gate must decline, not 'fix' with a cell ban."""
    vios, rows, sizes = one_master_fixture()
    comps, _ = attrib.parse_def_components(def_text(rows))
    far = [vio(c["x"] + 3.0, c["y"] + CELL_H + 2.0) for c in comps]
    r = attrib.attribute(far, comps, sizes)
    assert not r["eligible"]
    assert r["reason"] == "every_violation_is_outside_every_cell"


def test_offsets_that_scatter_are_refused_as_context_dependent():
    """Same master, but the offset moves per instance: that is an interaction
    with the neighbourhood, and banning the master is not the fix."""
    vios, rows, sizes = one_master_fixture()
    comps, _ = attrib.parse_def_components(def_text(rows))
    scattered = [vio(c["x"] + 1.0 + 0.4 * i, c["y"] + 2.0)
                 for i, c in enumerate(comps)]
    r = attrib.attribute(scattered, comps, sizes)
    assert not r["eligible"] and "spread" in r["reason"]


def test_violations_spread_over_many_masters_are_refused():
    rows, vios = [], []
    sizes = {BAD: (CELL_W, CELL_H), GOOD: (CELL_W, CELL_H), OTHER: (CELL_W, CELL_H)}
    for i in range(8):
        master = BAD if i < 4 else OTHER
        rows.append(placed(f"u{i}", master, i, 0))
        vios.append(vio(i * CELL_W + 6.6675, 4.2))
    comps, _ = attrib.parse_def_components(def_text(rows))
    r = attrib.attribute(vios, comps, sizes)
    assert not r["eligible"] and "owns only" in r["reason"]


def test_ban_that_would_starve_synthesis_is_refused():
    """With no surviving sibling the ban does not clean the design, it kills the
    mapping. Measured precondition: gf180's aoi221 ships _1/_2/_4 and _1 is
    already banned by the platform, so _4 is what makes the real fix safe."""
    vios, rows, sizes = one_master_fixture()
    comps, _ = attrib.parse_def_components(def_text(rows))
    sizes = {BAD: (CELL_W, CELL_H), OTHER: (CELL_W, CELL_H)}   # no GOOD sibling
    r = attrib.attribute(vios, comps, sizes)
    assert not r["eligible"] and "no" in r["reason"] and "synthesis" in r["reason"]


def test_sibling_already_covered_by_a_dont_use_pattern_does_not_count():
    vios, rows, sizes = one_master_fixture()
    comps, _ = attrib.parse_def_components(def_text(rows))
    r = attrib.attribute(vios, comps, sizes, dont_use_patterns=["*_4"])
    assert not r["eligible"] and "synthesis" in r["reason"]


@pytest.mark.parametrize("master", ["pdk_fd_sc__fill_2", "pdk_fd_sc__tapcell_2",
                                    "pdk_fd_sc__decap_2", "pdk_fd_sc__antenna_2"])
def test_flow_inserted_cells_are_refused(master):
    """DONT_USE_CELLS cannot remove a cell synthesis never chose -- tapcell,
    fill, decap and diode insertion all run after mapping."""
    rows = [placed(f"u{i}", master, i, 0) for i in range(8)]
    vios = [vio(i * CELL_W + 6.6675, 4.2) for i in range(8)]
    sizes = {master: (CELL_W, CELL_H),
             master.replace("_2", "_4"): (CELL_W, CELL_H)}
    comps, _ = attrib.parse_def_components(def_text(rows))
    r = attrib.attribute(vios, comps, sizes)
    assert not r["eligible"] and "inserted by the flow" in r["reason"]


# ------------------------------------------------ override (the silent no-op)

@pytest.mark.parametrize("line,name,clobbers", [
    ("export DONT_USE_CELLS = *_1", "DONT_USE_CELLS", True),
    ("export DONT_USE_CELLS := *_1", "DONT_USE_CELLS", True),
    ("DONT_USE_CELLS = *_1", "DONT_USE_CELLS", True),
    ("override export DONT_USE_CELLS = *_1", "DONT_USE_CELLS", True),
    ("export DONT_USE_CELLS ?= *_1", "DONT_USE_CELLS", False),   # defers
    ("export DONT_USE_CELLS += *_1", "DONT_USE_CELLS", False),   # appends
    ("export TECH_LEF                   ?= $(PLATFORM_DIR)/x.lef",
     "TECH_LEF", False),
])
def test_only_plain_assignment_clobbers_the_design_config(line, name, clobbers):
    """Which spellings need `override` on the design side. variables.mk includes
    the platform config AFTER the design config, so a plain platform assignment
    wins and the design-side `export` is a SILENT no-op; `?=`/`+=` do not."""
    keys = dsf.assigned_keys_from_text(line + "\n")
    assert (name in keys) is clobbers


def test_a_variable_assigned_both_ways_still_clobbers():
    keys = dsf.assigned_keys_from_text("export X ?= a\nexport X = b\n")
    assert "X" in keys


def test_dont_use_edit_needs_override_on_the_installed_platforms():
    """The load-bearing fact, read from the real checkout: gf180 and nangate45
    assign DONT_USE_CELLS plainly (so the ban needs `override`), sky130hd uses
    `+=` (so it must NOT be overridden). Skips when no ORFS is resolvable --
    find_flow_dir() reads the environment, which a test run may have scrubbed."""
    gf = dsf.platform_assigned_keys("gf180")
    n45 = dsf.platform_assigned_keys("nangate45")
    sky = dsf.platform_assigned_keys("sky130hd")
    if not (gf or n45 or sky):
        pytest.skip("no ORFS checkout to read platform configs from")
    assert "DONT_USE_CELLS" in gf
    assert "DONT_USE_CELLS" in n45
    assert "DONT_USE_CELLS" not in sky


def test_override_survives_a_later_stacked_apply():
    """A second accepted fix must not rewrite the block's `override export` back
    to a plain `export` -- that would silently un-apply the cell ban while the
    config still *looks* repaired."""
    base = "export PLATFORM = gf180\n"
    one = dsf.apply_edits(base, {"DONT_USE_CELLS": "*_1 X"},
                          override_keys=["DONT_USE_CELLS"])
    assert "override export DONT_USE_CELLS = *_1 X" in one
    two = dsf.apply_edits(one, {"MAX_REPAIR_ANTENNAS_ITER_DRT": "10"},
                          override_keys=["DONT_USE_CELLS"])
    assert "override export DONT_USE_CELLS = *_1 X" in two
    assert "export MAX_REPAIR_ANTENNAS_ITER_DRT = 10" in two
    assert dsf.parse_config(two)["DONT_USE_CELLS"] == "*_1 X"


def test_override_is_not_applied_blanket():
    """`override` also defeats make's command-line variables, and run_orfs.sh
    passes DESIGN_CONFIG/FLOW_VARIANT that way -- only declared keys get it."""
    out = dsf.apply_edits("", {"A": "1", "B": "2"}, override_keys=["A"])
    assert "override export A = 1" in out
    assert "\nexport B = 2" in out and "override export B" not in out


# ------------------------------------------------ the strategy as emitted

def test_strategy_carries_the_override_declaration_and_reruns_from_synth(tmp_path,
                                                                          monkeypatch):
    proj = tmp_path / "proj"
    (proj / "drc").mkdir(parents=True)
    vios, rows, sizes = one_master_fixture()
    (proj / "6_final.def").write_text(def_text(rows))
    lef = tmp_path / "sc.lef"
    lef.write_text(lef_text())
    (proj / "drc" / "6_drc.lyrdb").write_text("<report-database/>")

    monkeypatch.setattr(dsf, "_drc_geometry_inputs",
                        lambda p, c: (proj / "drc" / "6_drc.lyrdb",
                                      proj / "6_final.def", [str(lef)]))
    sys.path.insert(0, str(Path(dsf.__file__).resolve().parents[1] / "dashboard"))
    import render_drc_violation
    monkeypatch.setattr(render_drc_violation, "parse_lyrdb_violations",
                        lambda path: vios)

    strat = dsf._cell_master_drc_strategy(proj, {}, {"PLATFORM": "gf180",
                                                     "DONT_USE_CELLS": "*_1"})
    assert strat is not None
    assert strat["id"] == "dont_use_violating_master"
    assert strat["config_edits"] == {"DONT_USE_CELLS": f"*_1 {BAD}"}
    assert strat["override_keys"] == ["DONT_USE_CELLS"]
    assert strat["rerun_from"] == "synth"      # a cell ban changes the mapping
    assert strat["recheck"] == "drc"
    ev = strat["geometry_evidence"]
    assert ev["master"] == BAD and ev["rule"] == "CO.6a"
    assert ev["cell_frame_spread_um"] == [0.0, 0.0]
    assert ev["surviving_siblings"] == [GOOD]


def test_strategy_is_not_reoffered_once_the_master_is_already_banned(tmp_path,
                                                                     monkeypatch):
    proj = tmp_path / "proj"
    proj.mkdir()
    vios, rows, sizes = one_master_fixture()
    (proj / "6_final.def").write_text(def_text(rows))
    lef = tmp_path / "sc.lef"
    lef.write_text(lef_text())
    monkeypatch.setattr(dsf, "_drc_geometry_inputs",
                        lambda p, c: (proj / "x.lyrdb", proj / "6_final.def",
                                      [str(lef)]))
    sys.path.insert(0, str(Path(dsf.__file__).resolve().parents[1] / "dashboard"))
    import render_drc_violation
    monkeypatch.setattr(render_drc_violation, "parse_lyrdb_violations",
                        lambda path: vios)
    assert dsf._cell_master_drc_strategy(
        proj, {}, {"PLATFORM": "gf180", "DONT_USE_CELLS": f"*_1 {BAD}"}) is None


def test_missing_geometry_yields_no_strategy_not_a_crash(tmp_path):
    assert dsf._cell_master_drc_strategy(tmp_path, {}, {"PLATFORM": "gf180"}) is None
