"""Strength metrics (spec §5.6 / decision 6): trends vs heuristics generation."""
import build_strength_report
import knowledge_db


def _seed(conn, gen, first_clean, iters, design="d", n=4):
    for i in range(n):
        conn.execute(
            "INSERT OR REPLACE INTO runs (run_id, project_path, design_name,"
            " platform, ingested_at, heuristics_generation,"
            " first_attempt_clean, fix_iters_to_clean, wall_s_to_clean)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (f"r{gen}_{design}_{i}", f"/p/{design}{gen}{i}", f"{design}{gen}{i}",
             "nangate45", "t", gen, first_clean, iters, 100.0 * (i + 1)))
    conn.commit()


def test_report_groups_by_generation(tmp_path):
    conn = knowledge_db.connect(tmp_path / "knowledge.sqlite")
    knowledge_db.ensure_schema(conn)
    _seed(conn, gen=1, first_clean=0, iters=4)
    _seed(conn, gen=2, first_clean=1, iters=1)
    rep = build_strength_report.build(tmp_path / "knowledge.sqlite")
    gens = {g["generation"]: g for g in rep["generations"]}
    assert gens[1]["first_pass_clean_rate"] == 0.0
    assert gens[2]["first_pass_clean_rate"] == 1.0
    assert gens[2]["median_fix_iters_to_clean"] == 1
    assert rep["trend"]["first_pass_improving"] is True


def test_transfer_evidence_lists_cross_design_wins(tmp_path):
    conn = knowledge_db.connect(tmp_path / "knowledge.sqlite")
    knowledge_db.ensure_schema(conn)
    for d, sess in (("alpha", "s1"), ("beta", "s2")):
        conn.execute(
            "INSERT OR REPLACE INTO fix_trajectories (fix_session_id,"
            " project_path, design_name, platform, check_type, path_json,"
            " outcome, winning_strategy, symptom_id) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (sess, f"/p/{d}", d, "nangate45", "drc", "[]", "resolved",
             "antenna_diode_repair", "deadbeef00000001"))
    conn.commit()
    rep = build_strength_report.build(tmp_path / "knowledge.sqlite")
    ev = rep["transfer_evidence"][0]
    assert ev["symptom_id"] == "deadbeef00000001"
    assert set(ev["designs"]) == {"alpha", "beta"}
