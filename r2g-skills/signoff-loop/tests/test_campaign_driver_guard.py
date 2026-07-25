"""Regression tests for the campaign_resume_waves.sh single-instance guard.

failure-patterns #37 (2026-07-11): the driver's internal single-instance guard used
an UN-anchored `pgrep -f "campaign_resume_waves\\.sh"`, which false-matched the
operator's *launching shell* — whose command line literally contains the driver path
(the /r2g-debug Step 2 launch block does `setsid bash tools/campaign_resume_waves.sh`).
When that launching shell outlived the guard check (a `sleep`-and-confirm launch), the
driver mistook its own launcher for a rival driver and refused to start. This is the
exact "un-anchored -f false-matches your own shell" trap the operator-side guard + the
/r2g-debug Step 0 note already end-anchor around; the driver's own guard did not.

These tests drive the guard in isolation via the `R2G_GUARD_SELFTEST=1` hook (which
runs ONLY the guard, then prints `guard-passed` and exits before any wave work).
"""
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
DRIVER = REPO / "tools" / "campaign_resume_waves.sh"


def _selftest_env(ledger: Path) -> dict:
    env = dict(os.environ)
    env.update(R2G_GUARD_SELFTEST="1", PLATFORM="sky130hs", LEDGER=str(ledger))
    return env


def _real_driver_running() -> bool:
    """True if a genuine driver (cmdline ENDS in the script name) is already up — e.g.
    a live campaign wave on this host. pgrep's own cmdline ends in `.sh$`, not the bare
    script name, so it never self-matches."""
    return bool(
        subprocess.run(
            ["pgrep", "-f", r"campaign_resume_waves\.sh$"],
            capture_output=True, text=True,
        ).stdout.strip()
    )


def test_guard_ignores_self_mentioning_launcher_shell(tmp_path):
    """A launcher shell whose -c command string contains the driver path must NOT be
    mistaken for a second driver (the live-reproduced #37 false-positive)."""
    if _real_driver_running():
        pytest.skip("a genuine campaign_resume_waves.sh driver is running on this host; "
                    "the launcher-false-positive assertion needs a driver-free baseline")
    ledger = tmp_path / "guard_campaign.jsonl"
    ledger.write_text('{"design":"x","state":"pending"}\n')
    out_f, err_f = tmp_path / "out.txt", tmp_path / "err.txt"
    # The launcher's OWN cmdline embeds the driver path AND it stays alive (`wait`)
    # while the child driver runs its guard — exactly the operator launch pattern.
    launch = (
        f'setsid bash {DRIVER} >"{out_f}" 2>"{err_f}" & '
        f'child=$!; wait "$child"; echo "child_rc=$?"'
    )
    p = subprocess.run(
        ["bash", "-c", launch], cwd=REPO, env=_selftest_env(ledger),
        capture_output=True, text=True, timeout=60,
    )
    driver_out = out_f.read_text() if out_f.exists() else ""
    driver_err = err_f.read_text() if err_f.exists() else ""
    ctx = f"launcher_stdout={p.stdout!r} driver_out={driver_out!r} driver_err={driver_err!r}"
    assert "child_rc=0" in p.stdout, ctx
    assert "guard-passed" in driver_out, ctx
    assert "already running" not in driver_err, ctx


def test_guard_still_detects_a_real_second_driver(tmp_path):
    """End-anchoring must NOT blind the guard: a genuine process whose cmdline ENDS in
    campaign_resume_waves.sh (a real rival driver invocation) must still be caught."""
    decoy = tmp_path / "campaign_resume_waves.sh"
    decoy.write_text("#!/bin/bash\nsleep 30\n")
    decoy.chmod(0o755)
    # Run the decoy so its cmdline is `bash <tmp>/campaign_resume_waves.sh` (ends in .sh).
    proc = subprocess.Popen(
        ["setsid", "bash", str(decoy)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        # Wait until the decoy is visible to an end-anchored pgrep (kills startup race).
        deadline = time.time() + 8
        while time.time() < deadline:
            if subprocess.run(
                ["pgrep", "-f", r"campaign_resume_waves\.sh$"],
                capture_output=True, text=True,
            ).stdout.strip():
                break
            time.sleep(0.1)
        ledger = tmp_path / "real_rival_campaign.jsonl"
        ledger.write_text('{"design":"x","state":"pending"}\n')
        p = subprocess.run(
            ["bash", str(DRIVER)], cwd=REPO, env=_selftest_env(ledger),
            capture_output=True, text=True, timeout=60,
        )
        ctx = f"stdout={p.stdout!r} stderr={p.stderr!r}"
        assert p.returncode != 0, ctx
        assert "already running" in p.stderr, ctx
        assert "guard-passed" not in p.stdout, ctx
    finally:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        proc.wait(timeout=10)


# --------------------------------------------------------------------------- #
# pool.env sizing: it may override, but never SILENTLY                        #
# --------------------------------------------------------------------------- #
# failure-patterns #57 (2026-07-25): pool.env is re-sourced at the top of every wave,
# so it wins over the operator's launch-time env — that IS the documented no-restart
# retune. But a pool.env retuned on 2026-07-10 ("host idle") carried WORKERS=8
# NUM_CORES=8, and relaunching on a host with ~9 free cores silently requested 64,
# discarding an explicit `WORKERS=2 NUM_CORES=4` with no message. On a SHARED host that
# thrashes another user's jobs. The override stays; the silence does not.
# A platform name no real campaign uses, so these tests get their OWN
# tools/_<plat>_resume_logs/ and can never clobber a live pool.env or append an
# OVERSUBSCRIBED line to an operator's waves.log (which the first cut of these tests
# did — a test that edits the thing it is testing on is not isolated).
_SIZING_PLATFORM = "r2gselftest"
_SIZING_LOGDIR = REPO / "tools" / f"_{_SIZING_PLATFORM}_resume_logs"


def _sizing_env(tmp_path, **overrides) -> dict:
    env = dict(os.environ)
    # SKIP_INSTANCE_GUARD: the sizing seam does no wave work, so it must be runnable
    # while a REAL campaign driver is up — otherwise these tests pass only on an idle
    # host and silently skip exactly when a campaign is live.
    env.update(R2G_SIZING_SELFTEST="1", PLATFORM=_SIZING_PLATFORM,
               SKIP_INSTANCE_GUARD="1", LEDGER=str(tmp_path / "led.jsonl"))
    env.update(overrides)
    return env


def _run_sizing(tmp_path, logdir_pool: str | None, **overrides):
    """Run the sizing seam with a pool.env planted in the platform's log dir."""
    pool = _SIZING_LOGDIR / "pool.env"
    try:
        if logdir_pool is not None:
            pool.parent.mkdir(parents=True, exist_ok=True)
            pool.write_text(logdir_pool)
        return subprocess.run(["bash", str(DRIVER)], cwd=REPO,
                              env=_sizing_env(tmp_path, **overrides),
                              capture_output=True, text=True, timeout=60)
    finally:
        shutil.rmtree(_SIZING_LOGDIR, ignore_errors=True)


def test_pool_env_override_of_an_explicit_launch_value_is_announced(tmp_path):
    r = _run_sizing(tmp_path, "WORKERS=8\nNUM_CORES=8\nWAVE_MAX=32\n",
                    WORKERS="2", NUM_CORES="4", WAVE_MAX="8")
    assert "sizing workers=8 num_cores=8" in r.stdout, r.stdout
    assert "pool.env overrode your launch WORKERS=2 with 8" in r.stderr, r.stderr
    assert "pool.env overrode your launch NUM_CORES=4 with 8" in r.stderr, r.stderr


def test_no_override_warning_when_launch_env_is_unset(tmp_path):
    """Only an EXPLICIT launch value can be overridden — defaults must stay quiet."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("WORKERS", "NUM_CORES", "WAVE_MAX")}
    pool = _SIZING_LOGDIR / "pool.env"
    try:
        pool.parent.mkdir(parents=True, exist_ok=True)
        pool.write_text("WORKERS=8\nNUM_CORES=8\n")
        env.update(R2G_SIZING_SELFTEST="1", PLATFORM=_SIZING_PLATFORM,
                   SKIP_INSTANCE_GUARD="1", LEDGER=str(tmp_path / "led.jsonl"))
        r = subprocess.run(["bash", str(DRIVER)], cwd=REPO, env=env,
                           capture_output=True, text=True, timeout=60)
    finally:
        shutil.rmtree(_SIZING_LOGDIR, ignore_errors=True)
    assert "overrode your launch" not in r.stderr, r.stderr


def test_oversubscription_is_reported(tmp_path):
    """A request far beyond any plausible free-core count must warn."""
    r = _run_sizing(tmp_path, "WORKERS=512\nNUM_CORES=512\n")
    assert "OVERSUBSCRIBED" in r.stderr, r.stderr
    assert "SHARED host" in r.stderr


def test_modest_sizing_does_not_warn(tmp_path):
    r = _run_sizing(tmp_path, "WORKERS=1\nNUM_CORES=1\n")
    assert "OVERSUBSCRIBED" not in r.stderr, r.stderr
