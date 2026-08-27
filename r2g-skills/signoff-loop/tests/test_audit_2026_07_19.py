"""2026-07-19 post-consolidation audit fixes (failure-patterns #52).

Acceptance criteria from the report:
  1. an explicit ORFS failure can never be learnable success   (P0-R1)
  3. parking increments the lifecycle version and a late win cannot re-promote
     the parked recipe                                          (P0-N1)
"""
import json
import sqlite3
from pathlib import Path

import ab_runner
import engineer_loop as el
import knowledge_db
import recipe_lifecycle


def _conn(tmp_path):
    c = knowledge_db.connect(tmp_path / "knowledge.sqlite")
    knowledge_db.ensure_schema(c)
    return c


# ── P0-R1: explicit ORFS failure vetoes the relaxed success path ─────────────

def test_orfs_fail_vetoes_clean_signoff():
    """The real store's rv32i_csr row: died at synth in 10s, yet carries clean
    DRC/LVS/RCX carried over from an earlier flow in the same project dir."""
    row = {"orfs_status": "fail", "orfs_fail_stage": "synth",
           "drc_status": "clean", "lvs_status": "clean", "rcx_status": "complete"}
    assert knowledge_db.is_success(row) is False


def test_relaxed_path_still_rescues_incomplete_runs():
    """The veto must be narrow: 'partial'/'unknown' mean the backend record is
    merely INCOMPLETE, which is exactly what the relaxed path exists to rescue."""
    for status in ("partial", "unknown", None):
        row = {"orfs_status": status, "drc_status": "clean",
               "lvs_status": "clean", "rcx_status": "complete"}
        assert knowledge_db.is_success(row) is True, status


def test_strict_pass_unaffected():
    assert knowledge_db.is_success(
        {"orfs_status": "pass", "drc_status": "clean",
         "lvs_status": "clean", "rcx_status": "complete"}) is True


def test_learner_counts_orfs_fail_as_failure(tmp_path):
    """End-to-end: the rebuilt learner must not count the failed run a success."""
    conn = _conn(tmp_path)
    common = dict(design_family="risc", platform="nangate45",
                  drc_status="clean", lvs_status="clean", rcx_status="complete")
    for i, orfs in enumerate(("pass", "pass", "fail")):
        conn.execute(
            "INSERT INTO runs (run_id, project_path, design_name, design_family,"
            " platform, orfs_status, orfs_fail_stage, drc_status, lvs_status,"
            " rcx_status, core_utilization, ingested_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"r{i}", f"/p/{i}", f"d{i}", common["design_family"],
             common["platform"], orfs, "synth" if orfs == "fail" else None,
             "clean", "clean", "complete", 20.0, "2026-07-19T00:00:00Z"))
    conn.commit()
    conn.row_factory = sqlite3.Row
    runs = [dict(r) for r in conn.execute("SELECT * FROM runs")]
    assert sum(1 for r in runs if knowledge_db.is_success(r)) == 2
    assert sum(1 for r in runs if not knowledge_db.is_success(r)) == 1


# ── P0-N1: parking is a versioned transition; a parked recipe is unjudgeable ──

_PARKED_KEY = dict(symptom_id="S1", design_class="logic/small",
                   platform="nangate45", strategy="lvs_resolve_unknown")


def _seed_candidate(conn, key, *, version):
    conn.execute(
        "INSERT INTO recipe_status (symptom_id, design_class, platform, strategy,"
        " status, provenance, status_version, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (key["symptom_id"], key["design_class"], key["platform"], key["strategy"],
         "candidate", "seed", version, "2026-07-19T00:00:00Z"))
    conn.commit()


def test_park_bumps_status_version(tmp_path):
    conn = _conn(tmp_path)
    _seed_candidate(conn, _PARKED_KEY, version=7)
    assert recipe_lifecycle.park_nondivergent(conn) == 1
    assert recipe_lifecycle.get_status(conn, **_PARKED_KEY) == "parked"
    # 7 -> 8: parking is a lifecycle transition like any other, so the plan/judge
    # staleness handshake sees it and cancels trials planned before the park.
    assert recipe_lifecycle.get_status_version(conn, **_PARKED_KEY) == 8


def test_park_bumps_version_on_legacy_null_row(tmp_path):
    """Committed rows predate versioning (all-NULL); COALESCE must still bump."""
    conn = _conn(tmp_path)
    _seed_candidate(conn, _PARKED_KEY, version=None)
    recipe_lifecycle.park_nondivergent(conn)
    assert recipe_lifecycle.get_status_version(conn, **_PARKED_KEY) == 1


def test_park_is_idempotent(tmp_path):
    """plan_arms parks at the top of EVERY drain — a no-op park must not churn
    the version, or the handshake would cancel healthy trials every round."""
    conn = _conn(tmp_path)
    _seed_candidate(conn, _PARKED_KEY, version=7)
    assert recipe_lifecycle.park_nondivergent(conn) == 1
    assert recipe_lifecycle.park_nondivergent(conn) == 0
    assert recipe_lifecycle.get_status_version(conn, **_PARKED_KEY) == 8


def test_late_win_cannot_repromote_a_parked_recipe(tmp_path):
    """candidate -> parked -> late win. The trial is recorded as honest history
    but must not resurrect a deliberately non-divergent strategy.

    The trial is deliberately a COUNTABLE one (legacy grandfathered metrics, no
    provenance_complete key) so the parked guard is the only thing that can stop
    it. Stamping provenance_complete=True instead would make the trial fail the
    verifiability re-derivation against a store holding no such runs, and the
    test would pass for the wrong reason — it did, until that was caught."""
    conn = _conn(tmp_path)
    _seed_candidate(conn, _PARKED_KEY, version=7)
    recipe_lifecycle.park_nondivergent(conn)
    conn.execute(
        "INSERT INTO ab_trials (symptom_id, design_class, platform, strategy,"
        " verdict, metrics_json, arm_a_run_id, arm_b_run_id) VALUES (?,?,?,?,?,?,?,?)",
        (_PARKED_KEY["symptom_id"], _PARKED_KEY["design_class"],
         _PARKED_KEY["platform"], _PARKED_KEY["strategy"], "win",
         json.dumps({}), None, None))
    conn.commit()
    assert ab_runner.judge_recipe(conn, **_PARKED_KEY) is None
    assert recipe_lifecycle.get_status(conn, **_PARKED_KEY) == "parked"


# ── Arming the plan/judge staleness handshake (2026-07-19) ───────────────────

def _legacy_null_version_row(conn, key):
    conn.execute(
        "INSERT INTO recipe_status (symptom_id, design_class, platform, strategy,"
        " status, provenance, status_version, updated_at) VALUES (?,?,?,?,?,?,NULL,?)",
        (key["symptom_id"], key["design_class"], key["platform"], key["strategy"],
         "candidate", "legacy", "2026-07-19T00:00:00Z"))
    conn.commit()


def test_ensure_schema_arms_legacy_null_versions(tmp_path):
    """status_version shipped nullable and stayed NULL on every existing row, so
    the guard it exists for was DECORATIVE: the planner stamps an arm only
    `if _rsv is not None`, so an all-NULL column meant nothing was ever stamped
    and the judge's cancel could never fire. The committed store sat at 0 of 140
    versioned — shipped, tested, and inert."""
    conn = _conn(tmp_path)
    _legacy_null_version_row(conn, _PARKED_KEY)
    assert recipe_lifecycle.get_status_version(conn, **_PARKED_KEY) is None
    assert knowledge_db._migrate_arm_status_version(conn) == 1
    conn.commit()
    assert recipe_lifecycle.get_status_version(conn, **_PARKED_KEY) == 1


def test_arming_is_idempotent_and_leaves_lifecycle_alone(tmp_path):
    """A migration that churned the version every connect would cancel healthy
    trials on every drain; one that moved `status` would be a silent demotion."""
    conn = _conn(tmp_path)
    _legacy_null_version_row(conn, _PARKED_KEY)
    knowledge_db._migrate_arm_status_version(conn)
    conn.commit()
    assert knowledge_db._migrate_arm_status_version(conn) == 0
    assert recipe_lifecycle.get_status_version(conn, **_PARKED_KEY) == 1
    assert recipe_lifecycle.get_status(conn, **_PARKED_KEY) == "candidate"


def test_armed_row_makes_the_planner_stamp_and_the_judge_see_movement(tmp_path):
    """End-to-end semantics of 'armed': the planner's `if _rsv is not None` gate
    now passes, and a demotion landing after that plan changes the value the
    judge compares — which is exactly the cancel condition."""
    conn = _conn(tmp_path)
    _legacy_null_version_row(conn, _PARKED_KEY)
    knowledge_db._migrate_arm_status_version(conn)
    conn.commit()
    planned = recipe_lifecycle.get_status_version(conn, **_PARKED_KEY)
    assert planned is not None, "planner would still skip the stamp"
    recipe_lifecycle.demote(conn, reason="live_regression", **_PARKED_KEY)
    current = recipe_lifecycle.get_status_version(conn, **_PARKED_KEY)
    assert current is not None and current != planned, \
        "a lifecycle move between plan and judge must be visible"


def test_judge_cancels_only_on_a_stamped_arm(tmp_path):
    """Arming must not retroactively cancel in-flight work. The judge's cancel is
    gated on the ARM carrying a stamp, and no already-planned arm can gain one —
    at migration time this store had 1454 unjudged arm entries, none stamped, so
    every one stays grandfathered. This binds that gate so a future refactor
    cannot quietly drop it and cancel a whole in-flight round."""
    src = Path(el.__file__).read_text(encoding="utf-8")
    guard = [ln.strip() for ln in src.splitlines()
             if "_planned_sv" in ln and "is not None" in ln]
    assert guard, "the judge lost its unstamped-arm grandfather gate"


def test_absent_recipe_row_yields_no_version(tmp_path):
    """An arm whose recipe row was deleted must read None, not 0/1 — a spurious
    value would make the judge compare against a version that never existed."""
    conn = _conn(tmp_path)
    assert el._recipe_status_version(conn, dict(_PARKED_KEY)) is None


def test_parked_guard_does_not_block_ordinary_recipes(tmp_path, monkeypatch):
    """The guard keys on 'parked' only — a normal candidate still judges.

    The evidence must now be VERIFIABLE to drive a transition (P0-R3, 2026-07-20:
    untraceable legacy rows with NULL run_ids are quarantined), so this seeds a
    stamped trial with resolvable arms rather than the bare NULL-run_id row it
    used to rely on. The assertion under test is unchanged: a non-parked key
    reaches its corpus and transitions.
    """
    conn = _conn(tmp_path)
    key = dict(symptom_id="S2", design_class="logic/small",
               platform="nangate45", strategy="density_relief")
    _seed_candidate(conn, key, version=1)
    monkeypatch.setattr(ab_runner, "_runs_exist", lambda *a, **k: True)
    monkeypatch.setattr(ab_runner, "_arms_owned", lambda *a, **k: True)
    conn.execute(
        "INSERT INTO ab_trials (symptom_id, design_class, platform, strategy,"
        " verdict, metrics_json, arm_a_run_id, arm_b_run_id) VALUES (?,?,?,?,?,?,?,?)",
        (key["symptom_id"], key["design_class"], key["platform"], key["strategy"],
         "win", json.dumps({"provenance_complete": True}), "RID_A", "RID_B"))
    conn.commit()
    assert ab_runner.judge_recipe(conn, **key) == "promoted"
