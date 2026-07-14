#!/usr/bin/env python3
"""Collect EDA + repo tool-version fingerprints for provenance stamping.

fix_events.tool_versions_json and A/B trial metrics were shipped with the column
but NEVER a writer, so 100% of historical fix events had a null version fingerprint
and no promotion could be pinned to the toolchain that produced it (2026-07-13,
failure-patterns #45). This module is the single writer.

Design constraints (this runs on the ingest hot path):
  * CACHED — `collect()` shells out at most once per process (lru_cache).
  * FAIL-SAFE — a missing/hanging tool records null for that entry, never raises
    (subprocess timeouts are swallowed); ingest must never crash on a toolchain gap.
  * OVERRIDABLE — R2G_TOOL_VERSIONS_JSON injects the whole map verbatim, for
    reproducible re-ingest and deterministic tests.

The fingerprint is the tool's OWN version string plus the ORFS + agent git HEADs —
enough to answer "which toolchain produced this evidence?" without pinning a full
image digest (that stays an operator/CI concern).
"""
from __future__ import annotations

import functools
import json
import os
import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _run(cmd: list[str]) -> str | None:
    """First non-empty stripped line of `cmd` stdout+stderr, or None on any failure
    (not found, nonzero exit, timeout). Version banners often print to stderr."""
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.SubprocessError):
        return None
    for line in (out.stdout + "\n" + out.stderr).splitlines():
        line = line.strip()
        if line:
            return line[:200]
    return None


def _git_head(root: str | None) -> str | None:
    if not root or not Path(root).exists():
        return None
    return _run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"])


def _klayout_cmd() -> list[str]:
    cmd = os.environ.get("KLAYOUT_CMD")
    return cmd.split() if cmd else ["klayout"]


@functools.lru_cache(maxsize=1)
def collect() -> dict:
    """{openroad, yosys, klayout, orfs, agent} version fingerprint. Cached."""
    override = os.environ.get("R2G_TOOL_VERSIONS_JSON")
    if override:
        try:
            return json.loads(override)
        except ValueError:
            pass
    return {
        "openroad": _run([os.environ.get("OPENROAD_EXE", "openroad"), "-version"]),
        "yosys": _run([os.environ.get("YOSYS_EXE", "yosys"), "-V"]),
        "klayout": _run(_klayout_cmd() + ["-v"]),
        "orfs": _git_head(os.environ.get("ORFS_ROOT")),
        "agent": _git_head(str(_REPO_ROOT)),
    }


def collect_json() -> str:
    """Canonical JSON of collect() (stable key order) for stamping into a column."""
    return json.dumps(collect(), sort_keys=True)


if __name__ == "__main__":
    print(json.dumps(collect(), indent=2, sort_keys=True))
