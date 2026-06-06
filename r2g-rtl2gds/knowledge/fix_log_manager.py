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
