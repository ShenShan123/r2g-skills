"""A signoff A/B arm with NO signoff check EXECUTED must not read as a success.

Found by the 2026-08-01 gf180 wave (failure-patterns.md #32b). gf180 ships no
`drc/` and no `lvs/` directory at all, so run_drc/run_lvs honestly record
`status='skipped'` — but the flow still completes six stages, so
`knowledge_db.is_success` takes its strict `orfs_status='pass'` path and BOTH
arms of a signoff trial read `is_success=True` no matter what the recipe did.
The verdict then turns entirely on wall-clock: four real `pdn_die_floor` trials
came back `success_tie_cost_within_noise` separated by 78 vs 79.5 seconds. Had
one arm been noise-faster, judge v2 would have declared a WIN and promoted a
signoff recipe backed by zero signoff evidence.

This is the same metric-granularity lesson `_arm_metric` already applies to the
timing / synth / DRC / LVS branches ("the generic is_success ties both arms
whenever an UNRELATED residual keeps the run non-clean"), finally applied to the
DEFAULT signoff branch for the case where NOTHING ran.
"""
import engineer_loop as el
import knowledge_db


def _conn(tmp_path):
    c = knowledge_db.connect(tmp_path / "knowledge.sqlite")
    knowledge_db.ensure_schema(c)
    return c


def _add_run(conn, project_path, *, drc, lvs, orfs_status="pass",
             run_id="r1", elapsed=80.0):
    conn.execute(
        "INSERT INTO runs (run_id, project_path, design_name, platform, "
        " ingested_at, orfs_status, drc_status, lvs_status, rcx_status, "
        " total_elapsed_s, outcome_score) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (run_id, project_path, "d", "gf180", "2026-08-01T00:00:00",
         orfs_status, drc, lvs, "complete", elapsed, 1.0))
    conn.commit()


def test_no_signoff_executed_is_not_a_success(tmp_path):
    """THE bug: gf180 (no DRC deck, no LVS deck) -> both arms trivially succeed."""
    conn = _conn(tmp_path)
    _add_run(conn, "/x/arm_a", drc="skipped", lvs="skipped")
    m = el._arm_metric(conn, "/x/arm_a")
    assert m["judged_on"] == "signoff:unverifiable", m
    assert m["is_success"] is False, (
        "a signoff arm whose DRC and LVS never ran must not count as a success — "
        "otherwise the trial is decided by wall-clock noise and can promote a "
        "recipe backed by zero signoff evidence")


def test_null_signoff_is_NOT_unverifiable(tmp_path):
    """Only an EXPLICIT 'skipped' counts — NULL is a different fact.

    'skipped' is a positive statement by run_drc/run_lvs that the check was
    deliberately not run for want of a deck. NULL merely means no signoff data was
    recorded, which is the normal state of a ROUTE arm whose success is legitimately
    established by the flow getting past route. Treating NULL as unverifiable made
    route_relief unjudgeable (test_route_ab_loop / test_ab_drain_parallel).
    """
    conn = _conn(tmp_path)
    _add_run(conn, "/x/arm_null", drc=None, lvs=None)
    m = el._arm_metric(conn, "/x/arm_null")
    assert m["judged_on"] == "signoff"
    assert m["is_success"] is True


def test_drc_only_platform_stays_judgeable(tmp_path):
    """ihp-sg13g2 has a real DRC deck but no KLayout LVS deck (tier research_ready).

    The guard requires BOTH checks unexecuted, so real DRC evidence still judges.
    """
    conn = _conn(tmp_path)
    _add_run(conn, "/x/arm_ihp", drc="clean", lvs="skipped")
    m = el._arm_metric(conn, "/x/arm_ihp")
    assert m["judged_on"] == "signoff", m
    assert m["is_success"] is True


def test_fully_signed_off_arm_unaffected(tmp_path):
    """sky130hd/nangate45: both checks executed clean -> ordinary signoff judging."""
    conn = _conn(tmp_path)
    _add_run(conn, "/x/arm_hd", drc="clean", lvs="clean")
    m = el._arm_metric(conn, "/x/arm_hd")
    assert m["judged_on"] == "signoff"
    assert m["is_success"] is True


def test_both_unverifiable_arms_do_not_promote(tmp_path):
    """End of the chain: two unverifiable arms must not produce a decisive verdict.

    Both arms non-success is what makes judge v2 return the honest
    'never succeeded' inconclusive instead of a cost tiebreak.
    """
    conn = _conn(tmp_path)
    _add_run(conn, "/x/a", drc="skipped", lvs="skipped", run_id="ra", elapsed=78.0)
    _add_run(conn, "/x/b", drc="skipped", lvs="skipped", run_id="rb", elapsed=79.5)
    a = el._arm_metric(conn, "/x/a")
    b = el._arm_metric(conn, "/x/b")
    assert not a["is_success"] and not b["is_success"]
    # The cost difference that WOULD have decided it under the old behaviour.
    assert a["wall_s"] != b["wall_s"]


def test_backend_abort_arm_keeps_legacy_judgment(tmp_path):
    """The narrowing: a run that DIED never reached signoff — that is not the same
    as a completed run whose platform has no deck.

    A backend-abort arm (orfs_status='fail', null signoff) is already honestly
    False under the whole-run judgment, so it keeps judged_on='signoff'. Guarding
    on `success` first is what keeps these two cases apart
    (tests/test_judge_v2_symptom_target.py::test_no_target_falls_back_to_is_success).
    """
    conn = _conn(tmp_path)
    _add_run(conn, "/x/arm_dead", drc=None, lvs=None, orfs_status="fail",
             run_id="rdead")
    m = el._arm_metric(conn, "/x/arm_dead")
    assert m["is_success"] is False
    assert m["judged_on"] == "signoff", (
        "a backend abort must keep the legacy whole-run judgment — only a run that "
        "would otherwise read SUCCESS with no executed check is 'unverifiable'")
