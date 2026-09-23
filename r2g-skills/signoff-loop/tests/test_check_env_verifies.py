"""check_env.sh must verify what it reports, not echo a non-empty string.

Regression (CORRECTIONS #6, E6 provisioning log F1/F3/F4, 2026-09-22): on a fresh
host check_env.sh exited 0 and printed `ok` for
  * PDK_ROOT pointing at a mode-0750 tree this user cannot read (F1);
  * an ORFS checkout whose flow/scripts/defaults.py was not executable, so
    `make synth` died with Error 126 (F3);
  * flow/settings.mk assigning a dead YOSYS_EXE, which overrides the exported
    one inside make (F3);
  * a python3 without PyYAML, so ORFS's defaults.py died and every stage failed
    as if it were the design's fault (F4).
Each case is rebuilt here against a hermetic copy of scripts/flow/.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]


def _exe(path: Path, text: str) -> Path:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _env(tmp_path: Path, *, broken: bool) -> dict[str, str]:
    skill = tmp_path / "skill"
    (skill / "scripts").mkdir(parents=True)
    shutil.copytree(SKILL / "scripts" / "flow", skill / "scripts" / "flow")
    flow = tmp_path / "orfs" / "flow"
    (flow / "scripts").mkdir(parents=True)
    (flow / "platforms").mkdir()
    (flow / "Makefile").write_text("# fake\n")
    defaults = _exe(flow / "scripts" / "defaults.py", "#!/usr/bin/env python3\n")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    tools = {name: _exe(bindir / name, "#!/bin/sh\nexit 0\n")
             for name in ("openroad", "yosys", "iverilog", "vvp")}
    # python3 shim: `import yaml` works unless broken.
    _exe(bindir / "python3",
         '#!/bin/sh\ncase "$*" in *yaml*) exit ' + ("1" if broken else "0") + ";; esac\n"
         'exec /usr/bin/python3 "$@"\n')
    pdk = tmp_path / "pdk"
    (pdk / "sky130A").mkdir(parents=True)
    if broken:
        pdk.chmod(0o000)
        defaults.chmod(0o644)
        (flow / "settings.mk").write_text(f"export YOSYS_EXE = {tmp_path}/gone/yosys\n")
    env = {"PATH": f"{bindir}:/usr/bin:/bin", "HOME": str(tmp_path),
           "ORFS_ROOT": str(tmp_path / "orfs"), "PDK_ROOT": str(pdk),
           "OPENROAD_EXE": str(tools["openroad"]), "YOSYS_EXE": str(tools["yosys"]),
           "IVERILOG_EXE": str(tools["iverilog"]), "VVP_EXE": str(tools["vvp"])}
    env["_CHECK_ENV"] = str(skill / "scripts" / "flow" / "check_env.sh")
    return env


def _check_env(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", env["_CHECK_ENV"]], env=env,
                          capture_output=True, text=True, timeout=120)


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-000 directory")
def test_e6_false_oks_are_reported_bad(tmp_path: Path) -> None:
    env = _env(tmp_path, broken=True)
    try:
        r = _check_env(env)
    finally:
        (tmp_path / "pdk").chmod(0o755)
    assert r.returncode == 1, r.stdout
    bad = [ln for ln in r.stdout.splitlines() if ln.startswith("BAD ")]
    assert any("PDK_ROOT" in ln and "not a readable directory" in ln for ln in bad), bad
    assert any("defaults.py" in ln and "not executable" in ln for ln in bad), bad
    assert any("YOSYS_EXE" in ln and "settings.mk" in ln for ln in bad), bad
    assert any(ln.split()[1] == "python3" and "yaml" in ln for ln in bad), bad


def test_a_usable_environment_is_ok(tmp_path: Path) -> None:
    r = _check_env(_env(tmp_path, broken=False))
    assert r.returncode == 0, r.stdout
    assert not [ln for ln in r.stdout.splitlines() if ln.startswith(("BAD ", "MISS "))]
