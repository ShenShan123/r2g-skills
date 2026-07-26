"""RMD3-P1-02 (failure-patterns.md #58): capability metadata must be bound to
the RESOLVED signoff environment, never the parent's ambient one.

The 2026-07-24/26 pilots shipped eight sky130hd/hs manifests declaring
`platform_capability.missing=["lvs"]` while the SAME manifest bound clean
Netgen LVS evidence and (in six/seven cases) declared strict_clean=true. Root
cause: parent entrypoints probed capability against ambient env; child checks
resolved tools/PDK via _env.sh."""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
FLOW = SKILL / "scripts" / "flow"
REPORTS = SKILL / "scripts" / "reports"
for p in (str(FLOW), str(REPORTS)):
    if p not in sys.path:
        sys.path.insert(0, p)

import build_signoff_manifest as bsm  # noqa: E402
import platform_capability as pc  # noqa: E402


def _fake_sky130_env(tmp_path) -> dict:
    """A self-contained fake resolved env: executable magic/netgen + PDK tree."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    for name in ("magic", "netgen"):
        exe = bindir / name
        exe.write_text("#!/bin/sh\nexit 0\n")
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    pdk = tmp_path / "pdk"
    (pdk / "sky130A" / "libs.tech" / "magic").mkdir(parents=True)
    (pdk / "sky130A" / "libs.tech" / "netgen").mkdir(parents=True)
    (pdk / "sky130A" / "libs.tech" / "magic" / "sky130A.tech").write_text("tech\n")
    (pdk / "sky130A" / "libs.tech" / "netgen" / "sky130A_setup.tcl").write_text("setup\n")
    return {"MAGIC_EXE": str(bindir / "magic"),
            "NETGEN_EXE": str(bindir / "netgen"),
            "PDK_ROOT": str(pdk),
            "PATH": str(bindir)}


def _fake_flow_dir(tmp_path) -> Path:
    fd = tmp_path / "flow"
    pdir = fd / "platforms" / "sky130hd"
    pdir.mkdir(parents=True)
    (pdir / "config.mk").write_text("export PLATFORM = sky130hd\n")
    return fd


def test_probe_uses_explicit_env_not_ambient(tmp_path, monkeypatch):
    fd = _fake_flow_dir(tmp_path)
    env = _fake_sky130_env(tmp_path)
    # Ambient is scrubbed — exactly the pilot's parent-entrypoint condition.
    for k in ("MAGIC_EXE", "NETGEN_EXE", "PDK_ROOT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("PATH", "/nonexistent")
    caps_ambient = pc.probe_platform(str(fd), "sky130hd")
    assert caps_ambient["lvs"]["ok"] is False          # ambient cannot see the tools
    caps_resolved = pc.probe_platform(str(fd), "sky130hd", env=env)
    assert caps_resolved["lvs"]["ok"] is True, caps_resolved["lvs"]
    assert "lvs" not in caps_resolved["missing"]


def test_env_digest_stable_and_path_independent(tmp_path):
    env = _fake_sky130_env(tmp_path)
    d1 = pc.signoff_env_digest(env)
    d2 = pc.signoff_env_digest(dict(env, PATH="/completely/different"))
    assert d1 == d2                                    # PATH is session noise
    d3 = pc.signoff_env_digest(dict(env, PDK_ROOT="/elsewhere"))
    assert d3 != d1                                    # resolved paths bind


def _clean_bundle_project(tmp_path) -> Path:
    """A project whose report bundle is fully strict-clean, all bound to RUN_X."""
    proj = tmp_path / "proj"
    rep = proj / "reports"
    rep.mkdir(parents=True)
    (proj / "constraints").mkdir()
    (proj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = demo\nexport PLATFORM = sky130hd\n")
    (proj / "constraints" / "constraint.sdc").write_text(
        "set clk_period 10.0\ncreate_clock -period $clk_period [get_ports clk]\n")
    run = proj / "backend" / "RUN_X" / "results"
    run.mkdir(parents=True)
    (run / "6_final.def").write_text("DESIGN demo ;\n")
    docs = {
        "drc.json": {"status": "clean", "total_violations": 0, "run_tag": "RUN_X"},
        "lvs.json": {"status": "clean", "mismatch_count": 0, "run_tag": "RUN_X"},
        "route.json": {"status": "clean", "total_violations": 0, "backend_run": "RUN_X"},
        "rcx.json": {"status": "complete"},
        "timing_check.json": {"tier": "clean", "wns_ns": 0.4, "clock_period_ns": 10.0},
        "ppa.json": {"orfs_status": "complete", "run_dir": str(proj / "backend" / "RUN_X")},
        "fmax_search.json": {"status": "ok", "winner": {"period": 10.0}},
    }
    for fn, doc in docs.items():
        (rep / fn).write_text(json.dumps(doc))
    return proj


def test_manifest_blocks_strict_clean_on_capability_contradiction(tmp_path, monkeypatch):
    proj = _clean_bundle_project(tmp_path)
    fd = _fake_flow_dir(tmp_path)
    # Probe environment resolves, but capability is NOT strict-ready.
    monkeypatch.setattr(pc, "resolve_signoff_env", lambda **kw: {"PATH": "/nonexistent"})
    monkeypatch.setattr(pc, "find_flow_dir", lambda explicit=None, env=None: str(fd))
    m = bsm.build(str(proj))
    cap = m["platform_capability"]
    assert cap is not None and cap["strict_signoff_ready"] is False
    assert m["strict_clean"] is False
    assert any("RMD3-P1-02" in reason for reason in m["strict_missing"]), m["strict_missing"]


def test_manifest_strict_clean_with_consistent_capability(tmp_path, monkeypatch):
    proj = _clean_bundle_project(tmp_path)
    fd = _fake_flow_dir(tmp_path)
    env = _fake_sky130_env(tmp_path)
    monkeypatch.setattr(pc, "resolve_signoff_env", lambda **kw: env)
    monkeypatch.setattr(pc, "find_flow_dir", lambda explicit=None, env=None: str(fd))
    m = bsm.build(str(proj))
    cap = m["platform_capability"]
    # The record carries the resolved provenance the plan requires.
    assert cap["env_source"] == "_env.sh"
    assert cap["resolved_env"]["NETGEN_EXE"] == env["NETGEN_EXE"]
    assert cap["resolved_env"]["PDK_ROOT"] == env["PDK_ROOT"]
    assert "PATH" not in cap["resolved_env"]
    assert cap["env_digest"] == pc.signoff_env_digest(env)
    # sky130hd LVS reads capable under the resolved env; the fake platform dir
    # lacks a DRC deck etc., so full strict-readiness is legitimately false and
    # the consistency gate must therefore hold strict_clean back — no
    # contradictory clean manifest can be emitted either way.
    assert cap["missing"] and "lvs" not in cap["missing"]
    assert m["strict_clean"] is False


def test_manifest_blocks_strict_clean_when_env_unresolvable(tmp_path, monkeypatch):
    proj = _clean_bundle_project(tmp_path)
    monkeypatch.setattr(pc, "resolve_signoff_env", lambda **kw: None)
    monkeypatch.setattr(pc, "find_flow_dir", lambda explicit=None, env=None: None)
    m = bsm.build(str(proj))
    assert m["platform_capability"] is None
    assert m["strict_clean"] is False
    assert any("RMD3-P1-02" in r for r in m["strict_missing"])


def test_cli_manifest_carries_env_provenance(tmp_path):
    """#58 follow-up: the CLI's JSON is persisted by eda-install into
    install_manifest.json — it must carry falsifiable env provenance."""
    import subprocess
    fd = _fake_flow_dir(tmp_path)
    mod = FLOW / "platform_capability.py"
    r = subprocess.run([sys.executable, str(mod), "--flow-dir", str(fd),
                        "--platform", "sky130hd"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    m = json.loads(r.stdout)
    assert m["env_source"] in ("_env.sh", "ambient")
    assert len(m["env_digest"]) == 64
    assert isinstance(m["resolved_env"], dict) and "PATH" not in m["resolved_env"]
    # Escape hatch: --no-resolve-env probes ambient, and says so.
    r2 = subprocess.run([sys.executable, str(mod), "--flow-dir", str(fd),
                         "--platform", "sky130hd", "--no-resolve-env"],
                        capture_output=True, text=True, timeout=120)
    assert json.loads(r2.stdout)["env_source"] == "ambient"


def test_manifest_records_probe_error_not_ambiguous_none(tmp_path, monkeypatch):
    """#58 follow-up: 'probe crashed' must be distinguishable from 'not probed'
    — and still block strict_clean."""
    proj = _clean_bundle_project(tmp_path)

    def _boom(**kw):
        raise RuntimeError("probe exploded")
    monkeypatch.setattr(pc, "resolve_signoff_env", _boom)
    m = bsm.build(str(proj))
    cap = m["platform_capability"]
    assert cap is not None and "probe exploded" in cap["probe_error"]
    assert m["strict_clean"] is False
    assert any("probe crashed" in r for r in m["strict_missing"])


def test_resolve_signoff_env_returns_dict_or_none():
    env = pc.resolve_signoff_env()
    # Machine-dependent content; the CONTRACT is a dict of non-empty strings
    # (or None when _env.sh cannot resolve).
    if env is not None:
        assert isinstance(env, dict)
        assert all(isinstance(v, str) and v for v in env.values())
