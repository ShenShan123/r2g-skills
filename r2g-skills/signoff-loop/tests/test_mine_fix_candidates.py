"""Tests for the evidence-backed fix_candidates rollup in mine_rules.py."""
from __future__ import annotations

import json

import knowledge_db
import mine_rules


def _insert_trajectory(conn, *, fix_session_id, design_name, design_family,
                       platform, check_type, violation_class, outcome,
                       winning_strategy, path_json=None):
    if path_json is None:
        # Default path: a single step whose verdict matches the outcome, so the
        # per-strategy success/failure tally (parsed from path_json) is coherent.
        if outcome == "resolved":
            step = {"iter": 0, "strategy": winning_strategy,
                    "before": 10, "after": 0, "verdict": "cleared"}
        else:
            step = {"iter": 0, "strategy": winning_strategy or "none",
                    "before": 10, "after": 8, "verdict": "no_change"}
        path_json = json.dumps([step])
    conn.execute(
        "INSERT INTO fix_trajectories "
        "(fix_session_id, project_path, design_name, design_family, platform, "
        " check_type, violation_class, path_json, n_iters, outcome, "
        " winning_strategy, winning_config_json, failed_strategies_json, "
        " initial_count, final_count, total_elapsed_s) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (fix_session_id, f"/tmp/{fix_session_id}", design_name, design_family,
         platform, check_type, violation_class, path_json, 1, outcome,
         winning_strategy, "{}", "[]", 10.0,
         0.0 if outcome == "resolved" else 5.0, 12.0),
    )


def test_mine_emits_fix_candidates(tmp_knowledge_dir):
    db_path = tmp_knowledge_dir / "knowledge.sqlite"
    conn = knowledge_db.connect(db_path)
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")

    # 4 resolved episodes sharing (family, check, violation_class, winning_strategy)
    designs = ["eth_demux", "eth_axis", "eth_mac", "eth_phy"]
    for i, design in enumerate(designs):
        _insert_trajectory(
            conn, fix_session_id=f"sess_{i}", design_name=design,
            design_family="verilog_ethernet", platform="nangate45",
            check_type="drc", violation_class="M3_ANTENNA",
            outcome="resolved", winning_strategy="antenna_diode_repair",
        )
    # 1 abandoned episode in the same group. Mirrors _build_trajectory: an
    # abandoned episode carries winning_strategy=None, but its path_json records
    # the strategy that was *tried and failed* (verdict no_change/regression).
    # This drives the named strategy's clearance_rate below 1.0 and must NOT
    # collapse into a phantom strategy=None bucket.
    _insert_trajectory(
        conn, fix_session_id="sess_aband", design_name="eth_loop",
        design_family="verilog_ethernet", platform="nangate45",
        check_type="drc", violation_class="M3_ANTENNA",
        outcome="abandoned", winning_strategy=None,
        path_json=json.dumps([
            {"iter": 0, "strategy": "antenna_diode_repair",
             "before": 12, "after": 12, "verdict": "no_change"},
            {"iter": 1, "strategy": "antenna_diode_repair",
             "before": 12, "after": 15, "verdict": "regression"},
        ]),
    )
    # A different group with only 2 resolved -> below resolved>=3 threshold
    for i, design in enumerate(["ibex_a", "ibex_b"]):
        _insert_trajectory(
            conn, fix_session_id=f"ibex_{i}", design_name=design,
            design_family="ibex", platform="sky130hd",
            check_type="lvs", violation_class="NET_MISMATCH",
            outcome="resolved", winning_strategy="rename_net",
        )
    conn.commit()
    conn.close()

    out = tmp_knowledge_dir / "failure_candidates.json"
    data = mine_rules.mine(db_path, out)

    assert "fix_candidates" in data
    written = json.loads(out.read_text())
    assert written["fix_candidates"] == data["fix_candidates"]

    cands = data["fix_candidates"]
    # Only the verilog_ethernet/drc/M3_ANTENNA/antenna_diode_repair strategy
    # clears resolved>=3 (4 cleared steps across the resolved episodes).
    assert len(cands) == 1
    c = cands[0]
    assert c["family"] == "verilog_ethernet"
    assert c["platform"] == "nangate45"
    assert c["check"] == "drc"
    assert c["violation_class"] == "M3_ANTENNA"
    assert c["winning_strategy"] == "antenna_diode_repair"
    # Successes/failures are tallied PER STRATEGY from path_json, not from the
    # episode outcome column. The abandoned episode contributes 2 failed steps
    # (no_change + regression) for antenna_diode_repair.
    assert c["resolved"] == 4
    assert c["abandoned"] == 2
    assert abs(c["clearance_rate"] - (4 / 6)) < 1e-9
    assert c["clearance_rate"] < 1.0
    assert c["example_session"] in {f"sess_{i}" for i in range(4)}

    # No phantom strategy=None bucket may pollute the candidates: the abandoned
    # episode's failures attribute to the named strategy, not a null row.
    assert all(x["winning_strategy"] is not None for x in cands)

    # the below-threshold ibex group must not appear
    assert all(x["family"] != "ibex" for x in cands)


def test_mine_fix_candidates_absent_table(tmp_knowledge_dir):
    """No fix_trajectories table -> empty list, existing behavior intact."""
    db_path = tmp_knowledge_dir / "knowledge.sqlite"
    conn = knowledge_db.connect(db_path)
    # Create ONLY the runs/failure_events tables, not fix_trajectories.
    conn.executescript(
        "CREATE TABLE runs (run_id TEXT PRIMARY KEY, design_name TEXT, "
        "design_family TEXT, platform TEXT, core_utilization REAL, "
        "place_density_lb_addon REAL, synth_hierarchical INTEGER, abc_area INTEGER);"
        "CREATE TABLE failure_events (run_id TEXT, stage TEXT, signature TEXT, "
        "detail TEXT);"
    )
    conn.commit()
    conn.close()

    out = tmp_knowledge_dir / "failure_candidates.json"
    data = mine_rules.mine(db_path, out)

    assert data["fix_candidates"] == []
    assert "candidates" in data
