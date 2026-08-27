"""Portable canonical-freeze pointer contract."""
from __future__ import annotations

from pathlib import Path

from evaluation.freeze_pointer import load_pointer, resolve_bundle


def test_freeze_pointer_is_relative_and_resolvable_from_repo_layout(monkeypatch):
    monkeypatch.delenv("TEHM_CANONICAL_BUNDLE", raising=False)
    pointer = load_pointer()
    assert not Path(pointer["canonical_bundle"]).is_absolute()
    expected = (Path(__file__).resolve().parents[2] / "evidence" /
                "tehm-evidence-freeze-v4-dev").resolve()
    assert resolve_bundle(require_exists=False) == expected


def test_freeze_pointer_allows_explicit_external_bundle(monkeypatch, tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    monkeypatch.setenv("TEHM_CANONICAL_BUNDLE", str(bundle))
    assert resolve_bundle() == bundle.resolve()
