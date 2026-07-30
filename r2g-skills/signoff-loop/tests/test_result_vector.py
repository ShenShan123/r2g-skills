"""RMD3-P0-01 (failure-patterns.md #58): the global result-vector comparator.

A live repair must never be recorded as a win when ANY globally-protected signal
regresses. The sky130hs SHA-256 pilot case: `density_relief` improved DRC 10->8,
route went 0->32 and LVS broke — yet fix_log said `applied` and ingest mapped it
to `win`. These tests cover the shared comparator (knowledge/result_vector.py),
the fix_signoff.sh live wiring (verdict override + config revert), and the
ingest-side belt-and-braces downgrades.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import ingest_run
import knowledge_db
import result_vector as rv

SKILL = Path(__file__).resolve().parents[1]
FIX_SIGNOFF = SKILL / "scripts" / "flow" / "fix_signoff.sh"


# ── unit: compare() ───────────────────────────────────────────────────────────

def _vec(**signals):
    base = {
        "orfs": {"status": None, "fresh": False},
        "route": {"status": None, "total_violations": None, "fresh": False},
        "drc": {"status": None, "total_violations": None, "classes": {}, "fresh": False},
        "lvs": {"status": None, "mismatch_count": None, "mismatch_class": None, "fresh": False},
        "timing": {"tier": None, "wns_ns": None, "clock_period_ns": None, "fresh": False},
        "rcx": {"status": None, "fresh": False},
    }
    for k, v in signals.items():
        base[k].update(v)
    return {"vector_version": rv.VECTOR_VERSION, "signals": base,
            "layout": {}, "project": "p", "captured_at": "t"}


def test_route_count_increase_is_regression():
    pre = _vec(route={"status": "clean", "total_violations": 0, "fresh": True})
    post = _vec(route={"status": "fail", "total_violations": 32, "fresh": True})
    out = rv.compare(pre, post, "drc")
    assert out["gate"] == "regression"
    assert "route_regression:0->32" in out["regressions"]


def test_stale_post_signal_is_unknown_never_regression():
    pre = _vec(lvs={"status": "clean", "mismatch_count": 0, "fresh": True})
    post = _vec(lvs={"status": "fail", "mismatch_count": 9, "fresh": False})
    out = rv.compare(pre, post, "drc")
    assert out["gate"] == "ok"
    assert "lvs:unmeasured_post" in out["unknowns"]


def test_lvs_flip_and_orfs_partial_are_regressions():
    pre = _vec(lvs={"status": "clean", "mismatch_count": 0, "fresh": True},
               orfs={"status": "complete", "fresh": True})
    post = _vec(lvs={"status": "fail", "mismatch_count": 3, "fresh": True},
                orfs={"status": "partial", "fresh": True})
    out = rv.compare(pre, post, "drc")
    assert "lvs_regression:clean->fail" in out["regressions"]
    assert "orfs_regression:complete->partial" in out["regressions"]


def test_timing_tier_and_constraint_protection():
    pre = _vec(timing={"tier": "clean", "clock_period_ns": 2.0, "fresh": True})
    post = _vec(timing={"tier": "severe", "clock_period_ns": 3.0, "fresh": True})
    out = rv.compare(pre, post, "drc")
    assert any(r.startswith("timing_regression:clean->severe") for r in out["regressions"])
    assert any(r.startswith("constraint_relaxed:clock_period_ns") for r in out["regressions"])
    # A timing-target repair legitimately edits the period: no constraint veto,
    # and tier improvement is not a regression.
    pre2 = _vec(timing={"tier": "severe", "clock_period_ns": 2.0, "fresh": True})
    post2 = _vec(timing={"tier": "clean", "clock_period_ns": 3.0, "fresh": True})
    assert rv.compare(pre2, post2, "timing")["gate"] == "ok"


def test_improvement_with_no_side_damage_is_ok():
    pre = _vec(drc={"status": "fail", "total_violations": 10,
                    "classes": {"li.3": 10}, "fresh": True},
               route={"status": "clean", "total_violations": 0, "fresh": True})
    post = _vec(drc={"status": "fail", "total_violations": 8,
                     "classes": {"li.3": 8}, "fresh": True},
                route={"status": "clean", "total_violations": 0, "fresh": True})
    out = rv.compare(pre, post, "drc")
    assert out["gate"] == "ok" and not out["regressions"]


def test_new_drc_class_materiality_matches_ab_policy():
    # Below pre-total: benign visibility, no veto (P0-13 materiality).
    assert rv.new_class_regression({"li.3": 10}, {"m3.2": 8}) is None
    # Above pre-total: materially worse, veto names the class.
    veto = rv.new_class_regression({"li.3": 1}, {"m3.2": 8})
    assert veto == "new_drc_class:m3.2"


def test_compare_status_rows_parity_with_ab_veto():
    a = {"orfs": "pass", "drc": "clean", "lvs": "clean", "tier": "clean"}
    assert rv.compare_status_rows(a, dict(a, orfs="fail")) == "orfs_regression:pass->fail"
    assert rv.compare_status_rows(a, dict(a, lvs="fail")) == "lvs_regression:clean->fail"
    assert rv.compare_status_rows(a, dict(a, lvs=None)) == "check_missing:lvs"
    assert rv.compare_status_rows(a, dict(a, drc="stuck")) == "drc_regression:clean->stuck"
    assert "timing_regression" in rv.compare_status_rows(a, dict(a, tier="severe"))
    assert rv.compare_status_rows(a, dict(a)) is None
    # no-signal statuses never fire
    b = {"orfs": None, "drc": "skipped", "lvs": "", "tier": None}
    assert rv.compare_status_rows(b, b) is None


# ── capture(): freshness binding ─────────────────────────────────────────────

def test_capture_binds_freshness_to_newest_run(tmp_path):
    proj = tmp_path / "p"
    (proj / "reports").mkdir(parents=True)
    run = proj / "backend" / "RUN_B" / "results"
    run.mkdir(parents=True)
    (run / "6_final.def").write_text("DESIGN d ;\n")
    (proj / "reports" / "route.json").write_text(json.dumps(
        {"status": "fail", "total_violations": 32, "backend_run": "RUN_B"}))
    (proj / "reports" / "lvs.json").write_text(json.dumps(
        {"status": "clean", "mismatch_count": 0, "run_tag": "RUN_A"}))  # stale
    vec = rv.capture(proj)
    assert vec["layout"]["run_tag"] == "RUN_B"
    assert vec["signals"]["route"]["fresh"] is True
    assert vec["signals"]["lvs"]["fresh"] is False
    assert vec["signals"]["drc"]["fresh"] is False   # absent report


# ── live replay: the sky130hs SHA-256 acceptance case ────────────────────────

DEF_A = "DESIGN demo ;\nCOMPONENTS 10 ;\nEND DESIGN\n"
DEF_B = "DESIGN demo ;\nCOMPONENTS 11 ;\nEND DESIGN\n"


def _stub(path: Path, body: str):
    path.write_text("#!/usr/bin/env bash\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _newest_tag_snippet():
    # First arg is the project dir; echo the newest backend RUN tag.
    return 'tag="$(ls -t "$1/backend" | head -1)"\n'


def _replay_project(tmp_path):
    proj = tmp_path / "proj"
    (proj / "reports").mkdir(parents=True)
    (proj / "constraints").mkdir()
    (proj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = demo\nexport PLATFORM = sky130hs\n")
    run0 = proj / "backend" / "RUN_A" / "results"
    run0.mkdir(parents=True)
    (run0 / "6_final.def").write_text(DEF_A)
    (proj / "reports" / "drc.json").write_text(json.dumps(
        {"status": "fail", "total_violations": 10,
         "categories": {"li.3": {"count": 10}}, "run_tag": "RUN_A"}))
    (proj / "reports" / "lvs.json").write_text(json.dumps(
        {"status": "clean", "mismatch_count": 0, "run_tag": "RUN_A"}))
    return proj


def _replay_stubs(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    # density_relief once; STOP when it is excluded (the loop's `tried` list).
    _stub(bindir / "diagnose.py",
          'if [[ "$*" == *"--next"* ]]; then\n'
          '  if [[ "$*" == *density_relief* ]]; then echo -e "STOP\\tno_strategy"; \n'
          '  else echo -e "density_relief\\tfloorplan\\tdrc"; fi\n'
          'elif [[ "$*" == *"--apply"* ]]; then\n'
          '  echo "export PLACE_DENSITY_LB_ADDON = 0.20  # r2g-fix" >> "$1/constraints/config.mk"\n'
          '  echo "{\\"config_edits\\": {\\"PLACE_DENSITY_LB_ADDON\\": \\"0.20\\"}}"\nfi')
    # The regressive reflow: a NEW run with DIFFERENT layout bytes.
    _stub(bindir / "run_orfs.sh",
          'd="$1/backend/RUN_B"\nmkdir -p "$d/results"\n'
          'printf %s "' + DEF_B.replace("\n", "\\n") + '" > "$d/results/6_final.def"')
    _stub(bindir / "run_drc.sh", 'exit 0')
    _stub(bindir / "noop.sh", 'exit 0')
    # Extractors report per-run truth: RUN_A was route-clean / DRC 10;
    # RUN_B has DRC 8 (the "improvement") and 32 route violations (the damage).
    _stub(bindir / "extract_drc.py",
          _newest_tag_snippet() +
          'if [[ "$tag" == "RUN_B" ]]; then n=8; else n=10; fi\n'
          'cat > "$2" <<EOF\n'
          '{"status": "fail", "total_violations": $n,'
          ' "categories": {"li.3": {"count": $n}}, "run_tag": "$tag"}\nEOF')
    _stub(bindir / "extract_route.py",
          _newest_tag_snippet() +
          'if [[ "$tag" == "RUN_B" ]]; then\n'
          '  echo "{\\"status\\": \\"fail\\", \\"total_violations\\": 32, \\"backend_run\\": \\"RUN_B\\"}" > "$2"\n'
          'else\n'
          '  echo "{\\"status\\": \\"clean\\", \\"total_violations\\": 0, \\"backend_run\\": \\"RUN_A\\"}" > "$2"\n'
          'fi')
    _stub(bindir / "extract_ppa.py",
          _newest_tag_snippet() +
          'echo "{\\"orfs_status\\": \\"complete\\", \\"run_dir\\": \\"$1/backend/$tag\\"}" > "$2"')
    return bindir


def _replay_env(bindir):
    return dict(os.environ,
                R2G_JOURNAL="0",
                R2G_DIAGNOSE=str(bindir / "diagnose.py"),
                R2G_RUN_ORFS=str(bindir / "run_orfs.sh"),
                R2G_RUN_DRC=str(bindir / "run_drc.sh"),
                R2G_RUN_LVS=str(bindir / "noop.sh"),
                R2G_EXTRACT_DRC=str(bindir / "extract_drc.py"),
                R2G_EXTRACT_LVS=str(bindir / "noop.sh"),
                R2G_EXTRACT_ROUTE=str(bindir / "extract_route.py"),
                R2G_EXTRACT_PPA=str(bindir / "extract_ppa.py"),
                R2G_CHECK_TIMING=str(bindir / "noop.sh"))


def test_replay_sky130hs_density_relief_is_regression_not_win(tmp_path):
    """Acceptance (remediation plan RMD3-P0-01): replaying the sky130hs SHA-256
    transition — target DRC 10->8 while route 0->32 — must yield verdict
    `regression`, revert the config edit, and never ingest a win."""
    proj = _replay_project(tmp_path)
    bindir = _replay_stubs(tmp_path)
    r = subprocess.run(["bash", str(FIX_SIGNOFF), str(proj), "sky130hs",
                        "--check", "drc"],
                       env=_replay_env(bindir), capture_output=True, text=True,
                       timeout=180)
    assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)  # residual remains

    rows = [json.loads(l) for l in
            (proj / "reports" / "fix_log.jsonl").read_text().splitlines() if l.strip()]
    it1 = next(row for row in rows if row.get("iter") == 1)
    assert it1["verdict"] == "regression", rows
    assert any(g.startswith("route_regression:0->32")
               for g in it1["global_regressions"]), it1
    assert "GLOBAL REGRESSION" in r.stdout
    # The regressive config edit was reverted to the last accepted state.
    cfg = (proj / "constraints" / "config.mk").read_text()
    assert "PLACE_DENSITY_LB_ADDON" not in cfg
    # Audit evidence quarantined.
    audit = json.loads((proj / "reports" / "global_regression_it1.json").read_text())
    assert audit["strategy"] == "density_relief" and audit["regressions"]

    # Ingest: the event lands as `regression` — no positive evidence anywhere.
    db = tmp_path / "k.sqlite"
    conn = knowledge_db.connect(db)
    knowledge_db.ensure_schema(conn)
    n = ingest_run._ingest_fix_events(conn, proj, "demo", "demo", "sky130hs")
    assert n >= 1
    verdicts = [v for (v,) in conn.execute(
        "SELECT verdict FROM fix_events WHERE strategy='density_relief'")]
    assert verdicts and all(v == "regression" for v in verdicts), verdicts
    conn.close()


# ── ingest-side belt-and-braces ──────────────────────────────────────────────

def _log_row(**kw):
    row = {"check": "drc", "iter": 1, "strategy": "s", "before": 10, "after": 8,
           "verdict": "applied", "from_stage": "floorplan",
           "fix_session_id": "sess1", "violation_class": "li.3",
           "cumulative_config": '{"PLACE_DENSITY_LB_ADDON":"0.05"}',
           "config_delta": (
               '{"PLACE_DENSITY_LB_ADDON":{"before":null,"after":"0.05"}}'),
           "env_flags": "{}",
           "predicates": {}, "ts": "2026-07-26T00:00:00"}
    row.update(kw)
    return row


def _mk_project(tmp_path, rows, session_compare=None):
    proj = tmp_path / "proj_ing"
    (proj / "reports").mkdir(parents=True)
    with open(proj / "reports" / "fix_log.jsonl", "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    if session_compare is not None:
        (proj / "reports" / "fix_session_compare.json").write_text(
            json.dumps(session_compare))
    return proj


def _ingest(tmp_path, proj):
    conn = knowledge_db.connect(tmp_path / "k2.sqlite")
    knowledge_db.ensure_schema(conn)
    ingest_run._ingest_fix_events(conn, proj, "demo", "demo", "sky130hs")
    return conn


def test_ingest_row_level_global_regressions_override_applied(tmp_path):
    proj = _mk_project(tmp_path, [
        _log_row(global_regressions=["route_regression:0->32"])])
    conn = _ingest(tmp_path, proj)
    (v,) = conn.execute("SELECT verdict FROM fix_events").fetchone()
    assert v == "regression"
    conn.close()


def test_ingest_session_residual_downgrades_wins_to_inconclusive(tmp_path):
    # A drc win in a session that ended with an UNEXPLAINED lvs regression
    # (measured only when the later lvs phase re-graded) -> inconclusive.
    proj = _mk_project(
        tmp_path,
        [_log_row()],
        session_compare={"fix_session_id": "sess1", "check": "both",
                         "comparison": {"regressions": ["lvs_regression:clean->fail"],
                                        "gate": "regression"}})
    conn = _ingest(tmp_path, proj)
    (v,) = conn.execute("SELECT verdict FROM fix_events").fetchone()
    assert v == "inconclusive"
    conn.close()


def test_ingest_session_regression_already_covered_keeps_earlier_win(tmp_path):
    # Iter1 genuinely won; iter2 regressed route and was caught LIVE (row-level
    # global_regressions + revert). The session-end route regression is therefore
    # EXPLAINED — iter1's win must survive (the #44 lost-wins lesson).
    proj = _mk_project(
        tmp_path,
        [_log_row(iter=1, strategy="good", before=10, after=6),
         _log_row(iter=2, strategy="bad", before=6, after=5, verdict="regression",
                  global_regressions=["route_regression:0->32"])],
        session_compare={"fix_session_id": "sess1", "check": "both",
                         "comparison": {"regressions": ["route_regression:0->32"],
                                        "gate": "regression"}})
    conn = _ingest(tmp_path, proj)
    got = dict(conn.execute("SELECT strategy, verdict FROM fix_events").fetchall())
    assert got["good"] == "win" and got["bad"] == "regression", got
    conn.close()


def test_ingest_session_own_check_win_exempt(tmp_path):
    # The lvs phase FOUGHT the lvs regression (20 -> 12 is a win on that check):
    # its win survives; the drc win in the same session is downgraded.
    proj = _mk_project(
        tmp_path,
        [_log_row(iter=1, check="drc", strategy="d", before=10, after=6),
         _log_row(iter=1, check="lvs", strategy="l", before=20, after=12,
                  violation_class="top_pin_mismatch")],
        session_compare={"fix_session_id": "sess1", "check": "both",
                         "comparison": {"regressions": ["lvs_regression:clean->fail"],
                                        "gate": "regression"}})
    conn = _ingest(tmp_path, proj)
    got = {(c, s): v for c, s, v in conn.execute(
        "SELECT check_type, strategy, verdict FROM fix_events").fetchall()}
    assert got[("lvs", "l")] == "win", got
    assert got[("drc", "d")] == "inconclusive", got
    conn.close()


def test_ingest_stale_session_compare_ignored(tmp_path):
    # Compare file keyed to a DIFFERENT session id (prior invocation) -> no effect.
    proj = _mk_project(
        tmp_path,
        [_log_row()],
        session_compare={"fix_session_id": "other", "check": "both",
                         "comparison": {"regressions": ["lvs_regression:clean->fail"],
                                        "gate": "regression"}})
    conn = _ingest(tmp_path, proj)
    (v,) = conn.execute("SELECT verdict FROM fix_events").fetchone()
    assert v == "win"
    conn.close()
