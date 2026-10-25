import pytest


@pytest.fixture(autouse=True)
def r2g_home(tmp_path, monkeypatch):
    monkeypatch.setenv("R2G_HOME", str(tmp_path / "home"))
    from r2g_agent import knowledge
    monkeypatch.setattr(knowledge, "_BACKEND_NAME", None)
    for key in ("R2G_MEMORY_BACKEND", "TEHM_DB", "TEHM_ARTIFACTS_ROOT"):
        monkeypatch.delenv(key, raising=False)
    for k in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    return tmp_path / "home"


class FakeAdapter:
    """Scripted model: a list of turns, each a list of events to yield."""
    def __init__(self, turns):
        self.turns = list(turns)
        self.seen = []

    def stream(self, messages, tools, system=""):
        self.seen.append([dict(m) for m in messages])
        for ev in (self.turns.pop(0) if self.turns else [{"type": "text", "delta": "done"}]):
            yield ev
        yield {"type": "done", "stop_reason": "end"}


@pytest.fixture
def fake_adapter():
    return FakeAdapter
