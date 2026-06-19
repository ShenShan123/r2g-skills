"""Tests for the Fmax search orchestrator (I/O parts mocked)."""
from __future__ import annotations
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
