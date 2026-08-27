"""Backend-neutral runtime consultation helpers.

Runtime call sites must not import a concrete memory implementation.  This
module is the small Phase-1 router: it opens the process-locked backend and
adapts its candidates/proposals to the existing signoff strategy contract.
"""
from __future__ import annotations

from pathlib import Path

from contracts import RepairContext
from factory import open_memory_backend


def ingest_project(project_dir: Path) -> dict:
    """Route project evidence ingestion to exactly one backend authority."""
    backend = open_memory_backend()
    if backend.name == "none":
        return {"backend": "none", "record_id": f"none:{project_dir.name}",
                "receipts": 0}
    receipts = backend.ingest_project(project_dir)
    if backend.name == "legacy":
        return {"backend": "legacy", "record_id": receipts.record_id,
                "receipts": 1}
    backend.rebuild()
    return {
        "backend": "tehm",
        "record_id": (receipts[-1].transition_id if receipts else
                      f"tehm:{project_dir.name}"),
        "receipts": len(receipts),
    }


def signoff_strategies(*, project_dir: Path, check: str, design_id: str | None,
                       platform: str | None, cfg: dict, reports: dict,
                       limit: int = 5) -> list[dict]:
    """Return backend-attributed strategies for a signoff repair context.

    ``none`` naturally returns no candidates.  The legacy runtime continues to
    use its byte-compatible in-place ranking path, so this router is consulted
    only by the TEHM arm today.  Errors are fail-closed and are intentionally
    allowed to reach the call site for visible reporting (H12).
    """
    backend = open_memory_backend()
    if backend.name != "tehm":
        return []
    context = RepairContext(
        project_dir=project_dir, design_id=design_id, platform=platform,
        check=check, reports=reports, cfg=cfg,
    )
    query = backend.build_query(context)
    strategies: list[dict] = []
    for candidate in backend.retrieve(query, limit=limit):
        proposal = backend.propose_activation(candidate, context)
        if proposal is None or proposal.applicability_status != "APPLICABLE":
            continue
        binding = proposal.binding or {}
        action = binding.get("action") or binding.get("rewrite") or binding
        if not isinstance(action, dict):
            continue
        action_payload = action.get("payload") or action
        payload = candidate.payload or {}
        strategy = {
            "id": f"tehm_{candidate.candidate_id}",
            "source": "tehm_rule",
            "rule_id": candidate.candidate_id,
            "rationale": "TEHM validity-gated typed rule",
            "config_edits": action_payload.get("config_edits") or {},
            "rerun_from": action_payload.get("rerun_from"),
            "recheck": action_payload.get("recheck") or check,
            "auto_apply": True,
            "tehm_score": candidate.score,
            "activation_id": proposal.activation_id,
            "obligation_coverage": proposal.obligation_coverage,
        }
        # Current TEHM candidates also expose their synthesized skill payload;
        # retain its executable fields when the proposal binding is structural.
        skill = payload.get("rule") or payload.get("skill") or payload
        if isinstance(skill, dict):
            execution = skill.get("execution") or {}
            rewrite = skill.get("rewrite") or {}
            strategy["config_edits"] = (strategy["config_edits"] or
                                        rewrite.get("config_edits") or {})
            strategy["rerun_from"] = (strategy["rerun_from"] or
                                      execution.get("rerun_from"))
            strategy["recheck"] = (strategy["recheck"] or
                                   execution.get("recheck") or check)
        strategies.append(strategy)
    return strategies


def config_recommendation(*, project_dir: Path, design_id: str | None,
                          platform: str | None, cfg: dict,
                          reports: dict | None = None) -> dict | None:
    """Return the highest-ranked executable TEHM config proposal, if any.

    Initial configuration has no failure check, so the query uses the explicit
    ``config`` action scope.  A store without an applicable config rule honestly
    returns ``None`` and the shared static policy remains authoritative.
    """
    backend = open_memory_backend()
    if backend.name != "tehm":
        return None
    context = RepairContext(
        project_dir=project_dir, design_id=design_id, platform=platform,
        check="config", reports=reports or {}, cfg=cfg,
    )
    query = backend.build_query(context)
    for candidate in backend.retrieve(query, limit=3):
        proposal = backend.propose_activation(candidate, context)
        if proposal is None or proposal.applicability_status != "APPLICABLE":
            continue
        action = (proposal.binding or {}).get("action") or {}
        payload = action.get("payload") or {}
        edits = payload.get("config_edits") or {}
        if edits:
            return {
                "config_edits": edits,
                "source": "tehm_rule",
                "rule_id": candidate.candidate_id,
                "activation_id": proposal.activation_id,
                "score": candidate.score,
                "obligation_coverage": proposal.obligation_coverage,
            }
    return None
