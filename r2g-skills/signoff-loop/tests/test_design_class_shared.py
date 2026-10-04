"""design_class has ONE derivation, shared by ingest and diagnose (redesign A3).

diagnose used to size the class from proj/synth/synth.log, which run_orfs.sh projects
never write, so every live lookup keyed '<type>/unknown' while ingest stored
'<type>/<band>' from ppa.json and the lifecycle gate matched nothing.
"""
from __future__ import annotations

import json
import sqlite3

import diagnose_signoff_fix as dsf
import ingest_run


def _proj(tmp_path, cells=1460):
    p = tmp_path / "des_area"
    (p / "reports").mkdir(parents=True)
    (p / "constraints").mkdir()
    (p / "synth").mkdir()                              # empty, as run_orfs.sh leaves it
    (p / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = des_area\nexport PLATFORM = sky130hs\nexport CORE_UTILIZATION = 17\n")
    if cells is not None:
        (p / "reports" / "ppa.json").write_text(json.dumps({"geometry": {"instance_count": cells}}))
    (p / "reports" / "drc.json").write_text(json.dumps(
        {"status": "fail", "total_violations": 12, "categories": {"m3.2": {"count": 12}}}))
    return p


def test_helper_reads_ppa_and_pins_prior_count(tmp_path):
    cfg = {"PLATFORM": "sky130hs"}
    assert ingest_run.project_design_class(_proj(tmp_path), cfg) == "logic/small"
    q = _proj(tmp_path / "b", cells=None)
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE runs (project_path TEXT, cell_count INT, ingested_at TEXT, run_id TEXT)")
    conn.execute("INSERT INTO runs VALUES (?, 60000, '2026-10-01', 'r1')", (str(q.resolve()),))
    assert ingest_run.project_design_class(q, cfg) == "logic/unknown"
    assert ingest_run.project_design_class(q, cfg, conn=conn) == "logic/large"


def test_diagnose_keys_live_gates_with_the_ingest_class(tmp_path, monkeypatch, capsys):
    p = _proj(tmp_path)
    seen = {}
    real = dsf._annotate_live_gates

    def _spy(plan, proj, **kw):
        seen["design_class"] = kw.get("design_class")
        return real(plan, proj, **kw)
    monkeypatch.setattr(dsf, "_annotate_live_gates", _spy)
    monkeypatch.setenv("R2G_MEMORY_BACKEND", "legacy")
    assert dsf.main([str(p), "--check", "drc", "--list"]) == 0
    capsys.readouterr()
    assert seen["design_class"] == ingest_run.project_design_class(
        p, {"PLATFORM": "sky130hs"}) == "logic/small"
