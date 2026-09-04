"""Release-freeze scope guards for local governing documents."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _freeze_module():
    path = Path(__file__).parents[1] / "scripts" / "freeze_evidence_v3.py"
    spec = importlib.util.spec_from_file_location("tehm_freeze_evidence_v3", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_freeze_rejects_tracked_governing_docs(monkeypatch):
    module = _freeze_module()
    original = module._git

    def fake_git(*args, **kwargs):
        if args[:3] == ("ls-files", "-z", "--"):
            return b"memory/docs/local.md\0"
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "_git", fake_git)
    with pytest.raises(RuntimeError, match="release scope violation"):
        module.source_state()


def test_freeze_source_state_has_no_tracked_docs_now():
    module = _freeze_module()
    state = module.source_state()
    assert not any(
        item["path"].startswith("memory/docs/")
        for item in state["untracked_files"]
    )
