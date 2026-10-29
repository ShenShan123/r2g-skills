"""Run separately with envs/rtlfixer/bin/python; no external model calls."""
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "adapters"))


def test_fixed_roster_is_first_two_distinct_native_tasks():
    from fixer_runtime import first_two
    rows = first_two()
    assert rows[0]["row"] == 0 and rows[0]["task_id"] != rows[1]["task_id"]


def test_native_compiler_rag_and_shared_transport_construct_without_network():
    if sys.version_info[:2] != (3, 10):
        pytest.skip("RTLFixer requires its isolated pydantic-v1 environment")
    from fixer_runtime import first_two, make_agent
    from executors.vg_executor import VerilogExecutor
    class NeverSend:
        def create(self, **kwargs):
            raise AssertionError("construction must not call a model")
    transport = NeverSend()
    agent = make_agent(VerilogExecutor(), transport, first_two()[0]["item"])
    assert {t.name for t in agent.toolkit.tools} == {"verilog_compiler", "error_lookup"}
    assert agent.llm.client is transport
    assert agent.toolkit.llm.client is transport
    assert agent.agent_executor.agent.llm.client is transport


def test_real_native_rag_agent_compiler_and_simulator_with_offline_transport():
    if sys.version_info[:2] != (3, 10):
        pytest.skip("RTLFixer requires its isolated pydantic-v1 environment")
    import os
    import tempfile
    from fixer_runtime import FixerEvidence, execute_native, make_agent
    from executors.vg_executor import VerilogExecutor
    from bridge import FullMemory
    workspace = Path(tempfile.mkdtemp(prefix="rtlfixer-native-", dir=ROOT / "reports"))
    previous = Path.cwd()
    old_tmp = tempfile.tempdir
    old_path = os.environ["PATH"]
    try:
        os.chdir(workspace)
        tempfile.tempdir = str(workspace)
        os.environ["PATH"] = str(ROOT / "bin") + ":/usr/bin:/bin"
        memory = FullMemory("rtlfixer", root=workspace)
        item = {"task_id": "offline_adder", "prompt": "module top_module(input a, b, output out);",
                "solution": "module top_module(input a, b, output out); assign out = ; endmodule",
                "detail_description": "Output the sum modulo two of inputs a and b.",
                "test": 'module tb; reg a,b; wire out; top_module d(a,b,out); initial begin '
                        'a=0;b=1;#1; if(out!==1) $display("Mismatches: 1 in 1 samples"); '
                        'else $display("Mismatches: 0 in 1 samples"); $finish;end endmodule'}
        evidence = FixerEvidence(memory, item)
        executor = evidence.bind(VerilogExecutor())
        correct = "module top_module(input a, b, output out); assign out = a ^ b; endmodule"
        class OfflineTransport:
            count = 0
            def create(self, **kwargs):
                self.count += 1
                if kwargs.get("functions"):
                    import json
                    message = {"role": "assistant", "content": None, "function_call": {
                        "name": "verilog_compiler", "arguments": json.dumps({"code_completion": correct})}}
                else:
                    message = {"role": "assistant", "content": "YES"}
                return {"choices": [{"message": message, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                        "model": "offline-test-only"}
        transport = OfflineTransport()
        agent = make_agent(executor, transport, item)
        result = execute_native(agent, executor, item)
        from native_serialization import write_episode
        import json
        write_episode(workspace, {"status": "ACCEPTED", "cost": {"online_requests": 0}, "native_result": result})
        assert json.loads((workspace / "episode.json").read_text())["native_result"]["functional_verification"]["passed"]
        assert json.loads((workspace / "terminal-accounting.json").read_text())["status"] == "ACCEPTED"
        assert result["native_compile"]["passed"]
        assert result["functional_verification"]["passed"]
        assert evidence.counter >= 3 and transport.count > 0
        assert "error_lookup" in [t.name for t in agent.toolkit.tools]
        conn, _ = memory.backend._open()
        assert conn.execute("SELECT COUNT(*) FROM tehm_transitions").fetchone()[0] >= 3
        memory.close()
    finally:
        os.chdir(previous)
        tempfile.tempdir = old_tmp
        os.environ["PATH"] = old_path


def test_scalar_terminal_survives_unknown_rich_trajectory_type(tmp_path):
    from native_serialization import write_episode
    import json
    with pytest.raises(TypeError):
        write_episode(tmp_path, {"status": "TECHNICAL_STOP", "cost": {"known_tokens": 123},
                                 "native_result": {"unknown_object": object()}})
    assert json.loads((tmp_path / "terminal-accounting.json").read_text())["cost"]["known_tokens"] == 123


@pytest.mark.parametrize("success", [True, False])
def test_local_source_reuse_and_entry_restoration_use_native_evaluator(success):
    if sys.version_info[:2] != (3, 10):
        pytest.skip("RTLFixer isolated environment required")
    import os
    import tempfile
    from fixer_runtime import FixerEvidence
    from bridge import FullMemory
    from executors.vg_executor import VerilogExecutor
    from test_local_reuse import fixture_choice
    work = Path(tempfile.mkdtemp(prefix="fixer-local-", dir=ROOT / "reports"))
    previous, old_tmp, old_path = Path.cwd(), tempfile.tempdir, os.environ["PATH"]
    memory = FullMemory("rtlfixer", root=work)
    good = "module top_module(input a,b,output out);assign out=a^b;endmodule"
    bad = "module top_module(input a,b,output out);assign out=a&b;endmodule"
    item = {"task_id": "OFFLINE-FIXTURE", "prompt": "module top_module(input a,b,output out);",
        "solution": bad if success else good,
        "test": 'module tb;reg a,b;wire out;top_module d(a,b,out);initial begin '
                'a=0;b=1;#1;if(out!==1)$display("Mismatches: 1 in 1 samples");'
                'else $display("Mismatches: 0 in 1 samples");$finish;end endmodule'}
    try:
        os.chdir(work)
        tempfile.tempdir = str(work)
        os.environ["PATH"] = str(ROOT / "bin") + ":/usr/bin:/bin"
        evidence = FixerEvidence(memory, item)
        executor = evidence.bind(VerilogExecutor())
        executor.evaluate(item, item["solution"], item["test"], timeout=20, compile_only=False)
        entry = evidence.state()
        choice = fixture_choice(entry, good if success else bad)
        choice["action"]["payload"]["config_edits"] = {"candidate.sv": good if success else bad}
        result = evidence.try_local(executor, choice)
        assert (result is not None) is success
        if not success:
            assert evidence.state().canonical() == entry.canonical()
        events = [__import__("json").loads(l) for l in memory.journal.path.read_text().splitlines()]
        writes = [e for e in events if e["event"] == "memory_write"]
        assert all(e["rolled_back"] is (not success) for e in writes[-2:])
        assert writes[-1]["outcome"] == ("PASS" if success else "REGRESSION")
    finally:
        memory.close()
        os.chdir(previous)
        tempfile.tempdir = old_tmp
        os.environ["PATH"] = old_path
