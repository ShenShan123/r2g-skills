"""Tests for the toolchain bootstrap (detect → plan → pin → verify).

Covers the first slice landed on branch feat/r2g-bootstrap:
  - detect_env.sh   emits a complete KEY=VALUE contract
  - bootstrap.sh    computes the correct per-tier plan from a saved detect dump
                    (synthetic --plan-from fixtures → no real toolchain / network)
  - write_env_local.sh generates a valid pin file (header + R2G_GRAPH_PYTHON)
  - the two _env.sh copies stay byte-identical (CLAUDE.md md5 a5ac873e… invariant)

Design doc: docs/superpowers/plans/r2g-skills-bootstrap-2026-07-08.md.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

import pytest

EDA_ROOT = Path(__file__).resolve().parents[1]          # …/r2g-skills/eda-install
SKILLS_ROOT = EDA_ROOT.parent                           # …/r2g-skills

DETECT = EDA_ROOT / "scripts" / "setup" / "detect_env.sh"
BOOTSTRAP = EDA_ROOT / "bootstrap.sh"
WRITE_ENV = EDA_ROOT / "scripts" / "setup" / "write_env_local.sh"
# The shared resolver ships byte-identical in ALL FOUR skills (CLAUDE.md md5 invariant).
ENV_COPIES = [
    EDA_ROOT / "scripts" / "flow" / "_env.sh",
    SKILLS_ROOT / "signoff-loop" / "scripts" / "flow" / "_env.sh",
    SKILLS_ROOT / "def-graph" / "scripts" / "flow" / "_env.sh",
    SKILLS_ROOT / "rtl-acquire" / "scripts" / "flow" / "_env.sh",
]

DETECT_KEYS = {
    "OS_FAMILY", "PKG_MGR", "HAVE_SUDO", "HAVE_CONDA", "PYTHON3",
    "BIG_VOLUME", "BIG_VOLUME_FREE_GB", "MIN_FREE_GB",
    "ORFS_ROOT", "FLOW_DIR", "OPENROAD_EXE", "YOSYS_EXE", "IVERILOG_EXE",
    "VVP_EXE", "VERILATOR_EXE", "KLAYOUT_CMD", "MAGIC_EXE", "NETGEN_EXE",
    "STA_EXE", "PDK_ROOT", "SKY130A_DIR", "GRAPH_PYTHON",
}

# --- synthetic machines (KEY=VALUE detect dumps) ------------------------------

def _dump(d: dict) -> str:
    return "".join(f"{k}={v}\n" for k, v in d.items())


_PROVISIONED_NOSUDO = {
    "OS_FAMILY": "rhel", "PKG_MGR": "none", "HAVE_SUDO": "0",
    "HAVE_CONDA": "/home/me/miniconda3/bin/conda",
    "BIG_VOLUME": "/proj/me", "BIG_VOLUME_FREE_GB": "100", "MIN_FREE_GB": "15",
    "ORFS_ROOT": "/proj/me/ORFS", "FLOW_DIR": "/proj/me/ORFS/flow",
    "OPENROAD_EXE": "/proj/me/ORFS/tools/install/OpenROAD/bin/openroad",
    "YOSYS_EXE": "/proj/me/ORFS/tools/install/yosys/bin/yosys",
    "IVERILOG_EXE": "/home/me/miniconda3/envs/eda/bin/iverilog",
    "VVP_EXE": "/home/me/miniconda3/envs/eda/bin/vvp",
    "MAGIC_EXE": "/x/magic", "NETGEN_EXE": "/x/netgen", "KLAYOUT_CMD": "/usr/bin/klayout",
    "PDK_ROOT": "/proj/me/pdk", "SKY130A_DIR": "/proj/me/pdk/sky130A",
    "GRAPH_PYTHON": "/proj/me/venv/bin/python",
}

_BARE_NOSUDO = {
    "OS_FAMILY": "rhel", "PKG_MGR": "none", "HAVE_SUDO": "0",
    "HAVE_CONDA": "/c/bin/conda",
    "BIG_VOLUME": "/proj/me", "BIG_VOLUME_FREE_GB": "100", "MIN_FREE_GB": "15",
    "ORFS_ROOT": "", "FLOW_DIR": "", "OPENROAD_EXE": "", "YOSYS_EXE": "",
    "IVERILOG_EXE": "", "VVP_EXE": "", "VERILATOR_EXE": "", "KLAYOUT_CMD": "",
    "MAGIC_EXE": "", "NETGEN_EXE": "", "STA_EXE": "", "PDK_ROOT": "",
    "SKY130A_DIR": "", "GRAPH_PYTHON": "",
}

_BARE_SUDO = dict(_BARE_NOSUDO, HAVE_SUDO="1", PKG_MGR="apt", HAVE_CONDA="")


# --- helpers ------------------------------------------------------------------

_ROW = re.compile(r"^(?P<tier>[a-z][a-z0-9-]*)\s+(?P<status>OK|MISS|OPT|\?)\s+(?P<need>req|opt)\s+(?P<action>.*)$")


def _plan(tmp_path: Path, dump: dict, *extra: str) -> tuple[str, dict]:
    f = tmp_path / "detect.txt"
    f.write_text(_dump(dump))
    out = subprocess.run(
        ["bash", str(BOOTSTRAP), "--plan-from", str(f), *extra],
        capture_output=True, text=True, check=True,
    ).stdout
    rows = {}
    for line in out.splitlines():
        m = _ROW.match(line.strip())
        if m:
            rows[m["tier"]] = (m["status"], m["need"], m["action"])
    return out, rows


# --- detect_env.sh ------------------------------------------------------------

def test_detect_emits_complete_contract():
    out = subprocess.run(["bash", str(DETECT)], capture_output=True, text=True, check=True).stdout
    got = {}
    for line in out.splitlines():
        assert "=" in line, f"non KEY=VALUE stdout line: {line!r}"
        k, _, v = line.partition("=")
        got[k] = v
    assert DETECT_KEYS <= set(got), f"missing keys: {DETECT_KEYS - set(got)}"
    assert got["HAVE_SUDO"] in ("0", "1")
    assert got["MIN_FREE_GB"].isdigit()


# --- bootstrap.sh planner -----------------------------------------------------

def test_plan_provisioned_all_ok(tmp_path):
    out, rows = _plan(tmp_path, _PROVISIONED_NOSUDO)
    for tier in ("core", "frontend", "sky130", "klayout", "pdk", "graph"):
        assert rows[tier][0] == "OK", f"{tier} not OK: {rows[tier]}"
    assert "sudo=NO" in out
    assert "no-sudo" in out


def test_plan_bare_nosudo_uses_conda_no_build(tmp_path):
    _out, rows = _plan(tmp_path, _BARE_NOSUDO)
    # required tiers are MISSing, optional tiers are installable (OPT)
    assert rows["core"][0] == "MISS"
    assert rows["frontend"][0] == "MISS"
    assert rows["sky130"][0] == "OPT"
    assert rows["pdk"][0] == "OPT"
    assert rows["graph"][0] == "OPT"
    # no-sudo → conda binaries, ORFS cloned but NOT built
    assert "conda" in rows["core"][2]
    assert "no build" in rows["core"][2]
    assert "build_openroad" not in rows["core"][2]


def test_plan_bare_sudo_offers_source_build(tmp_path):
    out, rows = _plan(tmp_path, _BARE_SUDO)
    assert rows["core"][0] == "MISS"
    assert "build_openroad" in rows["core"][2]
    assert "sudo=yes" in out


def test_plan_graph_flips_ok_when_present(tmp_path):
    _o1, r_absent = _plan(tmp_path, _BARE_NOSUDO)
    assert r_absent["graph"][0] == "OPT"
    _o2, r_present = _plan(tmp_path, dict(_BARE_NOSUDO, GRAPH_PYTHON="/v/bin/python"))
    assert r_present["graph"][0] == "OK"


def test_plan_tiers_subset(tmp_path):
    _out, rows = _plan(tmp_path, _PROVISIONED_NOSUDO, "--tiers", "core,graph")
    assert set(rows) == {"core", "graph"}


def test_dry_run_installs_nothing(tmp_path):
    f = tmp_path / "detect.txt"
    f.write_text(_dump(_BARE_NOSUDO))
    r = subprocess.run(["bash", str(BOOTSTRAP), "--plan-from", str(f)],
                       capture_output=True, text=True, check=True)
    assert "nothing installed" in r.stdout


# --- write_env_local.sh -------------------------------------------------------

def test_write_env_local_dry_run(tmp_path):
    r = subprocess.run(
        ["bash", str(WRITE_ENV), "--graph-python", "/sentinel/py", "--dry-run"],
        capture_output=True, text=True, check=True,
    )
    body = r.stdout
    assert "GENERATED by scripts/setup/write_env_local.sh" in body
    assert 'export R2G_GRAPH_PYTHON="/sentinel/py"' in body
    # openroad/yosys under $ORFS_ROOT/tools/install must NOT be pinned (autodetect finds them)
    for line in body.splitlines():
        if line.startswith("export OPENROAD_EXE="):
            assert "/tools/install/" not in line


def _run_write_env_no_graph(*args):
    """Run write_env_local.sh with R2G_GRAPH_PYTHON guaranteed absent from the env."""
    import os
    env = {k: v for k, v in os.environ.items() if k != "R2G_GRAPH_PYTHON"}
    return subprocess.run(
        ["bash", str(WRITE_ENV), *args],
        capture_output=True, text=True, check=True, env=env,
    )


def test_write_env_local_preserves_existing_graph_pin(tmp_path):
    """Regenerating pins must NOT drop an existing R2G_GRAPH_PYTHON (failure-patterns #26).

    2026-07-09: a bootstrap-wide pin regeneration run from a shell without
    R2G_GRAPH_PYTHON silently stripped the graph-venv pin from signoff-loop +
    def-graph on a provisioned machine — run_graphs.sh then SKIPs the PyG stage
    (graph_skipped) while looking like success. The writer must recall the pin
    from the target's existing env.local.sh.
    """
    fake_py = tmp_path / "venv" / "bin" / "python"
    fake_py.parent.mkdir(parents=True)
    fake_py.write_text("#!/bin/sh\nexit 0\n")
    fake_py.chmod(0o755)
    refs = tmp_path / "references"
    refs.mkdir()
    (refs / "env.local.sh").write_text(
        f'export R2G_GRAPH_PYTHON="{fake_py}"\n')

    _run_write_env_no_graph("--target", str(refs))

    body = (refs / "env.local.sh").read_text()
    assert f'export R2G_GRAPH_PYTHON="{fake_py}"' in body, \
        "regeneration dropped the existing graph-venv pin"


def test_write_env_local_hints_when_graph_pin_absent(tmp_path):
    """With no pin anywhere, the generated file must carry a loud HINT, not silence."""
    refs = tmp_path / "references"
    refs.mkdir()

    _run_write_env_no_graph("--target", str(refs))

    body = (refs / "env.local.sh").read_text()
    assert "export R2G_GRAPH_PYTHON=" not in body
    assert "HINT" in body and "R2G_GRAPH_PYTHON" in body and "SKIP" in body, \
        "missing graph pin must be loudly HINTed in the generated file"


# --- byte-identical _env.sh invariant (CLAUDE.md) -----------------------------

def test_env_sh_copies_identical():
    digests = {p: hashlib.md5(p.read_bytes()).hexdigest() for p in ENV_COPIES}
    assert len(set(digests.values())) == 1, (
        "scripts/flow/_env.sh has diverged across skills: "
        + ", ".join(f"{p.parents[2].name}={h[:8]}" for p, h in digests.items())
    )


# --- conda-staged PDK autodetect ----------------------------------------------

def test_env_sh_detects_conda_staged_pdk(tmp_path):
    # open_pdks.sky130a stages sky130A under <conda>/envs/<env>/share/pdk (the pdk
    # tier's install location); _env.sh must autodetect it. Uses the eda-install copy
    # (no references/env.local.sh to preset PDK_ROOT) + a minimal env so nothing else
    # can set PDK_ROOT — so this can only pass if the conda-PDK block actually ran.
    base = tmp_path / "miniconda3" / "envs" / "eda" / "share" / "pdk"
    (base / "sky130A").mkdir(parents=True)
    envsh = ENV_COPIES[0]  # …/eda-install/scripts/flow/_env.sh
    minimal = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path / "nohome"),            # keep $HOME/miniconda3 from matching a real one
        "CONDA_PREFIX": str(tmp_path / "miniconda3"),
        "R2G_CONDA_ENV": "eda",
    }
    script = (f'source "{envsh}" >/dev/null 2>&1; '
              f'echo "PDK_ROOT=${{PDK_ROOT:-}}"; echo "SKY=${{SKY130A_DIR:-}}"')
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=minimal).stdout
    assert f"PDK_ROOT={base}" in out
    assert f"SKY={base}/sky130A" in out


# --- relocated conda root + hand-staged PDK autodetect (failure-patterns #29) ---

def test_env_sh_detects_relocated_conda_tools_and_staged_pdk(tmp_path):
    """A conda `eda` env on a big volume (not $HOME) and a hand-staged PDK must be
    autodetected by _env.sh WITHOUT pins (failure-patterns #29: the 2026-07-09
    conda relocation to /proj/workarea/$USER/miniconda3 + sky130_pdk staging were
    findable only via env.local.sh pins — one pin regeneration lost them and
    sky130 DRC/LVS would have silently skipped). Exercised via the $HOME-based
    probes; the /proj/workarea/$USER probes share the same loop."""
    conda_bin = tmp_path / "miniconda3" / "envs" / "eda" / "bin"
    conda_bin.mkdir(parents=True)
    for tool in ("iverilog", "vvp", "magic", "netgen"):
        exe = conda_bin / tool
        exe.write_text("#!/bin/sh\nexit 0\n")
        exe.chmod(0o755)
    staged_pdk = tmp_path / "sky130_pdk" / "share" / "pdk"
    (staged_pdk / "sky130A").mkdir(parents=True)

    envsh = ENV_COPIES[0]  # eda-install copy: no references/env.local.sh to preset anything
    minimal = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "R2G_CONDA_ENV": "eda",
    }
    script = (f'source "{envsh}" >/dev/null 2>&1; '
              f'for v in IVERILOG_EXE VVP_EXE MAGIC_EXE NETGEN_EXE PDK_ROOT; do '
              f'eval "echo $v=\\${{$v:-}}"; done')
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=minimal).stdout
    for tool, var in (("iverilog", "IVERILOG_EXE"), ("vvp", "VVP_EXE"),
                      ("magic", "MAGIC_EXE"), ("netgen", "NETGEN_EXE")):
        assert f"{var}={conda_bin / tool}" in out, f"{var} not autodetected from conda env bin:\n{out}"
    assert f"PDK_ROOT={staged_pdk}" in out, f"staged sky130_pdk not autodetected:\n{out}"


def test_write_env_local_preserves_all_pins(tmp_path):
    """Regenerating pins must NOT drop ANY existing pin-only value (failure-patterns #29,
    generalizing #26): write_env_local.sh resolves through the eda-install copy of
    _env.sh, whose skill dir has no references/env.local.sh — values that existed
    only as pins in the TARGET's env.local.sh (conda signoff tools, a staged
    PDK_ROOT) were silently dropped on every regeneration (bit 2026-07-09 19:12:
    sky130 signoff tools + PDK unpinned on a provisioned machine)."""
    fake_bin = tmp_path / "eda" / "bin"
    fake_bin.mkdir(parents=True)
    pins = {}
    for tool, var in (("iverilog", "IVERILOG_EXE"), ("vvp", "VVP_EXE"),
                      ("magic", "MAGIC_EXE"), ("netgen", "NETGEN_EXE")):
        exe = fake_bin / tool
        exe.write_text("#!/bin/sh\nexit 0\n")
        exe.chmod(0o755)
        pins[var] = str(exe)
    pdk = tmp_path / "pdk"
    (pdk / "sky130A").mkdir(parents=True)

    refs = tmp_path / "references"
    refs.mkdir()
    (refs / "env.local.sh").write_text(
        "".join(f'export {var}="{path}"\n' for var, path in pins.items())
        + f'export PDK_ROOT="{pdk}"\n')

    env = {k: v for k, v in __import__("os").environ.items()
           if k not in pins and k not in ("PDK_ROOT", "SKY130A_DIR", "R2G_GRAPH_PYTHON")}
    subprocess.run(["bash", str(WRITE_ENV), "--target", str(refs)],
                   capture_output=True, text=True, check=True, env=env)

    body = (refs / "env.local.sh").read_text()
    for var, path in pins.items():
        assert f'export {var}="{path}"' in body, \
            f"regeneration dropped the existing {var} pin:\n{body}"
    assert f'export PDK_ROOT="{pdk}"' in body, \
        f"regeneration dropped the existing PDK_ROOT pin:\n{body}"


def test_write_env_local_drops_stale_tool_pin(tmp_path):
    """The recall is validated, not blind: a pin to a deleted binary must NOT be
    carried forward (that would freeze a dead path into every future regen)."""
    refs = tmp_path / "references"
    refs.mkdir()
    (refs / "env.local.sh").write_text(
        f'export IVERILOG_EXE="{tmp_path}/gone/iverilog"\n'
        f'export PDK_ROOT="{tmp_path}/gone-pdk"\n')

    env = {k: v for k, v in __import__("os").environ.items()
           if k not in ("IVERILOG_EXE", "PDK_ROOT", "SKY130A_DIR")}
    subprocess.run(["bash", str(WRITE_ENV), "--target", str(refs)],
                   capture_output=True, text=True, check=True, env=env)

    body = (refs / "env.local.sh").read_text()
    assert f'{tmp_path}/gone/iverilog' not in body, "stale tool pin was carried forward"
    assert f'{tmp_path}/gone-pdk' not in body, "stale PDK_ROOT pin was carried forward"
