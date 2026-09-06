"""Training-derived, evaluation-only numeric ORFS configuration transforms.

No tool execution or promotion occurs here. Target binding inspects existing
configuration values; it never accepts a proposed fix from the target query.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping

from tehm.causal.mechanism import load_transition_facts
from tehm.causal.replication import evaluate_replicated_effect
from tehm.ids import stable_dumps
from tehm.knowledge import get_knowledge_by_object_id, evaluate_knowledge_authority
from .receipts import RuntimeBindingReceipt
from .synthesis import AssetProposal

CONTRACT = "flow_numeric_config_v1"
_RANGES = {"CORE_UTILIZATION": (0, 100), "PLACE_DENSITY": (0, 1),
           "ROUTING_LAYER_ADJUSTMENT": (0, 1)}


def _digest(value):
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _numeric(key, value):
    if key not in _RANGES or type(value) not in (str, int, float):
        raise ValueError("unsupported numeric flow configuration")
    try:
        number = float(value)
    except ValueError as exc:
        raise ValueError("flow configuration must be numeric") from exc
    low, high = _RANGES[key]
    if not math.isfinite(number) or not low < number <= high:
        raise ValueError("flow configuration value is out of range")
    return number


def build_flow_asset_proposal(conn, knowledge_id: str, *, campaign_id: str) -> AssetProposal:
    """Extract one unanimous successful training action, not a new capability."""
    claim = get_knowledge_by_object_id(conn, knowledge_id)
    if claim.status != "validated" or not evaluate_knowledge_authority(conn, claim).eligible:
        raise ValueError("flow asset requires validated knowledge authority")
    transitions = set()
    for path_id in claim.causal_path_ids:
        if not evaluate_replicated_effect(
                conn, path_id, campaign_id=campaign_id, persist=False).eligible:
            raise ValueError("flow asset requires current training replication")
        row = conn.execute("SELECT source_transitions_json FROM tehm_causal_paths "
                           "WHERE path_id=?", (path_id,)).fetchone()
        transitions.update(json.loads(row[0]))
    facts = [load_transition_facts(conn, key) for key in sorted(transitions)]
    treatments = [f for f in facts if f.action.get("domain") != "flow.BASELINE_CONTROL"]
    for fact in treatments:
        require_hardware_oracle(fact.verifier)
    if (not treatments or any(f.action.get("domain") != "flow.CONFIG_DELTA" or
            f.outcome != "PASS" or f.action_digest not in
            claim.intervention.get("action_digests", ()) for f in treatments)):
        raise ValueError("flow asset requires successful claim-bound CONFIG_DELTA witnesses")
    if len({f.action_digest for f in treatments}) != 1:
        raise ValueError("flow asset training actions are ambiguous")
    action = json.loads(stable_dumps(treatments[0].action))
    edits = action["payload"].get("config_edits")
    if not isinstance(edits, dict) or not edits:
        raise ValueError("flow asset has no config edits")
    for key, value in edits.items():
        _numeric(key, value)
    # Keep only the executable configuration delta. Remaining source action
    # metadata is frozen in provenance, not interpreted as target evidence.
    concrete = {"domain": "flow.CONFIG_DELTA",
                "transformation_family": action["transformation_family"],
                "payload": {"config_edits": edits}}
    return AssetProposal(
        asset_type="FLOW_CONFIG_TRANSFORM", name="flow." + claim.knowledge_id,
        version=CONTRACT, definition={"action": concrete, "binding_contract": CONTRACT},
        input_contract={"required": ["flow_config", "flow_design_id"]},
        output_contract={"required": ["config_delta", "independent_oracle_receipt"]},
        verifier_contract={"independent": True, "obligations": [
            "ORFS_TARGET_PASS", "ORFS_NON_TARGET_NO_REGRESSION"]},
        compatibility={"target_scope": "global",
                       "compatibility_profile": claim.compatibility_profile},
        provenance={"generator_is_verifier": False, "binding_contract": CONTRACT,
            "bound_mechanism_family": claim.mechanism_family,
            "mechanism_knowledge_ids": [claim.object_id],
            "campaign_id": campaign_id, "training_action": action,
            "evidence_transitions": [f.transition_id for f in treatments],
            "claim_scope": "existing_training_action_reuse_only"})


def require_hardware_oracle(verifier: Mapping) -> None:
    """Configuration presence is an edit check, not hardware repair evidence.

Passing the generic causal replication gate does not upgrade the measured
objective. Keep such historical paths inspectable without using them to
bootstrap repair assets or challenge cohorts.
"""
    semantic = verifier.get("semantic_oracle")
    if not isinstance(semantic, Mapping):
        return
    specs = [semantic.get("spec")]
    for side in ("before", "after"):
        item = semantic.get(side)
        if isinstance(item, Mapping):
            specs.append(item.get("spec"))
    if any(isinstance(spec, Mapping) and spec.get("kind") == "config_presence"
           for spec in specs):
        raise ValueError("configuration presence is not hardware repair evidence")


def select_flow_binding(conn, asset: Mapping, knowledge_ids: set[str], context: Mapping):
    """Replay extraction from canonical witnesses before binding a target."""
    provenance = asset.get("provenance") or {}
    refs = provenance.get("mechanism_knowledge_ids")
    if not isinstance(refs, list) or len(refs) != 1 or refs[0] not in knowledge_ids:
        raise ValueError("flow asset knowledge binding mismatch")
    proposal = build_flow_asset_proposal(
        conn, refs[0], campaign_id=provenance.get("campaign_id"))
    expected = proposal.to_dict()
    for key in ("asset_type", "name", "version", "definition", "input_contract",
                "output_contract", "verifier_contract", "compatibility"):
        if stable_dumps(asset.get(key)) != stable_dumps(expected[key]):
            raise ValueError("flow asset differs from training-derived proposal")
    for key, value in proposal.provenance.items():
        if provenance.get(key) != value:
            raise ValueError("flow asset provenance differs from training witnesses")
    return bind_flow_config(asset, refs[0], context)


def bind_flow_config(asset: Mapping, knowledge_id: str, context: Mapping) -> RuntimeBindingReceipt:
    """Bind a fixed delta to observed numeric values; no action overrides."""
    config = context.get("flow_config")
    design = context.get("flow_design_id")
    if not isinstance(config, Mapping) or type(design) is not str or not design.strip():
        raise ValueError("flow binding requires target design and observed config")
    edits = asset["definition"]["action"]["payload"]["config_edits"]
    if not isinstance(edits, Mapping) or not edits:
        raise ValueError("flow binding has no config edits")
    changed = False
    for key, value in edits.items():
        changed |= _numeric(key, value) != _numeric(key, config.get(key))
    if not changed:
        raise ValueError("flow binding is a no-op on target configuration")
    witness = {"contract": CONTRACT, "asset_id": asset.get("asset_id"),
               "knowledge_id": knowledge_id, "design_id": design,
               "observed_config": dict(config), "config_edits": dict(edits)}
    return RuntimeBindingReceipt(
        asset_id=asset.get("asset_id"), knowledge_id=knowledge_id, target_design=design,
        candidate_entities=tuple(sorted(edits)), selected_binding={},
        structural_evidence=(_digest(witness),), failure_evidence=(),
        ambiguity_count=0, eligible=True, reason="fixed_training_config_delta",
        binding_digest=_digest(witness))
