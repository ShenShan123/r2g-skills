"""P1c exit conditions (user review 2026-10-09): capture identity and conflicting content.

- The capture record id is scoped to (design, platform, fix session, check, iteration, log time): equal session
  and iteration names in different designs or runs never merge; byte-identical copies of the same attempt do.
- The strategy label is NOT part of the identity: a relabelled copy of an attempt is the same attempt.
- Re-importing the same experience adds nothing; DIFFERENT content under the same identity is a conflict that
  aborts the import (all or nothing), never a silent skip.
"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tehm import db as tehm_db  # noqa: E402
from tehm.adapters.r2g_evidence import CaptureConflict  # noqa: E402
from tehm_backend import TehmMemoryBackend  # noqa: E402


def _row(it=1, ts="2026-10-01T00:00:01-07:00", session="seed_r1_x_catalogue", **kw):
    r = {"fix_session_id": session, "iter": it, "check": "drc", "strategy": "density_relief",
         "violation_class": "li.3", "from_stage": "floorplan", "before": 4, "after": 2, "verdict": "applied",
         "rolled_back": False, "config_delta": {"CORE_UTILIZATION": "8"}, "config_before": {"CORE_UTILIZATION": "12"}}
    if ts is not None:
        r["ts"] = ts
    r.update(kw)
    return r


def _project(root, dirname, design, rows):
    p = root / dirname
    (p / "constraints").mkdir(parents=True)
    (p / "reports").mkdir()
    (p / "constraints" / "config.mk").write_text(
        f"export DESIGN_NAME = {design}\nexport PLATFORM = sky130hs\nexport CORE_UTILIZATION = 12\n")
    (p / "reports" / "fix_log.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return p


@pytest.fixture
def backend(tmp_path):
    b = TehmMemoryBackend(db_path=tmp_path / "t.sqlite", artifact_root=tmp_path / "a")
    yield b
    b.close()


def _ids(tmp_path):
    with sqlite3.connect(tmp_path / "t.sqlite") as c:
        return sorted(r[0] for r in c.execute("SELECT record_id FROM tehm_attempts"))


def test_equal_session_and_iteration_in_different_designs_never_merge(tmp_path, backend):
    backend.ingest_project(_project(tmp_path, "a", "aes", [_row()]))
    backend.ingest_project(_project(tmp_path, "b", "sha", [_row()]))
    ids = _ids(tmp_path)
    assert len(ids) == 2 and len(set(ids)) == 2


def test_a_reused_session_name_from_another_run_is_another_attempt(tmp_path, backend):
    backend.ingest_project(_project(tmp_path, "run1", "aes", [_row(ts="2026-10-01T00:00:01-07:00", after=0, verdict="cleared")]))
    backend.ingest_project(_project(tmp_path, "run2", "aes", [_row(ts="2026-10-02T00:00:01-07:00", after=0, verdict="cleared")]))
    assert len(set(_ids(tmp_path))) == 2
    # identical measured content -> one content-addressed transition, but two attempts = two evidence units
    from tehm import evidence as ev
    with sqlite3.connect(tmp_path / "t.sqlite") as c:
        tid = c.execute("SELECT DISTINCT transition_id FROM tehm_attempts").fetchall()
        assert len(tid) == 1
        # a TEMP table shadows the store's: one rule whose own application is that transition
        c.execute("CREATE TEMP TABLE tehm_rule_sources (rule_id TEXT, episode_id TEXT, source_substitution_json TEXT)")
        c.execute("INSERT INTO tehm_rule_sources (rule_id, episode_id, source_substitution_json) VALUES ('r', 'e', ?)",
                  (json.dumps({tid[0][0]: {}}),))
        e = ev.rule_evidence(c, "r")
    assert e["units"] == 2 and e["positive"] == 2


def test_iterations_restarting_within_one_session_stay_distinct(tmp_path, backend):
    backend.ingest_project(_project(tmp_path, "a", "aes", [
        _row(it=1, ts="2026-10-01T00:00:01-07:00"), _row(it=1, ts="2026-10-01T00:09:00-07:00", after=1)]))
    assert len(set(_ids(tmp_path))) == 2


def test_a_byte_identical_copy_in_another_directory_adds_nothing(tmp_path, backend):
    backend.ingest_project(_project(tmp_path, "seed-d1", "aes", [_row(), _row(it=2, ts="2026-10-01T00:05:00-07:00")]))
    with sqlite3.connect(tmp_path / "t.sqlite") as c:
        w = tehm_db.read_watermark(c)
    assert backend.ingest_project(_project(tmp_path, "seed-d1b", "aes",
                                           [_row(), _row(it=2, ts="2026-10-01T00:05:00-07:00")])) == []
    assert len(_ids(tmp_path)) == 2
    with sqlite3.connect(tmp_path / "t.sqlite") as c:
        assert tehm_db.read_watermark(c) == w


@pytest.mark.parametrize("changed", [
    {"strategy": "llm_edit:CORE_UTILIZATION"},                          # a relabelled copy
    {"situation": {"v": "sit-v2", "check": "drc", "violation_class": "li.3"}},   # a re-derived annotation
    {"after": 0, "verdict": "cleared"},                                 # a different measured outcome
])
def test_different_content_under_the_same_identity_is_a_conflict_never_a_silent_skip(tmp_path, backend, changed):
    backend.ingest_project(_project(tmp_path, "seed-d1", "aes", [_row()]))
    with pytest.raises(CaptureConflict) as exc:
        backend.ingest_project(_project(tmp_path, "seed-d1c", "aes", [_row(it=2, ts="2026-10-01T00:05:00-07:00"),
                                                                       _row(**changed)]))
    msg = str(exc.value)
    assert sorted(changed)[0] in msg                                      # names the differing field(s)
    assert len(_ids(tmp_path)) == 1                                       # the whole batch was rolled back


def test_rows_without_a_log_time_merge_only_when_byte_identical(tmp_path, backend):
    backend.ingest_project(_project(tmp_path, "a", "aes", [_row(ts=None)]))
    assert backend.ingest_project(_project(tmp_path, "b", "aes", [_row(ts=None)])) == []
    with pytest.raises(CaptureConflict):
        backend.ingest_project(_project(tmp_path, "c", "aes", [_row(ts=None, after=3)]))


def test_provenance_carries_the_identity_scope_and_content_digest(tmp_path, backend):
    backend.ingest_project(_project(tmp_path, "a", "aes", [_row()]))
    with sqlite3.connect(tmp_path / "t.sqlite") as c:
        prov = json.loads(c.execute("SELECT provenance_json FROM tehm_transitions").fetchone()[0])
    scope = prov["record_scope"]
    assert (scope["design"], scope["platform"], scope["session"], scope["check"], scope["iter"], scope["ts"]) == \
        ("aes", "sky130hs", "seed_r1_x_catalogue", "drc", 1, "2026-10-01T00:00:01-07:00")
    assert prov["record_digest"].startswith("sha256:")
    with sqlite3.connect(tmp_path / "t.sqlite") as c:
        assert c.execute("SELECT record_digest FROM tehm_attempts").fetchone()[0] == prov["record_digest"]


def test_equal_session_names_in_different_designs_build_separate_episodes(tmp_path, backend):
    # episodes accumulate per session key; an unscoped key chained both designs' steps into one episode
    backend.ingest_project(_project(tmp_path, "a", "aes", [_row(after=0, verdict="cleared")]))
    backend.ingest_project(_project(tmp_path, "b", "sha", [_row(after=0, verdict="cleared")]))
    with sqlite3.connect(tmp_path / "t.sqlite") as c:
        chains = [json.loads(r[0]) for r in c.execute(
            "SELECT trajectory_summary_json FROM tehm_episodes")]
        steps = c.execute("SELECT episode_id, count(*) FROM tehm_episode_steps GROUP BY episode_id").fetchall()
    assert len(steps) == 2 and all(n == 1 for _, n in steps), (steps, chains)


def test_two_sessions_with_identical_step_content_are_two_episodes(tmp_path, backend):
    # content-addressed episodes collapsed such sessions (and refused them when terminal_status differed)
    backend.ingest_project(_project(tmp_path, "a", "aes", [_row(session="s1", after=0, verdict="cleared")]))
    backend.ingest_project(_project(tmp_path, "b", "aes", [
        _row(session="s2", ts="2026-10-03T00:00:00-07:00", after=0, verdict="cleared"),
        _row(session="s2", it=2, ts="2026-10-03T00:05:00-07:00", after=0, verdict="cleared")]))
    with sqlite3.connect(tmp_path / "t.sqlite") as c:
        sessions = {json.loads(p).get("session_id") for (p,) in c.execute("SELECT provenance_json FROM tehm_episodes")}
    assert sessions == {"aes:sky130hs:s1", "aes:sky130hs:s2"}
