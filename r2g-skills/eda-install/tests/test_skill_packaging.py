"""The four skills must load in both agents that read the SKILL.md format: Claude Code and OpenAI Codex.

Codex parses SKILL.md frontmatter with a strict YAML parser and silently leaves a skill out of its catalog when the
frontmatter does not parse (2026-11-24: def-graph was missing from Codex's skill list because a wrapped plain-scalar
list item in its metadata was invalid YAML, while Claude Code loaded it). install.sh must put the skills where each
agent looks: Claude Code ~/.claude/skills or <project>/.claude/skills, Codex ${CODEX_HOME:-~/.codex}/skills or
<project>/.agents/skills.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]          # r2g-skills/ (the skill collection)
SKILLS = ["eda-install", "rtl-acquire", "signoff-loop", "def-graph"]
SKILL_FILES = [ROOT / s / "SKILL.md" for s in SKILLS] + [ROOT / "rtl-acquire/vendor/rtl-expander/SKILL.md"]


def _frontmatter(path: Path) -> str:
    m = re.match(r"---\n(.*?)\n---\n", path.read_text(), re.S)
    assert m, f"{path} has no frontmatter block"
    return m.group(1)


@pytest.mark.parametrize("path", SKILL_FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_frontmatter_is_strict_yaml_with_name_and_description(path):
    yaml = pytest.importorskip("yaml")
    meta = yaml.safe_load(_frontmatter(path))
    assert isinstance(meta, dict)
    assert meta.get("name") == path.parent.name, "name must match the skill directory"
    desc = meta.get("description")
    assert isinstance(desc, str) and desc.strip(), "description must be a non-empty string"
    assert len(desc) <= 1024, "keep the description within the 1024-character skill limit"


@pytest.mark.parametrize("agent,scope,expected", [
    ("claude", "project", ".claude/skills"),
    ("codex", "project", ".agents/skills"),
    ("codex", "user", "codex_home/skills"),
])
def test_installer_targets_each_agent(tmp_path, agent, scope, expected):
    args = ["bash", str(ROOT / "install.sh"), "--link"]
    if agent == "codex":
        args.append("--codex")
    args += ["--project", str(tmp_path)] if scope == "project" else ["--user"]
    env = dict(os.environ, CODEX_HOME=str(tmp_path / "codex_home"), HOME=str(tmp_path / "home"))
    subprocess.run(args, check=True, capture_output=True, text=True, env=env)
    dest = tmp_path / expected
    for s in SKILLS:
        assert (dest / s / "SKILL.md").is_file(), f"{s} not installed under {dest}"
    if agent == "codex":
        assert not (tmp_path / ".claude").exists() and not (tmp_path / "home" / ".claude").exists()
