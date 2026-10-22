"""ann-v2 row-field provenance and rollback evidence (spec §2, §5; user decision D2, 2026-10-08).

Raw evidence only. A field recorded in the action's fix-log row is KNOWN only when the
row ON DISK (the file the unit's provenance names) carries that key with the exported
value; a value present only in the SOURCE-v1 export copy is UNKNOWN `export_only`.

`rolled_back` is KNOWN from exactly two raw sources:
  1. the on-disk row carries `rolled_back` (writer >= e57acd03, 2026-10-08);
  2. `reports/global_regression_it<N>.json`, written at the regression-revert site, bound
     to the unit by iteration, strategy and the pre-action run tag (`pre.layout.run_tag`).
Legacy rows without a marker are UNKNOWN, never "not rolled back" (D2). The export's
enriched `rolled_back` (ann-v1 "rollback marker semantics") is never read.
"""
from __future__ import annotations

import json
from pathlib import Path

from ann_v2 import fields as FD


def _jsonl_line(path: Path, line: int):
    try:
        lines = path.read_text().splitlines()
        return json.loads(lines[line - 1]) if 0 < line <= len(lines) else None
    except (OSError, ValueError, TypeError):
        return None


def _json_item(path: Path, item: int):
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if isinstance(data, list):
        return data[item - 1] if 0 < item <= len(data) else None
    return data if item in (None, 1) else None


def on_disk_rows(u: dict) -> list:
    """[(row, path, locator_prefix)] for every provenance source whose on-disk row is the
    unit's own (same fix_session_id and iter)."""
    raw = u.get("raw_row") or {}
    key = (raw.get("fix_session_id"), int(raw.get("iter") or 1), raw.get("strategy"), raw.get("check"))
    out = []
    for s in (u.get("provenance") or {}).get("sources") or []:
        path, line = Path(s.get("path") or ""), s.get("line")
        if not line:
            continue
        if path.name.endswith(".jsonl"):
            row, prefix = _jsonl_line(path, int(line)), f"line {line}"
        elif path.suffix == ".json":
            row, prefix = _json_item(path, int(line)), f"item {line}"
        else:
            continue
        if isinstance(row, dict) and (row.get("fix_session_id"), int(row.get("iter") or 1), row.get("strategy"),
                                      row.get("check")) == key:
            out.append((row, path, prefix))
    return out


def _get(row: dict, key_path: str):
    cur = row
    for k in key_path.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return False, None
        cur = cur[k]
    return True, cur


def row_field(u: dict, key_path: str, method: str) -> dict:
    """Field record for a row-recorded value, KNOWN only from the on-disk row."""
    rows = on_disk_rows(u)
    if not rows:
        return FD.unknown("row_missing_on_disk", method)
    found = [(v, p, pre) for r, p, pre in rows for ok, v in [_get(r, key_path)] if ok]
    if not found:
        ok, _ = _get(u.get("raw_row") or {}, key_path)
        return FD.unknown("export_only" if ok else "not_recorded", method)
    if len({json.dumps(v, sort_keys=True) for v, _, _ in found}) > 1:
        return FD.unknown("copy_conflict", method, [FD.src(p, f"{pre}: {key_path}") for _, p, pre in found])
    v, p, pre = found[0]
    exported_ok, exported = _get(u.get("raw_row") or {}, key_path)
    if exported_ok and json.dumps(exported, sort_keys=True) != json.dumps(v, sort_keys=True):
        return FD.unknown("conflicting_sources", method, [FD.src(p, f"{pre}: {key_path}")])
    return FD.known(v, [FD.src(p, f"{pre}: {key_path}") for _, p, pre in found], method)


def regression_files(projects, iteration: int) -> list:
    out = []
    for p in projects:
        f = Path(p) / "reports" / f"global_regression_it{iteration}.json"
        d = FD._json(f)
        if isinstance(d, dict):
            out.append((d, f))
    return out


def rollback_state(projects, row: dict | None, pre_run_tag: str | None = None) -> tuple:
    """(value, source, reason): True only from the row marker or a regression snapshot OWNED
    by this row (snapshot.load_owned: unique owner row, no copy conflict); False only from
    the row marker; None otherwise. A snapshot never shows "not rolled back"."""
    if isinstance(row, dict) and isinstance(row.get("rolled_back"), bool):
        return row["rolled_back"], None, None
    if not isinstance(row, dict):
        return None, None, "row_missing_on_disk"
    from ann_v2 import snapshot as SN
    own = SN.load_owned(row, projects)
    if own["status"] == "OWNED":
        return True, [FD.src(Path(s["path"]), s["locator"]) for s in own["sources"]], None
    return None, ([FD.src(Path(s["path"]), s["locator"]) for s in own["sources"]] or None), own["reason"]


def raw_rollback(u: dict, projects, pre_run_tag: str | None) -> dict:
    rows = on_disk_rows(u)
    if not rows:
        return FD.unknown("row_missing_on_disk", "fix_log_row")
    with_key = [(r, p, pre) for r, p, pre in rows if isinstance(r.get("rolled_back"), bool)]
    if with_key:
        if len({r["rolled_back"] for r, _, _ in with_key}) > 1:
            return FD.unknown("copy_conflict", "fix_log_row", [FD.src(p, f"{pre}: rolled_back") for _, p, pre in with_key])
        return FD.known(with_key[0][0]["rolled_back"], [FD.src(p, f"{pre}: rolled_back") for _, p, pre in with_key],
                        "fix_log_row")
    value, sources, reason = rollback_state(projects, rows[0][0], pre_run_tag)
    if value is True:
        return FD.known(True, sources, "global_regression_file")
    if reason == "not_recorded" and "rolled_back" in (u.get("raw_row") or {}):
        return FD.unknown("export_only", "fix_log_row")          # export enrichment, never read
    return FD.unknown(reason or "not_recorded", "fix_log_row" if not sources else "global_regression_file", sources)
