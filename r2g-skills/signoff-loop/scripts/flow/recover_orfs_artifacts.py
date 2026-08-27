#!/usr/bin/env python3
"""Recover an already-completed ORFS workspace into its empty r2g backend run.

This is intentionally a recovery operation, not a way to manufacture a pass.  It
requires a successful six-stage journal, a successful run-meta record, and the
canonical final GDS/DEF/ODB in the exact platform/nickname/variant workspace.
The copied bytes and their source are recorded in artifact-recovery.json.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path


REQUIRED_STAGES = ("synth", "floorplan", "place", "cts", "route", "finish")
FINAL_FILES = ("6_final.gds", "6_final.def", "6_final.odb")


def _config_value(path: Path, key: str) -> str | None:
    pattern = re.compile(rf"^\s*export\s+{re.escape(key)}\s*=\s*(.*?)\s*$")
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = pattern.match(line)
        if match:
            return match.group(1).strip().strip('"\'')
    return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(doc, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def recover(project: Path, flow_dir: Path, run_dir: Path | None = None) -> dict:
    project = project.resolve()
    flow_dir = flow_dir.resolve()
    config = project / "constraints" / "config.mk"
    if not config.is_file():
        raise RuntimeError(f"missing config: {config}")

    design = _config_value(config, "DESIGN_NAME")
    nickname = _config_value(config, "DESIGN_NICKNAME") or design
    platform = _config_value(config, "PLATFORM")
    variant = project.name
    if not design or not nickname or not platform:
        raise RuntimeError("config.mk must define DESIGN_NAME and PLATFORM")

    if run_dir is None:
        candidates = sorted((project / "backend").glob("RUN_*"), key=lambda p: p.stat().st_mtime)
        if len(candidates) != 1:
            raise RuntimeError(f"expected exactly one backend RUN_*, found {len(candidates)}; pass --run-dir")
        run_dir = candidates[0]
    run_dir = run_dir.resolve()
    if run_dir.parent != (project / "backend").resolve():
        raise RuntimeError(f"run directory is outside project backend: {run_dir}")

    meta_path = run_dir / "run-meta.json"
    stage_path = run_dir / "stage_log.jsonl"
    meta = json.loads(meta_path.read_text())
    if int(meta.get("make_status", -1)) != 0:
        raise RuntimeError("refusing recovery: run-meta make_status is not zero")
    rows = [json.loads(line) for line in stage_path.read_text().splitlines() if line.strip()]
    status = {row.get("stage"): row.get("status") for row in rows}
    bad = [stage for stage in REQUIRED_STAGES if status.get(stage) != 0]
    if bad:
        raise RuntimeError(f"refusing recovery: incomplete/failed stages: {', '.join(bad)}")

    roots = {
        "results": flow_dir / "results" / platform / nickname / variant,
        "logs": flow_dir / "logs" / platform / nickname / variant,
        "objects": flow_dir / "objects" / platform / nickname / variant,
        "reports_orfs": flow_dir / "reports" / platform / nickname / variant,
    }
    for name in FINAL_FILES:
        if not (roots["results"] / name).is_file():
            raise RuntimeError(f"refusing recovery: missing canonical source {roots['results'] / name}")

    for label, source in roots.items():
        if source.is_dir():
            shutil.copytree(source, run_dir / label, dirs_exist_ok=True)
    final_dir = run_dir / "final"
    final_dir.mkdir(exist_ok=True)
    for source in roots["results"].glob("6_final.*"):
        if source.is_file():
            shutil.copy2(source, final_dir / source.name)

    artifacts = {
        name: {"bytes": (run_dir / "results" / name).stat().st_size,
               "sha256": _sha256(run_dir / "results" / name)}
        for name in FINAL_FILES
    }
    recovery = {
        "schema_version": 1,
        "recovered_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "project": str(project),
        "run_tag": run_dir.name,
        "design_name": design,
        "design_nickname": nickname,
        "platform": platform,
        "flow_variant": variant,
        "source_roots": {key: str(value) for key, value in roots.items()},
        "validation": {"make_status": 0, "completed_stages": list(REQUIRED_STAGES)},
        "artifacts": artifacts,
    }
    _atomic_json(run_dir / "artifact-recovery.json", recovery)

    meta.update({
        "design_nickname": nickname,
        "orfs_results": str(roots["results"]),
        "orfs_logs": str(roots["logs"]),
        "orfs_objects": str(roots["objects"]),
        "orfs_reports": str(roots["reports_orfs"]),
        "artifact_recovery": "artifact-recovery.json",
    })
    _atomic_json(meta_path, meta)
    return recovery


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("--flow-dir", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path)
    args = parser.parse_args()
    result = recover(args.project, args.flow_dir, args.run_dir)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
