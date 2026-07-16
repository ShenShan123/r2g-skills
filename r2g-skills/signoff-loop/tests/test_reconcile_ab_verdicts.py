"""2026-06-26 honesty fix: reconcile stale A/B verdicts against the current judge.

The 2026-06-25 success-tie tiebreak (COST_FLOOR + strict separation) made
judge_repeated stop deciding equally-correct arms on wall-clock jitter -- but
verdicts recorded under the OLD rule stayed frozen in ab_trials, and judge_recipe
counts those strings, so a recipe sat `promoted` on noise (`ab_corpus:3w1l`) with
no re-judge path. reconcile_ab_verdicts re-derives each verdict from the stored
metrics and reverts the now-evidence-less promotion. These tests lock that.
"""
import ab_runner
import knowledge_db
import recipe_lifecycle
import reconcile_ab_verdicts


def _conn(tmp_path):
    c = knowledge_db.connect(tmp_path / "k.sqlite")
    knowledge_db.ensure_schema(c)
    return c


def _samp(wall, *, success=True, score=0.8833, iters=1):
    return {"is_success": success, "outcome_score": score, "wall_s": wall,
            "fix_iters": iters}


def test_reconcile_flips_noise_verdicts_and_reverts_promotion(tmp_path):
    """The exact nangate45 antenna case: identical-outcome arms whose win/loss came from
    the old flat-2pct tiebreak. Reconcile flips all to inconclusive and reverts the
    promotion (no decisive evidence left) to candidate."""
    conn = _conn(tmp_path)
    key = dict(symptom_id="84ffbb", design_class="logic/unknown",
               platform="nangate45", strategy="antenna_diode_repair")
    # 3 "win" + 1 "loss" recorded under the OLD judge: same is_success+outcome_score,
    # differing only by a few seconds of wall-clock noise.
    trials = [
        ("win",  [_samp(93), _samp(91)],   [_samp(89), _samp(90)]),
        ("win",  [_samp(101), _samp(102)], [_samp(98), _samp(101)]),
        ("win",  [_samp(116)],             [_samp(113)]),
        ("loss", [_samp(123)],             [_samp(134)]),
    ]
    for i, (verdict, A, B) in enumerate(trials):
        # Distinct REAL run_ids on distinct subjects -> provenance_complete=True, so these
        # DECISIVE verdicts count in judge_recipe. (Post-P0-1 (failure-patterns #48) a
        # noise-promotion needing reconcile is a provenance-COMPLETE trial carrying an
        # old-judge noise verdict; a None/fabricated-run_id trial is provenance-incomplete
        # (P0-10) and never promotes, so there would be nothing for reconcile to revert.)
        for arm, rid in (("A", f"ra{i}"), ("B", f"rb{i}")):
            conn.execute(
                "INSERT OR REPLACE INTO runs (run_id, project_path, design_name, "
                "platform, ingested_at, cell_count) VALUES (?,?,?,?,?,?)",
                (rid, str(tmp_path / f"d{i}_ab{arm}_diode_0"), f"d{i}",
                 key["platform"], "2026-06-10T00:00:00Z", 1000))
        ab_runner.record_trial(conn, key=key, verdict=verdict, arm_a_run_id=f"ra{i}",
                               arm_b_run_id=f"rb{i}",
                               metrics={"A_samples": A, "B_samples": B})
    # judge_recipe promoted it on the frozen 3w1l corpus (the bug).
    assert recipe_lifecycle.get_status(conn, **key) == "promoted"

    out = reconcile_ab_verdicts.reconcile(conn)
    assert len(out["verdicts_flipped"]) == 4
    assert all(f["to"] == "inconclusive" for f in out["verdicts_flipped"])
    # promotion was fabricated -> reverted to candidate for honest re-validation.
    assert recipe_lifecycle.get_status(conn, **key) == "candidate"
    assert out["reverted_to_candidate"] and \
        out["reverted_to_candidate"][0]["from"] == "promoted"
    # the ab_trials corpus now has zero decisive verdicts.
    wl = conn.execute("SELECT SUM(verdict IN ('win','loss')) FROM ab_trials").fetchone()[0]
    assert (wl or 0) == 0


def test_reconcile_is_idempotent(tmp_path):
    conn = _conn(tmp_path)
    key = dict(symptom_id="s", design_class="logic/small",
               platform="nangate45", strategy="antenna_diode_repair")
    ab_runner.record_trial(conn, key=key, verdict="win", arm_a_run_id=None,
                           arm_b_run_id=None,
                           metrics={"A_samples": [_samp(100)], "B_samples": [_samp(99)]})
    reconcile_ab_verdicts.reconcile(conn)
    second = reconcile_ab_verdicts.reconcile(conn)
    assert second["verdicts_flipped"] == []      # nothing left to flip


def test_reconcile_preserves_real_divergent_win(tmp_path):
    """A genuinely divergent trial (arm A fails to sign off, arm B succeeds) is a REAL win
    and must survive reconcile untouched -- the recipe stays promoted."""
    conn = _conn(tmp_path)
    key = dict(symptom_id="route1", design_class="logic/small",
               platform="sky130hd", strategy="route_relief")
    A = [_samp(5400, success=False, score=0.0, iters=None)]   # control: route timeout
    B = [_samp(37, success=True, score=0.9)]                  # relief: signs off
    ab_runner.record_trial(conn, key=key, verdict="win", arm_a_run_id="ra",
                           arm_b_run_id="rb", metrics={"A_samples": A, "B_samples": B})
    assert recipe_lifecycle.get_status(conn, **key) == "promoted"
    out = reconcile_ab_verdicts.reconcile(conn)
    assert out["verdicts_flipped"] == []
    assert recipe_lifecycle.get_status(conn, **key) == "promoted"


def test_reconcile_keeps_sparse_metric_verdict(tmp_path):
    """A trial with no A/B samples in metrics_json keeps its stored verdict -- reconcile
    never invents a verdict from missing data."""
    conn = _conn(tmp_path)
    key = dict(symptom_id="s2", design_class="logic/small",
               platform="nangate45", strategy="antenna_diode_repair")
    ab_runner.record_trial(conn, key=key, verdict="win", arm_a_run_id=None,
                           arm_b_run_id=None, metrics={})
    out = reconcile_ab_verdicts.reconcile(conn)
    assert out["verdicts_flipped"] == []
