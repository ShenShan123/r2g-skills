"""tools/run_signoff.sh must grade a design against ITS platform's decks.

Regression (CORRECTIONS #12, 2026-09-22): run_signoff.sh called run_drc/lvs/rcx.sh
with no platform, and all three default to sky130hd. A nangate45 GDS was graded
against the sky130hd DRC deck and recorded `drc.json: status=clean, 0 violations`.
The checkers are stubbed here; what matters is the arguments they receive.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _fake_repo(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    (repo / "tools").mkdir(parents=True)
    shutil.copy(REPO / "tools" / "run_signoff.sh", repo / "tools" / "run_signoff.sh")
    flow = repo / "r2g-skills" / "signoff-loop" / "scripts" / "flow"
    flow.mkdir(parents=True)
    calls = tmp_path / "calls.txt"
    for name in ("run_drc.sh", "run_lvs.sh", "run_rcx.sh"):
        (flow / name).write_text(f'#!/bin/sh\necho "{name} $*" >> "{calls}"\n')
    return repo, calls


def _project(tmp_path: Path, config: str) -> Path:
    proj = tmp_path / "base" / "t_42"
    (proj / "constraints").mkdir(parents=True)
    (proj / "constraints" / "config.mk").write_text(config)
    return proj


def _run(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(repo / "tools" / "run_signoff.sh"), *args],
                          capture_output=True, text=True, timeout=120)


def test_platform_comes_from_config_mk(tmp_path: Path) -> None:
    repo, calls = _fake_repo(tmp_path)
    proj = _project(tmp_path, "export DESIGN_NAME = top\nexport PLATFORM    = nangate45\n")

    out = _run(repo, str(proj))

    assert out.returncode == 0, out.stderr
    assert calls.read_text().splitlines() == [
        f"run_drc.sh {proj} nangate45 t_42",
        f"run_lvs.sh {proj} nangate45 t_42",
        f"run_rcx.sh {proj} nangate45 t_42",
    ]
    # The verdict line records which platform it was graded against.
    assert json.loads(out.stdout.strip().splitlines()[-1])["platform"] == "nangate45"


def test_explicit_platform_wins(tmp_path: Path) -> None:
    repo, calls = _fake_repo(tmp_path)
    proj = _project(tmp_path, "export PLATFORM = sky130hd\n")

    out = _run(repo, str(proj), "sky130hs")

    assert out.returncode == 0, out.stderr
    assert all(" sky130hs " in line for line in calls.read_text().splitlines())


def test_refuses_without_a_platform(tmp_path: Path) -> None:
    repo, calls = _fake_repo(tmp_path)
    proj = _project(tmp_path, "export DESIGN_NAME = top\n")

    out = _run(repo, str(proj))

    assert out.returncode == 2
    assert "no platform" in out.stderr
    assert not calls.exists()        # nothing was graded against a guessed deck
