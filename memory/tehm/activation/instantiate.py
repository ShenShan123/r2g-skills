"""Step 5: instantiate the rewrite (design doc 10).

Turns a bound rule into a concrete, executable structured action for the
flow/signoff domain: ``{config_edits, rerun_from, recheck}``. Concrete slots
come from the rule pattern; hole slots come from the binding. An unbound hole
stays out of the action (the pipeline refuses to execute with UNRESOLVED holes).
"""
from __future__ import annotations

from contracts import RepairContext
from tehm.ids import is_hole

INSTANTIATE_VERSION = "instantiate-v0.1"


def instantiate_rewrite(rule: dict, binding, context: RepairContext) -> dict:
    """Produce the structured action (design doc 10 Step 5 example)."""
    before = rule.get("before_pattern") or {}
    after = rule.get("after_pattern") or {}
    substitutions = binding.substitutions if binding else {}

    knob = _resolve(before.get("knob"), substitutions)
    value = _resolve(after.get("rewrite.value"), substitutions)
    rerun = _resolve(after.get("execution.rerun_from"), substitutions)
    recheck = _resolve(after.get("execution.recheck")
                       or before.get("target_check"), substitutions)

    config_edits = {}
    if knob is not None and value is not None:
        config_edits[knob] = value
    unresolved_knobs: list[str] = []
    witness_selected: list[str] = []
    categorical_selected: list[str] = []
    dropped_knobs: list[str] = []
    knobs = before.get("knobs")
    if isinstance(knobs, str) and knobs and not is_hole(knobs):
        config_edits, unresolved_knobs, witness_selected = _multi_knob_edits(
            knobs.split(","), after, substitutions, context.cfg or {},
            rule.get("hole_witnesses") or {})
        if unresolved_knobs:
            config_edits, unresolved_knobs, categorical_selected, dropped_knobs = _resolve_categorical(
                unresolved_knobs, config_edits, after, rule.get("hole_witnesses") or {})

    # action domain: the rule's own domain (rtl.* / signoff.* / flow.*) wins;
    # flow/signoff falls back to CONFIG_DELTA / REPAIR_ACTION by content.
    action_domain = rule.get("action_domain")
    if not action_domain:
        action_domain = (rule.get("before_pattern") or {}).get("action_domain")
    if not action_domain:
        action_domain = ("flow.CONFIG_DELTA" if config_edits
                         else "signoff.REPAIR_ACTION")

    payload = {
        "config_edits": config_edits,
        "rerun_from": rerun,
        "recheck": recheck,
        "dependency_cone_changed": bool(rerun),
        "register_boundary_changed": False,
    }
    if unresolved_knobs:
        payload["unresolved_knobs"] = unresolved_knobs
    if witness_selected:
        payload["witness_selected"] = witness_selected          # numeric holes: median witness
    if categorical_selected:
        payload["categorical_selected"] = categorical_selected  # categorical holes: mode
    if dropped_knobs:
        payload["dropped_knobs"] = dropped_knobs
    if action_domain.startswith("rtl."):
        for key in ("module", "reset_signal", "signal", "case_expr",
                    "higher_label", "lower_label", "source_state",
                    "target_state", "add_condition", "reg", "target",
                    "replacement", "count", "compatibility_profile"):
            value = (rule.get("after_pattern") or {}).get(f"rtl.{key}") or \
                (rule.get("before_pattern") or {}).get(f"rtl.{key}")
            if key == "compatibility_profile":
                value = value or (rule.get("before_pattern") or {}).get(
                    "compatibility_profile")
            if value is not None:
                payload[key] = _resolve(value, substitutions)

    return {
        "domain": action_domain,
        "transformation_family": rule.get("transformation_family"),
        "payload": payload,
    }


def _multi_knob_edits(knobs: list, after: dict, substitutions: dict, cfg: dict,
                      witnesses: dict) -> tuple[dict, list, list]:
    """B2 + Phase D amendment D-A1: rebuild the {knob: value} map of a multi-knob
    rule, in order of preference —

      1. a concrete absolute value (incl. CONFIG_UNSET: remove the knob);
      2. a concrete delta applied to the knob's CURRENT config value;
      3. the MEDIAN of the verified witness deltas (applied to the current value),
         else the median of the witness absolute values — every witness is a PASS
         source of this rule in the same situation; marked ``witness_selected``;
      4. otherwise unresolved (the caller refuses the action: a partial edit set
         is never executed).
    """
    edits: dict = {}
    unresolved: list = []
    selected: list = []
    for knob in knobs:
        abs_slot = after.get(f"rewrite.knob.{knob}.abs")
        delta_slot = after.get(f"rewrite.knob.{knob}.delta")
        absolute = _resolve(abs_slot, substitutions)
        delta = _resolve(delta_slot, substitutions)
        if absolute is not None and not is_hole(absolute):
            edits[knob] = str(absolute)
            continue
        current = _number(cfg.get(knob))
        if _number(delta) is not None and current is not None:
            edits[knob] = "%g" % (current + _number(delta))
            continue
        med_delta = _median(witnesses.get(delta_slot)) if is_hole(delta_slot) else None
        if med_delta is not None and current is not None:
            edits[knob] = "%g" % (current + med_delta)
            selected.append(knob)
            continue
        med_abs = _median(witnesses.get(abs_slot)) if is_hole(abs_slot) else None
        if med_abs is not None:
            edits[knob] = "%g" % med_abs
            selected.append(knob)
            continue
        unresolved.append(knob)
    return edits, unresolved, selected


def _resolve_categorical(unresolved: list, edits: dict, after: dict, witnesses: dict):
    """Phase F unblocker 2 (the default since Phase H): a holed NON-numeric knob
    (e.g. MIN_ROUTING_LAYER met1 vs met2) takes the most common verified witness value;
    on a tie it is LEFT OUT, as long as at least one knob remains. Returns
    (edits, still_unresolved, picked, dropped)."""
    edits, still, picked, dropped = dict(edits), [], [], []
    for knob in unresolved:
        values = [str(v) for v in (witnesses.get(after.get(f"rewrite.knob.{knob}.abs")) or [])
                  if v is not None]
        counts = {v: values.count(v) for v in values}
        best = sorted(counts.items(), key=lambda kv: -kv[1])
        if best and (len(best) == 1 or best[0][1] > best[1][1]):
            edits[knob] = best[0][0]
            picked.append(knob)
        elif best:
            dropped.append(knob)
        else:
            still.append(knob)
    if not edits:                      # never execute an empty action
        return {}, unresolved, [], []
    return edits, still, picked, dropped


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _median(values) -> float | None:
    """Median of the witness values, or None unless ALL are numeric."""
    nums = [_number(v) for v in (values or [])]
    if not nums or any(n is None for n in nums):
        return None
    nums.sort()
    mid = len(nums) // 2
    return nums[mid] if len(nums) % 2 else (nums[mid - 1] + nums[mid]) / 2


def _resolve(value, substitutions: dict):
    if isinstance(value, str) and is_hole(value):
        return substitutions.get(value)
    return value
