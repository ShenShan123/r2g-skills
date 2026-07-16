"""P0-9 (2026-07-15): a bad R2G_GRAPH_PYTHON is a STRUCTURED toolchain skip, not an
unstructured FileNotFoundError that upstream mislabels as an RTL/design synth failure."""
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(_SCRIPTS))
sys.path.insert(0, str(_SCRIPTS / "execute"))

import expand_candidates as ec  # noqa: E402


def test_bogus_graph_python_is_structured_skip(monkeypatch, tmp_path):
    """A SET-but-missing/non-executable R2G_GRAPH_PYTHON returns ('skipped', <toolchain
    reason>) — the exec never raises out of graph_convert, so the design is recorded as
    graph_skipped (a toolchain gap), never routed into RTL/design repair learning."""
    monkeypatch.setenv("R2G_GRAPH_PYTHON", "/tmp/does_not_exist_python_xyz")
    state, log = ec.graph_convert(tmp_path / "n.v", tmp_path / "o.pt", "d",
                                  tmp_path / "config.mk", tmp_path / "stats.json")
    assert state == "skipped"
    assert "toolchain_graph_python_missing" in log


def test_unset_graph_python_still_skips(monkeypatch, tmp_path):
    """The pre-existing unset path stays a clean structured skip (not a regression)."""
    monkeypatch.delenv("R2G_GRAPH_PYTHON", raising=False)
    monkeypatch.setattr(ec, "graph_python", lambda: "")
    state, log = ec.graph_convert(tmp_path / "n.v", tmp_path / "o.pt", "d",
                                  tmp_path / "config.mk", tmp_path / "stats.json")
    assert state == "skipped"
    assert "not set" in log
