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


# ── Win 2: variance-aware (LCB) promotion ────────────────────────────────────

def test_lcb_penalizes_variance():
    """The lower-confidence bound (mean − z·stderr) discounts a high-variance
    sample: a lower-mean/zero-variance arm beats a higher-mean/high-variance one."""
    high_var = ab_runner.lcb([1.0, 1.0, 0.0, 0.0], z=1.0)   # mean .5, big spread
    steady = ab_runner.lcb([0.4, 0.4, 0.4, 0.4], z=1.0)     # mean .4, zero spread
    assert steady > high_var


def test_lcb_single_sample_is_mean():
    assert ab_runner.lcb([0.7], z=1.0) == 0.7
    assert ab_runner.lcb([], z=1.0) == 0.0


def test_judge_repeated_prefers_reliable_arm_over_flaky(tmp_path):
    a = [{"is_success": True, "wall_s": 100.0},
         {"is_success": False, "wall_s": 100.0}]    # flaky 0.5
    b = [{"is_success": True, "wall_s": 100.0},
         {"is_success": True, "wall_s": 100.0}]      # reliable 1.0
    assert ab_runner.judge_repeated(a, b) == "win"


def test_judge_repeated_high_variance_b_loses_to_steady_a():
    """The documented LVS-crash heisenbug: one lucky win is not evidence. A flaky
    arm B must LOSE to a steady arm A under the LCB even with the same max."""
    a = [{"is_success": True} for _ in range(4)]              # steady clean
    b = [{"is_success": True}, {"is_success": True},
         {"is_success": False}, {"is_success": False}]        # flaky 0.5
    assert ab_runner.judge_repeated(a, b) == "loss"


def test_judge_repeated_never_promotes_non_clean_b():
    a = [{"is_success": False}, {"is_success": False}]
    b = [{"is_success": False}, {"is_success": False}]
    assert ab_runner.judge_repeated(a, b) == "inconclusive"


def test_judge_repeated_k1_matches_binary_judge(tmp_path):
    # k=1 degrades to the single-run binary verdict: B clean where A is not -> win.
    assert ab_runner.judge_repeated([{"is_success": False}],
                                    [{"is_success": True}]) == "win"


def test_verdict_journaled(tmp_path, monkeypatch):
    """Tier B2: record_trial journals a promote (win) / demote (loss) action into
    the journal DB, carrying symptom_id + trial_id. Best-effort + advisory: the
    knowledge-side recipe_status stays the source of truth for the verdict."""
    import journal_db
    jdb = tmp_path / "journal.sqlite"
    monkeypatch.setenv("R2G_JOURNAL_DB", str(jdb))
    conn = _conn(tmp_path)
    recipe_lifecycle.stage_shadow(conn, provenance="test", **KEY)
    tid = ab_runner.record_trial(conn, key=KEY, verdict="win", arm_a_run_id="ra",
                                 arm_b_run_id="rb", metrics={"lcb": 0.8})
    jc = journal_db.connect(jdb)
    row = jc.execute("SELECT action_type, symptom_id, "
                     "json_extract(payload_json,'$.trial_id'), "
                     "json_extract(payload_json,'$.strategy') FROM actions").fetchone()
    assert row[0] == "promote"
    assert row[1] == KEY["symptom_id"]
    assert row[2] == tid
    assert row[3] == KEY["strategy"]
    # Journaling follows the corpus TRANSITION (2026-06-24 L1-02): after the win (1w0l
    # -> promoted) a single loss is 1w1l (tie) -> status UNCHANGED -> no demote journal;
    # a SECOND loss is 1w2l (net-negative) -> shadow -> demote journaled.
    ab_runner.record_trial(conn, key=KEY, verdict="loss", arm_a_run_id="ra",
                           arm_b_run_id="rb", metrics={})
    assert [r[0] for r in jc.execute(
        "SELECT action_type FROM actions ORDER BY action_id").fetchall()] == ["promote"]
    ab_runner.record_trial(conn, key=KEY, verdict="loss", arm_a_run_id="ra",
                           arm_b_run_id="rb", metrics={})
    types = [r[0] for r in jc.execute(
        "SELECT action_type FROM actions ORDER BY action_id").fetchall()]
    assert types == ["promote", "demote"]


def test_verdict_journal_disabled_is_silent(tmp_path, monkeypatch):
    """R2G_JOURNAL=0 silences the new promote/demote write without breaking the
    knowledge-side promotion (acceptance #5)."""
    monkeypatch.setenv("R2G_JOURNAL", "0")
    jdb = tmp_path / "journal.sqlite"
    monkeypatch.setenv("R2G_JOURNAL_DB", str(jdb))
    conn = _conn(tmp_path)
    recipe_lifecycle.stage_shadow(conn, provenance="test", **KEY)
    ab_runner.record_trial(conn, key=KEY, verdict="win", arm_a_run_id="ra",
                           arm_b_run_id="rb", metrics={})
    assert recipe_lifecycle.get_status(conn, **KEY) == "promoted"   # knowledge unaffected
    assert not jdb.exists()                                          # nothing journaled


def test_repeats_default_is_two():
    import os
    os.environ.pop("R2G_AB_REPEATS", None)
    assert ab_runner.ab_repeats() == 2
    os.environ["R2G_AB_REPEATS"] = "3"
    try:
        assert ab_runner.ab_repeats() == 3
    finally:
        os.environ.pop("R2G_AB_REPEATS", None)


# ── 2026-06-24 loop-closure: inconclusive is non-terminal + corpus aggregation ──

def test_inconclusive_does_not_demote_candidate(tmp_path):
    """Bug #2 (2026-06-24): an `inconclusive` verdict carries NO information and must
    NOT demote a candidate to terminal `shadow` — the recipe stays 'candidate' so the
    next drain re-plans it. (Before: record_trial demoted on every non-win, burying a
    recipe on a single noisy/inert trial with no re-enqueue path.)"""
    conn = _conn(tmp_path)
    recipe_lifecycle.enqueue_candidate(conn, **KEY)
    assert recipe_lifecycle.get_status(conn, **KEY) == "candidate"
    ab_runner.record_trial(conn, key=KEY, verdict="inconclusive",
                           arm_a_run_id="ra", arm_b_run_id="rb", metrics={})
    assert recipe_lifecycle.get_status(conn, **KEY) == "candidate"


def test_late_loss_does_not_bury_net_winner(tmp_path):
    """Bug #5 (2026-06-24): recipe_status reflects the FULL trial corpus, not the LAST
    trial. Two wins then a single loss stays `promoted` (net +1 decisive) — the old
    last-trial UPSERT would have demoted it to shadow on the trailing loss."""
    conn = _conn(tmp_path)
    recipe_lifecycle.enqueue_candidate(conn, **KEY)
    for v in ("win", "win", "loss"):
        ab_runner.record_trial(conn, key=KEY, verdict=v, arm_a_run_id="ra",
                               arm_b_run_id="rb", metrics={})
    assert recipe_lifecycle.get_status(conn, **KEY) == "promoted"


def test_corpus_net_loss_demotes(tmp_path):
    """Net-negative decisive evidence demotes to shadow (a genuinely-losing recipe
    rests); a tie leaves the prior status."""
    conn = _conn(tmp_path)
    recipe_lifecycle.enqueue_candidate(conn, **KEY)
    for v in ("loss", "win", "loss"):          # net -1
        ab_runner.record_trial(conn, key=KEY, verdict=v, arm_a_run_id="ra",
                               arm_b_run_id="rb", metrics={})
    assert recipe_lifecycle.get_status(conn, **KEY) == "shadow"


def test_later_win_revives_shadow(tmp_path):
    """Bug #2/#5: a demoted recipe is NOT terminal — a later win that makes the corpus
    net-positive flips shadow back to promoted (one bad trial cannot bury it forever)."""
    conn = _conn(tmp_path)
    recipe_lifecycle.enqueue_candidate(conn, **KEY)
    ab_runner.record_trial(conn, key=KEY, verdict="loss", arm_a_run_id="ra",
                           arm_b_run_id="rb", metrics={})
    assert recipe_lifecycle.get_status(conn, **KEY) == "shadow"
    ab_runner.record_trial(conn, key=KEY, verdict="win", arm_a_run_id="ra",
                           arm_b_run_id="rb", metrics={})        # 1w1l tie
    assert recipe_lifecycle.get_status(conn, **KEY) == "shadow"
    ab_runner.record_trial(conn, key=KEY, verdict="win", arm_a_run_id="ra",
                           arm_b_run_id="rb", metrics={})        # 2w1l net +1
    assert recipe_lifecycle.get_status(conn, **KEY) == "promoted"


def test_jitter_cost_tie_is_inconclusive():
    """Bug #4 (2026-06-24): two all-success arms differing only by ~3% wall-clock
    JITTER is noise, not a win — the lone nangate45 antenna promotion (trial 26) rested
    on exactly this 101/102 vs 98/101 s 'win'. Must return inconclusive."""
    a = [{"is_success": True, "wall_s": 101.0}, {"is_success": True, "wall_s": 102.0}]
    b = [{"is_success": True, "wall_s": 98.0}, {"is_success": True, "wall_s": 101.0}]
    assert ab_runner.judge_repeated(a, b) == "inconclusive"


def test_deterministic_large_cost_win_preserved():
    """Bug #4 must NOT regress a REAL large-delta cost win, nor the documented
    se==0-is-maximal-confidence invariant (route_relief 37s vs 5400s)."""
    a = [{"is_success": True, "wall_s": 5400.0}, {"is_success": True, "wall_s": 5410.0}]
    b = [{"is_success": True, "wall_s": 37.0}, {"is_success": True, "wall_s": 38.0}]
    assert ab_runner.judge_repeated(a, b) == "win"
    a2 = [{"is_success": True, "wall_s": 100.0}, {"is_success": True, "wall_s": 100.0}]
    b2 = [{"is_success": True, "wall_s": 50.0}, {"is_success": True, "wall_s": 50.0}]
    assert ab_runner.judge_repeated(a2, b2) == "win"          # se==0 deterministic delta
