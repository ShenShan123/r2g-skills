#!/usr/bin/env python3
"""Deterministic recipe-contradiction probe (read-only over knowledge.sqlite).

Flags STRUCTURAL contradictions: for the SAME symptom signature, two recipes
(strategies) that applied OPPOSITE directions on the SAME config knob where BOTH
reached a successful outcome. A contradiction means the learner is carrying two
mutually-incompatible "fixes" for one symptom — at most one can be right, so the
probe surfaces it for an operator to demote the weaker arm. It NEVER auto-applies
anything: it only emits a paste-ready ``engineer_loop.py demote`` command.

WHY data-driven, not name-driven
--------------------------------
Knob direction is NOT recoverable from strategy names (nearly every utilization
strategy only LOWERS CORE_UTILIZATION) and heuristics.json stores only outcome
COUNTS, never the knob delta. The real applied direction lives in the DB:
  * ``fix_events`` is the ONLY table carrying BOTH the symptom_id AND the strategy
    that produced an edit (plus that iteration's config_delta_json and a verdict).
    It is the spine that ties a knob edit to a (symptom, strategy).
  * ``config_lineage.diff_json`` is the ONLY place carrying the {old,new} pair, so
    it is the authoritative DIRECTION source. We attribute an edge to a strategy
    by matching design_name + platform + knob + the new value the strategy wrote.

SUCCESS gate (BOTH arms): a strategy's evidence counts only if its fix_event
verdict is a clear (cleared|win) AND the matched lineage edge's current_outcome
is_success is true. A losing arm is not a contradiction — it is just a worse fix.

SUPERSESSION exclusion (conservative — never cry wolf): there is no 'superseded'
status in the schema, so we infer it. A lineage edge is SUPERSEDED when its
current_run_id is itself the previous_run_id of a strictly-later edge (the run was
improved upon). Evidence built only on superseded edges is dropped; if that leaves
a strategy with no live evidence, no contradiction is reported. We also drop the
pair when the surviving evidence of one arm is from a strictly-earlier
recipe_status.generation than the other (the older recipe was already replaced).

READ-ONLY: opens the DB via knowledge_db.connect (the caller may also pass a live
conn). Reads ONLY knowledge.sqlite + heuristics.json — never journal.sqlite.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

_KNOWLEDGE_DIR = Path(__file__).resolve().parents[2] / "knowledge"
if str(_KNOWLEDGE_DIR) not in sys.path:
    sys.path.insert(0, str(_KNOWLEDGE_DIR))
import knowledge_db  # noqa: E402

DEFAULT_HEURISTICS_PATH = _KNOWLEDGE_DIR / "heuristics.json"

# fix_event verdicts that count as the strategy having cleared the symptom.
_CLEAR_VERDICTS = ("cleared", "win")
# fix_event after_status values that count as a positive signoff state.
_CLEAR_STATUSES = ("clean", "clean_beol")


def _to_float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _direction(old, new) -> str | None:
    """raise | lower | None (unchanged / unparseable). Numeric comparison."""
    o, n = _to_float(old), _to_float(new)
    if o is None or n is None or o == n:
        return None
    return "raise" if n > o else "lower"


def _outcome_is_success(current_outcome: str | None) -> bool:
    """current_outcome is the JSON ingest stamps onto a lineage edge; it carries a
    pre-computed is_success (single shared predicate). Absent/old rows: treat the
    edge as usable evidence only if it explicitly says success."""
    if not current_outcome:
        return False
    try:
        obj = json.loads(current_outcome)
    except (TypeError, ValueError):
        return False
    return bool(obj.get("is_success")) if isinstance(obj, dict) else False


def _superseded_run_ids(conn: sqlite3.Connection) -> set[str]:
    """Run ids that were later improved upon: a current_run_id that is itself the
    previous_run_id of a STRICTLY-LATER edge (by created_at, then row id as a
    deterministic tiebreak). Conservative — only same design+platform chains."""
    cur = conn.execute(
        "SELECT id, design_name, platform, current_run_id, previous_run_id, "
        "created_at FROM config_lineage")
    edges = [dict(zip(
        ("id", "design_name", "platform", "current_run_id", "previous_run_id",
         "created_at"), r)) for r in cur.fetchall()]
    # Index edges by (design, platform) -> list, and by current_run_id -> edge.
    by_current: dict[tuple, dict] = {}
    for e in edges:
        by_current[(e["design_name"], e["platform"], e["current_run_id"])] = e
    superseded: set[str] = set()
    for e in edges:
        # Does some LATER edge use e.current_run_id as its previous_run_id?
        producer = by_current.get(
            (e["design_name"], e["platform"], e["previous_run_id"]))
        if producer is None:
            continue
        # e improves on `producer` iff e is strictly later in the chain.
        later = (e["created_at"] or "", e["id"]) > (
            producer["created_at"] or "", producer["id"])
        if later:
            superseded.add(producer["current_run_id"])
    return superseded


def _strategy_generation(conn: sqlite3.Connection) -> dict[tuple, int]:
    """(symptom_id, strategy) -> max recipe_status.generation (or -1 if none)."""
    out: dict[tuple, int] = {}
    try:
        cur = conn.execute(
            "SELECT symptom_id, strategy, generation, status FROM recipe_status")
    except sqlite3.OperationalError:
        return out
    for sid, strat, gen, _status in cur.fetchall():
        g = gen if isinstance(gen, int) else -1
        key = (sid, strat)
        out[key] = max(out.get(key, -1), g)
    return out


def _strategy_status(conn: sqlite3.Connection) -> dict[tuple, str]:
    """(symptom_id, design_class, platform, strategy) -> status. Absent = promoted
    (grandfathered, mirrors recipe_lifecycle.GRANDFATHERED)."""
    out: dict[tuple, str] = {}
    try:
        cur = conn.execute(
            "SELECT symptom_id, design_class, platform, strategy, status "
            "FROM recipe_status")
    except sqlite3.OperationalError:
        return out
    for sid, dc, plat, strat, status in cur.fetchall():
        out[(sid, dc, plat, strat)] = status
    return out


def _lineage_direction_index(conn: sqlite3.Connection, superseded: set[str]):
    """(design_name, platform, knob, new_value) -> direction, restricted to
    SUCCESSFUL, NON-superseded edges. new_value is the canonical string ingest
    stored. A given (design,plat,knob,new) maps to a single direction in practice
    (a config edit has one old->new); if a chain produced conflicting old values
    we keep the most recent (deterministic by created_at, id)."""
    cur = conn.execute(
        "SELECT id, design_name, platform, current_run_id, diff_json, "
        "current_outcome, created_at FROM config_lineage "
        "ORDER BY created_at, id")
    index: dict[tuple, str] = {}
    for row in cur.fetchall():
        (lid, design, plat, cur_run, diff_json, current_outcome,
         _created) = row
        if cur_run in superseded:
            continue
        if not _outcome_is_success(current_outcome):
            continue
        try:
            diff = json.loads(diff_json) if diff_json else {}
        except (TypeError, ValueError):
            continue
        changed = (diff.get("changed") or {}) if isinstance(diff, dict) else {}
        for knob, od in changed.items():
            if not isinstance(od, dict):
                continue
            direction = _direction(od.get("old"), od.get("new"))
            if direction is None:
                continue
            new_val = str(od.get("new"))
            # later edges overwrite earlier (rows are ORDER BY created_at,id).
            index[(design, plat, knob, new_val)] = direction
    return index


def _canon_value(v) -> str:
    """Mirror ingest's _canon for delta values so a fix_event's '15' / '15.0'
    matches the lineage edge's stored new value."""
    s = str(v).strip()
    try:
        f = float(s)
        if f == int(f):
            return str(int(f))
        return str(f)
    except (ValueError, OverflowError):
        return s


def find_contradictions(conn: sqlite3.Connection,
                        heuristics: dict | None = None) -> list[dict]:
    """Return STRUCTURAL recipe contradictions over the knowledge store.

    Each dict: {symptom_id, design_class, platform, knob, strategy_a, dir_a,
    strategy_b, dir_b, evidence_a:{attempts,successes}, evidence_b:{...},
    severity ('high' if both promoted else 'medium'), demote_command}.
    Deterministic + read-only; never touches journal.sqlite.
    """
    superseded = _superseded_run_ids(conn)
    dir_index = _lineage_direction_index(conn, superseded)
    gen = _strategy_generation(conn)
    status = _strategy_status(conn)

    # Walk fix_events (the symptom+strategy spine). For each successful edit that
    # touched a knob and whose direction we can resolve from lineage, accumulate
    # per (symptom, knob, direction) -> strategy -> {attempts, successes}, plus the
    # design_class/platform we will key the demote on.
    try:
        cur = conn.execute(
            "SELECT symptom_id, strategy, design_name, platform, verdict, "
            "after_status, config_delta_json FROM fix_events "
            "WHERE symptom_id IS NOT NULL AND strategy IS NOT NULL "
            "ORDER BY symptom_id, strategy, design_name")
    except sqlite3.OperationalError:
        return []

    # (symptom, knob) -> direction -> strategy -> evidence dict
    accum: dict[tuple, dict] = {}
    # (symptom, knob, direction, strategy) -> (design_class, platform) for demote.
    keymap: dict[tuple, tuple] = {}

    for (sid, strat, design, plat, verdict, after_status,
         delta_json) in cur.fetchall():
        if not delta_json:
            continue
        try:
            delta = json.loads(delta_json)
        except (TypeError, ValueError):
            continue
        if not isinstance(delta, dict):
            continue
        cleared = (verdict in _CLEAR_VERDICTS) or (
            after_status in _CLEAR_STATUSES)
        if not cleared:
            continue
        for knob, new_val in delta.items():
            direction = dir_index.get(
                (design, plat, knob, _canon_value(new_val)))
            if direction is None:
                continue   # no successful, non-superseded lineage edge to attribute
            sk = (sid, knob)
            by_dir = accum.setdefault(sk, {})
            by_strat = by_dir.setdefault(direction, {})
            ev = by_strat.setdefault(
                strat, {"attempts": 0, "successes": 0})
            ev["attempts"] += 1
            ev["successes"] += 1
            # design_class is not on fix_events; the contradiction is keyed by the
            # symptom signature. Use the platform from the evidence and the
            # canonical 'unknown/unknown' design_class the learner buckets under
            # when no class was recorded (matches recipes[sid][dc] shape).
            keymap_key = (sid, knob, direction, strat)
            keymap.setdefault(keymap_key, ("unknown/unknown", plat))

    results: list[dict] = []
    for (sid, knob), by_dir in sorted(accum.items()):
        if "raise" not in by_dir or "lower" not in by_dir:
            continue
        # One contradiction per opposed strategy PAIR (deterministic: strongest
        # raiser vs strongest lowerer by success count, then name).
        def _pick(d: dict) -> str:
            return sorted(d, key=lambda s: (-d[s]["successes"], s))[0]

        strat_a = _pick(by_dir["raise"])
        strat_b = _pick(by_dir["lower"])
        ev_a = by_dir["raise"][strat_a]
        ev_b = by_dir["lower"][strat_b]

        dc_a, plat_a = keymap[(sid, knob, "raise", strat_a)]
        dc_b, plat_b = keymap[(sid, knob, "lower", strat_b)]

        # Generation supersession: if one arm's recipe is a strictly-earlier
        # generation than the other, the newer one replaced it — not a live
        # contradiction. -1 (no recipe_status row) is treated as equal/unknown so
        # grandfathered recipes still contradict each other.
        ga = gen.get((sid, strat_a), -1)
        gb = gen.get((sid, strat_b), -1)
        if ga >= 0 and gb >= 0 and ga != gb:
            continue

        # Pick the WEAKER arm (lower success rate; tiebreak: fewer successes, then
        # name) as the demote target so the operator silences the worse fix.
        def _rate(ev: dict) -> float:
            return ev["successes"] / ev["attempts"] if ev["attempts"] else 0.0

        arm_a = (strat_a, dc_a, plat_a, ev_a, "raise")
        arm_b = (strat_b, dc_b, plat_b, ev_b, "lower")
        weaker, stronger = sorted(
            (arm_a, arm_b),
            key=lambda a: (_rate(a[3]), a[3]["successes"], a[0]))[0:2]

        st_a = status.get((sid, dc_a, plat_a, strat_a), "promoted")
        st_b = status.get((sid, dc_b, plat_b, strat_b), "promoted")
        severity = "high" if (st_a == "promoted" and st_b == "promoted") else "medium"

        w_strat, w_dc, w_plat, _w_ev, _w_dir = weaker
        o_strat = stronger[0]
        demote_command = (
            "python3 scripts/loop/engineer_loop.py demote "
            f"--symptom {sid} --design-class {w_dc} --platform {w_plat} "
            f"--strategy {w_strat} "
            f'--reason "structural contradiction with {o_strat} on {knob}"')

        results.append({
            "symptom_id": sid,
            "design_class": dc_a if dc_a == dc_b else dc_a,
            "platform": plat_a if plat_a == plat_b else plat_a,
            "knob": knob,
            "strategy_a": strat_a,
            "dir_a": "raise",
            "strategy_b": strat_b,
            "dir_b": "lower",
            "evidence_a": dict(ev_a),
            "evidence_b": dict(ev_b),
            "severity": severity,
            "demote_command": demote_command,
        })
    return results


def _load_heuristics(path: Path | str | None) -> dict:
    if path is None:
        path = DEFAULT_HEURISTICS_PATH
    p = Path(path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _print_summary(hits: list[dict]) -> None:
    if not hits:
        print("No structural recipe contradictions found.")
        return
    print(f"{len(hits)} structural recipe contradiction(s) found:\n")
    for h in hits:
        print(f"  symptom {h['symptom_id']}  knob {h['knob']}  "
              f"[{h['severity'].upper()}]")
        print(f"    {h['strategy_a']} ({h['dir_a']}) "
              f"vs {h['strategy_b']} ({h['dir_b']})  "
              f"evidence {h['evidence_a']} / {h['evidence_b']}")
        print(f"    -> {h['demote_command']}\n")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--db", type=Path, default=knowledge_db.DEFAULT_DB_PATH)
    p.add_argument("--heuristics", type=Path, default=DEFAULT_HEURISTICS_PATH)
    p.add_argument("--json", action="store_true",
                   help="emit the contradiction list as JSON")
    args = p.parse_args(argv)

    heuristics = _load_heuristics(args.heuristics)
    conn = knowledge_db.connect(args.db)
    try:
        hits = find_contradictions(conn, heuristics)
    finally:
        conn.close()

    if args.json:
        print(json.dumps(hits, indent=2, sort_keys=True))
    else:
        _print_summary(hits)
    return 0   # reporting tool: always succeed


if __name__ == "__main__":
    sys.exit(main())
