"""Guards tools/check_db_integrity.py — the BOTH-DBs cross-check that proves the
journal + knowledge stores agree after every move (driven by /r2g-debug each wave).

Severity contract under test (the reason this tool exists alongside honesty.py):
  * knowledge.sqlite is the source of truth -> its dishonesty is ALARM (exit 1).
  * journal.sqlite is a best-effort ledger -> a move it failed to record is WARN
    (exit 0): a lead to chase, never a corrupted lesson.

The knowledge-side gates are NOT re-implemented here; the tool imports
knowledge/honesty.py::run_all, so this test also pins that delegation (a fail run
with no failure_event must ALARM, exactly as honesty.py's own test asserts)."""
import importlib.util
import sqlite3
from pathlib import Path

import knowledge_db        # on sys.path via tests/conftest.py
import journal_db

ROOT = Path(__file__).resolve().parents[2]
_TOOL = ROOT / "tools" / "check_db_integrity.py"

_spec = importlib.util.spec_from_file_location("check_db_integrity", _TOOL)
cdi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cdi)


# --------------------------------------------------------------------------- #
# fixture builders — real schema, real journal writer                          #
# --------------------------------------------------------------------------- #
def _kdb(tmp_path, *, runs=(), fail_events=(), ab_trials=(), recipes=(), escalations=()):
    """runs: (run_id, project_path, orfs_status, orfs_fail_stage, platform)."""
    p = tmp_path / "knowledge.sqlite"
    con = knowledge_db.connect(p)
    knowledge_db.ensure_schema(con)
    for rid, proj, status, fstage, plat in runs:
        con.execute("INSERT INTO runs (run_id, project_path, ingested_at, orfs_status,"
                    " orfs_fail_stage, platform) VALUES (?,?,?,?,?,?)",
                    (rid, proj, "2026-06-30T00:00:00", status, fstage, plat))
    for rid, sig in fail_events:
        con.execute("INSERT INTO failure_events (run_id, signature) VALUES (?,?)", (rid, sig))
    for sym, dclass, plat, strat, verdict in ab_trials:
        con.execute("INSERT INTO ab_trials (symptom_id, design_class, platform, strategy,"
                    " verdict) VALUES (?,?,?,?,?)", (sym, dclass, plat, strat, verdict))
    for sym, dclass, plat, strat, status in recipes:
        con.execute("INSERT INTO recipe_status (symptom_id, design_class, platform, strategy,"
                    " status) VALUES (?,?,?,?,?)", (sym, dclass, plat, strat, status))
    for sym, reason, status in escalations:
        con.execute("INSERT INTO escalations (symptom_id, reason, status) VALUES (?,?,?)",
                    (sym, reason, status))
    con.commit()
    con.close()
    return p


def _jdb(tmp_path, actions=()):
    """actions: (project_path, action_type, run_id, symptom_id)."""
    p = tmp_path / "journal.sqlite"
    con = journal_db.connect(p)
    journal_db.ensure_schema(con)
    for proj, atype, rid, sym in actions:
        journal_db.append_action(con, project_path=proj, actor="loop",
                                 action_type=atype, run_id=rid, symptom_id=sym)
    con.close()
    return p


def _run(kdb, jdb, *extra):
    return cdi.main(["--kdb", str(kdb), "--jdb", str(jdb), *extra])


# --------------------------------------------------------------------------- #
def test_consistent_stores_pass(tmp_path, capsys):
    """A fail run with its event + an ab_launch'd/promoted recipe whose moves are
    journaled = no ALARM, no WARN."""
    kdb = _kdb(
        tmp_path,
        runs=[("R1", "/p/clean", "pass", None, "nangate45"),
              ("R2", "/p/abort", "fail", "place", "nangate45")],
        fail_events=[("R2", "orfs-fail-place-DPL-0024")],
        ab_trials=[("S1", "logic/small", "nangate45", "core_util_relief", "win")],
        recipes=[("S1", "logic/small", "nangate45", "core_util_relief", "promoted")],
    )
    jdb = _jdb(tmp_path, actions=[
        ("/p/abort", "ab_launch", "R2", "S1"),   # links project /p/abort -> J2 ok, J4 ok
        ("/p/abort", "promote", None, "S1"),      # the promotion move IS in the ledger
    ])
    rc = _run(kdb, jdb)
    out = capsys.readouterr().out
    assert rc == 0
    assert "verdict: PASS" in out
    assert "[ALARM]" not in out and "[warn]" not in out


def test_fail_run_without_event_alarms(tmp_path, capsys):
    """Delegated honesty: a fail run missing its failure_event is a HARD knowledge lie."""
    kdb = _kdb(tmp_path,
               runs=[("R1", "/p/x", "fail", "place", "nangate45")],
               fail_events=[],   # the lie
               ab_trials=[("S", "c", "nangate45", "s", "win")])  # satisfy K2 so the ALARM is H, not K2
    jdb = _jdb(tmp_path, actions=[("/p/x", "stage_rerun", "R1", "S")])
    rc = _run(kdb, jdb)
    out = capsys.readouterr().out
    assert rc == 1
    assert "[ALARM]" in out and "H:every_fail_has_event" in out


def test_unlinked_journal_actions_alarm_J2(tmp_path, capsys):
    """A project with a knowledge run AND journal actions but ZERO back-filled run_id:
    ingest never linked the ledger to the result -> HARD."""
    kdb = _kdb(tmp_path, runs=[("R1", "/p/x", "pass", None, "nangate45")])
    jdb = _jdb(tmp_path, actions=[("/p/x", "tool_invoke", None, None)])  # run_id never back-filled
    rc = _run(kdb, jdb)
    out = capsys.readouterr().out
    assert rc == 1
    assert "ALARM" in out and "J2" in out


def test_promote_without_action_warns_L2(tmp_path, capsys):
    """A promoted recipe whose symptom has no `promote` action: knowledge committed a
    move the ledger never recorded. WARN (trend), not ALARM — knowledge is honest."""
    kdb = _kdb(tmp_path,
               runs=[("R1", "/p/x", "pass", None, "nangate45")],
               recipes=[("S9", "logic/small", "nangate45", "core_util_relief", "promoted")])
    jdb = _jdb(tmp_path, actions=[("/p/x", "tool_invoke", "R1", None)])  # alive, linked, but no promote
    rc = _run(kdb, jdb)
    out = capsys.readouterr().out
    assert rc == 0
    assert "[warn] L2" in out
    assert "[ALARM]" not in out


def test_dangling_journal_run_id_warns_J4(tmp_path, capsys):
    """A journal action whose run_id is absent from knowledge.runs (wiped/re-ingested
    project): cross-DB referential break, but benign residue -> WARN."""
    kdb = _kdb(tmp_path, runs=[("R1", "/p/x", "pass", None, "nangate45")])
    jdb = _jdb(tmp_path, actions=[
        ("/p/x", "tool_invoke", "R1", None),       # keeps /p/x linked so J2 stays clean
        ("/p/gone", "tool_invoke", "GHOST", None),  # GHOST not in runs -> J4
    ])
    rc = _run(kdb, jdb)
    out = capsys.readouterr().out
    assert rc == 0
    assert "[warn] J4" in out
    assert "[ALARM]" not in out


def test_platform_scope_isolates_correspondence(tmp_path, capsys):
    """--platform must scope L1/L2: a sky130hd-only ledger gap is invisible when the
    run under test is nangate45 (the campaign /r2g-debug drives)."""
    kdb = _kdb(tmp_path,
               runs=[("R1", "/p/x", "pass", None, "nangate45")],
               recipes=[("Ssky", "logic/small", "sky130hd", "density_relief", "promoted")])
    jdb = _jdb(tmp_path, actions=[("/p/x", "tool_invoke", "R1", None)])  # no promote for Ssky
    # global: L2 warns about the sky130hd gap
    assert _run(kdb, jdb) == 0
    assert "[warn] L2" in capsys.readouterr().out
    # nangate45-scoped: that gap is out of scope -> L2 clean
    assert _run(kdb, jdb, "--platform", "nangate45") == 0
    assert "[ok  ] L2" in capsys.readouterr().out


def test_missing_journal_is_warn_not_alarm(tmp_path, capsys):
    """A fresh clone has no journal (gitignored). Honesty still runs; cross-DB is just
    not verifiable -> WARN, exit 0 (never a false ALARM)."""
    kdb = _kdb(tmp_path, runs=[("R1", "/p/x", "pass", None, "nangate45")])
    rc = cdi.main(["--kdb", str(kdb), "--jdb", str(tmp_path / "nope.sqlite")])
    out = capsys.readouterr().out
    assert rc == 0
    assert "J*" in out and "journal DB absent" in out
