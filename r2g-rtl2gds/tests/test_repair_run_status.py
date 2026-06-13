"""Tests for repair_run_status.py — reconcile dead orfs_status='partial' rows.

The one-time repair pass re-reads each project's latest
backend/RUN_*/stage_log.jsonl and re-derives orfs_status via the same
ingest_run helpers, updating the row only when the derived value differs.
It is read-from-stage-log only, idempotent, and backs up the DB to <db>.bak
before writing.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import knowledge_db
import repair_run_status


def _make_project(root: Path, name: str) -> Path:
    """A fixture project with clean signoff reports and an all-pass stage log."""
    proj = root / name
    (proj / "reports").mkdir(parents=True)
    (proj / "reports" / "drc.json").write_text(
        json.dumps({"status": "clean", "total_violations": 0}))
    (proj / "reports" / "lvs.json").write_text(json.dumps({"status": "clean"}))
    (proj / "reports" / "rcx.json").write_text(json.dumps({"status": "complete"}))
    run_dir = proj / "backend" / "RUN_2026-06-05_00-00-00"
    run_dir.mkdir(parents=True)
    # Record shape that ingest_run._derive_orfs_status parses as all-pass:
    # status == "pass" for each of the six required stages.
    stages = ["synth", "floorplan", "place", "cts", "route", "finish"]
    with (run_dir / "stage_log.jsonl").open("w") as fh:
        for st in stages:
            fh.write(json.dumps({"stage": st, "status": "pass", "elapsed_s": 1}) + "\n")
    return proj


def _make_failing_project(root: Path, name: str, fail_stage: str = "place",
                          err_line: str | None = None) -> Path:
    """A fixture project whose latest stage log aborts at ``fail_stage``.

    Uses the production INT exit-code shape (status 0 == pass, nonzero == fail)
    so the test exercises the same `_norm_stage_status` path the live writer hits.
    When ``err_line`` is given it is written to the RUN dir's flow.log so the
    `[ERROR XXX-0000]` detail path is covered too.
    """
    proj = root / name
    run_dir = proj / "backend" / "RUN_2026-06-05_00-00-00"
    run_dir.mkdir(parents=True)
    order = ["synth", "floorplan", "place", "cts", "route", "finish"]
    with (run_dir / "stage_log.jsonl").open("w") as fh:
        for st in order:
            if st == fail_stage:
                fh.write(json.dumps({"stage": st, "status": 1, "elapsed_s": 1}) + "\n")
                break
            fh.write(json.dumps({"stage": st, "status": 0, "elapsed_s": 1}) + "\n")
    if err_line is not None:
        (run_dir / "flow.log").write_text(err_line + "\n")
    return proj


def _orfs_fail_events(conn: sqlite3.Connection, run_id: str) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return conn.execute(
        "SELECT stage, signature, detail FROM failure_events "
        "WHERE run_id=? AND signature LIKE 'orfs-fail-%' ORDER BY id",
        (run_id,),
    ).fetchall()


def _new_db(tmp_knowledge_dir: Path) -> Path:
    db_path = tmp_knowledge_dir / "knowledge.sqlite"
    conn = knowledge_db.connect(db_path)
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    conn.close()
    return db_path


def _insert_run(conn: sqlite3.Connection, run_id: str, project: Path,
                status: str = "partial") -> None:
    conn.execute(
        "INSERT INTO runs (run_id, project_path, design_name, ingested_at, "
        "orfs_status, orfs_fail_stage) VALUES (?,?,?,?,?,?)",
        (run_id, str(project.resolve()), project.name,
         "2026-06-05T00:00:00Z", status, "route"),
    )
    conn.commit()


def test_repair_flips_partial_to_pass(tmp_knowledge_dir, tmp_path):
    db_path = _new_db(tmp_knowledge_dir)
    proj = _make_project(tmp_path / "cases", "good_design")

    conn = knowledge_db.connect(db_path)
    _insert_run(conn, "run1", proj, status="partial")

    repair_run_status.repair(tmp_path / "cases", conn)

    row = conn.execute(
        "SELECT orfs_status, orfs_fail_stage FROM runs WHERE run_id='run1'"
    ).fetchone()
    assert row[0] == "pass"
    assert row[1] is None
    conn.close()


def test_repair_is_idempotent(tmp_knowledge_dir, tmp_path):
    db_path = _new_db(tmp_knowledge_dir)
    proj = _make_project(tmp_path / "cases", "good_design")

    conn = knowledge_db.connect(db_path)
    _insert_run(conn, "run1", proj, status="partial")

    repair_run_status.repair(tmp_path / "cases", conn)
    first = conn.execute(
        "SELECT orfs_status FROM runs WHERE run_id='run1'").fetchone()[0]
    # Second pass must change nothing.
    repair_run_status.repair(tmp_path / "cases", conn)
    second = conn.execute(
        "SELECT orfs_status FROM runs WHERE run_id='run1'").fetchone()[0]
    assert first == second == "pass"
    conn.close()


def test_repair_leaves_unresolvable_rows_untouched(tmp_knowledge_dir, tmp_path):
    """A row whose project dir is missing keeps its original status."""
    db_path = _new_db(tmp_knowledge_dir)
    missing = tmp_path / "cases" / "gone"

    conn = knowledge_db.connect(db_path)
    _insert_run(conn, "run_gone", missing, status="partial")

    repair_run_status.repair(tmp_path / "cases", conn)

    status = conn.execute(
        "SELECT orfs_status FROM runs WHERE run_id='run_gone'").fetchone()[0]
    assert status == "partial"
    conn.close()


def test_main_creates_backup(tmp_knowledge_dir, tmp_path, capsys):
    db_path = _new_db(tmp_knowledge_dir)
    proj = _make_project(tmp_path / "cases", "good_design")

    conn = knowledge_db.connect(db_path)
    _insert_run(conn, "run1", proj, status="partial")
    conn.close()

    repair_run_status.main([
        "--db", str(db_path),
        "--cases-root", str(tmp_path / "cases"),
    ])

    bak = Path(str(db_path) + ".bak")
    assert bak.exists()

    # The histogram is printed and the row was repaired on disk.
    out = capsys.readouterr().out
    assert "orfs_status" in out
    conn = knowledge_db.connect(db_path)
    status = conn.execute(
        "SELECT orfs_status FROM runs WHERE run_id='run1'").fetchone()[0]
    assert status == "pass"
    conn.close()


# --- failure_events reconciliation -------------------------------------------
# repair() flips orfs_status='partial' -> 'fail' from the stage log, but the
# `failure_events` projection (what the learner / escalation / search_failures
# actually consume) is maintained only by the LIVE ingest path. A repair that
# updates orfs_status without re-emitting the orfs-fail-<stage> event leaves the
# store silently inconsistent: the run says 'fail' yet is invisible as a failure.
# These tests pin the dual-write so a reconciled status is as visible as a live one.


def test_repair_partial_to_fail_emits_failure_event(tmp_knowledge_dir, tmp_path):
    db_path = _new_db(tmp_knowledge_dir)
    proj = _make_failing_project(
        tmp_path / "cases", "broke_design", fail_stage="place",
        err_line="[ERROR PPL-0024] Number of IO pins (1523) exceeds maximum")

    conn = knowledge_db.connect(db_path)
    _insert_run(conn, "run1", proj, status="partial")

    repair_run_status.repair(tmp_path / "cases", conn)

    row = conn.execute(
        "SELECT orfs_status, orfs_fail_stage FROM runs WHERE run_id='run1'").fetchone()
    assert row[0] == "fail" and row[1] == "place"
    events = _orfs_fail_events(conn, "run1")
    assert len(events) == 1
    # The tool's [ERROR XXX-0000] code is folded into the signature, the full
    # line into the detail — same shape ingest_run writes live.
    assert events[0]["signature"] == "orfs-fail-place-PPL-0024"
    assert events[0]["stage"] == "place"
    assert "PPL-0024" in (events[0]["detail"] or "")
    conn.close()


def test_repair_backfills_failure_event_for_already_fail_row(tmp_knowledge_dir, tmp_path):
    """The exact production gap: a row reconciled to 'fail' by an *earlier*
    repair (before this fix) is already 'fail' with the right stage but has NO
    failure_event. A re-run must backfill it even though orfs_status is unchanged."""
    db_path = _new_db(tmp_knowledge_dir)
    # Project dir is GONE (historical nangate45 run) — repair can't read a stage
    # log, so the backfill must come from the runs columns alone.
    missing = tmp_path / "cases" / "long_gone"

    conn = knowledge_db.connect(db_path)
    conn.execute(
        "INSERT INTO runs (run_id, project_path, design_name, ingested_at, "
        "orfs_status, orfs_fail_stage) VALUES (?,?,?,?,?,?)",
        ("run_old", str(missing.resolve()), "long_gone",
         "2026-06-05T00:00:00Z", "fail", "floorplan"),
    )
    conn.commit()
    assert _orfs_fail_events(conn, "run_old") == []  # the bug state

    repair_run_status.repair(tmp_path / "cases", conn)

    events = _orfs_fail_events(conn, "run_old")
    assert len(events) == 1
    assert events[0]["signature"] == "orfs-fail-floorplan"  # no flow.log -> no code
    assert events[0]["stage"] == "floorplan"
    assert events[0]["detail"] is None
    conn.close()


def test_repair_failure_event_is_idempotent(tmp_knowledge_dir, tmp_path):
    db_path = _new_db(tmp_knowledge_dir)
    proj = _make_failing_project(tmp_path / "cases", "broke_design", fail_stage="route")

    conn = knowledge_db.connect(db_path)
    _insert_run(conn, "run1", proj, status="partial")

    repair_run_status.repair(tmp_path / "cases", conn)
    repair_run_status.repair(tmp_path / "cases", conn)  # second pass: no dupes

    events = _orfs_fail_events(conn, "run1")
    assert len(events) == 1
    assert events[0]["signature"] == "orfs-fail-route"
    conn.close()


def test_repair_clears_stale_failure_event_on_downgrade(tmp_knowledge_dir, tmp_path):
    """A row that was 'fail' but whose stage log now derives 'pass' must lose its
    stale orfs-fail event (else the learner sees a phantom failure)."""
    db_path = _new_db(tmp_knowledge_dir)
    proj = _make_project(tmp_path / "cases", "now_good")  # all-pass stage log

    conn = knowledge_db.connect(db_path)
    _insert_run(conn, "run1", proj, status="fail")
    conn.execute(
        "INSERT INTO failure_events (run_id, stage, signature, detail) "
        "VALUES (?,?,?,?)", ("run1", "route", "orfs-fail-route", "stale"))
    conn.commit()

    repair_run_status.repair(tmp_path / "cases", conn)

    assert conn.execute(
        "SELECT orfs_status FROM runs WHERE run_id='run1'").fetchone()[0] == "pass"
    assert _orfs_fail_events(conn, "run1") == []
    conn.close()


def test_repair_does_not_clobber_older_run_of_multirun_project(tmp_knowledge_dir, tmp_path):
    """A project re-run after a fix has TWO rows (old fail + new clean) sharing one
    project_path; only the latest run's stage_log survives on disk. Repair must
    reconcile ONLY the latest-ingested row and leave the older fail row — and its
    failure_event — exactly as the live ingest recorded them. (Regression: the
    tool used to re-derive every row from the latest stage_log, flipping the
    historical failure to 'pass' and deleting its event.)"""
    db_path = _new_db(tmp_knowledge_dir)
    proj = _make_project(tmp_path / "cases", "rerun_design")  # on-disk log = all-pass

    conn = knowledge_db.connect(db_path)
    # Older run: failed at route, ingested earlier, with its orfs-fail event.
    conn.execute(
        "INSERT INTO runs (run_id, project_path, design_name, ingested_at, "
        "orfs_status, orfs_fail_stage) VALUES (?,?,?,?,?,?)",
        ("run_old", str(proj.resolve()), "rerun_design",
         "2026-06-13T01:00:00Z", "fail", "route"),
    )
    conn.execute(
        "INSERT INTO failure_events (run_id, stage, signature, detail) VALUES (?,?,?,?)",
        ("run_old", "route", "orfs-fail-route-GRT-0116", "[ERROR GRT-0116] congestion"))
    # Newer run: the clean re-run, ingested later (matches the on-disk stage_log).
    conn.execute(
        "INSERT INTO runs (run_id, project_path, design_name, ingested_at, "
        "orfs_status, orfs_fail_stage) VALUES (?,?,?,?,?,?)",
        ("run_new", str(proj.resolve()), "rerun_design",
         "2026-06-13T05:00:00Z", "partial", None),
    )
    conn.commit()

    repair_run_status.repair(tmp_path / "cases", conn)

    old = conn.execute(
        "SELECT orfs_status, orfs_fail_stage FROM runs WHERE run_id='run_old'").fetchone()
    new = conn.execute(
        "SELECT orfs_status FROM runs WHERE run_id='run_new'").fetchone()
    # Older fail row + its event untouched; newer row reconciled to pass.
    assert old[0] == "fail" and old[1] == "route"
    assert len(_orfs_fail_events(conn, "run_old")) == 1
    assert _orfs_fail_events(conn, "run_old")[0]["signature"] == "orfs-fail-route-GRT-0116"
    assert new[0] == "pass"
    conn.close()


def test_repair_backfills_older_run_event_additively(tmp_knowledge_dir, tmp_path):
    """An OLDER run of a multi-run project that failed but carries NO event gets a
    bare orfs-fail-<stage> from its stored columns (additive), while its status is
    left untouched. The latest run is reconciled from the on-disk log as usual."""
    db_path = _new_db(tmp_knowledge_dir)
    proj = _make_project(tmp_path / "cases", "multirun2")  # on-disk log = all-pass

    conn = knowledge_db.connect(db_path)
    conn.execute(
        "INSERT INTO runs (run_id, project_path, design_name, ingested_at, "
        "orfs_status, orfs_fail_stage) VALUES (?,?,?,?,?,?)",
        ("old_cts", str(proj.resolve()), "multirun2",
         "2026-05-21T00:00:00Z", "fail", "cts"),
    )  # older fail, no event
    conn.execute(
        "INSERT INTO runs (run_id, project_path, design_name, ingested_at, "
        "orfs_status, orfs_fail_stage) VALUES (?,?,?,?,?,?)",
        ("new_ok", str(proj.resolve()), "multirun2",
         "2026-06-13T00:00:00Z", "partial", None),
    )
    conn.commit()

    repair_run_status.repair(tmp_path / "cases", conn)

    old = conn.execute(
        "SELECT orfs_status FROM runs WHERE run_id='old_cts'").fetchone()[0]
    assert old == "fail"  # status untouched
    ev = _orfs_fail_events(conn, "old_cts")
    assert len(ev) == 1 and ev[0]["signature"] == "orfs-fail-cts"
    assert ev[0]["detail"] is None  # no flow.log for an older run -> bare event
    assert conn.execute(
        "SELECT orfs_status FROM runs WHERE run_id='new_ok'").fetchone()[0] == "pass"
    conn.close()


def test_repair_preserves_non_orfs_failure_events(tmp_knowledge_dir, tmp_path):
    """Diagnosis-derived events (synthesis_errors, unconstrained_timing, ...) for
    the same run must survive — reconciliation only owns 'orfs-fail-%' signatures."""
    db_path = _new_db(tmp_knowledge_dir)
    proj = _make_failing_project(tmp_path / "cases", "broke_design", fail_stage="cts")

    conn = knowledge_db.connect(db_path)
    _insert_run(conn, "run1", proj, status="partial")
    conn.execute(
        "INSERT INTO failure_events (run_id, stage, signature, detail) "
        "VALUES (?,?,?,?)", ("run1", None, "synthesis_errors", "kept"))
    conn.commit()

    repair_run_status.repair(tmp_path / "cases", conn)

    kept = conn.execute(
        "SELECT count(*) FROM failure_events WHERE run_id='run1' "
        "AND signature='synthesis_errors'").fetchone()[0]
    assert kept == 1
    assert len(_orfs_fail_events(conn, "run1")) == 1
    conn.close()
