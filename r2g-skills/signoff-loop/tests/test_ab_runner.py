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
    # Seed the synthetic arm run_ids the record_trial tests reuse so they are REAL
    # rows in `runs` (P0-10, 2026-07-15: a decisive trial's run_ids must resolve to
    # actually-ingested runs). ra/rb are the two arms of ONE base subject 'subjX'
    # (P1-11 subject key strips the _ab[AB]_ suffix), so repeated ra/rb trials
    # correctly aggregate as one independent subject.
    for rid, base in (("ra", "subjX_abA_antenna__0"), ("rb", "subjX_abB_antenna__0"),
                      ("rx", "subjY_abA_antenna__0")):
        c.execute(
            "INSERT OR REPLACE INTO runs (run_id, project_path, design_name, "
            "platform, ingested_at, cell_count) VALUES (?,?,?,?,?,?)",
            (rid, str(tmp_path / base), "subjX", KEY["platform"],
             "2026-06-10T00:00:00Z", 1000))
    c.commit()
    return c


def _seed_history(conn, base, n=3, design_class="crypto/small", platform="nangate45"):
    """run_violations rows whose symptom matches KEY, attached to small runs.
    Subject dirs are REAL (created under `base`): plan_trial Tier 1 isdir-filters
    subjects since 2026-07-03 (wiped-round ghost dirs must never become arms)."""
    for i in range(n):
        rid = f"r{i}_{design_class.replace('/', '_')}"
        proj = base / f"d{i}_{design_class.replace('/', '_')}"
        proj.mkdir(exist_ok=True)
        conn.execute(
            "INSERT OR REPLACE INTO runs (run_id, project_path, design_name, "
            "platform, ingested_at, cell_count, design_class) "
            "VALUES (?,?,?,?,?,?,?)",
            (rid, str(proj), f"d{i}", platform, "2026-06-10T00:00:00Z",
             1000 + i, design_class))
        conn.execute(
            "INSERT OR REPLACE INTO run_violations (run_id, platform, "
            "drc_status, symptom_id, snapshot_ts) VALUES (?,?,?,?,?)",
            (rid, platform, "fail", KEY["symptom_id"], "2026-06-10T00:00:00Z"))
    conn.commit()


def test_plan_trial_selects_cheapest_matched_designs(tmp_path):
    conn = _conn(tmp_path)
    _seed_history(conn, tmp_path)
    trial = ab_runner.plan_trial(conn, **KEY, n_designs=2)
    assert [d["design_name"] for d in trial["designs"]] == ["d0", "d1"]
    assert trial["arm_a"]["exclude_strategy"] == KEY["strategy"]
    assert trial["arm_b"]["rank_first_strategy"] == KEY["strategy"]


def test_plan_trial_never_crosses_platforms(tmp_path):
    """2026-06-25: an A/B arm flows at the RECIPE's platform, so a nangate45 recipe must
    NEVER get sky130hd subjects. The old pooled_platform/None tiers dropped the platform
    filter, so a nangate45 core_util_relief trial pulled sky130hd designs -> a
    wrong-platform arm flow -> guaranteed-meaningless verdict."""
    conn = _conn(tmp_path)
    _seed_history(conn, tmp_path, n=3, design_class="logic/medium", platform="sky130hd")
    trial = ab_runner.plan_trial(
        conn, symptom_id=KEY["symptom_id"], design_class="logic/medium",
        platform="nangate45", strategy="core_util_relief", n_designs=2)
    assert trial is None            # sky130hd subjects must NOT satisfy a nangate45 trial


def test_plan_trial_relaxes_class_when_exact_too_few(tmp_path):
    conn = _conn(tmp_path)
    _seed_history(conn, tmp_path, n=1, design_class="crypto/small")
    _seed_history(conn, tmp_path, n=2, design_class="logic/medium")
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


def test_record_trial_stamps_provenance_complete(tmp_path):
    """A trial with distinct arm run_ids is provenance_complete=True; a trial with
    missing/identical run_ids is False, so audit/replay can exclude unverifiable
    evidence (failure-patterns #45)."""
    conn = _conn(tmp_path)
    recipe_lifecycle.stage_shadow(conn, provenance="test", **KEY)
    tid = ab_runner.record_trial(conn, key=KEY, verdict="win",
                                 arm_a_run_id="ra", arm_b_run_id="rb",
                                 metrics={"judge_version": 2})
    m = json.loads(conn.execute("SELECT metrics_json FROM ab_trials WHERE trial_id=?",
                                (tid,)).fetchone()[0])
    assert m["provenance_complete"] is True

    tid2 = ab_runner.record_trial(conn, key=KEY, verdict="inconclusive",
                                  arm_a_run_id=None, arm_b_run_id=None,
                                  metrics={"judge_version": 2})
    m2 = json.loads(conn.execute("SELECT metrics_json FROM ab_trials WHERE trial_id=?",
                                 (tid2,)).fetchone()[0])
    assert m2["provenance_complete"] is False


def test_record_trial_warns_on_decisive_without_run_ids(tmp_path, capsys):
    """A DECISIVE verdict lacking distinct run_ids warns — that is the case that
    would otherwise promote a recipe on unverifiable evidence (failure-patterns #45)."""
    conn = _conn(tmp_path)
    recipe_lifecycle.stage_shadow(conn, provenance="test", **KEY)
    ab_runner.record_trial(conn, key=KEY, verdict="win",
                           arm_a_run_id=None, arm_b_run_id=None,
                           metrics={"judge_version": 2})
    assert "unverifiable" in capsys.readouterr().err


def test_incomplete_provenance_win_does_not_promote(tmp_path):
    """P0-1 (failure-patterns #48, 2026-07-14): a DECISIVE `win` whose provenance is
    incomplete (missing/identical arm run_ids -> provenance_complete=False) is
    UNVERIFIABLE and must NOT promote. record_trial still writes the row + warns, but
    judge_recipe excludes it, so the recipe stays 'candidate'. A later VERIFIABLE win
    (distinct run_ids) then promotes it — proving the gate blocks only the unverifiable
    evidence, not real wins."""
    conn = _conn(tmp_path)
    recipe_lifecycle.enqueue_candidate(conn, **KEY)
    # decisive win, but no distinct run_ids -> provenance_complete stamped False
    ab_runner.record_trial(conn, key=KEY, verdict="win", arm_a_run_id=None,
                           arm_b_run_id=None, metrics={})
    assert recipe_lifecycle.get_status(conn, **KEY) == "candidate"   # NOT promoted
    # identical run_ids are equally unverifiable
    ab_runner.record_trial(conn, key=KEY, verdict="win", arm_a_run_id="rx",
                           arm_b_run_id="rx", metrics={})
    assert recipe_lifecycle.get_status(conn, **KEY) == "candidate"   # still NOT promoted
    # a genuinely verifiable win (distinct run_ids) finally promotes
    ab_runner.record_trial(conn, key=KEY, verdict="win", arm_a_run_id="ra",
                           arm_b_run_id="rb", metrics={})
    assert recipe_lifecycle.get_status(conn, **KEY) == "promoted"


def test_incomplete_provenance_loss_does_not_demote(tmp_path):
    """P0-1 mirror: an unverifiable `loss` (no distinct run_ids) must not demote a
    promoted recipe either — it carries no decisive weight in the corpus judge."""
    conn = _conn(tmp_path)
    recipe_lifecycle.enqueue_candidate(conn, **KEY)
    ab_runner.record_trial(conn, key=KEY, verdict="win", arm_a_run_id="ra",
                           arm_b_run_id="rb", metrics={})           # verifiable win
    assert recipe_lifecycle.get_status(conn, **KEY) == "promoted"
    ab_runner.record_trial(conn, key=KEY, verdict="loss", arm_a_run_id=None,
                           arm_b_run_id=None, metrics={})           # unverifiable loss
    assert recipe_lifecycle.get_status(conn, **KEY) == "promoted"   # unchanged


def _seed_run(conn, rid, base, platform="nangate45"):
    conn.execute("INSERT OR REPLACE INTO runs (run_id, project_path, design_name, "
                 "platform, ingested_at, cell_count) VALUES (?,?,?,?,?,?)",
                 (rid, str(base), "d", platform, "2026-06-10T00:00:00Z", 1000))
    conn.commit()


def test_fabricated_run_ids_never_promote(tmp_path):
    """P0-10 (2026-07-15): a decisive `win` citing run_ids that DON'T exist in `runs`
    is unverifiable — record_trial stamps provenance_complete=False and judge_recipe
    excludes it, so the recipe stays candidate. A trial cannot self-certify its own
    provenance through arbitrary strings."""
    conn = _conn(tmp_path)
    recipe_lifecycle.enqueue_candidate(conn, **KEY)
    ab_runner.record_trial(conn, key=KEY, verdict="win",
                           arm_a_run_id="fake-A", arm_b_run_id="fake-B", metrics={})
    m = json.loads(conn.execute(
        "SELECT metrics_json FROM ab_trials ORDER BY trial_id DESC LIMIT 1").fetchone()[0])
    assert m["provenance_complete"] is False
    assert recipe_lifecycle.get_status(conn, **KEY) == "candidate"   # NOT promoted


def _win_trials_on_subject(conn, base, n):
    """Record n winning trials that reuse ONE base design (distinct real run_ids)."""
    for i in range(n):
        a, b = f"{base}a{i}", f"{base}b{i}"
        _seed_run(conn, a, conn_dir(conn) / f"{base}_abA_antenna__{i}")
        _seed_run(conn, b, conn_dir(conn) / f"{base}_abB_antenna__{i}")
        ab_runner.record_trial(conn, key=KEY, verdict="win",
                               arm_a_run_id=a, arm_b_run_id=b, metrics={})


def conn_dir(conn):
    import pathlib
    return pathlib.Path(conn.execute("PRAGMA database_list").fetchone()[2]).parent


def test_pseudo_replication_cannot_overturn_a_genuine_loss(tmp_path):
    """P1-11 (2026-07-15): five wins on ONE reused subject are ONE independent vote,
    not five. A genuine loss on a DIFFERENT subject demotes to shadow; five
    pseudo-replicated wins on a single subject then read 1w1l (tie) and CANNOT
    revive it to PROMOTED. Under the old raw-row count they read 5w1l -> promoted —
    the reused subject masqueraded as five-fold corroboration.
    (2026-07-16 issue 2: a tied corpus now lands DETERMINISTICALLY in 'candidate'
    — re-validation, still never a promotion. The old 'stays shadow' expectation
    was itself order-dependent: win-first would have read 'promoted'.)"""
    conn = _conn(tmp_path)
    recipe_lifecycle.enqueue_candidate(conn, **KEY)
    _seed_run(conn, "s2a", tmp_path / "s2_abA_antenna__0")
    _seed_run(conn, "s2b", tmp_path / "s2_abB_antenna__0")
    ab_runner.record_trial(conn, key=KEY, verdict="loss",
                           arm_a_run_id="s2a", arm_b_run_id="s2b", metrics={})
    assert recipe_lifecycle.get_status(conn, **KEY) == "shadow"
    _win_trials_on_subject(conn, "s1", 5)                     # 5 wins, ONE subject
    # 1w1l independent-subject tie: re-queued for validation, NEVER promoted
    assert recipe_lifecycle.get_status(conn, **KEY) == "candidate"


def test_pseudo_replicated_wins_count_once_in_evidence(tmp_path):
    """P1-11: five wins on one subject promote HONESTLY as 1w0l — the evidence string
    reflects one independent subject, not five (no fabricated corroboration)."""
    conn = _conn(tmp_path)
    recipe_lifecycle.enqueue_candidate(conn, **KEY)
    _win_trials_on_subject(conn, "s1", 5)
    prov = conn.execute(
        "SELECT provenance FROM recipe_status WHERE symptom_id=? AND design_class=? "
        "AND platform=? AND strategy=?",
        (KEY["symptom_id"], KEY["design_class"], KEY["platform"],
         KEY["strategy"])).fetchone()[0]
    assert prov == "ab_corpus:1w0l"                          # one subject, not "5w0l"


def test_trial_uuid_makes_record_idempotent(tmp_path):
    """P0-16 (2026-07-15): a crash/retry that re-records the SAME planned trial (same
    trial_uuid) must not create a second row inflating the evidence corpus."""
    conn = _conn(tmp_path)
    recipe_lifecycle.enqueue_candidate(conn, **KEY)
    uuid = "trial-abc-0"
    for _ in range(3):     # retried three times
        ab_runner.record_trial(conn, key=KEY, verdict="win", arm_a_run_id="ra",
                               arm_b_run_id="rb", metrics={}, trial_uuid=uuid)
    n = conn.execute("SELECT COUNT(*) FROM ab_trials WHERE trial_uuid=?",
                     (uuid,)).fetchone()[0]
    assert n == 1                                            # exactly one row, not three


def test_negative_wall_never_wins_cost_tiebreak():
    """P1-16 (2026-07-15): a negative/NaN wall time is a corrupt sample and must not
    manufacture a cost_tiebreak win. Two clean arms where B reports a negative wall
    time -> the value drops out and the verdict is inconclusive (not a win)."""
    a = [{"is_success": True, "wall_s": 100.0}, {"is_success": True, "wall_s": 100.0}]
    b = [{"is_success": True, "wall_s": -5.0}, {"is_success": True, "wall_s": float("nan")}]
    verdict, reason = ab_runner.judge_repeated_ex(a, b)
    assert verdict == "inconclusive"
    assert reason != "cost_tiebreak"


def test_nan_metrics_serialize_without_crash(tmp_path):
    """P1-16: a NaN inside the metrics dict is sanitized to null before storage
    (allow_nan=False would otherwise crash, or NaN would round-trip as a decisive
    corrupt sample)."""
    conn = _conn(tmp_path)
    recipe_lifecycle.enqueue_candidate(conn, **KEY)
    ab_runner.record_trial(conn, key=KEY, verdict="inconclusive", arm_a_run_id="ra",
                           arm_b_run_id="rb",
                           metrics={"A_samples": [{"wall_s": float("nan")}]})
    mj = conn.execute("SELECT metrics_json FROM ab_trials ORDER BY trial_id DESC "
                      "LIMIT 1").fetchone()[0]
    assert "NaN" not in mj                        # not the bare non-standard token
    json.loads(mj)                                # parses as strict JSON


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


# ── 2026-07-16 issue 1: run-to-arm OWNERSHIP, not mere existence ─────────────

def test_foreign_real_runs_cannot_certify_a_win(tmp_path):
    """Two runs that EXIST but belong to unrelated projects/platforms must stamp
    provenance_complete=False and never promote (2026-07-16 issue 1 probe)."""
    conn = _conn(tmp_path)
    recipe_lifecycle.enqueue_candidate(conn, **KEY)
    _seed_run(conn, "f1", tmp_path / "unrelated_project_one", platform="sky130hd")
    _seed_run(conn, "f2", tmp_path / "other_design_two", platform="asap7")
    tid = ab_runner.record_trial(conn, key=KEY, verdict="win",
                                 arm_a_run_id="f1", arm_b_run_id="f2", metrics={})
    m = json.loads(conn.execute("SELECT metrics_json FROM ab_trials WHERE trial_id=?",
                                (tid,)).fetchone()[0])
    assert m["provenance_complete"] is False
    assert recipe_lifecycle.get_status(conn, **KEY) == "candidate"   # NOT promoted


def test_swapped_arm_roles_are_not_owned(tmp_path):
    """The A column must hold the _abA_ run and B the _abB_ run — a swapped pair
    is not the planned experiment."""
    conn = _conn(tmp_path)
    assert ab_runner._arms_owned(conn, KEY, "ra", "rb") is True
    assert ab_runner._arms_owned(conn, KEY, "rb", "ra") is False


def test_cross_subject_and_cross_strategy_pairs_are_not_owned(tmp_path):
    """Arms of two DIFFERENT subjects (or another strategy's arms) are a mix of
    experiments, not one trial."""
    conn = _conn(tmp_path)
    # rx is subjY's A arm: pairing it with subjX's B arm crosses subjects
    assert ab_runner._arms_owned(conn, KEY, "rx", "rb") is False
    # a density_relief arm pair cannot certify this antenna trial
    for rid, base in (("da", "s_abA_density__0"), ("db", "s_abB_density__0")):
        _seed_run(conn, rid, tmp_path / base)
    assert ab_runner._arms_owned(conn, KEY, "da", "db") is False


def test_cross_platform_arm_runs_are_not_owned(tmp_path):
    """Arm runs whose ingested platform differs from the trial key's are foreign."""
    conn = _conn(tmp_path)
    for rid, base in (("pa", "s_abA_antenna__0"), ("pb", "s_abB_antenna__0")):
        _seed_run(conn, rid, tmp_path / base, platform="sky130hd")   # key: nangate45
    assert ab_runner._arms_owned(conn, KEY, "pa", "pb") is False


def _h6(key):
    """The planner's trial_h6 — a short hash of the FULL recipe key."""
    import hashlib
    return hashlib.sha1("|".join([
        key["symptom_id"], key["design_class"], key["platform"],
        key["strategy"]]).encode("utf-8")).hexdigest()[:6].upper()


def test_same_strategy_other_recipe_key_is_not_owned(tmp_path):
    """P0-R2 (2026-07-19 audit, failure-patterns #52). Subject, role, tail,
    strategy prefix and platform all matching still does NOT make an arm pair
    this key's evidence — symptom_id and design_class were never bound. In the
    committed store ONE arm pair was the sole decisive evidence promoting THREE
    distinct density_relief design classes."""
    conn = _conn(tmp_path)
    strat8 = KEY["strategy"][:8]
    foreign = dict(KEY, design_class="bus_heavy/large")     # same strategy+platform
    for rid, arm in (("ha", "A"), ("hb", "B")):
        _seed_run(conn, rid, tmp_path / f"subj_ab{arm}_{strat8}{_h6(foreign)}_0")
    # The arms were planned for `foreign`, so they own THAT key and not KEY.
    assert ab_runner._arms_owned(conn, foreign, "ha", "hb") is True
    assert ab_runner._arms_owned(conn, KEY, "ha", "hb") is False


def test_same_strategy_other_symptom_is_not_owned(tmp_path):
    """Same shape, differing only in symptom_id."""
    conn = _conn(tmp_path)
    strat8 = KEY["strategy"][:8]
    foreign = dict(KEY, symptom_id="feedface00000002")
    for rid, arm in (("sa", "A"), ("sb", "B")):
        _seed_run(conn, rid, tmp_path / f"subj_ab{arm}_{strat8}{_h6(foreign)}_0")
    assert ab_runner._arms_owned(conn, foreign, "sa", "sb") is True
    assert ab_runner._arms_owned(conn, KEY, "sa", "sb") is False


def test_hashless_legacy_arm_dirs_stay_grandfathered(tmp_path):
    """All 6 decisive committed trials have hash-less tails (`density__0`) that
    predate the trial_h6 scheme. Rejecting them would flip no verdict today, but
    would make live keys un-re-derivable from evidence nobody can regenerate —
    so bind what was recorded, never retroactively invalidate what predates the
    recording (same principle as the absent-provenance_complete carve-out)."""
    conn = _conn(tmp_path)
    strat8 = KEY["strategy"][:8]
    for rid, arm in (("la", "A"), ("lb", "B")):
        _seed_run(conn, rid, tmp_path / f"subj_ab{arm}_{strat8}_0")
    assert ab_runner._arms_owned(conn, KEY, "la", "lb") is True


def test_stamped_true_provenance_is_reverified_at_judge(tmp_path):
    """Defense-in-depth: a row whose metrics CLAIM provenance_complete=True but
    whose run_ids don't resolve locally as this trial's own arms (merged bundle,
    rerouted caller) must not drive a transition; absent-key legacy rows stay
    countable (grandfathered)."""
    conn = _conn(tmp_path)
    recipe_lifecycle.enqueue_candidate(conn, **KEY)
    conn.execute(
        "INSERT INTO ab_trials (symptom_id, design_class, platform, strategy, "
        "arm_a_run_id, arm_b_run_id, verdict, metrics_json, ts) VALUES (?,?,?,?,?,?,?,?,?)",
        (KEY["symptom_id"], KEY["design_class"], KEY["platform"], KEY["strategy"],
         "ghost-a", "ghost-b", "win", json.dumps({"provenance_complete": True}), "t"))
    conn.commit()
    assert ab_runner.judge_recipe(conn, **KEY) is None               # excluded
    assert recipe_lifecycle.get_status(conn, **KEY) == "candidate"
    # A legacy row with NO provenance key and NULL run_ids no longer counts
    # either (P0-R3, 2026-07-20 operator ruling: quarantine forward only). It
    # used to be grandfathered as countable, which let untraceable evidence
    # drive a NEW promotion. It stays visible in ab_trials; it just cannot move
    # a lifecycle state. Nothing is demoted — the key simply stays put.
    conn.execute(
        "INSERT INTO ab_trials (symptom_id, design_class, platform, strategy, "
        "verdict, metrics_json, ts) VALUES (?,?,?,?,?,?,?)",
        (KEY["symptom_id"], KEY["design_class"], KEY["platform"], KEY["strategy"],
         "win", json.dumps({}), "t"))
    conn.commit()
    assert ab_runner.judge_recipe(conn, **KEY) is None
    assert recipe_lifecycle.get_status(conn, **KEY) == "candidate"
