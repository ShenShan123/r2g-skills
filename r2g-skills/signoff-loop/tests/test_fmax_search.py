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


def test_cleanup_variants_removes_orfs_scratch(tmp_path):
    """The probe's ORFS results/logs/objects/reports leaked ~1 GB/probe when only the
    project dir was removed (2026-09-30 AIC v2 on 203: 708 orphaned probe dirs)."""
    import json
    flow = tmp_path / "orfs" / "flow"
    v = tmp_path / "design_cases" / "alu_fmax_p0045"
    other = "alu_fmax_p0050"
    for sub in ("results", "logs", "objects", "reports"):
        (flow / sub / "sky130hd" / "alu" / v.name).mkdir(parents=True)
        (flow / sub / "sky130hd" / "alu" / other).mkdir(parents=True)
    run = v / "backend" / "RUN_x"
    run.mkdir(parents=True)
    (run / "run-meta.json").write_text(json.dumps(
        {"orfs_results": str(flow / "results" / "sky130hd" / "alu" / v.name)}))
    fs.cleanup_variants([v])
    assert not v.exists()
    for sub in ("results", "logs", "objects", "reports"):
        assert not (flow / sub / "sky130hd" / "alu" / v.name).exists()
        assert (flow / sub / "sky130hd" / "alu" / other).exists()   # never another variant's


def test_cleanup_variants_ignores_foreign_orfs_path(tmp_path):
    import json
    v = tmp_path / "alu_fmax_p0045"
    victim = tmp_path / "flow" / "results" / "sky130hd" / "alu" / "NOT_THIS_VARIANT"
    victim.mkdir(parents=True)
    (v / "backend" / "RUN_x").mkdir(parents=True)
    (v / "backend" / "RUN_x" / "run-meta.json").write_text(json.dumps({"orfs_results": str(victim)}))
    fs.cleanup_variants([v])
    assert victim.exists()


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
    fake_kdb.get_platform_heuristics = lambda *a, **kw: None
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
    fake_kdb.get_platform_heuristics = lambda *a, **kw: None
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


def test_record_period_relax_appends_and_cli(tmp_path):
    import json, subprocess, sys
    proj = tmp_path / "p"
    (proj / "reports").mkdir(parents=True)
    rep = proj / "reports" / "fmax_search.json"
    rep.write_text(json.dumps({"status": "ok", "winner": {"period": 5.0}}))
    assert fs.record_period_relax(proj, 5.0, 5.5, "period_relax") is True
    r = subprocess.run([sys.executable, fs.__file__, "--record-relax", "5.5", "6.0",
                        "check_timing_minor", str(proj)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "recorded" in r.stdout, r.stdout + r.stderr
    steps = json.loads(rep.read_text())["relaxations"]
    assert [(s["from"], s["to"], s["source"]) for s in steps] == [
        (5.0, 5.5, "period_relax"), (5.5, 6.0, "check_timing_minor")]
    assert not (proj / "reports" / "fmax_search.json.tmp").exists()


def test_record_period_relax_noop_without_ok_search(tmp_path):
    proj = tmp_path / "p"
    (proj / "reports").mkdir(parents=True)
    assert fs.record_period_relax(proj, 5.0, 5.5, "period_relax") is False
    (proj / "reports" / "fmax_search.json").write_text('{"status": "inconclusive"}')
    assert fs.record_period_relax(proj, 5.0, 5.5, "period_relax") is False


# --- 2026-11-25 optimisation: lazy confirm grid + ECP-refined verification ---------------

def _grid_probe(threshold, calls):
    import fmax_model as fm
    def pl(period):
        calls.append(period)
        ws = period - threshold
        return {"place_ws": ws, "place_tns": 0.0 if ws >= 0 else -1.0,
                "status": fm.classify_probe(ws, 0.0 if ws >= 0 else -1.0, period)}
    return pl


@pytest.mark.parametrize("threshold", [4.0, 5.05, 5.12, 5.2, 6.0])
def test_lazy_confirm_returns_identical_edge_with_fewer_probes(threshold):
    """For any deterministic monotone probe the lazy (loosest-first, stop at first pass)
    grid returns the SAME edge as the eager grid, never with more probes."""
    eager_calls, lazy_calls = [], []
    eager = fs.confirm_grid(5.1, _grid_probe(threshold, eager_calls), width=0.02, n=3)
    st = {}
    lazy = fs.confirm_grid(5.1, _grid_probe(threshold, lazy_calls), width=0.02, n=3,
                           lazy=True, stats=st)
    assert lazy == pytest.approx(eager)
    assert len(lazy_calls) <= len(eager_calls) == 3
    assert st["probes"] == len(lazy_calls) and st["lazy"] is True


def test_lazy_confirm_one_probe_when_loosest_passes():
    calls = []
    edge = fs.confirm_grid(5.1, _grid_probe(4.0, calls), width=0.02, n=3, lazy=True)
    assert calls == [pytest.approx(5.1 * 1.02)] and edge == pytest.approx(5.1 * 1.02)


def test_lazy_confirm_nonmonotone_still_matches_eager():
    """A noisy probe where only the middle point passes: both modes return it."""
    import fmax_model as fm
    def make(calls):
        def pl(p):
            calls.append(p)
            ok = abs(p - 5.1) < 1e-9
            return {"place_ws": 0.1 if ok else -0.1, "place_tns": 0.0 if ok else -1.0,
                    "status": "pass" if ok else "fail"}
        return pl
    a, b = [], []
    assert fs.confirm_grid(5.1, make(a), lazy=True) == pytest.approx(
        fs.confirm_grid(5.1, make(b)))
    assert len(a) == 2 and len(b) == 3


def test_place_probe_memo_skips_repeat_period(tmp_path, monkeypatch):
    runs = []
    def fake_run_probe(v, platform, stages, **kw):
        runs.append(v.name)
        return {"place_ws": 0.2, "place_tns": 0.0, "floorplan_ws": 0.5, "completed": True}
    monkeypatch.setattr(fs, "run_probe", fake_run_probe)
    monkeypatch.setattr(fs, "clone_variant", lambda base, p: tmp_path / f"v{p}")
    stats = {}
    _, pl = fs._make_real_probes(tmp_path, "nangate45", timeout_s=10, place_fast=False,
                                 created=[], memo=True, stats=stats)
    r1, r2 = pl(1.5), pl(1.5)
    assert r1 == r2 and len(runs) == 1
    assert stats["orfs_runs"] == 1 and stats["memo_hits"] == 1
    # memo off (legacy): every call runs ORFS
    runs.clear()
    _, pl0 = fs._make_real_probes(tmp_path, "nangate45", timeout_s=10, place_fast=False,
                                  created=[])
    pl0(1.5); pl0(1.5)
    assert len(runs) == 2


def test_ecp_refine_period_directions():
    import fmax_model as fm
    # closed with 0.3 ns slack at 2.0 -> tighten to (2.0-0.3)*1.02
    assert fm.ecp_refine_period(2.0, 0.3, closed=True) == pytest.approx(1.7 * 1.02)
    # closed but gain < 1% -> no extra flow
    assert fm.ecp_refine_period(2.0, 0.01, closed=True) is None
    # setup miss -> loosen to ECP*(1+m), at least +1%
    assert fm.ecp_refine_period(2.0, -0.2, closed=False) == pytest.approx(2.2 * 1.02)
    assert fm.ecp_refine_period(2.0, -0.001, closed=False) == pytest.approx(2.001 * 1.02)
    assert fm.ecp_refine_period(2.0, -0.001, closed=False, margin=0.0) == pytest.approx(2.0 * 1.01)
    # hold miss / unknown slack -> nothing a period change can fix
    assert fm.ecp_refine_period(2.0, 0.3, closed=False, hold_ws=-0.05) is None
    assert fm.ecp_refine_period(2.0, None, closed=False) is None


def _ok_report(base, period=2.0):
    import json
    (base / "reports").mkdir(parents=True, exist_ok=True)
    (base / "reports" / "fmax_search.json").write_text(json.dumps({
        "status": "ok", "winner": {"period": period, "fmax_place_proxy": 0.6},
        "labels": ["x"]}))


def test_record_verify_ecp_tightens_winner(tmp_path):
    import json
    _ok_report(tmp_path, 1.96)
    calls = []
    def fake_verify(base, platform, period):
        calls.append(period)
        return {"period": period, "closed": True, "finish_ws": 0.05, "finish_tns": 0.0,
                "hold_ws": 0.1, "wall_s": 1.0}
    v = {"period": 2.0, "closed": True, "finish_ws": 0.4, "finish_tns": 0.0, "hold_ws": 0.1}
    out = fs._record_verify(tmp_path, "nangate45", v, ecp_refine=True, margin=0.02,
                            verify_fn=fake_verify)
    assert calls == [pytest.approx(1.6 * 1.02)]
    d = json.loads((tmp_path / "reports" / "fmax_search.json").read_text())
    assert d["winner"]["period"] == pytest.approx(1.632)
    assert d["winner"]["source"] == "ecp_refine_verified"
    assert d["proxy_winner"]["period"] == 1.96
    assert d["verify"]["verified_period"] == pytest.approx(1.632)
    assert out["verified_period"] == pytest.approx(1.632)


def test_record_verify_ecp_failed_refine_keeps_verified_period(tmp_path):
    import json
    _ok_report(tmp_path, 1.96)
    fake = lambda b, p, t: {"period": t, "closed": False, "finish_ws": -0.02,
                            "finish_tns": -0.1, "hold_ws": 0.1}
    v = {"period": 2.0, "closed": True, "finish_ws": 0.4, "finish_tns": 0.0, "hold_ws": 0.1}
    fs._record_verify(tmp_path, "nangate45", v, ecp_refine=True, margin=0.02, verify_fn=fake)
    d = json.loads((tmp_path / "reports" / "fmax_search.json").read_text())
    assert d["winner"]["period"] == 2.0 and len(d["verify"]["tries"]) == 2
    assert d["winner"]["source"] == "verify_confirm_edge"


def test_record_verify_without_ecp_keeps_legacy_winner(tmp_path):
    import json
    _ok_report(tmp_path, 1.96)
    def boom(*a):
        raise AssertionError("no extra flow without --ecp-refine")
    v = {"period": 2.0, "closed": True, "finish_ws": 0.4, "finish_tns": 0.0, "hold_ws": 0.1}
    fs._record_verify(tmp_path, "nangate45", v, ecp_refine=False, margin=0.02, verify_fn=boom)
    d = json.loads((tmp_path / "reports" / "fmax_search.json").read_text())
    assert d["winner"]["period"] == 1.96 and "proxy_winner" not in d
    assert d["verify"]["verified_period"] == 2.0


def test_verify_winner_survives_readonly_knowledge_store(tmp_path, monkeypatch):
    """A refused knowledge write (shipped-store guard) must not lose the verify result."""
    import json, sqlite3, types, sys
    base = tmp_path / "d"
    (base / "constraints").mkdir(parents=True)
    (base / "constraints" / "config.mk").write_text("export DESIGN_NAME = d\n")
    (base / "constraints" / "constraint.sdc").write_text("set clk_period 2.0\n")
    rd = tmp_path / "run"
    (rd / "logs").mkdir(parents=True)
    (rd / "logs" / "6_report.json").write_text(json.dumps({
        "finish__timing__setup__ws": 0.1, "finish__timing__setup__tns": 0.0,
        "finish__timing__hold__ws": 0.02}))
    monkeypatch.setattr(fs, "run_probe", lambda *a, **k: {"floorplan_ws": 0.5, "place_ws": 0.3})
    monkeypatch.setattr(fs, "_latest_run_dir", lambda v: rd)
    kdb = types.ModuleType("knowledge_db")
    kdb.infer_family = lambda *a, **k: None
    kdb.load_families = lambda: {}
    def _connect():
        raise sqlite3.OperationalError("attempt to write a readonly database")
    kdb.connect = _connect
    monkeypatch.setitem(sys.modules, "knowledge_db", kdb)
    out = fs.verify_winner(base, "nangate45", 2.0)
    assert out["closed"] is True and out["finish_ws"] == 0.1 and out["period"] == 2.0


def test_record_verify_ecp_all_miss_marks_winner_unverified(tmp_path):
    """Codex review 2026-11-25: when no full flow closes, the proxy winner must be
    labelled unverified, never silently look verified."""
    import json
    _ok_report(tmp_path, 1.96)
    fake = lambda b, p, t: {"period": t, "closed": False, "finish_ws": -0.1,
                            "finish_tns": -1.0, "hold_ws": 0.1}
    v = {"period": 2.0, "closed": False, "finish_ws": -0.2, "finish_tns": -2.0, "hold_ws": 0.1}
    out = fs._record_verify(tmp_path, "nangate45", v, ecp_refine=True, margin=0.02, verify_fn=fake)
    d = json.loads((tmp_path / "reports" / "fmax_search.json").read_text())
    assert out["verified_period"] is None and d["verify"]["verified_period"] is None
    assert d["winner"]["period"] == 1.96 and d["winner"]["source"] == "proxy_unverified"
    assert any("unverified" in l for l in d["labels"])


def test_verify_winner_incomplete_flow_is_not_closed(tmp_path, monkeypatch):
    """Codex review 2026-11-25: passing finish numbers from a failed run never count."""
    import json, types, sys
    base = tmp_path / "d"
    (base / "constraints").mkdir(parents=True)
    (base / "constraints" / "config.mk").write_text("export DESIGN_NAME = d\n")
    (base / "constraints" / "constraint.sdc").write_text("set clk_period 2.0\n")
    rd = tmp_path / "run"
    (rd / "logs").mkdir(parents=True)
    (rd / "logs" / "6_report.json").write_text(json.dumps({
        "finish__timing__setup__ws": 0.1, "finish__timing__setup__tns": 0.0}))
    monkeypatch.setattr(fs, "run_probe", lambda *a, **k: {"completed": False})
    monkeypatch.setattr(fs, "_latest_run_dir", lambda v: rd)
    monkeypatch.setattr(fs, "record_verify_triple", lambda *a, **k: "x")
    kdb = types.ModuleType("knowledge_db")
    kdb.infer_family = lambda *a, **k: None
    kdb.load_families = lambda: {}
    kdb.ensure_schema = lambda c: None
    class _C:
        def close(self): pass
    kdb.connect = lambda: _C()
    monkeypatch.setitem(sys.modules, "knowledge_db", kdb)
    assert fs.verify_winner(base, "nangate45", 2.0)["closed"] is False


def test_heuristics_path_env_is_honored(tmp_path, monkeypatch):
    monkeypatch.delenv("R2G_HEURISTICS_PATH", raising=False)
    assert fs._heuristics_kw() == {}
    monkeypatch.setenv("R2G_HEURISTICS_PATH", str(tmp_path / "h.json"))
    assert fs._heuristics_kw() == {"heuristics_path": str(tmp_path / "h.json")}


@pytest.mark.parametrize("argv_extra,env,expect", [
    ([], None, True), ([], "0", False), (["--eager-confirm"], None, False),
    (["--lazy-confirm"], "0", True)])
def test_lazy_confirm_cli_default_and_opt_out(tmp_path, monkeypatch, argv_extra, env, expect):
    import sys, types
    (tmp_path / "constraints").mkdir()
    (tmp_path / "constraints" / "config.mk").write_text("export DESIGN_NAME = alu\n")
    (tmp_path / "constraints" / "constraint.sdc").write_text("set clk_period 10.0\n")
    if env is None:
        monkeypatch.delenv("R2G_FMAX_LAZY_CONFIRM", raising=False)
    else:
        monkeypatch.setenv("R2G_FMAX_LAZY_CONFIRM", env)
    monkeypatch.setattr(sys, "argv", ["fmax_search.py", str(tmp_path), "nangate45", *argv_extra])
    monkeypatch.setattr(fs, "assert_safe_knobs", lambda _: None)
    monkeypatch.setattr("fmax_search.seed_period", lambda *a, **kw: 10.0)
    seen = {}
    def _probes(*a, **kw):
        seen["memo"] = kw.get("memo")
        return (lambda p: None), (lambda p: {})
    monkeypatch.setattr(fs, "_make_real_probes", _probes)
    monkeypatch.setattr(fs, "search", lambda *a, **kw: {"status": "inconclusive", "log": []})
    kdb = types.ModuleType("knowledge_db")
    kdb.infer_family = lambda *a, **kw: None
    kdb.load_families = lambda: {}
    kdb.get_family_heuristics = lambda *a, **kw: None
    kdb.get_platform_heuristics = lambda *a, **kw: None
    monkeypatch.setitem(sys.modules, "knowledge_db", kdb)
    import fmax_model as fm_mod
    monkeypatch.setattr(fm_mod, "select_model", lambda *a, **kw: (None, "default-static"))
    fs.main()
    assert seen["memo"] is expect
