"""Decision tests for guardian.py with the OS layer faked: nothing is signalled, nothing is read from /proc."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import guardian  # noqa: E402

GIB = 2 ** 30


def proc(pid, comm, pgid, rss_gib, ppid=100, start=0):
    return {"pid": pid, "comm": comm, "ppid": ppid, "pgid": pgid, "start": start, "rss": int(rss_gib * GIB)}


@pytest.fixture
def fake(monkeypatch, tmp_path):
    calls = {"kill": [], "stop": [], "cont": []}
    st = {"procs": [], "anon": None, "avail": 500.0, "free": 1000.0, "cmd": {}}
    monkeypatch.setattr(guardian, "our_procs", lambda: st["procs"])
    monkeypatch.setattr(guardian, "cmdline", lambda pid: st["cmd"].get(pid, ""))
    monkeypatch.setattr(guardian, "cgroup_anon_gib", lambda: st["anon"])
    monkeypatch.setattr(guardian, "mem_available_gib", lambda: st["avail"])
    monkeypatch.setattr(guardian, "podman_containers", lambda: [])
    monkeypatch.setattr(guardian, "kill_group", lambda pg: calls["kill"].append(pg))
    monkeypatch.setattr(guardian, "age_s", lambda start: start)

    class VFS:
        f_bavail, f_frsize = 0, 1
    def statvfs(_d):
        v = VFS(); v.f_bavail = int(st["free"] * GIB); return v
    monkeypatch.setattr(guardian.os, "statvfs", statvfs)
    monkeypatch.setattr(guardian.os, "kill", lambda pid, sig: calls["stop" if sig == guardian.signal.SIGSTOP else "cont"].append(pid))

    def run(*extra):
        monkeypatch.setattr(sys, "argv", ["guardian", "--state", str(tmp_path / "s"), "--watch-dir", "/x", *extra])
        guardian.main()
        lines = (tmp_path / "s" / "guardian.jsonl").read_text().splitlines() if (tmp_path / "s" / "guardian.jsonl").exists() else []
        return [json.loads(l) for l in lines]
    return st, calls, run


def test_group_over_bound_is_killed_and_queued_for_annotation(fake, tmp_path):
    st, calls, run = fake
    st["procs"] = [proc(10, "openroad", 10, 210), proc(20, "openroad", 20, 30)]
    st["cmd"] = {10: "openroad -metrics /work/logs/asap7/fabric22/base/x.json"}
    log = run()
    assert calls["kill"] == [10]
    assert log[0]["reason"] == "group_bound" and log[0]["design"] == "fabric22"
    assert json.loads((tmp_path / "s" / "pending.json").read_text())[0]["design"] == "fabric22"


def test_normal_large_design_is_left_alone(fake):
    st, calls, run = fake
    st["procs"] = [proc(10, "openroad", 10, 40)]
    run()
    assert calls["kill"] == []


def test_cgroup_pressure_kills_largest_only_above_min(fake):
    st, calls, run = fake
    st["anon"] = 250
    st["procs"] = [proc(10, "openroad", 10, 150), proc(20, "yosys", 20, 80)]
    run("--cgroup-act-gib", "240")
    assert calls["kill"] == [10]


def test_pressure_without_a_large_target_does_not_kill(fake):
    st, calls, run = fake
    st["avail"] = 100
    st["procs"] = [proc(10, "openroad", 10, 20)]
    log = run("--avail-floor-gib", "150")
    assert calls["kill"] == [] and log[-1]["action"] == "pressure_no_target"


def test_non_tool_groups_are_never_targets(fake):
    st, calls, run = fake
    st["procs"] = [proc(10, "python3", 10, 300), proc(11, "bash", 11, 300)]   # e.g. someone's analysis
    run()
    assert calls["kill"] == []


def test_disk_low_pauses_drivers_and_recovery_resumes_them(fake):
    st, calls, run = fake
    st["procs"] = [proc(30, "python3", 30, 0.1)]
    st["cmd"] = {30: "python3 orfs_spec.py --select x"}
    st["free"] = 50
    run("--disk-stop-gib", "80", "--disk-resume-gib", "120")
    assert calls["stop"] == [30]
    st["free"] = 100                       # between thresholds: stay paused
    run("--disk-stop-gib", "80", "--disk-resume-gib", "120")
    assert calls["cont"] == []
    st["free"] = 130
    run("--disk-stop-gib", "80", "--disk-resume-gib", "120")
    assert calls["cont"] == [30]


def test_orphan_tool_past_age_is_killed(fake):
    st, calls, run = fake
    st["procs"] = [proc(40, "openroad", 40, 1, ppid=1, start=8 * 3600), proc(41, "openroad", 41, 1, ppid=1, start=3600)]
    run("--orphan-max-h", "7")
    assert calls["kill"] == [40]


def test_dry_run_acts_on_nothing(fake):
    st, calls, run = fake
    st["procs"] = [proc(10, "openroad", 10, 250)]
    log = run("--dry-run")
    assert calls["kill"] == [] and log[0]["dry_run"] is True
