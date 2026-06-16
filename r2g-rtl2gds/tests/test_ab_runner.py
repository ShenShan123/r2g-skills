"""Inline recipe A/B (spec §5.4): match, plan arms, judge honestly, promote."""
import json

import ab_runner
import knowledge_db
import recipe_lifecycle

KEY = dict(symptom_id="deadbeef00000001", design_class="crypto/small",
           platform="nangate45", strategy="antenna_diode_repair")


def _conn(tmp_path):
    c = knowledge_db.connect(tmp_path / "knowledge.sqlite")
    knowledge_db.ensure_schema(c)
    return c


def _seed_history(conn, n=3, design_class="crypto/small", platform="nangate45"):
    """run_violations rows whose symptom matches KEY, attached to small runs."""
    for i in range(n):
        rid = f"r{i}"
        conn.execute(
            "INSERT OR REPLACE INTO runs (run_id, project_path, design_name, "
            "platform, ingested_at, cell_count, design_class) "
            "VALUES (?,?,?,?,?,?,?)",
            (rid, f"/p/d{i}", f"d{i}", platform, "2026-06-10T00:00:00Z",
             1000 + i, design_class))
        conn.execute(
            "INSERT OR REPLACE INTO run_violations (run_id, platform, "
            "drc_status, symptom_id, snapshot_ts) VALUES (?,?,?,?,?)",
            (rid, platform, "fail", KEY["symptom_id"], "2026-06-10T00:00:00Z"))
    conn.commit()


def test_plan_trial_selects_cheapest_matched_designs(tmp_path):
    conn = _conn(tmp_path)
    _seed_history(conn)
    trial = ab_runner.plan_trial(conn, **KEY, n_designs=2)
    assert [d["design_name"] for d in trial["designs"]] == ["d0", "d1"]
    assert trial["arm_a"]["exclude_strategy"] == KEY["strategy"]
    assert trial["arm_b"]["rank_first_strategy"] == KEY["strategy"]


def test_plan_trial_relaxes_class_when_exact_too_few(tmp_path):
    conn = _conn(tmp_path)
    _seed_history(conn, n=1, design_class="crypto/small")
    _seed_history_other = _seed_history(conn, n=2, design_class="logic/medium")
    trial = ab_runner.plan_trial(conn, **KEY, n_designs=2)
    assert trial["match_level"] == "pooled_class"
    assert len(trial["designs"]) == 2


def test_judge_win_promotes(tmp_path):
    conn = _conn(tmp_path)
    recipe_lifecycle.stage_shadow(conn, provenance="test", **KEY)
    arm_a = {"is_success": False, "wall_s": 900.0, "fix_iters": None}
    arm_b = {"is_success": True, "wall_s": 600.0, "fix_iters": 2}
    verdict = ab_runner.judge(arm_a, arm_b)
    assert verdict == "win"
    tid = ab_runner.record_trial(conn, key=KEY, verdict=verdict,
                                 arm_a_run_id="ra", arm_b_run_id="rb",
                                 metrics={"a": arm_a, "b": arm_b})
    assert recipe_lifecycle.get_status(conn, **KEY) == "promoted"
    row = conn.execute("SELECT verdict FROM ab_trials WHERE trial_id=?",
                       (tid,)).fetchone()
    assert row[0] == "win"


def test_judge_both_fail_is_inconclusive_never_win(tmp_path):
    arm_a = {"is_success": False, "wall_s": 900.0, "fix_iters": None}
    arm_b = {"is_success": False, "wall_s": 100.0, "fix_iters": None}
    assert ab_runner.judge(arm_a, arm_b) == "inconclusive"


def test_judge_crash_arm_is_inconclusive(tmp_path):
    assert ab_runner.judge(None, {"is_success": True, "wall_s": 1.0,
                                  "fix_iters": 0}) == "inconclusive"


def test_outcome_score_never_promotes_a_non_clean_arm(tmp_path):
    """Win 1 invariant H4: outcome_score is an ordering HINT only. Two non-clean
    arms — even with a clearly better outcome_score on B — must stay 'inconclusive'
    and never promote (promotion still requires a clean arm). is_success is the
    sole authority for a 'win'."""
    conn = _conn(tmp_path)
    recipe_lifecycle.stage_shadow(conn, provenance="test", **KEY)
    arm_a = {"is_success": False, "wall_s": 900.0, "fix_iters": None,
             "outcome_score": 0.30}
    arm_b = {"is_success": False, "wall_s": 100.0, "fix_iters": None,
             "outcome_score": 0.95}        # much "better" but still non-clean
    verdict = ab_runner.judge(arm_a, arm_b)
    assert verdict == "inconclusive"
    ab_runner.record_trial(conn, key=KEY, verdict=verdict, arm_a_run_id="ra",
                           arm_b_run_id="rb", metrics={"a": arm_a, "b": arm_b})
    # inconclusive -> reverts to shadow, NEVER promoted.
    assert recipe_lifecycle.get_status(conn, **KEY) == "shadow"


def test_loss_reverts_candidate_to_shadow(tmp_path):
    conn = _conn(tmp_path)
    recipe_lifecycle.stage_shadow(conn, provenance="test", **KEY)
    ab_runner.record_trial(conn, key=KEY, verdict="loss", arm_a_run_id="ra",
                           arm_b_run_id="rb", metrics={})
    assert recipe_lifecycle.get_status(conn, **KEY) == "shadow"
