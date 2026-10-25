"""Situation-scoped negative rules (R2G memory redesign B5, 2026-10-01).

``veto(strategy, situation)``: a flow/signoff strategy is vetoed in a situation
when the verified evidence says it does not work THERE —

  * at least ``min_failures`` verified FAIL episodes (no_change or regression;
    adapter B1 ``oracle_complete`` + verdict FAIL) in exactly that situation,
  * spread over at least ``min_designs`` distinct designs (lineages), and
  * strictly more failures than verified PASS episodes in the same situation.

Scope is the situation (r2g ``knowledge/situation.py`` sit-v1) — never a project
path or design class. Vetoes are DERIVED on read from the verified transition
corpus (the same learner partition crystallisation uses), not persisted as rule
rows, so they can never drift from their evidence; every veto carries its
evidence so the caller can log why a strategy was dropped.
"""
from __future__ import annotations

import sqlite3

from tehm import db as tehm_db
from tehm.verified_execution import require_verified_transition

VETO_MIN_FAILURES = 2
VETO_MIN_DESIGNS = 2


def situation_vetoes(conn: sqlite3.Connection, situation: dict | None, *,
                     campaign_id: str = "live",
                     min_failures: int = VETO_MIN_FAILURES,
                     min_designs: int = VETO_MIN_DESIGNS) -> dict[str, dict]:
    """{strategy: evidence} for every strategy vetoed in ``situation``."""
    if not isinstance(situation, dict) or not situation:
        return {}
    return decide_vetoes(verified_outcomes(conn, campaign_id=campaign_id), situation,
                         min_failures=min_failures, min_designs=min_designs)


def verified_outcomes(conn: sqlite3.Connection, *, campaign_id: str = "live") -> list[dict]:
    """Every learner-eligible, verified (oracle-complete PASS/FAIL) flow/signoff outcome:
    {transition_id, design, strategy, verdict, situation, tier}."""
    rows = conn.execute(
        "SELECT t.transition_id, t.action_json, t.verifier_json, s.lineage_id "
        "FROM tehm_transitions t JOIN tehm_states s ON s.state_id=t.source_state_id "
        "WHERE EXISTS (SELECT 1 FROM tehm_dataset_membership dm "
        "WHERE dm.transition_id=t.transition_id AND dm.campaign_id=? "
        "AND dm.split='training' AND dm.learner_eligible=1)", (campaign_id,)).fetchall()
    out = []
    for tid, action_json, verifier_json, lineage in rows:
        action = tehm_db.read_json(action_json) or {}
        payload = action.get("payload") or {}
        verifier = tehm_db.read_json(verifier_json) or {}
        verdict = verifier.get("verdict")
        if (not isinstance(payload.get("situation"), dict)
                or verifier.get("oracle_complete") is not True or verdict not in ("PASS", "FAIL")):
            continue
        try:
            require_verified_transition(conn, tid)
        except ValueError:
            continue
        strategy = str(payload.get("strategy") or action.get("transformation_family") or "")
        if strategy:
            graded = payload.get("graded_severity") or {}
            out.append({"transition_id": tid, "design": lineage or tid, "strategy": strategy,
                        "verdict": verdict, "situation": payload["situation"],
                        "tier": payload.get("evidence_tier") or "live",
                        "knob_edits": payload.get("knob_edits") or {},
                        "rerun_from": payload.get("rerun_from"),
                        "severity_before": graded.get("before"),
                        "severity_after": graded.get("after")})
    return out


def decide_vetoes(outcomes, situation: dict, *, min_failures: int = VETO_MIN_FAILURES,
                  min_designs: int = VETO_MIN_DESIGNS) -> dict[str, dict]:
    """The ONE veto decision. A strategy is vetoed when the verified evidence in exactly
    this situation has >= min_failures FAILs on >= min_designs designs and more FAILs than
    PASSes."""
    tally: dict[str, dict] = {}
    for o in outcomes:
        if o["situation"] != situation:
            continue
        t = tally.setdefault(o["strategy"], {"fail": 0, "pass": 0, "fail_designs": set(),
                                             "transitions": []})
        if o["verdict"] == "FAIL":
            t["fail"] += 1
            t["fail_designs"].add(o["design"])
            t["transitions"].append(o["transition_id"])
        else:
            t["pass"] += 1
    vetoes: dict[str, dict] = {}
    for strategy, t in sorted(tally.items()):
        if not (t["fail"] >= min_failures and len(t["fail_designs"]) >= min_designs
                and t["fail"] > t["pass"]):
            continue
        vetoes[strategy] = {"failures": t["fail"], "passes": t["pass"],
                            "designs": sorted(t["fail_designs"]),
                            "transitions": sorted(t["transitions"])}
    return vetoes
