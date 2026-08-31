"""Narrow, auditable RTL asset proposals.

This is a template builder, not arbitrary code generation.  The resulting
definition names an existing parser-backed executor and remains unregistered
until the caller explicitly submits it to Asset Memory.
"""
from __future__ import annotations

from dataclasses import dataclass
import copy
import hashlib
import json
from pathlib import Path
from collections.abc import Mapping, Sequence

from tehm.ids import stable_dumps
from tehm.rtl.compatibility import profile_for_action
from tehm.rtl.rtl_actions import RTL_ACTION_DOMAINS
from tehm.rtl.verilog_parse import parse_verilog

from .registry import register_asset
from .receipts import CapabilityGapReceipt, AssetReceipt

RTL_ASSET_VERSION = "rtl-rewrite-asset-v0.1"


@dataclass(frozen=True)
class AssetProposal:
    asset_type: str
    name: str
    version: str
    definition: dict
    input_contract: dict
    output_contract: dict
    verifier_contract: dict
    compatibility: dict
    provenance: dict
    promotion_eligible: bool = False

    def to_dict(self) -> dict:
        return {
            "asset_type": self.asset_type, "name": self.name,
            "version": self.version, "definition": self.definition,
            "input_contract": self.input_contract,
            "output_contract": self.output_contract,
            "verifier_contract": self.verifier_contract,
            "compatibility": self.compatibility,
            "provenance": self.provenance,
            "promotion_eligible": self.promotion_eligible,
        }


def build_rtl_asset_proposal(
    gap: CapabilityGapReceipt | dict,
    *,
    name: str,
    transformation_family: str,
    action_payload_template: dict,
    compatibility_profile: str,
    verifier_obligations: tuple[str, ...] | list[str],
    creator: str = "tehm-asset-synthesizer",
    mechanism_knowledge_ids: Sequence[str] | None = None,
) -> AssetProposal:
    gap_dict = gap.to_dict() if hasattr(gap, "to_dict") else dict(gap or {})
    domain = str(action_payload_template.get("domain") or "")
    if domain not in RTL_ACTION_DOMAINS:
        raise ValueError(f"asset proposal requires supported RTL action domain: {domain!r}")
    if not name or not transformation_family or not compatibility_profile:
        raise ValueError("asset proposal name/family/profile are required")
    payload = dict(action_payload_template)
    payload["domain"] = domain
    payload["compatibility_profile"] = profile_for_action(payload)
    if payload["compatibility_profile"] != compatibility_profile:
        raise ValueError("action payload profile does not match asset compatibility")
    if any(isinstance(value, str) and value.startswith("$H")
           for value in payload.values()):
        raise ValueError("asset template holes must be bound before executable validation")
    obligations = tuple(sorted(set(str(item) for item in verifier_obligations)))
    if not obligations:
        raise ValueError("asset proposal needs verifier obligations")
    knowledge_ids = tuple(sorted({str(item).strip() for item in
                                  (mechanism_knowledge_ids or ())}))
    if any(not item or "@" not in item for item in knowledge_ids):
        raise ValueError(
            "mechanism_knowledge_ids must contain knowledge object IDs (knowledge_id@version)")
    provenance = {
        "creator": creator, "generator_is_verifier": False,
        "gap_id": gap_dict.get("gap_id"),
        "evidence_transitions": list(gap_dict.get("evidence_transitions", [])),
        "asset_version": RTL_ASSET_VERSION,
    }
    # Knowledge binding is optional at proposal time so old fixture proposals
    # remain replayable.  The P7 strict selector requires this field before an
    # asset can become a knowledge-grounded advisory candidate.
    if knowledge_ids:
        provenance["mechanism_knowledge_ids"] = list(knowledge_ids)
    return AssetProposal(
        asset_type="RTL_REWRITE_TEMPLATE", name=name, version=RTL_ASSET_VERSION,
        definition={
            "executor": "tehm.rtl.rtl_actions.apply_rtl_action",
            "action": {"domain": domain, "transformation_family": transformation_family,
                       "payload": payload},
            "template_kind": "parser_backed_rtl_rewrite",
        },
        input_contract={
            "required": ["rtl_source", "structural_graph", "failure_trace"],
            "compatibility_profile": compatibility_profile,
        },
        output_contract={
            "required": ["rtl_source", "parser_graph_delta", "edit_receipt"],
            "preserves": ["module_parseability"],
        },
        verifier_contract={
            "independent": True,
            "obligations": list(obligations),
            "oracle_types": ["TARGET_TEST", "REGRESSION", "FORMAL"],
            "runner": "registered_external_oracle",
        },
        compatibility={
            "compatibility_profile": compatibility_profile,
            "action_domain": domain,
        },
        provenance=provenance,
        promotion_eligible=False)


def register_asset_proposal(conn, proposal: AssetProposal,
                            *, target_scope: str | None = None,
                            commit: bool = True) -> AssetReceipt:
    if proposal.promotion_eligible:
        raise ValueError("asset proposals cannot be promotion eligible")
    return register_asset(
        conn, asset_type=proposal.asset_type, name=proposal.name,
        version=proposal.version, definition=proposal.definition,
        input_contract=proposal.input_contract,
        output_contract=proposal.output_contract,
        verifier_contract=proposal.verifier_contract,
        compatibility=proposal.compatibility, provenance=proposal.provenance,
        target_scope=target_scope, commit=commit)


def bind_rtl_asset_to_project(asset: Mapping, project: Path | str, *,
                              expected_mechanism_family: str | None = None) -> dict:
    """Bind a parser-backed RTL template to one concrete project context.

    A proposal is deliberately abstract at the Asset Memory boundary, while
    the existing RTL executor requires concrete module/state/signal slots.
    Binding therefore consumes only the project manifest's typed ``fix``
    payload and verifies its domain, transformation family, compatibility
    profile, and parser-visible module before returning an executable copy.
    It never edits the project or registers/promotes an asset.
    """
    if not isinstance(asset, Mapping):
        raise TypeError("asset must be a mapping")
    project = Path(project).resolve()
    try:
        manifest = json.loads((project / "manifest.json").read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"project manifest is unavailable: {project}") from exc
    if not isinstance(manifest, dict):
        raise ValueError("project manifest must be an object")
    if expected_mechanism_family is not None and str(
            manifest.get("mechanism_family") or "") != str(expected_mechanism_family):
        raise ValueError(
            "project mechanism family is incompatible with the asset gap: "
            f"{manifest.get('mechanism_family')!r} != {expected_mechanism_family!r}")
    definition = asset.get("definition")
    if not isinstance(definition, Mapping):
        raise ValueError("asset definition is missing")
    action = definition.get("action")
    if not isinstance(action, Mapping):
        raise ValueError("asset definition.action is missing")
    if not isinstance(action.get("payload"), Mapping):
        raise ValueError("asset definition.action.payload is missing")
    fix = manifest.get("fix")
    if not isinstance(fix, Mapping):
        raise ValueError("project manifest.fix is missing")
    domain = str(action.get("domain") or "")
    fix_domain = str(fix.get("domain") or domain)
    if fix_domain != domain or domain not in RTL_ACTION_DOMAINS:
        raise ValueError(
            f"project fix domain {fix_domain!r} is incompatible with asset {domain!r}")
    transformation_family = str(action.get("transformation_family") or "")
    fix_family = str(fix.get("transformation_family") or transformation_family)
    if transformation_family and fix_family != transformation_family:
        raise ValueError(
            "project transformation family is incompatible with the asset: "
            f"{fix_family!r} != {transformation_family!r}")
    profile = str((asset.get("compatibility") or {}).get(
        "compatibility_profile") or "")
    if not profile:
        raise ValueError("asset compatibility_profile is missing")
    payload = {key: fix[key] for key in (
        "module", "source_state", "target_state", "add_condition", "reg",
        "target", "replacement", "count", "reset_signal", "signal",
        "case_expr", "higher_label", "lower_label") if key in fix}
    payload["domain"] = domain
    payload["compatibility_profile"] = profile_for_action({
        "domain": domain, "compatibility_profile": profile})
    if payload["compatibility_profile"] != profile:
        raise ValueError("project binding profile does not match asset compatibility")
    rtl_files = sorted((project / "rtl").glob("*.v"))
    if not rtl_files:
        raise ValueError(f"project has no rtl/*.v: {project}")
    modules = parse_verilog(rtl_files[0].read_text())
    module_name = str(payload.get("module") or "")
    if not module_name or not any(module.name == module_name for module in modules):
        raise ValueError(f"bound module is not parser-visible: {module_name!r}")
    bound = copy.deepcopy(dict(asset))
    bound_definition = bound.setdefault("definition", {})
    bound_action = bound_definition.setdefault("action", {})
    bound_action["payload"] = payload
    binding_digest = "sha256:" + hashlib.sha256(stable_dumps({
        "asset_id": asset.get("asset_id"),
        "project": str(project),
        "design": manifest.get("design"),
        "mechanism_family": manifest.get("mechanism_family"),
        "payload": payload,
    }).encode()).hexdigest()
    provenance = dict(bound.get("provenance") or {})
    provenance.update({
        "bound_project": str(project),
        "bound_design": manifest.get("design"),
        "bound_mechanism_family": manifest.get("mechanism_family"),
        "binding_contract": "manifest_fix_v1",
        "binding_digest": binding_digest,
    })
    bound["provenance"] = provenance
    return bound


synthesize_rtl_asset = build_rtl_asset_proposal
synthesize_asset = build_rtl_asset_proposal


__all__ = ["AssetProposal", "RTL_ASSET_VERSION", "bind_rtl_asset_to_project",
           "build_rtl_asset_proposal", "register_asset_proposal",
           "synthesize_asset", "synthesize_rtl_asset"]
