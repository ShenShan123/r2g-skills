"""Win 3 — r2g-bench: held-out checkpoint self-evaluation.

A held-out design set (knowledge/eval/bench_set.json) scored per-checkpoint with
partial credit (reuses Win 1 outcome_score). Runs whose design matches the set are
flagged is_bench=1 at ingest and EXCLUDED from the learning read — never from the
failure_events write path (a bench fail still gets its orfs-fail-% event). The
bench is a NON-BLOCKING scoreboard.
"""
import json
from pathlib import Path

import eval_heuristics
import ingest_run
import knowledge_db
import learn_heuristics


def _bench_set(tmp_path, names):
    p = tmp_path / "bench_set.json"
    p.write_text(json.dumps(
        {"version": 1, "designs": [{"design_name": n, "platform": "sky130hd",
                                    "band": "small"} for n in names]}))
    return p


def _mk(tmp_path, name, *, stage_log, drc, lvs, family_dir=None):
    p = tmp_path / (family_dir or name)
    (p / "constraints").mkdir(parents=True)
    (p / "reports").mkdir()
    (p / "backend").mkdir()
    (p / "constraints" / "config.mk").write_text(
        f"export DESIGN_NAME = {name}\nexport PLATFORM = sky130hd\n"
        "export CORE_UTILIZATION = 40\n")
    (p / "reports" / "drc.json").write_text(json.dumps(drc))
    (p / "reports" / "lvs.json").write_text(json.dumps(lvs))
    (p / "reports" / "ppa.json").write_text(json.dumps(
        {"summary": {}, "geometry": {"instance_count": 1200}}))
    (p / "backend" / "stage_log.jsonl").write_text(
        "\n".join(json.dumps(s) for s in stage_log) + "\n")
    return p


def _conn(tmp_path, monkeypatch):
    monkeypatch.setenv("R2G_JOURNAL_DB", str(tmp_path / "journal.sqlite"))
    c = knowledge_db.connect(tmp_path / "k.sqlite")
    knowledge_db.ensure_schema(c)
    return c


_CLEAN_LOG = [{"stage": s, "status": 0} for s in
              ("synth", "floorplan", "place", "cts", "route", "finish")]
_ROUTE_ABORT = [{"stage": s, "status": 0} for s in
                ("synth", "floorplan", "place", "cts")] + [{"stage": "route", "status": 1}]


def test_ingest_flags_bench_design(tmp_path, monkeypatch):
    monkeypatch.setenv("R2G_BENCH_SET", str(_bench_set(tmp_path, ["aes_encipher_block"])))
    conn = _conn(tmp_path, monkeypatch)
    rid_b = ingest_run.ingest(_mk(tmp_path, "aes_encipher_block",
                                  stage_log=_CLEAN_LOG, drc={"status": "clean"},
                                  lvs={"status": "clean"}), conn)
    rid_n = ingest_run.ingest(_mk(tmp_path, "ordinary_logic",
                                  stage_log=_CLEAN_LOG, drc={"status": "clean"},
                                  lvs={"status": "clean"}), conn)
    assert conn.execute("SELECT is_bench FROM runs WHERE run_id=?", (rid_b,)).fetchone()[0] == 1
    assert (conn.execute("SELECT is_bench FROM runs WHERE run_id=?",
                         (rid_n,)).fetchone()[0] or 0) == 0


def test_bench_fail_run_still_records_failure_event(tmp_path, monkeypatch):
    """Honesty invariant H3: is_bench filters the LEARNING read only — a bench fail
    still gets its orfs-fail-% failure_event and stays in the honesty count."""
    monkeypatch.setenv("R2G_BENCH_SET", str(_bench_set(tmp_path, ["des_area"])))
    conn = _conn(tmp_path, monkeypatch)
    rid = ingest_run.ingest(_mk(tmp_path, "des_area", stage_log=_ROUTE_ABORT,
                                drc={"status": "unknown"}, lvs={"status": "unknown"}), conn)
    assert conn.execute("SELECT is_bench FROM runs WHERE run_id=?", (rid,)).fetchone()[0] == 1
    ev = conn.execute("SELECT signature FROM failure_events WHERE run_id=? AND "
                      "signature LIKE 'orfs-fail-%'", (rid,)).fetchall()
    assert ev, "bench fail run must still carry its orfs-fail-% event"


def test_learn_excludes_bench_from_family_medians(tmp_path, monkeypatch):
    """A held-out bench run must not bias the learned family medians."""
    monkeypatch.setenv("R2G_BENCH_SET", str(_bench_set(tmp_path, ["benchy"])))
    conn = _conn(tmp_path, monkeypatch)
    # three normal successes for family 'normal' at CU 40
    for i in range(3):
        ingest_run.ingest(_mk(tmp_path, f"normal_{i}", stage_log=_CLEAN_LOG,
                              drc={"status": "clean"}, lvs={"status": "clean"},
                              family_dir=f"normal_{i}"), conn)
    conn.close()
    # add a bench run that, if counted, would skew the corpus — must be excluded.
    conn = knowledge_db.connect(tmp_path / "k.sqlite")
    ingest_run.ingest(_mk(tmp_path, "benchy", stage_log=_CLEAN_LOG,
                          drc={"status": "clean"}, lvs={"status": "clean"}), conn)
    conn.close()
    learned_rows = learn_heuristics._fetch_learnable_rows(
        knowledge_db.connect(tmp_path / "k.sqlite"))
    assert all((r.get("is_bench") or 0) == 0 for r in learned_rows)
    assert not any(r["design_name"] == "benchy" for r in learned_rows)


def test_bench_score_reports_sr_and_outcome(tmp_path, monkeypatch):
    monkeypatch.setenv("R2G_BENCH_SET",
                       str(_bench_set(tmp_path, ["aes_encipher_block"])))
    db = tmp_path / "k.sqlite"
    conn = _conn(tmp_path, monkeypatch)
    # two repeats for one bench design: one clean (rcx? no -> lvs clean), one route abort
    ingest_run.ingest(_mk(tmp_path, "aes_encipher_block", stage_log=_CLEAN_LOG,
                          drc={"status": "clean"}, lvs={"status": "clean"},
                          family_dir="aes_r0"), conn)
    ingest_run.ingest(_mk(tmp_path, "aes_encipher_block", stage_log=_ROUTE_ABORT,
                          drc={"status": "unknown"}, lvs={"status": "unknown"},
                          family_dir="aes_r1"), conn)
    conn.close()
    card = eval_heuristics.bench_score(db, _bench_set(tmp_path, ["aes_encipher_block"]))
    d = card["designs"]["aes_encipher_block"]
    assert d["n_runs"] == 2
    assert d["success_rate"] == 0.5          # one clean, one abort
    assert 0.0 < d["mean_outcome_score"] < 1.0
    assert d["outcome_lcb"] <= d["mean_outcome_score"]   # LCB over repeats
    assert "overall" in card and card["overall"]["n_designs"] >= 1
