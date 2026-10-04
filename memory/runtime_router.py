"""Backend-neutral runtime consultation helpers.

Runtime call sites must not import a concrete memory implementation.  This
module is the small Phase-1 router: it opens the process-locked backend and
adapts its candidates/proposals to the existing signoff strategy contract.
"""
from __future__ import annotations

from pathlib import Path
import sys

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
                       limit: int = 5, situation: dict | None = None,
                       backend=None, severity=None) -> list[dict]:
    """Return backend-attributed strategies for a signoff repair context.

    ``none`` naturally returns no candidates.  The legacy runtime continues to
    use its byte-compatible in-place ranking path, so this router is consulted
    only by the TEHM arm today.  Errors are fail-closed and are intentionally
    allowed to reach the call site for visible reporting (H12).
    """
    backend = backend or open_memory_backend()   # explicit backend: offline replay (Phase D1)
    if backend.name != "tehm":
        return []
    # Route fixes are stored as backend-stage evidence (check orfs_stage, class
    # route — r2g knowledge/symptom.py), so their rules match on orfs_stage; the
    # situation's violation_class then separates route from place/floorplan.
    context = RepairContext(
        project_dir=project_dir, design_id=design_id, platform=platform,
        check=("orfs_stage" if check == "route" else check), reports=reports,
        cfg=cfg, situation=situation,
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
            "transformation_family": (binding.get("transformation_family") or
                                      (candidate.payload or {}).get("transformation_family")),
            # D-A1: a candidate (not yet promoted) rule's application is a trial.
            "memory_lifecycle": binding.get("lifecycle_status"),
            "memory_trial": binding.get("lifecycle_status") != "promoted",
            "witness_selected": action_payload.get("witness_selected") or [],
            "categorical_selected": action_payload.get("categorical_selected") or [],
            "dropped_knobs": action_payload.get("dropped_knobs") or [],   # Phase F unblocker 2
            "value_tolerated": bool(binding.get("value_tolerated")),
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
        # B4: never hand the fix loop an action it cannot execute faithfully.
        reason = _reject_reason(strategy, action_payload)
        if reason:
            print(f"TEHM strategy {strategy['id']} rejected: {reason}", file=sys.stderr)
            continue
        strategy["recheck"] = check
        strategy["mechanisms"] = mechanisms(strategy)
        print("TEHM mechanisms: %s %s" % (strategy["id"], ",".join(strategy["mechanisms"])), file=sys.stderr)
        strategies.append(strategy)
    # Phase G2 compositions follow the rule strategies (the default since Phase H).
    strategies += _composition_strategies(backend, check, cfg, situation, severity)
    return strategies


def _composition_strategies(backend, check, cfg, situation, severity) -> list[dict]:
    """Phase G2: compositions of verified components (tehm.retrieval.compose), AFTER the
    rule strategies. Every composition is a trial (never promoted here); the knob policy
    and exclusion gates of the caller still apply."""
    from tehm.retrieval.compose import compositions
    from tehm.retrieval.vetoes import verified_outcomes
    conn, _ = backend._open()
    out = []
    for c in compositions(verified_outcomes(conn), situation or {}, cfg or {}, severity):
        out.append({
            "id": "tehm_" + c["id"], "source": "tehm_compose", "rule_id": c["id"],
            "rationale": "TEHM composition of verified components (%s, predicted severity %s)"
                         % (c["reason"], c["predicted_severity"]),
            "config_edits": c["edits"], "rerun_from": "floorplan", "recheck": check,
            "auto_apply": True, "transformation_family": "COMPOSE_" + "_".join(c["dims"]),
            "memory_trial": True, "witness_selected": [], "dropped_knobs": [],
            "composition": {"predicted_severity": c["predicted_severity"], "tested": c["tested"],
                            "components": c["components"], "current_severity": severity},
        })
        out[-1]["mechanisms"] = mechanisms(out[-1])
        print("TEHM mechanisms: %s %s" % (out[-1]["id"], ",".join(out[-1]["mechanisms"])), file=sys.stderr)
        print("TEHM composition proposed: %s %s predicted=%s tested=%s components=%d"
              % (out[-1]["id"], c["edits"], c["predicted_severity"], c["tested"], len(c["components"])),
              file=sys.stderr)
    return out


def mechanisms(strategy: dict) -> list[str]:
    """Phase H attribution: which memory mechanisms produced this strategy.

    rule_llm / rule_r2g / rule_component /   what kind of stored rule (crystallised from LLM fixes,
    rule_knob_subset                         from r2g's own fixes, from component trials, or a
                                             knob-subset projection)
    trial_candidate                          a not-yet-promoted rule applied as a checked trial
    value_median / value_categorical         a holed knob filled from verified witnesses
    categorical_drop                         a tied categorical knob left out
    v4_tolerance                             the rule passed V4 only thanks to value tolerance
    compose_tested / compose_untested        a composition of verified components
    (The knowledge SOURCE of a rule's evidence is resolved offline from the store; the path --
    diagnose or B6 -- and the safety gates are logged by the caller.)"""
    if strategy.get("source") == "tehm_compose":
        return ["compose_tested" if (strategy.get("composition") or {}).get("tested") else "compose_untested"]
    family = str(strategy.get("transformation_family") or "")
    tags = ["rule_knob_subset" if family.startswith("KNOB_SUBSET") else
            "rule_llm" if family.startswith("LLM_EDIT") else
            "rule_component" if family.startswith("COMPONENT_") else "rule_r2g"]
    if strategy.get("memory_trial"):
        tags.append("trial_candidate")
    if strategy.get("witness_selected"):
        tags.append("value_median")
    if strategy.get("categorical_selected"):
        tags.append("value_categorical")
    if strategy.get("dropped_knobs"):
        tags.append("categorical_drop")
    if strategy.get("value_tolerated"):
        tags.append("v4_tolerance")
    return tags


def signoff_vetoes(*, situation: dict | None, backend=None) -> dict[str, dict]:
    """B5: {strategy: evidence} vetoed in this situation (TEHM backend only)."""
    backend = backend or open_memory_backend()
    if backend.name != "tehm" or not situation:
        return {}
    return backend.situation_vetoes(situation)


def _reject_reason(strategy: dict, action_payload: dict) -> str | None:
    if action_payload.get("unresolved_knobs"):
        return "unfilled knob(s) " + ",".join(action_payload["unresolved_knobs"])
    edits = strategy.get("config_edits")
    if not isinstance(edits, dict) or not edits:
        return "empty config_edits"
    if any(v is None or (isinstance(v, str) and v.startswith("$H"))
           for v in edits.values()):
        return "unbound hole in config_edits"
    return None


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
