"""Win 1 — dense signoff reward (outcome_score): stage-progress + VRR.

The score is a PURE function of one run's OWN artifacts (its stage_log + its own
fix_log), additive and advisory: is_success stays the sole authority for clean/
fail and for recipe promotion. PPA-product term is deferred.
"""
import json
from pathlib import Path

import ingest_run
import knowledge_db


# ── pure VRR arithmetic ──────────────────────────────────────────────────────

def test_vrr_basic_reduction():
    rows = [{"iter": 1, "before": 10, "after": 2}]
    assert ingest_run._vrr_from_fix_log(rows) == 0.8


def test_vrr_zero_floor_on_regression():
    # after > before -> negative reduction -> floored at 0, never negative.
    rows = [{"iter": 1, "before": 4, "after": 9}]
    assert ingest_run._vrr_from_fix_log(rows) == 0.0


def test_vrr_uses_first_before_and_last_after_across_iters():
    rows = [{"iter": 2, "before": 6, "after": 1},
            {"iter": 1, "before": 10, "after": 6}]
    # before from earliest iter (10), after from latest iter (1) -> 0.9
    assert ingest_run._vrr_from_fix_log(rows) == 0.9


def test_vrr_null_when_no_fix():
    assert ingest_run._vrr_from_fix_log([]) is None


def test_vrr_null_when_before_zero():
    # No violations to reduce -> VRR undefined (NULL, not 1.0 or 0.0).
    assert ingest_run._vrr_from_fix_log([{"iter": 1, "before": 0, "after": 0}]) is None


# ── stage-progress ladder ────────────────────────────────────────────────────

def _route_abort_log():
    return [{"stage": "synth", "status": 0}, {"stage": "floorplan", "status": 0},
            {"stage": "place", "status": 0}, {"stage": "cts", "status": 0},
            {"stage": "route", "status": 1}]


def test_stage_rank_route_abort_is_three():
    # A route abort reached 'route' on the ladder (rank 3) -> the gradient AES/DES have.
    rank = ingest_run._furthest_stage_rank(
        _route_abort_log(), "fail", "route", None, None, None)
    assert rank == 3


def test_stage_rank_clean_signoff_is_six():
    log = [{"stage": s, "status": 0} for s in
           ("synth", "floorplan", "place", "cts", "route", "finish")]
    rank = ingest_run._furthest_stage_rank(
        log, "pass", None, "clean", "clean", "complete")
    assert rank == 6


def test_stage_rank_place_abort_is_two():
    log = [{"stage": "synth", "status": 0}, {"stage": "floorplan", "status": 0},
           {"stage": "place", "status": 1}]
    assert ingest_run._furthest_stage_rank(log, "fail", "place", None, None, None) == 2


def test_stage_rank_skipped_signoff_does_not_count():
    log = [{"stage": s, "status": 0} for s in
           ("synth", "floorplan", "place", "cts", "route", "finish")]
    # finish reached (rank 3); skipped DRC/LVS did NOT reach those stages.
    assert ingest_run._furthest_stage_rank(
        log, "pass", None, "skipped", "skipped", None) == 3


def test_stage_rank_unknown_is_none():
    assert ingest_run._furthest_stage_rank([], "unknown", None, None, None, None) is None


# ── score combination + renormalization + cold start ─────────────────────────

def test_outcome_score_stage_only_when_vrr_null():
    # renormalize to w_stage = 1.0 when vrr is NULL -> score == stage_progress.
    assert ingest_run._outcome_score(3, None) == 0.5


def test_outcome_score_blends_stage_and_vrr():
    # rank 4/6 = 0.6667 stage; vrr 0.5 -> 0.7*0.6667 + 0.3*0.5 = 0.6167
    s = ingest_run._outcome_score(4, 0.5)
    assert abs(s - (0.7 * (4 / 6) + 0.3 * 0.5)) < 1e-9


def test_outcome_score_null_when_stage_unknown():
    # NULL (not measured) is distinct from 0 (measured worst).
    assert ingest_run._outcome_score(None, 0.9) is None
    assert ingest_run._outcome_score(None, None) is None


# ── end-to-end ingest ────────────────────────────────────────────────────────

def _mk(tmp_path, name, *, drc, lvs, fix=None, stage_log, ppa_geo=None):
    p = tmp_path / name
    (p / "constraints").mkdir(parents=True)
    (p / "reports").mkdir()
    (p / "backend").mkdir()
    (p / "constraints" / "config.mk").write_text(
        f"export DESIGN_NAME = {name}\nexport PLATFORM = nangate45\n")
    (p / "reports" / "drc.json").write_text(json.dumps(drc))
    (p / "reports" / "lvs.json").write_text(json.dumps(lvs))
    (p / "reports" / "ppa.json").write_text(json.dumps(
        {"summary": {}, "geometry": ppa_geo or {"instance_count": 900}}))
    (p / "backend" / "stage_log.jsonl").write_text(
        "\n".join(json.dumps(s) for s in stage_log) + "\n")
    if fix is not None:
        (p / "reports" / "fix_log.jsonl").write_text(
            "\n".join(json.dumps(r) for r in fix) + "\n")
    return p


def _conn(tmp_path, monkeypatch):
    monkeypatch.setenv("R2G_JOURNAL_DB", str(tmp_path / "journal.sqlite"))
    c = knowledge_db.connect(tmp_path / "k.sqlite")
    knowledge_db.ensure_schema(c)
    return c


def _score(conn, run_id):
    return conn.execute("SELECT outcome_score FROM runs WHERE run_id=?",
                        (run_id,)).fetchone()[0]


def test_ingest_route_abort_scores_half_from_stage(tmp_path, monkeypatch):
    conn = _conn(tmp_path, monkeypatch)
    rid = ingest_run.ingest(_mk(
        tmp_path, "aes_routestuck", drc={"status": "unknown"},
        lvs={"status": "unknown"}, stage_log=_route_abort_log()), conn)
    # route abort, no fix -> stage_progress 0.5, vrr NULL -> 0.5
    assert abs(_score(conn, rid) - 0.5) < 1e-9


def test_ingest_drc_fix_run_is_vrr_boosted(tmp_path, monkeypatch):
    conn = _conn(tmp_path, monkeypatch)
    log = [{"stage": s, "status": 0} for s in
           ("synth", "floorplan", "place", "cts", "route", "finish")]
    rid = ingest_run.ingest(_mk(
        tmp_path, "fixme_small", drc={"status": "clean", "total_violations": 0},
        lvs={"status": "clean"}, stage_log=log,
        fix=[{"fix_session_id": "s1", "check": "drc", "violation_class": "antenna",
              "iter": 1, "strategy": "antenna_diode_repair",
              "before": 8, "after": 0, "verdict": "cleared"}]), conn)
    # rank 6 (rcx? no -> lvs clean=5? rcx none) ; drc clean + lvs clean -> rank 5.
    # stage 5/6 = 0.8333, vrr 1.0 -> 0.7*0.8333 + 0.3*1.0 = 0.8833
    assert abs(_score(conn, rid) - (0.7 * (5 / 6) + 0.3 * 1.0)) < 1e-9


def test_outcome_score_idempotent_across_unrelated_ingests(tmp_path, monkeypatch):
    conn = _conn(tmp_path, monkeypatch)
    proj = _mk(tmp_path, "stable_x", drc={"status": "unknown"},
               lvs={"status": "unknown"}, stage_log=_route_abort_log())
    rid = ingest_run.ingest(proj, conn)
    first = _score(conn, rid)
    for i in range(5):
        ingest_run.ingest(_mk(tmp_path, f"other_{i}", drc={"status": "unknown"},
                              lvs={"status": "unknown"},
                              stage_log=_route_abort_log()), conn)
    again = ingest_run.ingest(proj, conn)
    assert again == rid
    assert _score(conn, rid) == first   # byte-identical, no cross-row drift


def test_gate_unchanged_is_success_independent_of_score(tmp_path, monkeypatch):
    conn = _conn(tmp_path, monkeypatch)
    log = [{"stage": s, "status": 0} for s in
           ("synth", "floorplan", "place", "cts", "route", "finish")]
    rid = ingest_run.ingest(_mk(
        tmp_path, "clean_one", drc={"status": "clean", "total_violations": 0},
        lvs={"status": "clean"}, stage_log=log), conn)
    row = dict(zip([c[0] for c in conn.execute("SELECT * FROM runs").description],
                   conn.execute("SELECT * FROM runs WHERE run_id=?", (rid,)).fetchone()))
    # outcome_score present AND is_success still derives solely from signoff status.
    assert row["outcome_score"] is not None
    assert knowledge_db.is_success(row) is True
