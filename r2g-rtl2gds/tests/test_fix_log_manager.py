"""Unit tests for the autonomous fix-log manager (pure helpers)."""
from __future__ import annotations
import json
import fix_log_manager as flm


def test_canonical_key_merges_within_tolerance():
    e1 = {"check_type": "drc", "violation_class": "M2_ANTENNA",
          "strategy": "antenna_density_relief",
          "cumulative_config_json": json.dumps({"CORE_UTILIZATION": "15"})}
    e2 = dict(e1, cumulative_config_json=json.dumps({"CORE_UTILIZATION": "14"}))  # within 15%
    e3 = dict(e1, cumulative_config_json=json.dumps({"CORE_UTILIZATION": "5"}))   # far
    assert flm.canonical_action_key(e1) == flm.canonical_action_key(e2)
    assert flm.canonical_action_key(e1) != flm.canonical_action_key(e3)


def test_canonical_key_keeps_violation_class_distinct():
    base = {"check_type": "drc", "strategy": "antenna_diode_repair",
            "cumulative_config_json": "{}"}
    assert (flm.canonical_action_key(dict(base, violation_class="M2_ANTENNA"))
            != flm.canonical_action_key(dict(base, violation_class="M3_ANTENNA")))


def test_dedup_collapses_repeats_keeps_last():
    evs = [{"iter": 1, "check_type": "drc", "violation_class": "M2_ANTENNA",
            "strategy": "antenna_diode_repair", "cumulative_config_json": "{}", "after_count": 9},
           {"iter": 2, "check_type": "drc", "violation_class": "M2_ANTENNA",
            "strategy": "antenna_diode_repair", "cumulative_config_json": "{}", "after_count": 3}]
    out = flm.dedup_events_by_action(evs)
    assert len(out) == 1 and out[0]["after_count"] == 3   # freshest wins


def test_bound_rule_details_caps_samples():
    b = flm.bound_rule_details({"samples": list(range(100))}, top_n=20)
    assert b["total"] == 100 and len(b["samples"]) == 20 and b["truncated"] is True


def test_manage_relearns_and_recipes_survive_archive(tmp_knowledge_dir, monkeypatch):
    import json, knowledge_db, fix_log_manager as flm
    db = tmp_knowledge_dir / "runs.sqlite"
    conn = knowledge_db.connect(db)
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    cols = ("fix_session_id","design_family","platform","check_type","violation_class",
            "iter","strategy","before_count","after_count","verdict")
    for it,(strat,bc,ac,v) in enumerate(
            [("antenna_density_relief",147,147,"no_change"),
             ("antenna_diode_repair",147,0,"cleared")], start=1):
        conn.execute(f"INSERT INTO fix_events ({','.join(cols)}) VALUES ({','.join('?'*len(cols))})",
                     ("s1","ethernet","nangate45","drc","M2_ANTENNA",it,strat,bc,ac,v))
    conn.commit(); conn.close()

    out = tmp_knowledge_dir / "heuristics.json"
    monkeypatch.setattr(flm, "FIX_EVENTS_MAX_ROWS", 1)   # force archival
    rep = flm.manage(db, out_path=out)

    data = json.loads(out.read_text())
    strat = (data["families"]["ethernet"]["platforms"]["nangate45"]
             ["fix_recipes"]["drc"]["M2_ANTENNA"]["strategies"])
    assert strat["antenna_diode_repair"]["successes"] == 1   # survived archival
    assert rep["archived"] >= 1
    conn = knowledge_db.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM fix_events").fetchone()[0] == 0
    conn.close()
    assert (tmp_knowledge_dir / "fix_events_archive.sqlite").exists()


def test_recipes_survive_a_second_relearn_after_archive(tmp_knowledge_dir, monkeypatch):
    """After archive_old_raw moves an episode's raw events to the sidecar, the
    NEXT learn must rebuild trajectories from hot UNION archived — else the
    archived episode's recipe silently vanishes (bug #12)."""
    import json, knowledge_db, fix_log_manager as flm
    db = tmp_knowledge_dir / "runs.sqlite"
    conn = knowledge_db.connect(db)
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    cols = ("fix_session_id","design_family","platform","check_type","violation_class",
            "iter","strategy","before_count","after_count","verdict")
    for it,(strat,bc,ac,v) in enumerate(
            [("antenna_density_relief",147,147,"no_change"),
             ("antenna_diode_repair",147,0,"cleared")], start=1):
        conn.execute(f"INSERT INTO fix_events ({','.join(cols)}) VALUES ({','.join('?'*len(cols))})",
                     ("s1","ethernet","nangate45","drc","M2_ANTENNA",it,strat,bc,ac,v))
    conn.commit(); conn.close()

    out = tmp_knowledge_dir / "heuristics.json"
    monkeypatch.setattr(flm, "FIX_EVENTS_MAX_ROWS", 1)   # force archival on first manage
    flm.manage(db, out_path=out)                          # learn -> archive (hot now empty)

    conn = knowledge_db.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM fix_events").fetchone()[0] == 0
    conn.close()
    assert (tmp_knowledge_dir / "fix_events_archive.sqlite").exists()

    # Second manage: learn must rebuild from the archived events too.
    flm.manage(db, out_path=out)
    data = json.loads(out.read_text())
    strat = (data["families"]["ethernet"]["platforms"]["nangate45"]
             ["fix_recipes"]["drc"]["M2_ANTENNA"]["strategies"])
    assert strat["antenna_diode_repair"]["successes"] == 1   # survived 2nd rebuild
    # The trajectory must also still be present (re-derived from archive).
    conn = knowledge_db.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM fix_trajectories "
                        "WHERE fix_session_id='s1'").fetchone()[0] == 1
    conn.close()
