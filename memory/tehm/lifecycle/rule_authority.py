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
from pathlib import Path

from tehm import db as tehm_db
from tehm.causal.transfer_ledger import (
    load_causal_transfer_receipt, verify_causal_transfer)
from tehm.dataset import normalize_stored_learner_bool
from tehm.ids import stable_dumps
from tehm.physical.orfs_preflight import validate_persisted_execution_preflight

from .promotion_gates import PROMOTION_GATE_VERSION, REQUIRED_GATES, evaluate_promotion_gates
from .rule_status import get_status, set_status


AUTHORITY_VERSION = "rule-promotion-authority-v1"
EXTERNAL_AUTHORITY_PROJECTION_VERSION = "external-orfs-authority-v1"
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
# Trial rows are enriched after the authority attempt with derived metadata.
# Keep that metadata out of the content-bound trial witness so a receipt can be
# replayed after the runner stamps registry/authority summaries.  Measured arm
# pairs and their scalar outcomes remain in the binding and are still
# tamper-sensitive.
_TRIAL_DERIVED_METRIC_KEYS = frozenset({
    "authority_receipt", "promotion_gates", "registry_authority",
    "authority_projection_error", "evidence_reconciliation",
})

_EXTERNAL_AUTHORITY_SPLITS = frozenset({"calibration", "heldout"})
_EXTERNAL_UTILITY_VERDICTS = frozenset({
    "HARMFUL", "REGRESSION", "PARETO_SAFE", "SUPPORT", "NEUTRAL",
})


def _validate_external_rule_binding(
        conn: sqlite3.Connection, *, rule_id: str, record: Mapping) -> None:
    """Require an external record to describe the candidate rule's action.

    Utility and conformal values are otherwise easy to attach to an unrelated
    rule: both are valid evidence rows in isolation.  Strict authority must
    bind the observation's action domain and transformation family to the
    immutable rule definition before allowing either value into the ledger.
    """
    row = _rule_row(conn, rule_id)
    if row is None:
        raise ValueError("external_authority:rule_missing")
    action = record.get("action")
    if not isinstance(action, Mapping):
        raise ValueError("external_authority:record_action_malformed")
    action_domain = action.get("domain")
    family = action.get("transformation_family")
    if (type(action_domain) is not str or not action_domain.strip() or
            type(family) is not str or not family.strip()):
        raise ValueError("external_authority:record_action_incomplete")
    action_domain = action_domain.strip()
    family = family.strip()
    before = _json_mapping(row["before_pattern_json"])
    after = _json_mapping(row["after_pattern_json"])
    context = _json_mapping(row["context_profile_json"])
    raw_domains = (before.get("action_domain"), after.get("action_domain"))
    raw_families = (before.get("transformation_family"), after.get("transformation_family"),
                    before.get("type"), after.get("type"))
    if any(value is not None and
           (type(value) is not str or not value.strip())
           for value in (*raw_domains, *raw_families)):
        raise ValueError("external_authority:rule_action_binding_unavailable")
    expected_domains = {value.strip() for value in raw_domains if value is not None}
    expected_families = {value.strip() for value in raw_families if value is not None}
    if not expected_domains or not expected_families:
        raise ValueError("external_authority:rule_action_binding_unavailable")
    if action_domain not in expected_domains:
        raise ValueError("external_authority:rule_action_domain_mismatch")
    if family not in expected_families:
        raise ValueError("external_authority:rule_transformation_mismatch")
    # Compatibility is part of a typed action when both sides declare it.  A
    # missing profile on the external record is not silently treated as a
    # match, because that would let a profile-agnostic row support a typed
    # rule.  Older untyped rules remain supported when neither side declares
    # the profile.
    raw_profiles = (
        before.get("compatibility_profile"),
        after.get("compatibility_profile"),
        context.get("compatibility_profile"),
    )
    if any(value is not None and
           (type(value) is not str or not value.strip())
           for value in raw_profiles):
        raise ValueError("external_authority:rule_action_binding_unavailable")
    expected_profiles = {value.strip() for value in raw_profiles if value is not None}
    if expected_profiles:
        action_payload = action.get("payload")
        if not isinstance(action_payload, Mapping):
            action_payload = {}
        observed_profile = (
            action.get("compatibility_profile")
            if action.get("compatibility_profile") is not None
            else action_payload.get("compatibility_profile")
            if action_payload.get("compatibility_profile") is not None
            else record.get("compatibility_profile"))
        if (type(observed_profile) is not str or
                not observed_profile.strip()):
            raise ValueError("external_authority:compatibility_profile_mismatch")
        observed_profile = observed_profile.strip()
        if observed_profile not in expected_profiles:
            raise ValueError("external_authority:compatibility_profile_mismatch")


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
        # Evidence identity is part of the content-addressed witness.  Do
        # not stringify caller values here: ``1`` and ``"1"`` must not become
        # interchangeable IDs, and a truthy object must not become a verdict
        # or split accepted by the authority ledger.
        evidence_id = raw.get("evidence_id")
        split = raw.get("split")
        verdict = raw.get("verdict")
        lineage_id = raw.get("lineage_id")
        identity_error = False
        if type(evidence_id) is not str or not evidence_id.strip():
            errors.append(f"{gate}:entry_{ordinal}_evidence_id_malformed")
            identity_error = True
        if type(split) is not str or not split.strip():
            errors.append(f"{gate}:entry_{ordinal}_split_malformed")
            identity_error = True
        if type(verdict) is not str or not verdict.strip():
            errors.append(f"{gate}:entry_{ordinal}_verdict_malformed")
            identity_error = True
        if lineage_id is not None and (
                type(lineage_id) is not str or not lineage_id.strip()):
            errors.append(f"{gate}:entry_{ordinal}_lineage_id_malformed")
            identity_error = True
        if identity_error:
            continue
        evidence_id = evidence_id.strip()
        split = split.strip()
        verdict = verdict.strip()
        if lineage_id is not None:
            lineage_id = lineage_id.strip()
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
    # Boolean values are not numeric evidence.  ``float(True)`` and
    # ``float(False)`` would otherwise turn a caller's status bit into a
    # perfect/failing coverage or rate measurement.
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _strict_measurement(value) -> float | None:
    """Read a JSON/SQLite measurement without accepting string numerics."""
    if isinstance(value, bool) or type(value) not in (int, float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _strict_text(value, *, label: str) -> str:
    """Return one non-empty text identity without coercing weak values."""
    if type(value) is not str or not value.strip():
        raise ValueError(f"{label}_malformed")
    return value.strip()


def _stored_bool(value, *, field: str) -> bool:
    """Decode an authority ledger boolean without accepting truthy text."""
    if type(value) is not int or value not in (0, 1):
        raise ValueError(f"authority receipt {field} field is malformed")
    return bool(value)


def _authority_thresholds(raw, reasons: list[str]) -> dict[str, float]:
    """Read persisted gate thresholds without allowing malformed replay input.

    A receipt with a missing threshold is an older/incomplete receipt and is
    reported as such.  A present non-finite or non-numeric threshold is
    likewise invalid, but verification must return an ineligible result rather
    than raising while replaying an untrusted receipt.
    """
    defaults = {
        "obligation_coverage": 1.0,
        "cross_lineage_te": 1.0,
        "harmful_rate": 0.0,
        "conformal_coverage": 0.80,
    }
    if not isinstance(raw, Mapping):
        reasons.append("authority_thresholds_missing")
        return defaults
    result = {}
    for name, default in defaults.items():
        if name not in raw:
            reasons.append(f"authority_threshold_{name}_missing")
            result[name] = default
            continue
        value = raw.get(name)
        if isinstance(value, bool):
            reasons.append(f"authority_threshold_{name}_malformed")
            result[name] = default
            continue
        number = _finite_number(value)
        if number is None:
            reasons.append(f"authority_threshold_{name}_malformed")
            result[name] = default
            continue
        result[name] = number
    return result


def _payload_number(entry: Mapping, names: tuple[str, ...]) -> float | None:
    payload = entry.get("payload")
    if not isinstance(payload, Mapping):
        payload = {}
    for name in names:
        value = _finite_number(payload.get(name))
        if value is not None:
            return value
    return _finite_number(entry.get("value"))


def _strict_json_value(raw, *, label: str):
    """Decode one persisted JSON value without silently accepting corruption."""
    try:
        value = json.loads(raw or "null")
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"trial_authority:{label}_malformed") from exc
    return value


def _external_file_digest(path: Path) -> str:
    """Hash an external evidence file without changing it."""
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"external_authority:evidence_file_missing:{path}")
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise ValueError(f"external_authority:evidence_file_unreadable:{path}") from exc
    return digest.hexdigest()


def _open_external_staging_snapshot(path: Path):
    """Open a campaign DB read-only and return its logical content digest.

    The authority boundary consumes a closed/checkpointed staging snapshot.
    An outstanding WAL/SHM sidecar is rejected rather than opened with plain
    ``mode=ro`` (which can create a new ``-shm`` file while reading).  Once the
    snapshot is sidecar-free, ``immutable=1`` prevents all filesystem writes;
    hashing ``iterdump()`` binds logical content rather than SQLite layout.
    """
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"external_authority:staging_db_missing:{path}")
    if Path(str(path) + "-wal").exists() or Path(str(path) + "-shm").exists():
        raise ValueError("external_authority:staging_db_not_checkpointed")
    conn = None
    try:
        conn = sqlite3.connect(
            f"file:{path}?mode=ro&immutable=1", uri=True, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        # Hold one consistent read snapshot while hashing and projecting.  A
        # concurrent writer may append a later staging campaign, but it cannot
        # make this projection mix rows from two database states.
        conn.execute("BEGIN")
        meta = conn.execute(
            "SELECT value FROM tehm_meta WHERE key='schema_version'"
        ).fetchone()
        expected = f"tehm-v{tehm_db.config.DB_SCHEMA_VERSION}"
        if meta is None or meta["value"] != expected:
            conn.close()
            raise ValueError("external_authority:staging_schema_mismatch")
        required = {"tehm_transitions", "tehm_states", "tehm_dataset_membership"}
        present = {
            str(row[0]) for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")
        }
        missing = sorted(required - present)
        if missing:
            conn.close()
            raise ValueError(
                "external_authority:staging_tables_missing:" + ",".join(missing))
        dump = "\n".join(str(line) for line in conn.iterdump()).encode()
        return conn, hashlib.sha256(dump).hexdigest()
    except ValueError:
        raise
    except (OSError, sqlite3.Error) as exc:
        if conn is not None:
            conn.close()
        raise ValueError(f"external_authority:staging_db_unreadable:{path}") from exc


def _external_conformal_value(record: Mapping):
    """Extract and validate an explicitly recorded calibration coverage."""
    verification = record.get("verification")
    if not isinstance(verification, Mapping):
        verification = {}
    candidates = []
    for source in (record.get("conformal"), verification.get("conformal")):
        if source is not None:
            candidates.append(source)
    if not candidates:
        return None
    if any(not isinstance(source, Mapping) or
           stable_dumps(dict(source)) != stable_dumps(dict(candidates[0]))
           for source in candidates[1:]):
        raise ValueError("external_authority:conformal_sources_mismatch")
    raw = candidates[0]
    if not isinstance(raw, Mapping):
        raise ValueError("external_authority:conformal_malformed")
    coverage = _finite_number(raw.get("coverage"))
    covered = raw.get("covered")
    total = raw.get("total")
    if covered is not None or total is not None:
        if (isinstance(covered, bool) or isinstance(total, bool) or
                not isinstance(covered, int) or not isinstance(total, int) or
                total <= 0 or covered < 0 or covered > total):
            raise ValueError("external_authority:conformal_counts_malformed")
        ratio = covered / total
        if coverage is None:
            coverage = ratio
        elif not math.isclose(coverage, ratio, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("external_authority:conformal_coverage_mismatch")
    if coverage is None or not 0.0 <= coverage <= 1.0:
        raise ValueError("external_authority:conformal_coverage_malformed")
    payload = {"coverage": coverage}
    if covered is not None:
        payload.update({"covered": covered, "total": total})
    for key in ("method", "interval_method", "calibration_digest"):
        if key in raw:
            payload[key] = raw[key]
    return payload


def _external_transition_binding(
        staging: sqlite3.Connection, *, record: Mapping):
    """Require one staging transition to match the external record witness."""
    try:
        record_id = _strict_text(record.get("record_id"), label="record_id")
        lineage_id = _strict_text(record.get("lineage_id"), label="lineage_id")
    except ValueError as exc:
        raise ValueError("external_authority:record_identity_incomplete") from exc
    action = record.get("action")
    delta = record.get("observation_delta")
    verification = record.get("verification")
    if not isinstance(action, Mapping):
        raise ValueError("external_authority:record_identity_incomplete")
    try:
        action_domain = _strict_text(action.get("domain"), label="action_domain")
        _strict_text(action.get("transformation_family"),
                     label="transformation_family")
    except ValueError as exc:
        raise ValueError("external_authority:record_identity_incomplete")
    if not isinstance(delta, Mapping) or not isinstance(verification, Mapping):
        raise ValueError("external_authority:record_payload_incomplete")
    candidates = []
    for candidate in staging.execute(
            "SELECT * FROM tehm_transitions ORDER BY transition_id").fetchall():
        try:
            provenance = json.loads(candidate["provenance_json"] or "{}")
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("external_authority:transition_provenance_malformed") from exc
        if not isinstance(provenance, Mapping):
            raise ValueError("external_authority:transition_provenance_malformed")
        persisted_record_id = provenance.get("record_id")
        if type(persisted_record_id) is not str or not persisted_record_id.strip():
            raise ValueError("external_authority:transition_provenance_malformed")
        if persisted_record_id.strip() == record_id:
            candidates.append(candidate)
    if len(candidates) != 1:
        raise ValueError(
            "external_authority:record_transition_count=" + str(len(candidates)))
    transition = candidates[0]
    # The external observation is only an audit receipt; authority must still
    # bind it to the canonical transition's executable oracle.  Checking the
    # copied ``before``/``after`` fields alone would let a source omit the
    # persisted ``oracle_complete`` witness and attach utility/conformal
    # numbers to a partial or compile-only transition.
    from tehm.verified_execution import require_verified_transition
    try:
        require_verified_transition(staging, str(transition["transition_id"]))
    except ValueError as exc:
        raise ValueError(
            "external_authority:transition_execution_incomplete") from exc
    try:
        persisted_action = json.loads(transition["action_json"] or "null")
        persisted_delta = json.loads(transition["observation_delta_json"] or "null")
        persisted_verifier = json.loads(transition["verifier_json"] or "null")
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("external_authority:transition_payload_malformed") from exc
    if (not isinstance(persisted_action, Mapping) or
            not isinstance(persisted_delta, Mapping) or
            stable_dumps(dict(persisted_action)) != stable_dumps(dict(action)) or
            stable_dumps(dict(persisted_delta)) != stable_dumps(dict(delta))):
        raise ValueError("external_authority:transition_semantic_mismatch")
    # The capture adapter normalises VerifierSnapshot and therefore drops
    # adapter-only fields.  Compare every persisted field supplied by the
    # external record; an extra external field is harmless, a mismatch is not.
    if not isinstance(persisted_verifier, Mapping):
        raise ValueError("external_authority:transition_verifier_malformed")
    for key, value in persisted_verifier.items():
        if key in verification and verification[key] != value:
            raise ValueError(f"external_authority:transition_verifier_mismatch:{key}")
    if type(transition["action_domain"]) is not str or not transition["action_domain"].strip():
        raise ValueError("external_authority:transition_action_domain_mismatch")
    if transition["action_domain"] != action_domain:
        raise ValueError("external_authority:transition_action_domain_mismatch")
    states = staging.execute(
        "SELECT state_id, lineage_id FROM tehm_states WHERE state_id IN (?, ?)",
        (transition["source_state_id"], transition["target_state_id"])).fetchall()
    if len(states) != 2:
        raise ValueError("external_authority:transition_lineage_mismatch")
    for state in states:
        if (type(state["lineage_id"]) is not str or
                not state["lineage_id"].strip() or
                state["lineage_id"].strip() != lineage_id):
            raise ValueError("external_authority:transition_lineage_mismatch")
    return transition


def build_external_observation_authority_evidence(
        conn: sqlite3.Connection, *, observations_path: Path,
        staging_db: Path, campaign_id: str,
        case_ids: Iterable[str], rule_id: str | None = None) -> dict[str, list[dict]]:
    """Project selected external observations into DB-bound authority rows.

    The observation JSONL is only a source receipt.  A row becomes authority
    evidence only when it is a complete positive calibration/held-out record,
    its record ID resolves to exactly one immutable staging transition, the
    transition payload and lineage agree, and the requested campaign contains
    the same split with ``learner_eligible=0``.  This projector deliberately
    emits only ``harmful_rate`` and ``conformal_coverage`` rows.  Rollback,
    registry, obligation and cross-lineage TE must still come from their own
    independent ledgers.
    """
    if not isinstance(campaign_id, str) or not campaign_id.strip():
        raise ValueError("external_authority:campaign_id_required")
    if isinstance(case_ids, (str, bytes)):
        raise ValueError("external_authority:case_ids_must_be_sequence")
    try:
        requested = tuple(case_ids)
    except TypeError as exc:
        raise ValueError("external_authority:case_ids_must_be_sequence") from exc
    if (not requested or
            any(type(value) is not str or not value.strip()
                for value in requested)):
        raise ValueError("external_authority:case_ids_required")
    requested = tuple(value.strip() for value in requested)
    if len(set(requested)) != len(requested):
        raise ValueError("external_authority:case_ids_duplicate")

    observations_path = Path(observations_path).expanduser().resolve()
    observation_digest = _external_file_digest(observations_path)
    bound_rule_digest = None
    if rule_id is not None:
        bound_rule_digest = _rule_content_digest(_rule_row(conn, rule_id))
        if bound_rule_digest is None:
            raise ValueError("external_authority:rule_content_digest_unavailable")
    # Import locally to keep lifecycle imports independent of the batch lane.
    from tehm.batch_lane import BatchLaneError, read_external_observations
    try:
        rows = read_external_observations(observations_path)
    except BatchLaneError as exc:
        # Keep the authority projector's public error vocabulary stable even
        # when the lower-level observation reader catches a contradictory
        # learner flag before row projection begins.
        if "learner firewall" in str(exc):
            raise ValueError("external_authority:learner_firewall_violation") from exc
        raise
    by_case: dict[str, dict] = {}
    for row in rows:
        case_id = row.get("case_id")
        if type(case_id) is not str or not case_id.strip():
            raise ValueError("external_authority:case_id_missing")
        case_id = case_id.strip()
        if case_id in by_case:
            raise ValueError("external_authority:duplicate_observation_case")
        by_case[case_id] = row
    missing = sorted(set(requested) - set(by_case))
    if missing:
        raise ValueError("external_authority:case_ids_missing:" + ",".join(missing))

    staging, staging_digest = _open_external_staging_snapshot(staging_db)
    evidence = {gate: [] for gate in REQUIRED_GATES}
    try:
        for case_id in sorted(requested):
            row = by_case[case_id]
            split = row.get("split")
            if type(split) is not str:
                raise ValueError("external_authority:invalid_authority_split")
            split = split.strip()
            if split not in _EXTERNAL_AUTHORITY_SPLITS:
                raise ValueError("external_authority:invalid_authority_split")
            if row.get("classification") != "ELIGIBLE_POSITIVE":
                raise ValueError("external_authority:observation_not_positive")
            if row.get("learner_eligible") is not False:
                raise ValueError("external_authority:learner_firewall_violation")
            if any((not isinstance(row.get(side), Mapping) or
                    row[side].get("complete") is not True)
                   for side in ("before", "after")):
                raise ValueError("external_authority:full_oracle_incomplete")
            record = row.get("record")
            if not isinstance(record, Mapping):
                raise ValueError("external_authority:record_missing")
            record = dict(record)
            if rule_id is not None:
                _validate_external_rule_binding(
                    conn, rule_id=rule_id, record=record)
            try:
                record_lineage = _strict_text(
                    record.get("lineage_id"), label="record_lineage")
                row_lineage = _strict_text(
                    row.get("lineage_id"), label="row_lineage")
            except ValueError as exc:
                raise ValueError("external_authority:lineage_mismatch") from exc
            if row_lineage != record_lineage:
                raise ValueError("external_authority:lineage_mismatch")
            transition = _external_transition_binding(staging, record=record)
            membership = staging.execute(
                """SELECT split, learner_eligible FROM tehm_dataset_membership
                   WHERE transition_id=? AND campaign_id=?""",
                (transition["transition_id"], campaign_id)).fetchall()
            if len(membership) != 1:
                raise ValueError("external_authority:membership_missing_or_duplicate")
            try:
                membership_eligible = normalize_stored_learner_bool(
                    membership[0]["learner_eligible"])
            except ValueError as exc:
                raise ValueError(
                    "external_authority:membership_learner_flag_malformed") from exc
            if (membership[0]["split"] != split or membership_eligible is not False):
                raise ValueError("external_authority:membership_firewall_mismatch")

            try:
                receipt_id = _strict_text(
                    row.get("receipt_id"), label="receipt_id")
                receipt_digest = _strict_text(
                    row.get("receipt_sha256"), label="receipt_sha256")
            except ValueError as exc:
                raise ValueError("external_authority:receipt_digest_missing") from exc
            base_payload = {
                "projection_version": EXTERNAL_AUTHORITY_PROJECTION_VERSION,
                "source_receipt_id": receipt_id,
                "source_receipt_sha256": receipt_digest,
                "observations_sha256": observation_digest,
                "staging_db_sha256": staging_digest,
                "campaign_id": campaign_id,
                "case_id": case_id,
                "record_id": str(record["record_id"]),
                "transition_id": transition["transition_id"],
                "lineage_id": record_lineage,
                "split": split,
                "action_domain": str(record["action"]["domain"]),
                "transformation_family": str(
                    record["action"].get("transformation_family") or ""),
            }
            if rule_id is not None:
                base_payload.update({
                    "rule_id": rule_id,
                    "rule_content_digest": bound_rule_digest,
                })
            delta = dict(record["observation_delta"])
            verifier = dict(record["verification"])
            try:
                execution_preflight = validate_persisted_execution_preflight(
                    record.get("action"), verifier)
            except ValueError as exc:
                raise ValueError(
                    "external_authority:" + str(exc)) from exc
            if execution_preflight is not None:
                # Keep the projection bound to the exact action and executed
                # hook. The full record remains the source witness; these
                # compact fields make cohort mixing auditable without copying
                # the complete Tcl command list into every gate row.
                base_payload.update({
                    "action_content_sha256": hashlib.sha256(
                        stable_dumps(dict(record["action"])).encode()).hexdigest(),
                    "execution_preflight_digest": str(
                        execution_preflight["digest"]),
                    "execution_preflight_platform": str(
                        execution_preflight.get("platform") or ""),
                    "execution_preflight_hook_sha256": str(
                        execution_preflight.get("hook_sha256") or ""),
                })
            if verifier.get("verdict") != "PASS":
                raise ValueError("external_authority:record_not_pass")
            if delta.get("created_regressions"):
                raise ValueError("external_authority:record_has_regressions")
            raw_utility = delta.get("utility_verdict")
            if raw_utility is not None and type(raw_utility) is not str:
                raise ValueError("external_authority:utility_verdict_malformed")
            utility = (raw_utility.strip().upper()
                       if isinstance(raw_utility, str) else "")
            if utility in {"", "UNKNOWN"}:
                utility = None
            elif utility not in _EXTERNAL_UTILITY_VERDICTS:
                raise ValueError("external_authority:utility_verdict_malformed")
            persisted_delta = json.loads(transition["observation_delta_json"])
            persisted_raw_utility = ((persisted_delta or {}).get(
                "utility_verdict") if isinstance(persisted_delta, Mapping) else None)
            if (persisted_raw_utility is not None and
                    type(persisted_raw_utility) is not str):
                raise ValueError("external_authority:utility_verdict_malformed")
            persisted_utility = (
                persisted_raw_utility.strip().upper()
                if isinstance(persisted_raw_utility, str) else "")
            if (utility or "UNKNOWN") != (persisted_utility or "UNKNOWN"):
                raise ValueError("external_authority:utility_verdict_mismatch")
            if utility is not None:
                harmful_payload = {
                    **base_payload,
                    "utility_verdict": utility,
                    "harmful": utility in {"HARMFUL", "REGRESSION"},
                }
                evidence["harmful_rate"].append({
                    "evidence_id": f"external-orfs:{receipt_digest}:harmful",
                    "split": split, "lineage_id": record_lineage,
                    "verdict": "PASS", "payload": harmful_payload,
                })

            conformal = _external_conformal_value(record)
            if conformal is not None:
                if split != "calibration":
                    raise ValueError("external_authority:conformal_invalid_split")
                evidence["conformal_coverage"].append({
                    "evidence_id": f"external-orfs:{receipt_digest}:conformal",
                    "split": split, "lineage_id": record_lineage,
                    "verdict": "PASS",
                    "payload": {**base_payload, **conformal},
                })
    finally:
        staging.close()
    return evidence


def build_external_observation_authority_evidence_batch(
        conn: sqlite3.Connection, *, sources: Iterable[Mapping],
        rule_id: str | None = None) -> dict[str, list[dict]]:
    """Combine several independently frozen external/staging evidence sources.

    A real authority cohort often spans multiple campaign-local staging DBs:
    for example, a calibration campaign, a held-out transfer campaign, and a
    second source-disjoint support replay.  Callers must not concatenate the
    projected dictionaries themselves because that can silently double-count
    one transition or make source ordering part of an untracked receipt.  This
    seam reuses the single-source projector, orders sources deterministically,
    and rejects duplicate receipt/evidence/transition witnesses before the
    result reaches :func:`record_rule_authority`.

    ``conn`` is accepted for API symmetry with the single-source projector;
    all external/staging inputs are opened read-only and the caller's
    authority connection is not mutated by this function.
    """
    if isinstance(sources, (str, bytes)):
        raise ValueError("external_authority:sources_must_be_sequence")
    try:
        raw_sources = list(sources)
    except TypeError as exc:
        raise ValueError("external_authority:sources_must_be_sequence") from exc
    if not raw_sources:
        raise ValueError("external_authority:sources_required")

    normalised: list[dict] = []
    for source in raw_sources:
        if not isinstance(source, Mapping):
            raise ValueError("external_authority:source_must_be_mapping")
        required = ("observations_path", "staging_db", "campaign_id", "case_ids")
        missing = [name for name in required if name not in source]
        if missing:
            raise ValueError(
                "external_authority:source_fields_missing:" + ",".join(missing))
        observations_path = Path(source["observations_path"]).expanduser().resolve()
        staging_db = Path(source["staging_db"]).expanduser().resolve()
        campaign_id = source["campaign_id"]
        case_ids = source["case_ids"]
        if type(campaign_id) is not str or not campaign_id.strip():
            raise ValueError("external_authority:source_campaign_id_required")
        if isinstance(case_ids, (str, bytes)):
            raise ValueError("external_authority:source_case_ids_must_be_sequence")
        try:
            case_ids = tuple(case_ids)
        except TypeError as exc:
            raise ValueError(
                "external_authority:source_case_ids_must_be_sequence") from exc
        if (not case_ids or
                any(type(value) is not str or not value.strip()
                    for value in case_ids)):
            raise ValueError("external_authority:source_case_ids_required")
        case_ids = tuple(value.strip() for value in case_ids)
        normalised.append({
            "observations_path": observations_path,
            "staging_db": staging_db,
            "campaign_id": campaign_id.strip(),
            "case_ids": case_ids,
        })

    # Stable source order makes the enclosing content-addressed authority
    # receipt independent of the order in which an operator listed campaigns.
    normalised.sort(key=lambda source: (
        str(source["campaign_id"]), str(source["observations_path"]),
        str(source["staging_db"]), source["case_ids"]))

    merged = {gate: [] for gate in REQUIRED_GATES}
    seen_evidence: set[tuple[str, str]] = set()
    # One external record can legitimately establish both the utility and
    # calibration gates, so witness IDs are unique per gate.  Reusing the
    # same transition from a *different* source, however, would double-count
    # the cohort and is rejected below.
    seen_gate_receipts: set[tuple[str, str]] = set()
    seen_gate_transitions: set[tuple[str, str]] = set()
    seen_gate_records: set[tuple[str, str]] = set()
    seen_cases: set[str] = set()
    case_sources: dict[str, tuple[str, str, str]] = {}
    transition_sources: dict[str, tuple[str, str, str]] = {}
    transition_cases: dict[str, str] = {}
    record_sources: dict[str, tuple[str, str, str]] = {}
    record_cases: dict[str, str] = {}
    receipt_sources: dict[str, tuple[str, str, str]] = {}
    receipt_cases: dict[str, str] = {}
    routing_cohort: tuple[str, str, str, str] | None = None
    for source in normalised:
        projected = build_external_observation_authority_evidence(
            conn,
            observations_path=source["observations_path"],
            staging_db=source["staging_db"],
            campaign_id=source["campaign_id"],
            case_ids=source["case_ids"],
            rule_id=rule_id,
        )
        for gate in REQUIRED_GATES:
            for entry in projected.get(gate, []):
                payload = entry.get("payload") or {}
                receipt_id = str(payload.get("source_receipt_id") or "")
                transition_id = str(payload.get("transition_id") or "")
                record_id = str(payload.get("record_id") or "")
                case_id = str(payload.get("case_id") or "")
                source_identity = (
                    str(payload.get("observations_sha256") or ""),
                    str(payload.get("staging_db_sha256") or ""),
                    str(payload.get("campaign_id") or ""),
                )
                preflight_digest_value = str(
                    payload.get("execution_preflight_digest") or "")
                if preflight_digest_value:
                    cohort = (
                        str(payload.get("action_content_sha256") or ""),
                        str(payload.get("execution_preflight_platform") or ""),
                        str(payload.get("execution_preflight_hook_sha256") or ""),
                        preflight_digest_value,
                    )
                    if routing_cohort is None:
                        routing_cohort = cohort
                    elif routing_cohort != cohort:
                        raise ValueError(
                            "external_authority:mixed_routing_preflight_cohort")
                evidence_key = (gate, str(entry.get("evidence_id") or ""))
                if evidence_key in seen_evidence:
                    raise ValueError(
                        "external_authority:duplicate_evidence_witness")
                if case_id and case_id in seen_cases:
                    # The same case may be represented by utility and
                    # conformal rows in one source, but not by two source
                    # specifications.  Gate-specific rows are handled by the
                    # source identity check below.
                    prior_case_source = case_sources.get(case_id)
                    if prior_case_source != source_identity:
                        raise ValueError(
                            "external_authority:duplicate_case_witness")
                if (receipt_id and
                        (gate, receipt_id) in seen_gate_receipts):
                    raise ValueError(
                        "external_authority:duplicate_receipt_witness")
                if (transition_id and
                        (gate, transition_id) in seen_gate_transitions):
                    raise ValueError(
                        "external_authority:duplicate_transition_witness")
                if (record_id and
                        (gate, record_id) in seen_gate_records):
                    raise ValueError(
                        "external_authority:duplicate_record_witness")
                if transition_id:
                    previous_source = transition_sources.get(transition_id)
                    if (previous_source is not None and
                            (previous_source != source_identity or
                             transition_cases.get(transition_id) != case_id)):
                        raise ValueError(
                            "external_authority:duplicate_transition_witness")
                    transition_sources[transition_id] = source_identity
                    transition_cases[transition_id] = case_id
                if record_id:
                    previous_source = record_sources.get(record_id)
                    if (previous_source is not None and
                            (previous_source != source_identity or
                             record_cases.get(record_id) != case_id)):
                        raise ValueError(
                            "external_authority:duplicate_record_witness")
                    record_sources[record_id] = source_identity
                    record_cases[record_id] = case_id
                if receipt_id:
                    previous_source = receipt_sources.get(receipt_id)
                    if (previous_source is not None and
                            (previous_source != source_identity or
                             receipt_cases.get(receipt_id) != case_id)):
                        raise ValueError(
                            "external_authority:duplicate_receipt_witness")
                    receipt_sources[receipt_id] = source_identity
                    receipt_cases[receipt_id] = case_id
                seen_evidence.add(evidence_key)
                if receipt_id:
                    seen_gate_receipts.add((gate, receipt_id))
                if transition_id:
                    seen_gate_transitions.add((gate, transition_id))
                if record_id:
                    seen_gate_records.add((gate, record_id))
                if case_id:
                    seen_cases.add(case_id)
                    case_sources[case_id] = source_identity
                merged[gate].append(entry)
    return merged


def record_rule_authority_from_external_observations(
        conn: sqlite3.Connection, *, rule_id: str, target_scope: str,
        trial_id: str, expected_status_version: int | None,
        observations_path: Path, staging_db: Path, campaign_id: str,
        case_ids: Iterable[str], causal_transfer_receipt_ids=None,
        min_obligation_coverage: float = 1.0,
        min_cross_lineage_te: float = 1.0,
        max_harmful_rate: float = 0.0,
        min_conformal_coverage: float = 0.80) -> "RuleAuthorityReceipt":
    """Compose external calibration evidence with the strict rule authority.

    This convenience seam prevents callers from manually merging independent
    evidence planes.  External observations contribute only harmful/conformal
    rows; trial/activation projection and optional replay-verified L4 transfer
    remain owned by :func:`record_rule_authority`.  Any source binding error is
    raised before the authority ledger write is attempted.
    """
    evidence = build_external_observation_authority_evidence_batch(
        conn, sources=[{
            "observations_path": observations_path,
            "staging_db": staging_db,
            "campaign_id": campaign_id,
            "case_ids": case_ids,
        }], rule_id=rule_id)
    trial_evidence = build_trial_authority_evidence(
        conn, trial_id=trial_id, rule_id=rule_id, target_scope=target_scope)
    for gate in ("rollback_verified", "registry_verified",
                 "obligation_coverage", "harmful_rate"):
        evidence[gate].extend(trial_evidence.get(gate, []))
    return record_rule_authority(
        conn, rule_id=rule_id, target_scope=target_scope, evidence=evidence,
        trial_id=trial_id, expected_status_version=expected_status_version,
        min_obligation_coverage=min_obligation_coverage,
        min_cross_lineage_te=min_cross_lineage_te,
        max_harmful_rate=max_harmful_rate,
        min_conformal_coverage=min_conformal_coverage,
        causal_transfer_receipt_ids=causal_transfer_receipt_ids)


def record_rule_authority_from_external_observation_sources(
        conn: sqlite3.Connection, *, rule_id: str, target_scope: str,
        trial_id: str, expected_status_version: int | None,
        sources: Iterable[Mapping], causal_transfer_receipt_ids=None,
        min_obligation_coverage: float = 1.0,
        min_cross_lineage_te: float = 1.0,
        max_harmful_rate: float = 0.0,
        min_conformal_coverage: float = 0.80) -> "RuleAuthorityReceipt":
    """Record authority from multiple independently frozen audit sources.

    This is the multi-campaign counterpart of
    :func:`record_rule_authority_from_external_observations`.  It composes
    only the external harmful/conformal planes; trial/activation and optional
    causal-transfer evidence remain independently replayed by
    :func:`record_rule_authority`.
    """
    evidence = build_external_observation_authority_evidence_batch(
        conn, sources=sources, rule_id=rule_id)
    trial_evidence = build_trial_authority_evidence(
        conn, trial_id=trial_id, rule_id=rule_id, target_scope=target_scope)
    for gate in ("rollback_verified", "registry_verified",
                 "obligation_coverage", "harmful_rate"):
        evidence[gate].extend(trial_evidence.get(gate, []))
    return record_rule_authority(
        conn, rule_id=rule_id, target_scope=target_scope, evidence=evidence,
        trial_id=trial_id, expected_status_version=expected_status_version,
        min_obligation_coverage=min_obligation_coverage,
        min_cross_lineage_te=min_cross_lineage_te,
        max_harmful_rate=max_harmful_rate,
        min_conformal_coverage=min_conformal_coverage,
        causal_transfer_receipt_ids=causal_transfer_receipt_ids)


def _trial_authority_row(conn: sqlite3.Connection, trial_id: str):
    """Load one immutable trial row by ID or deterministic UUID."""
    try:
        trial_id = _strict_text(trial_id, label="trial_id")
    except ValueError as exc:
        raise ValueError("trial_authority:trial_id_malformed") from exc
    row = conn.execute(
        "SELECT * FROM tehm_trials WHERE trial_id=? OR trial_uuid=? "
        "ORDER BY created_at DESC LIMIT 1", (trial_id, trial_id)).fetchone()
    if row is None:
        raise ValueError("trial_authority:trial_missing")
    return row


def build_trial_authority_evidence(
        conn: sqlite3.Connection, *, trial_id: str, rule_id: str,
        target_scope: str) -> dict[str, list[dict]]:
    """Project measured trial/activation rows into rule-gate evidence.

    This is the database-bound counterpart to hand-authored gate payloads.  It
    only projects rollback, obligation, registry, and explicit utility facts
    that are present in the trial's activation witnesses.  Missing conformal
    or cross-lineage evidence is intentionally left unestablished for the
    caller to provide through their independent calibration/transfer ledgers.
    Any malformed or mismatched activation witness aborts the projection.
    """
    row = _trial_authority_row(conn, trial_id)
    try:
        rule_id = _strict_text(rule_id, label="rule_id")
        target_scope = _strict_text(target_scope, label="target_scope")
        row_rule_id = _strict_text(row["rule_id"], label="trial_rule_id")
        row_target_scope = _strict_text(
            row["target_scope"], label="trial_target_scope")
        row_trial_id = _strict_text(row["trial_id"], label="trial_row_id")
        row_trial_uuid = (_strict_text(row["trial_uuid"], label="trial_uuid")
                          if row["trial_uuid"] is not None else None)
    except ValueError as exc:
        raise ValueError("trial_authority:trial_identity_malformed") from exc
    if row_rule_id != rule_id or row_target_scope != target_scope:
        raise ValueError("trial_authority:trial_rule_scope_mismatch")
    metrics = _strict_json_value(row["metrics_json"], label="metrics")
    if not isinstance(metrics, Mapping):
        raise ValueError("trial_authority:metrics_not_mapping")
    raw_pairs = metrics.get("pairs")
    if raw_pairs is None:
        raw_pairs = []
    if not isinstance(raw_pairs, list):
        raise ValueError("trial_authority:pairs_not_list")

    evidence: dict[str, list[dict]] = {
        gate: [] for gate in REQUIRED_GATES}
    seen_activation_ids: set[str] = set()
    for ordinal, raw_pair in enumerate(raw_pairs):
        if not isinstance(raw_pair, Mapping):
            raise ValueError(f"trial_authority:pair_{ordinal}_malformed")
        try:
            activation_id = _strict_text(
                raw_pair.get("activation_id"), label="activation_id")
        except ValueError as exc:
            raise ValueError(
                f"trial_authority:pair_{ordinal}_activation_missing") from exc
        if activation_id in seen_activation_ids:
            raise ValueError("trial_authority:duplicate_activation_witness")
        seen_activation_ids.add(activation_id)
        activation = conn.execute(
            "SELECT * FROM tehm_activations WHERE activation_id=?",
            (activation_id,)).fetchone()
        if activation is None:
            raise ValueError("trial_authority:activation_missing")
        activation_trial_uuid = activation["trial_uuid"]
        activation_rule_id = activation["rule_id"]
        if type(activation_rule_id) is not str or not activation_rule_id.strip():
            raise ValueError("trial_authority:activation_rule_identity_malformed")
        activation_rule_id = activation_rule_id.strip()
        if activation_trial_uuid is not None and (
                type(activation_trial_uuid) is not str or
                not activation_trial_uuid.strip()):
            raise ValueError("trial_authority:activation_trial_identity_malformed")
        if isinstance(activation_trial_uuid, str):
            activation_trial_uuid = activation_trial_uuid.strip()
        if (activation_rule_id != rule_id or
                activation_trial_uuid not in (None, row_trial_uuid)):
            raise ValueError("trial_authority:activation_trial_binding_mismatch")
        raw_lineage = raw_pair.get("subject_lineage")
        if raw_lineage is None:
            raw_lineage = raw_pair.get("lineage_id")
        try:
            lineage_id = _strict_text(raw_lineage, label="pair_lineage")
        except ValueError as exc:
            raise ValueError(
                f"trial_authority:pair_{ordinal}_lineage_missing") from exc

        pair_rollback = raw_pair.get("rollback_receipt")
        if not isinstance(pair_rollback, Mapping):
            raise ValueError("trial_authority:rollback_witness_missing")
        db_rollback = _strict_json_value(
            activation["rollback_receipt_json"], label="rollback_receipt")
        if (not isinstance(db_rollback, Mapping) or
                stable_dumps(dict(pair_rollback)) != stable_dumps(dict(db_rollback))):
            raise ValueError("trial_authority:rollback_witness_mismatch")
        if not isinstance(db_rollback.get("verified"), bool):
            raise ValueError("trial_authority:rollback_verdict_missing")

        pair_coverage = _strict_measurement(
            raw_pair.get("obligation_coverage"))
        db_coverage = _strict_measurement(activation["obligation_coverage"])
        if (pair_coverage is None or db_coverage is None or
                pair_coverage != db_coverage):
            raise ValueError("trial_authority:obligation_witness_mismatch")

        base_id = f"{row_trial_id}:{activation_id}"
        evidence["rollback_verified"].append({
            "evidence_id": f"{base_id}:rollback",
            "split": "ab", "lineage_id": lineage_id,
            "verdict": "PASS" if db_rollback["verified"] else "FAIL",
            "payload": {
                "trial_id": row_trial_id,
                "trial_uuid": row_trial_uuid,
                "activation_id": activation_id,
                "verified": db_rollback["verified"],
                "rollback_receipt": dict(db_rollback),
            },
        })
        evidence["obligation_coverage"].append({
            "evidence_id": f"{base_id}:obligation",
            "split": "ab", "lineage_id": lineage_id, "verdict": "PASS",
            "payload": {
                "trial_id": row_trial_id,
                "trial_uuid": row_trial_uuid,
                "activation_id": activation_id,
                "coverage": db_coverage,
                "obligation_coverage": db_coverage,
            },
        })

        # Utility is projected only from a durable produced transition's
        # observation delta.  Trial-summary JSON is not an independent
        # utility witness: absence is NOT_ESTABLISHED, never an inferred safe
        # outcome from a successful target oracle.
        utility = None
        produced_transition_id = activation["produced_transition_id"]
        if produced_transition_id is not None:
            try:
                produced_transition_id = _strict_text(
                    produced_transition_id, label="produced_transition_id")
            except ValueError as exc:
                raise ValueError(
                    "trial_authority:produced_transition_id_malformed") from exc
        if produced_transition_id:
            transition = conn.execute(
                "SELECT observation_delta_json FROM tehm_transitions "
                "WHERE transition_id=?", (produced_transition_id,)).fetchone()
            if transition is None:
                raise ValueError("trial_authority:utility_transition_missing")
            # A trial utility row is learner-derived evidence.  The presence
            # of a transition ID is not enough: replay the same complete
            # executable oracle required by online admission before projecting
            # the outcome into the harmful-rate gate.
            from tehm.verified_execution import require_verified_transition
            try:
                require_verified_transition(conn, produced_transition_id)
            except ValueError as exc:
                raise ValueError(
                    "trial_authority:utility_transition_unverified") from exc
            delta = _strict_json_value(
                transition["observation_delta_json"], label="utility_delta")
            if not isinstance(delta, Mapping):
                raise ValueError("trial_authority:utility_delta_not_mapping")
            utility = delta.get("utility_verdict")
        if utility is not None:
            if type(utility) is not str or not utility.strip():
                raise ValueError("trial_authority:utility_verdict_malformed")
            utility = utility.strip().upper()
            # A real execution can establish verification without an
            # independent utility/PPA verdict.  Capture adapters encode that
            # state as UNKNOWN; it must remain an unestablished harmful-rate
            # gate, not be treated as malformed evidence or an implicit safe
            # outcome.
            if utility in {"", "UNKNOWN"}:
                utility = None
            elif utility not in {"HARMFUL", "REGRESSION", "PARETO_SAFE",
                                 "SUPPORT", "NEUTRAL", "PASS"}:
                raise ValueError("trial_authority:utility_verdict_malformed")
            if utility is not None:
                evidence["harmful_rate"].append({
                    "evidence_id": f"{base_id}:utility",
                    "split": "ab", "lineage_id": lineage_id, "verdict": "PASS",
                    "payload": {
                        "trial_id": row_trial_id,
                        "trial_uuid": row_trial_uuid,
                        "activation_id": activation_id,
                        "utility_verdict": utility,
                        "harmful": utility in {"HARMFUL", "REGRESSION"},
                    },
                })

    rule_row = _rule_row(conn, rule_id)
    digest = _rule_content_digest(rule_row)
    status = get_status(conn, rule_id=rule_id, target_scope=target_scope)
    registry_ok = bool(
        rule_row is not None and digest and status is not None and
        status.get("status") == "candidate" and
        status.get("status_version") == row["status_version"])
    evidence["registry_verified"].append({
        "evidence_id": f"{row_trial_id}:registry",
        "split": "ab", "lineage_id": None,
        "verdict": "PASS" if registry_ok else "FAIL",
        "payload": {
            "trial_id": row_trial_id,
            "trial_uuid": row_trial_uuid,
            "status": status.get("status") if status else None,
            "status_version": status.get("status_version") if status else None,
            "rule_content_digest": digest,
        },
    })
    return evidence


def _receipt_ids(values) -> tuple[str, ...]:
    """Normalize an explicit causal-transfer receipt selection."""
    if isinstance(values, (str, bytes)) or values is None:
        raise ValueError("causal transfer receipt IDs must be a sequence")
    try:
        values = tuple(values)
    except TypeError as exc:
        raise ValueError(
            "causal transfer receipt IDs must be a sequence") from exc
    if (not values or
            any(type(value) is not str or not value.strip()
                for value in values)):
        raise ValueError("causal transfer receipt IDs must be non-empty")
    result = tuple(value.strip() for value in values)
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
        from tehm.verified_execution import require_verified_transition
        try:
            require_verified_transition(conn, transition_id)
        except ValueError:
            errors.append(
                "cross_lineage_te:rule_binding_transfer_transition_unverified")
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
        from tehm.verified_execution import require_verified_transition
        try:
            require_verified_transition(conn, transition_id)
        except ValueError:
            errors.append(
                "cross_lineage_te:rule_binding_source_transition_unverified")
            continue
        memberships = conn.execute(
            "SELECT split, learner_eligible FROM tehm_dataset_membership "
            "WHERE transition_id=? AND campaign_id=?",
            (transition_id, ledger.training_campaign_id)).fetchall()
        source_ok = False
        malformed_membership = False
        for membership in memberships:
            if membership["split"] != "training":
                continue
            try:
                eligible = normalize_stored_learner_bool(
                    membership["learner_eligible"])
            except ValueError:
                malformed_membership = True
                continue
            if eligible is True:
                source_ok = True
        if malformed_membership:
            errors.append(
                "cross_lineage_te:rule_binding_source_membership_malformed")
        if not source_ok:
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
    errors = list(errors)
    gate_inputs: dict = {}
    details: dict = {"errors": sorted(set(errors))}

    # External projections optionally carry the rule digest they were bound
    # against.  Re-check it here (and therefore during receipt replay) so a
    # rule edit between projection and authority recording cannot reuse stale
    # utility/calibration rows.
    for gate in REQUIRED_GATES:
        for item in entries.get(gate, []):
            payload = item.get("payload") or {}
            bound_digest = payload.get("rule_content_digest")
            if (bound_digest is not None and
                    bound_digest != rule_digest):
                errors.append(f"{gate}:rule_content_digest_mismatch")
    details["errors"] = sorted(set(errors))

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
            type(expected_status_version) is int and
            expected_status_version > 0 and
            status.get("status_version") == expected_status_version and
            rule_digest)
        for item in registry:
            payload = item.get("payload") or {}
            payload_version = payload.get("status_version")
            if (type(payload_version) is not int or payload_version <= 0 or
                    type(expected_status_version) is not int or
                    expected_status_version <= 0):
                errors.append("registry_verified:status_version_malformed")
            registry_ok = registry_ok and item.get("verdict") == "PASS" and (
                payload.get("rule_content_digest") == rule_digest and
                type(payload_version) is int and
                payload_version > 0 and
                payload_version == expected_status_version and
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
    # Registry/measurement validation above can discover additional errors
    # after the initial diagnostic snapshot.  Keep the derived audit details
    # complete so a receipt does not hide the reason that forced a gate to
    # fail closed.
    details["errors"] = sorted(set(errors))
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
    raw_metrics = _json_mapping(row["metrics_json"])
    metrics = {
        key: value for key, value in raw_metrics.items()
        if key not in _TRIAL_DERIVED_METRIC_KEYS
    }
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
    if (expected_status_version is None or
            type(expected_status_version) is not int or
            expected_status_version <= 0):
        reasons.append("trial_status_version_malformed")
    elif row["status_version"] != expected_status_version:
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
    try:
        rule_id = _strict_text(rule_id, label="rule_id")
        target_scope = _strict_text(target_scope, label="target_scope")
        if trial_id is not None:
            trial_id = _strict_text(trial_id, label="trial_id")
    except ValueError as exc:
        raise ValueError("rule authority identity is malformed") from exc
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
    # ``_derive_gate_inputs`` can discover malformed payloads after its
    # initial normalisation pass (for example a boolean registry version).
    # Preserve those diagnostics in the public receipt; otherwise the gate
    # would fail closed while the reason silently disappeared.
    reasons = (list(errors) + list(derived_details.get("errors") or []) +
               trial_reasons)
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
            # Replay is an authority boundary too.  Do not turn a weakly
            # typed receipt reference (for example ``1``) into the string
            # ``"1"`` and then look it up: that would let a caller select a
            # different immutable evidence row than the one named by the
            # content-addressed receipt.  The write-side normaliser applies
            # the same identity rules; keeping them symmetric makes a
            # receipt fail closed even when its outer digest is stale.
            evidence_id = ref.get("evidence_id")
            split = ref.get("split")
            verdict = ref.get("verdict")
            lineage_id = ref.get("lineage_id")
            evidence_digest = ref.get("evidence_digest")
            malformed = False
            for field_name, value in (
                    ("evidence_id", evidence_id),
                    ("split", split),
                    ("verdict", verdict),
                    ("evidence_digest", evidence_digest)):
                if type(value) is not str or not value.strip():
                    reasons.append(f"evidence:{gate}:ref_{field_name}_malformed")
                    malformed = True
            if lineage_id is not None and (
                    type(lineage_id) is not str or not lineage_id.strip()):
                reasons.append(f"evidence:{gate}:ref_lineage_id_malformed")
                malformed = True
            if malformed:
                continue
            evidence_id = evidence_id.strip()
            split = split.strip()
            verdict = verdict.strip()
            evidence_digest = evidence_digest.strip()
            if lineage_id is not None:
                lineage_id = lineage_id.strip()
            if split not in EVIDENCE_SPLITS:
                reasons.append(f"evidence:{gate}:ref_invalid_split")
                continue
            if split not in GATE_ALLOWED_SPLITS[gate]:
                reasons.append(f"evidence:{gate}:ref_invalid_evidence_split")
                continue
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
            row_split = row["split"]
            row_verdict = row["verdict"]
            row_lineage = row["lineage_id"]
            row_digest = row["evidence_digest"]
            if (type(row_split) is not str or not row_split.strip() or
                    type(row_verdict) is not str or not row_verdict.strip() or
                    (row_lineage is not None and
                     (type(row_lineage) is not str or not row_lineage.strip())) or
                    type(row_digest) is not str or not row_digest.strip()):
                reasons.append(f"evidence:{gate}:row_identity_malformed")
                continue
            row_split = row_split.strip()
            row_verdict = row_verdict.strip()
            row_lineage = row_lineage.strip() if row_lineage is not None else None
            row_digest = row_digest.strip()
            if row_split not in GATE_ALLOWED_SPLITS[gate]:
                reasons.append(f"evidence:{gate}:invalid_evidence_split")
            payload = _json_mapping(row["payload_json"])
            expected = _evidence_digest(
                rule_id=rule_id, target_scope=target_scope, gate_name=gate,
                evidence_id=evidence_id, split=row_split,
                lineage_id=row_lineage, verdict=row_verdict,
                payload=payload)
            if (row_split, row_lineage, row_verdict) != (
                    split, lineage_id, verdict):
                reasons.append(f"evidence:{gate}:row_mismatch")
            if row_digest != expected or evidence_digest != expected:
                reasons.append(f"evidence:{gate}:digest_mismatch")
            loaded[gate].append({
                "evidence_id": evidence_id, "split": row_split,
                "lineage_id": row_lineage, "verdict": row_verdict,
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
    for field in ("authority_version", "authority_receipt_id",
                  "receipt_digest", "rule_id", "target_scope"):
        value = data.get(field)
        if type(value) is not str or not value.strip():
            reasons.append(f"authority_{field}_malformed")
    for field in ("rule_id", "target_scope"):
        value = payload.get(field)
        if type(value) is not str or not value.strip():
            reasons.append(f"authority_{field}_payload_malformed")
    expected_digest = _receipt_digest(payload)
    if data.get("authority_version") != AUTHORITY_VERSION:
        reasons.append("authority_version_mismatch")
    if data.get("receipt_digest") != expected_digest:
        reasons.append("authority_receipt_digest_mismatch")
    if data.get("authority_receipt_id") != _receipt_id(expected_digest):
        reasons.append("authority_receipt_id_mismatch")
    rule_id = data.get("rule_id") if type(data.get("rule_id")) is str else ""
    target_scope = (data.get("target_scope")
                    if type(data.get("target_scope")) is str else "")
    rule_id = rule_id.strip()
    target_scope = target_scope.strip()
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
    replay_status_version = expected_version
    if expected_version is not None:
        if (isinstance(expected_version, bool) or
                not isinstance(expected_version, int) or expected_version <= 0):
            reasons.append("authority_status_version_malformed")
            replay_status_version = None
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
        transfer_id = payload_item.get("transfer_receipt_id")
        if type(transfer_id) is not str or not transfer_id.strip():
            reasons.append("cross_lineage_te:transfer_receipt_id_malformed")
            continue
        transfer_id = transfer_id.strip()
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
    thresholds = _authority_thresholds(payload.get("thresholds"), reasons)
    gate_inputs, details = _derive_gate_inputs(
        loaded, (), rule_row=row, status=status,
        expected_status_version=replay_status_version,
        rule_digest=current_digest,
        min_obligation_coverage=thresholds["obligation_coverage"],
        min_cross_lineage_te=thresholds["cross_lineage_te"],
        max_harmful_rate=thresholds["harmful_rate"],
        min_conformal_coverage=thresholds["conformal_coverage"])
    reasons.extend(details.get("errors") or [])
    gate_report = evaluate_promotion_gates(
        gate_inputs, strict=True,
        min_obligation_coverage=thresholds["obligation_coverage"],
        min_cross_lineage_te=thresholds["cross_lineage_te"],
        max_harmful_rate=thresholds["harmful_rate"],
        min_conformal_coverage=thresholds["conformal_coverage"])
    for key in ("checks", "gate_status", "missing", "failed",
                "not_established", "all_gates_established"):
        if stable_dumps(payload.get(key)) != stable_dumps(gate_report.get(key)):
            reasons.append(f"authority_{key}_mismatch")
    trial_id = payload.get("trial_id")
    if trial_id is not None and (
            type(trial_id) is not str or not trial_id.strip()):
        reasons.append("authority_trial_id_malformed")
        trial_id = None
    elif isinstance(trial_id, str):
        trial_id = trial_id.strip()
    trial_ok, trial_binding, trial_reasons = _trial_binding(
        conn, rule_id=rule_id, target_scope=target_scope, trial_id=trial_id,
        expected_status_version=replay_status_version)
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
            stored_eligible = None
            try:
                stored_eligible = _stored_bool(
                    stored["eligible"], field="eligible")
            except ValueError:
                reasons.append("authority_receipt_row_eligible_malformed")
            try:
                stored_payload = json.loads(stored["receipt_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                stored_payload = None
            if not isinstance(stored_payload, Mapping) or stable_dumps(
                    dict(stored_payload)) != stable_dumps(payload):
                reasons.append("authority_receipt_row_mismatch")
            expected_row = (
                rule_id, target_scope, expected_version,
                (int(data.get("eligible"))
                 if type(data.get("eligible")) is bool else None),
                stored["receipt_json"], expected_digest)
            if stored_eligible is None or tuple(stored) != expected_row:
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
    "EXTERNAL_AUTHORITY_PROJECTION_VERSION",
    "RULE_EVIDENCE_TYPES", "RuleAuthorityReceipt",
    "build_causal_transfer_evidence", "build_trial_authority_evidence",
    "build_external_observation_authority_evidence",
    "build_external_observation_authority_evidence_batch",
    "record_rule_authority_from_external_observation_sources",
    "record_rule_authority_from_external_observations",
    "promote_rule",
    "record_rule_authority", "rule_content_digest", "verify_rule_authority",
]
