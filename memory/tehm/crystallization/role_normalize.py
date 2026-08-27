"""Role-normalized rewrite (design doc 6.4 Step 4, 6.6).

Every verified transition is projected onto a fixed, role-aligned slot schema
so multiple episodes can be anti-unified. For the flow/signoff v1 domain the
slots are:

    match.target_check       check name (drc / lvs / timing / ...)
    match.knob               the config knob the action rewrote
    rewrite.value            the new knob value
    execution.rerun_from     stage the flow re-ran from
    execution.recheck        check re-verified after the rewrite
    verification.verdict     target oracle verdict after the action
    verification.oracle_type oracle that produced the verdict

Structural identity (knob/check/stage names) rides as slot VALUES so that
anti-unification can hole them when they differ across episodes — this is how a
``PLACE_DENSITY_LB_ADDON`` fix and a ``ROUTE_DENSITY_LAYER_ADDON`` fix that share
an effect crystallize into ONE rule with ``$KNOB``.
"""
from __future__ import annotations

from dataclasses import dataclass

from tehm.crystallization.effects import effect_key_from_transition_dict

ROLE_NORMALIZE_VERSION = "role-normalize-v0.1"


@dataclass(frozen=True)
class RoleNormalizedRewrite:
    """One transition projected onto the shared slot schema."""

    effect_key: str
    domain: str
    action_domain: str
    transformation_family: str
    slots: tuple  # ordered (path, value) pairs
    obligations: tuple
    outcome: str
    episode_id: str
    transition_id: str
    lineage_id: str | None = None

    def slot_dict(self) -> dict:
        return dict(self.slots)

    def to_dict(self) -> dict:
        return {
            "effect_key": self.effect_key,
            "domain": self.domain,
            "action_domain": self.action_domain,
            "transformation_family": self.transformation_family,
            "slots": list(self.slots),
            "obligations": list(self.obligations),
            "outcome": self.outcome,
            "episode_id": self.episode_id,
            "transition_id": self.transition_id,
            "lineage_id": self.lineage_id,
        }


def normalize_rewrite(transition_dict: dict, *, effect_key: str | None = None,
                      episode_id: str | None = None,
                      lineage_id: str | None = None) -> RoleNormalizedRewrite:
    """Project a transition's canonical dict onto the role-normalized slots.

    ``transition_dict`` has the ``to_dict`` shape (action / observation_delta /
    verifier / provenance / outcome / transition_id).
    """
    action = transition_dict.get("action") or {}
    delta = transition_dict.get("observation_delta") or {}
    verifier = transition_dict.get("verifier") or {}
    payload = action.get("payload") or {}

    config_edits = payload.get("config_edits") or {}
    knob = min(config_edits) if isinstance(config_edits, dict) and config_edits else None
    new_value = config_edits.get(knob) if knob is not None else None

    rerun_from = payload.get("rerun_from")
    recheck = payload.get("recheck") or _scope_check(verifier)
    verdict = verifier.get("verdict")
    oracle = verifier.get("oracle_type")
    domain = str(transition_dict.get("domain") or "")
    if not domain:
        # derive from the action domain when the transition row lacks it.
        action_domain = str(action.get("domain") or "")
        domain = "rtl" if action_domain.startswith("rtl.") else "flow.signoff"

    slots: list[tuple[str, object]] = []
    if recheck:
        slots.append(("match.target_check", str(recheck)))
    if knob is not None:
        slots.append(("match.knob", str(knob)))
    if new_value is not None:
        slots.append(("rewrite.value", new_value))
    if rerun_from:
        slots.append(("execution.rerun_from", str(rerun_from)))
    if recheck:
        slots.append(("execution.recheck", str(recheck)))
    if verdict:
        slots.append(("verification.verdict", str(verdict)))
    if oracle:
        slots.append(("verification.oracle_type", str(oracle)))
    # RTL action domains (design doc 26 Phase 10): the edit parameters ride the
    # slots so two guard-strengthen fixes on different states crystallize with
    # $SRC/$DST/$COND holes instead of collapsing into a concrete repeat.
    if str(action.get("domain", "")).startswith("rtl."):
        rtl_payload = action.get("payload") or {}
        profile = rtl_payload.get("compatibility_profile")
        if profile:
            slots.append(("match.compatibility_profile", str(profile)))
        for key in ("module", "reset_signal", "signal", "case_expr",
                    "higher_label", "lower_label", "source_state",
                    "target_state", "add_condition", "reg", "target",
                    "replacement", "count"):
            value = rtl_payload.get(key)
            if value is not None and value != "":
                slots.append((f"rtl.{key}", str(value)))

    return RoleNormalizedRewrite(
        effect_key=effect_key or effect_key_from_transition_dict(transition_dict),
        domain=domain,
        action_domain=str(action.get("domain") or "unknown"),
        transformation_family=str(action.get("transformation_family") or "unknown"),
        slots=tuple(slots),
        obligations=tuple(_obligations(delta, verifier)),
        outcome=str(transition_dict.get("outcome") or "UNKNOWN"),
        episode_id=str(episode_id or (transition_dict.get("provenance") or {})
                       .get("episode_id") or ""),
        transition_id=str(transition_dict.get("transition_id") or ""),
        lineage_id=lineage_id,
    )


def _obligations(delta: dict, verifier: dict) -> list[str]:
    obligations: list[str] = []
    if delta.get("original_failure") == "REMOVED":
        obligations.append("TARGET_FAILURE_REMOVED")
    if verifier.get("oracle_type") == "REGRESSION":
        obligations.append("PRESERVE_FROZEN_REGRESSION")
    if not obligations:
        obligations.append("VERIFIER_" + str(verifier.get("oracle_type", "UNKNOWN")))
    return sorted(obligations)


def _scope_check(verifier: dict) -> str | None:
    scope = str(verifier.get("scope") or "")
    if scope.startswith("signoff:"):
        return scope[len("signoff:"):]
    return None
