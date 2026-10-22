"""Where things live: the repo with the skills, and the user's r2g home."""
from __future__ import annotations

import os
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent


def repo_root() -> Path:
    """The r2g-skills checkout this package belongs to (agent/ sits in it)."""
    env = os.environ.get("R2G_REPO")
    if env:
        return Path(env).resolve()
    return PKG_DIR.parent.parent


def skills_dir() -> Path:
    return repo_root() / "r2g-skills"


def signoff_scripts() -> Path:
    return skills_dir() / "signoff-loop" / "scripts"


def defgraph_scripts() -> Path:
    return skills_dir() / "def-graph" / "scripts"


def tools_dir() -> Path:
    return repo_root() / "tools"


def r2g_home() -> Path:
    """Per-user state: config, job database, workspaces."""
    home = Path(os.environ.get("R2G_HOME", Path.home() / ".r2g"))
    home.mkdir(parents=True, exist_ok=True)
    return home


def examples_dir() -> Path:
    return PKG_DIR / "examples"
