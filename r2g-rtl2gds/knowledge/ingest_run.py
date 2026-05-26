#!/usr/bin/env python3
"""Ingest one design_cases/<project> directory into knowledge/runs.sqlite.

Usage:
  ingest_run.py <project-dir>
  ingest_run.py <project-dir> --db <path>

Reads the structured JSON artifacts the flow already produces:
  constraints/config.mk
  reports/ppa.json
  reports/timing_check.json
  reports/drc.json
  reports/lvs.json
  reports/rcx.json
  reports/diagnosis.json
  backend/stage_log.jsonl

Nothing here parses raw ORFS logs — if an artifact is missing, the
corresponding column is left NULL. Idempotent: re-ingesting the same
completed run produces the same run_id.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

import knowledge_db


_CONFIG_LINE_RE = re.compile(r"(?:export\s+)?(\w+)\s*=\s*(.*)")


def _parse_config_mk(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8", errors="ignore").replace("\\\n", " ")
    fields: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _CONFIG_LINE_RE.match(line)
        if m:
            fields[m.group(1)] = m.group(2).strip()
    return fields


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _read_stage_log(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def _derive_orfs_status(stages: list[dict[str, Any]]) -> tuple[str, str | None]:
    if not stages:
        return ("unknown", None)
    saw_fail = False
    fail_stage = None
    last_stage_name = None
    stage_names_done = {s.get("stage") for s in stages if s.get("status") == "pass"}
    for s in stages:
        if s.get("status") not in ("pass", "fail"):
            continue
        last_stage_name = s.get("stage")
        if s.get("status") == "fail" and not saw_fail:
            saw_fail = True
            fail_stage = s.get("stage")
    if saw_fail:
        return ("fail", fail_stage)
    required = ["synth", "floorplan", "place", "cts", "route", "finish"]
    if all(name in stage_names_done for name in required):
        return ("pass", None)
    return ("partial", last_stage_name)


def _compute_run_id(project: Path, ppa_path: Path) -> str:
    marker = str(ppa_path.stat().st_mtime_ns) if ppa_path.exists() else ""
    h = hashlib.sha1()
    h.update(str(project.resolve()).encode("utf-8"))
    h.update(b":")
    h.update(marker.encode("utf-8"))
    return h.hexdigest()


def _to_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_int(v: Any) -> int | None:
    f = _to_float(v)
    return int(f) if f is not None else None


def _coerce_bool_int(s: str | None) -> int | None:
    if s is None:
        return None
    s = s.strip()
    if s in ("1", "true", "TRUE", "True", "yes"):
        return 1
    if s in ("0", "false", "FALSE", "False", "no", ""):
        return 0
    return None


def _record_lineage(conn: sqlite3.Connection, run_id: str,
                    design_name: str, platform: str,
                    cfg: dict[str, str], orfs_status: str) -> None:
    """If a previous run exists for this design/platform, record the config diff."""
    prev = conn.execute(
        "SELECT run_id, extra_config_json, core_utilization, "
        "place_density_lb_addon, synth_hierarchical, abc_area, die_area, "
        "clock_period_ns "
        "FROM runs "
        "WHERE design_name = ? AND platform = ? AND run_id != ? "
        "ORDER BY ingested_at DESC LIMIT 1",
        (design_name, platform, run_id),
    ).fetchone()
    if prev is None:
        return

    # Keys that are design identity, not tuning parameters — exclude from diff
    _IDENTITY_KEYS = {"DESIGN_NAME", "PLATFORM", "VERILOG_FILES", "SDC_FILE"}

    prev_run_id = prev[0]
    # Reconstruct previous config dict from stored columns
    prev_cfg: dict[str, str] = {}
    if prev[1]:  # extra_config_json
        try:
            extra = json.loads(prev[1])
            prev_cfg.update({k: v for k, v in extra.items()
                             if k not in _IDENTITY_KEYS})
        except (json.JSONDecodeError, TypeError):
            pass
    col_map = {
        "CORE_UTILIZATION": prev[2], "PLACE_DENSITY_LB_ADDON": prev[3],
        "SYNTH_HIERARCHICAL": prev[4], "ABC_AREA": prev[5],
        "DIE_AREA": prev[6], "CLOCK_PERIOD": prev[7],
    }
    for k, v in col_map.items():
        if v is not None:
            # Normalize float DB values so "30.0" matches raw config string "30"
            s = str(v)
            if s.endswith(".0") and s[:-2].lstrip("-").isdigit():
                s = s[:-2]
            prev_cfg[k] = s

    # Normalize current config values to strings for comparison.
    # Exclude identity keys — not tuning parameters, not stored in prev_cfg.
    cur_cfg = {k: str(v).strip() for k, v in cfg.items()
               if v and k not in _IDENTITY_KEYS}

    # Canonicalize numeric strings so "0.20" == "0.2" and "30" == "30.0".
    # This prevents spurious diffs caused by float DB round-trips.
    def _canon(s: str) -> str:
        try:
            f = float(s)
            # If it's a whole number, use integer string representation
            if f == int(f):
                return str(int(f))
            # Otherwise use repr to avoid trailing zeros (0.20 → 0.2)
            return str(f)
        except (ValueError, OverflowError):
            return s

    prev_cfg = {k: _canon(v) for k, v in prev_cfg.items()}
    cur_cfg = {k: _canon(v) for k, v in cur_cfg.items()}

    diff = knowledge_db.diff_config_rows(prev_cfg, cur_cfg)
    if not diff["changed"] and not diff["added"] and not diff["removed"]:
        return

    conn.execute(
        "INSERT INTO config_lineage "
        "(design_name, platform, current_run_id, previous_run_id, "
        " diff_json, current_outcome, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (design_name, platform, run_id, prev_run_id,
         json.dumps(diff, sort_keys=True), orfs_status,
         _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"),
    )


def ingest(project: Path,
           conn: sqlite3.Connection,
           families_path: Path | None = None) -> str:
    project = Path(project)
    if not project.is_dir():
        raise FileNotFoundError(f"Project directory not found: {project}")

    families_path = Path(families_path) if families_path else knowledge_db.DEFAULT_FAMILIES_PATH
    families = knowledge_db.load_families(families_path)

    cfg = _parse_config_mk(project / "constraints" / "config.mk")
    design_name = cfg.get("DESIGN_NAME", "unknown")
    design_family = knowledge_db.infer_family(design_name, families)
    platform = cfg.get("PLATFORM", "nangate45")

    ppa = _read_json(project / "reports" / "ppa.json") or {}
    summary = ppa.get("summary", {}) if isinstance(ppa, dict) else {}
    timing = summary.get("timing", {}) if isinstance(summary, dict) else {}
    power = summary.get("power", {}) if isinstance(summary, dict) else {}
    area = summary.get("area", {}) if isinstance(summary, dict) else {}
    geometry = ppa.get("geometry", {}) if isinstance(ppa, dict) else {}

    drc = _read_json(project / "reports" / "drc.json") or {}
    lvs = _read_json(project / "reports" / "lvs.json") or {}
    rcx = _read_json(project / "reports" / "rcx.json") or {}
    tcheck = _read_json(project / "reports" / "timing_check.json") or {}
    diag = _read_json(project / "reports" / "diagnosis.json") or {}
    # stage_log.jsonl lives inside backend/RUN_<timestamp>/.  Find the
    # most-recently-modified one, falling back to the legacy flat path.
    stage_log_path = project / "backend" / "stage_log.jsonl"
    run_dirs = sorted(
        (d for d in (project / "backend").iterdir()
         if d.is_dir() and d.name.startswith("RUN_")),
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    ) if (project / "backend").is_dir() else []
    for rd in run_dirs:
        candidate = rd / "stage_log.jsonl"
        if candidate.exists():
            stage_log_path = candidate
            break
    stage_log = _read_stage_log(stage_log_path)

    orfs_status, fail_stage = _derive_orfs_status(stage_log)
    total_elapsed = sum(_to_float(s.get("elapsed_s")) or 0.0 for s in stage_log) or None

    # Cell count: prefer geometry.instance_count (authoritative, from 6_report.json),
    # fall back to geometry.stdcell_count when instance_count is absent in partial runs.
    cell_count = _to_int(geometry.get("instance_count"))
    if cell_count is None:
        cell_count = _to_int(geometry.get("stdcell_count"))

    # Area: geometry.die_area_um2 is authoritative; area.design_area_um2 is a
    # placer-stage estimate used as fallback.
    area_um2 = _to_float(geometry.get("die_area_um2"))
    if area_um2 is None:
        area_um2 = _to_float(area.get("design_area_um2"))

    # Power: extract_ppa.py stores total_power_w in Watts; convert to mW.
    total_power_w = _to_float(power.get("total_power_w"))
    power_mw = total_power_w * 1000.0 if total_power_w is not None else None

    ppa_path = project / "reports" / "ppa.json"
    run_id = _compute_run_id(project, ppa_path)

    row = {
        "run_id":            run_id,
        "project_path":      str(project.resolve()),
        "design_name":       design_name,
        "design_family":     design_family,
        "platform":          platform,
        "ingested_at":       _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",

        "core_utilization":       _to_float(cfg.get("CORE_UTILIZATION")),
        "place_density_lb_addon": _to_float(cfg.get("PLACE_DENSITY_LB_ADDON")),
        "synth_hierarchical":     _coerce_bool_int(cfg.get("SYNTH_HIERARCHICAL")),
        "abc_area":               _coerce_bool_int(cfg.get("ABC_AREA")),
        "die_area":               cfg.get("DIE_AREA"),
        "clock_period_ns":        _to_float(cfg.get("CLOCK_PERIOD")),
        "extra_config_json":      json.dumps({
            k: v for k, v in cfg.items()
            if k not in {
                "DESIGN_NAME", "PLATFORM", "CORE_UTILIZATION",
                "PLACE_DENSITY_LB_ADDON", "SYNTH_HIERARCHICAL", "ABC_AREA",
                "DIE_AREA", "CLOCK_PERIOD",
            }
        }, sort_keys=True),

        "orfs_status":     orfs_status,
        "orfs_fail_stage": fail_stage,
        "wns_ns":          _to_float(timing.get("setup_wns")),
        "tns_ns":          _to_float(timing.get("setup_tns")),
        "timing_tier":     tcheck.get("tier"),
        "cell_count":      cell_count,
        "area_um2":        area_um2,
        "power_mw":        power_mw,
        "drc_status":      drc.get("status"),          # clean | fail | unknown
        "drc_violations":  _to_int(drc.get("total_violations")),
        "lvs_status":      lvs.get("status"),          # clean | fail | skipped | unknown
        "rcx_status":      rcx.get("status"),          # complete | empty | no_spef | skipped

        "total_elapsed_s":  total_elapsed,
        "stage_times_json": json.dumps(stage_log, sort_keys=True),
    }

    columns = list(row.keys())
    placeholders = ", ".join(f":{c}" for c in columns)
    conn.execute(
        f"INSERT OR REPLACE INTO runs ({', '.join(columns)}) VALUES ({placeholders})",
        row,
    )

    # Rebuild failure events for this run (idempotent).
    conn.execute("DELETE FROM failure_events WHERE run_id = ?", (run_id,))
    for issue in (diag.get("issues") or []):
        sig = (issue.get("kind") or "").strip()
        if not sig:
            continue
        conn.execute(
            "INSERT INTO failure_events (run_id, stage, signature, detail) "
            "VALUES (?, ?, ?, ?)",
            (run_id, issue.get("stage"), sig, issue.get("summary")),
        )
    if orfs_status == "fail" and fail_stage:
        conn.execute(
            "INSERT INTO failure_events (run_id, stage, signature, detail) "
            "VALUES (?, ?, ?, ?)",
            (run_id, fail_stage, f"orfs-fail-{fail_stage}", None),
        )
    _record_lineage(conn, run_id, design_name, platform, cfg, orfs_status)
    conn.commit()
    return run_id


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("project", type=Path, help="Path to design_cases/<project> directory")
    p.add_argument("--db", type=Path, default=knowledge_db.DEFAULT_DB_PATH,
                   help="SQLite database path (default: knowledge/runs.sqlite)")
    p.add_argument("--schema", type=Path, default=knowledge_db.DEFAULT_SCHEMA_PATH,
                   help="Schema SQL path")
    p.add_argument("--families", type=Path, default=knowledge_db.DEFAULT_FAMILIES_PATH,
                   help="families.json path")
    args = p.parse_args()

    conn = knowledge_db.connect(args.db)
    knowledge_db.ensure_schema(conn, schema_path=args.schema)
    run_id = ingest(args.project, conn, families_path=args.families)
    # Warn loudly if the run is about to be classified 'unknown' because
    # stage_log.jsonl is missing — this silently excludes runs from learning.
    status_row = conn.execute(
        "SELECT orfs_status FROM runs WHERE run_id = ?", (run_id,),
    ).fetchone()
    if status_row and status_row[0] == "unknown":
        print(
            f"WARNING: no backend/stage_log.jsonl under {args.project}; "
            "orfs_status='unknown'. This run will NOT contribute to "
            "learn_heuristics.py. Re-run via run_orfs.sh to emit stage_log.jsonl.",
            file=sys.stderr,
        )
    conn.close()
    print(f"Ingested run_id={run_id} from {args.project}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
