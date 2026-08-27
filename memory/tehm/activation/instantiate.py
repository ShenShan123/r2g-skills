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


def _resolve(value, substitutions: dict):
    if isinstance(value, str) and is_hole(value):
        return substitutions.get(value)
    return value
