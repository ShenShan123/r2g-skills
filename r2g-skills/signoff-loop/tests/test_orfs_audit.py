"""Process accounting must distinguish preflight failures and actual make runs."""
import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import sys

FLOW = Path(__file__).resolve().parents[1] / "scripts" / "flow"
SHIM = FLOW / "orfs_audit_bin" / "make"


def environment(tmp_path, executable="/usr/bin/make"):
    return {**os.environ, "R2G_ORFS_AUDIT_FILE": str(tmp_path / "audit.jsonl"),
            "R2G_ORFS_REAL_MAKE": executable, "R2G_ORFS_AUDIT_PYTHON": sys.executable,
            "R2G_ORFS_AUDIT_PROJECT": str(tmp_path), "R2G_ORFS_AUDIT_JOB": "job-1",
            "R2G_ORFS_AUDIT_EXECUTION": "execution-1"}


def rows(tmp_path):
    return [json.loads(x) for x in (tmp_path / "audit.jsonl").read_text().splitlines()]


def test_success_failure_and_unknown_arguments_redacted(tmp_path):
    (tmp_path / "Makefile").write_text("synth:\n\t@true\nroute:\n\t@false\n")
    env = environment(tmp_path)
    assert subprocess.run([SHIM, "synth", "API_KEY=secret-value", "UNKNOWN=secret"],
                          env=env, cwd=tmp_path).returncode == 0
    assert subprocess.run([SHIM, "route"], env=env, cwd=tmp_path).returncode == 2
    result = rows(tmp_path)
    assert [r["event"] for r in result] == ["make_start", "make_end"] * 2
    assert [r["exit_code"] for r in result] == [None, 0, None, 2]
    assert result[0]["pid"] == result[1]["pid"] != result[0]["observer_pid"]
    assert result[0]["invocation_id"] == result[1]["invocation_id"]
    assert "secret" not in (tmp_path / "audit.jsonl").read_text()
    assert result[0]["project"] == str(tmp_path)
    assert result[0]["execution"] == "execution-1"


def test_missing_executable_is_not_make_start(tmp_path):
    assert subprocess.run([SHIM, "synth"], env=environment(tmp_path, "/no/such/make")).returncode == 127
    assert [r["event"] for r in rows(tmp_path)] == ["make_spawn_failed"]


def test_parallel_append_is_complete_json(tmp_path):
    env = environment(tmp_path, "/bin/true")
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(pool.map(lambda _: subprocess.run([SHIM, "synth"], env=env).returncode, range(24)))
    result = rows(tmp_path)
    assert codes == [0] * 24
    assert len(result) == 48
    assert len({r["invocation_id"] for r in result}) == 24


def test_timeout_keeps_process_group_reaping_and_records_exit(tmp_path):
    (tmp_path / "Makefile").write_text("synth:\n\t@sleep 30\n")
    result = subprocess.run(["timeout", "--kill-after=2", "0.5", str(SHIM), "synth"],
                            env=environment(tmp_path), cwd=tmp_path, timeout=5)
    assert result.returncode == 124
    events = rows(tmp_path)
    assert [r["event"] for r in events] == ["make_start", "make_end"]
    assert events[-1]["exit_code"] != 0


def test_no_audit_preserves_make_exit(tmp_path):
    env = environment(tmp_path, "/bin/false")
    env.pop("R2G_ORFS_AUDIT_FILE")
    assert subprocess.run([SHIM], env=env).returncode == 1
    assert not (tmp_path / "audit.jsonl").exists()


def test_observer_isolated_but_make_inherits_original_python_environment(tmp_path):
    fake = tmp_path / "make-sentinel"
    fake.write_text('#!/bin/bash\nprintf "%s\\n" "$PYTHONHOME" "$PYTHONPATH" "$PYTHONSAFEPATH" > "$SENTINEL_OUTPUT"\n')
    fake.chmod(0o755)
    env = environment(tmp_path, str(fake))
    values = ["/deliberately/invalid/pythonhome", "/sentinel/path with spaces", "sentinel-safe-path"]
    env.update(dict(zip(("PYTHONHOME", "PYTHONPATH", "PYTHONSAFEPATH"), values)))
    sentinel = tmp_path / "received.env"
    env["SENTINEL_OUTPUT"] = str(sentinel)
    assert subprocess.run([SHIM, "synth"], env=env).returncode == 0
    assert sentinel.read_text().splitlines() == values
    assert [r["event"] for r in rows(tmp_path)] == ["make_start", "make_end"]


def test_wrapper_preflight_failure_does_not_imply_make(tmp_path):
    env = environment(tmp_path)
    env["ORFS_MAX_CPUS"] = "not-a-number"
    result = subprocess.run(["bash", str(FLOW / "run_orfs.sh"), str(tmp_path), "nangate45"], env=env)
    assert result.returncode == 2
    events = rows(tmp_path)
    assert [r["event"] for r in events] == ["wrapper_start", "wrapper_end"]
    assert events[0]["wrapper_id"] == events[1]["wrapper_id"]
    assert events[0]["exit_code"] is None
    assert events[1]["exit_code"] == 2
