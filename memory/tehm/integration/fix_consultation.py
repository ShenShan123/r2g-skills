"""TEHM fix consultation (design doc 20.7 / 20.8 / 28.4 additive seam).

When ``R2G_MEMORY_BACKEND=tehm`` the signoff fix router (diagnose_signoff_fix)
consults the TEHM rule store for admissible rules matching the current repair
state and adapts each APPLICABLE rule into a diagnose-compatible strategy
proposal marked ``source='tehm_rule'`` (design doc 28.4 attribution — a memory
hit is never dressed up as a cold-start hit).

The strategies are ordered by the TEHM retrieval score; the caller prepends them
ahead of the shared cold-start catalog (the TEHM arm's authority). Fail-closed:
any TEHM error degrades to the shared base policy, never a fabricated rule.
"""
from __future__ import annotations

import sqlite3

from contracts import RepairContext
from tehm.activation.binding import bind_rule
from tehm.activation.instantiate import instantiate_rewrite
from tehm.retrieval.index import build_index
from tehm.retrieval.pipeline import retrieve
from tehm.retrieval.result import APPLICABLE

CONSULTATION_VERSION = "fix-consultation-v0.1"


def tehm_strategies_for(conn: sqlite3.Connection, *, check: str,
                        design_id: str | None, platform: str | None,
                        cfg: dict, drc: dict, lvs: dict,
                        limit: int = 3) -> list[dict]:
    """Retrieve admissible rules and adapt each applicable one into a strategy."""
    context = RepairContext(
        check=check, design_id=design_id, platform=platform, cfg=cfg,
        reports={"drc": drc or {}, "lvs": lvs or {}})
    receipt = retrieve(conn, context, limit=limit)
    # Revalidate the same production authority when resolving receipt IDs back
    # to rule definitions.  An intervening demotion or malformed lifecycle row
    # must not turn a previously retrieved ID into a live strategy.
    index = build_index(conn, lifecycle_statuses=frozenset({"promoted"}))

    strategies: list[dict] = []
    for r in receipt.results:
        if r.applicability_status != APPLICABLE:
            continue                      # symbolic veto is final (9.5)
        rule = index.get(r.rule_id)
        if rule is None:
            continue
        binding = bind_rule(rule, context)   # concrete slots bound; holes left
        action = instantiate_rewrite(rule, binding, context)
        payload = action.get("payload") or {}
        strategies.append({
            "id": f"tehm_{r.transformation_family.lower()}",
            "rationale": (f"TEHM rule {r.rule_id} (sim={r.similarity:.2f}, "
                          f"score={r.score:.3f})"),
            "config_edits": payload.get("config_edits") or {},
            "sdc_edits": {},
            "rerun_from": payload.get("rerun_from"),
            "recheck": payload.get("recheck"),
            "source": "tehm_rule",
            "rule_id": r.rule_id,
            "tehm_score": r.score,
            "binding_status": binding.status,
        })
    # highest retrieval score first (the TEHM arm's authority ordering).
    strategies.sort(key=lambda s: s["tehm_score"], reverse=True)
    return strategies
