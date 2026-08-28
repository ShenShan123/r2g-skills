"""Capability-gap detection from learner-eligible canonical evidence.

Gap detection is a receipt-producing diagnostic.  It never creates an asset,
changes a rule status, or consumes calibration/held-out evidence.
"""
from __future__ import annotations

import hashlib
import sqlite3
from collections import defaultdict

from tehm import db as tehm_db
from tehm.causal.mechanism import load_transition_facts
from tehm.ids import stable_dumps

from .receipts import CapabilityGapReceipt
from .registry import get_asset, get_asset_status


def _promoted_asset_profiles(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        """SELECT DISTINCT s.asset_id, s.target_scope
             FROM tehm_asset_status s
            WHERE s.status='promoted'"""
    ).fetchall()
    profiles: set[str] = set()
    for row in rows:
        try:
            status = get_asset_status(
                conn, asset_id=str(row["asset_id"]),
                target_scope=str(row["target_scope"]))
        except ValueError:
            # A malformed lifecycle row is untrusted and must not cover a
            # capability gap merely because its raw status column says
            # ``promoted``.
            continue
        if status is None or status["status"] != "promoted":
            continue
        # Gap detection is a learner-facing consumer.  Do not let a status
        # row or JSON blob bypass the registry's content-addressed checks:
        # get_asset() validates every contract, provenance JSON and digest.
        asset = get_asset(conn, str(row["asset_id"]))
        if asset is None:
            continue
        compatibility = asset.get("compatibility")
        if not isinstance(compatibility, dict):
            continue
        profile = (compatibility.get("compatibility_profile") or
                   compatibility.get("profile"))
        if profile:
            profiles.add(str(profile))
    return profiles


def _promoted_rule_families(conn: sqlite3.Connection) -> set[str]:
    # A raw ``status='promoted'`` value is not enough to establish coverage:
    # lifecycle metadata is derived state and may be malformed in a copied or
    # externally edited database.  Reuse the lifecycle reader before a
    # promoted rule can suppress a capability-gap receipt.
    from tehm.lifecycle.rule_status import RuleLifecycleError, get_status

    promoted_rule_ids: set[str] = set()
    for row in conn.execute(
            "SELECT rule_id, target_scope FROM tehm_rule_status "
            "WHERE status='promoted'"):
        try:
            status = get_status(
                conn, rule_id=row["rule_id"], target_scope=row["target_scope"])
        except RuleLifecycleError:
            continue
        if status is not None and status["status"] == "promoted":
            promoted_rule_ids.add(row["rule_id"])
    if not promoted_rule_ids:
        return set()
    placeholders = ",".join("?" for _ in promoted_rule_ids)
    rows = conn.execute(
        f"""SELECT e.mechanism_family
              FROM tehm_rule_sources rsrc
              JOIN tehm_episodes e ON e.episode_id=rsrc.episode_id
             WHERE rsrc.rule_id IN ({placeholders})
               AND e.mechanism_family IS NOT NULL""",
        tuple(sorted(promoted_rule_ids))).fetchall()
    return {str(row["mechanism_family"]) for row in rows
            if row["mechanism_family"]}


def detect_capability_gaps(
    conn: sqlite3.Connection,
    *,
    campaign_id: str = "live",
    min_lineages: int = 2,
    min_failures: int = 2,
) -> list[CapabilityGapReceipt]:
    if not campaign_id:
        raise ValueError("campaign_id is required")
    if min_lineages < 1 or min_failures < 1:
        raise ValueError("gap thresholds must be positive")
    rows = conn.execute(
        """SELECT t.transition_id
             FROM tehm_transitions t
            WHERE EXISTS (SELECT 1 FROM tehm_dataset_membership dm
                            WHERE dm.transition_id=t.transition_id
                              AND dm.campaign_id=? AND dm.split='training'
                              AND dm.learner_eligible=1)
            ORDER BY t.transition_id""", (campaign_id,)).fetchall()
    groups: dict[tuple[str, str | None], list] = defaultdict(list)
    for row in rows:
        facts = load_transition_facts(conn, row["transition_id"])
        profile = facts.compatibility_profile
        groups[(facts.mechanism_family, profile)].append(facts)
    promoted_profiles = _promoted_asset_profiles(conn)
    promoted_families = _promoted_rule_families(conn)
    receipts: list[CapabilityGapReceipt] = []
    for (family, profile), facts in sorted(groups.items(), key=lambda item: str(item[0])):
        transition_ids = tuple(sorted(facts_item.transition_id for facts_item in facts))
        lineages = tuple(sorted({facts_item.lineage_id for facts_item in facts
                                 if facts_item.lineage_id}))
        failures = tuple(sorted({facts_item.transition_id for facts_item in facts
                                 if facts_item.outcome in {"FAIL", "REGRESSION"}}))
        # A verified repair transition carries the failed source condition in
        # ``original_failure=REMOVED``.  Count that as failure evidence for a
        # missing-asset diagnosis, but keep unresolved post-action failures
        # separate so a successful repair is not mislabeled
        # ``repeated_executable_failure``.
        initial_failures = tuple(sorted({facts_item.transition_id for facts_item in facts
                                         if facts_item.delta.get(
                                             "original_failure") in {"PRESENT", "REMOVED"}}))
        failure_evidence = tuple(sorted(set(failures) | set(initial_failures)))
        covered_by_asset = bool(profile and profile in promoted_profiles)
        covered_by_rule = family in promoted_families
        reasons: list[str] = []
        if len(lineages) >= min_lineages and not covered_by_asset and not covered_by_rule:
            reasons.append("repeated_unsupported_mechanism")
        if len(failures) >= min_failures:
            reasons.append("repeated_executable_failure")
        if len(lineages) >= min_lineages and profile and not covered_by_asset:
            reasons.append("structural_coverage_gap")
        obligation_gap = any(
            item.delta.get("created_regressions") or item.delta.get("newly_observed_failures")
            for item in facts)
        if obligation_gap and not covered_by_rule:
            reasons.append("obligation_coverage_gap")
        if not reasons:
            continue
        reasons = sorted(set(reasons))
        evidence_lineages = lineages
        identity = {
            "campaign_id": campaign_id, "family": family, "profile": profile,
            "transitions": transition_ids, "lineages": evidence_lineages,
            "reasons": reasons,
        }
        gap_id = "gap_" + hashlib.sha1(stable_dumps(identity).encode()).hexdigest()[:20]
        support = min(1.0, len(lineages) / max(float(min_lineages), 1.0))
        failure_support = min(1.0, len(failure_evidence) /
                              max(float(min_failures), 1.0))
        confidence = round(0.6 * support + 0.4 * failure_support, 6)
        asset_type = ("RTL_REWRITE_TEMPLATE" if any(
            str(item.action.get("domain", "")).startswith("rtl.") for item in facts)
                      else "REPAIR_OPERATOR")
        receipts.append(CapabilityGapReceipt(
            gap_id=gap_id, mechanism_family=family,
            compatibility_profile=profile,
            evidence_transitions=transition_ids,
            evidence_lineages=evidence_lineages,
            missing_asset_types=(asset_type,),
            reason="+".join(reasons),
            current_action_coverage={
                "promoted_asset": covered_by_asset,
                "promoted_rule": covered_by_rule,
                "observed": len(facts), "failures": len(failures),
                "initial_failure_evidence": len(initial_failures),
                "failure_evidence": len(failure_evidence),
            }, confidence=confidence))
    return receipts


detect_capability_gap = detect_capability_gaps


__all__ = ["detect_capability_gap", "detect_capability_gaps"]
