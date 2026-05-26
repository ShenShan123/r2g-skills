"""Tests for monitor_health.py: detect family/platform degradation."""
from __future__ import annotations

import knowledge_db
import monitor_health


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
    conn = knowledge_db.connect(tmp_knowledge_dir / "runs.sqlite")
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

    alerts = monitor_health.check(
        db_path=tmp_knowledge_dir / "runs.sqlite",
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

    alerts = monitor_health.check(
        db_path=tmp_knowledge_dir / "runs.sqlite",
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

    alerts = monitor_health.check(
        db_path=tmp_knowledge_dir / "runs.sqlite",
        window=3,
        threshold=0.5,
    )
    assert len(alerts) == 0
    conn.close()
