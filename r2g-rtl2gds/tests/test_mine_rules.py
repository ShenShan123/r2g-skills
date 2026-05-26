"""Tests for mine_rules.py."""
from __future__ import annotations

import json

import knowledge_db
import mine_rules


def _insert_run(conn, **row):
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


def test_mine_surfaces_repeated_signature(tmp_knowledge_dir):
    db_path = tmp_knowledge_dir / "runs.sqlite"
    conn = knowledge_db.connect(db_path)
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    # Three PDN-0179 failures across two distinct designs
    for i, design in enumerate(["black_parrot", "black_parrot", "swerv_wrapper"]):
        rid = f"fail_{i}"
        _insert_run(conn, run_id=rid, design_name=design,
                    design_family="bp_multi_top" if "parrot" in design else "swerv",
                    platform="nangate45",
                    core_utilization=40.0, place_density_lb_addon=0.20,
                    synth_hierarchical=1, abc_area=1,
                    orfs_status="fail", orfs_fail_stage="floorplan")
        conn.execute(
            "INSERT INTO failure_events (run_id, stage, signature, detail) "
            "VALUES (?, ?, ?, ?)",
            (rid, "floorplan", "pdn-0179", "Unable to repair all channels."),
        )
    # One irrelevant single failure (should not surface)
    _insert_run(conn, run_id="noise", design_name="ibex_core",
                design_family="ibex", platform="nangate45",
                orfs_status="fail", orfs_fail_stage="route")
    conn.execute(
        "INSERT INTO failure_events (run_id, stage, signature, detail) "
        "VALUES (?, ?, ?, ?)",
        ("noise", "route", "grt-0116", None),
    )
    conn.commit()
    conn.close()

    out = tmp_knowledge_dir / "failure_candidates.json"
    mine_rules.mine(db_path, out)

    data = json.loads(out.read_text())
    sigs = {c["signature"]: c for c in data["candidates"]}
    assert "pdn-0179" in sigs
    assert sigs["pdn-0179"]["occurrences"] == 3
    assert sigs["pdn-0179"]["distinct_designs"] == 2
    assert "grt-0116" not in sigs  # below threshold
