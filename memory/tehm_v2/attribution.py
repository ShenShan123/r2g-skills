"""Rejection attribution for the DEV phase (user review 2026-10-18: typed matching is a selection
strategy, not proof that an action cannot work -- track both the waste it avoids and the valid
actions it rejects).

A typed-matching (or applicability) rejection in the TEHM-v2 arm is paired with the SAME edit
executed for the SAME task FROM THE SAME PRE-EXECUTION STATE (pre_state: the failure signature,
its metric, and the configuration values of the edited knobs plus the floorplan knobs) in the
ablation arm where that mechanism is off (user review 2026-10-18: an ablation run is not
automatically a counterfactual; a different starting state is `unmatched`):
  avoided_waste      the ablation run was no_improvement / regression / no_effect / rerun failure
  wrongly_rejected   the ablation run cleared or improved the target
  unmatched          the ablation never executed that edit (counterfactual UNKNOWN; reported)
"""
from __future__ import annotations

import json

from tehm_v2.policy import action_key

STATE_KNOBS = ("CORE_UTILIZATION", "PLACE_DENSITY", "PLACE_DENSITY_LB_ADDON", "DIE_AREA", "CORE_AREA")


def pre_state(signature, metric, cfg: dict, edits: dict) -> str:
    """Canonical pre-execution state used for matching (values as strings)."""
    keys = sorted(set(STATE_KNOBS) | set(edits or {}))
    return json.dumps({"signature": signature, "metric": metric,
                       "config": {k: (None if (cfg or {}).get(k) is None else str(cfg[k])) for k in keys}},
                      sort_keys=True)

WASTE = ("no_improvement", "regression", "recipe_no_effect", "no_change", "no_target")
VALID = ("cleared", "applied")


def rejection_ledger(decision_log: list, ablation_rows: list, mechanism: str = "typed_matching") -> dict:
    """decision_log: [{task, id, edits, pre_state, decision, mechanism}]; ablation_rows: fix-log rows
    of the ablation arm with added `task` and `pre_state` keys (pre_state() of the row's own
    pre-execution state)."""
    executed = {}
    for r in ablation_rows:
        if r.get("strategy") in (None, "none"):
            continue
        executed.setdefault((r["task"], action_key(r.get("edits") or {}), r.get("pre_state")), []).append(r.get("verdict"))
    out = {"rejected": 0, "avoided_waste": 0, "wrongly_rejected": 0, "unmatched": 0, "items": []}
    for d in decision_log:
        if d.get("decision") != "rejected" or d.get("mechanism") != mechanism:
            continue
        out["rejected"] += 1
        verdicts = executed.get((d["task"], action_key(d.get("edits") or {}), d.get("pre_state")))
        if d.get("pre_state") is None:
            verdicts = None                                  # no recorded pre-state: never matched
        if not verdicts:
            cls = "unmatched"
        elif any(v in VALID for v in verdicts):
            cls = "wrongly_rejected"
        elif all(v in WASTE or str(v).startswith(("rerun_failed", "memory_rerun_failed")) for v in verdicts):
            cls = "avoided_waste"
        else:
            cls = "unmatched"
        out[cls] += 1
        out["items"].append({"task": d["task"], "id": d.get("id"), "class": cls, "ablation_verdicts": verdicts})
    return out
