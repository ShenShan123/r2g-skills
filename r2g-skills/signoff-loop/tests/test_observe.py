"""Tests for observe.py — the read-only observability module (2026-07-18 merge
of monitor_health + trace_provenance): degradation alerts + cross-DB provenance."""
from __future__ import annotations

import knowledge_db
import journal_db
import observe
import recipe_lifecycle


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
    defaults["ingested_at"] = defaults.get("ingested_at") or "2026-04-11T00:00:00Z"
    defaults["project_path"] = defaults["project_path"] or f"/tmp/{defaults['run_id']}"
    cols = ", ".join(defaults.keys())
    ph = ", ".join(f":{k}" for k in defaults.keys())
    conn.execute(f"INSERT INTO runs ({cols}) VALUES ({ph})", defaults)


def _open_db(tmp_knowledge_dir):
    conn = knowledge_db.connect(tmp_knowledge_dir / "knowledge.sqlite")
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    return conn


def test_detects_degradation(tmp_knowledge_dir):
    """5 old passes + 3 recent failures should flag degradation."""
    conn = _open_db(tmp_knowledge_dir)
    for i in range(5):
        _insert(conn, run_id=f"old_pass_{i}",
                design_name="aes128_core", design_family="aes_xcrypt",
                platform="nangate45", orfs_status="pass",
                drc_status="clean", lvs_status="clean", rcx_status="complete",
                ingested_at=f"2026-04-0{i+1}T00:00:00Z")
    for i in range(3):
        _insert(conn, run_id=f"new_fail_{i}",
                design_name="aes128_core", design_family="aes_xcrypt",
                platform="nangate45", orfs_status="fail",
                orfs_fail_stage="place",
                ingested_at=f"2026-04-1{i}T00:00:00Z")
    conn.commit()

    alerts = observe.check(
        db_path=tmp_knowledge_dir / "knowledge.sqlite",
        window=3,
        threshold=0.5,
    )
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert["family"] == "aes_xcrypt"
    assert alert["platform"] == "nangate45"
    assert alert["recent_success_rate"] == 0.0
    assert alert["historical_success_rate"] > 0.5
    assert alert["severity"] == "degraded"
    conn.close()


def test_no_alert_when_healthy(tmp_knowledge_dir):
    """All-pass family should produce no alerts."""
    conn = _open_db(tmp_knowledge_dir)
    for i in range(5):
        _insert(conn, run_id=f"healthy_{i}",
                design_name="ibex_core", design_family="ibex",
                platform="nangate45", orfs_status="pass",
                drc_status="clean", lvs_status="clean", rcx_status="complete",
                ingested_at=f"2026-04-0{i+1}T00:00:00Z")
    conn.commit()

    alerts = observe.check(
        db_path=tmp_knowledge_dir / "knowledge.sqlite",
        window=3,
        threshold=0.5,
    )
    assert len(alerts) == 0
    conn.close()


def test_skips_families_with_too_few_runs(tmp_knowledge_dir):
    """Families with fewer than window runs should not produce alerts."""
    conn = _open_db(tmp_knowledge_dir)
    _insert(conn, run_id="lone_fail",
            design_name="tiny_design", design_family="tiny",
            platform="nangate45", orfs_status="fail",
            orfs_fail_stage="synth",
            ingested_at="2026-04-11T00:00:00Z")
    conn.commit()

    alerts = observe.check(
        db_path=tmp_knowledge_dir / "knowledge.sqlite",
        window=3,
        threshold=0.5,
    )
    assert len(alerts) == 0
    conn.close()


# --- trace (formerly trace_provenance.py; spec §5.9, decision 11) -----------
KEY = dict(symptom_id="deadbeef00000001", design_class="crypto/small",
           platform="nangate45", strategy="antenna_diode_repair")


def _setup(tmp_path):
    kc = knowledge_db.connect(tmp_path / "knowledge.sqlite")
    knowledge_db.ensure_schema(kc)
    jc = journal_db.connect(tmp_path / "journal.sqlite")
    journal_db.ensure_schema(jc)
    # knowledge side: run + trajectory + promoted recipe + trial
    kc.execute("INSERT OR REPLACE INTO runs (run_id, project_path, design_name,"
               " platform, ingested_at, design_class) "
               "VALUES ('r1','/p/d1','d1','nangate45','t','crypto/small')")
    kc.execute("INSERT OR REPLACE INTO fix_trajectories (fix_session_id,"
               " project_path, design_name, platform, check_type,"
               " violation_class, path_json, outcome, winning_strategy,"
               " symptom_id) VALUES ('sess1','/p/d1','d1','nangate45','drc',"
               "'antenna','[]','resolved','antenna_diode_repair',"
               "'deadbeef00000001')")
    kc.execute("INSERT INTO ab_trials (symptom_id, design_class, platform,"
               " strategy, verdict, ts) VALUES (?,?,?,?,'win','t')",
               tuple(KEY.values()))
    recipe_lifecycle.promote(kc, evidence="ab_trial:1", **KEY)
    kc.commit()
    # journal side: action + bug for the same session/run
    journal_db.append_action(jc, project_path="/p/d1", actor="loop",
                             action_type="config_knob_delta",
                             payload={"knob": "SKIP_ANTENNA_REPAIR", "new": "1"},
                             fix_session_id="sess1", run_id="r1")
    journal_db.append_tool_bug(jc, project_path="/p/d1", stage="route",
                               tool="openroad", signature="antenna ratio",
                               symptom_id="deadbeef00000001", run_id="r1")
    return kc, jc


def test_solution_to_origin_tree(tmp_path):
    _setup(tmp_path)
    tree = observe.solution_origin(
        knowledge_db_path=tmp_path / "knowledge.sqlite",
        journal_db_path=tmp_path / "journal.sqlite", **KEY)
    assert tree["status"] == "promoted"
    assert tree["ab_trials"][0]["verdict"] == "win"
    assert tree["episodes"][0]["design_name"] == "d1"
    assert tree["episodes"][0]["actions"][0]["action_type"] == "config_knob_delta"
    assert tree["bugs"][0]["signature"] == "antenna ratio"


def test_bug_to_solutions(tmp_path):
    _setup(tmp_path)
    sols = observe.bug_solutions(
        knowledge_db_path=tmp_path / "knowledge.sqlite",
        symptom_id="deadbeef00000001")
    assert sols[0]["strategy"] == "antenna_diode_repair"
    assert sols[0]["status"] == "promoted"
    assert "d1" in sols[0]["proven_on"]


def test_read_only_no_writes(tmp_path):
    kc, jc = _setup(tmp_path)
    before = (tmp_path / "knowledge.sqlite").stat().st_mtime_ns
    observe.solution_origin(
        knowledge_db_path=tmp_path / "knowledge.sqlite",
        journal_db_path=tmp_path / "journal.sqlite", **KEY)
    assert (tmp_path / "knowledge.sqlite").stat().st_mtime_ns == before
