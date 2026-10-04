"""A stage that died by a crash signal is a tool failure, not the design's.

Regression (CORRECTIONS #15/#25, 2026-09-22): openroad v2.0-17598 crashes
reproducibly (an OpenSTA thread-race SIGSEGV at CTS, an ODB assertion SIGABRT in
TritonRoute), and the STA one is load-dependent. run_orfs.sh records make's exit
(2), so ingest wrote orfs_status='fail' + orfs-fail-<stage>: the design "failed"
the stage, the learner counted it, and the bias tracks machine load. The signal
survives only as text in flow.log, so the rule reads it there.

The fixtures are excerpts (elisions marked) of real flow.logs: E5L
e5l_3944650111ac_mips ("Command terminated by signal 6" at 5_2_route) and E8
fft8_stream (OpenSTA "Signal 11 received" in repair_timing).
"""
from __future__ import annotations

import shutil
from pathlib import Path

import ingest_run
import knowledge_db
import learn_heuristics

STAGES = ('{"stage": "synth", "status": 0, "elapsed_s": 9}\n'
          '{"stage": "floorplan", "status": 0, "elapsed_s": 5}\n'
          '{"stage": "place", "status": 0, "elapsed_s": 28}\n'
          '{"stage": "cts", "status": 0, "elapsed_s": 42}\n'
          '{"stage": "route", "status": 2, "elapsed_s": 412}\n')


def _project(tmp_path: Path, fixtures_dir: Path, flow_log: str | None = None) -> Path:
    proj = tmp_path / "mips"
    (proj / "constraints").mkdir(parents=True)
    (proj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = mips\nexport PLATFORM = sky130hd\n")
    run = proj / "backend" / "RUN_2026-09-22_23-52-36_3752481_6250"
    run.mkdir(parents=True)
    (run / "stage_log.jsonl").write_text(STAGES)
    if flow_log is None:
        shutil.copy(fixtures_dir / "flow_log_crash" / "route_sigabrt_mips.flow_log.txt",
                    run / "flow.log")
    else:
        (run / "flow.log").write_text(flow_log)
    return proj


def _ingest(proj: Path, tmp_knowledge_dir: Path):
    conn = knowledge_db.connect(tmp_knowledge_dir / "knowledge.sqlite")
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    run_id = ingest_run.ingest(proj, conn,
                               families_path=tmp_knowledge_dir / "families.json")
    conn.commit()
    return conn, run_id


def test_real_route_sigabrt_is_a_tool_crash(fixtures_dir, tmp_knowledge_dir, tmp_path) -> None:
    conn, run_id = _ingest(_project(tmp_path, fixtures_dir), tmp_knowledge_dir)

    status, stage = conn.execute(
        "SELECT orfs_status, orfs_fail_stage FROM runs WHERE run_id=?", (run_id,)).fetchone()
    assert (status, stage) == ("tool_crash", "route")
    sigs = [r[0] for r in conn.execute(
        "SELECT signature FROM failure_events WHERE run_id=?", (run_id,))]
    assert sigs == ["tool-crash-route-SIGABRT"]          # no orfs-fail-route design verdict
    assert conn.execute("SELECT COUNT(*) FROM run_violations WHERE run_id=?",
                        (run_id,)).fetchone()[0] == 0    # no design symptom to repair
    # Neither a success nor a failure sample for the learner.
    assert run_id not in {r["run_id"] for r in learn_heuristics._fetch_learnable_rows(conn)}


def test_a_genuine_route_failure_stays_the_designs(fixtures_dir, tmp_knowledge_dir,
                                                   tmp_path) -> None:
    log = ("Running global_route.tcl, stage 5_1_grt\n"
           "[ERROR GRT-0116] Global routing finished with congestion. Check the "
           "congestion regions in the DRC Viewer.\n"
           "Elapsed time: 0:41.02[h:]min:sec.\n"
           "make: *** [Makefile:640: results/sky130hd/mips/base/5_1_grt.odb] Error 1\n"
           "ERROR: Stage 'route' failed (exit code 2) after 60s\n")
    conn, run_id = _ingest(_project(tmp_path, fixtures_dir, log), tmp_knowledge_dir)
    status, = conn.execute("SELECT orfs_status FROM runs WHERE run_id=?", (run_id,)).fetchone()
    assert status == "fail"
    sig, = conn.execute("SELECT signature FROM failure_events WHERE run_id=?",
                        (run_id,)).fetchone()
    assert sig == "orfs-fail-route-GRT-0116"


def test_only_the_failing_step_is_read(tmp_path: Path) -> None:
    # A SIGSEGV line from an EARLIER step is not this stage's cause.
    (tmp_path / "flow.log").write_text(
        "Running cts.tcl, stage 4_1_cts\nSignal 11 received\n"
        "Running detail_route.tcl, stage 5_2_route\n[ERROR DRT-0305] Net has no pins\n"
        "ERROR: Stage 'route' failed (exit code 2) after 5s\n")
    assert ingest_run._stage_crash(tmp_path, "route") is None


def test_external_kills_keep_their_timeout_meaning(tmp_path: Path) -> None:
    (tmp_path / "flow.log").write_text(
        "Running detail_route.tcl, stage 5_2_route\nCommand terminated by signal 9\n"
        "ERROR: Stage 'route' failed (exit code 137) after 7200s\n")
    assert ingest_run._stage_crash(tmp_path, "route") is None


def test_real_opensta_race_sigsegv(fixtures_dir, tmp_path: Path) -> None:
    # Real excerpt: E8 fft8_stream (ihp-sg13g2), repair_timing SIGSEGV in
    # sta::DispatchQueue::dispatch_thread_handler while the route stage rebuilt
    # 4_1_cts. The stage that failed is 'route'; its cause is the crash.
    shutil.copy(fixtures_dir / "flow_log_crash" / "cts_sigsegv_fft8_stream.flow_log.txt",
                tmp_path / "flow.log")
    assert ingest_run._stage_crash(tmp_path, "route") == ("SIGSEGV", "Signal 11 received")


def test_our_own_stage_timeout_is_not_a_crash(tmp_path: Path) -> None:
    # Review finding (2026-09-23): a tool SIGTERMed by run_orfs.sh's stage timeout
    # can print a crash trace while dying. Status 124/137 keeps its timeout meaning.
    (tmp_path / "flow.log").write_text(
        "Running detail_route.tcl, stage 5_2_route\nSignal 11 received\n"
        "ERROR: Stage 'route' failed (exit code 124) after 7200s\n")
    stages = [{"stage": "synth", "status": 0}, {"stage": "route", "status": 124}]
    assert ingest_run._derive_orfs_status(stages, "full", tmp_path) == ("fail", "route")


def test_a_crash_after_the_tool_already_errored_stays_the_designs(tmp_path: Path) -> None:
    (tmp_path / "flow.log").write_text(
        "Running global_route.tcl, stage 5_1_grt\n"
        "[ERROR GRT-0116] Global routing finished with congestion.\n"
        "Signal 11 received\nCommand terminated by signal 11\n"
        "ERROR: Stage 'route' failed (exit code 2) after 60s\n")
    assert ingest_run._stage_crash(tmp_path, "route") is None


def test_repair_leaves_the_same_projection_as_live_ingest(fixtures_dir, tmp_knowledge_dir,
                                                          tmp_path) -> None:
    # Review finding (2026-09-23): a row ingested as 'fail' before the rule existed
    # and later reconciled to 'tool_crash' kept its orfs_stage design symptom and got
    # no crash event.
    import repair_run_status

    proj = _project(tmp_path, fixtures_dir)
    conn, run_id = _ingest(proj, tmp_knowledge_dir)
    run_dir = next((proj / "backend").glob("RUN_*"))
    # Re-create the pre-rule store state of this run.
    conn.execute("DELETE FROM failure_events WHERE run_id=?", (run_id,))
    conn.execute("INSERT INTO failure_events (run_id, stage, signature, detail) "
                 "VALUES (?, 'route', 'orfs-fail-route', NULL)", (run_id,))
    conn.execute("INSERT INTO run_violations (run_id, design_family, platform) "
                 "VALUES (?, 'x', 'sky130hd')", (run_id,))

    repair_run_status._reconcile_orfs_failure_event(conn, run_id, "tool_crash", "route",
                                                    run_dir)

    assert [r[0] for r in conn.execute(
        "SELECT signature FROM failure_events WHERE run_id=?", (run_id,))] == [
        "tool-crash-route-SIGABRT"]
    assert conn.execute("SELECT COUNT(*) FROM run_violations WHERE run_id=?",
                        (run_id,)).fetchone()[0] == 0
