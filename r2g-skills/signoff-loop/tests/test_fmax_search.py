"""Tests for the Fmax search orchestrator (I/O parts mocked)."""
from __future__ import annotations
import pytest
import fmax_search as fs


def test_clone_variant_symlinks_rtl_and_rewrites_sdc(tmp_path):
    base = tmp_path / "design_cases" / "alu"
    (base / "constraints").mkdir(parents=True)
    (base / "rtl").mkdir(parents=True)
    (base / "rtl" / "alu.v").write_text("module alu(); endmodule\n", encoding="utf-8")
    (base / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = alu\n", encoding="utf-8")
    (base / "constraints" / "constraint.sdc").write_text(
        "set clk_period 10.0\n", encoding="utf-8")

    variant = fs.clone_variant(base, 4.5)
    assert variant.name == "alu_fmax_p0045"
    # rtl symlinked (not copied)
    assert (variant / "rtl").is_symlink() or (variant / "rtl" / "alu.v").exists()
    # sdc rewritten to the probe period
    assert "set clk_period 4.5" in (variant / "constraints" / "constraint.sdc").read_text()
    # config.mk copied
    assert (variant / "constraints" / "config.mk").exists()


def test_density_floor_and_unique_variant_asserts(tmp_path):
    base = tmp_path / "design_cases" / "alu"
    (base / "constraints").mkdir(parents=True)
    (base / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = alu\nexport PLACE_DENSITY_LB_ADDON = 0.05\n", encoding="utf-8")
    (base / "constraints" / "constraint.sdc").write_text("set clk_period 10.0\n", encoding="utf-8")
    import pytest
    with pytest.raises(ValueError, match="PLACE_DENSITY_LB_ADDON"):
        fs.assert_safe_knobs(base)


def test_search_with_injected_probes_writes_report(tmp_path, monkeypatch):
    import json, fmax_model as fm
    base = tmp_path / "design_cases" / "alu"
    (base / "constraints").mkdir(parents=True)
    (base / "reports").mkdir(parents=True)
    (base / "constraints" / "config.mk").write_text("export DESIGN_NAME = alu\n", encoding="utf-8")
    (base / "constraints" / "constraint.sdc").write_text("set clk_period 10.0\n", encoding="utf-8")

    # Inject pure probes (no ORFS): linear slack with true Fmax period 5.0.
    def fp(period): return (period - 5.0) + 0.3
    def pl(period):
        ws = period - 5.0
        return {"place_ws": ws, "place_tns": 0.0, "status": fm.classify_probe(ws, 0.0, period)}

    result = fs.search(base, platform="nangate45", seed_period=10.0,
                       floorplan_probe=fp, place_probe=pl, model=None,
                       model_provenance="default-static")
    assert result["status"] == "ok"
    assert result["fmax_predicted_signoff_period"] == result["t_star"]
    # report written
    rpt = json.loads((base / "reports" / "fmax_search.json").read_text())
    assert rpt["winner"]["period"] == result["t_star"]
    assert "Fmax_predicted_signoff" in rpt["labels"][0]
    assert any("CTS-skew-unmodeled" in l for l in rpt["labels"])


def _mk_base(tmp_path):
    base = tmp_path / "design_cases" / "alu"
    (base / "constraints").mkdir(parents=True)
    (base / "reports").mkdir(parents=True)
    (base / "constraints" / "config.mk").write_text("export DESIGN_NAME = alu\n", encoding="utf-8")
    (base / "constraints" / "constraint.sdc").write_text("set clk_period 10.0\n", encoding="utf-8")
    return base


# ---- BUG #3 (2026-06-30): asap7 liberty time_unit=1ps -> Fmax must be normalized to GHz ----

def test_platform_time_unit_ns():
    assert fs._platform_time_unit_ns("asap7") == 0.001       # 1ps
    assert fs._platform_time_unit_ns("nangate45") == 1.0     # 1ns
    assert fs._platform_time_unit_ns("sky130hd") == 1.0
    assert fs._platform_time_unit_ns("unknown_future") == 1.0  # safe default


def test_build_labels_nangate45_unchanged():
    # ns platform (tu=1.0): identical to the historical behavior (1/t_star GHz, period t_star ns).
    res = {"t_star": 5.0, "t_place_proxy": 4.5, "fmax_place_proxy": 1.0 / 4.5}
    labels = fs.build_labels(res, "default-static", False, 1.0)
    assert "0.2" in labels[0] and "period 5 ns" in labels[0]   # 1/5 = 0.2 GHz


def test_build_labels_asap7_normalizes_1000x():
    # asap7 (tu=0.001): t_star=409.6 is PICOSECONDS -> 0.4096 ns -> 2.441 GHz, NOT 0.00244.
    res = {"t_star": 409.6, "t_place_proxy": 380.0, "fmax_place_proxy": 1.0 / 380.0}
    labels = fs.build_labels(res, "default-static", False, 0.001)
    assert "2.441" in labels[0], labels[0]        # 1/(409.6*0.001) GHz
    assert "0.4096" in labels[0], labels[0]        # period in ns
    assert "0.00244" not in labels[0]              # the 1000x-low bug value is GONE


def test_search_asap7_records_realistic_ghz(tmp_path):
    import json, fmax_model as fm
    base = _mk_base(tmp_path)
    # True closing period 400 in the platform STA unit (ps for asap7).
    def fp(period): return (period - 400.0) + 0.3
    def pl(period):
        ws = period - 400.0
        return {"place_ws": ws, "place_tns": 0.0, "status": fm.classify_probe(ws, 0.0, period)}
    result = fs.search(base, platform="asap7", seed_period=10.0,
                       floorplan_probe=fp, place_probe=pl, model=None,
                       model_provenance="default-static")
    assert result["status"] == "ok"
    w = json.loads((base / "reports" / "fmax_search.json").read_text())["winner"]
    # raw STA-unit period preserved (this is what rewrite_clk_period writes to the SDC + seeds next search)
    assert w["period"] == result["t_star"]
    # human-facing fields normalized to ns/GHz via the 1ps factor
    assert w["period_ns"] == pytest.approx(w["period"] * 0.001)
    assert w["fmax_predicted_signoff"] == pytest.approx(1.0 / (w["period"] * 0.001))
    # realistic 7nm Fmax (~GHz), NOT the 1000x-low ~0.002 GHz the bug produced
    assert w["fmax_predicted_signoff"] > 1.0


def test_search_nangate45_fmax_unchanged(tmp_path):
    import json, fmax_model as fm
    base = _mk_base(tmp_path)
    def fp(period): return (period - 5.0) + 0.3
    def pl(period):
        ws = period - 5.0
        return {"place_ws": ws, "place_tns": 0.0, "status": fm.classify_probe(ws, 0.0, period)}
    result = fs.search(base, platform="nangate45", seed_period=10.0,
                       floorplan_probe=fp, place_probe=pl, model=None,
                       model_provenance="default-static")
    w = json.loads((base / "reports" / "fmax_search.json").read_text())["winner"]
    # ns platform: tu=1.0 -> period_ns == period and fmax == 1/period (byte-identical behavior)
    assert w["period_ns"] == pytest.approx(w["period"])
    assert w["fmax_predicted_signoff"] == pytest.approx(1.0 / w["period"])


def test_confirm_grid_picks_looser_pass_edge():
    import fmax_model as fm
    # places: pass at >=5.1, fail below. Grid around t_star=5.1.
    def pl(period):
        ws = period - 5.0
        return {"place_ws": ws, "place_tns": 0.0, "status": fm.classify_probe(ws, 0.0, period)}
    edge = fs.confirm_grid(5.1, pl, model=None, width=0.02, n=3)
    # looser passing edge should be >= 5.1
    assert edge >= 5.1


def test_cleanup_variants_removes_dirs(tmp_path):
    base = tmp_path / "design_cases" / "alu"
    v = tmp_path / "design_cases" / "alu_fmax_p0045"
    v.mkdir(parents=True)
    fs.cleanup_variants([v])
    assert not v.exists()


def test_record_verify_triple_appends_to_db(tmp_path, tmp_knowledge_dir, monkeypatch):
    import knowledge_db
    conn = knowledge_db.connect(tmp_knowledge_dir / "runs.sqlite")
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    fs.record_verify_triple(conn, design_name="alu", design_family="alu",
                            platform="nangate45", period=5.1,
                            floorplan_ws=0.4, place_ws=0.2, finish_ws=0.05)
    r = conn.execute("SELECT clock_period_ns, floorplan_setup_ws, place_setup_ws, "
                     "finish_setup_ws, eval_arm FROM runs").fetchone()
    assert r[:4] == (5.1, 0.4, 0.2, 0.05)
    assert r[4] == "fmax_verify"  # tagged so it is identifiable but still learnable
    conn.close()


# BUG #8 — argparse round-trip: --max-parallel was never registered; confirm it
# raises SystemExit (argparse unrecognized-argument error), while the real flags parse.
def test_main_argparse_rejects_max_parallel(tmp_path, monkeypatch):
    """--max-parallel must not be a known flag (the search is sequential)."""
    import pytest, sys
    monkeypatch.setattr(sys, "argv", ["fmax_search.py", str(tmp_path), "--max-parallel", "4"])
    with pytest.raises(SystemExit):
        fs.main()


def test_main_argparse_accepts_real_flags(tmp_path, monkeypatch):
    """The documented flags (--verify, --keep-variants, --place-fast, --probe-timeout)
    must parse without error.  We intercept assert_safe_knobs to avoid needing a
    real config.mk, then let main() hit an expected early exit via knowledge_db."""
    import sys
    (tmp_path / "constraints").mkdir()
    (tmp_path / "constraints" / "config.mk").write_text("export DESIGN_NAME = alu\n")
    (tmp_path / "constraints" / "constraint.sdc").write_text("set clk_period 10.0\n")
    monkeypatch.setattr(sys, "argv",
                        ["fmax_search.py", str(tmp_path), "nangate45",
                         "--place-fast", "--probe-timeout", "120", "--keep-variants"])
    monkeypatch.setattr(fs, "assert_safe_knobs", lambda _: None)
    # Stub out the knowledge + search machinery so we don't actually launch ORFS.
    import fmax_model as fm
    monkeypatch.setattr("fmax_search.seed_period", lambda *a, **kw: 10.0)
    def _fake_search(*a, **kw):
        return {"status": "inconclusive", "t_star": None, "log": []}
    monkeypatch.setattr(fs, "search", _fake_search)
    # knowledge_db import inside main() — stub it (the heuristics read API
    # lives in knowledge_db since the 2026-07-18 query_knowledge fold-in).
    import types, sys as _sys
    fake_kdb = types.ModuleType("knowledge_db")
    fake_kdb.infer_family = lambda *a, **kw: "alu"
    fake_kdb.load_families = lambda: {}
    fake_kdb.get_family_heuristics = lambda *a, **kw: None
    fake_kdb.get_closing_period = lambda *a, **kw: None
    monkeypatch.setitem(_sys.modules, "knowledge_db", fake_kdb)
    import fmax_model as fm_mod
    monkeypatch.setattr(fm_mod, "select_model", lambda *a, **kw: (None, "default-static"))
    rc = fs.main()
    assert rc == 1   # inconclusive → non-zero exit (normal)


# BUG #9 — no 'set clk_period' SDC must not crash the search; writes
# fmax_search.json with status=="no_clock_constraint" and returns 0.
def test_main_no_clock_constraint_exits_cleanly(tmp_path, monkeypatch):
    import json, sys, types
    # Set up a minimal project with a clockless SDC.
    (tmp_path / "constraints").mkdir()
    (tmp_path / "constraints" / "config.mk").write_text("export DESIGN_NAME = combo\n")
    (tmp_path / "constraints" / "constraint.sdc").write_text(
        "# purely combinational — no create_clock, no set clk_period\n")
    monkeypatch.setattr(sys, "argv", ["fmax_search.py", str(tmp_path), "nangate45"])
    monkeypatch.setattr(fs, "assert_safe_knobs", lambda _: None)
    monkeypatch.setattr("fmax_search.seed_period", lambda *a, **kw: 10.0)

    # _make_real_probes returns probes that call clone_variant, which calls
    # fm.rewrite_clk_period. Patch clone_variant to raise the expected ValueError.
    def _raising_clone(base, period):
        raise ValueError("no 'set clk_period' line found in SDC")
    monkeypatch.setattr(fs, "clone_variant", _raising_clone)

    fake_kdb = types.ModuleType("knowledge_db")
    fake_kdb.infer_family = lambda *a, **kw: "combo"
    fake_kdb.load_families = lambda: {}
    fake_kdb.get_family_heuristics = lambda *a, **kw: None
    fake_kdb.get_closing_period = lambda *a, **kw: None
    monkeypatch.setitem(sys.modules, "knowledge_db", fake_kdb)
    import fmax_model as fm_mod
    monkeypatch.setattr(fm_mod, "select_model", lambda *a, **kw: (None, "default-static"))

    # Must not raise; must return 0.
    rc = fs.main()
    assert rc == 0, "main() must exit 0 for no_clock_constraint"

    rpt_path = tmp_path / "reports" / "fmax_search.json"
    assert rpt_path.exists(), "fmax_search.json must be written"
    rpt = json.loads(rpt_path.read_text())
    assert rpt["status"] == "no_clock_constraint", (
        f"expected status='no_clock_constraint', got {rpt['status']!r}"
    )
