"""Composition selection over a pool of verified components (R2G memory Phase G2).

Card: memory/evaluation/r2g_memory_phaseG_contract_20261002.md. MEDEM-inspired: components
are first evaluated individually and hierarchically (G1 trials: one knob dimension at a
time on top of the best values kept so far, each with a GRADED severity effect); only then
are compositions chosen, under a budget. Unlike MEDEM, a component that does not clear
alone is kept if it lowers severity -- in repair, such components are often half of the
fix that works (complementarity evidence, Phase F).

Inputs are the shared verified outcomes (tehm.retrieval.vetoes.verified_outcomes) with
tier ``component_trial``: knob_edits {knob: {before, after}}, severity_before/after.

Matching is on the situation CORE (check, class, error code, platform, die mode, pin
pressure); the utilisation / count bands are left out because graded severity carries the
magnitude (card amendment G-A1).

Prediction for the current task:
  place (density excess, additive physics):  predicted = current + (after - before)
  route / DRC (counts, scale with design):   predicted = current * after / before
Edits are TRANSLATED to the task: numeric knobs keep their verified delta relative to the
current config; non-numeric / flag knobs take the verified absolute value.
"""
from __future__ import annotations

import hashlib
import json

CORE_FIELDS = ("check", "violation_class", "error_code", "platform", "die_mode", "perimeter_band")
UNTESTED_BUDGET = 1           # at most one untested composition per task
PARTIAL_MIN_REDUCTION = 0.5   # a non-clearing proposal must predict >= 50% severity reduction
# "Predicted clear" needs margin: the density estimate's ratio spans 1.00-1.19, so a prediction
# AT the limit is a coin flip (card amendment G-A1). Counts must reach 0.
CLEAR_MARGIN = {"place": 0.03, "count": 0.0}


def core(situation: dict) -> tuple:
    return tuple((situation or {}).get(k) for k in CORE_FIELDS)


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _translate(knob_edits: dict, cfg: dict) -> dict | None:
    """Verified edit set -> edits for the current task (None if not translatable)."""
    out = {}
    for knob, e in sorted((knob_edits or {}).items()):
        e = e if isinstance(e, dict) else {"before": None, "after": e}
        before, after, cur = _num(e.get("before")), _num(e.get("after")), _num(cfg.get(knob))
        if before is not None and after is not None and cur is not None and knob == "CORE_UTILIZATION":
            out[knob] = "%g" % max(5.0, min(90.0, cur + (after - before)))
        elif e.get("after") is not None:
            out[knob] = "%g" % after if after is not None else str(e["after"])
        else:
            return None
    return out or None


def _predict(stage_kind: str, current: float, before: float, after: float) -> float | None:
    if current is None or before is None or after is None:
        return None
    if stage_kind == "place":
        return current + (after - before)
    return current * (after / before) if before > 0 else None


def compositions(outcomes, situation: dict, cfg: dict, current_severity) -> list[dict]:
    """Ranked composition proposals for the current task. Each: {edits, predicted_severity,
    components (transition ids), tested (bool), dims, reason}. Empty when the pool has
    nothing usable."""
    if current_severity is None:
        return []
    stage_kind = "place" if (situation or {}).get("violation_class") == "place" else "count"
    pool = [o for o in outcomes if o.get("tier") == "component_trial"
            and core(o.get("situation") or {}) == core(situation)
            and o.get("severity_before") is not None and o.get("severity_after") is not None]
    if not pool:
        return []
    # 1. observed (tested) compositions: each trial edit set, effect averaged over designs
    groups: dict[str, list] = {}
    for o in pool:
        key = ",".join(sorted(o["knob_edits"]))
        sig = json.dumps({k: (o["knob_edits"][k] or {}).get("after") if k != "CORE_UTILIZATION" else
                          round(_num((o["knob_edits"][k] or {}).get("after") or 0) -
                                _num((o["knob_edits"][k] or {}).get("before") or 0), 3)
                          for k in sorted(o["knob_edits"])}, sort_keys=True)
        groups.setdefault(key + "|" + sig, []).append(o)
    cands = []
    for members in groups.values():
        preds = [_predict(stage_kind, float(current_severity), m["severity_before"], m["severity_after"])
                 for m in members]
        preds = [p for p in preds if p is not None]
        edits = _translate(members[0]["knob_edits"], cfg)
        if not preds or not edits:
            continue
        cands.append({"edits": edits, "predicted_severity": round(sum(preds) / len(preds), 4),
                      "components": sorted(m["transition_id"] for m in members), "tested": True,
                      "dims": sorted(edits), "support": len(members)})
    # 2. one untested union: the best single-dimension effect per dimension, unioned
    best_by_dim: dict[str, dict] = {}
    for c in cands:
        if len(c["dims"]) == 1:
            d = c["dims"][0]
            if d not in best_by_dim or c["predicted_severity"] < best_by_dim[d]["predicted_severity"]:
                best_by_dim[d] = c
    tested_sets = {tuple(c["dims"]) for c in cands}
    if len(best_by_dim) >= 2 and tuple(sorted(best_by_dim)) not in tested_sets and UNTESTED_BUDGET > 0:
        edits, pred, comps = {}, float(current_severity), []
        for c in best_by_dim.values():
            edits.update(c["edits"])
            delta = c["predicted_severity"] - float(current_severity)
            pred = pred + delta if stage_kind == "place" else (
                pred * (c["predicted_severity"] / float(current_severity)) if current_severity else pred)
            comps += c["components"]
        cands.append({"edits": edits, "predicted_severity": round(pred, 4), "components": sorted(comps),
                      "tested": False, "dims": sorted(edits), "support": 0})
    # 3. rank: predicted to clear first (cheapest = fewest knobs, then lowest severity);
    #    otherwise only clearly-better partial proposals.
    clear = [c for c in cands if c["predicted_severity"] <= -CLEAR_MARGIN[stage_kind]]
    if clear:
        ranked = sorted(clear, key=lambda c: (not c["tested"], len(c["dims"]), c["predicted_severity"]))
    else:
        cur = float(current_severity)
        ranked = sorted([c for c in cands if cur > 0 and c["predicted_severity"] <= cur * (1 - PARTIAL_MIN_REDUCTION)],
                        key=lambda c: (c["predicted_severity"], not c["tested"], len(c["dims"])))
    untested_seen = 0
    out = []
    for c in ranked:
        if not c["tested"]:
            untested_seen += 1
            if untested_seen > UNTESTED_BUDGET:
                continue
        c["id"] = "compose_" + hashlib.sha1(json.dumps(c["edits"], sort_keys=True).encode()).hexdigest()[:12]
        c["reason"] = ("predicted clear" if c["predicted_severity"] <= -CLEAR_MARGIN[stage_kind]
                       else "predicted >= 50% reduction")
        out.append(c)
    return out
