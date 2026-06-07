#!/usr/bin/env python3
"""Read-only observability projection over the knowledge store.

Surfaces the otherwise write-only ``config_lineage`` table, the ``runs.sqlite``
outcome distribution, and learned-heuristics health as a deterministic data
dict, for rendering in the static dashboard.

STRICTLY DESCRIPTIVE / READ-ONLY:
  * The DB is opened ``mode=ro`` (uri); this module NEVER writes to it.
  * It only ever writes JSON (the projection) to --out or stdout.
  * It is NEVER wired into suggest_config / config recommendation. It is the
    diagnostic that would have screamed "747/750 partial, heuristics empty".

The learnable-pairs count reuses ``knowledge_db.is_success`` — the single shared
success predicate — so the health strip and the learner can never disagree.

The config-variant lineage is a LOOSE single-parent diff chain (one
previous_run_id per edge), not a true DAG. The walk is best-effort and never
infinite-loops (guarded by a visited set); it falls back to created_at ordering
when the chain branches or cycles.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sqlite3
import sys
from pathlib import Path

# Make knowledge/ importable as a plain module (mirrors tests/conftest.py, but a
# standalone script must add the path itself).
_KNOWLEDGE_DIR = Path(__file__).resolve().parents[2] / "knowledge"
if str(_KNOWLEDGE_DIR) not in sys.path:
    sys.path.insert(0, str(_KNOWLEDGE_DIR))
import knowledge_db  # noqa: E402
import learn_heuristics  # noqa: E402  (authoritative MIN_SUCCESSFUL threshold)

DEFAULT_HEURISTICS_PATH = _KNOWLEDGE_DIR / "heuristics.json"

# Outcome fields surfaced per run in a lineage edge's outcome_delta.
_OUTCOME_FIELDS = ("orfs_status", "drc_status", "lvs_status", "timing_tier")


def _connect_ro(db_path: Path | str) -> sqlite3.Connection:
    """Open the DB strictly read-only. Never mutates the store."""
    conn = sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _load_heuristics(heuristics_path: Path | str | None) -> dict:
    """Load heuristics.json; never raise on missing/malformed file."""
    if heuristics_path is None:
        heuristics_path = DEFAULT_HEURISTICS_PATH
    path = Path(heuristics_path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _build_health(rows: list[dict], heuristics: dict) -> dict:
    total = len(rows)

    status_counts: dict[str, int] = {}
    for r in rows:
        status = r.get("orfs_status") or "unknown"
        status_counts[status] = status_counts.get(status, 0) + 1
    # Deterministic key order.
    orfs_status_counts = {k: status_counts[k] for k in sorted(status_counts)}

    partial_or_unknown = sum(
        c for s, c in status_counts.items() if s in ("partial", "unknown")
    )
    pct_partial_or_unknown = (
        round(100.0 * partial_or_unknown / total, 1) if total else 0.0
    )

    def _count(pred) -> int:
        return sum(1 for r in rows if pred(r))

    signoff_positive = {
        "lvs_clean": _count(lambda r: r.get("lvs_status") == "clean"),
        "drc_clean": _count(lambda r: r.get("drc_status") == "clean"),
        "drc_clean_beol": _count(lambda r: r.get("drc_status") == "clean_beol"),
        "rcx_complete": _count(lambda r: r.get("rcx_status") == "complete"),
    }

    # Learnable (design_family, platform) groups with >= 3 successful rows.
    # Reuse knowledge_db.is_success so this agrees with the learner exactly.
    groups: dict[tuple[str, str], int] = {}
    for r in rows:
        fam = r.get("design_family") or "unknown"
        plat = r.get("platform") or "unknown"
        key = (fam, plat)
        if knowledge_db.is_success(r):
            groups[key] = groups.get(key, 0) + 1
        else:
            groups.setdefault(key, 0)
    # Threshold sourced from the learner's own constant so the health strip and
    # learn_heuristics can never disagree on what "learnable" means.
    learnable_pairs = sum(1 for c in groups.values()
                          if c >= learn_heuristics.MIN_SUCCESSFUL)

    families = heuristics.get("families") if isinstance(heuristics, dict) else None
    if not isinstance(families, dict):
        families = {}
    heuristics_family_count = len(families)
    min_successful_required = heuristics.get("min_successful_runs_required", 3) \
        if isinstance(heuristics, dict) else 3
    try:
        min_successful_required = int(min_successful_required)
    except (TypeError, ValueError):
        min_successful_required = 3

    return {
        "total_runs": total,
        "orfs_status_counts": orfs_status_counts,
        "pct_partial_or_unknown": pct_partial_or_unknown,
        "signoff_positive": signoff_positive,
        "learnable_pairs": learnable_pairs,
        "heuristics_populated": bool(families),
        "heuristics_family_count": heuristics_family_count,
        "min_successful_required": min_successful_required,
    }


def _parse_diff(diff_json: str | None) -> dict:
    if not diff_json:
        return {"changed": {}, "added": {}, "removed": {}}
    try:
        data = json.loads(diff_json)
    except Exception:
        return {"changed": {}, "added": {}, "removed": {}}
    if not isinstance(data, dict):
        return {"changed": {}, "added": {}, "removed": {}}
    return {
        "changed": data.get("changed", {}) or {},
        "added": data.get("added", {}) or {},
        "removed": data.get("removed", {}) or {},
    }


def _order_edges(edges: list[dict]) -> list[dict]:
    """Order edges by following the previous->current chain from roots.

    A LOOSE single-parent chain: build adjacency previous_run_id -> [edges],
    find roots (a current whose previous is not itself any current in this
    group), and walk. Guard with a visited set so a branch/cycle never loops
    forever; any edge not reached by the walk is appended in the deterministic
    fallback order (created_at, then lineage row id).
    """
    fallback = sorted(
        edges, key=lambda e: (e["created_at"] or "", e["_lineage_id"])
    )
    currents = {e["current_run_id"] for e in edges}
    by_prev: dict[str, list[dict]] = {}
    for e in fallback:  # deterministic insertion order for adjacency
        by_prev.setdefault(e["previous_run_id"], []).append(e)

    # Roots: edges whose previous_run_id is not produced as a current in-group.
    roots = [e for e in fallback if e["previous_run_id"] not in currents]

    ordered: list[dict] = []
    visited: set[int] = set()
    # Walk each root chain; deterministic because `fallback` is sorted and
    # by_prev preserves that order.
    for root in roots:
        stack = [root]
        while stack:
            edge = stack.pop(0)
            lid = edge["_lineage_id"]
            if lid in visited:
                continue
            visited.add(lid)
            ordered.append(edge)
            # Continue the chain from this edge's current node.
            children = by_prev.get(edge["current_run_id"], [])
            # Prepend children so we depth-walk the linear chain in order.
            stack = list(children) + stack

    # Append any edge not reached (branch/cycle remnants) in fallback order.
    for e in fallback:
        if e["_lineage_id"] not in visited:
            visited.add(e["_lineage_id"])
            ordered.append(e)
    return ordered


def _build_provenance(conn: sqlite3.Connection) -> list[dict]:
    # Map run_id -> outcome fields (None if a run row is absent).
    run_outcomes: dict[str, dict] = {}
    cur = conn.execute(
        "SELECT run_id, orfs_status, drc_status, lvs_status, timing_tier FROM runs"
    )
    for row in cur.fetchall():
        run_outcomes[row["run_id"]] = {
            f: row[f] for f in _OUTCOME_FIELDS
        }

    def _outcomes(run_id: str | None) -> dict:
        return run_outcomes.get(run_id, {f: None for f in _OUTCOME_FIELDS})

    # Group lineage edges by (design_name, platform).
    cur = conn.execute(
        "SELECT id, design_name, platform, current_run_id, previous_run_id, "
        "diff_json, created_at FROM config_lineage"
    )
    groups: dict[tuple[str, str], list[dict]] = {}
    for row in cur.fetchall():
        key = (row["design_name"], row["platform"])
        groups.setdefault(key, []).append({
            "_lineage_id": row["id"],
            "current_run_id": row["current_run_id"],
            "previous_run_id": row["previous_run_id"],
            "diff_json": row["diff_json"],
            "created_at": row["created_at"],
        })

    provenance: list[dict] = []
    for (design_name, platform) in sorted(groups):
        raw_edges = groups[(design_name, platform)]
        ordered = _order_edges(raw_edges)
        edges = []
        for e in ordered:
            prev = _outcomes(e["previous_run_id"])
            cur_o = _outcomes(e["current_run_id"])
            edges.append({
                "previous_run_id": e["previous_run_id"],
                "current_run_id": e["current_run_id"],
                "diff": _parse_diff(e["diff_json"]),
                "outcome_delta": {
                    f: [prev[f], cur_o[f]] for f in _OUTCOME_FIELDS
                },
            })
        provenance.append({
            "design_name": design_name,
            "platform": platform,
            "edge_count": len(edges),
            "edges": edges,
        })
    return provenance


def _fix_effectiveness(conn: sqlite3.Connection) -> list[dict]:
    """Per-(family, platform, check, violation_class) fix-strategy effectiveness.

    Rolls up ``fix_trajectories`` per
    (design_family, platform, check_type, violation_class, strategy). Resolved
    (successes) and abandoned (failures) are attributed PER STRATEGY by parsing
    each trajectory's path_json verdicts — exactly like
    learn_heuristics._recipes_from_trajectories — NOT by the episode-level
    ``winning_strategy`` column. That column is NULL for every abandoned episode,
    so a GROUP BY winning_strategy would collapse all failures into a phantom
    strategy=NULL bucket and make every named strategy report abandoned=0 /
    clearance_rate=1.0. The clearance rate is successes / (successes + failures).

    READ-ONLY safety: ``mode=ro`` cannot CREATE tables, so if a (legacy) DB lacks
    the ``fix_trajectories`` table this MUST NOT crash. We probe sqlite_master
    first and return an empty projection when the table is absent.
    """
    has_table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='fix_trajectories'"
    ).fetchone()
    if not has_table:
        return []

    # ORDER BY keeps the projection deterministic regardless of insert order.
    cur = conn.execute(
        "SELECT design_family, platform, check_type, violation_class, path_json "
        "FROM fix_trajectories "
        "ORDER BY design_family, platform, check_type, violation_class, "
        "         fix_session_id"
    )

    # (family, platform, check, class) -> strategy -> {resolved, abandoned}.
    groups: dict[tuple, dict] = {}
    for row in cur.fetchall():
        key = (row["design_family"], row["platform"], row["check_type"],
               row["violation_class"])
        group = groups.get(key)
        if group is None:
            group = {
                "design_family": row["design_family"],
                "platform": row["platform"],
                "check_type": row["check_type"],
                "violation_class": row["violation_class"],
                "_strategies": {},   # strategy -> {resolved, abandoned}
            }
            groups[key] = group
        try:
            steps = json.loads(row["path_json"] or "[]")
        except (TypeError, ValueError):
            steps = []
        for step in steps:
            sid = step.get("strategy")
            if not sid or sid == "none":
                continue
            verdict = step.get("verdict")
            if verdict not in ("cleared", "win", "no_change", "regression"):
                continue
            tally = group["_strategies"].setdefault(
                sid, {"resolved": 0, "abandoned": 0}
            )
            if verdict in ("cleared", "win"):
                tally["resolved"] += 1
            else:
                tally["abandoned"] += 1

    result: list[dict] = []
    for group in groups.values():
        strat_tallies = group.pop("_strategies")
        strategies = []
        # Deterministic strategy order (None never appears now, but sort safely).
        for sid in sorted(strat_tallies, key=lambda s: ("" if s is None else s)):
            tally = strat_tallies[sid]
            resolved = tally["resolved"]
            abandoned = tally["abandoned"]
            attempts = resolved + abandoned
            clearance_rate = round(resolved / attempts, 4) if attempts else 0.0
            strategies.append({
                "strategy": sid,
                "resolved": resolved,
                "abandoned": abandoned,
                "attempts": attempts,
                "clearance_rate": clearance_rate,
            })
        group["strategies"] = strategies
        result.append(group)
    return result


def build_view(db_path: Path | str, heuristics_path: Path | str | None = None) -> dict:
    """Pure, deterministic read-only projection over runs.sqlite + heuristics.json.

    Returns exactly {"health": {...}, "provenance": [...],
    "fix_effectiveness": [...]}. No timestamp — the CLI stamps generated_at
    separately so this stays golden-testable.
    """
    heuristics = _load_heuristics(heuristics_path)
    conn = _connect_ro(db_path)
    try:
        cur = conn.execute("SELECT * FROM runs")
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        health = _build_health(rows, heuristics)
        provenance = _build_provenance(conn)
        fix_effectiveness = _fix_effectiveness(conn)
    finally:
        conn.close()
    return {
        "health": health,
        "provenance": provenance,
        "fix_effectiveness": fix_effectiveness,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--db", type=Path, default=knowledge_db.DEFAULT_DB_PATH)
    p.add_argument("--heuristics", type=Path, default=DEFAULT_HEURISTICS_PATH)
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args()

    view = build_view(args.db, args.heuristics)
    # The file gets a generated_at for provenance; build_view itself stays pure.
    out_obj = dict(view)
    out_obj["generated_at"] = _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    text = json.dumps(out_obj, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"Wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
