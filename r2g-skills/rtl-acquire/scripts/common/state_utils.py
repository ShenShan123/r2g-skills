#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from common.io_utils import now_iso, write_json


RECENT_DESIGN_LOG_COUNT = 3
RECENT_SUMMARY_LINE_COUNT = 80
# ORFS work paths are results/<platform>/<DESIGN_NAME>/<FLOW_VARIANT>/…; the
# candidate's unique id is the FLOW_VARIANT (run_orfs.sh convergence 2026-07-09
# — DESIGN_NAME is the top module and may collide across candidates).
DESIGN_PATH_RE = re.compile(r"(?:results|logs|objects)/[^/\s]+/[^/\s]+/([^/\s]+)/")


def pid_is_alive(pid: int | str | None) -> bool:
    try:
        pid_int = int(pid)
    except Exception:
        return False
    if pid_int <= 0:
        return False
    try:
        os.kill(pid_int, 0)
    except OSError:
        return False
    return True


def compact_status_log(
    log_path: Path,
    *,
    keep_designs: int = RECENT_DESIGN_LOG_COUNT,
    keep_summary_lines: int = RECENT_SUMMARY_LINE_COUNT,
) -> None:
    if not log_path.exists():
        return

    lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    if not lines:
        return

    summary_lines: list[str] = []
    design_blocks: list[tuple[str, list[str]]] = []
    current_design: str | None = None
    current_block: list[str] = []

    def flush_block() -> None:
        nonlocal current_design, current_block
        if current_design is not None and current_block:
            design_blocks.append((current_design, current_block[:]))
        current_design = None
        current_block = []

    for line in lines:
        match = DESIGN_PATH_RE.search(line)
        design = match.group(1) if match else None

        if line.startswith("[") and " CMD " in line and current_design is not None:
            flush_block()
            summary_lines.append(line)
            continue

        if design:
            if current_design != design:
                flush_block()
                current_design = design
                current_block = [line]
            else:
                current_block.append(line)
            continue

        if current_design is not None:
            current_block.append(line)
        else:
            summary_lines.append(line)

    flush_block()

    kept_blocks = design_blocks[-keep_designs:]
    kept_summary = summary_lines[-keep_summary_lines:]

    out_lines: list[str] = []
    if kept_summary:
        out_lines.append("# Recent Expansion Summary")
        out_lines.extend(kept_summary)

    for design_name, block_lines in kept_blocks:
        if out_lines:
            out_lines.append("")
        out_lines.append(f"===== DESIGN {design_name} =====")
        out_lines.extend(block_lines)

    log_path.write_text("\n".join(out_lines).rstrip() + "\n", encoding="utf-8")


def refresh_design_stage_index(out_root: Path) -> Path | None:
    status_root = out_root / "_design_status"
    if not status_root.exists():
        return None

    design_rows: list[dict] = []
    stage_counts: dict[str, int] = {}
    state_counts: dict[str, int] = {}

    for path in sorted(status_root.glob("*.json")):
        if path.name == "design_stage_index.json":
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        design_rows.append(payload)
        stage = str(payload.get("stage", ""))
        state = str(payload.get("state", ""))
        if stage:
            stage_counts[stage] = stage_counts.get(stage, 0) + 1
        if state:
            state_counts[state] = state_counts.get(state, 0) + 1

    design_rows.sort(key=lambda row: (str(row.get("stage", "")), str(row.get("design", ""))))
    out_path = status_root / "design_stage_index.json"
    write_json(
        out_path,
        {
            "updated_at": now_iso(),
            "out_root": str(out_root),
            "design_count": len(design_rows),
            "stage_counts": stage_counts,
            "state_counts": state_counts,
            "designs": design_rows,
        },
    )
    return out_path
