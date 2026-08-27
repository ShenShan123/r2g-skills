"""Database-bound authority for rule promotion.

The six rule-promotion gates are deliberately not a caller-owned boolean map.
This module records the evidence rows that establish each gate, binds them to
the current rule and candidate status version, and re-derives the conjunction
on every verification.  It is therefore safe to use as the only production
bridge from an A/B trial to ``tehm_rule_status.status='promoted'``.

The ledger is an additive v4 extension.  It is created with individual DDL
statements so an older v4 database can opt in without implicitly committing an
outer transaction.  Failed or incomplete authority attempts are recorded as
ineligible receipts; they never mutate rule lifecycle state.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from tehm import db as tehm_db
from tehm.causal.transfer_ledger import (
    load_causal_transfer_receipt, verify_causal_transfer)
from tehm.ids import stable_dumps

from .promotion_gates import PROMOTION_GATE_VERSION, REQUIRED_GATES, evaluate_promotion_gates
from .rule_status import get_status, set_status


AUTHORITY_VERSION = "rule-promotion-authority-v1"
RULE_EVIDENCE_TYPES = {
    gate: f"rule_gate:{gate}" for gate in REQUIRED_GATES
}
EVIDENCE_SPLITS = frozenset({"training", "calibration", "heldout", "ab"})

# A promotion claim must not use a training row to masquerade as a held-out
# transfer or calibration result.  These are evidence-plane restrictions, not
# production lifecycle statuses.
GATE_ALLOWED_SPLITS = {
    "rollback_verified": frozenset({"training", "ab"}),
    "registry_verified": frozenset({"training", "ab"}),
    "obligation_coverage": frozenset({"training", "ab"}),
    "cross_lineage_te": frozenset({"heldout", "ab"}),
    "harmful_rate": frozenset({"calibration", "heldout", "ab"}),
    "conformal_coverage": frozenset({"calibration"}),
}

_RULE_CONTENT_FIELDS = (
    "domain", "before_pattern_json", "after_pattern_json",
    "hard_preconditions_json", "context_profile_json", "obligations_json",
    "validity_status", "validity_profile_json", "confidence_json",
    "utility_json", "risk_profile_json", "predicate_schema_version",
    "role_schema_version", "crystallizer_version", "merge_trace_digest",
)
_RULE_JSON_FIELDS = frozenset({
    "before_pattern_json", "after_pattern_json", "hard_preconditions_json",
    "context_profile_json", "obligations_json", "validity_profile_json",
    "confidence_json", "utility_json", "risk_profile_json",
})


@dataclass(frozen=True)
class RuleAuthorityReceipt:
    """Content-addressed, DB-bound rule promotion authority receipt."""

    rule_id: str
    target_scope: str
    status_version: int | None
    rule_content_digest: str
    trial_id: str | None
    authority_version: str
    eligible: bool
    checks: dict
    gate_status: dict[str, str]
    missing: tuple[str, ...]
    failed: tuple[str, ...]
    not_established: tuple[str, ...]
    evidence_refs: dict[str, list[dict]] = field(default_factory=dict)
    evidence: dict[str, list[dict]] = field(default_factory=dict)
    reasons: tuple[str, ...] = ()
    authority_receipt_id: str = ""
    receipt_digest: str = ""
    payload: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "target_scope": self.target_scope,
            "status_version": self.status_version,
            "rule_content_digest": self.rule_content_digest,
            "trial_id": self.trial_id,
            "authority_version": self.authority_version,
            "eligible": self.eligible,
            "checks": dict(self.checks),
            "gate_status": dict(self.gate_status),
            "missing": list(self.missing),
            "failed": list(self.failed),
            "not_established": list(self.not_established),
            "evidence_refs": self.evidence_refs,
            "evidence": self.evidence,
            "reasons": list(self.reasons),
            "authority_receipt_id": self.authority_receipt_id,
            "receipt_digest": self.receipt_digest,
            "payload": self.payload,
        }


def _as_dict(value) -> dict:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    if not isinstance(value, Mapping):
        raise TypeError("rule authority receipt must be a mapping")
    return dict(value)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _ensure_rule_authority_schema(conn: sqlite3.Connection) -> None:
    """Create the additive rule-authority ledger without implicit commits."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS tehm_rule_authority_evidence (
            rule_id         TEXT NOT NULL,
            target_scope    TEXT NOT NULL,
            gate_name       TEXT NOT NULL,
            evidence_id     TEXT NOT NULL,
            split           TEXT NOT NULL CHECK (split IN
                            ('training', 'calibration', 'heldout', 'ab')),
            lineage_id      TEXT,
            verdict         TEXT NOT NULL,
            payload_json    TEXT NOT NULL,
            evidence_digest TEXT NOT NULL,
            PRIMARY KEY (rule_id, target_scope, gate_name, evidence_id)
        )""")
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_rule_authority_evidence_scope
            ON tehm_rule_authority_evidence
               (rule_id, target_scope, gate_name, split, verdict)""")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS tehm_rule_authority_receipts (
            authority_receipt_id TEXT PRIMARY KEY,
            rule_id             TEXT NOT NULL,
            target_scope        TEXT NOT NULL,
            status_version      INTEGER,
            eligible            INTEGER NOT NULL CHECK (eligible IN (0, 1)),
            receipt_json        TEXT NOT NULL,
            receipt_digest      TEXT NOT NULL UNIQUE,
            created_at          TEXT NOT NULL
        )""")
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_rule_authority_receipts_scope
            ON tehm_rule_authority_receipts(rule_id, target_scope, eligible)""")


def _rule_row(conn: sqlite3.Connection, rule_id: str):
    return conn.execute("SELECT * FROM tehm_rules WHERE rule_id=?", (rule_id,)).fetchone()


def _rule_content_digest(row) -> str | None:
    if row is None:
        return None
    content = {}
    for field_name in _RULE_CONTENT_FIELDS:
        try:
            raw = row[field_name]
        except (KeyError, IndexError):
            return None
        if field_name in _RULE_JSON_FIELDS:
            try:
                value = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                return None
        else:
            value = raw
        content[field_name] = value
    return "sha256:" + hashlib.sha256(stable_dumps(content).encode()).hexdigest()


def _json_mapping(raw) -> dict:
    if isinstance(raw, Mapping):
        return dict(raw)
    try:
        value = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


def _normalise_entries(values, *, gate: str) -> tuple[list[dict], list[str]]:
    """Normalise gate evidence while retaining malformed-input diagnostics."""
    if values is None:
        return [], []
    if isinstance(values, Mapping):
        values = [values]
    elif isinstance(values, (str, bytes)):
        return [], [f"{gate}:evidence_not_iterable"]
    try:
        iterator = iter(values)
    except TypeError:
        return [], [f"{gate}:evidence_not_iterable"]
    entries: list[dict] = []
    errors: list[str] = []
    for ordinal, raw in enumerate(iterator):
        if not isinstance(raw, Mapping):
            errors.append(f"{gate}:entry_{ordinal}_malformed")
            continue
        evidence_id = str(raw.get("evidence_id") or "")
        split = str(raw.get("split") or "")
        verdict = str(raw.get("verdict") or "")
        lineage_id = raw.get("lineage_id")
        if not evidence_id or not split or not verdict:
            errors.append(f"{gate}:entry_{ordinal}_incomplete")
            continue
        if split not in EVIDENCE_SPLITS:
            errors.append(f"{gate}:invalid_split")
        elif split not in GATE_ALLOWED_SPLITS[gate]:
            errors.append(f"{gate}:invalid_evidence_split")
        payload = raw.get("payload")
        if isinstance(payload, Mapping):
            payload = dict(payload)
        else:
            payload = {
                str(key): value for key, value in raw.items()
                if key not in {"evidence_id", "split", "verdict", "lineage_id"}
            }
        entries.append({
            "evidence_id": evidence_id,
            "split": split,
            "lineage_id": lineage_id,
            "verdict": verdict,
            "payload": payload,
        })
        if verdict != "PASS":
            errors.append(f"{gate}:evidence_verdict_not_pass")
    return entries, errors


def _finite_number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _payload_number(entry: Mapping, names: tuple[str, ...]) -> float | None:
    payload = entry.get("payload")
    if not isinstance(payload, Mapping):
        payload = {}
    for name in names:
        value = _finite_number(payload.get(name))
        if value is not None:
            return value
    return _finite_number(entry.get("value"))


def _receipt_ids(values) -> tuple[str, ...]:
    """Normalize an explicit causal-transfer receipt selection."""
    if isinstance(values, (str, bytes)) or values is None:
        raise ValueError("causal transfer receipt IDs must be a sequence")
    try:
        result = tuple(str(value).strip() for value in values)
    except TypeError as exc:
        raise ValueError(
            "causal transfer receipt IDs must be a sequence") from exc
    if not result or any(not value for value in result):
        raise ValueError("causal transfer receipt IDs must be non-empty")
    if len(set(result)) != len(result):
        raise ValueError("causal transfer receipt IDs contain duplicates")
    return tuple(sorted(result))


def _rule_binding_fields(row) -> tuple[set[str], set[str]]:
    """Extract mechanism families and action domains from a rule definition."""
    families: set[str] = set()
    domains: set[str] = set()
    if row is None:
        return families, domains
    for field_name in ("before_pattern_json", "after_pattern_json"):
        try:
            pattern = _json_mapping(row[field_name])
        except (KeyError, IndexError, TypeError):
            pattern = {}
        for key in ("type", "transformation_family", "mechanism_family"):
            value = pattern.get(key)
            if isinstance(value, str) and value:
                families.add(value)
        value = pattern.get("action_domain")
        if isinstance(value, str) and value:
            domains.add(value)
    return families, domains


def _rule_source_transition_ids(
        conn: sqlite3.Connection, rule_id: str) -> tuple[str, ...]:
    """Read transition witnesses attached to one crystallized rule."""
    if not _table_exists(conn, "tehm_rule_sources"):
        return ()
    rows = conn.execute(
        "SELECT source_substitution_json FROM tehm_rule_sources "
        "WHERE rule_id=? ORDER BY episode_id", (rule_id,)).fetchall()
    transition_ids: set[str] = set()
    for row in rows:
        substitutions = _json_mapping(row["source_substitution_json"])
        transition_ids.update(
            str(value) for value in substitutions.keys() if str(value))
    return tuple(sorted(transition_ids))


def _bind_transfer_to_rule(
        conn: sqlite3.Connection, ledger, *, rule_id: str,
        rule_row, rule_digest: str | None) -> tuple[dict | None, list[str]]:
    """Require an L4 receipt to witness the selected rule's mechanism.

    A valid transfer receipt is not universal authority for every candidate:
    its path family, held-out action domain, training campaign, and source
    transition witnesses must agree with the rule currently being evaluated.
    """
    errors: list[str] = []
    path = conn.execute(
        "SELECT mechanism_family FROM tehm_causal_paths WHERE path_id=?",
        (ledger.path_id,)).fetchone()
    if path is None:
        return None, ["cross_lineage_te:rule_binding_path_missing"]
    path_family = str(path["mechanism_family"] or "")
    families, rule_domains = _rule_binding_fields(rule_row)
    if not path_family or path_family not in families:
        errors.append("cross_lineage_te:rule_binding_mechanism_mismatch")

    transfer_ids = tuple(str(value) for value in
                         (ledger.transfer_receipt.get(
                             "transfer_transition_ids") or ()) if str(value))
    transfer_domains: set[str] = set()
    for transition_id in transfer_ids:
        transition = conn.execute(
            "SELECT action_domain, action_json FROM tehm_transitions "
            "WHERE transition_id=?", (transition_id,)).fetchone()
        if transition is None:
            errors.append("cross_lineage_te:rule_binding_transfer_transition_missing")
            continue
        action_domain = str(transition["action_domain"] or "")
        if action_domain:
            transfer_domains.add(action_domain)
        action = _json_mapping(transition["action_json"])
        family = action.get("transformation_family")
        if family and str(family) != path_family:
            errors.append("cross_lineage_te:rule_binding_transition_family_mismatch")
    if rule_domains and (not transfer_domains or
                         not transfer_domains.issubset(rule_domains)):
        errors.append("cross_lineage_te:rule_binding_action_domain_mismatch")

    source_ids = _rule_source_transition_ids(conn, rule_id)
    training_ids = tuple(str(value) for value in
                         (ledger.transfer_receipt.get(
                             "training_transition_ids") or ()) if str(value))
    if not source_ids:
        errors.append("cross_lineage_te:rule_binding_sources_missing")
    elif not set(source_ids).issubset(set(training_ids)):
        errors.append("cross_lineage_te:rule_binding_training_sources_mismatch")
    for transition_id in source_ids:
        membership = conn.execute(
            "SELECT 1 FROM tehm_dataset_membership "
            "WHERE transition_id=? AND campaign_id=? AND split='training' "
            "AND learner_eligible=1 LIMIT 1",
            (transition_id, ledger.training_campaign_id)).fetchone()
        if membership is None:
            errors.append("cross_lineage_te:rule_binding_source_firewall_mismatch")
    if errors:
        return None, errors
    binding = {
        "rule_id": rule_id,
        "rule_content_digest": rule_digest,
        "path_mechanism_family": path_family,
        "rule_action_domains": sorted(rule_domains),
        "transfer_action_domains": sorted(transfer_domains),
        "rule_source_transition_ids": list(source_ids),
    }
    return binding, []


def _transfer_entry(
        conn: sqlite3.Connection, receipt_id: str, *, rule_id: str | None = None,
        rule_row=None, rule_digest: str | None = None,
        ) -> tuple[dict | None, list[str]]:
    """Build one cross-lineage gate row from a replay-verified L4 receipt.

    The receipt's convenience projection is never trusted: verification
    replays the path and transfer witnesses against the same database before
    an authority evidence row is created.  Invalid or merely negative
    transfer receipts are returned as diagnostics and cannot satisfy the gate.
    """
    errors: list[str] = []
    try:
        ledger = load_causal_transfer_receipt(conn, receipt_id)
    except (TypeError, ValueError, sqlite3.Error) as exc:
        return None, [f"cross_lineage_te:transfer_receipt_malformed:{exc}"]
    if ledger is None:
        return None, ["cross_lineage_te:transfer_receipt_missing"]
    try:
        checked = verify_causal_transfer(conn, ledger.to_dict())
    except (TypeError, ValueError, KeyError, sqlite3.Error) as exc:
        return None, [f"cross_lineage_te:transfer_replay_error:{exc}"]
    if checked.get("verified") is not True:
        return None, [
            "cross_lineage_te:transfer_receipt_unverified:" +
            ",".join(str(x) for x in checked.get("reasons") or ())]
    transfer = ledger.transfer_receipt
    if (checked.get("eligible") is not True or
            checked.get("evidence_level") != "L4_TRANSFER_SUPPORTED_MECHANISM"):
        return None, ["cross_lineage_te:transfer_receipt_not_l4"]
    training_lineages = tuple(sorted({str(x) for x in
                                      transfer.get("training_lineages") or ()
                                      if str(x)}))
    transfer_lineages = tuple(sorted({str(x) for x in
                                      transfer.get("transfer_lineages") or ()
                                      if str(x)}))
    if len(training_lineages) < 2 or not transfer_lineages:
        return None, ["cross_lineage_te:transfer_lineage_witness_insufficient"]
    rule_binding = None
    if rule_id is not None:
        rule_binding, binding_errors = _bind_transfer_to_rule(
            conn, ledger, rule_id=rule_id, rule_row=rule_row,
            rule_digest=rule_digest)
        if binding_errors:
            return None, binding_errors
    payload = {
        "causal_transfer_verified": True,
        "transfer_supported": True,
        "te_pass": True,
        "evidence_level": "L4_TRANSFER_SUPPORTED_MECHANISM",
        "transfer_receipt_id": ledger.transfer_receipt_id,
        "path_id": ledger.path_id,
        "path_digest": ledger.path_digest,
        "training_campaign_id": ledger.training_campaign_id,
        "transfer_campaign_id": ledger.transfer_campaign_id,
        "training_lineages": list(training_lineages),
        "transfer_lineages": list(transfer_lineages),
        "training_lineage_count": len(training_lineages),
        "transfer_lineage_count": len(transfer_lineages),
        "require_full_oracle": ledger.require_full_oracle,
    }
    if rule_binding is not None:
        payload["rule_binding"] = rule_binding
    return {
        "evidence_id": ledger.transfer_receipt_id,
        "split": "heldout",
        # A transfer receipt may cover several held-out rows.  The complete
        # lineage vectors remain in the signed payload; this scalar is only a
        # legacy index field and is deterministic for a singleton case.
        "lineage_id": transfer_lineages[0],
        "verdict": "PASS",
        "payload": payload,
    }, errors


def build_causal_transfer_evidence(
        conn: sqlite3.Connection, receipt_ids: Iterable[str], *,
        rule_id: str | None = None) -> list[dict]:
    """Return authority-ready rows for replay-verified L4 receipts.

    This helper is intentionally strict.  A caller that wants an ineligible
    transfer recorded as a negative attempt should call
    :func:`record_rule_authority` without this helper and provide ordinary
    evidence rows; a transfer selected for a cross-lineage gate must be
    verified against the current shadow ledger first.  Supplying ``rule_id``
    additionally binds each receipt to that rule's mechanism, action domain,
    training witnesses, and content digest.
    """
    ids = _receipt_ids(receipt_ids)
    rule_row = _rule_row(conn, rule_id) if rule_id is not None else None
    rule_digest = _rule_content_digest(rule_row) if rule_id is not None else None
    if rule_id is not None and (rule_row is None or rule_digest is None):
        raise ValueError("cross_lineage_te:rule_binding_rule_missing")
    entries: list[dict] = []
    errors: list[str] = []
    for receipt_id in ids:
        entry, entry_errors = _transfer_entry(
            conn, receipt_id, rule_id=rule_id, rule_row=rule_row,
            rule_digest=rule_digest)
        errors.extend(entry_errors)
        if entry is not None:
            entries.append(entry)
    if errors:
        raise ValueError("; ".join(sorted(set(errors))))
    return entries


def _derive_gate_inputs(
    entries: Mapping[str, list[dict]],
    errors: Iterable[str],
    *,
    rule_row,
    status: Mapping | None,
    expected_status_version: int | None,
    rule_digest: str | None,
    min_obligation_coverage: float,
    min_cross_lineage_te: float,
    max_harmful_rate: float,
    min_conformal_coverage: float,
) -> tuple[dict, dict]:
    """Derive gate inputs and audit metrics solely from recorded evidence."""
    gate_inputs: dict = {}
    details: dict = {"errors": sorted(set(errors))}

    rollback = entries.get("rollback_verified", [])
    if rollback:
        gate_inputs["rollback_verified"] = all(
            item.get("verdict") == "PASS" and
            (item.get("payload") or {}).get("verified") is True
            for item in rollback)
    details["rollback_count"] = len(rollback)

    registry = entries.get("registry_verified", [])
    if registry:
        registry_ok = bool(
            rule_row is not None and status is not None and
            status.get("status") == "candidate" and
            expected_status_version is not None and
            status.get("status_version") == expected_status_version and
            rule_digest)
        for item in registry:
            payload = item.get("payload") or {}
            registry_ok = registry_ok and item.get("verdict") == "PASS" and (
                payload.get("rule_content_digest") == rule_digest and
                payload.get("status_version") == expected_status_version and
                payload.get("status") == "candidate")
        gate_inputs["registry_verified"] = registry_ok
    details["registry_count"] = len(registry)

    obligation = entries.get("obligation_coverage", [])
    obligation_values = [
        value for item in obligation
        for value in [_payload_number(item, ("obligation_coverage", "coverage"))]
        if value is not None
    ]
    if obligation:
        gate_inputs["obligation_coverage"] = (
            min(obligation_values) if len(obligation_values) == len(obligation)
            and obligation_values else None)
    details["obligation_coverages"] = obligation_values

    transfer = entries.get("cross_lineage_te", [])
    lineages = sorted({str(item.get("lineage_id")) for item in transfer
                       if item.get("lineage_id") not in (None, "")})
    transfer_pass = []
    transfer_training_lineages: set[str] = set()
    transfer_heldout_lineages: set[str] = set()
    verified_transfer_rows = []
    for item in transfer:
        payload = item.get("payload") or {}
        supported = payload.get("te_pass")
        if supported is None:
            supported = payload.get("transfer_supported")
        if supported is None:
            coverage = _payload_number(item, ("te", "coverage"))
            supported = coverage is not None and coverage >= min_cross_lineage_te
        transfer_pass.append(item.get("verdict") == "PASS" and supported is True)
        if payload.get("causal_transfer_verified") is True:
            verified_transfer_rows.append(item)
            transfer_training_lineages.update(
                str(value) for value in payload.get("training_lineages") or ()
                if str(value))
            transfer_heldout_lineages.update(
                str(value) for value in payload.get("transfer_lineages") or ()
                if str(value))
    if transfer:
        if verified_transfer_rows:
            # A replay-verified L4 receipt carries the complete training and
            # held-out lineage vectors.  One such receipt is sufficient to
            # establish the transfer gate because the underlying evaluator
            # already proves L3 replication plus a disjoint held-out witness.
            gate_inputs["cross_lineage_te"] = (
                1.0 if all(transfer_pass) and
                len(transfer_training_lineages) >= 2 and
                bool(transfer_heldout_lineages) else 0.0)
        else:
            # Legacy direct evidence retains the measured singleton failure:
            # it cannot establish transfer even if its boolean says PASS.
            gate_inputs["cross_lineage_te"] = (
                sum(transfer_pass) / len(transfer_pass)
                if len(lineages) >= 2 else 0.0)
    details["cross_lineage_lineages"] = lineages
    details["cross_lineage_count"] = len(transfer)
    details["causal_transfer_count"] = len(verified_transfer_rows)
    details["causal_transfer_training_lineages"] = sorted(
        transfer_training_lineages)
    details["causal_transfer_heldout_lineages"] = sorted(
        transfer_heldout_lineages)

    utility = entries.get("harmful_rate", [])
    harmful_values: list[bool] = []
    for item in utility:
        payload = item.get("payload") or {}
        if isinstance(payload.get("harmful"), bool):
            harmful_values.append(payload["harmful"])
        elif str(payload.get("utility_verdict") or "").upper() in {
                "HARMFUL", "REGRESSION"}:
            harmful_values.append(True)
        elif str(payload.get("utility_verdict") or "").upper() in {
                "PARETO_SAFE", "SUPPORT", "NEUTRAL", "PASS"}:
            harmful_values.append(False)
    if utility:
        gate_inputs["harmful_rate"] = (
            sum(harmful_values) / len(harmful_values)
            if len(harmful_values) == len(utility) else None)
    details["harmful_count"] = sum(harmful_values)
    details["utility_count"] = len(utility)

    conformal = entries.get("conformal_coverage", [])
    covered = 0.0
    total = 0.0
    direct: list[float] = []
    conformal_malformed = False
    for item in conformal:
        payload = item.get("payload") or {}
        covered_value = _finite_number(payload.get("covered"))
        total_value = _finite_number(payload.get("total"))
        if covered_value is not None and total_value is not None and total_value > 0:
            covered += covered_value
            total += total_value
            continue
        value = _payload_number(item, ("conformal_coverage", "coverage"))
        if value is None:
            conformal_malformed = True
        else:
            direct.append(value)
    if conformal:
        if conformal_malformed:
            gate_inputs["conformal_coverage"] = None
        elif total > 0:
            gate_inputs["conformal_coverage"] = covered / total
        elif direct:
            gate_inputs["conformal_coverage"] = sum(direct) / len(direct)
        else:
            gate_inputs["conformal_coverage"] = None
    details["conformal_coverage"] = (
        gate_inputs.get("conformal_coverage") if conformal else None)
    # Keep thresholds in the derived evidence so a replayer can detect a
    # receipt generated under a different policy.
    details["thresholds"] = {
        "obligation_coverage": float(min_obligation_coverage),
        "cross_lineage_te": float(min_cross_lineage_te),
        "harmful_rate": float(max_harmful_rate),
        "conformal_coverage": float(min_conformal_coverage),
    }
    # Any malformed/forbidden row makes the corresponding measured gate fail
    # closed even if another row in the same cohort happens to pass.
    error_gates = {str(error).split(":", 1)[0] for error in errors}
    for gate in error_gates:
        if gate not in REQUIRED_GATES:
            continue
        if gate in {"rollback_verified", "registry_verified"}:
            gate_inputs[gate] = False
        else:
            gate_inputs[gate] = None
    return gate_inputs, details


def _evidence_digest(*, rule_id: str, target_scope: str, gate_name: str,
                     evidence_id: str, split: str, lineage_id, verdict: str,
                     payload: Mapping) -> str:
    value = {
        "rule_id": rule_id, "target_scope": target_scope,
        "gate_name": gate_name, "evidence_id": evidence_id,
        "split": split, "lineage_id": lineage_id, "verdict": verdict,
        "payload": dict(payload),
    }
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _trial_binding(conn: sqlite3.Connection, *, rule_id: str,
                   target_scope: str, trial_id: str | None,
                   expected_status_version: int | None) -> tuple[bool, dict, list[str]]:
    reasons: list[str] = []
    if not trial_id:
        return False, {}, ["trial_evidence_required"]
    row = conn.execute(
        "SELECT * FROM tehm_trials WHERE trial_id=? OR trial_uuid=? "
        "ORDER BY created_at DESC LIMIT 1", (trial_id, trial_id)).fetchone()
    if row is None:
        return False, {}, ["trial_evidence_missing"]
    metrics = _json_mapping(row["metrics_json"])
    binding = {
        "trial_id": row["trial_id"],
        "trial_uuid": row["trial_uuid"],
        "rule_id": row["rule_id"],
        "target_scope": row["target_scope"],
        "status_version": row["status_version"],
        "verdict": row["verdict"],
        "metrics": metrics,
    }
    digest = "sha256:" + hashlib.sha256(stable_dumps(binding).encode()).hexdigest()
    binding["trial_digest"] = digest
    if row["rule_id"] != rule_id or row["target_scope"] != target_scope:
        reasons.append("trial_rule_scope_mismatch")
    if expected_status_version is None or row["status_version"] != expected_status_version:
        reasons.append("trial_status_version_mismatch")
    if row["verdict"] != "win":
        reasons.append("trial_verdict_not_win")
    if metrics.get("arms_differ") is not True:
        reasons.append("trial_arms_not_different")
    if metrics.get("created_regressions"):
        reasons.append("trial_created_regression")
    coverage = _finite_number(metrics.get("obligation_coverage"))
    if coverage is None or coverage < 1.0:
        reasons.append("trial_obligation_coverage_insufficient")
    return not reasons, binding, reasons


def _receipt_digest(payload: Mapping) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(dict(payload)).encode()).hexdigest()


def _receipt_id(receipt_digest: str) -> str:
    return "rule_authority_" + receipt_digest.split(":", 1)[1][:20]


def _insert_evidence_row(conn: sqlite3.Connection, *, rule_id: str,
                         target_scope: str, gate_name: str, entry: Mapping,
                         digest: str) -> None:
    payload_json = stable_dumps(entry["payload"])
    existing = conn.execute(
        """SELECT split, lineage_id, verdict, payload_json, evidence_digest
             FROM tehm_rule_authority_evidence
            WHERE rule_id=? AND target_scope=? AND gate_name=? AND evidence_id=?""",
        (rule_id, target_scope, gate_name, entry["evidence_id"])).fetchone()
    values = (entry["split"], entry.get("lineage_id"), entry["verdict"],
              payload_json, digest)
    if existing is not None:
        if tuple(existing) != values:
            raise ValueError("rule authority evidence is immutable and conflicts")
        return
    conn.execute(
        """INSERT INTO tehm_rule_authority_evidence
           (rule_id, target_scope, gate_name, evidence_id, split, lineage_id,
            verdict, payload_json, evidence_digest)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (rule_id, target_scope, gate_name, entry["evidence_id"], *values))


def _payload_from_entries(entries: Mapping[str, list[dict]]) -> dict[str, list[dict]]:
    return {
        gate: [
            {key: value for key, value in item.items() if key != "payload"}
            | {"payload": dict(item.get("payload") or {})}
            for item in entries.get(gate, [])
        ]
        for gate in REQUIRED_GATES
    }


def record_rule_authority(
    conn: sqlite3.Connection,
    *,
    rule_id: str,
    target_scope: str,
    evidence: Mapping[str, Iterable[Mapping] | Mapping] | None,
    trial_id: str | None,
    expected_status_version: int | None = None,
    min_obligation_coverage: float = 1.0,
    min_cross_lineage_te: float = 1.0,
    max_harmful_rate: float = 0.0,
    min_conformal_coverage: float = 0.80,
    causal_transfer_receipt_ids: Iterable[str] | None = None,
) -> RuleAuthorityReceipt:
    """Record independently supplied evidence and derive all six gates.

    ``evidence`` contains payload-bearing rows keyed by gate.  No gate score or
    boolean is accepted as authority input: values are derived from those rows
    and the current rule/status/trial rows.  ``trial_id`` is mandatory for an
    eligible receipt and binds the conjunction to a real winning A/B trial.
    When ``causal_transfer_receipt_ids`` is supplied, the cross-lineage gate is
    constructed only from replay-verified L4 ledger receipts in this same DB;
    hand-authored cross-lineage rows are rejected rather than merged.
    """
    if not isinstance(evidence, Mapping):
        evidence = {}
    row = _rule_row(conn, rule_id)
    rule_digest = _rule_content_digest(row)
    status = get_status(conn, rule_id=rule_id, target_scope=target_scope)
    if expected_status_version is None and status is not None:
        expected_status_version = int(status["status_version"])
    entries: dict[str, list[dict]] = {}
    errors: list[str] = []
    for gate in REQUIRED_GATES:
        values, gate_errors = _normalise_entries(evidence.get(gate), gate=gate)
        entries[gate] = values
        errors.extend(gate_errors)
        # Presence of malformed data is a measured authority failure, not an
        # unestablished gate.  A sentinel is unnecessary; gate derivation sees
        # the non-empty entries and returns a failing value where applicable.
    if causal_transfer_receipt_ids is not None:
        # A caller must choose one authority source for cross-lineage TE.  A
        # hand-authored row mixed with ledger receipts would make it possible
        # to retain a forged PASS alongside a verified witness.
        if entries["cross_lineage_te"]:
            errors.append(
                "cross_lineage_te:direct_evidence_conflicts_with_transfer_receipts")
        try:
            transfer_ids = _receipt_ids(causal_transfer_receipt_ids)
        except ValueError as exc:
            transfer_ids = ()
            errors.append(f"cross_lineage_te:{exc}")
        for transfer_id in transfer_ids:
            entry, transfer_errors = _transfer_entry(
                conn, transfer_id, rule_id=rule_id, rule_row=row,
                rule_digest=rule_digest)
            errors.extend(transfer_errors)
            if entry is not None:
                entries["cross_lineage_te"].append(entry)
    gate_inputs, derived_details = _derive_gate_inputs(
        entries, errors, rule_row=row, status=status,
        expected_status_version=expected_status_version,
        rule_digest=rule_digest,
        min_obligation_coverage=min_obligation_coverage,
        min_cross_lineage_te=min_cross_lineage_te,
        max_harmful_rate=max_harmful_rate,
        min_conformal_coverage=min_conformal_coverage)
    gate_report = evaluate_promotion_gates(
        gate_inputs, strict=True,
        min_obligation_coverage=min_obligation_coverage,
        min_cross_lineage_te=min_cross_lineage_te,
        max_harmful_rate=max_harmful_rate,
        min_conformal_coverage=min_conformal_coverage)
    trial_ok, trial_binding, trial_reasons = _trial_binding(
        conn, rule_id=rule_id, target_scope=target_scope, trial_id=trial_id,
        expected_status_version=expected_status_version)
    reasons = list(errors) + trial_reasons
    if row is None:
        reasons.append("rule_missing")
    if rule_digest is None:
        reasons.append("rule_content_digest_unavailable")
    if not gate_report["eligible"]:
        reasons.extend(f"gate:{name}" for name in gate_report["missing"])
        reasons.extend(f"gate_failed:{name}" for name in gate_report["failed"])
    eligible = bool(gate_report["eligible"] and trial_ok and not reasons)

    refs: dict[str, list[dict]] = {gate: [] for gate in REQUIRED_GATES}
    evidence_payload = _payload_from_entries(entries)
    for gate in REQUIRED_GATES:
        for item in entries[gate]:
            digest = _evidence_digest(
                rule_id=rule_id, target_scope=target_scope, gate_name=gate,
                evidence_id=item["evidence_id"], split=item["split"],
                lineage_id=item.get("lineage_id"), verdict=item["verdict"],
                payload=item["payload"])
            refs[gate].append({
                key: item.get(key) for key in
                ("evidence_id", "split", "lineage_id", "verdict")
            } | {"evidence_digest": digest})

    payload = {
        "authority_version": AUTHORITY_VERSION,
        "rule_id": rule_id,
        "target_scope": target_scope,
        "status_version": expected_status_version,
        "rule_content_digest": rule_digest,
        "trial_id": trial_id,
        "trial_binding": trial_binding,
        "eligible": eligible,
        "checks": dict(gate_report["checks"]),
        "gate_status": dict(gate_report["gate_status"]),
        "missing": list(gate_report["missing"]),
        "failed": list(gate_report["failed"]),
        "not_established": list(gate_report["not_established"]),
        "all_gates_established": gate_report["all_gates_established"],
        "thresholds": dict(gate_report["thresholds"]),
        "evidence_refs": refs,
        "evidence": evidence_payload,
        "derived": derived_details,
        "reasons": sorted(set(reasons)),
    }
    receipt_digest = _receipt_digest(payload)
    receipt_id = _receipt_id(receipt_digest)
    had_outer_transaction = conn.in_transaction
    savepoint = "tehm_rule_authority_v1"
    _ensure_rule_authority_schema(conn)
    conn.execute(f"SAVEPOINT {savepoint}")
    savepoint_active = True
    try:
        for gate in REQUIRED_GATES:
            for item in entries[gate]:
                digest = _evidence_digest(
                    rule_id=rule_id, target_scope=target_scope, gate_name=gate,
                    evidence_id=item["evidence_id"], split=item["split"],
                    lineage_id=item.get("lineage_id"), verdict=item["verdict"],
                    payload=item["payload"])
                _insert_evidence_row(
                    conn, rule_id=rule_id, target_scope=target_scope,
                    gate_name=gate, entry=item, digest=digest)
        receipt_json = stable_dumps(payload)
        existing = conn.execute(
            """SELECT rule_id, target_scope, status_version, eligible,
                      receipt_json, receipt_digest
                 FROM tehm_rule_authority_receipts
                WHERE authority_receipt_id=?""", (receipt_id,)).fetchone()
        values = (rule_id, target_scope, expected_status_version, int(eligible),
                  receipt_json, receipt_digest)
        if existing is not None:
            if tuple(existing) != values:
                raise ValueError("rule authority receipt is immutable and conflicts")
        else:
            conn.execute(
                """INSERT INTO tehm_rule_authority_receipts
                   (authority_receipt_id, rule_id, target_scope, status_version,
                    eligible, receipt_json, receipt_digest, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (receipt_id, *values, tehm_db.now_local()))
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        savepoint_active = False
        if not had_outer_transaction:
            conn.commit()
    except Exception:
        if savepoint_active:
            conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise
    return RuleAuthorityReceipt(
        rule_id=rule_id, target_scope=target_scope,
        status_version=expected_status_version,
        rule_content_digest=rule_digest or "", trial_id=trial_id,
        authority_version=AUTHORITY_VERSION, eligible=eligible,
        checks=dict(gate_report["checks"]),
        gate_status=dict(gate_report["gate_status"]),
        missing=tuple(gate_report["missing"]), failed=tuple(gate_report["failed"]),
        not_established=tuple(gate_report["not_established"]),
        evidence_refs=refs, evidence=evidence_payload,
        reasons=tuple(sorted(set(reasons))), authority_receipt_id=receipt_id,
        receipt_digest=receipt_digest, payload=payload)


def rule_content_digest(conn: sqlite3.Connection, rule_id: str) -> str | None:
    """Return the digest bound by authority for one stored rule definition."""
    return _rule_content_digest(_rule_row(conn, rule_id))


def _load_evidence_rows(conn: sqlite3.Connection, *, rule_id: str,
                        target_scope: str, refs: Mapping) -> tuple[dict, list[str]]:
    loaded: dict[str, list[dict]] = {gate: [] for gate in REQUIRED_GATES}
    reasons: list[str] = []
    if not _table_exists(conn, "tehm_rule_authority_evidence"):
        return loaded, ["rule_evidence_ledger_missing"]
    for gate in REQUIRED_GATES:
        raw_refs = refs.get(gate, []) if isinstance(refs, Mapping) else []
        if not isinstance(raw_refs, list):
            reasons.append(f"evidence:{gate}:refs_malformed")
            continue
        for ref in raw_refs:
            if not isinstance(ref, Mapping):
                reasons.append(f"evidence:{gate}:ref_malformed")
                continue
            evidence_id = str(ref.get("evidence_id") or "")
            row = conn.execute(
                """SELECT split, lineage_id, verdict, payload_json,
                          evidence_digest
                     FROM tehm_rule_authority_evidence
                    WHERE rule_id=? AND target_scope=? AND gate_name=?
                      AND evidence_id=?""",
                (rule_id, target_scope, gate, evidence_id)).fetchone()
            if row is None:
                reasons.append(f"evidence:{gate}:row_missing")
                continue
            if str(row["split"]) not in GATE_ALLOWED_SPLITS[gate]:
                reasons.append(f"evidence:{gate}:invalid_evidence_split")
            payload = _json_mapping(row["payload_json"])
            expected = _evidence_digest(
                rule_id=rule_id, target_scope=target_scope, gate_name=gate,
                evidence_id=evidence_id, split=str(row["split"]),
                lineage_id=row["lineage_id"], verdict=str(row["verdict"]),
                payload=payload)
            if (row["split"], row["lineage_id"], row["verdict"]) != (
                    ref.get("split"), ref.get("lineage_id"), ref.get("verdict")):
                reasons.append(f"evidence:{gate}:row_mismatch")
            if row["evidence_digest"] != expected or ref.get("evidence_digest") != expected:
                reasons.append(f"evidence:{gate}:digest_mismatch")
            loaded[gate].append({
                "evidence_id": evidence_id, "split": row["split"],
                "lineage_id": row["lineage_id"], "verdict": row["verdict"],
                "payload": payload,
            })
    return loaded, reasons


def verify_rule_authority(conn: sqlite3.Connection, authority_receipt) -> dict:
    """Re-derive a receipt from current rule, trial, status and ledger rows."""
    try:
        data = _as_dict(authority_receipt)
    except TypeError as exc:
        return {"eligible": False, "reasons": [str(exc)]}
    payload = data.get("payload")
    if not isinstance(payload, Mapping):
        return {"eligible": False, "reasons": ["authority_payload_missing"]}
    payload = dict(payload)
    reasons: list[str] = []
    expected_digest = _receipt_digest(payload)
    if data.get("authority_version") != AUTHORITY_VERSION:
        reasons.append("authority_version_mismatch")
    if data.get("receipt_digest") != expected_digest:
        reasons.append("authority_receipt_digest_mismatch")
    if data.get("authority_receipt_id") != _receipt_id(expected_digest):
        reasons.append("authority_receipt_id_mismatch")
    rule_id = str(data.get("rule_id") or payload.get("rule_id") or "")
    target_scope = str(data.get("target_scope") or payload.get("target_scope") or "")
    if payload.get("rule_id") != rule_id or payload.get("target_scope") != target_scope:
        reasons.append("authority_scope_payload_mismatch")
    row = _rule_row(conn, rule_id)
    current_digest = _rule_content_digest(row)
    if current_digest is None:
        reasons.append("rule_missing_or_malformed")
    elif payload.get("rule_content_digest") != current_digest:
        reasons.append("rule_content_digest_mismatch")
    status = get_status(conn, rule_id=rule_id, target_scope=target_scope)
    expected_version = payload.get("status_version")
    if status is None:
        reasons.append("candidate_status_missing")
    elif status.get("status") != "candidate":
        reasons.append("candidate_status_not_current")
    elif status.get("status_version") != expected_version:
        reasons.append("candidate_status_version_stale")

    refs = payload.get("evidence_refs")
    loaded, evidence_reasons = _load_evidence_rows(
        conn, rule_id=rule_id, target_scope=target_scope, refs=refs)
    reasons.extend(evidence_reasons)
    # A cross-lineage row produced by the L4 bridge is only valid while its
    # referenced transfer ledger receipt still replays against this database.
    # The immutable rule-authority row alone is not an independent witness.
    for item in loaded.get("cross_lineage_te", []):
        payload_item = item.get("payload") or {}
        if payload_item.get("causal_transfer_verified") is not True:
            continue
        transfer_id = str(payload_item.get("transfer_receipt_id") or "")
        entry, transfer_errors = _transfer_entry(
            conn, transfer_id, rule_id=rule_id, rule_row=row,
            rule_digest=current_digest)
        if transfer_errors:
            reasons.extend(transfer_errors)
        elif entry is None:
            reasons.append("cross_lineage_te:transfer_receipt_replay_missing")
        elif stable_dumps(entry) != stable_dumps(item):
            reasons.append("cross_lineage_te:transfer_evidence_projection_mismatch")
    expected_evidence = payload.get("evidence")
    if not isinstance(expected_evidence, Mapping):
        reasons.append("authority_evidence_payload_missing")
    else:
        canonical_loaded = _payload_from_entries(loaded)
        if stable_dumps(dict(expected_evidence)) != stable_dumps(canonical_loaded):
            reasons.append("authority_evidence_payload_mismatch")
    thresholds = payload.get("thresholds")
    if not isinstance(thresholds, Mapping):
        thresholds = {}
        reasons.append("authority_thresholds_missing")
    gate_inputs, details = _derive_gate_inputs(
        loaded, (), rule_row=row, status=status,
        expected_status_version=(int(expected_version)
                                 if expected_version is not None else None),
        rule_digest=current_digest,
        min_obligation_coverage=float(thresholds.get("obligation_coverage", 1.0)),
        min_cross_lineage_te=float(thresholds.get("cross_lineage_te", 1.0)),
        max_harmful_rate=float(thresholds.get("harmful_rate", 0.0)),
        min_conformal_coverage=float(thresholds.get("conformal_coverage", 0.80)))
    gate_report = evaluate_promotion_gates(
        gate_inputs, strict=True,
        min_obligation_coverage=float(thresholds.get("obligation_coverage", 1.0)),
        min_cross_lineage_te=float(thresholds.get("cross_lineage_te", 1.0)),
        max_harmful_rate=float(thresholds.get("harmful_rate", 0.0)),
        min_conformal_coverage=float(thresholds.get("conformal_coverage", 0.80)))
    for key in ("checks", "gate_status", "missing", "failed",
                "not_established", "all_gates_established"):
        if stable_dumps(payload.get(key)) != stable_dumps(gate_report.get(key)):
            reasons.append(f"authority_{key}_mismatch")
    trial_id = payload.get("trial_id")
    trial_ok, trial_binding, trial_reasons = _trial_binding(
        conn, rule_id=rule_id, target_scope=target_scope, trial_id=trial_id,
        expected_status_version=(int(expected_version)
                                 if expected_version is not None else None))
    reasons.extend(trial_reasons)
    if stable_dumps(payload.get("trial_binding") or {}) != stable_dumps(trial_binding):
        reasons.append("trial_binding_mismatch")
    if payload.get("eligible") is not True or not gate_report["eligible"] or not trial_ok:
        reasons.append("authority_receipt_not_eligible")
    if _table_exists(conn, "tehm_rule_authority_receipts"):
        stored = conn.execute(
            """SELECT rule_id, target_scope, status_version, eligible,
                      receipt_json, receipt_digest
                 FROM tehm_rule_authority_receipts
                WHERE authority_receipt_id=?""",
            (data.get("authority_receipt_id"),)).fetchone()
        if stored is None:
            reasons.append("authority_receipt_row_missing")
        else:
            try:
                stored_payload = json.loads(stored["receipt_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                stored_payload = None
            if not isinstance(stored_payload, Mapping) or stable_dumps(
                    dict(stored_payload)) != stable_dumps(payload):
                reasons.append("authority_receipt_row_mismatch")
            if tuple(stored) != (
                    rule_id, target_scope, expected_version,
                    int(data.get("eligible") is True),
                    stored["receipt_json"], expected_digest):
                reasons.append("authority_receipt_row_digest_mismatch")
    else:
        reasons.append("authority_receipt_ledger_missing")
    return {
        "eligible": not reasons,
        "reasons": sorted(set(reasons)),
        "checks": dict(gate_report["checks"]),
        "gate_report": gate_report,
        "rule_id": rule_id,
        "target_scope": target_scope,
        "status_version": expected_version,
        "rule_content_digest": current_digest,
        "trial_id": trial_id,
        "derived": details,
    }


def promote_rule(conn: sqlite3.Connection, authority_receipt,
                 *, provenance: Mapping | None = None):
    """Promote a candidate rule only through a verified authority receipt."""
    data = _as_dict(authority_receipt)
    checked = verify_rule_authority(conn, data)
    if not checked["eligible"]:
        raise ValueError(
            "rule authority receipt is not eligible: "
            f"{checked['reasons']}")
    status = get_status(conn, rule_id=checked["rule_id"],
                        target_scope=checked["target_scope"])
    if status is None or status["status"] != "candidate":
        raise ValueError("rule status is not candidate")
    if status["status_version"] != checked["status_version"]:
        raise ValueError("rule authority receipt status version is stale")
    return set_status(
        conn, rule_id=checked["rule_id"], target_scope=checked["target_scope"],
        status="promoted",
        provenance={"authority": AUTHORITY_VERSION,
                    "authority_receipt": data,
                    **dict(provenance or {})})


__all__ = [
    "AUTHORITY_VERSION", "EVIDENCE_SPLITS", "GATE_ALLOWED_SPLITS",
    "RULE_EVIDENCE_TYPES", "RuleAuthorityReceipt", "build_causal_transfer_evidence",
    "promote_rule",
    "record_rule_authority", "rule_content_digest", "verify_rule_authority",
]
