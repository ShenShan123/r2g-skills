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
    db_path = tmp_knowledge_dir / "runs.sqlite"
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
    db_path = tmp_knowledge_dir / "runs.sqlite"
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


def test_learn_skips_family_with_no_successful_runs(tmp_knowledge_dir):
    db_path = tmp_knowledge_dir / "runs.sqlite"
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
