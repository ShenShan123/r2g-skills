"""The graph venv must not inherit the parent interpreter's PYTHONHOME.

Regression (wave-3 E9, 2026-09-22): on hosts where `python3` is the oss-cad-suite
wrapper, PYTHONHOME is exported to every child. `expand_candidates.graph_convert`
passed it on to R2G_GRAPH_PYTHON, and the venv died with "No module named
'encodings'": 69/69 synthesis-qualified designs became graph_failed, and the
failure was misattributed to a missing torch.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "execute"))

import expand_candidates as xc  # noqa: E402


def test_graph_child_does_not_inherit_pythonhome(tmp_path: Path, monkeypatch) -> None:
    seen = tmp_path / "seen_env.txt"
    probe = tmp_path / "graph_python"
    probe.write_text(
        "#!/bin/sh\n"
        f'printf "PYTHONHOME=%s\\n" "${{PYTHONHOME-<unset>}}" >> "{seen}"\n'
        "exit 1\n", encoding="utf-8")
    probe.chmod(0o755)
    monkeypatch.setenv("PYTHONHOME", "/opt/OpenROAD/oss-cad-suite")
    monkeypatch.setattr(xc, "graph_python", lambda: str(probe))
    monkeypatch.setattr(xc, "_resolve_lib_env", lambda _cfg: {})

    state, _log = xc.graph_convert(tmp_path / "n.v", tmp_path / "g.pt", "d",
                                   tmp_path / "config.mk", tmp_path / "stats.json")

    assert state == "failed"  # the probe exits 1; what matters is its environment
    assert seen.read_text(encoding="utf-8").splitlines() == ["PYTHONHOME=<unset>"]


def test_other_commands_keep_the_environment(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PYTHONHOME", "/opt/example")
    out = xc.run(["sh", "-c", 'printf %s "$PYTHONHOME"'], capture=True)
    assert out.stdout == "/opt/example"
