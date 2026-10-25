"""Promotion authority (design doc 24.3, 20.11).

TEHM promotion must come from: a real executable A/B trial, sufficient obligation
coverage, no hard regression, an unchanged status version, and arms that actually
did different work. Any violation => the trial is refused (no lifecycle change).
"""
from __future__ import annotations

import sqlite3

from tehm.lifecycle.rule_status import get_status, set_status

AUTHORITY_VERSION = "lifecycle-authority-v0.1"
MIN_OBLIGATION_COVERAGE = 1.0


def apply_production_trial_verdict(conn: sqlite3.Connection, **kwargs) -> str | None:
    """Apply a production lifecycle verdict with the complete gate firewall.

    ``apply_trial_verdict`` remains source-compatible for deterministic legacy
    fixtures, but production adapters must use this wrapper.  A production
    promotion additionally requires a database-bound rule authority receipt;
    a caller-supplied gate map is only diagnostic and can never grant
    authority.  Omitting any of rollback, registry, obligation, cross-lineage
    TE, harmful-rate, or conformal-coverage evidence therefore fails closed
    before a ``promoted`` row can be written.
    """
    kwargs["strict_promotion_gates"] = True
    return apply_trial_verdict(conn, **kwargs)


def apply_trial_verdict(conn: sqlite3.Connection, *, rule_id: str,
                        target_scope: str, verdict: str,
                        obligation_coverage: float | None,
                        created_regressions: list,
                        arms_differ: bool,
                        expected_status_version: int,
                        provenance: dict | None = None,
                        promotion_gates: dict | None = None,
                        authority_receipt=None,
                        strict_promotion_gates: bool = False) -> str | None:
    """Apply an A/B verdict to the rule lifecycle; None = no change (refused).

    Returns the new status (``promoted`` / ``demoted``) or None.
    """
    current = get_status(conn, rule_id=rule_id, target_scope=target_scope)
    if current is None or current["status_version"] != expected_status_version:
        return None                          # stale trial / no lifecycle row
    if not arms_differ:
        return None                          # arms did identical work (24.3)
    if created_regressions:
        return None                          # hard regression (24.3)
    if verdict == "win":
        if obligation_coverage is None or \
                obligation_coverage < MIN_OBLIGATION_COVERAGE:
            return None                      # insufficient coverage (24.3)
        # Existing direct/unit callers remain source-compatible.  All
        # production executors opt into strict mode and must provide the
        # complete rollback/registry/TE/harmful/conformal conjunction.
        if strict_promotion_gates:
            # The map is retained for backwards-compatible diagnostics, but a
            # production decision must be re-derived from immutable DB-bound
            # evidence.  This blocks a forged all-True payload from changing
            # lifecycle state.
            if authority_receipt is None:
                return None
            from tehm.lifecycle.rule_authority import verify_rule_authority

            authority = verify_rule_authority(conn, authority_receipt)
            if (not authority["eligible"] or
                    authority.get("rule_id") != rule_id or
                    authority.get("target_scope") != target_scope or
                    authority.get("status_version") != expected_status_version):
                return None
            gate_report = authority.get("gate_report") or {}
            if gate_report.get("eligible") is not True:
                return None
        set_status(conn, rule_id=rule_id, target_scope=target_scope,
                   status="promoted",
                   provenance={"authority": "ab_trial",
                               **(provenance or {}),
                               **({"promotion_gates": gate_report}
                                  if strict_promotion_gates else {})})
        return "promoted"
    if verdict == "loss":
        set_status(conn, rule_id=rule_id, target_scope=target_scope,
                   status="demoted",
                   provenance={"authority": "ab_trial", **(provenance or {})})
        return "demoted"
    return None                              # inconclusive -> unchanged
