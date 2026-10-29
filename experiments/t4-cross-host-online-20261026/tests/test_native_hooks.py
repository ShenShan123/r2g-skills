import json
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
os.environ["MSWEA_SILENT_STARTUP"] = "1"
os.environ["MSWEA_GLOBAL_CONFIG_DIR"] = str(ROOT / "inputs/mini-global")
sys.path[:0] = [str(ROOT / "adapters"), str(ROOT / "sources/rtlscout"),
                str(ROOT / "sources/mini-swe-agent/src")]
from bridge import FullMemory, State
from hooks import HostObserver, attach_mini, attach_rtlscout
from native_evidence import ScoutEvidence


def test_mini_original_agent_and_environment_are_used(tmp_path):
    from minisweagent.agents.default import DefaultAgent
    from minisweagent.environments.local import LocalEnvironment
    class ScriptedModel:
        def query(self, messages):
            return {"role": "assistant", "content": "offline integration action",
                    "extra": {"actions": [{"command": "python3 -c 'assert True'"}]}}
        def get_template_vars(self): return {}
        def format_observation_messages(self, message, outputs, template_vars):
            return [{"role": "user", "content": str(outputs[0]["returncode"])}]
        def format_message(self, **kw): return kw
    memory = FullMemory("mini-swe-agent", root=tmp_path)
    state = State("offline-mini", "offline-mini", "software", "python", "target_test",
                  {"status": "UNKNOWN"}, {}, {})
    try:
        agent = DefaultAgent(ScriptedModel(), LocalEnvironment(cwd=str(tmp_path)),
                             system_template="Native host", instance_template="Public task",
                             cost_limit=0, step_limit=2)
        attach_mini(agent, HostObserver(memory, lambda: state, lambda *a: None))
        assert agent.step()[-1]["content"] == "0" and agent.n_calls == 1
        events = [json.loads(l) for l in memory.journal.path.read_text().splitlines()]
        assert any(e["event"] == "memory_consult" for e in events)
        # A successful bash command without a task verifier is NOT written as a repair.
        assert not any(e["event"] == "memory_write" for e in events)
    finally:
        memory.close()


def test_scout_native_fake_provider_real_tools_and_online_write(tmp_path, monkeypatch):
    from rtlscout.agent import RTLAgent
    from rtlscout.fake_provider import build_fake_client
    import tempfile
    memory = FullMemory("rtlscout", root=tmp_path)
    try:
        work = memory.arm_root / "episodes/offline-scout"
        agent = RTLAgent(build_fake_client("simple_adder_pass"), workdir=work,
                         max_steps=3, language="verilog", run_cec=False)
        # Mirror run_scout's real entry point. Native cost invokes Yosys with
        # inherited cwd and tempfile paths; neither may escape this workspace.
        monkeypatch.chdir(agent.workspace)
        monkeypatch.setattr(tempfile, 'tempdir', str(agent.workspace))
        monkeypatch.setenv('TMPDIR', str(agent.workspace))
        agent.design_top_module = "adder"
        shutil.copy2(ROOT / "sources/rtlscout/rtlscout/smoke_benchmarks/simple_adder/tb.sv",
                     agent.workspace / "tb.sv")
        evidence = ScoutEvidence(memory, agent, "offline-simple-adder")
        attach_rtlscout(agent, HostObserver(memory, evidence.state, evidence.verify))
        result = agent.run("Implement an 8-bit modulo-256 adder.", "offline-simple-adder")
        assert result.passed and len(result.all_evals) == 1
        events = [json.loads(l) for l in memory.journal.path.read_text().splitlines()]
        writes = [e for e in events if e["event"] == "memory_write"]
        assert len(writes) == 1 and writes[0]["outcome"] == "NEUTRAL"
        assert writes[0]["rebuild_ok"]
        # It was clean at the first measurement; do not claim a repaired prior failure.
    finally:
        memory.close()
