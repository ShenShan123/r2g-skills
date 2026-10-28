"""One real input-selection recovery, without claiming a physical delivery.

Source/SQLite fixtures are isolated; pipeline submission is recorded, not run.
The real compiler regression demonstrates a genuine design.json omission, not
an RTL patch or a synthetic PASS report.
"""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from r2g_agent import debug_actions, design_checks, project
from r2g_agent.agent_loop import SessionStore
from r2g_agent.input_snapshots import capture, digest, read_tree, reference
from r2g_agent.jobs import JobStore
from r2g_agent.submissions import source_snapshot
from r2g_agent.tool_outputs import ToolContext


TOP = "module top(input a, output y);\nchild u(.a(a), .y(y));\nendmodule\n"
CHILD = "module child(input a, output y);\nassign y = a;\nendmodule\n"


@pytest.fixture
def recovery(tmp_path, monkeypatch):
    monkeypatch.setenv("R2G_HOME", str(tmp_path / "home"))
    sessions, jobs = SessionStore(tmp_path / "sessions.sqlite"), JobStore(tmp_path / "jobs.sqlite")
    sid = sessions.create()
    turn, _ = sessions.turns.create(sid, "input-list-recovery", {"message": "run unchanged design"})
    root = tmp_path / "input"
    root.mkdir()
    (root / "top.v").write_text(TOP)
    (root / "child.v").write_text(CHILD)
    (root / "design.json").write_text(json.dumps({"top": "top", "files": ["top.v"]}))
    pdir = tmp_path / "project"
    project.create(root, pdir, "nangate45", top="top", name="top")
    before = capture(pdir)
    sources = [{"name": "top", "path": str(root), "top": "top"}]
    snapshot = source_snapshot(sources)
    spec = {"platform": "nangate45", "output": "graph", "params": {}, "sources": sources, "source_snapshot": snapshot}
    submission = {"request_key": "original-submission", "request_hash": digest(spec), "source_snapshot": snapshot,
                  "session_id": sid, "turn_id": turn["id"], "action_id": "submit-original"}
    jid, _ = jobs.create_submission("missing compilation unit fixture", spec, [{"name": "top", "project": str(pdir)}], submission)
    detail = {"top": "top", "source": str(root), "failed_stage": "synth", "input_bindings": {"synth": {
        "before": reference(before), "after": reference(before), "unchanged": True, "exit_code": 1}}}
    jobs.set_design(jid, "top", status="failed", stage="synth", detail=detail,
                    event=("synth", "fail", "Module `child' referenced in module `top' is not part of the design.", {}))
    jobs.set_job(jid, "failed")
    sessions.turns.link_job(turn["id"], jid)
    event = next(event for event in jobs.events(jid) if event["kind"] == "fail")
    context = ToolContext(sid, sessions.outputs, turn_id=turn["id"], action_id="recover-dependency", job_id=jid)
    pipeline = SimpleNamespace(submit=Mock(return_value="recorded-child"), cancel=Mock())
    controller = debug_actions.DependencyDebugController(sessions.path, jobs, pipeline)
    return SimpleNamespace(sessions=sessions, jobs=jobs, sid=sid, turn=turn, root=root, project=pdir,
                           before=before, jid=jid, event=event, context=context, pipeline=pipeline, controller=controller)


def run(case):
    return case.controller.execute(case.context, job_id=case.jid, design="top", evidence_event_id=case.event["id"])


def install_checker(monkeypatch, *, status="passed", yosys=False):
    monkeypatch.setattr(debug_actions.capabilities, "tool_inventory", lambda names: {"tools": {"yosys": {"usable": yosys}}})
    checker = Mock(side_effect=lambda path, top, check, **kw: {"status": status, "check": check,
                                                            "functional_verification": "not_run"})
    monkeypatch.setattr(debug_actions.design_checks, "check_design", checker)
    return checker


def test_real_omitted_compilation_unit_recovers_without_editing_rtl(recovery, monkeypatch):
    case = recovery
    executable = shutil.which("iverilog")
    if not executable:
        pytest.skip("Icarus not installed")
    original = subprocess.run([executable, "-g2012", "-tnull", "-s", "top", "top.v"], cwd=case.root,
                              capture_output=True, timeout=10)
    assert original.returncode != 0 and b"Unknown module type: child" in original.stderr
    inv = {"tools": {"iverilog": {"usable": True, "path": executable,
                                  "sha256": hashlib.sha256(Path(executable).read_bytes()).hexdigest()},
                     "yosys": {"usable": False}}}
    monkeypatch.setattr(debug_actions.capabilities, "tool_inventory", lambda names: inv)
    delegated = case.controller.authorize(case.sid, case.jid)
    assert delegated["scope"]["max_attempts"] == 1
    result = run(case)
    assert result["status"] == "running" and result["child_job_id"] == "recorded-child"
    assert result["candidate"]["added_compile_units"] == ["child.v"]
    assert result["outcome"]["checks"][0]["status"] == "passed"
    assert result["outcome"]["checks"][0]["functional_verification"] == "not_run"
    assert not result["delivered"] and not result["rtl_modified"]
    assert capture(case.project) == case.before
    args, kw = case.pipeline.submit.call_args
    candidate = Path(args[0][0]["path"])
    assert (candidate / "top.v").read_text() == TOP
    assert (candidate / "child.v").read_text() == CHILD
    assert json.loads((candidate / "design.json").read_text())["files"] == ["top.v", "child.v"]
    assert kw["params"] == {} and kw["platform"] == "nangate45"
    assert not args[0][0]["dependency_debug_receipt"]["allow_recursive_debug"]
    assert run(case) == result
    assert case.pipeline.submit.call_count == 1


def test_real_yosys_sees_the_original_omission_then_validates_the_recovered_units(recovery, monkeypatch):
    case = recovery
    tools = {name: shutil.which(name) for name in ("iverilog", "yosys")}
    if not all(tools.values()):
        pytest.skip("Icarus and Yosys are required")
    original = subprocess.run([tools["yosys"], "-Q", "-T", "-p", "read_verilog top.v; hierarchy -check -top top"],
                              cwd=case.root, capture_output=True, timeout=10)
    assert original.returncode != 0 and b"not part of the design" in original.stderr
    inv = {"tools": {name: {"usable": True, "path": executable,
                            "sha256": hashlib.sha256(Path(executable).read_bytes()).hexdigest()}
                     for name, executable in tools.items()}}
    monkeypatch.setattr(debug_actions.capabilities, "tool_inventory", lambda names: inv)
    case.controller.authorize(case.sid, case.jid)
    result = run(case)
    assert [(check["check"], check["status"]) for check in result["outcome"]["checks"]] == [
        ("compile", "passed"), ("synthesis_structure", "passed")]
    candidate = Path(case.pipeline.submit.call_args.args[0][0]["path"])
    recreated = case.root.parent / "recreated-child-project"
    meta = project.create(candidate, recreated, "nangate45", top="top", name="top")
    assert {Path(name).name for name in meta["files"]} == {"top.v", "child.v"}
    assert read_tree(recreated / "rtl") == read_tree(case.project / "rtl")
    assert (recreated / "constraints/constraint.sdc").read_bytes() == (case.project / "constraints/constraint.sdc").read_bytes()


def test_scope_missing_and_readonly_reviews_cannot_execute(recovery, monkeypatch):
    case = recovery
    checker = install_checker(monkeypatch)
    assert case.controller.authority(case.sid, case.jid) is None
    with pytest.raises(debug_actions.DebugDenied, match="delegation"):
        run(case)
    case.controller.authorize(case.sid, case.jid)
    with pytest.raises(debug_actions.DebugDenied):
        case.controller.execute(replace(case.context, read_only=True), job_id=case.jid, design="top", evidence_event_id=case.event["id"])
    checker.assert_not_called()
    case.pipeline.submit.assert_not_called()


@pytest.mark.parametrize("part", ["top.v", "child.v", "design.json"])
def test_source_change_before_delegation_is_stale(recovery, part):
    case = recovery
    target = case.root / part
    target.write_bytes(target.read_bytes() + b"\n")
    with pytest.raises(debug_actions.DebugDenied, match="source version"):
        case.controller.authorize(case.sid, case.jid)


@pytest.mark.parametrize("part", ["rtl/top.v", "constraints/constraint.sdc"])
def test_job_snapshot_change_cannot_recover_stale_evidence(recovery, monkeypatch, part):
    case = recovery
    checker = install_checker(monkeypatch)
    case.controller.authorize(case.sid, case.jid)
    target = case.project / part
    target.write_bytes(target.read_bytes() + b"\n")
    with pytest.raises(debug_actions.DebugDenied, match="bound"):
        run(case)
    checker.assert_not_called()
    case.pipeline.submit.assert_not_called()


def test_new_directory_content_does_not_expand_approved_scope(recovery, monkeypatch):
    case = recovery
    checker = install_checker(monkeypatch)
    case.controller.authorize(case.sid, case.jid)
    (case.root / "new_constraint.sdc").write_text("set_false_path -from [all_inputs]\n")
    with pytest.raises(debug_actions.DebugDenied, match="directory changed"):
        run(case)
    checker.assert_not_called()


def test_constraints_change_during_diagnostics_blocks_child_submission(recovery, monkeypatch):
    case = recovery
    checker = install_checker(monkeypatch)
    case.controller.authorize(case.sid, case.jid)

    def mutate_after_check(*args, **kwargs):
        target = case.project / "constraints/constraint.sdc"
        target.write_bytes(target.read_bytes() + b"\n# changed while diagnostics ran\n")
        return {"status": "passed", "check": "compile"}

    checker.side_effect = mutate_after_check
    with pytest.raises(debug_actions.DebugDenied, match="bound"):
        run(case)
    assert case.controller.authority(case.sid, case.jid)["status"] == "stopped"
    case.pipeline.submit.assert_not_called()


def test_changed_flow_parameters_cannot_reuse_the_existing_delegation(recovery, monkeypatch):
    case = recovery
    checker = install_checker(monkeypatch)
    case.controller.authorize(case.sid, case.jid)
    job = case.jobs.job(case.jid)
    job["spec"]["params"]["period_ns"] = 100
    with case.jobs._conn() as c:
        c.execute("UPDATE jobs SET spec=? WHERE id=?", (json.dumps(job["spec"]), case.jid))
    with pytest.raises(debug_actions.DebugDenied, match="parameters changed"):
        run(case)
    checker.assert_not_called()


def test_custom_constraints_cannot_be_silently_dropped_by_child_generation(recovery, monkeypatch):
    case = recovery
    checker = install_checker(monkeypatch)
    target = case.project / "constraints/constraint.sdc"
    target.write_bytes(target.read_bytes() + b"\nset_max_transition 0.1 [current_design]\n")
    snapshot = capture(case.project)
    detail = case.jobs.design(case.jid, "top")["detail"]
    detail["input_bindings"]["synth"].update(before=reference(snapshot), after=reference(snapshot))
    with case.jobs._conn() as c:
        c.execute("UPDATE designs SET detail=? WHERE job_id=? AND name='top'", (json.dumps(detail), case.jid))
    case.controller.authorize(case.sid, case.jid)
    with pytest.raises(debug_actions.DebugDenied, match="custom parent constraints"):
        run(case)
    checker.assert_not_called()
    case.pipeline.submit.assert_not_called()


def test_policy_version_drift_invalidates_existing_delegation(recovery, monkeypatch):
    case = recovery
    checker = install_checker(monkeypatch)
    case.controller.authorize(case.sid, case.jid)
    monkeypatch.setattr(debug_actions, "_policy", lambda: {"version": "changed-after-delegation"})
    with pytest.raises(debug_actions.DebugDenied, match="policy version"):
        run(case)
    checker.assert_not_called()


def test_recovery_receipt_cannot_bootstrap_another_automatic_round(recovery):
    case = recovery
    job = case.jobs.job(case.jid)
    job["spec"]["sources"][0]["dependency_debug_receipt"] = {"allow_recursive_debug": False}
    with case.jobs._conn() as c:
        c.execute("UPDATE jobs SET spec=? WHERE id=?", (json.dumps(job["spec"]), case.jid))
    with pytest.raises(debug_actions.DebugDenied, match="another recovery"):
        case.controller.authorize(case.sid, case.jid)


def test_single_file_submission_does_not_authorize_its_siblings(recovery):
    case = recovery
    job = case.jobs.job(case.jid)
    job["spec"]["sources"][0]["path"] = str(case.root / "top.v")
    snapshot = source_snapshot(job["spec"]["sources"])
    job["spec"]["source_snapshot"] = snapshot
    with case.jobs._conn() as c:
        c.execute("UPDATE jobs SET spec=? WHERE id=?", (json.dumps(job["spec"]), case.jid))
        c.execute("UPDATE submissions SET source_snapshot=? WHERE job_id=?", (snapshot, case.jid))
    with pytest.raises(debug_actions.DebugDenied, match="single-file"):
        case.controller.authorize(case.sid, case.jid)


def test_failure_at_other_stage_or_environment_error_is_not_input_recovery(recovery, monkeypatch):
    case = recovery
    checker = install_checker(monkeypatch)
    case.controller.authorize(case.sid, case.jid)
    with case.jobs._conn() as c:
        c.execute("UPDATE events SET message=? WHERE id=?", ("unknown module; Permission denied", case.event["id"]))
    with pytest.raises(debug_actions.DebugDenied, match="environment"):
        run(case)
    checker.assert_not_called()


def test_failed_cheap_check_prevents_expensive_child_and_is_not_repeated(recovery, monkeypatch):
    case = recovery
    checker = install_checker(monkeypatch, status="failed")
    case.controller.authorize(case.sid, case.jid)
    first = run(case)
    assert first["status"] == "check_failed"
    assert run(case) == first
    assert checker.call_count == 1
    case.pipeline.submit.assert_not_called()


def test_available_yosys_must_pass_too(recovery, monkeypatch):
    case = recovery
    checker = install_checker(monkeypatch, yosys=True)
    checker.side_effect = [{"status": "passed", "check": "compile"}, {"status": "failed", "check": "synthesis_structure"}]
    case.controller.authorize(case.sid, case.jid)
    assert run(case)["status"] == "check_failed"
    assert checker.call_count == 2
    case.pipeline.submit.assert_not_called()


def test_uncertain_submission_is_not_replayed_and_can_reconcile_committed_child(recovery, monkeypatch):
    case = recovery
    checker = install_checker(monkeypatch)
    delegated = case.controller.authorize(case.sid, case.jid)

    def commit_then_crash(sources, **kw):
        submission = {**kw["submission"], "request_hash": "recorded-child-request", "source_snapshot": "recorded-child-source"}
        jid, _ = case.jobs.create_submission("recorded child", {"sources": sources}, [{"name": "top"}], submission)
        raise RuntimeError("interrupted after commit")

    case.pipeline.submit.side_effect = commit_then_crash
    with pytest.raises(RuntimeError, match="after commit"):
        run(case)
    assert run(case)["status"] == "recovery_required"
    assert checker.call_count == 1 and case.pipeline.submit.call_count == 1
    case.controller.tick()
    recovered = case.controller.authority(case.sid, case.jid)
    assert recovered["status"] == "running"
    assert recovered["child_job_id"] == case.jobs.job_for_submission("dependency-debug:" + delegated["id"])["job_id"]


def test_repeated_authorization_cannot_extend_deadline_or_restore_attempt(recovery, monkeypatch):
    case = recovery
    checker = install_checker(monkeypatch, status="failed")
    first = case.controller.authorize(case.sid, case.jid)
    run(case)
    repeated = case.controller.authorize(case.sid, case.jid)
    assert repeated["id"] == first["id"] and repeated["deadline"] == first["deadline"]
    assert repeated["status"] == "check_failed"
    assert checker.call_count == 1


def test_startup_uncertainty_cannot_replay_an_incomplete_attempt(recovery, monkeypatch):
    case = recovery
    checker = install_checker(monkeypatch)
    case.controller.authorize(case.sid, case.jid)
    with case.controller._c() as c:
        c.execute("UPDATE dependency_debug SET status='executing'")
    case.controller.recover()
    result = run(case)
    assert result["status"] == "recovery_required"
    checker.assert_not_called()
    case.pipeline.submit.assert_not_called()


def test_deadline_cancels_an_active_child(recovery, monkeypatch):
    case = recovery
    install_checker(monkeypatch)
    case.controller.authorize(case.sid, case.jid)
    run(case)
    child = case.jobs.create_job("active child fixture", {}, [{"name": "top"}])
    case.jobs.set_job(child, "running")
    with case.controller._c() as c:
        c.execute("UPDATE dependency_debug SET deadline=0,child_job_id=?", (child,))
    case.controller.tick()
    assert case.controller.authority(case.sid, case.jid)["status"] == "budget_exhausted"
    case.pipeline.cancel.assert_called_once_with(child)
