"""A repeat notice is evidence for the next decision, never execution authority."""
import shutil
import subprocess

import pytest

from r2g_agent import agent_loop, tools
from r2g_agent.jobs import JobStore


class DiagnosticModel:
    """Scripted responsiveness; does not stand in for a real model benchmark."""
    def __init__(self, ignore=False):
        self.systems, self.requests = [], []
        self.primary = 0
        self.inspected = self.compiled = False
        self.ignore = ignore

    def stream(self, messages, specs, system):
        self.systems.append(system)
        self.requests.append(messages)
        if "[repeated_tool_call]" in system and not self.ignore:
            self.inspected = True
        if self.inspected and not self.compiled:
            name = "rea_inspect_dependency"
            if any(m.get("name") == name for m in messages):
                name = "rea_compile_with_dependency"
                self.compiled = True
        elif self.compiled:
            name = None
        elif self.primary < 5:
            self.primary += 1
            name = "rea_compile_primary"
        else:
            name = None
        if name:
            yield {"type": "tool_call", "id": "call-%d" % len(self.systems), "name": name, "arguments": {}}
        else:
            yield {"type": "text", "delta": "Compile-only recovery; physical signoff unknown." if self.compiled else "Blocked: no new evidence."}
        yield {"type": "done", "metadata": {"stream_complete": True, "usage": {"status": "unresolved"}}}


@pytest.fixture
def diagnosis(tmp_path, monkeypatch):
    compiler = shutil.which("iverilog")
    if not compiler:
        pytest.skip("Icarus is required for this real compilation fixture")
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    top, child = fixture / "top.v", fixture / "child.v"
    top.write_text("module top(input clk, output q); missing_cell u0(.clk(clk),.q(q)); endmodule\n")
    child.write_text("module missing_cell(input clk, output q); assign q=clk; endmodule\n")
    observations = []
    def primary():
        return compile_files(False)
    def full():
        return compile_files(True)
    def inspect():
        result = {"file": "child.v", "text": child.read_text()}
        observations.append(("inspect", result))
        return result
    def compile_files(with_child):
        args = [compiler, "-g2012", "-s", "top", "-o", str(fixture / "test.out"), str(top)]
        result = subprocess.run(args + ([str(child)] if with_child else []), capture_output=True, text=True, timeout=5)
        value = {"exit_code": result.returncode, "stderr": result.stderr, "scope": "compilation only"}
        observations.append(("full" if with_child else "primary", value))
        return value
    for name, fn in (("rea_compile_primary", primary), ("rea_compile_with_dependency", full), ("rea_inspect_dependency", inspect)):
        monkeypatch.setitem(tools.REGISTRY, name, {"fn": fn, "spec": {"name": name,
            "description": "isolated compilation fixture", "parameters": {"type": "object", "properties": {}}}})
    monkeypatch.setattr(tools.RT, "store", JobStore())
    sessions = agent_loop.SessionStore()
    return sessions, observations, top


def test_notice_changes_next_diagnostic_without_claiming_eda_pass(diagnosis):
    sessions, observations, top = diagnosis
    original = top.read_bytes()
    model = DiagnosticModel()
    sid = sessions.create()
    events = list(agent_loop.run_turn(model, sessions, sid, "Diagnose dependency; preserve RTL."))
    assert [name for name, _ in observations] == ["primary", "primary", "primary", "inspect", "full"]
    assert observations[-1][1]["exit_code"] == 0 and top.read_bytes() == original
    assert events[-1]["type"] == "done"
    recorded = [call["context"].get("loop_feedback") for call in sessions.model_calls(sid)]
    assert next(item for item in recorded if item)["consecutive_calls"] == 3
    assert "physical signoff unknown" in sessions.load(sid)[-1]["content"]


def test_baseline_without_notice_keeps_repeating(diagnosis, monkeypatch):
    sessions, observations, _ = diagnosis
    monkeypatch.setattr(agent_loop, "_repeat_notice", lambda actions: None, raising=False)
    model = DiagnosticModel()
    list(agent_loop.run_turn(model, sessions, sessions.create(), "Diagnose dependency."))
    assert len(observations) == 5 and all(item[1]["exit_code"] != 0 for item in observations)


def test_notice_does_not_force_scripted_model_to_change(diagnosis):
    sessions, observations, _ = diagnosis
    model = DiagnosticModel(ignore=True)
    list(agent_loop.run_turn(model, sessions, sessions.create(), "Diagnose dependency."))
    assert any("[repeated_tool_call]" in system for system in model.systems)
    assert len(observations) == 5 and all(item[1]["exit_code"] != 0 for item in observations)


def test_notice_resets_at_next_user_turn(diagnosis):
    sessions, _, _ = diagnosis
    model = DiagnosticModel(ignore=True)
    sid = sessions.create()
    list(agent_loop.run_turn(model, sessions, sid, "First task."))
    second = DiagnosticModel()
    list(agent_loop.run_turn(second, sessions, sid, "New task."))
    assert "[repeated_tool_call]" not in second.systems[0]


def test_repeat_with_new_observations_is_not_reported_as_identical(diagnosis, monkeypatch):
    sessions, _, _ = diagnosis
    count = 0
    def evolving_observation():
        nonlocal count
        count += 1
        return {"event_cursor": count, "status": "running"}
    entry = tools.REGISTRY["rea_compile_primary"]
    monkeypatch.setitem(tools.REGISTRY, "rea_compile_primary", {**entry, "fn": evolving_observation})
    model = DiagnosticModel(ignore=True)
    sid = sessions.create()
    list(agent_loop.run_turn(model, sessions, sid, "Observe a progressing fixture."))
    feedback = [c["context"]["loop_feedback"] for c in sessions.model_calls(sid) if "loop_feedback" in c["context"]]
    assert feedback and all(item["last_three_observations_identical"] is False for item in feedback)


def test_repeat_reminder_does_not_authorize_mutation_in_review(fake_adapter, monkeypatch):
    sessions = agent_loop.SessionStore()
    monkeypatch.setattr(tools.RT, "store", JobStore())
    entry = tools.REGISTRY["start_job"]
    def forbidden(**kwargs):
        pytest.fail("a repeat reminder cannot authorize a job submission")
    monkeypatch.setitem(tools.REGISTRY, "start_job", {**entry, "fn": forbidden})
    sid = sessions.create()
    turn, _ = sessions.turns.create(sid, "read-only-repeat", {"message": "Review only."}, kind="job_review", max_model_calls=4)
    model = fake_adapter([[{"type": "tool_call", "id": str(i), "name": "start_job", "arguments": {}}] for i in range(4)])
    events = list(agent_loop.run_turn(model, sessions, sid, "Review only.", turn_id=turn["id"]))
    results = [event["result"] for event in events if event["type"] == "tool_result"]
    assert len(results) == 4 and all(result["error_code"] == "read_only_review" for result in results)
    assert sessions.model_calls(sid)[-1]["context"]["loop_feedback"]["consecutive_calls"] == 3
