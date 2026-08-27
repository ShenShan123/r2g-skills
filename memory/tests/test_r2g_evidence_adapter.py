"""R2G real-evidence capture adapter (design doc 20.4, 21.2).

Reads a real R2G project dir (``reports/*.json`` + ``config.mk`` +
``fix_log.jsonl`` in the exact ``fix_signoff.sh _log_iter`` schema) and emits one
verified transition per fix iteration, accumulated into a repair episode graph.
"""
from __future__ import annotations

import json
from pathlib import Path

from tehm.adapters.r2g_evidence import (
    build_execution_records,
    capture_r2g_project,
    collect_execution_evidence,
    parse_config_mk,
)
from tehm.canonical.capture import ExecutionRecord

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ANTENNA_PROJ = FIXTURES / "project_antenna_fix"


def _head_episode(conn) -> dict:
    row = conn.execute(
        """SELECT episode_id, terminal_status, trajectory_summary_json
           FROM tehm_episodes
           ORDER BY (SELECT COUNT(*) FROM tehm_episode_steps s
                     WHERE s.episode_id = tehm_episodes.episode_id) DESC
           LIMIT 1""").fetchone()
    return {"episode_id": row["episode_id"], "terminal_status": row["terminal_status"],
            "summary": json.loads(row["trajectory_summary_json"])}


# -- evidence collection ------------------------------------------------------

def test_parse_config_mk():
    cfg = parse_config_mk(
        "# comment\nexport DESIGN_NAME = aes128\nexport PLATFORM=sky130hd\n"
        "not_export = ignored\n")
    assert cfg == {"DESIGN_NAME": "aes128", "PLATFORM": "sky130hd"}


def test_collect_evidence_real_format():
    ev = collect_execution_evidence(ANTENNA_PROJ)
    assert ev["config"]["DESIGN_NAME"] == "antenna_demo"
    assert ev["config"]["PLATFORM"] == "sky130hd"
    assert "drc" in ev["reports"] and "lvs" in ev["reports"]
    assert len(ev["fix_log"]) == 3


def test_fix_log_rows_match_real_schema():
    ev = collect_execution_evidence(ANTENNA_PROJ)
    row = ev["fix_log"][0]
    for key in ("check", "iter", "strategy", "before", "after", "verdict",
                "fix_session_id", "violation_class", "after_status",
                "config_delta", "predicates", "global_regressions", "ts"):
        assert key in row, key
    assert isinstance(row["predicates"], dict)   # real _log_iter shape
    assert isinstance(row["global_regressions"], list)


# -- ExecutionRecord construction ----------------------------------------------

def test_build_records_one_per_iteration():
    ev = collect_execution_evidence(ANTENNA_PROJ)
    records = build_execution_records(ev)
    assert len(records) == 3
    for i, record in enumerate(records):
        assert isinstance(record, ExecutionRecord)
        assert record.action["transformation_family"].isupper()
        assert record.episode["episode_id"] == "fix_ant_20260731_001"
        assert record.episode["step_index"] == i


def test_build_records_verdicts_honest():
    ev = collect_execution_evidence(ANTENNA_PROJ)
    records = build_execution_records(ev)
    # iter 0/1 = partial improvement (applied) -> UNKNOWN verdict, PRESENT failure
    assert records[0].verification["verdict"] == "UNKNOWN"
    assert records[0].observation_delta["original_failure"] == "PRESENT"
    assert records[0].observation_delta["first_divergence"] == {"before": 7, "after": 3}
    # iter 2 = cleared -> PASS, failure REMOVED
    assert records[2].verification["verdict"] == "PASS"
    assert records[2].observation_delta["original_failure"] == "REMOVED"


def test_build_records_config_accumulates():
    ev = collect_execution_evidence(ANTENNA_PROJ)
    records = build_execution_records(ev)
    assert records[0].before["config"]["PLACE_DENSITY_LB_ADDON"] == "0.10"  # base
    assert records[1].before["config"]["PLACE_DENSITY_LB_ADDON"] == "0.14"  # after iter0
    assert records[2].after["config"]["ROUTE_DENSITY_LAYER_ADDON"] == "0.10"


# -- capture ------------------------------------------------------------------

def test_capture_r2g_project_accumulates_episode(tmp_tehm):
    conn, store, _ = tmp_tehm
    receipts = capture_r2g_project(conn, store, ANTENNA_PROJ)
    assert len(receipts) == 3
    assert conn.execute("SELECT COUNT(*) FROM tehm_transitions").fetchone()[0] == 3
    head = _head_episode(conn)
    assert head["terminal_status"] == "VERIFIED_REPAIR"
    assert head["summary"] == {"steps": 3, "positive_transitions": 1,
                               "neutral_transitions": 2, "harmful_transitions": 0,
                               "oracle_calls": None}


def test_capture_r2g_project_idempotent(tmp_tehm):
    conn, store, _ = tmp_tehm
    capture_r2g_project(conn, store, ANTENNA_PROJ)
    first = [tuple(r) for r in conn.execute(
        "SELECT transition_id FROM tehm_transitions ORDER BY transition_id")]
    capture_r2g_project(conn, store, ANTENNA_PROJ)
    second = [tuple(r) for r in conn.execute(
        "SELECT transition_id FROM tehm_transitions ORDER BY transition_id")]
    assert first == second
    assert conn.execute("SELECT COUNT(*) FROM tehm_transitions").fetchone()[0] == 3


def test_capture_r2g_clean_run_yields_no_transitions(tmp_tehm):
    """A project without fix_log.jsonl (e.g. a clean run) produces nothing."""
    conn, store, _ = tmp_tehm
    clean = FIXTURES / "project_clean_run"
    receipts = capture_r2g_project(conn, store, clean)
    assert receipts == []
    assert conn.execute("SELECT COUNT(*) FROM tehm_transitions").fetchone()[0] == 0


def test_tehm_honesty_green_after_real_capture(tmp_tehm):
    conn, store, _ = tmp_tehm
    capture_r2g_project(conn, store, ANTENNA_PROJ)
    from tehm import honesty

    all_ok, report = honesty.run_all(conn, store, tmp_tehm[2] / "tehm.sqlite")
    assert all_ok, report
