#!/usr/bin/env python3
"""Autonomous fix-log manager (spec 2026-06-05 §5.5). PURE helpers here:
config-normalized merge key (D11) + detail-blob bounding (D13). Stateful
manage()/archive routines are added in the learn task once learn_heuristics
exists. Model/ranking logic stays in fix_model.py.
"""
from __future__ import annotations
import json
import math

CONFIG_TOL = 0.15            # ±15% numeric tolerance for "same action"
RULE_DETAIL_TOP_N = 20       # cap verbose per-violation detail (D13)
FIX_EVENTS_MAX_ROWS = 50000  # archive trigger (D13)
DB_MAX_MB = 200


def _bucket(val, tol: float) -> str:
    """Bucket a numeric value into a log-spaced band at relative tolerance `tol`,
    so near-equal values collapse; non-numeric values pass through verbatim."""
    try:
        f = float(val)
    except (TypeError, ValueError):
        return str(val)
    if f == 0:
        return "z"
    # Snap to the nearest log-spaced band center (round, not floor): floor lands
    # band edges between near-equal values, so two values within `tol` could
    # straddle a boundary and fail to collapse. round keeps within-tol values
    # in the same band while staying coarse enough to separate far-apart ones.
    return ("+" if f > 0 else "-") + str(int(round(math.log(abs(f)) / math.log(1.0 + tol))))


def canonical_action_key(event: dict, tol: float = CONFIG_TOL) -> tuple:
    """Config-normalized merge key (check, violation_class, strategy, config-sig).
    Same knob within `tol` collapses; distinct knobs/values stay separate."""
    raw = event.get("cumulative_config_json") or event.get("config_delta_json") or "{}"
    try:
        d = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (TypeError, ValueError):
        d = {}
    sig = tuple(sorted((k, _bucket(v, tol)) for k, v in d.items()))
    return (event.get("check_type"), event.get("violation_class"),
            event.get("strategy"), sig)


def dedup_events_by_action(events: list[dict], tol: float = CONFIG_TOL) -> list[dict]:
    """Collapse identical canonical actions within an episode to the LAST (freshest)
    occurrence so retries don't inflate counts. Ordered by iter."""
    ordered = sorted(events, key=lambda e: (e.get("iter") or 0))
    collapsed: dict[tuple, dict] = {}
    for e in ordered:
        collapsed[canonical_action_key(e, tol)] = e   # last wins
    return sorted(collapsed.values(), key=lambda e: (e.get("iter") or 0))


def bound_rule_details(details, top_n: int = RULE_DETAIL_TOP_N):
    """Cap a verbose detail blob: top_n sample entries + a total count (D13)."""
    if details is None:
        return None
    items = (details["samples"] if isinstance(details, dict) and "samples" in details
             else details if isinstance(details, list) else None)
    if items is None:
        return details
    return {"total": len(items), "samples": list(items)[:top_n], "truncated": len(items) > top_n}


def db_size_mb(db_path) -> float:
    import os
    return os.path.getsize(db_path) / (1024 * 1024) if os.path.exists(db_path) else 0.0


def _archive_db_path(db_path):
    from pathlib import Path
    return Path(db_path).with_name("fix_events_archive.sqlite")


def archive_old_raw(db_path, *, max_rows=None, max_mb=None) -> int:
    """Move raw fix_events of fully-merged episodes (those with a fix_trajectory),
    oldest first, into the sidecar archive DB when the hot DB exceeds a threshold.
    Trajectories are never moved, so recipes are unaffected. Returns rows archived."""
    import knowledge_db
    max_rows = FIX_EVENTS_MAX_ROWS if max_rows is None else max_rows
    max_mb = DB_MAX_MB if max_mb is None else max_mb
    conn = knowledge_db.connect(db_path)
    try:
        n = conn.execute("SELECT COUNT(*) FROM fix_events").fetchone()[0]
        if n <= max_rows and db_size_mb(db_path) <= max_mb:
            return 0
        arch = _archive_db_path(db_path)
        ac = knowledge_db.connect(arch); knowledge_db.ensure_schema(ac); ac.close()
        conn.execute("ATTACH DATABASE ? AS arch", (str(arch),))
        sessions = [r[0] for r in conn.execute(
            "SELECT e.fix_session_id FROM fix_events e "
            "JOIN fix_trajectories t USING(fix_session_id) "
            "GROUP BY e.fix_session_id ORDER BY MIN(e.ts)")]
        archived = 0
        for sid in sessions:
            if (conn.execute("SELECT COUNT(*) FROM fix_events").fetchone()[0] <= max_rows
                    and db_size_mb(db_path) <= max_mb):
                break
            conn.execute("INSERT INTO arch.fix_events_archive "
                         "SELECT * FROM fix_events WHERE fix_session_id = ?", (sid,))
            archived += conn.execute(
                "DELETE FROM fix_events WHERE fix_session_id = ?", (sid,)).rowcount
        conn.commit()
        conn.execute("DETACH DATABASE arch")
    finally:
        conn.close()
    # VACUUM needs its own autocommit connection (cannot run inside a txn).
    vac = knowledge_db.connect(db_path); vac.isolation_level = None
    vac.execute("VACUUM"); vac.close()
    return archived


def manage(db_path, *, out_path=None, autolearn=True) -> dict:
    """Autonomous post-ingest step: re-derive Tier-2/Tier-3 (learn) then enforce the
    size policy. Returns {rows, archived, db_mb}."""
    import knowledge_db
    if out_path is None:
        out_path = knowledge_db.DEFAULT_KNOWLEDGE_DIR / "heuristics.json"
    if autolearn:
        import learn_heuristics
        learn_heuristics.learn(db_path, out_path)   # builds trajectories+recipes FIRST
    archived = archive_old_raw(db_path)             # safe: trajectories already built
    conn = knowledge_db.connect(db_path)
    try:
        rows = conn.execute("SELECT COUNT(*) FROM fix_events").fetchone()[0]
    finally:
        conn.close()
    return {"rows": rows, "archived": archived, "db_mb": round(db_size_mb(db_path), 2)}
