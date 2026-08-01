"""Regressions for the vendored R2G2.0 four-stage pipeline and its ORFS adapters.

Every test here pins one of the deltas recorded in
``scripts/r2g2/R2G2_UPSTREAM.md``. They exist because upstream was verified on a
single nangate45 sample, so each defect is invisible on that platform and fires
on sky130/gf180/ihp -- exactly the ones this skill defaults to. If a future
R2G2.0 drop is re-vendored and a delta is dropped on the floor, these fail.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SKILL_ROOT = Path(__file__).resolve().parents[1]
R2G2_DIR = SKILL_ROOT / "scripts" / "r2g2"
ADAPT_DIR = SKILL_ROOT / "scripts" / "stage_dataset"


def _load(filename: str, name: str, directory: Path = R2G2_DIR):
    """Import a numbered vendored script by path (it cannot be `import`ed)."""
    path = directory / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, path
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def stage02():
    pytest.importorskip("torch")
    return _load("02_extract_features.py", "t_r2g2_features")


@pytest.fixture(scope="module")
def stage04():
    pytest.importorskip("torch")
    return _load("04_assemble_heterograph.py", "t_r2g2_assemble")


@pytest.fixture(scope="module")
def stage01():
    pytest.importorskip("torch")
    return _load("01_build_base_graph.py", "t_r2g2_base")


# --------------------------------------------------------------------------
# D1: FN/FS orientation transforms were swapped.
# --------------------------------------------------------------------------

# LEF/DEF: FN = MY (mirror about Y, changes x), FS = MX (mirror about X, changes y).
# Verified against OpenDB placed pin locations (aes_core/sky130hd): the upstream
# table scored 0/190 on FS instances, this one scores 190/190.
EXPECTED_ORIENTS = {
    "N": (1.0, 2.0),
    "S": (9.0, 18.0),
    "W": (18.0, 1.0),
    "E": (2.0, 9.0),
    "FN": (9.0, 2.0),
    "FS": (1.0, 18.0),
    "FW": (2.0, 1.0),
    "FE": (18.0, 9.0),
}


@pytest.mark.parametrize("orient,expected", sorted(EXPECTED_ORIENTS.items()))
def test_transform_pin_matches_lefdef_orientation(stage02, orient, expected):
    assert stage02.transform_pin(1.0, 2.0, orient, 10.0, 20.0) == expected


def test_transform_pin_agrees_with_techlib_apply_orient(stage02):
    """The vendored transform and this skill's own must not diverge.

    techlib.lef.apply_orient is the copy that was validated against OpenDB, so a
    disagreement means one of the two regressed.
    """
    sys.path.insert(0, str(SKILL_ROOT / "scripts" / "extract"))
    from techlib.lef import apply_orient

    for orient in EXPECTED_ORIENTS:
        assert stage02.transform_pin(1.0, 2.0, orient, 10.0, 20.0) == pytest.approx(
            apply_orient(1.0, 2.0, orient, 10.0, 20.0)
        ), orient


def test_fs_is_not_the_upstream_swapped_form(stage02):
    """Guard the specific regression rather than just the correct answer."""
    width, height = 10.0, 20.0
    upstream_fs = (width - 1.0, 2.0)  # what upstream returned for FS
    assert stage02.transform_pin(1.0, 2.0, "FS", width, height) != upstream_fs


# --------------------------------------------------------------------------
# D2: Liberty parsing must tolerate quoted identifiers and values (sky130).
# --------------------------------------------------------------------------

QUOTED_LIBERTY = textwrap.dedent(
    """\
    library ("demo_lib") {
      capacitive_load_unit (1.0,"pf");
      nom_voltage : "1.8";
      cell ("demo__dff_1") {
        area : "3.75";
        cell_leakage_power : "0.0012";
        ff ("IQ","IQ_N") {
        }
        pin ("CLK") {
          direction : "input";
          clock : "true";
          capacitance : "0.004";
        }
        pin ("D") {
          direction : "input";
          capacitance : "0.002";
          max_transition : "1.5";
        }
        pin ("Q") {
          direction : "output";
          max_capacitance : "0.3";
          function : "IQ";
        }
      }
    }
    """
)

# The nangate45 spelling of the same library. `function` is quoted in *both*
# dialects (Liberty requires it), so only the identifiers and scalar values that
# sky130 quotes are unquoted here.
UNQUOTED_LIBERTY = "\n".join(
    line if line.lstrip().startswith("function") else line.replace('"', "")
    for line in QUOTED_LIBERTY.splitlines()
) + "\n"


@pytest.fixture
def quoted_lib(tmp_path):
    path = tmp_path / "quoted.lib"
    path.write_text(QUOTED_LIBERTY, encoding="utf-8")
    return path


def test_stage02_liberty_parses_quoted_cell_and_pins(stage02, quoted_lib):
    db = stage02.parse_liberty([quoted_lib])
    assert db["v_nom"] == pytest.approx(1.8)
    # capacitive_load_unit ("pf") -> 1000 fF per unit; unparsed it would be 1.0
    # and every pin capacitance would come out 1000x too small.
    assert db["cap_scale_ff"] == pytest.approx(1000.0)

    assert "DEMO__DFF_1" in db["cells"], db["cells"].keys()
    cell = db["cells"]["DEMO__DFF_1"]
    assert cell["is_ff"] is True
    assert cell["area"] == pytest.approx(3.75)
    assert cell["power"] == pytest.approx(0.0012)

    pins = cell["pins"]
    assert {name: pin["direction"] for name, pin in pins.items()} == {
        "CLK": "INPUT",
        "D": "INPUT",
        "Q": "OUTPUT",
    }
    assert pins["CLK"]["clock"] is True
    assert pins["CLK"]["cap_fF"] == pytest.approx(4.0)
    assert pins["D"]["max_transition_ns"] == pytest.approx(1.5)
    assert pins["Q"]["max_capacitance_fF"] == pytest.approx(300.0)
    assert pins["Q"]["function"] == "IQ"


def test_stage01_liberty_parses_quoted_cell_and_directions(stage01, quoted_lib):
    directions, masters = stage01.parse_liberty([quoted_lib])
    # A quoted master name leaks '"' into cell_type_id lookups and drops every
    # gate to UNKNOWN -- the sky130-wide failure this guards.
    assert masters == ["demo__dff_1"]
    assert directions["DEMO__DFF_1"] == {"CLK": "INPUT", "D": "INPUT", "Q": "OUTPUT"}


def test_quoted_and_unquoted_liberty_agree(stage02, tmp_path):
    """Same library, quoted vs not, must parse identically."""
    quoted = tmp_path / "q.lib"
    plain = tmp_path / "p.lib"
    quoted.write_text(QUOTED_LIBERTY, encoding="utf-8")
    plain.write_text(UNQUOTED_LIBERTY, encoding="utf-8")
    assert stage02.parse_liberty([quoted])["cells"] == stage02.parse_liberty([plain])["cells"]


# --------------------------------------------------------------------------
# D8: gzipped Liberty (gf180 ships only .lib.gz) must parse, and an empty
# parse must fail closed instead of producing a silently featureless dataset.
# --------------------------------------------------------------------------


@pytest.fixture
def gzipped_lib(tmp_path):
    import gzip

    path = tmp_path / "quoted.lib.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write(QUOTED_LIBERTY)
    return path


def test_stage01_reads_gzipped_liberty(stage01, gzipped_lib):
    directions, masters = stage01.parse_liberty([gzipped_lib])
    assert masters == ["demo__dff_1"]
    assert directions["DEMO__DFF_1"]["CLK"] == "INPUT"


def test_stage02_reads_gzipped_liberty(stage02, gzipped_lib, quoted_lib):
    """Compressed and plain must yield identical cell tables."""
    assert stage02.parse_liberty([gzipped_lib])["cells"] == stage02.parse_liberty([quoted_lib])["cells"]


def test_glob_liberty_finds_compressed_files(stage01, tmp_path):
    (tmp_path / "a.lib").write_text("", encoding="utf-8")
    (tmp_path / "b.lib.gz").write_bytes(b"")
    (tmp_path / "c.txt").write_text("", encoding="utf-8")
    names = sorted(p.name for p in stage01.glob_liberty(tmp_path))
    assert names == ["a.lib", "b.lib.gz"]


def test_stage01_fails_closed_on_an_unparseable_liberty(stage01, tmp_path, monkeypatch):
    """A Liberty that yields zero cells must raise, not pass vacuously.

    Upstream only raised for masters it FOUND but could not encode, so a file it
    could not read at all (gf180's gzip decoded as text) sailed through with
    every Liberty feature dead and zero gate->gate edges.
    """
    source = (R2G2_DIR / "01_build_base_graph.py").read_text(encoding="utf-8")
    assert "if not liberty_masters:" in source
    assert "Liberty解析结果为空" in source

    empty = tmp_path / "not_really.lib"
    empty.write_text("this is not liberty\n", encoding="utf-8")
    _, masters = stage01.parse_liberty([empty])
    assert masters == []  # the condition the guard fires on


def test_stage02_fails_closed_on_an_empty_cell_table():
    source = (R2G2_DIR / "02_extract_features.py").read_text(encoding="utf-8")
    assert 'if not lib["cells"]:' in source
    assert "Liberty解析结果为空" in source


def test_encode_map_generator_rejects_an_empty_liberty(tmp_path):
    """Exit-code contract through the CLI."""
    bogus = tmp_path / "empty.lib"
    bogus.write_text("nothing here\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(ADAPT_DIR / "build_encode_map.py"),
         "--platform", "demo", "--lib", str(bogus), "--out", str(tmp_path / "m.csv")],
        capture_output=True, text=True, timeout=900,
    )
    assert result.returncode != 0
    assert "no Liberty cells parsed" in (result.stdout + result.stderr)


def test_explicit_liberty_is_not_widened_by_a_directory_scan(encode_map_mod, tmp_path):
    """A platform lib directory is not a library.

    gf180's holds 30 files spanning TWO cell libraries (7-track and 9-track)
    across every PVT corner. Scanning it on top of the ORFS-resolved Liberty
    would put cells from the wrong physical library into the id vocabulary and
    make electrical values depend on glob order.
    """
    lib_dir = tmp_path / "lib"
    lib_dir.mkdir()
    chosen = lib_dir / "fam_9t__ff_n40C.lib"
    chosen.write_text(QUOTED_LIBERTY, encoding="utf-8")
    for other in ("fam_7t__ff_125C.lib", "fam_9t__ss_125C.lib"):
        (lib_dir / other).write_text(QUOTED_LIBERTY, encoding="utf-8")

    only_explicit = encode_map_mod.liberty_files(lib_dir, [chosen])
    assert [p.name for p in only_explicit] == [chosen.name]

    # …but an operator who explicitly asks for the directory still gets it.
    scanned = encode_map_mod.liberty_files(lib_dir, [chosen], glob_dir=True)
    assert len(scanned) == 3


def test_sample_config_does_not_scan_the_platform_lib_dir():
    source = (ADAPT_DIR / "make_sample_config.py").read_text(encoding="utf-8")
    assert '"yosys_hierarchy_lib_dir": str(libs[0].parent)' not in source
    assert "Deliberately NOT setting yosys_hierarchy_lib_dir" in source


def test_encode_map_generator_globs_compressed_liberty(encode_map_mod, tmp_path):
    import gzip

    lib_dir = tmp_path / "lib"
    lib_dir.mkdir()
    with gzip.open(lib_dir / "x.lib.gz", "wt", encoding="utf-8") as handle:
        handle.write(QUOTED_LIBERTY)
    found = encode_map_mod.liberty_files(lib_dir, [])
    assert [p.name for p in found] == ["x.lib.gz"]


# --------------------------------------------------------------------------
# D10: LEF pin geometry must come from POLYGON as well as RECT (gf180).
# D11: well-tap detection must be platform-aware (gf180 uses __filltie).
# --------------------------------------------------------------------------

POLYGON_LEF = textwrap.dedent(
    """\
    MACRO demo_poly
      CLASS core ;
      SIZE 2.24 BY 5.04 ;
      PIN I
        DIRECTION INPUT ;
        PORT
          LAYER Metal1 ;
            POLYGON 0.71 1.21 1.015 1.21 1.015 2.3 1.07 2.3 1.07 2.53 0.71 2.53  ;
        END
      END I
    END demo_poly
    MACRO demo_rect
      CLASS core ;
      SIZE 1.0 BY 2.0 ;
      PIN A
        DIRECTION INPUT ;
        PORT
          LAYER Metal1 ;
            RECT 0.1 0.2 0.3 0.6 ;
        END
      END A
    END demo_rect
    """
)


def test_lef_pin_geometry_reads_polygon_pins(stage02, tmp_path):
    """gf180 std cells use POLYGON for essentially every signal pin.

    With no geometry a pin falls back to the cell origin, `pin_position_valid`
    goes 0 for every pin, and because `stage_net_geometry` needs geometry on
    every endpoint, EVERY net's HPWL/bbox becomes NaN — 13 all-NaN columns on
    gf180 before this fix.
    """
    lef = tmp_path / "poly.lef"
    lef.write_text(POLYGON_LEF, encoding="utf-8")
    macros = stage02.parse_lef_geometry([lef])

    assert "DEMO_POLY" in macros
    # bbox of the polygon is x∈[0.71,1.07], y∈[1.21,2.53] → centre (0.89, 1.87)
    assert macros["DEMO_POLY"]["pins"]["I"] == pytest.approx((0.89, 1.87))
    # RECT pins keep working unchanged
    assert macros["DEMO_RECT"]["pins"]["A"] == pytest.approx((0.2, 0.4))


def test_lef_polygon_needs_at_least_three_points(stage02, tmp_path):
    """A malformed POLYGON must be ignored, not turned into a bogus centre."""
    lef = tmp_path / "bad.lef"
    lef.write_text(
        "MACRO m\n  SIZE 1 BY 1 ;\n  PIN A\n    PORT\n      POLYGON 0.1 0.2 ;\n    END\n"
        "  END A\nEND m\n",
        encoding="utf-8",
    )
    macros = stage02.parse_lef_geometry([lef])
    assert macros["M"]["pins"] == {}


def test_tap_patterns_are_platform_aware(config_mod):
    """gf180's well-tap/endcap masters contain no 'TAP'."""
    assert "TAP" in config_mod.tap_master_patterns("nangate45")
    gf180 = config_mod.tap_master_patterns("gf180")
    assert "FILLTIE" in gf180 and "ENDCAP" in gf180


def test_feature_stage_reads_tap_patterns_from_config():
    source = (R2G2_DIR / "02_extract_features.py").read_text(encoding="utf-8")
    assert 'cfg.get("tap_master_patterns")' in source
    assert 'if "TAP" in str(row.get("master", "")).upper()' not in source


def test_sample_config_emits_tap_patterns():
    source = (ADAPT_DIR / "make_sample_config.py").read_text(encoding="utf-8")
    assert '"tap_master_patterns": tap_master_patterns(platform)' in source


# --------------------------------------------------------------------------
# D3: the congestion grid is platform-derived, not a nangate45 constant.
# --------------------------------------------------------------------------


def test_congestion_grid_default_is_nangate45_unchanged(stage02, stage04):
    """The default must stay 2.1 um so the verified nangate45 build is bit-identical."""
    assert stage02.resolve_congestion_grid_um({}) == pytest.approx(2.1)
    assert stage04.resolve_congestion_grid_um({}) == pytest.approx(2.1)


def test_congestion_grid_follows_platform_pitch(stage02, stage04):
    cfg = {"congestion_grid_pitch_um": 0.46}  # sky130hd met3
    assert stage02.resolve_congestion_grid_um(cfg) == pytest.approx(6.9)
    assert stage04.resolve_congestion_grid_um(cfg) == pytest.approx(6.9)


def test_congestion_grid_explicit_override(stage02, stage04):
    cfg = {"congestion_grid_um": 3.5, "congestion_grid_pitch_um": 0.46}
    assert stage02.resolve_congestion_grid_um(cfg) == pytest.approx(3.5)
    assert stage04.resolve_congestion_grid_um(cfg) == pytest.approx(3.5)


@pytest.mark.parametrize(
    "cfg",
    [
        {"congestion_grid_um": 0},
        {"congestion_grid_um": -1},
        {"congestion_grid_pitch_um": 0},
        {"congestion_grid_tracks": 0},
    ],
)
def test_congestion_grid_rejects_nonpositive(stage02, stage04, cfg):
    # Fail closed: a zero grid would divide the die into one cell and silently
    # flatten every congestion feature.
    with pytest.raises(ValueError):
        stage02.resolve_congestion_grid_um(cfg)
    with pytest.raises(ValueError):
        stage04.resolve_congestion_grid_um(cfg)


@pytest.mark.parametrize(
    "tracks,pitch,dbu,platform",
    [(15, 0.14, 2000, "nangate45"), (15, 0.46, 1000, "sky130hd"),
     (15, 0.48, 1000, "sky130hs"), (15, 0.34, 1000, "li1-pitch")],
)
def test_grid_survives_the_dbu_round_trip(stage02, tracks, pitch, dbu, platform):
    """D7: the feature grid and the DBU-round-tripped label grid must compare equal.

    `02` records `tracks * pitch`; `03` records `round(grid*dbu)/dbu`. Those are
    bit-identical only for pitches that happen to land on a representable DBU
    multiple -- true for nangate45 and sky130hd, false for sky130hs. Upstream
    compared them with exact float equality, so a correct sky130hs build failed
    with "拥塞特征与标签的GCell规格不一致".
    """
    import math

    feature = stage02.resolve_congestion_grid_um(
        {"congestion_grid_tracks": tracks, "congestion_grid_pitch_um": pitch}
    )
    label = round(feature * dbu) / dbu
    assert math.isclose(feature, label, rel_tol=1e-9, abs_tol=1e-12), platform


def test_grid_comparison_still_rejects_a_real_mismatch(stage02):
    """The tolerance must not swallow an actually different grid."""
    import math

    nangate = stage02.resolve_congestion_grid_um({})                       # 2.1
    sky = stage02.resolve_congestion_grid_um({"congestion_grid_pitch_um": 0.48})  # 7.2
    assert not math.isclose(nangate, sky, rel_tol=1e-9, abs_tol=1e-12)


def test_assemble_grid_check_is_tolerant_not_exact():
    source = (R2G2_DIR / "04_assemble_heterograph.py").read_text(encoding="utf-8")
    assert "label_grid_steps != {feature_grid_step}" not in source
    assert "grid_mismatch" in source and "math.isclose" in source


def test_stage02_and_stage04_grid_resolvers_agree(stage02, stage04):
    """They are deliberate duplicates (04 does not import 02); keep them in step."""
    for cfg in ({}, {"congestion_grid_pitch_um": 0.19}, {"congestion_grid_tracks": 20},
                {"congestion_grid_tracks": 8, "congestion_grid_pitch_um": 0.34}):
        assert stage02.resolve_congestion_grid_um(cfg) == pytest.approx(
            stage04.resolve_congestion_grid_um(cfg)
        ), cfg


# --------------------------------------------------------------------------
# D4: SciPy must not be a module-scope import of the label stage.
# --------------------------------------------------------------------------


def test_label_stage_imports_without_scipy():
    """Run in a subprocess with scipy blocked: importing 03 must still work.

    A module-scope `from scipy.sparse import ...` takes wirelength, congestion,
    timing and RC down with IR drop, even under --skip-irdrop.
    """
    pytest.importorskip("torch")
    program = textwrap.dedent(
        f"""
        import importlib.util, sys
        class Blocker:
            def find_module(self, name, path=None):
                return self if name == "scipy" or name.startswith("scipy.") else None
            def load_module(self, name):
                raise ImportError("scipy blocked for this test")
        sys.meta_path.insert(0, Blocker())
        spec = importlib.util.spec_from_file_location("m03", {str(R2G2_DIR / "03_extract_labels.py")!r})
        module = importlib.util.module_from_spec(spec)
        sys.modules["m03"] = module
        spec.loader.exec_module(module)
        assert not any(n == "scipy" or n.startswith("scipy.") for n in sys.modules), "scipy imported eagerly"
        try:
            module._require_scipy()
        except ImportError as error:
            assert "--skip-irdrop" in str(error), str(error)
        print("OK")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True, timeout=600
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


# --------------------------------------------------------------------------
# Adapters.
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def encode_map_mod():
    return _load("build_encode_map.py", "t_r2g2_encode_map", ADAPT_DIR)


@pytest.fixture(scope="module")
def config_mod():
    return _load("make_sample_config.py", "t_r2g2_make_config", ADAPT_DIR)


@pytest.fixture(scope="module")
def timing_mod():
    return _load("emit_timing_reports.py", "t_r2g2_timing", ADAPT_DIR)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("met1", "METAL1"), ("M3", "METAL3"), ("metal10", "METAL10"),
        ("via2", "VIA2"), ("li1", "LI1"), ("", "UNKNOWN"),
    ],
)
def test_layer_normalization_matches_feature_stage(encode_map_mod, stage02, raw, expected):
    """Generated pin_layer_id raw_values must be what stage 02 looks up."""
    assert encode_map_mod.normalized_layer_name(raw) == expected
    assert stage02.normalized_layer_name(raw) == expected


def test_encode_map_covers_every_liberty_cell(encode_map_mod, stage01, tmp_path, quoted_lib):
    """Stage 01 hard-fails on a Liberty master missing from cell_type_id."""
    out = tmp_path / "encode_map.csv"
    result = subprocess.run(
        [sys.executable, str(ADAPT_DIR / "build_encode_map.py"),
         "--platform", "demo", "--lib", str(quoted_lib), "--out", str(out)],
        capture_output=True, text=True, timeout=900,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    import csv
    rows = list(csv.DictReader(out.open(encoding="utf-8")))
    cell_values = {r["raw_value"] for r in rows if r["map_name"] == "cell_type_id"}
    _, masters = stage01.parse_liberty([quoted_lib])
    assert {m.upper() for m in masters} <= cell_values
    assert "UNKNOWN" in cell_values

    # The global maps stage 02 requires must survive verbatim.
    by_name = {r["map_name"] for r in rows}
    assert {"cell_function_id", "clock_domain_id", "net_type_id", "orientation_id",
            "pin_direction_id", "pin_role_id", "pin_type_id", "placement_status_id",
            "cell_type_id", "pin_layer_id"} <= by_name
    assert "UNKNOWN" in {r["raw_value"] for r in rows if r["map_name"] == "pin_layer_id"}


def test_detect_top_module_picks_the_last_declaration(config_mod, tmp_path):
    netlist = tmp_path / "1_2_yosys.v"
    netlist.write_text(
        "module leaf_stub(a);\ninput a;\nendmodule\n"
        "module my_top(clk);\ninput clk;\nendmodule\n",
        encoding="utf-8",
    )
    assert config_mod.detect_top_module(netlist) == "my_top"


def test_detect_top_module_handles_escaped_names(config_mod, tmp_path):
    netlist = tmp_path / "n.v"
    netlist.write_text("module \\weird$top (a);\ninput a;\nendmodule\n", encoding="utf-8")
    assert config_mod.detect_top_module(netlist) == "weird$top"


def test_detect_top_module_fails_closed_on_empty(config_mod, tmp_path):
    netlist = tmp_path / "empty.v"
    netlist.write_text("// nothing here\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        config_mod.detect_top_module(netlist)


def test_timing_fields_carry_over_when_reports_still_exist(config_mod, tmp_path):
    reports = {name: tmp_path / name for name in
               ("paths_max.rpt", "paths_min.rpt", "timing_manifest.json")}
    for path in reports.values():
        path.write_text("x", encoding="utf-8")
    previous = {
        "timing_max_rpt": str(reports["paths_max.rpt"]),
        "timing_min_rpt": str(reports["paths_min.rpt"]),
        "timing_manifest": str(reports["timing_manifest.json"]),
        "timing_require_manifest": True,
        "timing_enabled": True,
    }
    cfg = {"timing_enabled": False}
    assert config_mod.carry_over_timing_fields(previous, cfg) is True
    assert cfg["timing_enabled"] is True
    assert cfg["timing_manifest"] == str(reports["timing_manifest.json"])


def test_timing_fields_are_dropped_when_a_report_is_gone(config_mod, tmp_path):
    """A genuinely missing report must still degrade the column honestly."""
    present = tmp_path / "paths_max.rpt"
    present.write_text("x", encoding="utf-8")
    previous = {
        "timing_max_rpt": str(present),
        "timing_min_rpt": str(tmp_path / "gone.rpt"),
        "timing_manifest": str(tmp_path / "gone.json"),
        "timing_enabled": True,
    }
    cfg = {"timing_enabled": False}
    assert config_mod.carry_over_timing_fields(previous, cfg) is False
    assert cfg["timing_enabled"] is False


def test_timing_carry_over_is_a_noop_on_a_fresh_config(config_mod):
    cfg = {"timing_enabled": False}
    assert config_mod.carry_over_timing_fields({}, cfg) is False
    assert cfg == {"timing_enabled": False}


def test_third_routing_layer_pitch(config_mod, tmp_path):
    lef = tmp_path / "tech.lef"
    lef.write_text(
        textwrap.dedent(
            """\
            LAYER li1
              TYPE ROUTING ;
              PITCH 0.34 0.34 ;
            END li1
            LAYER met1
              TYPE ROUTING ;
              PITCH 0.34 0.34 ;
            END met1
            LAYER met2
              TYPE ROUTING ;
              PITCH 0.46 0.46 ;
            END met2
            """
        ),
        encoding="utf-8",
    )
    assert config_mod.third_routing_layer_pitch_um(lef) == pytest.approx(0.46)


def test_third_routing_layer_pitch_none_when_too_few_layers(config_mod, tmp_path):
    """Fewer than three routing layers -> caller keeps the documented default."""
    lef = tmp_path / "thin.lef"
    lef.write_text("LAYER met1\n  TYPE ROUTING ;\n  PITCH 0.34 ;\nEND met1\n", encoding="utf-8")
    assert config_mod.third_routing_layer_pitch_um(lef) is None


def test_make_sample_config_rejects_a_non_run_dir(tmp_path):
    """Exit-code contract, proven through the CLI rather than in-process."""
    result = subprocess.run(
        [sys.executable, str(ADAPT_DIR / "make_sample_config.py"),
         "--run-dir", str(tmp_path), "--out-dir", str(tmp_path / "out")],
        capture_output=True, text=True, timeout=600,
    )
    assert result.returncode != 0
    assert "results/" in (result.stdout + result.stderr)


def test_timing_report_marker_slicing(timing_mod):
    captured = (
        "[INFO] banner\n"
        f"{timing_mod.MAX_BEGIN}\n"
        "Startpoint: a\nEndpoint: b\nPath Type: max\n   1.0   slack (MET)\n"
        f"{timing_mod.MAX_END}\n"
        f"{timing_mod.MIN_BEGIN}\nStartpoint: c\n{timing_mod.MIN_END}\n"
    )
    max_text = timing_mod.slice_between(captured, timing_mod.MAX_BEGIN, timing_mod.MAX_END)
    assert max_text.startswith("Startpoint: a")
    assert "banner" not in max_text
    assert "Startpoint: c" not in max_text


def test_timing_report_marker_slicing_missing_marker(timing_mod):
    # report_checks returns "" rather than the report text; an unfenced capture
    # must read as absent, not as an empty-but-valid report.
    assert timing_mod.slice_between("no markers here", timing_mod.MAX_BEGIN, timing_mod.MAX_END) == ""


def test_timing_path_count(timing_mod, tmp_path):
    report = tmp_path / "paths_max.rpt"
    report.write_text("Startpoint: a\nx\nStartpoint: b\ny\n", encoding="utf-8")
    assert timing_mod.count_paths(report) == 2


def test_timing_endpoint_budget_favours_breadth(timing_mod):
    """One path per endpoint, many endpoints -- not the reverse.

    ``-endpoint_path_count`` caps paths PER ENDPOINT. Setting it large spends the
    whole budget re-walking a handful of endpoints; measured on cordic/nangate45,
    10005 setup paths covered only 19 of the design's 107 endpoints. The node
    label is the endpoint's worst slack, so breadth is what matters.
    """
    tcl = timing_mod.build_tcl(
        libs=[Path("/x.lib")], lefs=[Path("/x.lef")], route_def=Path("/r.def"),
        spef=Path("/r.spef"), sdc=Path("/r.sdc"),
        max_rpt=Path("/max.rpt"), min_rpt=Path("/min.rpt"),
        max_paths=10000, endpoint_paths=1,
        group_flag="-group_path_count", endpoint_flag="-endpoint_path_count",
    )
    assert "-endpoint_path_count 1" in tcl
    assert "-group_path_count 10000" in tcl
    # the pre-fix shape: the same large number on both knobs
    assert "-endpoint_path_count 10000" not in tcl


def test_timing_endpoint_paths_defaults_to_one():
    source = (ADAPT_DIR / "emit_timing_reports.py").read_text(encoding="utf-8")
    assert '"--endpoint-paths", type=int, default=1' in source


def test_count_distinct_endpoints(timing_mod, tmp_path):
    """Endpoint count -- not path count -- bounds how many pins can be labelled."""
    report = tmp_path / "paths_max.rpt"
    report.write_text(
        "Startpoint: a\nEndpoint: e1\nx\n"
        "Startpoint: b\nEndpoint: e1\ny\n"      # same endpoint again
        "Startpoint: c\nEndpoint: e2\nz\n",
        encoding="utf-8",
    )
    assert timing_mod.count_paths(report) == 3
    assert timing_mod.count_distinct_endpoints(report) == 2


def test_timing_manifest_schema_matches_label_gate(timing_mod):
    """The emitter and the label-stage verifier must agree on the contract strings."""
    labels_source = (R2G2_DIR / "03_extract_labels.py").read_text(encoding="utf-8")
    assert f'TIMING_MANIFEST_SCHEMA = "{timing_mod.TIMING_MANIFEST_SCHEMA}"' in labels_source
    assert f'TIMING_SOURCE_CONTRACT = "{timing_mod.TIMING_SOURCE_CONTRACT}"' in labels_source


# --------------------------------------------------------------------------
# D5: nodes_iopin.csv is checked against the base graph like every other table.
# --------------------------------------------------------------------------


def test_assemble_checks_iopin_features_against_base(stage04):
    with pytest.raises(ValueError) as excinfo:
        stage04.require_same_keys({"a", "b"}, {"a"}, "nodes_iopin.csv")
    assert "nodes_iopin.csv" in str(excinfo.value)


def test_assemble_declares_io_features_alignment():
    """io_features must stay in the alignment map, next to the other three."""
    source = (R2G2_DIR / "04_assemble_heterograph.py").read_text(encoding="utf-8")
    assert '"io_features": require_same_keys(' in source
    assert "base.io_pin_names" in source


# --------------------------------------------------------------------------
# D6: the unmatched-gate statistic is honest.
# --------------------------------------------------------------------------


def test_base_graph_stats_distinguish_no_reference_from_no_match():
    source = (R2G2_DIR / "01_build_base_graph.py").read_text(encoding="utf-8")
    assert "name_reference_enabled" in source
    assert "reference_enabled = bool(reference_gates)" in source


# --------------------------------------------------------------------------
# Runner wiring.
# --------------------------------------------------------------------------


def test_runner_is_executable_and_bash_clean():
    runner = SKILL_ROOT / "scripts" / "flow" / "run_stage_dataset.sh"
    assert runner.exists()
    result = subprocess.run(["bash", "-n", str(runner)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_runner_resolves_its_sibling_script_dirs():
    """Evaluate the runner's own path arithmetic, not a copy of it.

    `scripts/flow/..` is `scripts`, not the skill root -- an off-by-one there
    made every step fail with a `scripts/scripts/...` ENOENT only at runtime.
    """
    runner = SKILL_ROOT / "scripts" / "flow" / "run_stage_dataset.sh"
    assignments = [
        line.strip()
        for line in runner.read_text(encoding="utf-8").splitlines()
        if line.startswith(("SKILL_DIR=", "R2G2_DIR=", "ADAPT_DIR="))
    ]
    assert len(assignments) == 3, assignments
    snippet = "\n".join(
        [f'HERE="{runner.parent}"', *assignments,
         'for d in "$R2G2_DIR" "$ADAPT_DIR"; do [ -d "$d" ] || { echo "missing $d"; exit 1; }; done',
         '[ -f "$R2G2_DIR/01_build_base_graph.py" ] || { echo "missing 01"; exit 1; }',
         '[ -f "$ADAPT_DIR/make_sample_config.py" ] || { echo "missing make_sample_config"; exit 1; }',
         'echo OK']
    )
    result = subprocess.run(["bash", "-c", snippet], capture_output=True, text=True, timeout=600)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


def test_runner_requires_a_project_dir():
    runner = SKILL_ROOT / "scripts" / "flow" / "run_stage_dataset.sh"
    result = subprocess.run(["bash", str(runner)], capture_output=True, text=True, timeout=600)
    assert result.returncode != 0
    assert "project_dir" in (result.stdout + result.stderr)


def test_upstream_provenance_is_recorded():
    """Re-vendoring without updating the delta list would silently drop the fixes."""
    doc = (R2G2_DIR / "R2G2_UPSTREAM.md").read_text(encoding="utf-8")
    for delta in ("D1", "D2", "D3", "D4", "D5", "D6"):
        assert f"### {delta} " in doc, delta
    assert "Dataset_R2G2.0(B).zip" in doc
