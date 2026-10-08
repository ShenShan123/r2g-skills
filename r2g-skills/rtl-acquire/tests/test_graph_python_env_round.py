"""run_expansion_round must launch the graph venv without the caller's PYTHONHOME.

Regression (independent review, 2026-09-23): the dataset_scale_report phase ran
$R2G_GRAPH_PYTHON through run() with the full inherited environment. 9ba9bc4 fixed
the same leak in expand_candidates only.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import run_expansion_round as rer  # noqa: E402


def test_run_drops_the_named_variables(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PYTHONHOME", "/opt/OpenROAD/oss-cad-suite")
    out = tmp_path / "seen.txt"
    rer.run(["sh", "-c", f'printf %s "${{PYTHONHOME-<unset>}}" > "{out}"'],
            status_path=tmp_path / "s.json", log_path=tmp_path / "l.log",
            payload={"phase": "t"}, drop_env=rer.GRAPH_PYTHON_DROP_ENV)
    assert out.read_text() == "<unset>"


def test_the_scale_report_launch_passes_the_drop_list() -> None:
    tree = ast.parse((SCRIPTS / "run_expansion_round.py").read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "run"
             and n.args and isinstance(n.args[0], ast.List)
             and any(getattr(e, "id", "") == "gpython" for e in n.args[0].elts)]
    assert calls, "the graph-venv launch moved; update this test"
    for call in calls:
        kw = {k.arg: k.value for k in call.keywords}
        assert getattr(kw.get("drop_env"), "id", "") == "GRAPH_PYTHON_DROP_ENV"
