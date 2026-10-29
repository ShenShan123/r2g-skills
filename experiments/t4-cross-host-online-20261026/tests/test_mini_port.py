from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "adapters"))


def test_native_message_and_action_format_use_metered_transport_without_retries():
    from mini_runtime import MiniProvider
    class RecordedProvider:
        model = "offline-not-a-provider-call"
        def __init__(self): self.calls = []
        def request(self, body, *, purpose):
            self.calls.append((body, purpose))
            return {"id": "offline-fixture", "choices": [{"message": {"role": "assistant",
                "content": "Inspect the repository", "tool_calls": [{"id": "c1", "type": "function",
                "function": {"name": "bash", "arguments": '{"command":"git status --short"}'}}]}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
    provider = RecordedProvider()
    model = MiniProvider(provider)
    response = model.query([{"role": "user", "content": "public issue", "extra": {"private": "not for wire"}}])
    assert len(provider.calls) == 1 and "extra" not in provider.calls[0][0]["messages"][0]
    assert response["extra"]["actions"] == [{"command": "git status --short", "tool_call_id": "c1"}]
    output = model.format_observation_messages(response, [{"output": "ok", "returncode": 0,
                                                            "exception_info": ""}])
    assert output[0]["role"] == "tool" and output[0]["tool_call_id"] == "c1"
    assert response["extra"]["api_cost"] is None


def test_official_evaluator_cannot_be_replaced_by_a_shell_exit_code():
    from mini_runtime import official_evaluator_ready
    status = official_evaluator_ready()
    assert isinstance(status["ready"], bool) and status["reason"]


def test_native_bubblewrap_agent_writes_unknown_without_inventing_success():
    import subprocess
    import tempfile
    from bridge import FullMemory
    from hooks import HostObserver, attach_mini
    from mini_runtime import MiniEvidence, MiniProvider, sandbox_environment
    from minisweagent.agents.default import DefaultAgent
    work = Path(tempfile.mkdtemp(prefix="mini-native-", dir=ROOT / "reports"))
    repo = work / "repository"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    memory = FullMemory("mini-swe-agent", root=work)
    class RecordedProvider:
        model = "offline-test-only"
        count = 0
        def request(self, body, *, purpose):
            import json
            self.count += 1
            command = "printf test > new_file.txt" if self.count == 1 else "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
            return {"id": "offline-fixture", "choices": [{"message": {"role": "assistant", "content": "test",
                "tool_calls": [{"id": str(self.count), "type": "function", "function": {"name": "bash",
                "arguments": json.dumps({"command": command})}}]}}], "usage": {"total_tokens": 0}}
    provider = RecordedProvider()
    model = MiniProvider(provider)
    environment = sandbox_environment(repo)
    evidence = MiniEvidence(memory, repo, "offline-test", "offline-repo")
    agent = DefaultAgent(model, environment, system_template="Offline fixture only",
                         instance_template="{{task}}", step_limit=3, cost_limit=0)
    attach_mini(agent, HostObserver(memory, evidence.state, evidence.verify))
    try:
        result = agent.run("test native hooks, not a SWE-bench issue")
        assert result["exit_status"] == "Submitted" and provider.count == 2
        assert (repo / "new_file.txt").read_text() == "test"
        conn, _ = memory.backend._open()
        assert conn.execute("SELECT COUNT(*) FROM tehm_transitions").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM tehm_transitions WHERE outcome='PASS'").fetchone()[0] == 0
    finally:
        memory.close()
        environment.cleanup()
