"""A configured graph interpreter that cannot start must stop the round, loudly.

Regression (independent review, 2026-09-23): a pinned R2G_GRAPH_PYTHON that could
not start was either recorded per design as graph_skipped (missing path) or
graph_failed (broken venv, leaked PYTHONHOME: wave-3 E9 lost 69/69 designs this
way), and the dataset_scale_report phase died with a bare CalledProcessError. The
owner's rule is fail loud: refuse the run up front and name the interpreter. An
unset R2G_GRAPH_PYTHON still means "no graphs wanted" and SKIPs per design.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "execute"))

import expand_candidates as ec  # noqa: E402
from skill_env import graph_python_start_error  # noqa: E402

DROP = ("PYTHONHOME", "PYTHONEXECUTABLE", "PYTHONNOUSERSITE")


def _stub(tmp_path: Path, body: str) -> Path:
    stub = tmp_path / "graph_python"
    stub.write_text("#!/bin/sh\n" + body)
    stub.chmod(0o755)
    return stub


def test_a_python_that_cannot_start_is_named_with_its_own_error(tmp_path: Path) -> None:
    stub = _stub(tmp_path, "echo 'Fatal Python error: init_fs_encoding' >&2\nexit 1\n")
    err = graph_python_start_error(str(stub), DROP)
    assert "cannot start" in err and str(stub) in err and "init_fs_encoding" in err


def test_a_missing_python_cannot_start(tmp_path: Path) -> None:
    assert "cannot start" in graph_python_start_error(str(tmp_path / "gone"), DROP)


def test_a_working_python_passes(tmp_path: Path) -> None:
    assert graph_python_start_error(str(_stub(tmp_path, "exit 0\n")), DROP) == ""


def test_the_probe_runs_it_the_way_the_launch_does(tmp_path: Path, monkeypatch) -> None:
    """The launch drops the caller's PYTHONHOME; the probe must too, or a healthy
    venv under the oss-cad wrapper would be refused."""
    monkeypatch.setenv("PYTHONHOME", "/opt/OpenROAD/oss-cad-suite")
    stub = _stub(tmp_path, '[ -z "${PYTHONHOME+x}" ]\n')
    assert graph_python_start_error(str(stub), DROP) == ""


def test_expand_candidates_refuses_the_round_before_touching_any_design(
        tmp_path: Path, monkeypatch, capsys) -> None:
    stub = _stub(tmp_path, "exit 1\n")
    monkeypatch.setenv("R2G_GRAPH_PYTHON", str(stub))
    csv_path = tmp_path / "candidates.csv"
    csv_path.write_text("design,priority,source_path\nd,high,/nowhere/d.v\n")
    out_root = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", [
        "expand_candidates.py", "--candidate-csv", str(csv_path),
        "--out-root", str(out_root), "--projects-root", str(tmp_path / "projects")])
    assert ec.main() == 2
    assert "cannot start" in capsys.readouterr().err
    assert not out_root.exists()


def test_the_round_checks_the_interpreter_before_the_scale_report() -> None:
    """dataset_scale_report must refuse a broken interpreter with a named error,
    not launch it and die on a bare CalledProcessError."""
    tree = ast.parse((SCRIPTS / "run_expansion_round.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and getattr(node.test, "id", "") == "gpython":
            body = ast.unparse(ast.Module(body=node.body, type_ignores=[]))
            break
    else:
        raise AssertionError("the `if gpython:` scale-report block moved; update this test")
    probe = body.find("graph_python_start_error(gpython")
    launch = body.find("run([gpython")
    assert 0 <= probe < launch, body
    assert "raise RuntimeError(start_error)" in body, body
