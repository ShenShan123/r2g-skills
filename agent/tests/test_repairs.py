"""Bounded repair regressions using isolated synthetic failure receipts.

Fixtures here are protocol tests, not production EDA evidence. Only the tests
explicitly named real_contract launch tools, using the registered counter8
oracle and a local controlled example; no model, network or EDA flow is run.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import asyncio
import hashlib
from itertools import count
import json
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from r2g_agent import input_snapshots, repair_checks, repairs
from r2g_agent.agent_loop import SessionStore
from r2g_agent.input_snapshots import capture, digest, manifest, read_tree, reference
from r2g_agent.jobs import JobStore
from r2g_agent.tool_outputs import ToolContext


EXAMPLE = Path(__file__).parents[1] / "r2g_agent/examples/counter8/counter8.v"
GOOD_RTL = EXAMPLE.read_text()
MISSING = "        else if (load)   q <= d"
BROKEN_RTL = GOOD_RTL.replace(MISSING + ";", MISSING)
TOOLCHAIN = Path("/data1/zhangdy/Tools/tehm-toolchain/oss-cad-suite/bin")


@pytest.fixture
def harness(tmp_path, monkeypatch):
    """Create fresh stores and an explicitly synthetic source-bound failure."""
    sessions = SessionStore(tmp_path / "sessions.sqlite")
    jobs = JobStore(tmp_path / "jobs.sqlite")
    sid = sessions.create()
    turn, _ = sessions.turns.create(sid, "synthetic-repair-request", {"message": "review fixture"})
    policy = {"id": repair_checks.ORACLE_ID, "testbench_sha256": "synthetic-test-only",
              "tools": {}, "checks": 2048}
    monkeypatch.setattr(repairs, "oracle_policy", lambda: policy)
    checker = Mock(return_value={"ok": True, "oracle": policy,
                                 "checks": [{"name": "synthetic-test-check", "exit_code": 0}]})
    monkeypatch.setattr(repairs, "check", checker)
    pipeline = SimpleNamespace(submit=Mock(return_value="synthetic-child"), cancel=Mock())
    controller = repairs.RepairController(sessions.path, jobs, pipeline)
    cases = count()

    def make(*, source=BROKEN_RTL, evidence="syntax error at counter8.v", linked=True,
             binding=True, top="counter8"):
        project = tmp_path / ("project-" + str(next(cases)))
        (project / "rtl").mkdir(parents=True)
        (project / "constraints").mkdir()
        (project / "rtl/counter8.v").write_text(source)
        (project / "constraints/design.sdc").write_text("create_clock -period 10 [get_ports clk]\n")
        snapshot = capture(project)
        detail = {"failed_stage": "synth", "top": top, "fixture": "synthetic-only"}
        if binding:
            detail["input_bindings"] = {"synth": {"before": reference(snapshot),
                "after": reference(snapshot), "unchanged": True, "exit_code": 1}}
        jid = jobs.create_job("synthetic failure fixture", {"platform": "fixture-platform",
            "output": "graph", "params": {}}, [{"name": "counter8", "project": str(project)}])
        jobs.set_design(jid, "counter8", status="failed", stage="synth", detail=detail,
                        event=("synth", "fail", evidence, {"fixture": "synthetic-only"}))
        jobs.set_job(jid, "failed")
        event = next(e for e in jobs.events(jid) if e["kind"] == "fail")
        if linked:
            sessions.turns.link_job(turn["id"], jid)
        context = ToolContext(sid, sessions.outputs, turn_id=turn["id"],
                              action_id="propose-" + jid, job_id=jid)
        return SimpleNamespace(project=project, jid=jid, event=event, snapshot=snapshot,
                               context=context, source=source)

    def propose(case, *, context=None, line=None, **overrides):
        rows = case.source.splitlines()
        if line is None:
            line = rows.index(MISSING) + 1
        arguments = {"job_id": case.jid, "design": "counter8", "file": "counter8.v", "line": line,
                     "expected_line": rows[line - 1], "evidence_event_id": case.event["id"],
                     "rationale": "Synthetic regression candidate; no production evidence."}
        arguments.update(overrides)
        return controller.propose(context or case.context, **arguments)

    def approve(case, proposal=None, **budget):
        proposal = proposal or propose(case)
        grant = controller.approve(sid, proposal["repair_proposal"], proposal["source_snapshot_id"], **budget)
        context = replace(case.context, grant_id=grant["id"], action_id="execute-" + proposal["repair_proposal"])
        return proposal, grant, context

    return SimpleNamespace(sessions=sessions, jobs=jobs, sid=sid, turn=turn, policy=policy,
        checker=checker, pipeline=pipeline, controller=controller, make=make, propose=propose, approve=approve)


def test_proposal_requires_host_approval_before_any_copy_or_execution(harness):
    h, case = harness, harness.make()
    before = capture(case.project)
    proposal = h.propose(case)
    assert proposal["status"] == "awaiting_approval"
    assert "+" + MISSING + ";" in proposal["diff"]
    with pytest.raises(repairs.RepairDenied):
        h.controller.execute(case.context, proposal["repair_proposal"])
    assert capture(case.project) == before
    assert not h.controller.root.exists()
    h.checker.assert_not_called()
    h.pipeline.submit.assert_not_called()


@pytest.mark.parametrize("change", ["read_only", "no_turn", "no_action", "no_grant", "other_session", "other_grant"])
def test_execution_rejects_missing_or_cross_scope_authority(harness, change):
    h, case = harness, harness.make()
    proposal, grant, context = h.approve(case)
    changes = {"read_only": {"read_only": True}, "no_turn": {"turn_id": None},
               "no_action": {"action_id": None}, "no_grant": {"grant_id": None},
               "other_session": {"session_id": h.sessions.create()}, "other_grant": {"grant_id": "other-grant"}}
    with pytest.raises(repairs.RepairDenied):
        h.controller.execute(replace(context, **changes[change]), proposal["repair_proposal"])
    assert h.controller.grant(h.sid, grant["id"])["rounds_used"] == 0
    h.checker.assert_not_called()
    h.pipeline.submit.assert_not_called()


@pytest.mark.parametrize("kind", ["unlinked", "wrong_job", "wrong_event", "no_binding", "infrastructure", "wrong_top"])
def test_proposal_requires_current_scoped_design_failure(harness, kind):
    h = harness
    case = h.make(linked=kind != "unlinked", binding=kind != "no_binding",
                  evidence="syntax error; Permission denied" if kind == "infrastructure" else "syntax error",
                  top="other_top" if kind == "wrong_top" else "counter8")
    options = {}
    if kind == "wrong_job":
        options["context"] = replace(case.context, job_id="unrelated")
    if kind == "wrong_event":
        options["evidence_event_id"] = case.event["id"] + 1
    with pytest.raises(repairs.RepairDenied):
        h.propose(case, **options)
    assert not h.controller.root.exists()
    h.checker.assert_not_called()


@pytest.mark.parametrize("part", ["rtl/counter8.v", "constraints/design.sdc"])
@pytest.mark.parametrize("when", ["before_approval", "before_execution"])
def test_changed_source_or_constraints_invalidates_authority(harness, part, when):
    h, case = harness, harness.make()
    proposal = h.propose(case)
    context = None
    if when == "before_execution":
        _, _, context = h.approve(case, proposal)
    path = case.project / part
    path.write_bytes(path.read_bytes() + b"\n// changed after evidence\n")
    with pytest.raises(repairs.RepairDenied):
        if context:
            h.controller.execute(context, proposal["repair_proposal"])
        else:
            h.approve(case, proposal)
    h.checker.assert_not_called()
    h.pipeline.submit.assert_not_called()


def test_source_bytes_cannot_drift_between_snapshot_and_patch_read(harness, monkeypatch):
    h, case = harness, harness.make()
    original_read = repairs.read_tree

    def changed_read(root):
        files = original_read(root)
        return {**files, "counter8.v": files["counter8.v"] + b"\n// changed between reads\n"}

    monkeypatch.setattr(repairs, "read_tree", changed_read)
    with pytest.raises(repairs.RepairDenied):
        h.propose(case)
    assert capture(case.project) == case.snapshot
    h.checker.assert_not_called()


@pytest.mark.parametrize("when", ["before_approval", "before_execution"])
def test_oracle_change_invalidates_approval(harness, monkeypatch, when):
    h, case = harness, harness.make()
    proposal = h.propose(case)
    context = None
    if when == "before_execution":
        _, _, context = h.approve(case, proposal)
    monkeypatch.setattr(repairs, "oracle_policy", lambda: {**h.policy, "testbench_sha256": "changed"})
    with pytest.raises(repairs.RepairDenied):
        if context:
            h.controller.execute(context, proposal["repair_proposal"])
        else:
            h.approve(case, proposal)
    h.checker.assert_not_called()


@pytest.mark.parametrize("snippet", [
    '    localparam TAG = "//"; initial $display("hidden");',
    '    localparam TAG = "/*"; initial $display("hidden"); // */',
    "    initial begin end", "    always #1 q = 0;", "    `include \"outside.v\"",
    "    always @(posedge clk) force q = 0;",
])
def test_dangerous_hdl_cannot_hide_behind_comment_markers(harness, snippet):
    h = harness
    case = h.make(source=BROKEN_RTL.replace("endmodule", snippet + "\nendmodule"))
    with pytest.raises(repairs.RepairDenied, match="policy"):
        h.propose(case)
    h.checker.assert_not_called()
    h.pipeline.submit.assert_not_called()


@pytest.mark.parametrize("path", ["../counter8.v", "/tmp/counter8.v", "rtl/counter8.v", "counter8.v/../counter8.v"])
def test_proposal_file_must_be_exact_registered_relative_member(harness, path):
    h, case = harness, harness.make()
    with pytest.raises(repairs.RepairDenied):
        h.propose(case, file=path)
    assert not h.controller.root.exists()


@pytest.mark.parametrize("budget", [
    {"max_rounds": 0}, {"max_rounds": 3}, {"max_rounds": True},
    {"max_model_calls": 0}, {"max_model_calls": 9}, {"max_model_calls": True},
    {"wall_seconds": 29}, {"wall_seconds": 901}, {"wall_seconds": 30.0},
])
def test_host_budget_limits_are_enforced(harness, budget):
    h, case = harness, harness.make()
    with pytest.raises(repairs.RepairDenied, match="budget"):
        h.approve(case, **budget)
    h.checker.assert_not_called()


def test_approval_requires_reviewed_snapshot(harness):
    h, case = harness, harness.make()
    proposal = h.propose(case)
    with pytest.raises(repairs.RepairDenied, match="snapshot"):
        h.controller.approve(h.sid, proposal["repair_proposal"], "unreviewed-snapshot")


@pytest.mark.parametrize("revoke", [False, True])
def test_expired_or_revoked_grant_cannot_execute(harness, revoke):
    h, case = harness, harness.make()
    proposal, grant, context = h.approve(case)
    with h.controller._c() as c:
        if revoke:
            c.execute("UPDATE repair_grants SET status='revoked' WHERE id=?", (grant["id"],))
        else:
            c.execute("UPDATE repair_grants SET deadline=? WHERE id=?", (time.time() - 1, grant["id"]))
    with pytest.raises(repairs.RepairDenied):
        h.controller.execute(context, proposal["repair_proposal"])
    h.checker.assert_not_called()
    h.pipeline.submit.assert_not_called()


def test_failed_candidate_then_different_candidate_preserves_original_and_budget(harness):
    h, case = harness, harness.make()
    before = capture(case.project)
    bad = h.propose(case, line=case.source.splitlines().index("module counter8 (") + 1)
    bad, grant, context = h.approve(case, bad)
    h.checker.side_effect = [{"ok": False, "oracle": h.policy, "checks": [{"name": "compile", "exit_code": 1}]},
                             {"ok": True, "oracle": h.policy, "checks": [{"name": "simulate", "exit_code": 0}]}]
    failed = h.controller.execute(context, bad["repair_proposal"])
    assert failed["status"] == "check_failed"
    h.pipeline.submit.assert_not_called()
    next_context = replace(context, action_id="different-candidate")
    good = h.propose(case, context=next_context)
    assert good["status"] == "approved"
    result = h.controller.execute(replace(next_context, action_id="execute-next"), good["repair_proposal"])
    assert result["status"] == "running"
    assert h.controller.grant(h.sid, grant["id"])["rounds_used"] == 2
    h.pipeline.submit.assert_called_once()
    sources = h.pipeline.submit.call_args.args[0]
    copied = Path(sources[0]["path"])
    assert (copied / "counter8.v").read_text() == GOOD_RTL
    assert sources[0]["repair_receipt"]["before"] == before
    assert sources[0]["repair_receipt"]["after_rtl"] == manifest(read_tree(copied))
    assert capture(case.project) == before


def test_same_candidate_new_action_does_not_replay_or_consume_another_round(harness):
    h, case = harness, harness.make()
    proposal, grant, context = h.approve(case)
    h.checker.return_value = {"ok": False, "oracle": h.policy, "checks": []}
    first = h.controller.execute(context, proposal["repair_proposal"])
    repeated = h.propose(case, context=replace(context, action_id="same-candidate-new-action"))
    assert repeated["repair_proposal"] == first["repair_proposal"]
    assert repeated["repeated_candidate"] is True
    assert h.controller.execute(replace(context, action_id="replay"), repeated["repair_proposal"])["status"] == "check_failed"
    assert h.controller.grant(h.sid, grant["id"])["rounds_used"] == 1
    h.checker.assert_called_once()
    h.pipeline.submit.assert_not_called()


def test_concurrent_same_candidate_proposals_share_durable_identity(harness):
    h, case = harness, harness.make()
    barrier = threading.Barrier(2)

    def propose(index):
        barrier.wait(timeout=5)
        return h.propose(case, context=replace(case.context, action_id="concurrent-proposal-" + str(index)))

    with ThreadPoolExecutor(max_workers=2) as executor:
        proposals = list(executor.map(propose, range(2)))
    assert proposals[0]["repair_proposal"] == proposals[1]["repair_proposal"]
    assert sum(bool(p.get("repeated_candidate")) for p in proposals) == 1
    h.checker.assert_not_called()


def test_concurrent_same_proposal_reserves_only_one_execution(harness):
    h, case = harness, harness.make()
    proposal, grant, context = h.approve(case)
    barrier = threading.Barrier(2)

    def run():
        barrier.wait(timeout=5)
        return h.controller.execute(context, proposal["repair_proposal"])

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: run(), range(2)))
    assert {r["status"] for r in results} <= {"executing", "running"}
    assert h.controller.grant(h.sid, grant["id"])["rounds_used"] == 1
    h.checker.assert_called_once()
    h.pipeline.submit.assert_called_once()


def test_running_candidate_prevents_parallel_different_candidate(harness):
    h, case = harness, harness.make()
    bad = h.propose(case, line=case.source.splitlines().index("module counter8 (") + 1)
    bad, grant, context = h.approve(case, bad)
    # A synthetic checker PASS tests concurrency authority, not HDL correctness.
    h.controller.execute(context, bad["repair_proposal"])
    other = h.propose(case, context=replace(context, action_id="different-proposal"))
    with pytest.raises(repairs.RepairDenied):
        h.controller.execute(replace(context, action_id="other-execution"), other["repair_proposal"])
    assert h.controller.grant(h.sid, grant["id"])["rounds_used"] == 1
    h.checker.assert_called_once()
    h.pipeline.submit.assert_called_once()


def test_round_budget_blocks_fullflow_after_last_failed_candidate(harness):
    h, case = harness, harness.make()
    bad = h.propose(case, line=case.source.splitlines().index("module counter8 (") + 1)
    bad, grant, context = h.approve(case, bad, max_rounds=1)
    h.checker.return_value = {"ok": False, "oracle": h.policy, "checks": []}
    h.controller.execute(context, bad["repair_proposal"])
    good = h.propose(case, context=replace(context, action_id="second-candidate"))
    with pytest.raises(repairs.RepairDenied, match="budget|active"):
        h.controller.execute(context, good["repair_proposal"])
    assert h.controller.grant(h.sid, grant["id"])["rounds_used"] == 1
    h.checker.assert_called_once()
    h.pipeline.submit.assert_not_called()


def test_recovery_never_replays_unknown_execution(harness):
    h, case = harness, harness.make()
    proposal, grant, context = h.approve(case)
    with h.controller._c() as c:
        c.execute("UPDATE repair_proposals SET status='executing' WHERE id=?", (proposal["repair_proposal"],))
        c.execute("UPDATE repair_grants SET rounds_used=1 WHERE id=?", (grant["id"],))
    reopened = repairs.RepairController(h.sessions.path, h.jobs, h.pipeline)
    reopened.recover()
    result = reopened.execute(context, proposal["repair_proposal"])
    assert result["status"] == "recovery_required"
    assert reopened.grant(h.sid, grant["id"])["rounds_used"] == 1
    h.checker.assert_not_called()
    h.pipeline.submit.assert_not_called()


def test_completed_submission_is_not_replayed_after_restart(harness):
    h, case = harness, harness.make()
    proposal, grant, context = h.approve(case)
    result = h.controller.execute(context, proposal["repair_proposal"])
    reopened = repairs.RepairController(h.sessions.path, h.jobs, h.pipeline)
    reopened.recover()
    again = reopened.execute(context, proposal["repair_proposal"])
    assert again["child_job_id"] == result["child_job_id"]
    assert reopened.grant(h.sid, grant["id"])["rounds_used"] == 1
    h.checker.assert_called_once()
    h.pipeline.submit.assert_called_once()


def test_uncertain_submission_failure_is_not_retried_after_restart(harness):
    h, case = harness, harness.make()
    proposal, grant, context = h.approve(case)
    h.pipeline.submit.side_effect = RuntimeError("synthetic disconnect after submission may have occurred")
    with pytest.raises(RuntimeError, match="synthetic disconnect"):
        h.controller.execute(context, proposal["repair_proposal"])
    reopened = repairs.RepairController(h.sessions.path, h.jobs, h.pipeline)
    reopened.recover()
    result = reopened.execute(context, proposal["repair_proposal"])
    assert result["status"] in {"stopped", "recovery_required"}
    assert result["outcome"]["execution_may_have_completed"] is True
    assert reopened.grant(h.sid, grant["id"])["rounds_used"] == 1
    h.checker.assert_called_once()
    h.pipeline.submit.assert_called_once()


@pytest.mark.parametrize("unknown_state", ["recovery_required", "uncertain_submission"])
def test_uncertain_previous_attempt_blocks_a_new_candidate(harness, unknown_state):
    h, case = harness, harness.make()
    bad = h.propose(case, line=case.source.splitlines().index("module counter8 (") + 1)
    bad, grant, context = h.approve(case, bad)
    if unknown_state == "recovery_required":
        with h.controller._c() as c:
            c.execute("UPDATE repair_proposals SET status='executing' WHERE id=?", (bad["repair_proposal"],))
            c.execute("UPDATE repair_grants SET rounds_used=1 WHERE id=?", (grant["id"],))
        h.controller.recover()
    else:
        h.pipeline.submit.side_effect = RuntimeError("synthetic uncertain submission")
        with pytest.raises(RuntimeError):
            h.controller.execute(context, bad["repair_proposal"])
    h.checker.reset_mock()
    h.pipeline.submit.reset_mock(side_effect=True)
    with pytest.raises(repairs.RepairDenied):
        next_proposal = h.propose(case, context=replace(context, action_id="new-after-uncertainty"))
        h.controller.execute(replace(context, action_id="execute-after-uncertainty"), next_proposal["repair_proposal"])
    assert h.controller.grant(h.sid, grant["id"])["rounds_used"] == 1
    h.checker.assert_not_called()
    h.pipeline.submit.assert_not_called()


def test_concurrent_model_reservations_cannot_overdraw(harness):
    h, case = harness, harness.make()
    _, grant, _ = h.approve(case, max_model_calls=1)
    barrier = threading.Barrier(2)

    def reserve():
        barrier.wait(timeout=5)
        try:
            repairs.reserve_model_for_turn(h.sessions.path, grant["id"], h.sid)
            return "reserved"
        except repairs.RepairDenied:
            return "denied"

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(lambda _: reserve(), range(2))) == ["denied", "reserved"]
    assert h.controller.grant(h.sid, grant["id"])["model_calls_used"] == 1


@pytest.mark.parametrize("name,arguments", [
    ("start_job", {"designs": [{"example": "counter8"}]}),
    ("start_batch", {"path": "/controlled/synthetic/path"}),
])
def test_repair_grant_cannot_bypass_controller_with_generic_submission(harness, monkeypatch, name, arguments):
    from r2g_agent import tools

    h, case = harness, harness.make()
    _, _, context = h.approve(case)
    function = Mock(return_value={"job_id": "must-not-be-created"})
    monkeypatch.setitem(tools.REGISTRY, name, {**tools.REGISTRY[name], "fn": function})
    result = tools.call(name, arguments, context=context)
    assert "error" in result
    function.assert_not_called()


def test_repair_grant_tools_only_expose_registered_scope(harness):
    from r2g_agent import tools

    h, case = harness, harness.make()
    _, _, context = h.approve(case)
    names = {s["name"] for s in tools.specs(session_scoped=True, context=context)}
    assert "execute_rtl_repair" in names
    assert names <= tools.REVIEW_TOOLS | {"execute_rtl_repair"}
    assert not names & {"start_job", "start_batch", "cancel_job", "check_environment"}


def test_repair_grant_read_tools_cannot_access_unrelated_job(harness, monkeypatch):
    from r2g_agent import tools

    h, case = harness, harness.make()
    _, _, context = h.approve(case)
    function = Mock(return_value={"job_id": "unrelated"})
    monkeypatch.setitem(tools.REGISTRY, "job_status", {**tools.REGISTRY["job_status"], "fn": function})
    result = tools.call("job_status", {"job_id": "unrelated"}, context=context)
    assert result.get("error_code") == "repair_job_scope"
    function.assert_not_called()


def test_model_cannot_supply_its_own_host_context(harness, monkeypatch):
    from r2g_agent import tools

    h, case = harness, harness.make()
    function = Mock(return_value={"status": "must-not-execute"})
    monkeypatch.setitem(tools.REGISTRY, "execute_rtl_repair", {
        **tools.REGISTRY["execute_rtl_repair"], "fn": function})
    result = tools.call("execute_rtl_repair", {"proposal_id": "fake", "_context": {"grant_id": "fake"}},
                        context=case.context)
    assert "error" in result
    function.assert_not_called()


def test_attempt_root_symlink_cannot_redirect_candidate_writes(harness, tmp_path):
    h, case = harness, harness.make()
    proposal, _, context = h.approve(case)
    outside = tmp_path / "outside-attempt-root"
    outside.mkdir()
    h.controller.root.symlink_to(outside, target_is_directory=True)
    with pytest.raises((repairs.RepairDenied, OSError, ValueError)):
        h.controller.execute(context, proposal["repair_proposal"])
    assert list(outside.iterdir()) == []
    h.checker.assert_not_called()
    h.pipeline.submit.assert_not_called()


@pytest.fixture
def child(harness, tmp_path):
    """A child with synthetic successful stage bindings, for negative gates."""
    h, parent = harness, harness.make()
    proposal, _, context = h.approve(parent)
    h.controller.execute(context, proposal["repair_proposal"])
    source = h.pipeline.submit.call_args.args[0][0]
    receipt = source["repair_receipt"]
    project = tmp_path / "child-project"
    (project / "rtl").mkdir(parents=True)
    (project / "constraints").mkdir()
    (project / "rtl/counter8.v").write_text(GOOD_RTL)
    (project / "constraints/design.sdc").write_text("create_clock -period 10 [get_ports clk]\n")
    snapshot = capture(project)
    bindings = {stage: {"before": reference(snapshot), "after": reference(snapshot),
                       "unchanged": True, "exit_code": 0, "execution_id": "synthetic-" + stage}
                for stage in ("synth", "flow", "signoff", "graph", "verify")}
    jid = h.jobs.create_job("synthetic child gate fixture", {"sources": [source]},
                            [{"name": "counter8", "project": str(project)}])
    h.jobs.set_job(jid, "running")
    h.jobs.set_design(jid, "counter8", status="running", detail={"input_bindings": bindings})
    return SimpleNamespace(h=h, jid=jid, project=project, receipt=receipt, bindings=bindings, snapshot=snapshot)


def test_child_receipt_reports_and_validation_are_bound_to_current_inputs(child):
    c = child
    repairs.prepare_child_receipt(c.h.jobs, c.jid, "counter8", c.project)
    assert json.loads((c.project / "reports/agent_repair.json").read_text()) == c.receipt
    assert json.loads((c.project / "reports/functional_check.json").read_text()) == c.receipt["cheap_checks"]
    repairs.validate_child(c.h.jobs, c.jid, "counter8", c.project)
    verdict = c.h.jobs.design(c.jid, "counter8")["detail"]["repair_validation"]
    assert verdict["status"] == "verified"
    assert verdict["snapshot"] == capture(c.project)


@pytest.mark.parametrize("when", ["prepare", "validate"])
def test_child_rejects_oracle_version_change(child, monkeypatch, when):
    c = child
    monkeypatch.setattr(repairs, "oracle_policy", lambda: {**c.h.policy, "testbench_sha256": "changed"})
    function = repairs.prepare_child_receipt if when == "prepare" else repairs.validate_child
    with pytest.raises(repairs.RepairDenied):
        function(c.h.jobs, c.jid, "counter8", c.project)
    assert "repair_validation" not in c.h.jobs.design(c.jid, "counter8")["detail"]


@pytest.mark.parametrize("stage", ["synth", "flow", "signoff", "graph", "verify"])
@pytest.mark.parametrize("defect", ["missing", "failed", "changed", "stale"])
def test_child_cannot_validate_missing_failed_or_stale_required_stage(child, stage, defect):
    c = child
    if defect == "missing":
        del c.bindings[stage]
    elif defect == "failed":
        c.bindings[stage]["exit_code"] = 1
    elif defect == "changed":
        c.bindings[stage]["unchanged"] = False
    else:
        c.bindings[stage]["before"]["rtl_sha256"] = "different-source-version"
    c.h.jobs.set_design(c.jid, "counter8", detail={"input_bindings": c.bindings})
    with pytest.raises(repairs.RepairDenied):
        repairs.validate_child(c.h.jobs, c.jid, "counter8", c.project)
    assert "repair_validation" not in c.h.jobs.design(c.jid, "counter8")["detail"]


@pytest.mark.parametrize("part", ["rtl/counter8.v", "constraints/design.sdc"])
def test_child_rejects_postverification_input_change(child, part):
    c = child
    path = c.project / part
    path.write_bytes(path.read_bytes() + b"\n// later mutation\n")
    with pytest.raises(repairs.RepairDenied):
        repairs.validate_child(c.h.jobs, c.jid, "counter8", c.project)


@pytest.mark.parametrize("stage", ["flow", "signoff", "graph"])
def test_child_cannot_mix_physical_results_from_other_constraints(child, stage):
    c = child
    for boundary in ("before", "after"):
        c.bindings[stage][boundary]["constraints_sha256"] = "other-constraints"
        c.bindings[stage][boundary]["sha256"] = "other-total-inputs"
    # A stage may be stable internally yet belong to an obsolete constraint set.
    c.h.jobs.set_design(c.jid, "counter8", detail={"input_bindings": c.bindings})
    with pytest.raises(repairs.RepairDenied):
        repairs.validate_child(c.h.jobs, c.jid, "counter8", c.project)


def test_child_allows_explicit_sizing_change_between_synthesis_and_physical_flow(child):
    c = child
    for boundary in ("before", "after"):
        c.bindings["synth"][boundary]["constraints_sha256"] = "presizing-constraints"
        c.bindings["synth"][boundary]["sha256"] = "presizing-inputs"
    c.h.jobs.set_design(c.jid, "counter8", detail={"input_bindings": c.bindings})
    repairs.validate_child(c.h.jobs, c.jid, "counter8", c.project)
    assert c.h.jobs.design(c.jid, "counter8")["detail"]["repair_validation"]["status"] == "verified"


@pytest.mark.parametrize("kind", ["file_symlink", "directory_symlink", "ancestor_symlink", "fifo"])
def test_input_capture_rejects_nonregular_and_linked_paths(tmp_path, kind):
    root, outside = tmp_path / "inputs", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "source.v").write_text("controlled fixture")
    if kind == "file_symlink":
        (root / "linked.v").symlink_to(outside / "source.v")
    elif kind == "directory_symlink":
        (root / "linked").symlink_to(outside, target_is_directory=True)
    elif kind == "ancestor_symlink":
        alias = tmp_path / "alias"
        alias.symlink_to(root, target_is_directory=True)
        (root / "nested").mkdir()
        root = alias / "nested"
    else:
        os.mkfifo(root / "named-pipe")
    with pytest.raises((ValueError, OSError)):
        read_tree(root)
    assert (outside / "source.v").read_text() == "controlled fixture"


@pytest.mark.parametrize("bound", ["bytes", "files", "depth"])
def test_input_capture_has_bounded_size_count_and_depth(tmp_path, monkeypatch, bound):
    if bound == "bytes":
        monkeypatch.setattr(input_snapshots, "MAX_INPUT_BYTES", 4)
        (tmp_path / "large.v").write_bytes(b"12345")
    elif bound == "files":
        monkeypatch.setattr(input_snapshots, "MAX_INPUT_FILES", 1)
        (tmp_path / "one.v").write_bytes(b"1")
        (tmp_path / "two.v").write_bytes(b"2")
    else:
        nested = tmp_path
        for _ in range(26):
            nested /= "nested"
        nested.mkdir(parents=True)
    with pytest.raises(ValueError, match="limit"):
        read_tree(tmp_path)


def test_input_capture_empty_directories_count_toward_traversal_bound(tmp_path, monkeypatch):
    monkeypatch.setattr(input_snapshots, "MAX_INPUT_FILES", 2)
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "c").mkdir()
    with pytest.raises(ValueError, match="limit"):
        read_tree(tmp_path)


def test_input_capture_rejects_mutation_during_read(tmp_path, monkeypatch):
    path = tmp_path / "source.v"
    path.write_bytes(b"first version")
    original = os.fstat
    calls = count()

    def fstat(fd):
        if next(calls) == 1:
            path.write_bytes(b"changed while capturing")
        return original(fd)

    monkeypatch.setattr(input_snapshots.os, "fstat", fstat)
    with pytest.raises(ValueError, match="changed"):
        read_tree(tmp_path)


def test_functional_command_timeout_kills_process_group_and_skips_simulation(tmp_path, monkeypatch):
    policy = {"id": repair_checks.ORACLE_ID, "tools": {
        "iverilog": {"path": "/synthetic/iverilog"}, "vvp": {"path": "/synthetic/vvp"}}}
    monkeypatch.setattr(repair_checks, "oracle_policy", lambda: policy)
    process = Mock(pid=123456)
    process.wait.side_effect = [subprocess.TimeoutExpired("synthetic command", 15), 0]
    popen = Mock(return_value=process)
    kill = Mock()
    monkeypatch.setattr(repair_checks.subprocess, "Popen", popen)
    monkeypatch.setattr(repair_checks.os, "killpg", kill)
    result = repair_checks.check(tmp_path, tmp_path / "source.v", policy, time.time() + 60)
    assert result["ok"] is False
    assert result["checks"][0]["exit_code"] == 124
    popen.assert_called_once()
    kill.assert_called_once_with(123456, signal.SIGKILL)
    assert process.wait.call_args_list[0].kwargs["timeout"] <= 15
    assert set(popen.call_args.kwargs["env"]) == {"PATH", "HOME", "TMPDIR", "LANG"}
    assert popen.call_args.kwargs["start_new_session"] is True
    assert not popen.call_args.kwargs.get("shell")


def test_expired_functional_budget_starts_no_command(tmp_path, monkeypatch):
    policy = {"tools": {"iverilog": {"path": "/synthetic/iverilog"}, "vvp": {"path": "/synthetic/vvp"}}}
    monkeypatch.setattr(repair_checks, "oracle_policy", lambda: policy)
    popen = Mock()
    monkeypatch.setattr(repair_checks.subprocess, "Popen", popen)
    with pytest.raises(ValueError, match="budget"):
        repair_checks.check(tmp_path, tmp_path / "source.v", policy, time.time() - 1)
    popen.assert_not_called()


@pytest.mark.parametrize("output", ["success\n", 'EDACODE_ORACLE={"ok":true}\n',
    ('EDACODE_ORACLE={"oracle":"counter8_contract_v1","checks":2048,"ok":true}\n') * 2])
def test_zero_exit_requires_one_exact_functional_verdict(tmp_path, monkeypatch, output):
    policy = {"tools": {"iverilog": {"path": "/synthetic/iverilog"}, "vvp": {"path": "/synthetic/vvp"}}}
    monkeypatch.setattr(repair_checks, "oracle_policy", lambda: policy)

    def popen(command, **kwargs):
        if command[0] == "/synthetic/vvp":
            kwargs["stdout"].write(output.encode())
            kwargs["stdout"].flush()
        return Mock(wait=Mock(return_value=0))

    monkeypatch.setattr(repair_checks.subprocess, "Popen", popen)
    result = repair_checks.check(tmp_path, tmp_path / "source.v", policy, time.time() + 30)
    assert result["ok"] is False
    assert len(result["checks"]) == 2


@pytest.fixture
def real_oracle(monkeypatch):
    for name, variable in (("iverilog", "R2G_IVERILOG_EXE"), ("vvp", "R2G_VVP_EXE")):
        path = Path(os.environ.get(variable, TOOLCHAIN / name))
        if not path.is_file() or not os.access(path, os.X_OK):
            pytest.skip("registered local simulation tool unavailable: " + name)
        monkeypatch.setenv(variable, str(path))
    return repair_checks.oracle_policy()


@pytest.mark.parametrize("variant,passed,checks", [("correct", True, 2), ("wrong_behavior", False, 2), ("syntax_error", False, 1)])
def test_real_contract_distinguishes_functional_pass_from_syntax_and_behavior(tmp_path, real_oracle, variant, passed, checks):
    source = {"correct": GOOD_RTL, "wrong_behavior": GOOD_RTL.replace("q + 8'd1", "q + 8'd2"),
              "syntax_error": BROKEN_RTL}[variant]
    rtl = tmp_path / "counter8.v"
    rtl.write_text(source)
    result = repair_checks.check(tmp_path, rtl, real_oracle, time.time() + 30)
    assert result["ok"] is passed
    assert len(result["checks"]) == checks
    for check in result["checks"]:
        log = tmp_path / (check["name"] + ".log")
        assert check["log_sha256"] == hashlib.sha256(log.read_bytes()).hexdigest()
    if passed:
        assert result["functional_result"] == {"oracle": repair_checks.ORACLE_ID, "checks": 2048, "ok": True}
    else:
        assert result["checks"][-1]["exit_code"] != 0


def test_real_contract_failed_candidate_then_correction_only_submits_checked_copy(harness, real_oracle, monkeypatch):
    h, case = harness, harness.make()
    monkeypatch.setattr(repairs, "oracle_policy", lambda: real_oracle)
    monkeypatch.setattr(repairs, "check", repair_checks.check)
    before = capture(case.project)
    bad = h.propose(case, line=case.source.splitlines().index("module counter8 (") + 1)
    bad, grant, context = h.approve(case, bad)
    failed = h.controller.execute(context, bad["repair_proposal"])
    assert failed["status"] == "check_failed"
    assert failed["outcome"]["cheap_checks"]["checks"][0]["exit_code"] != 0
    h.pipeline.submit.assert_not_called()
    good = h.propose(case, context=replace(context, action_id="real-correct-candidate"))
    result = h.controller.execute(replace(context, action_id="real-correct-execution"), good["repair_proposal"])
    assert result["status"] == "running"
    h.pipeline.submit.assert_called_once()
    receipt = h.pipeline.submit.call_args.args[0][0]["repair_receipt"]
    assert receipt["cheap_checks"]["functional_result"] == {
        "oracle": repair_checks.ORACLE_ID, "checks": 2048, "ok": True}
    assert receipt["policy_sha256"] == digest(real_oracle)
    assert capture(case.project) == before
    assert h.controller.grant(h.sid, grant["id"])["rounds_used"] == 2


@pytest.fixture
def approval_bridge(harness, monkeypatch):
    from r2g_agent.event_bridge import BridgeWorker

    bridge = BridgeWorker(harness.sessions, harness.jobs, Mock(side_effect=AssertionError("no model in this regression")))
    # Exercise actual durable enqueue without starting a model worker thread.
    monkeypatch.setattr(bridge, "launch", Mock())
    return bridge


def _approval_outbox(controller, grant_id):
    with controller._c() as c:
        return dict(c.execute("SELECT * FROM repair_approval_outbox WHERE grant_id=?", (grant_id,)).fetchone())


def test_approval_outbox_survives_crash_before_enqueue_and_dispatches_once(harness, approval_bridge):
    h, case = harness, harness.make()
    proposal, grant, _ = h.approve(case)
    assert _approval_outbox(h.controller, grant["id"])["status"] == "pending"
    assert h.sessions.turns.by_key("repair-grant:" + grant["id"]) is None
    reopened = repairs.RepairController(h.sessions.path, h.jobs, h.pipeline)
    first = reopened.dispatch_approvals(approval_bridge)
    second = reopened.dispatch_approvals(approval_bridge)
    turns = [turn for turn in h.sessions.turns.list(h.sid) if turn["kind"] == "repair_review"]
    assert len(turns) == 1
    assert first[grant["id"]]["id"] == turns[0]["id"]
    assert second == {}
    assert turns[0]["request_key"] == "repair-grant:" + grant["id"]
    assert turns[0]["payload"]["grant_id"] == grant["id"]
    assert turns[0]["job_id"] == case.jid
    assert proposal["repair_proposal"] in turns[0]["payload"]["message"]
    assert _approval_outbox(reopened, grant["id"])["turn_id"] == turns[0]["id"]
    approval_bridge.launch.assert_called_once()
    h.checker.assert_not_called()
    h.pipeline.submit.assert_not_called()


def test_approval_outbox_reuses_turn_committed_before_dispatch_ack(harness, approval_bridge, monkeypatch):
    h, case = harness, harness.make()
    _, grant, _ = h.approve(case)
    enqueue = approval_bridge.enqueue

    def commit_then_interrupt(*args, **kwargs):
        enqueue(*args, **kwargs)
        raise RuntimeError("synthetic interruption after durable turn creation")

    with monkeypatch.context() as local:
        local.setattr(approval_bridge, "enqueue", commit_then_interrupt)
        with pytest.raises(RuntimeError, match="after durable turn"):
            h.controller.dispatch_approvals(approval_bridge)
    committed = h.sessions.turns.by_key("repair-grant:" + grant["id"])
    assert committed is not None
    assert _approval_outbox(h.controller, grant["id"])["status"] == "pending"
    reopened = repairs.RepairController(h.sessions.path, h.jobs, h.pipeline)
    resumed = reopened.dispatch_approvals(approval_bridge)
    assert resumed[grant["id"]]["id"] == committed["id"]
    assert len([t for t in h.sessions.turns.list(h.sid) if t["kind"] == "repair_review"]) == 1
    assert _approval_outbox(reopened, grant["id"])["status"] == "dispatched"


@pytest.mark.parametrize("revoked", [False, True])
def test_approval_outbox_expires_pending_authority_without_enqueue(harness, approval_bridge, revoked):
    h, case = harness, harness.make()
    proposal, grant, _ = h.approve(case)
    with h.controller._c() as c:
        c.execute("UPDATE repair_grants SET deadline=?,status=? WHERE id=?",
                  (time.time() - 1, "revoked" if revoked else "approved", grant["id"]))
    reopened = repairs.RepairController(h.sessions.path, h.jobs, h.pipeline)
    assert reopened.dispatch_approvals(approval_bridge) == {}
    assert reopened.dispatch_approvals(approval_bridge) == {}
    assert _approval_outbox(reopened, grant["id"])["status"] == "expired"
    assert reopened.proposal(h.sid, proposal["repair_proposal"])["status"] == (
        "revoked" if revoked else "budget_exhausted")
    assert h.sessions.turns.by_key("repair-grant:" + grant["id"]) is None
    approval_bridge.launch.assert_not_called()
    h.pipeline.submit.assert_not_called()


def test_approval_api_enqueue_failure_keeps_recoverable_committed_grant(harness, approval_bridge, monkeypatch):
    from r2g_agent import repair_api

    h, case = harness, harness.make()
    proposal = h.propose(case)
    state = {"sessions": h.sessions, "bridge": approval_bridge}
    monkeypatch.setattr(repair_api, "_runtime", lambda: (state, h.controller))
    body = {"source_snapshot_id": proposal["source_snapshot_id"]}
    request = SimpleNamespace(json=AsyncMock(return_value=body))
    # Call the real async endpoint without HTTP worker threads or sockets.
    with monkeypatch.context() as local:
        local.setattr(approval_bridge, "enqueue", Mock(side_effect=RuntimeError("synthetic bridge unavailable")))
        with pytest.raises(RuntimeError, match="bridge unavailable"):
            asyncio.run(repair_api.approve(h.sid, proposal["repair_proposal"], request))
    persisted = h.controller.proposal(h.sid, proposal["repair_proposal"])
    gid = persisted["grant_id"]
    assert persisted["status"] == "approved"
    assert _approval_outbox(h.controller, gid)["status"] == "pending"
    reopened = repairs.RepairController(h.sessions.path, h.jobs, h.pipeline)
    reopened.dispatch_approvals(approval_bridge)
    response = asyncio.run(repair_api.approve(h.sid, proposal["repair_proposal"], request))
    assert response["grant"]["id"] == gid
    assert response["turn"]["id"] == _approval_outbox(reopened, gid)["turn_id"]
    assert len([t for t in h.sessions.turns.list(h.sid) if t["kind"] == "repair_review"]) == 1
    assert h.controller.grant(h.sid, gid)["rounds_used"] == 0
    h.pipeline.submit.assert_not_called()


@pytest.fixture
def committed_repair_child(harness):
    """Simulate the cross-database crash window using a real job commit."""
    h, case = harness, harness.make()
    proposal, grant, context = h.approve(case)
    committed = []

    def commit_then_interrupt(sources, **kwargs):
        submission = {**kwargs["submission"], "request_hash": digest(sources),
                      "source_snapshot": json.dumps({"fixture": "synthetic-only", "sources": sources})}
        jid, created = h.jobs.create_submission(
            "synthetic committed repair child", {"sources": sources},
            [{"name": sources[0]["name"], "project": sources[0]["path"]}], submission=submission)
        assert created
        h.jobs.set_job(jid, "running")
        committed.append(jid)
        raise RuntimeError("synthetic crash after child commit before proposal update")

    h.pipeline.submit.side_effect = commit_then_interrupt
    with pytest.raises(RuntimeError, match="after child commit"):
        h.controller.execute(context, proposal["repair_proposal"])
    assert h.controller.proposal(h.sid, proposal["repair_proposal"])["child_job_id"] is None
    return SimpleNamespace(h=h, case=case, proposal=proposal, grant=grant, context=context, jid=committed[0])


@pytest.mark.parametrize("prior_state", ["executing", "recovery_required", "stopped"])
def test_committed_child_is_reconciled_without_replaying_execution(committed_repair_child, prior_state):
    c, h = committed_repair_child, committed_repair_child.h
    with h.controller._c() as db:
        db.execute("UPDATE repair_proposals SET status=? WHERE id=?", (prior_state, c.proposal["repair_proposal"]))
    reopened = repairs.RepairController(h.sessions.path, h.jobs, h.pipeline)
    reopened.recover()
    reopened.tick()
    first = reopened.proposal(h.sid, c.proposal["repair_proposal"])
    assert first["child_job_id"] == c.jid
    assert first["status"] == "running"
    reopened.tick()
    result = reopened.execute(c.context, c.proposal["repair_proposal"])
    assert result["child_job_id"] == c.jid
    assert h.jobs.job_for_submission("repair:" + c.proposal["repair_proposal"])["job_id"] == c.jid
    assert reopened.grant(h.sid, c.grant["id"])["rounds_used"] == 1
    h.checker.assert_called_once()
    h.pipeline.submit.assert_called_once()
    h.pipeline.cancel.assert_not_called()


def test_expired_uncertain_child_stays_monitored_until_terminal(committed_repair_child):
    c, h = committed_repair_child, committed_repair_child.h
    h.jobs.set_job(c.jid, "recovery_required")
    h.pipeline.cancel.side_effect = h.jobs.request_cancel
    with h.controller._c() as db:
        db.execute("UPDATE repair_grants SET deadline=? WHERE id=?", (time.time() - 1, c.grant["id"]))
    reopened = repairs.RepairController(h.sessions.path, h.jobs, h.pipeline)
    reopened.tick()
    uncertain = reopened.proposal(h.sid, c.proposal["repair_proposal"])
    assert uncertain["child_job_id"] == c.jid
    assert uncertain["status"] == "budget_exhausted"
    assert uncertain["outcome"]["execution_may_have_completed"] is True
    assert h.jobs.job(c.jid)["status"] == "recovery_required"
    assert h.jobs.cancel_requested(c.jid)
    reopened.tick()
    assert h.pipeline.cancel.call_count == 2
    assert reopened.proposal(h.sid, c.proposal["repair_proposal"])["outcome"]["execution_may_have_completed"] is True
    h.jobs.set_job(c.jid, "cancelled", result={"fixture": "synthetic confirmed stop"})
    reopened.tick()
    terminal = reopened.proposal(h.sid, c.proposal["repair_proposal"])
    assert terminal["status"] == "budget_exhausted"
    assert terminal["outcome"]["execution_may_have_completed"] is False
    reopened.tick()
    assert h.pipeline.cancel.call_count == 2
    h.checker.assert_called_once()
    h.pipeline.submit.assert_called_once()


@pytest.mark.parametrize("terminal", ["done", "failed", "cancelled"])
def test_committed_child_completed_within_budget_is_reconciled_after_deadline(committed_repair_child, monkeypatch, terminal):
    c, h = committed_repair_child, committed_repair_child.h
    h.jobs.set_job(c.jid, terminal, result={"fixture": "synthetic terminal result"})
    assert h.jobs.job(c.jid)["updated"] < c.grant["deadline"]
    monkeypatch.setattr(repairs, "time", SimpleNamespace(time=lambda: c.grant["deadline"] + 1))
    reopened = repairs.RepairController(h.sessions.path, h.jobs, h.pipeline)
    reopened.tick()
    result = reopened.proposal(h.sid, c.proposal["repair_proposal"])
    assert result["status"] == ("validated" if terminal == "done" else "stopped")
    assert result["outcome"]["job_id"] == c.jid
    assert result["outcome"]["status"] == terminal
    assert "execution_may_have_completed" not in result["outcome"]
    h.pipeline.cancel.assert_not_called()
    h.pipeline.submit.assert_called_once()


def test_child_completing_after_deadline_never_becomes_validated(committed_repair_child):
    c, h = committed_repair_child, committed_repair_child.h
    with h.controller._c() as db:
        db.execute("UPDATE repair_grants SET deadline=? WHERE id=?", (time.time() - 1, c.grant["id"]))
    h.jobs.set_job(c.jid, "done", result={"fixture": "synthetic late completion"})
    reopened = repairs.RepairController(h.sessions.path, h.jobs, h.pipeline)
    reopened.tick()
    result = reopened.proposal(h.sid, c.proposal["repair_proposal"])
    assert result["status"] == "budget_exhausted"
    assert result["outcome"]["execution_may_have_completed"] is False
    h.pipeline.cancel.assert_not_called()
    h.pipeline.submit.assert_called_once()
