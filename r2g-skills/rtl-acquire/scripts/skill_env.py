#!/usr/bin/env python3
"""rtl-acquire environment resolution — a thin delegate over the shared r2g env.

Resolution order for every value (mirrors scripts/flow/_env.sh):
  1. explicit shell environment variable
  2. the shared env (`scripts/flow/_env.sh` sourced once, which itself honors
     $R2G_ENV_FILE and <skill>/references/env.local.sh, then autodetects)
  3. script default

Toolchain values (ORFS_ROOT, FLOW_DIR, YOSYS_EXE, R2G_GRAPH_PYTHON, ...) are
NEVER autodetected here — they come from the shared _env.sh, byte-identical
across all r2g sub-skills and pinned by eda-install's write_env_local.sh.
Only the corpus roots are rtl-acquire's own namespace (R2G_ACQUIRE_*).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
REF_DIR = SKILL_DIR / "references"
ENV_SH = SCRIPT_DIR / "flow" / "_env.sh"
R2G_SKILLS_DIR = SKILL_DIR.parent
REPO_ROOT = R2G_SKILLS_DIR.parent

# Shared-env keys worth capturing from _env.sh (plus any R2G_* pin).
_SHARED_KEY_RE = re.compile(
    r"^(R2G_[A-Z0-9_]+|ORFS_ROOT|FLOW_DIR|PDK_ROOT|SKY130A_DIR"
    r"|OPENROAD_EXE|YOSYS_EXE|KLAYOUT_CMD|MAGIC_EXE|NETGEN_EXE|STA_EXE"
    r"|IVERILOG_EXE|VVP_EXE|VERILATOR_EXE)$"
)

_shared_env_cache: dict[str, str] | None = None


def _parse_env_local(path: Path) -> dict[str, str]:
    """Pure-python fallback parse of an `export KEY=VALUE` snippet."""
    if not path.exists():
        return {}
    env: dict[str, str] = {}
    export_re = re.compile(r"^\s*export\s+([A-Za-z_][A-Za-z0-9_]*)=(.*)\s*$")
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        match = export_re.match(line.strip())
        if not match:
            continue
        value = match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        value = re.sub(
            r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))",
            lambda m: env.get(m.group(1) or m.group(2), m.group(0)),
            value,
        )
        env[match.group(1)] = os.path.expandvars(os.path.expanduser(value))
    return env


def shared_env(refresh: bool = False) -> dict[str, str]:
    """Source the shared _env.sh once and capture what it resolved."""
    global _shared_env_cache
    if _shared_env_cache is not None and not refresh:
        return _shared_env_cache
    env: dict[str, str] = {}
    if ENV_SH.exists():
        try:
            out = subprocess.run(
                ["bash", "-c", f'source "{ENV_SH}" >/dev/null 2>&1 && env -0'],
                capture_output=True,
                timeout=30,
            )
            if out.returncode == 0:
                for chunk in out.stdout.decode("utf-8", errors="ignore").split("\0"):
                    if "=" not in chunk:
                        continue
                    key, value = chunk.split("=", 1)
                    if _SHARED_KEY_RE.match(key):
                        env[key] = value
        except Exception:
            env = {}
    if not env:
        # bash unavailable (e.g. sandboxed unit test) — parse the pins directly.
        env = {
            k: v
            for k, v in _parse_env_local(REF_DIR / "env.local.sh").items()
            if _SHARED_KEY_RE.match(k)
        }
    _shared_env_cache = env
    return env


def resolve_str_env(name: str, default: str) -> str:
    return os.environ.get(name) or shared_env().get(name) or default


def resolve_path_env(name: str, default: str | Path) -> Path:
    return Path(resolve_str_env(name, str(default))).expanduser()


# --- corpus roots (rtl-acquire's own namespace) ----------------------------

def default_acquire_root() -> Path:
    return resolve_path_env(
        "R2G_ACQUIRE_ROOT", REPO_ROOT / "design_cases" / "_rtl_acquire"
    )


def default_workspace_root() -> Path:
    return resolve_path_env("R2G_ACQUIRE_WORKSPACE", default_acquire_root() / "workspace")


def default_out_root() -> Path:
    return resolve_path_env("R2G_ACQUIRE_OUT", default_acquire_root() / "corpus")


def default_downloads_root() -> Path:
    return resolve_path_env("R2G_ACQUIRE_DOWNLOADS", default_acquire_root() / "_downloads")


def default_seed_root() -> Path:
    return resolve_path_env("R2G_ACQUIRE_SEED_ROOT", default_acquire_root() / "orfs_seed_designs")


def default_merged_manifest() -> Path:
    return resolve_path_env(
        "R2G_ACQUIRE_MERGED_MANIFEST",
        default_acquire_root() / "netlist_graph_corpus_manifest.csv",
    )


# Legacy catch-alls kept for scripts that stage odd one-off artifacts.
def default_data_root() -> Path:
    return resolve_path_env("R2G_ACQUIRE_DATA_ROOT", default_acquire_root())


def default_work_root() -> Path:
    return resolve_path_env("R2G_ACQUIRE_WORK_ROOT", default_acquire_root())


# --- toolchain (always via the shared env) ---------------------------------

def default_flow_dir() -> Path:
    flow = resolve_str_env("FLOW_DIR", "")
    if flow:
        return Path(flow)
    orfs = resolve_str_env("ORFS_ROOT", "")
    if orfs:
        return Path(orfs) / "flow"
    return REPO_ROOT.parent / "OpenROAD-flow-scripts" / "flow"


def default_python_bin() -> str:
    return resolve_str_env("R2G_ACQUIRE_PYTHON", sys.executable)


def graph_python() -> str:
    """Torch-venv python for graph conversion; empty string => SKIP stage."""
    return resolve_str_env("R2G_GRAPH_PYTHON", "")


def default_yosys() -> str:
    return resolve_str_env("YOSYS_EXE", "yosys")


# --- sibling sub-skills (the scoped-reuse contract) -------------------------

def signoff_loop_dir() -> Path:
    return resolve_path_env("R2G_SIGNOFF_LOOP_DIR", R2G_SKILLS_DIR / "signoff-loop")


def def_graph_dir() -> Path:
    return resolve_path_env("R2G_DEF_GRAPH_DIR", R2G_SKILLS_DIR / "def-graph")


def run_orfs_script() -> Path:
    return signoff_loop_dir() / "scripts" / "flow" / "run_orfs.sh"


def netlist_graph_script() -> Path:
    return def_graph_dir() / "scripts" / "extract" / "graph" / "netlist_graph.py"


def resolve_platform_paths_script() -> Path:
    return def_graph_dir() / "scripts" / "flow" / "resolve_platform_paths.sh"


def knowledge_dir() -> Path:
    return signoff_loop_dir() / "knowledge"


# --- path helpers (the scrubbed call-site surface) ---------------------------

def skill_path(rel: str) -> Path:
    return SKILL_DIR / rel


def skill_reference_path(rel: str) -> Path:
    return REF_DIR / rel


def workspace_path(rel: str) -> Path:
    return default_workspace_root() / rel


def out_root_path(rel: str) -> Path:
    return default_out_root() / rel


def data_path(rel: str) -> Path:
    return default_data_root() / rel


def downloads_path(rel: str) -> Path:
    return default_downloads_root() / rel


def seed_root_path(rel: str) -> Path:
    return default_seed_root() / rel


def work_path(rel: str) -> Path:
    return default_work_root() / rel


def main() -> int:
    print(f"SKILL_DIR={SKILL_DIR}")
    print(f"acquire_root={default_acquire_root()}")
    print(f"workspace_root={default_workspace_root()}")
    print(f"out_root={default_out_root()}")
    print(f"downloads_root={default_downloads_root()}")
    print(f"merged_manifest={default_merged_manifest()}")
    print(f"flow_dir={default_flow_dir()}")
    print(f"python_bin={default_python_bin()}")
    print(f"graph_python={graph_python() or '(unset — graph stage SKIPs)'}")
    print(f"yosys={default_yosys()}")
    print(f"run_orfs={run_orfs_script()}")
    print(f"netlist_graph={netlist_graph_script()}")
    for key in ("ORFS_ROOT", "OPENROAD_EXE", "R2G_SC_LIB_FILES", "R2G_LIB_FILES"):
        value = resolve_str_env(key, "")
        print(f"{key}={value or '(unresolved)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
