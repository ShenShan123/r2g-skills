"""TEHM policy decision policy (T2 design draft §8; user plan 2026-10-18).

A pure layer over the repair plan of the TEHM arm only. NB and RECIPE never pass through
it, and it never touches the shared executor, knob permissions, acceptance or the engine
loop's deterministic recovery ladder. Four mechanisms, each switchable for the DEV
ablation:

  typed_matching     a memory candidate whose action types (from its knobs, and from the
                     failure types its evidence was learned on) do not include the current
                     typed failure (setup / hold / drv / drc:<rule> / route:<code> /
                     place:<code>) is rejected -- e.g. a DRV slew/cap margin on a setup-only
                     failure (the T1 defect).
  applicability      rejected before execution: no edits; a no-op edit (every knob already has
                     the value); a value outside the knob's range. (Unconstrained timing yields
                     no setup/hold failure, so typed matching rejects timing candidates.) No
                     knob "direction" is assumed: history shows both directions clearing.
  evidence_ranking   ordered by independent, admitted evidence (strict positives), not by a
                     single-step clear: support desc, then harm asc, ineffective asc, partial
                     progress desc. A move is deferred only on REPLICATED net harm: harm >
                     support + partial, with harm seen in >= k_harm independent source groups
                     (policy, default 2). Deferred memory goes after the shared catalogue; for
                     the TEHM arm a deferred catalogue move goes after the other catalogue moves
                     (never removed).
  repeat_control     within this episode: the same effective edit is never repeated; after a
                     REGRESSION the same knob is not pushed further in the same direction (a
                     NO_IMPROVEMENT only blocks the identical edit); if the last regression
                     was not rolled back, no memory candidate runs.

Evidence comes from a frozen index (evidence_index.py) built from verified ann-v2 labels.
Independence: observations collapse to one per (source_group, task, move); units of the
current task's own ip_family are never used. Every decision is returned with its reason, so
a DEV run can show what was retrieved, executed or rejected, and why.
"""
from __future__ import annotations

import json
import math

from tehm.policy.evidence_index import ORFS_DEFAULTS

MECHANISMS = ("typed_matching", "applicability", "evidence_ranking", "repeat_control")

# Knob semantics: the failure types a knob can act on, and the direction that can help them.
KNOBS = {   # the failure types a knob can act on, and its allowed range (no direction assumed)
    "CORE_UTILIZATION": {"types": {"route", "place", "drc"}, "min": 10.0, "max": 100.0},
    "PLACE_DENSITY": {"types": {"route", "place", "drc"}, "min": 0.1, "max": 1.0},
    "CELL_PAD_IN_SITES_GLOBAL_PLACEMENT": {"types": {"drc", "route"}, "min": 0.0, "max": 8.0},
    "CELL_PAD_IN_SITES_DETAIL_PLACEMENT": {"types": {"drc", "route"}, "min": 0.0, "max": 8.0},
    "R2G_ROUTE_SLEW_MARGIN": {"types": {"drv"}, "min": 0.0, "max": 60.0},
    "R2G_ROUTE_CAP_MARGIN": {"types": {"drv"}, "min": 0.0, "max": 60.0},
}


def _num(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def moves(edits: dict, cfg: dict) -> dict:
    """{knob: (before, after, direction)} for an edit set against the current config."""
    out = {}
    for k, v in (edits or {}).items():
        b, a = _num((cfg or {}).get(k)), _num(v)
        if b is None and (cfg or {}).get(k) is None:
            b = ORFS_DEFAULTS.get(k)          # unset means the ORFS default, same rule as the index
        if a is None:
            out[k] = (cfg.get(k) if cfg else None, v, "set")
        elif b is None:
            out[k] = (None, a, "set")
        else:
            out[k] = (b, a, "up" if a > b else "down" if a < b else "same")
    return out


def action_key(edits: dict) -> str:
    return json.dumps({k: str(v) for k, v in sorted((edits or {}).items())}, sort_keys=True)


def family(t: str) -> str:
    return t.split(":")[0]


def current_types(state: dict) -> set:
    """Typed current failure, e.g. {'setup'}, {'drv'}, {'drc', 'drc:li.3'}, {'route', 'route:GRT-0116'}."""
    out = set()
    for t in state.get("failures") or []:
        out.add(t)
        out.add(family(t))
    return out


def candidate_types(c: dict, index: dict) -> set:
    """The failure types a candidate can act on: its knobs' declared types, plus the types on
    which its moves have ADMITTED support in the index."""
    out = set()
    for k in (c.get("config_edits") or {}):
        out |= KNOBS.get(k, {}).get("types", set())
    for m in _evidence_for(c, index, None, set()):
        if m["outcome"] == "admitted_positive":
            out.add(family(m["signature_type"]))
    return out


def _evidence_for(c, index, signature, exclude_families):
    """Index entries matching this candidate's knob moves (by knob and direction)."""
    mv = c.get("_moves") or {}
    out = []
    for k, (_, _, d) in mv.items():
        for e in index.get("entries", []):
            if e["knob"] != k or e["direction"] != d:
                continue
            if signature is not None and e["signature"] != signature:
                continue
            if e.get("ip_family") in exclude_families:
                continue
            out.append(e)
    return out


def evidence_summary(c, index, signature, exclude_families) -> dict:
    """Independent support / harm / partial counts (one per source_group, task, move)."""
    seen = {}
    for e in _evidence_for(c, index, signature, exclude_families):
        key = (e.get("source_group"), e.get("task"), e["knob"], e["direction"], e["outcome"])
        seen[key] = e
    s = {"support": 0, "harm": 0, "partial": 0, "ineffective": 0, "unconfirmed": 0, "groups": set(),
         "harm_groups": set(), "units": []}
    for (grp, _, _, _, outcome), e in seen.items():
        if outcome == "admitted_positive":
            s["support"] += 1
        elif outcome == "harm":
            s["harm"] += 1
            if grp:
                s["harm_groups"].add(grp)
        elif outcome in ("improved", "cleared_new_failure"):
            s["partial"] += 1
        elif outcome == "no_change":
            s["ineffective"] += 1
        elif outcome == "cleared_unconfirmed":          # D8 candidate class: reported, never ranked
            s["unconfirmed"] += 1
        s["groups"].add(grp)
        s["units"].append(e.get("unit_id"))
    s["groups"] = sorted(g for g in s["groups"] if g)
    s["harm_groups"] = sorted(s["harm_groups"])
    return s


def _net_harm(ev: dict, k_harm: int) -> bool:
    """Replicated net harm: more harm than support + partial progress, seen in >= k_harm
    independent source groups."""
    return ev["harm"] > ev["support"] + ev["partial"] and len(ev["harm_groups"]) >= k_harm


def _applicability(c: dict, state: dict, cur: set) -> str | None:
    mv = c["_moves"]
    if not mv:
        return "no edits"
    if all(d == "same" for _, _, d in mv.values()):
        return "no-op: every edited knob already has this value"
    for k, (b, a, d) in mv.items():
        spec = KNOBS.get(k)
        if spec and isinstance(a, float) and not (spec["min"] <= a <= spec["max"]):
            return f"{k}={a} outside [{spec['min']}, {spec['max']}]"
    return None


def _episode_blocks(c: dict, episode: list) -> str | None:
    key = action_key(c.get("config_edits"))
    for r in episode:
        if r.get("action_key") == key:
            return f"repeat: identical edit already executed ({r.get('verdict')})"
    for r in episode:
        if r.get("verdict") != "regression":
            continue
        for k, (b, a, d) in c["_moves"].items():
            prev = (r.get("moves") or {}).get(k)
            if not prev or d not in ("up", "down") or prev[2] != d:
                continue
            if (d == "down" and a <= prev[1]) or (d == "up" and a >= prev[1]):
                return f"repeat: {k} {d} to {a} after the same push to {prev[1]} regressed"
    return None


def decide(strategies: list, state: dict, index: dict, episode: list, flags: dict,
           exclude_families: set | frozenset = frozenset(), k_harm: int = 2) -> tuple:
    """(ordered strategies, decisions). Memory candidates are those with source tehm_*;
    every other strategy is the shared catalogue, which is reordered (never removed) only by
    evidence_ranking."""
    flags = {m: bool(flags.get(m)) for m in MECHANISMS}
    cfg = state.get("cfg") or {}
    cur = current_types(state)
    signature = state.get("signature")
    decisions, memory, catalogue = [], [], []
    halt = None
    if flags["repeat_control"]:
        last_reg = [r for r in episode if r.get("verdict") == "regression"]
        if last_reg and last_reg[-1].get("rolled_back") is not True:
            halt = "the last regression was not rolled back: no memory candidate runs"
    for i, s in enumerate(strategies):
        c = dict(s)
        c["_moves"] = moves(c.get("config_edits"), cfg)
        c["_order"] = i
        is_mem = str(c.get("source", "")).startswith("tehm")
        reason = mech = None
        if is_mem and halt:
            reason, mech = halt, "repeat_control"
        if is_mem and not reason and flags["typed_matching"]:
            types = candidate_types(c, index)
            if types and cur and not (types & cur):
                reason, mech = f"type mismatch: acts on {sorted(types)}, failure is {sorted(cur)}", "typed_matching"
        if is_mem and not reason and flags["applicability"]:
            r = _applicability(c, state, cur)
            if r:
                reason, mech = r, "applicability"
        if not reason and flags["repeat_control"]:
            r = _episode_blocks(c, episode)
            if r:
                reason, mech = r, "repeat_control"
        ev = evidence_summary(c, index, signature, set(exclude_families)) if flags["evidence_ranking"] else None
        c["_evidence"] = ev
        if reason:
            decisions.append({"id": c.get("id"), "memory": is_mem, "decision": "rejected", "mechanism": mech,
                              "reason": reason})
            continue
        (memory if is_mem else catalogue).append(c)
    first, deferred_mem, cat_ok, cat_def = [], [], [], []
    if flags["evidence_ranking"]:
        for c in memory:
            (deferred_mem if _net_harm(c["_evidence"], k_harm) else first).append(c)
        first.sort(key=lambda c: (-c["_evidence"]["support"], c["_evidence"]["harm"],
                                  c["_evidence"]["ineffective"], -c["_evidence"]["partial"], c["_order"]))
        for c in catalogue:
            (cat_def if _net_harm(c["_evidence"], k_harm) else cat_ok).append(c)
    else:
        first, cat_ok = memory, catalogue
    ordered = first + cat_ok + cat_def + deferred_mem
    for pos, c in enumerate(ordered):
        ev = c.get("_evidence")
        decisions.append({"id": c.get("id"), "memory": str(c.get("source", "")).startswith("tehm"),
                          "decision": "kept", "position": pos,
                          "deferred": c in deferred_mem or c in cat_def,
                          "evidence": None if ev is None else {k: ev[k] for k in ("support", "harm", "partial", "ineffective",
                                                                                   "unconfirmed", "groups", "harm_groups")}})
    clean = [{k: v for k, v in c.items() if not k.startswith("_")} for c in ordered]
    return clean, decisions


def episode_rows(fix_log_rows: list, cfg_at=None) -> list:
    """This episode's executed actions: {action_key, moves, verdict, rolled_back}."""
    out = []
    for r in fix_log_rows:
        if r.get("strategy") in (None, "none") or str(r.get("verdict") or "").startswith("stop"):
            continue
        before = r.get("config_before") or {}
        delta = r.get("config_delta")
        if isinstance(delta, str):
            try:
                delta = json.loads(delta)
            except ValueError:
                delta = {}
        after = {k: (v.get("after") if isinstance(v, dict) else v) for k, v in (delta or {}).items()}
        out.append({"action_key": action_key(after), "moves": moves(after, before),
                    "verdict": r.get("verdict"), "rolled_back": r.get("rolled_back")})
    return out
