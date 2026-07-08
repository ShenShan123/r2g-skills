"""2026-07-04 judge v2: symptom-target metric + reason codes + planner suppression.

Before this, DRC/LVS signoff A/B arms were judged on the whole-run
knowledge_db.is_success, which ties both arms whenever an UNRELATED residual
keeps the run non-clean — 193/228 ab_trials inconclusive (antenna_diode_repair
0-decisive-in-93), 38 candidates capped dead, no reason recorded anywhere.
These tests lock:
  - judge_repeated_ex returns a queryable reason code (judge_repeated unchanged);
  - a signoff arm with a DRC symptom is judged on ITS class clearing, not
    whole-run success (the timing/synth metric-granularity lesson generalized);
  - the inconclusive re-plan cap counts only judge-v2 trials (pre-v2 verdicts
    were blind to the target symptom and must not permanently bar a candidate);
  - non-divergent strategies are refused at enqueue time and parked out of the
    candidate queue (previously 4 rows were re-skipped every drain, forever).
"""
import json

import ab_runner
import engineer_loop
import knowledge_db
import recipe_lifecycle


def _conn(tmp_path):
    c = knowledge_db.connect(tmp_path / "knowledge.sqlite")
    knowledge_db.ensure_schema(c)
    return c


# ── judge_repeated_ex reason codes ───────────────────────────────────────────

def test_both_arms_never_succeed_reason():
    a = [{"is_success": False, "wall_s": 70.0}]
    b = [{"is_success": False, "wall_s": 75.0}]
    assert ab_runner.judge_repeated_ex(a, b) == (
        "inconclusive", "both_arms_never_succeed")
    assert ab_runner.judge_repeated(a, b) == "inconclusive"   # wrapper unchanged


def test_arm_no_samples_reason():
    assert ab_runner.judge_repeated_ex([None], [{"is_success": True}]) == (
        "inconclusive", "arm_no_samples")


def test_b_never_succeeds_is_loss():
    a = [{"is_success": True}]
    b = [{"is_success": False}]
    assert ab_runner.judge_repeated_ex(a, b) == ("loss", "b_never_succeeds")


def test_success_lcb_delta_win():
    a = [{"is_success": False}, {"is_success": False}]
    b = [{"is_success": True}, {"is_success": True}]
    assert ab_runner.judge_repeated_ex(a, b) == ("win", "success_lcb_delta")


def test_success_tie_single_repeat_reason():
    a = [{"is_success": True, "wall_s": 100.0}]
    b = [{"is_success": True, "wall_s": 50.0}]
    # k=1: no variance estimate -> a cost-only difference stays inconclusive.
    assert ab_runner.judge_repeated_ex(a, b) == (
        "inconclusive", "success_tie_insufficient_repeats")


def test_success_tie_cost_within_noise_reason():
    a = [{"is_success": True, "wall_s": 100.0}, {"is_success": True, "wall_s": 101.0}]
    b = [{"is_success": True, "wall_s": 99.0}, {"is_success": True, "wall_s": 100.5}]
    assert ab_runner.judge_repeated_ex(a, b) == (
        "inconclusive", "success_tie_cost_within_noise")


def test_cost_tiebreak_win_reason():
    a = [{"is_success": True, "wall_s": 5400.0}, {"is_success": True, "wall_s": 5300.0}]
    b = [{"is_success": True, "wall_s": 37.0}, {"is_success": True, "wall_s": 40.0}]
    assert ab_runner.judge_repeated_ex(a, b) == ("win", "cost_tiebreak")


# ── symptom-target metric for signoff arms ───────────────────────────────────

def _insert_run(conn, run_id, project_path, *, drc_status=None, lvs_status=None,
                orfs_status="pass", elapsed=100.0):
    conn.execute(
        "INSERT INTO runs (run_id, project_path, ingested_at, platform, "
        "orfs_status, drc_status, lvs_status, total_elapsed_s) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (run_id, project_path, "2026-07-04T00:00:00Z", "sky130hd",
         orfs_status, drc_status, lvs_status, elapsed))
    conn.commit()


def _insert_rv(conn, run_id, categories):
    conn.execute(
        "INSERT INTO run_violations (run_id, platform, drc_status, "
        "drc_categories_json) VALUES (?,?,?,?)",
        (run_id, "sky130hd", "fail", json.dumps(categories)))
    conn.commit()


def test_symptom_target_resolves_drc_and_skips_backend(tmp_path):
    conn = _conn(tmp_path)
    conn.execute("INSERT INTO symptoms (symptom_id, check_type, class) "
                 "VALUES ('sdrc','drc','METAL5_ANTENNA')")
    conn.execute("INSERT INTO symptoms (symptom_id, check_type, class) "
                 "VALUES ('sroute','orfs_stage','route')")
    conn.commit()
    assert engineer_loop._symptom_target(conn, "sdrc") == ("drc", "METAL5_ANTENNA")
    assert engineer_loop._symptom_target(conn, "sroute") is None
    assert engineer_loop._symptom_target(conn, "missing") is None
    assert engineer_loop._symptom_target(None, "sdrc") is None


def test_drc_arm_success_when_target_class_cleared_despite_other_residual(tmp_path):
    """The 93-trial antenna block: arm clears METAL5_ANTENNA but an unrelated
    density residual keeps drc_status='fail' -> old judge tied both arms; the v2
    target metric must call the antenna symptom CLEARED."""
    conn = _conn(tmp_path)
    _insert_run(conn, "r1", "/p/armB", drc_status="fail", lvs_status="clean")
    _insert_rv(conn, "r1", {"density": {"count": 3}})     # antenna class absent
    m = engineer_loop._arm_metric(conn, "/p/armB",
                                  target=("drc", "METAL5_ANTENNA"))
    assert m["is_success"] is True
    assert m["judged_on"] == "symptom:drc:METAL5_ANTENNA"


def test_drc_arm_failure_when_target_class_remains(tmp_path):
    conn = _conn(tmp_path)
    _insert_run(conn, "r2", "/p/armA", drc_status="fail", lvs_status="clean")
    _insert_rv(conn, "r2", {"METAL5_ANTENNA": {"count": 2}})
    m = engineer_loop._arm_metric(conn, "/p/armA",
                                  target=("drc", "METAL5_ANTENNA"))
    assert m["is_success"] is False


def test_drc_arm_quoted_residual_class_still_matches_target(tmp_path):
    """Residual snapshots written before extract_drc normalization carry quoted
    classes ("'m3.2'"); the target comparison must normalize both sides."""
    conn = _conn(tmp_path)
    _insert_run(conn, "r3", "/p/armQ", drc_status="fail")
    _insert_rv(conn, "r3", {"'m3.2'": {"count": 1}})
    m = engineer_loop._arm_metric(conn, "/p/armQ", target=("drc", "m3.2"))
    assert m["is_success"] is False


def test_drc_arm_stuck_never_demonstrates_clearing(tmp_path):
    conn = _conn(tmp_path)
    _insert_run(conn, "r4", "/p/armS", drc_status="stuck")
    m = engineer_loop._arm_metric(conn, "/p/armS",
                                  target=("drc", "METAL5_ANTENNA"))
    assert m["is_success"] is False


def test_drc_arm_clean_is_cleared(tmp_path):
    conn = _conn(tmp_path)
    _insert_run(conn, "r5", "/p/armC", drc_status="clean_beol")
    m = engineer_loop._arm_metric(conn, "/p/armC",
                                  target=("drc", "METAL5_ANTENNA"))
    assert m["is_success"] is True


def test_lvs_arm_target_requires_clean(tmp_path):
    conn = _conn(tmp_path)
    _insert_run(conn, "r6", "/p/armL1", lvs_status="clean", drc_status="fail")
    _insert_run(conn, "r7", "/p/armL2", lvs_status="mismatch", drc_status="clean")
    ok = engineer_loop._arm_metric(conn, "/p/armL1", target=("lvs", "top_pin_mismatch"))
    bad = engineer_loop._arm_metric(conn, "/p/armL2", target=("lvs", "top_pin_mismatch"))
    assert ok["is_success"] is True and bad["is_success"] is False


def test_no_target_falls_back_to_is_success(tmp_path):
    """Backend-abort/unknown-symptom arms keep the legacy whole-run judgment."""
    conn = _conn(tmp_path)
    _insert_run(conn, "r8", "/p/armF", orfs_status="fail",
                drc_status=None, lvs_status=None)
    m = engineer_loop._arm_metric(conn, "/p/armF", target=None)
    assert m["is_success"] is False and m["judged_on"] == "signoff"


# ── judge_finished_trials records judge_version 2 + reason + target ─────────

def test_judge_finished_trials_records_v2_metadata(tmp_path):
    conn = _conn(tmp_path)
    conn.execute("INSERT INTO symptoms (symptom_id, check_type, class) "
                 "VALUES ('sant','drc','METAL5_ANTENNA')")
    conn.commit()
    # Arm A keeps the antenna residual; arm B clears it (unrelated density stays).
    pa, pb = str(tmp_path / "d_abA_ant_0"), str(tmp_path / "d_abB_ant_0")
    _insert_run(conn, "ra", pa, drc_status="fail", lvs_status="clean")
    _insert_rv(conn, "ra", {"METAL5_ANTENNA": {"count": 3}})
    _insert_run(conn, "rb", pb, drc_status="fail", lvs_status="clean")
    _insert_rv(conn, "rb", {"density": {"count": 1}})
    led = engineer_loop.Ledger(tmp_path / "l.jsonl")
    key = {"symptom_id": "sant", "design_class": "misc/small",
           "platform": "sky130hd", "strategy": "antenna_diode_repair"}
    for arm, pp in (("A", pa), ("B", pb)):
        led.add({"design": f"d_ab{arm}_ant_0", "project_path": pp,
                 "platform": "sky130hd", "kind": "ab_arm", "arm": arm,
                 "strategy": "antenna_diode_repair", "repeat": 0,
                 "check": "both", "ab_key": key, "match_level": "exact"})
        led.set_state(f"d_ab{arm}_ant_0", "clean")
    engineer_loop.judge_finished_trials(led, conn)
    row = conn.execute(
        "SELECT verdict, metrics_json FROM ab_trials").fetchone()
    assert row is not None
    verdict, mj = row
    m = json.loads(mj)
    assert verdict == "win"                       # B cleared its symptom, A did not
    assert m["judge_version"] == 2
    assert m["reason"] == "success_lcb_delta"
    assert m["target"] == {"check": "drc", "class": "METAL5_ANTENNA"}
    assert m["A_samples"][0]["judged_on"] == "symptom:drc:METAL5_ANTENNA"


# ── coverage cap counts only v2 inconclusives ────────────────────────────────

def _trial(conn, key, verdict, metrics):
    conn.execute(
        "INSERT INTO ab_trials (symptom_id, design_class, platform, strategy, "
        "verdict, metrics_json, ts) VALUES (?,?,?,?,?,?,?)",
        (key["symptom_id"], key["design_class"], key["platform"],
         key["strategy"], verdict, json.dumps(metrics), "2026-07-04T00:00:00Z"))
    conn.commit()


def test_coverage_gap_ignores_pre_v2_inconclusives(tmp_path):
    conn = _conn(tmp_path)
    key = {"symptom_id": "s1", "design_class": "c/small",
           "platform": "sky130hd", "strategy": "antenna_diode_repair"}
    for _ in range(4):                       # pre-v2: judged blind to the symptom
        _trial(conn, key, "inconclusive", {"A_samples": [], "B_samples": []})
    assert engineer_loop._ab_coverage_gap(conn, key) is False   # re-plannable now


def test_coverage_gap_caps_on_v2_inconclusives(tmp_path):
    conn = _conn(tmp_path)
    key = {"symptom_id": "s2", "design_class": "c/small",
           "platform": "sky130hd", "strategy": "antenna_diode_repair"}
    for _ in range(engineer_loop.AB_INCONCLUSIVE_MAX):
        _trial(conn, key, "inconclusive",
               {"judge_version": 2, "reason": "both_arms_never_succeed"})
    assert engineer_loop._ab_coverage_gap(conn, key) is True


def test_coverage_gap_decisive_any_era_unblocks(tmp_path):
    conn = _conn(tmp_path)
    key = {"symptom_id": "s3", "design_class": "c/small",
           "platform": "sky130hd", "strategy": "antenna_diode_repair"}
    for _ in range(5):
        _trial(conn, key, "inconclusive", {"judge_version": 2, "reason": "x"})
    _trial(conn, key, "win", {})             # decisive evidence: never capped
    assert engineer_loop._ab_coverage_gap(conn, key) is False


# ── non-divergent strategies: refused at enqueue, parked from the queue ─────

def test_enqueue_candidate_refuses_nondivergent(tmp_path):
    conn = _conn(tmp_path)
    assert recipe_lifecycle.enqueue_candidate(
        conn, symptom_id="s4", design_class="c/small", platform="sky130hd",
        strategy="lvs_resolve_unknown") is False
    assert recipe_lifecycle.pending_candidates(conn) == []


def test_diff_and_enqueue_refuses_nondivergent(tmp_path):
    conn = _conn(tmp_path)
    heur = {"generation": 1, "recipes": {"s5": {"c/small": {"sky130hd": {
        "strategies": {"lvs_resolve_unknown": {"attempts": 3, "successes": 1},
                       "antenna_diode_repair": {"attempts": 2, "successes": 2}}}}}}}
    enq = recipe_lifecycle.diff_and_enqueue(conn, heur, prev=None)
    strategies = {k[3] for k in enq}
    assert strategies == {"antenna_diode_repair"}


def test_park_nondivergent_heals_legacy_rows(tmp_path):
    conn = _conn(tmp_path)
    conn.execute(
        "INSERT INTO recipe_status (symptom_id, design_class, platform, strategy,"
        " status, provenance, updated_at) VALUES "
        "('s6','c/small','sky130hd','lvs_resolve_unknown','candidate','x','t')")
    conn.execute(
        "INSERT INTO recipe_status (symptom_id, design_class, platform, strategy,"
        " status, provenance, updated_at) VALUES "
        "('s7','c/small','sky130hd','antenna_diode_repair','candidate','x','t')")
    conn.commit()
    assert recipe_lifecycle.park_nondivergent(conn) == 1
    rows = dict(conn.execute(
        "SELECT strategy, status FROM recipe_status").fetchall())
    assert rows == {"lvs_resolve_unknown": "parked",
                    "antenna_diode_repair": "candidate"}
    # Parked rows leave the work queue; a real candidate stays.
    assert [k["strategy"] for k in recipe_lifecycle.pending_candidates(conn)] == \
        ["antenna_diode_repair"]
    # Idempotent.
    assert recipe_lifecycle.park_nondivergent(conn) == 0
