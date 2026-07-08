"""Decision-8 recipe projection + monotonic generation counter."""
import json

import knowledge_db
import learn_heuristics


def _seed(conn, *, design_class="crypto/small", platform="nangate45",
          sid="s1", strategy="antenna_diode_repair", verdict="cleared"):
    conn.execute(
        "INSERT OR REPLACE INTO runs (run_id, project_path, design_name, "
        "design_family, platform, ingested_at, design_class) "
        "VALUES (?,?,?,?,?,?,?)",
        (f"r_{sid}", f"/p/{sid}", f"d_{sid}", "fam", platform,
         "2026-06-10T00:00:00Z", design_class))
    conn.execute(
        "INSERT OR IGNORE INTO fix_events (fix_session_id, project_path, "
        "design_name, platform, check_type, violation_class, iter, strategy, "
        "before_count, after_count, verdict, ts, provenance, symptom_id, "
        "signature_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (sid, f"/p/{sid}", f"d_{sid}", platform, "drc", "antenna", 1, strategy,
         5, 0 if verdict == "cleared" else 5, verdict,
         "2026-06-10T00:00:00Z", "live", "deadbeef00000001",
         json.dumps({"check": "drc", "class": "antenna", "predicates": {}})))
    conn.commit()


def test_recipes_keyed_symptom_class_platform(tmp_path):
    db = tmp_path / "knowledge.sqlite"
    conn = knowledge_db.connect(db)
    knowledge_db.ensure_schema(conn)
    _seed(conn)
    data = learn_heuristics.learn(db, tmp_path / "heuristics.json")
    node = data["recipes"]["deadbeef00000001"]["crypto/small"]["nangate45"]
    assert node["strategies"]["antenna_diode_repair"]["successes"] == 1


def test_star_rollups_pool_across_class_and_platform(tmp_path):
    db = tmp_path / "knowledge.sqlite"
    conn = knowledge_db.connect(db)
    knowledge_db.ensure_schema(conn)
    _seed(conn, design_class="crypto/small", platform="nangate45", sid="s1")
    _seed(conn, design_class="logic/medium", platform="sky130hd", sid="s2")
    data = learn_heuristics.learn(db, tmp_path / "heuristics.json")
    bucket = data["recipes"]["deadbeef00000001"]
    # class rollup pools both classes for one platform-agnostic view
    assert bucket["*"]["*"]["strategies"]["antenna_diode_repair"]["attempts"] == 2
    assert bucket["crypto/small"]["*"]["strategies"][
        "antenna_diode_repair"]["attempts"] == 1


def test_generation_increments_monotonically(tmp_path):
    db = tmp_path / "knowledge.sqlite"
    conn = knowledge_db.connect(db)
    knowledge_db.ensure_schema(conn)
    _seed(conn)
    d1 = learn_heuristics.learn(db, tmp_path / "heuristics.json")
    d2 = learn_heuristics.learn(db, tmp_path / "heuristics.json")
    assert d2["generation"] == d1["generation"] + 1
