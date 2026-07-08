"""Empirical-vs-static A/B arm for the fix-learning loop (eval_heuristics.py).

summarize_fix_arms() compares fix_trajectories tagged with two arms
("ranked" vs "static") on payoff = (iters-to-resolve, total_elapsed_s). The
arm tag lives INSIDE the trajectory's winning_config_json JSON string under
key "eval_arm" — there is NO eval_arm column on fix_trajectories. The ranked
arm that reaches outcome "resolved" in fewer iterations scores a "win".
"""
from __future__ import annotations

import json

import knowledge_db
import eval_heuristics


def _seed_trajectory(conn, *, session_id, family, check, eval_arm,
                     n_iters, outcome, total_elapsed_s,
                     violation_class="M3_ANTENNA", platform="nangate45"):
    """Insert one fix_trajectory whose arm tag is encoded in
    winning_config_json under 'eval_arm' (no DB column added)."""
    winning_config = json.dumps({"eval_arm": eval_arm,
                                 "CORE_UTILIZATION": "20"})
    conn.execute(
        "INSERT INTO fix_trajectories "
        "(fix_session_id, design_name, design_family, platform, check_type, "
        " violation_class, n_iters, outcome, winning_strategy, "
        " winning_config_json, total_elapsed_s) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (session_id, f"{family}_d", family, platform, check,
         violation_class, n_iters, outcome, "antenna_diode_repair",
         winning_config, total_elapsed_s),
    )
    conn.commit()


def test_summarize_fix_arms_ranked_wins_on_fewer_iters(tmp_path,
                                                       tmp_knowledge_dir):
    conn = knowledge_db.connect(tmp_knowledge_dir / "knowledge.sqlite")
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")

    # Same family/check; ranked resolves in fewer iterations than static.
    _seed_trajectory(conn, session_id="rk0", family="verilog_ethernet",
                     check="drc", eval_arm="ranked", n_iters=2,
                     outcome="resolved", total_elapsed_s=120.0)
    _seed_trajectory(conn, session_id="st0", family="verilog_ethernet",
                     check="drc", eval_arm="static", n_iters=5,
                     outcome="resolved", total_elapsed_s=300.0)

    out_path = tmp_path / "fix_eval_summary.json"
    summary = eval_heuristics.summarize_fix_arms(
        tmp_knowledge_dir / "knowledge.sqlite", out_path=out_path)

    # File emitted next to (here: at) the requested path.
    assert out_path.exists()
    on_disk = json.loads(out_path.read_text())
    assert on_disk == summary

    # The (family, check) pair scores the ranked arm a win.
    pairs = {(p["design_family"], p["check_type"]): p for p in summary["pairs"]}
    pair = pairs[("verilog_ethernet", "drc")]
    assert pair["winner"] == "ranked"
    assert pair["ranked_outcome"] == "resolved"
    assert pair["static_outcome"] == "resolved"
    assert pair["ranked_n_iters"] == 2
    assert pair["static_n_iters"] == 5

    # Roll-up: one ranked win, zero static wins.
    assert summary["n_ranked_wins"] == 1
    assert summary["n_static_wins"] == 0


def test_summarize_fix_arms_no_winner_when_arm_missing(tmp_path,
                                                       tmp_knowledge_dir):
    """A pair with only one arm present cannot be scored a win for either side."""
    conn = knowledge_db.connect(tmp_knowledge_dir / "knowledge.sqlite")
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")

    _seed_trajectory(conn, session_id="rk1", family="iccad", check="drc",
                     eval_arm="ranked", n_iters=2, outcome="resolved",
                     total_elapsed_s=100.0)

    out_path = tmp_path / "fix_eval_summary.json"
    summary = eval_heuristics.summarize_fix_arms(
        tmp_knowledge_dir / "knowledge.sqlite", out_path=out_path)

    pairs = {(p["design_family"], p["check_type"]): p for p in summary["pairs"]}
    pair = pairs[("iccad", "drc")]
    assert pair["winner"] is None
    assert summary["n_ranked_wins"] == 0
    assert summary["n_static_wins"] == 0
