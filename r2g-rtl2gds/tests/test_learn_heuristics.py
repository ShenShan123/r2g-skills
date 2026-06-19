"""Tests for learn_heuristics.py."""
from __future__ import annotations

import json

import knowledge_db
import learn_heuristics


def _insert(conn, **row):
    defaults = dict.fromkeys([
        "run_id", "project_path", "design_name", "design_family", "platform",
        "ingested_at", "core_utilization", "place_density_lb_addon",
        "synth_hierarchical", "abc_area", "die_area", "clock_period_ns",
        "extra_config_json", "orfs_status", "orfs_fail_stage", "wns_ns", "tns_ns",
        "timing_tier", "cell_count", "area_um2", "power_mw",
        "drc_status", "drc_violations", "lvs_status", "rcx_status",
        "total_elapsed_s", "stage_times_json",
    ])
    defaults.update(row)
    defaults["ingested_at"] = "2026-04-11T00:00:00Z"
    defaults["project_path"] = defaults["project_path"] or f"/tmp/{defaults['run_id']}"
    cols = ", ".join(defaults.keys())
    ph = ", ".join(f":{k}" for k in defaults.keys())
    conn.execute(f"INSERT INTO runs ({cols}) VALUES ({ph})", defaults)


def _seed_aes_family(conn, good: int, bad: int):
    for i in range(good):
        _insert(conn, run_id=f"aes_good_{i}", design_name="aes128_core",
                design_family="aes_xcrypt", platform="nangate45",
                core_utilization=20.0 + i,
                place_density_lb_addon=0.18 + i * 0.02,
                cell_count=12000 + i * 100,
                orfs_status="pass",
                # Match the real values emitted by extract_{drc,lvs,rcx}.py
                drc_status="clean", lvs_status="clean", rcx_status="complete",
                total_elapsed_s=2000 + i * 10)
    for i in range(bad):
        _insert(conn, run_id=f"aes_bad_{i}", design_name="aes128_core",
                design_family="aes_xcrypt", platform="nangate45",
                core_utilization=45.0, place_density_lb_addon=0.05,
                cell_count=12500,
                orfs_status="fail", orfs_fail_stage="place",
                total_elapsed_s=900)


def test_learn_produces_family_heuristics(tmp_knowledge_dir):
    db_path = tmp_knowledge_dir / "knowledge.sqlite"
    conn = knowledge_db.connect(db_path)
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    _seed_aes_family(conn, good=5, bad=2)
    conn.commit()
    conn.close()

    out = tmp_knowledge_dir / "heuristics.json"
    learn_heuristics.learn(db_path, out)

    data = json.loads(out.read_text())
    assert data["source_run_count"] == 7
    fam = data["families"]["aes_xcrypt"]["platforms"]["nangate45"]
    # Only successful runs inform min/max/median bounds
    assert fam["success_count"] == 5
    assert fam["core_utilization"]["min_safe"] == 20.0
    assert fam["core_utilization"]["max_safe"] == 24.0
    assert fam["core_utilization"]["median"] == 22.0
    assert abs(fam["place_density_lb_addon"]["min_safe"] - 0.18) < 1e-9
    assert abs(fam["place_density_lb_addon"]["max_safe"] - 0.26) < 1e-9
    assert abs(fam["place_density_lb_addon"]["median"]  - 0.22) < 1e-9
    assert fam["success_rate"] == 5 / 7
    # Lock in the p90 nearest-rank formula and typical_cell_count. Both are
    # now derived from successful runs only (see Issue 1), so failed runs
    # must not affect these values.
    # Successful elapsed times: [2000, 2010, 2020, 2030, 2040] (sorted).
    # p90 idx = round(0.9 * (5 - 1)) = 4 → value 2040.
    assert fam["p90_elapsed_s"] == 2040
    # Successful cell counts: [12000, 12100, 12200, 12300, 12400], median = 12200
    assert fam["typical_cell_count"] == 12200


def test_learn_skips_families_with_too_few_samples(tmp_knowledge_dir):
    db_path = tmp_knowledge_dir / "knowledge.sqlite"
    conn = knowledge_db.connect(db_path)
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    # Seed 2 successful runs — one short of MIN_SUCCESSFUL=3 — so this test
    # would fail if the threshold silently regressed to >= 1 or >= 2.
    for i in range(2):
        _insert(conn, run_id=f"lonely_{i}", design_name="foobar",
                design_family="foobar", platform="nangate45",
                core_utilization=30, place_density_lb_addon=0.20,
                orfs_status="pass",
                drc_status="clean", lvs_status="clean", rcx_status="complete")
    conn.commit()
    conn.close()

    out = tmp_knowledge_dir / "heuristics.json"
    learn_heuristics.learn(db_path, out)
    data = json.loads(out.read_text())
    assert "foobar" not in data["families"]


def _learn_to_dict(tmp_knowledge_dir, db_path):
    out = tmp_knowledge_dir / "heuristics.json"
    learn_heuristics.learn(db_path, out)
    return json.loads(out.read_text())


def test_learn_admits_signoff_positive_partial_runs(tmp_knowledge_dir):
    """Relaxed predicate: partial runs with clean DRC/LVS/RCX are learnable."""
    db_path = tmp_knowledge_dir / "knowledge.sqlite"
    conn = knowledge_db.connect(db_path)
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    for i in range(3):
        _insert(conn, run_id=f"i2c_partial_{i}", design_name="i2c_master",
                design_family="i2c", platform="nangate45",
                core_utilization=20.0 + i,
                place_density_lb_addon=0.20 + i * 0.02,
                cell_count=3000 + i * 100,
                orfs_status="partial", orfs_fail_stage="route",
                drc_status="clean", lvs_status="clean", rcx_status="complete",
                total_elapsed_s=1000 + i * 10)
    conn.commit()
    conn.close()

    data = _learn_to_dict(tmp_knowledge_dir, db_path)
    fam = data["families"]["i2c"]["platforms"]["nangate45"]
    assert fam["success_count"] == 3
    assert fam["core_utilization"]["median"] == 21.0
    assert abs(fam["place_density_lb_addon"]["median"] - 0.22) < 1e-9
    assert fam["typical_cell_count"] == 3100


def test_learn_excludes_partial_without_positive_signoff(tmp_knowledge_dir):
    """Absence of all signoff data is NOT success — no fabrication guarantee."""
    db_path = tmp_knowledge_dir / "knowledge.sqlite"
    conn = knowledge_db.connect(db_path)
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    for i in range(3):
        _insert(conn, run_id=f"i2c_bare_{i}", design_name="i2c_master",
                design_family="i2c", platform="nangate45",
                core_utilization=20.0 + i, place_density_lb_addon=0.20,
                cell_count=3000,
                orfs_status="partial", orfs_fail_stage="route",
                drc_status=None, lvs_status=None, rcx_status=None,
                total_elapsed_s=1000)
    conn.commit()
    conn.close()

    data = _learn_to_dict(tmp_knowledge_dir, db_path)
    assert "i2c" not in data["families"]


def test_learn_excludes_partial_with_failed_signoff(tmp_knowledge_dir):
    """A failed/incomplete signoff blocks success even with other positives."""
    db_path = tmp_knowledge_dir / "knowledge.sqlite"
    conn = knowledge_db.connect(db_path)
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    for i in range(3):
        _insert(conn, run_id=f"i2c_lvsfail_{i}", design_name="i2c_master",
                design_family="i2c", platform="nangate45",
                core_utilization=20.0 + i, place_density_lb_addon=0.20,
                cell_count=3000,
                orfs_status="partial", orfs_fail_stage="route",
                drc_status="clean", lvs_status="incomplete",
                rcx_status="complete", total_elapsed_s=1000)
    conn.commit()
    conn.close()

    data = _learn_to_dict(tmp_knowledge_dir, db_path)
    assert "i2c" not in data["families"]


def test_symmetric_matcher_lvs_counts_as_success(tmp_knowledge_dir):
    """lvs_status='fail' + mismatch_class='symmetric_matcher' is a clean layout."""
    db_path = tmp_knowledge_dir / "knowledge.sqlite"
    conn = knowledge_db.connect(db_path)
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    for i in range(3):
        _insert(conn, run_id=f"i2c_sym_{i}", design_name="i2c_master",
                design_family="i2c", platform="nangate45",
                core_utilization=22.0 + i, place_density_lb_addon=0.20,
                cell_count=3000,
                orfs_status="partial", orfs_fail_stage="route",
                drc_status="clean", lvs_status="fail",
                lvs_mismatch_class="symmetric_matcher",
                rcx_status="complete", total_elapsed_s=1000)
    conn.commit()
    conn.close()

    data = _learn_to_dict(tmp_knowledge_dir, db_path)
    assert data["families"]["i2c"]["platforms"]["nangate45"]["success_count"] == 3


def test_clean_beol_drc_counts_as_success(tmp_knowledge_dir):
    """drc_status='clean_beol' is a positive clean signal."""
    db_path = tmp_knowledge_dir / "knowledge.sqlite"
    conn = knowledge_db.connect(db_path)
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    for i in range(3):
        _insert(conn, run_id=f"i2c_beol_{i}", design_name="i2c_master",
                design_family="i2c", platform="nangate45",
                core_utilization=22.0 + i, place_density_lb_addon=0.20,
                cell_count=3000,
                orfs_status="partial", orfs_fail_stage="route",
                drc_status="clean_beol", lvs_status="clean",
                rcx_status="complete", total_elapsed_s=1000)
    conn.commit()
    conn.close()

    data = _learn_to_dict(tmp_knowledge_dir, db_path)
    assert data["families"]["i2c"]["platforms"]["nangate45"]["success_count"] == 3


def test_learn_skips_family_with_no_successful_runs(tmp_knowledge_dir):
    db_path = tmp_knowledge_dir / "knowledge.sqlite"
    conn = knowledge_db.connect(db_path)
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    # Three rows, all failed — locks in the "zero successes → absent from
    # output" contract even though sample_size >= MIN_SUCCESSFUL.
    for i in range(3):
        _insert(conn, run_id=f"aes_allfail_{i}", design_name="aes128_core",
                design_family="aes_xcrypt", platform="nangate45",
                core_utilization=45.0, place_density_lb_addon=0.05,
                cell_count=12500,
                orfs_status="fail", orfs_fail_stage="place",
                total_elapsed_s=900)
    conn.commit()
    conn.close()

    out = tmp_knowledge_dir / "heuristics.json"
    learn_heuristics.learn(db_path, out)
    data = json.loads(out.read_text())
    assert "aes_xcrypt" not in data["families"]


def test_trajectories_carry_symptom(tmp_knowledge_dir):
    db = tmp_knowledge_dir / "knowledge.sqlite"
    conn = knowledge_db.connect(db)
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    # Insert two raw fix_events for one episode, both tagged with a symptom.
    sig = '{"check": "lvs", "class": "symmetric_matcher", "predicates": {}}'
    for it, verdict in ((1, "no_change"), (2, "cleared")):
        conn.execute(
            "INSERT INTO fix_events (fix_session_id, check_type, violation_class, "
            " iter, strategy, verdict, symptom_id, signature_json, ts) "
            "VALUES ('e1','lvs','symmetric_matcher',?,?,?,?,?,?)",
            (it, "lvs_same_nets_seed", verdict, "abc123def4560000", sig,
             f"2026-06-09T00:0{it}:00Z"))
    conn.commit()
    learn_heuristics._rebuild_fix_trajectories(conn)
    row = conn.execute(
        "SELECT symptom_id, signature_json FROM fix_trajectories").fetchone()
    assert row[0] == "abc123def4560000"
    assert json.loads(row[1])["class"] == "symmetric_matcher"
    conn.close()


def test_learn_emits_symptom_projection_pooled_across_families(tmp_knowledge_dir):
    db = tmp_knowledge_dir / "knowledge.sqlite"
    conn = knowledge_db.connect(db)
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    sig = '{"check": "drc", "class": "METAL1_ANTENNA", "predicates": {}}'
    sid = __import__("symptom").symptom_id(json.loads(sig))
    # Same symptom, TWO different families/platforms -> must pool into one bucket.
    rows = [("e_aes", "aes", "nangate45", "demo_aes"),
            ("e_fft", "fft", "nangate45", "demo_fft")]
    for ep, fam, plat, dn in rows:
        conn.execute(
            "INSERT INTO fix_events (fix_session_id, design_name, design_family, "
            " platform, check_type, violation_class, iter, strategy, verdict, "
            " symptom_id, signature_json, ts) "
            "VALUES (?,?,?,?,'drc','METAL1_ANTENNA',1,'antenna_diode_repair',"
            " 'cleared',?,?,?)",
            (ep, dn, fam, plat, sid, sig, "2026-06-09T00:00:00Z"))
    conn.commit(); conn.close()
    out = tmp_knowledge_dir / "heuristics.json"
    learn_heuristics.learn(db, out)
    data = json.loads(out.read_text())
    bucket = data["symptoms"][sid]
    assert bucket["check"] == "drc" and bucket["class"] == "METAL1_ANTENNA"
    assert bucket["n_sessions"] == 2                      # pooled across aes + fft
    assert set(bucket["platforms_seen"]) == {"nangate45"}
    assert sorted(bucket["evidence_designs"]) == ["demo_aes", "demo_fft"]
    strat = bucket["strategies"]["antenna_diode_repair"]
    assert strat["successes"] == 2
    assert strat["by_platform"]["nangate45"]["successes"] == 2
    # family name must NOT be a key anywhere in the symptom projection
    assert "aes" not in json.dumps(list(data["symptoms"].keys()))


def test_learn_emits_closing_period_and_deterioration(tmp_knowledge_dir):
    import knowledge_db, learn_heuristics
    conn = knowledge_db.connect(tmp_knowledge_dir / "runs.sqlite")
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    # 4 signoff-positive runs for (alu, nangate45): period, fp, place, finish.
    rows = [(10.0, 1.0, 0.8, 0.6),
            (10.0, 1.2, 0.9, 0.7),
            (8.0, 0.9, 0.7, 0.5),
            (8.0, 1.1, 0.8, 0.6)]
    for i, (period, fp, pl, fin) in enumerate(rows):
        conn.execute(
            "INSERT INTO runs (run_id, project_path, design_name, design_family, "
            "platform, ingested_at, clock_period_ns, floorplan_setup_ws, "
            "place_setup_ws, finish_setup_ws, wns_ns, drc_status, lvs_status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"r{i}", f"/tmp/r{i}", "alu", "alu", "nangate45", "2026-01-01T00:00:00Z",
             period, fp, pl, fin, fin, "clean", "clean"))
    conn.commit()
    out = tmp_knowledge_dir / "heuristics.json"
    data = learn_heuristics.learn(tmp_knowledge_dir / "runs.sqlite", out)
    entry = data["families"]["alu"]["platforms"]["nangate45"]
    # closing_period = period - finish_ws ; min over rows = min(9.4,9.3,7.5,7.4)=7.4
    assert entry["closing_period"]["min"] == 7.4
    sd = entry["slack_deterioration"]
    assert sd["n"] == 4
    # d_fp_pl per row = fp-place = [0.2,0.3,0.2,0.3]; p90 (idx round(0.9*3)=3) = 0.3
    assert abs(sd["d_fp_pl"]["ns_p90"] - 0.3) < 1e-9
    # d_pl_fin per row = place-finish = [0.2,0.2,0.2,0.2]; p90 = 0.2
    assert abs(sd["d_pl_fin"]["ns_p90"] - 0.2) < 1e-9
    conn.close()
