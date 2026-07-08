import importlib
import json

import knowledge_db
sync_lessons = importlib.import_module("sync_lessons")


def test_sync_parses_frontmatter_and_backfills_evidence(tmp_path, tmp_knowledge_dir):
    md = tmp_path / "failure-patterns.md"
    md.write_text(
        "# Failure Patterns\n\n"
        "## LVS symmetric-matcher residual\n"
        "<!-- r2g-lesson:\n"
        "id: lesson-lvs-symmetric-matcher\n"
        "status: active\n"
        'trigger: {check: lvs, class: symmetric_matcher, platform: "*"}\n'
        "strategy_ids: [lvs_same_nets_seed]\n"
        "-->\n"
        "Balanced unmatched nets + zero device mismatches => tool artifact; stop re-running.\n")
    conn = knowledge_db.connect(tmp_knowledge_dir / "knowledge.sqlite")
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    import symptom
    sig = symptom.canonical_signature("lvs", "symmetric_matcher", None)
    sid = symptom.symptom_id(sig)
    conn.execute("INSERT INTO runs (run_id, project_path, ingested_at) "
                 "VALUES ('r1','/x','2026-06-09T00:00:00Z')")
    conn.execute("INSERT INTO run_violations (run_id, lvs_status, symptom_id, "
                 "signature_json, snapshot_ts) VALUES "
                 "('r1','fail',?,?,?)", (sid, json.dumps(sig), "2026-06-09T00:00:00Z"))
    conn.commit()
    n = sync_lessons.sync(conn, patterns_path=md)
    assert n == 1
    row = conn.execute(
        "SELECT lesson_id, status, symptom_trigger_json, evidence_runs_json "
        "FROM lessons").fetchone()
    assert row[0] == "lesson-lvs-symmetric-matcher" and row[1] == "active"
    assert json.loads(row[2])["check"] == "lvs"
    assert "r1" in json.loads(row[3])
    # idempotent: same content -> still one row, no error.
    assert sync_lessons.sync(conn, patterns_path=md) == 1
    conn.close()


def test_parse_frontmatter_handles_dict_and_barelist():
    # trigger dict + a bare-word strategy_ids list must both parse to real JSON.
    fm = sync_lessons._parse_frontmatter(
        "id: l1\nstatus: active\n"
        'trigger: {check: lvs, class: symmetric_matcher, platform: "*"}\n'
        "strategy_ids: [lvs_same_nets_seed, antenna_diode_repair]")
    assert fm["trigger"] == {"check": "lvs", "class": "symmetric_matcher",
                             "platform": "*"}
    assert fm["strategy_ids"] == ["lvs_same_nets_seed", "antenna_diode_repair"]
