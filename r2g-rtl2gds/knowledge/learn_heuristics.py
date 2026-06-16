#!/usr/bin/env python3
"""Derive empirical per-family heuristics from knowledge.sqlite.

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


def _fetch_learnable_rows(conn) -> list[dict]:
    """Runs eligible for LEARNING — excludes held-out r2g-bench runs (Win 3). The
    filter lives ONLY here (the learning read); ingest still writes failure_events
    / run_violations for bench runs. COALESCE so legacy rows (is_bench NULL) and
    DBs predating the column are treated as not-bench (included)."""
    cur = conn.execute("SELECT * FROM runs WHERE COALESCE(is_bench, 0) = 0")
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _bench_project_paths(conn) -> set[str]:
    """project_paths of held-out bench runs, to exclude their fix trajectories from
    recipe learning. Tolerant of a DB predating the is_bench column."""
    try:
        return {r[0] for r in conn.execute(
            "SELECT project_path FROM runs WHERE is_bench = 1") if r[0]}
    except Exception:
        return set()


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
    # Symptom of the episode: first event's stored symptom, else coarse backfill
    # from (check_type, violation_class) so legacy/backfilled events still index by
    # symptom (symptom-indexed memory, spec 2026-06-09).
    import symptom as _symptom
    sid = first.get("symptom_id")
    sigj = first.get("signature_json")
    if not sid:
        sig = _symptom.canonical_signature(first.get("check_type"),
                                           first.get("violation_class"), None)
        sid, sigj = _symptom.symptom_id(sig), json.dumps(sig, sort_keys=True)
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
        "symptom_id": sid,
        "signature_json": sigj,
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


def _symptom_recipes_from_trajectories(trajectories: list[dict]) -> dict[str, dict]:
    """Aggregate trajectories BY symptom_id (pooled across families/platforms).
    Family-name is recorded only as evidence_designs provenance; platform is a
    conditioning attribute kept in platforms_seen + per-strategy by_platform
    (symptom-indexed memory, spec 2026-06-09)."""
    acc: dict[str, dict] = {}
    for t in trajectories:
        sid = t.get("symptom_id")
        if not sid:
            continue
        sig = json.loads(t.get("signature_json") or "{}")
        plat = t.get("platform") or "unknown"
        node = acc.setdefault(sid, {
            "check": sig.get("check"), "class": sig.get("class"),
            "predicates": sig.get("predicates") or {},
            "platforms_seen": set(), "evidence_designs": set(),
            "_sessions": set(), "strategies": {}})
        node["platforms_seen"].add(plat)
        if t.get("design_name"):
            node["evidence_designs"].add(t["design_name"])
        node["_sessions"].add(t.get("fix_session_id"))
        for step in json.loads(t.get("path_json") or "[]"):
            stratid = step.get("strategy")
            if not stratid or stratid == "none":
                continue
            s = node["strategies"].setdefault(stratid, {
                "attempts": 0, "successes": 0, "failures": 0, "wins": 0,
                "by_platform": {}})
            bp = s["by_platform"].setdefault(plat, {
                "attempts": 0, "successes": 0, "failures": 0, "wins": 0})
            verdict = step.get("verdict")
            for tgt in (s, bp):
                tgt["attempts"] += 1
                if verdict == "cleared":
                    tgt["successes"] += 1
                elif verdict == "win":
                    tgt["wins"] += 1
                elif verdict in ("no_change", "regression"):
                    tgt["failures"] += 1
    final: dict[str, dict] = {}
    for sid, node in acc.items():
        final[sid] = {
            "check": node["check"], "class": node["class"],
            "predicates": node["predicates"],
            "platforms_seen": sorted(node["platforms_seen"]),
            "evidence_designs": sorted(node["evidence_designs"]),
            "n_sessions": len(node["_sessions"]),
            "strategies": node["strategies"],
        }
    return final


def _bump_generation(conn) -> int:
    row = conn.execute("SELECT value FROM meta WHERE key='generation'").fetchone()
    gen = (int(row[0]) if row else 0) + 1
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('generation', ?)",
                 (str(gen),))
    conn.commit()
    return gen


def _design_class_by_project(conn) -> dict[str, str]:
    return {r[0]: (r[1] or "unknown/unknown") for r in conn.execute(
        "SELECT project_path, design_class FROM runs WHERE project_path IS NOT NULL")}


def _indexed_recipes(trajectories: list[dict],
                     class_of: dict[str, str],
                     score_of: dict[str, float] | None = None) -> dict:
    """Decision-8 projection: recipes[symptom_id][design_class][platform] with
    '*' pooled rollups at each relaxation level. Strategy counts mirror
    _recipes_from_trajectories semantics (cleared/win/no_change/regression).

    score_of maps project_path -> the run's dense outcome_score (Win 1). Each
    strategy accrues a `mean_outcome_score` over the runs whose fix episodes used
    it — the tiebreaker fix_model ranks on WITHIN equal clean-rate. Absent
    (legacy DB / no scored runs) -> the field is omitted and ranking is unchanged."""
    score_of = score_of or {}

    def _node():
        return {"strategies": {}, "_sessions": set()}

    acc: dict = {}
    for t in trajectories:
        sid = t.get("symptom_id")
        if not sid:
            continue
        dclass = class_of.get(t.get("project_path") or "", "unknown/unknown")
        plat = t.get("platform") or "unknown"
        run_score = score_of.get(t.get("project_path") or "")
        bucket = acc.setdefault(sid, {})
        targets = [bucket.setdefault(dc, {}).setdefault(p, _node())
                   for dc in (dclass, "*") for p in (plat, "*")]
        for step in json.loads(t.get("path_json") or "[]"):
            strat = step.get("strategy")
            if not strat or strat == "none":
                continue
            verdict = step.get("verdict")
            for node in targets:
                node["_sessions"].add(t.get("fix_session_id"))
                s = node["strategies"].setdefault(
                    strat, {"attempts": 0, "successes": 0, "failures": 0,
                            "wins": 0, "_scores": []})
                s["attempts"] += 1
                if verdict == "cleared":
                    s["successes"] += 1
                elif verdict == "win":
                    s["wins"] += 1
                elif verdict in ("no_change", "regression"):
                    s["failures"] += 1
                if run_score is not None:
                    s["_scores"].append(run_score)
    for sid, classes in acc.items():
        for dc, plats in classes.items():
            for p, node in plats.items():
                node["n_sessions"] = len(node.pop("_sessions"))
                for s in node["strategies"].values():
                    scores = s.pop("_scores", [])
                    if scores:
                        s["mean_outcome_score"] = statistics.mean(scores)
    return acc


def learn(db_path: Path | str,
          out_path: Path | str,
          enqueue_candidates: bool = True) -> dict:
    db_path = Path(db_path)
    out_path = Path(out_path)

    # Read the PRIOR heuristics off disk BEFORE we overwrite it, so the recipe
    # lifecycle can diff new/changed recipes against it. This is the production
    # path's candidate-enqueue hook — without it, recipe_status stayed empty and
    # the A/B loop never fired (Tier −1 Gate A diagnosis, 2026-06-16). engineer_loop
    # also enqueues in learn_cycle; diff_and_enqueue is idempotent so the two
    # compose safely.
    prev_heur = None
    if enqueue_candidates and out_path.exists():
        try:
            prev_heur = json.loads(out_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            prev_heur = None

    conn = knowledge_db.connect(db_path)
    try:
        # Idempotent: guarantees fix_events / fix_trajectories exist before we
        # SELECT / DELETE / INSERT on them, even on legacy DBs predating Task 1.
        knowledge_db.ensure_schema(conn)
        rows = _fetch_learnable_rows(conn)   # Win 3: held-out bench runs excluded
        bench_paths = _bench_project_paths(conn)
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
    # Win 3: drop held-out bench episodes from recipe learning (the trajectories
    # are still materialized in the table — only the LEARNING aggregation excludes).
    if bench_paths:
        trajectories = [t for t in trajectories
                        if t.get("project_path") not in bench_paths]
    class_of = _design_class_by_project(conn2)
    # Win 1: per-run dense reward, joined into recipes as a ranking tiebreaker.
    # NULL-filtered so legacy/unscored runs simply don't contribute (neutral); bench
    # runs excluded (Win 3).
    score_of = {r[0]: r[1] for r in conn2.execute(
        "SELECT project_path, outcome_score FROM runs "
        "WHERE project_path IS NOT NULL AND outcome_score IS NOT NULL "
        "AND COALESCE(is_bench, 0) = 0")}
    gen = _bump_generation(conn2)
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
        "schema_version": 3,                       # decision-8 recipes projection
        "generation": gen,
        "families": families,
        "symptoms": _symptom_recipes_from_trajectories(trajectories),
        "recipes": _indexed_recipes(trajectories, class_of, score_of),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # Tier −1 Gate A: enqueue new/changed recipes as A/B candidates so the
    # shadow→candidate→promoted lifecycle fires on EVERY learner rebuild, not only
    # inside engineer_loop.run (which never drove a production campaign). A failure
    # here must never break learning — the heuristics are already written.
    if enqueue_candidates:
        try:
            import recipe_lifecycle
            lc = knowledge_db.connect(db_path)
            try:
                recipe_lifecycle.diff_and_enqueue(lc, data, prev=prev_heur)
            finally:
                lc.close()
        except Exception as exc:                       # pragma: no cover - guard
            print(f"WARNING: A/B candidate enqueue skipped: {exc}", file=sys.stderr)
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
