#!/usr/bin/env python3
"""Derive empirical per-family heuristics from runs.sqlite.

Usage:
  learn_heuristics.py [--db <path>] [--out <path>]

Writes knowledge/heuristics.json. Pure derivation — no network, no execution.
A family/platform pair is included only when at least MIN_SUCCESSFUL
successful runs exist. Every learned metric is derived from successful runs
only; failed runs still count toward ``sample_size`` and ``success_rate``
but contribute nothing to ``core_utilization``, ``place_density_lb_addon``,
``typical_cell_count`` or ``p90_elapsed_s``.

"Successful" is defined by ``knowledge_db.is_success`` — the single shared
predicate. It now ALSO admits signoff-positive ``partial`` runs (a run that
reached a final signed-off layout with clean DRC/LVS/RCX but whose
stage_log.jsonl was incomplete, so ingest left orfs_status != 'pass'). Absence
of all signoff data is still NOT a success. See ``knowledge_db.is_success``.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import statistics
import sys
from pathlib import Path

import knowledge_db

MIN_SUCCESSFUL = 3

# Single source of truth for "a learnable success" lives in knowledge_db.
# Thin alias for readability inside this module.
_is_success = knowledge_db.is_success


def _fetch_rows(conn) -> list[dict]:
    cur = conn.execute("SELECT * FROM runs")
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _p90(values: list[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    idx = max(0, int(round(0.9 * (len(s) - 1))))
    return s[idx]


def _family_platform_entry(runs: list[dict]) -> dict | None:
    successes = [r for r in runs if _is_success(r)]
    if len(successes) < MIN_SUCCESSFUL:
        return None

    cu_vals = [r["core_utilization"] for r in successes
               if r.get("core_utilization") is not None]
    pd_vals = [r["place_density_lb_addon"] for r in successes
               if r.get("place_density_lb_addon") is not None]
    cell_vals = [r["cell_count"] for r in successes
                 if r.get("cell_count") is not None]
    elapsed_vals = [r["total_elapsed_s"] for r in successes
                    if r.get("total_elapsed_s") is not None]

    entry: dict = {
        "sample_size": len(runs),
        "success_count": len(successes),
        "success_rate": len(successes) / len(runs),
    }
    if cu_vals:
        entry["core_utilization"] = {
            "min_safe": min(cu_vals),
            "max_safe": max(cu_vals),
            "median": statistics.median(cu_vals),
        }
    if pd_vals:
        entry["place_density_lb_addon"] = {
            "min_safe": min(pd_vals),
            "max_safe": max(pd_vals),
            "median": statistics.median(pd_vals),
        }
    if cell_vals:
        entry["typical_cell_count"] = int(statistics.median(cell_vals))
    if elapsed_vals:
        entry["p90_elapsed_s"] = _p90(elapsed_vals)
    return entry


def _build_trajectory(events: list[dict]) -> dict:
    """Collapse one episode's fix_events (sorted by iter) into a trajectory row."""
    events = sorted(events, key=lambda e: (e.get("iter") or 0))
    first = events[0]
    path = [{"iter": e.get("iter"), "strategy": e.get("strategy"),
             "before": e.get("before_count"), "after": e.get("after_count"),
             "verdict": e.get("verdict")} for e in events]
    win = next((e for e in events if e.get("verdict") == "cleared"), None)
    failed = sorted({e.get("strategy") for e in events
                     if e.get("verdict") in ("no_change", "regression")
                     and e.get("strategy")})
    return {
        "fix_session_id": first.get("fix_session_id"),
        "project_path": first.get("project_path"),
        "design_name": first.get("design_name"),
        "design_family": first.get("design_family"),
        "platform": first.get("platform"),
        "check_type": first.get("check_type"),
        "violation_class": first.get("violation_class"),
        "path_json": json.dumps(path),
        "n_iters": len(events),
        "outcome": "resolved" if win else "abandoned",
        "winning_strategy": win.get("strategy") if win else None,
        "winning_config_json": win.get("cumulative_config_json") if win else None,
        "failed_strategies_json": json.dumps(failed),
        "initial_count": first.get("before_count"),
        "final_count": events[-1].get("after_count"),
        "total_elapsed_s": sum(e.get("elapsed_s") or 0.0 for e in events) or None,
    }


def _rebuild_fix_trajectories(conn) -> list[dict]:
    """Re-derive Tier-2 from Tier-1 (full rebuild — idempotent)."""
    cur = conn.execute("SELECT * FROM fix_events")
    cols = [c[0] for c in cur.description]
    events = [dict(zip(cols, r)) for r in cur.fetchall()]
    by_session: dict[str, list[dict]] = {}
    for e in events:
        by_session.setdefault(e["fix_session_id"], []).append(e)
    trajectories = [_build_trajectory(evs) for evs in by_session.values()]
    conn.execute("DELETE FROM fix_trajectories")
    for t in trajectories:
        keys = list(t.keys())
        ph = ", ".join(f":{k}" for k in keys)
        conn.execute(f"INSERT INTO fix_trajectories ({', '.join(keys)}) VALUES ({ph})", t)
    conn.commit()
    return events


def _fix_recipes_for_group(events: list[dict]) -> dict:
    """Tier-3 aggregate for one (family, platform): nested by check_type ->
    violation_class -> {strategies: {sid: {attempts,successes,failures,
    median_reduction_pct}}, n_sessions}."""
    out: dict = {}
    # group events by (check, vclass)
    buckets: dict[tuple, list[dict]] = {}
    for e in events:
        buckets.setdefault((e.get("check_type"), e.get("violation_class")), []).append(e)
    for (check, vclass), evs in buckets.items():
        if not check:
            continue
        strategies: dict[str, dict] = {}
        reductions: dict[str, list[float]] = {}
        for e in evs:
            sid = e.get("strategy")
            if not sid or sid == "none":
                continue
            s = strategies.setdefault(sid, {"attempts": 0, "successes": 0, "failures": 0})
            s["attempts"] += 1
            if e.get("verdict") == "cleared":
                s["successes"] += 1
            elif e.get("verdict") in ("no_change", "regression"):
                s["failures"] += 1
            bc, ac = e.get("before_count"), e.get("after_count")
            if bc and ac is not None and bc > 0:
                reductions.setdefault(sid, []).append((bc - ac) / bc)
        for sid, red in reductions.items():
            strategies[sid]["median_reduction_pct"] = statistics.median(red)
        out.setdefault(check, {})[vclass] = {
            "strategies": strategies,
            "n_sessions": len({e.get("fix_session_id") for e in evs}),
        }
    return out


def learn(db_path: Path | str,
          out_path: Path | str) -> dict:
    db_path = Path(db_path)
    out_path = Path(out_path)

    conn = knowledge_db.connect(db_path)
    try:
        # Idempotent: guarantees fix_events / fix_trajectories exist before we
        # SELECT / DELETE / INSERT on them, even on legacy DBs predating Task 1.
        knowledge_db.ensure_schema(conn)
        rows = _fetch_rows(conn)
        fix_events = _rebuild_fix_trajectories(conn)   # Tier-2 (idempotent rebuild)

        groups: dict[tuple[str, str], list[dict]] = {}
        for r in rows:
            fam = r.get("design_family") or "unknown"
            plat = r.get("platform") or "unknown"
            groups.setdefault((fam, plat), []).append(r)

        fix_groups: dict[tuple[str, str], list[dict]] = {}
        for e in fix_events:
            fam = e.get("design_family") or "unknown"
            plat = e.get("platform") or "unknown"
            fix_groups.setdefault((fam, plat), []).append(e)

        families: dict[str, dict] = {}
        for (fam, plat), group_rows in groups.items():
            entry = _family_platform_entry(group_rows)
            if entry is None:
                continue
            fam_obj = families.setdefault(fam, {"platforms": {}})
            fam_obj["platforms"][plat] = entry

        # Fold Tier-3 fix_recipes into existing entries, and create entries for
        # families that have fix history but no signoff-success run yet.
        for (fam, plat), evs in fix_groups.items():
            recipes = _fix_recipes_for_group(evs)
            if not recipes:
                continue
            fam_obj = families.setdefault(fam, {"platforms": {}})
            entry = fam_obj["platforms"].setdefault(plat, {"sample_size": 0,
                                                           "success_count": 0,
                                                           "success_rate": 0.0})
            entry["fix_recipes"] = recipes
    finally:
        conn.close()

    data = {
        "generated_at": _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "source_run_count": len(rows),
        "min_successful_runs_required": MIN_SUCCESSFUL,
        "families": families,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--db", type=Path, default=knowledge_db.DEFAULT_DB_PATH)
    p.add_argument("--out", type=Path,
                   default=knowledge_db.DEFAULT_KNOWLEDGE_DIR / "heuristics.json")
    args = p.parse_args()
    data = learn(args.db, args.out)
    total = sum(len(f["platforms"]) for f in data["families"].values())
    print(f"Wrote {args.out} ({len(data['families'])} families, "
          f"{total} family/platform entries, {data['source_run_count']} runs).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
