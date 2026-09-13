"""Exact source-binding replay shared by selection, construction and execution.

Eligibility here means unique static localization, never functional correctness,
Knowledge validation, Asset promotion, or production authorization.
"""
from collections.abc import Mapping
import copy

from .receipts import RuntimeBindingReceipt
from .structural_binding import CONTRACT as ALPHA_CONTRACT, bind_rtl_asset_to_source
from .guard_binding import CONTRACT as GUARD_CONTRACT
from tehm.ids import stable_dumps
from .registry import asset_content_digest
from .guard_binding import DOMAIN as GUARD_DOMAIN

SOURCE_CONTRACTS = frozenset({ALPHA_CONTRACT, GUARD_CONTRACT})


def source_contract(asset):
    definition = asset.get("definition") if isinstance(asset, Mapping) else None
    template = definition.get("binding_template") if isinstance(definition, Mapping) else None
    contract = template.get("contract") if isinstance(template, Mapping) else None
    return contract if isinstance(contract, str) and contract in SOURCE_CONTRACTS else None


def verify_source_copy(bound, registered):
    try:
        evidence = bound["provenance"]["binding_evidence"]
        if source_contract(registered) is None:
            return False
        digest = asset_content_digest(registered)
        if (digest is None or registered.get("content_digest") != digest or
                registered.get("asset_id") != "asset_" + digest.split(":", 1)[1][:24]):
            return False
        expected = bind_rtl_asset_to_source(registered, evidence["source"], design_id=evidence["design_id"])
        return stable_dumps(expected) == stable_dumps(bound)
    except (KeyError, TypeError, AttributeError, ValueError, NotImplementedError):
        return False


def source_runtime_binding(bound, knowledge_id):
    evidence = bound["provenance"]["binding_evidence"]
    provenance = bound["provenance"]
    payload = bound["definition"]["action"]["payload"]
    refs = provenance.get("mechanism_knowledge_ids") or ()
    if knowledge_id not in refs:
        raise ValueError("source binding is not linked to selected Knowledge")
    entities = tuple(sorted({str(payload[k]) for k in
        ("module", "source_state", "target_state", "add_condition") if k in payload}))
    return RuntimeBindingReceipt(asset_id=bound["asset_id"], knowledge_id=knowledge_id,
        target_design=evidence["design_id"], candidate_entities=entities,
        selected_binding=copy.deepcopy(payload), structural_evidence=(provenance["binding_digest"],),
        failure_evidence=(), ambiguity_count=0, eligible=True,
        reason="unique_source_only_static_binding", binding_digest=provenance["binding_digest"])


def verify_candidate_source_replay(candidate, source):
    replay = candidate.provenance.get("source_binding_replay")
    if replay is None:
        # A source-bound Asset may not downgrade to the legacy proof-less path.
        return (candidate.provenance.get("source_binding_required") is not True
                and candidate.concrete_action.get("domain") != GUARD_DOMAIN)
    try:
        registered, bound = replay["registered_asset"], replay["bound_asset"]
        evidence = bound["provenance"]["binding_evidence"]
        expected = bind_rtl_asset_to_source(registered, source, design_id=evidence["design_id"])
        return (verify_source_copy(bound, registered) and stable_dumps(expected) == stable_dumps(bound)
            and candidate.asset_id == registered["asset_id"]
            and candidate.concrete_action == bound["definition"]["action"]
            and candidate.knowledge_object_id in bound["provenance"].get("mechanism_knowledge_ids", ()))
    except (KeyError, TypeError, AttributeError, ValueError, NotImplementedError):
        return False
