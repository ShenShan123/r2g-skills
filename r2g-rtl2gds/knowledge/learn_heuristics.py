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
    """Collapse one (session, check_type) episode's fix_events (sorted by iter)
    into a trajectory row. All events MUST share one check_type — a '--check both'
    run reuses one fix_session_id across DRC and LVS, so callers key by
    (session, check_type) and we assert the invariant here (bug #2/#8)."""
    import fix_log_manager
    events = fix_log_manager.dedup_events_by_action(events)
    events = sorted(events, key=lambda e: (e.get("iter") or 0))
    checks = {e.get("check_type") for e in events}
    assert len(checks) == 1, f"mixed check_type in one trajectory: {checks}"
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


def _fetch_all_fix_events(conn) -> list[dict]:
    """Read raw fix_events from the HOT table UNION the cold sidecar archive, so a
    full rebuild stays lossless after archive_old_raw evicts merged episodes
    (bug #12). The archive lives next to the main DB as fix_events_archive.sqlite
    with the same columns (no PK). Sidecar-absent is handled gracefully."""
    import fix_log_manager
    db_path = Path(conn.execute("PRAGMA database_list").fetchone()[2])
    arch = fix_log_manager._archive_db_path(db_path)
    cur = conn.execute("SELECT * FROM fix_events")
    cols = [c[0] for c in cur.description]
    events = [dict(zip(cols, r)) for r in cur.fetchall()]
    if arch.exists():
        # ATTACH and UNION ALL the archived rows (same column names; column order
        # may differ, so select by explicit shared column list).
        conn.execute("ATTACH DATABASE ? AS arch", (str(arch),))
        try:
            acur = conn.execute(
                f"SELECT {', '.join(cols)} FROM arch.fix_events_archive")
            events.extend(dict(zip(cols, r)) for r in acur.fetchall())
        finally:
            conn.execute("DETACH DATABASE arch")
    return events


def _rebuild_fix_trajectories(conn) -> list[dict]:
    """Re-derive Tier-2 from Tier-1 (full rebuild — idempotent). Groups by
    (fix_session_id, check_type) so a '--check both' session yields one trajectory
    per check (bug #2/#8), and rebuilds from hot+archived events (bug #12)."""
    events = _fetch_all_fix_events(conn)
    by_session: dict[tuple, list[dict]] = {}
    for e in events:
        by_session.setdefault((e["fix_session_id"], e.get("check_type")), []).append(e)
    trajectories = [_build_trajectory(evs) for evs in by_session.values()]
    conn.execute("DELETE FROM fix_trajectories")
    for t in trajectories:
        keys = list(t.keys())
        ph = ", ".join(f":{k}" for k in keys)
        conn.execute(f"INSERT INTO fix_trajectories ({', '.join(keys)}) VALUES ({ph})", t)
    conn.commit()
    return events


def _recipes_from_trajectories(trajectories: list[dict]) -> dict[tuple, dict]:
    """Per (family, platform): check -> violation_class -> {strategies, n_sessions}.
    Derived from trajectory path_json so archived raw never changes the counts."""
    acc: dict[tuple, dict] = {}
    for t in trajectories:
        fam = t.get("design_family") or "unknown"
        plat = t.get("platform") or "unknown"
        check, vclass = t.get("check_type"), t.get("violation_class")
        if not check:
            continue
        node = (acc.setdefault((fam, plat), {}).setdefault(check, {})
                .setdefault(vclass, {"strategies": {}, "_sessions": set()}))
        node["_sessions"].add(t.get("fix_session_id"))
        for step in json.loads(t.get("path_json") or "[]"):
            sid = step.get("strategy")
            if not sid or sid == "none":
                continue
            s = node["strategies"].setdefault(sid, {"attempts": 0, "successes": 0,
                                                    "failures": 0, "wins": 0, "_red": []})
            s["attempts"] += 1
            verdict = step.get("verdict")
            if verdict == "cleared":
                s["successes"] += 1
            elif verdict == "win":
                # Real partial improvement: half credit (bug #7/#11). Tracked
                # separately so fix_model can score it above an untried strategy
                # and well above a pure loser, without claiming a full clearance.
                s["wins"] += 1
            elif verdict in ("no_change", "regression"):
                s["failures"] += 1
            bc, ac = step.get("before"), step.get("after")
            if bc and ac is not None and bc > 0:
                s["_red"].append((bc - ac) / bc)
    final: dict[tuple, dict] = {}
    for key, checks in acc.items():
        final[key] = {}
        for check, vmap in checks.items():
            final[key][check] = {}
            for vclass, node in vmap.items():
                strategies = {}
                for sid, s in node["strategies"].items():
                    red = s.pop("_red")
                    if red:
                        s["median_reduction_pct"] = statistics.median(red)
                    strategies[sid] = s
                final[key][check][vclass] = {"strategies": strategies,
                                             "n_sessions": len(node["_sessions"])}
    return final


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
        _rebuild_fix_trajectories(conn)   # Tier-2 (idempotent rebuild; materializes)

        groups: dict[tuple[str, str], list[dict]] = {}
        for r in rows:
            fam = r.get("design_family") or "unknown"
            plat = r.get("platform") or "unknown"
            groups.setdefault((fam, plat), []).append(r)

        families: dict[str, dict] = {}
        for (fam, plat), group_rows in groups.items():
            entry = _family_platform_entry(group_rows)
            if entry is None:
                continue
            fam_obj = families.setdefault(fam, {"platforms": {}})
            fam_obj["platforms"][plat] = entry
    finally:
        conn.close()

    # Tier-3 fix_recipes derive from Tier-2 fix_trajectories (never archived), so
    # archiving raw fix_events is lossless for learning. Re-read on a fresh
    # connection now that trajectories are materialized + committed.
    conn2 = knowledge_db.connect(db_path)
    cur = conn2.execute("SELECT * FROM fix_trajectories")
    tcols = [c[0] for c in cur.description]
    trajectories = [dict(zip(tcols, r)) for r in cur.fetchall()]
    conn2.close()
    for (fam, plat), recipes in _recipes_from_trajectories(trajectories).items():
        if not recipes:
            continue
        entry = (families.setdefault(fam, {"platforms": {}})["platforms"]
                 .setdefault(plat, {"sample_size": 0, "success_count": 0, "success_rate": 0.0}))
        entry["fix_recipes"] = recipes

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
