"""r2g_bounded_run must never SIGKILL its own caller's session.

Regression (wave-3, 2026-09-23): the checker's pgid was read back with `ps` right
after `setsid … &`. When the log-file open delayed the child's setsid(), `ps`
returned the CALLER's group; campaign drivers are session leaders, so that value
was the driver's session id, and the unconditional post-run cleanup
(`pkill -KILL -s`, `kill -KILL -- -pgid`) killed the driver and every flow in it
about 1 s after a DRC/LVS checker ended. The race is timing-dependent, so this
test forces the misread deterministically with a `ps` shim.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

BOUNDED = Path(__file__).resolve().parents[1] / "scripts" / "flow" / "_bounded_run.sh"


def _run_in_own_session(tmp_path: Path, script: str) -> subprocess.CompletedProcess[str]:
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    # Answers the pgid read-back with the CALLER's group, as the lost race did.
    (shim_dir / "ps").write_text(
        "#!/bin/bash\n"
        'if [[ "$*" == "-o pgid= -p "* ]]; then\n'
        '  exec /bin/ps -o pgid= -p "$PPID"\n'
        "fi\n"
        'exec /bin/ps "$@"\n', encoding="utf-8")
    (shim_dir / "ps").chmod(0o755)
    env = dict(os.environ, PATH=f"{shim_dir}:{os.environ['PATH']}")
    return subprocess.run(["setsid", "-w", "bash", "-c", script],
                          env=env, capture_output=True, text=True, timeout=60)


def test_misread_pgid_does_not_kill_the_caller(tmp_path: Path) -> None:
    log = tmp_path / "checker.log"
    result = _run_in_own_session(tmp_path, (
        f'source "{BOUNDED}"\n'
        f'r2g_bounded_run 20 1 "{log}" true\n'
        'echo "caller survived rc=$?"\n'))
    assert "caller survived rc=0" in result.stdout, (result.returncode, result.stderr)


def test_timeout_still_kills_the_checker_session(tmp_path: Path) -> None:
    marker = "sleep 61.73"
    result = _run_in_own_session(tmp_path, (
        f'source "{BOUNDED}"\n'
        f'r2g_bounded_run 2 1 "{tmp_path / "t.log"}" bash -c "{marker} & {marker}"\n'
        'rc=$?; sleep 1\n'
        f'echo "rc=$rc survivors=$(pgrep -f "^{marker}" | wc -l)"\n'))
    assert "rc=124 survivors=0" in result.stdout, (result.stdout, result.stderr)
