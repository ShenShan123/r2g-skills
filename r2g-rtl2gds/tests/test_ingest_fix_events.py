"""Tests for fix-event ingestion + the three new knowledge tables."""
from __future__ import annotations

import json as _json

import ingest_run
import knowledge_db


def _tables(conn):
    return {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}


def test_schema_creates_fix_tables(tmp_knowledge_dir):
    conn = knowledge_db.connect(tmp_knowledge_dir / "runs.sqlite")
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    tables = _tables(conn)
    assert {"fix_events", "fix_trajectories", "run_violations", "fix_events_archive"} <= tables
    # fix_events has the lossless detail columns + the idempotency constraint
    cols = {r[1] for r in conn.execute("PRAGMA table_info(fix_events)")}
    assert {"fix_session_id", "iter", "strategy", "violation_class", "verdict",
            "before_categories_json", "after_categories_json", "rule_details_json",
            "cumulative_config_json", "env_flags_json", "tool_versions_json",
            "stage_metrics_json", "provenance"} <= cols
    conn.close()


def test_normalize_verdict_passes_through_canonical_strings():
    # check_timing --journal (Task 7) emits canonical verdicts directly; the
    # ingester must not mangle them. Regression: 'win'/'no_change' were falling
    # through to 'inconclusive' because _VERDICT_MAP only held shell-legacy strings.
    assert ingest_run._normalize_verdict("cleared", 5, 0) == "cleared"
    # canonical 'win' survives; before/after still reclassify edge cases
    assert ingest_run._normalize_verdict("win", 4.5, 1.5) == "win"
    assert ingest_run._normalize_verdict("win", 1.5, 1.5) == "no_change"   # no actual gain
    assert ingest_run._normalize_verdict("win", 1.5, 4.5) == "regression"  # got worse
    assert ingest_run._normalize_verdict("no_change", 3, 3) == "no_change"
    assert ingest_run._normalize_verdict("regression", 1, 5) == "regression"
    # shell-legacy strings still map as before
    assert ingest_run._normalize_verdict("applied", 5, 2) == "win"
    assert ingest_run._normalize_verdict("no_improvement", 3, 3) == "no_change"
    # genuinely unknown / stop_* stays inconclusive
    assert ingest_run._normalize_verdict("stop_residual", 3, 3) == "inconclusive"
    assert ingest_run._normalize_verdict("apply_failed", None, None) == "inconclusive"


def test_project_family_matches_backfill_grouping(tmp_path):
    # Live ingest must group designs the same way backfill_fix_events does (by the
    # project-dir basename, which carries the source-repo prefix), so fix_recipes
    # aggregate in one namespace. Regression: live used config.mk DESIGN_NAME, which
    # drops the prefix (wb2axip_axi2axilite -> DESIGN_NAME 'axi2axilite' -> singleton).
    families = {"mappings": {"ChipTop": "boom_chiptop"},
                "patterns": [{"regex": "^aes", "family": "aes_xcrypt"}]}
    p = tmp_path / "wb2axip_axi2axilite"; p.mkdir()
    assert ingest_run._project_family(p, "axi2axilite", families) == "wb2axip"
    p2 = tmp_path / "iccad2015_unit18_in1"; p2.mkdir()
    assert ingest_run._project_family(p2, "test", families) == "iccad2015"
    # curated DESIGN_NAME mapping still wins over the dir basename
    p3 = tmp_path / "boom_mediumboom"; p3.mkdir()
    assert ingest_run._project_family(p3, "ChipTop", families) == "boom_chiptop"
    # DESIGN_NAME pattern still wins
    p4 = tmp_path / "some_aes_thing"; p4.mkdir()
    assert ingest_run._project_family(p4, "aes128_core", families) == "aes_xcrypt"


def test_fix_events_unique_constraint(tmp_knowledge_dir):
    import sqlite3
    conn = knowledge_db.connect(tmp_knowledge_dir / "runs.sqlite")
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    ins = ("INSERT OR IGNORE INTO fix_events "
           "(fix_session_id, iter, strategy) VALUES (?,?,?)")
    conn.execute(ins, ("sess1", 1, "antenna_diode_repair"))
    conn.execute(ins, ("sess1", 1, "antenna_diode_repair"))  # dup -> ignored
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM fix_events").fetchone()[0]
    assert n == 1
    conn.close()


def _mk_project(tmp_path, name="demo", platform="nangate45", drc_status="clean",
                fix_log=None):
    proj = tmp_path / name
    (proj / "constraints").mkdir(parents=True)
    (proj / "reports").mkdir()
    (proj / "constraints" / "config.mk").write_text(
        f"export DESIGN_NAME = {name}\nexport PLATFORM = {platform}\n")
    (proj / "reports" / "ppa.json").write_text(_json.dumps({"summary": {}, "geometry": {}}))
    (proj / "reports" / "drc.json").write_text(_json.dumps(
        {"status": drc_status, "total_violations": 0, "categories": {}}))
    if fix_log is not None:
        (proj / "reports" / "fix_log.jsonl").write_text(
            "\n".join(_json.dumps(r) for r in fix_log) + "\n")
    return proj


def test_ingest_reads_fix_log_into_fix_events(tmp_path, tmp_knowledge_dir):
    fix_log = [
        {"check": "drc", "iter": 1, "strategy": "antenna_density_relief",
         "before": "147", "after": "147", "verdict": "no_improvement",
         "from_stage": "floorplan", "fix_session_id": "sessA",
         "violation_class": "M3_ANTENNA",
         "before_categories": _json.dumps({"M3_ANTENNA": {"count": 147}}),
         "cumulative_config": _json.dumps({"CORE_UTILIZATION": "15"}),
         "ts": "2026-06-05T00:00:00Z"},
        {"check": "drc", "iter": 2, "strategy": "antenna_diode_repair",
         "before": "147", "after": "0", "verdict": "cleared",
         "from_stage": "route", "fix_session_id": "sessA",
         "violation_class": "M3_ANTENNA",
         "before_categories": _json.dumps({"M3_ANTENNA": {"count": 147}}),
         "cumulative_config": _json.dumps({"SKIP_ANTENNA_REPAIR": "1"}),
         "ts": "2026-06-05T00:01:00Z"},
    ]
    proj = _mk_project(tmp_path, fix_log=fix_log)
    conn = knowledge_db.connect(tmp_knowledge_dir / "runs.sqlite")
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    ingest_run.ingest(proj, conn,
                      families_path=tmp_knowledge_dir / "families.json")

    rows = list(conn.execute(
        "SELECT iter, strategy, verdict, violation_class, check_type, "
        "from_stage, design_family, platform FROM fix_events ORDER BY iter"))
    assert len(rows) == 2
    assert rows[0][2] == "no_change"      # 'no_improvement' normalized
    assert rows[1][2] == "cleared"
    assert rows[1][3] == "M3_ANTENNA"
    assert rows[1][4] == "drc" and rows[1][5] == "route"
    assert rows[0][6] == "demo" and rows[0][7] == "nangate45"  # identity backfilled

    # idempotent re-ingest: no duplicate fix_events
    ingest_run.ingest(proj, conn, families_path=tmp_knowledge_dir / "families.json")
    assert conn.execute("SELECT COUNT(*) FROM fix_events").fetchone()[0] == 2
    conn.close()


def test_ingest_writes_run_violations_snapshot(tmp_path, tmp_knowledge_dir):
    proj = _mk_project(tmp_path, drc_status="clean")
    conn = knowledge_db.connect(tmp_knowledge_dir / "runs.sqlite")
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    run_id = ingest_run.ingest(proj, conn,
                               families_path=tmp_knowledge_dir / "families.json")
    rv = conn.execute("SELECT run_id, drc_status, design_family FROM run_violations "
                      "WHERE run_id=?", (run_id,)).fetchone()
    assert rv is not None and rv[1] == "clean" and rv[2] == "demo"
    conn.close()
