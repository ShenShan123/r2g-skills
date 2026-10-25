"""The legacy seam must see the same user store as the new agent's scripts."""
from pathlib import Path

from factory import open_memory_backend
from legacy_backend import LegacyMemoryBackend


def test_factory_passes_explicit_legacy_db(tmp_path):
    db = tmp_path / 'user.sqlite'
    backend = open_memory_backend('legacy', db_path=db)
    assert backend.db_path() == db
    backend.close()


def test_legacy_adapter_honors_agent_environment(tmp_path, monkeypatch):
    db, heur = tmp_path / 'user.sqlite', tmp_path / 'user.json'
    monkeypatch.setenv('R2G_KNOWLEDGE_DB', str(db))
    monkeypatch.setenv('R2G_HEURISTICS_PATH', str(heur))
    backend = LegacyMemoryBackend()
    assert backend.db_path() == db
    assert backend.heuristics_path() == heur
    explicit = LegacyMemoryBackend(knowledge_dir=tmp_path)
    assert explicit.db_path() == tmp_path / 'knowledge.sqlite'
    assert explicit.heuristics_path() == tmp_path / 'heuristics.json'
