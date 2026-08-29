"""Real ORFS A/B executor for TEHM rules (design doc 20.11 / 24.3).

Arm A is the unmodified shared cold-start control.  Arm B receives exactly the
instantiated TEHM config rewrite.  Both arms run the shared ORFS flow and then
``fix_signoff.sh --max-iters 0``: this establishes the real DRC/LVS/timing
oracle but applies no additional catalog repair, preserving causal isolation.

The source project is never an execution target.  It is snapshotted before arm
creation, verified afterwards, and restored exactly if an external tool escapes
the sandbox.  Trial/activation evidence lands only in TEHM tables.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Mapping
from pathlib import Path

from contracts import RepairContext
from tehm import db as tehm_db
from tehm.activation.applicability import check_applicability
from tehm.activation.binding import bind_rule
from tehm.activation.instantiate import instantiate_rewrite
from tehm.activation.obligation_transfer import transfer_obligations
from tehm.activation.pipeline import ActivationRecord
from tehm.activation.update import persist_activation
from tehm.ids import stable_dumps
from tehm.lifecycle.authority import (
    apply_production_trial_verdict, apply_trial_verdict)
from tehm.lifecycle.promotion_gates import REQUIRED_GATES, evaluate_promotion_gates
from tehm.lifecycle.rule_authority import (
    build_trial_authority_evidence, record_rule_authority)
from tehm.lifecycle.rule_status import RuleLifecycleError, get_status
from tehm.lifecycle.trial_adapter import judge_trial, record_external_trial
from tehm.retrieval.index import build_index
from tehm.retrieval.result import APPLICABLE

# v0.4 changes strict production authority from caller gate-map diagnostics to
# a DB-bound RuleAuthorityReceipt.  The version participates in trial UUIDs so
# an older v0.3 trial cannot be mistaken for one that passed the new seam.
ORFS_TRIAL_VERSION = "orfs-trial-v0.4"
SUPPORTED_SCOPES = frozenset({"drc", "lvs", "timing", "route"})
_ADAPTER_AUTHORITY_GATES = frozenset({
    "rollback_verified", "obligation_coverage", "registry_verified"})
_INDEPENDENT_AUTHORITY_GATES = frozenset({"harmful_rate", "conformal_coverage"})
_CFG_RE = re.compile(r"^(\s*(?:override\s+)?(?:export\s+)?)([A-Z0-9_]+)(\s*[:?]?=\s*).*$")


def _strict_authority_from_metrics(metrics: Mapping) -> bool:
    """Fail closed when persisted authority-mode metadata is weakly typed.

    A malformed ``production_authority`` marker must never make recovery fall
    back to the compatibility lifecycle path.  Treat it as strict so the
    DB-bound receipt is required; well-typed false remains the legacy path.
    """
    marker = metrics.get("production_authority")
    if marker is not None and type(marker) is not bool:
        return True
    gate_map = metrics.get("promotion_gate_inputs")
    if gate_map is not None and not isinstance(gate_map, Mapping):
        return True
    return marker is True or gate_map is not None


def _record_orfs_rule_authority(
        conn: sqlite3.Connection, *, trial_id: str, rule_id: str,
        target_scope: str, expected_status_version: int,
        independent_evidence: Mapping | None = None,
        transfer_receipt_ids=None):
    """Build and persist the strict authority receipt for one ORFS trial.

    The trial adapter owns rollback, obligation and registry measurements.  A
    caller may add only independent payload-bearing utility/calibration rows;
    cross-lineage evidence is accepted through the replaying transfer-ledger
    seam.  If a persisted activation witness is malformed, the helper records
    an intentionally incomplete receipt rather than allowing a hand-authored
    replacement to reach production authority.
    """
    projection_error = None
    try:
        evidence = build_trial_authority_evidence(
            conn, trial_id=trial_id, rule_id=rule_id, target_scope=target_scope)
        if independent_evidence is None:
            independent_evidence = {}
        if not isinstance(independent_evidence, Mapping):
            raise ValueError("strict authority evidence must be a mapping")
        for gate, raw_entries in independent_evidence.items():
            if gate not in REQUIRED_GATES:
                raise ValueError(f"unsupported strict authority gate: {gate}")
            if gate in _ADAPTER_AUTHORITY_GATES:
                raise ValueError(
                    f"{gate} is adapter-owned and cannot be caller supplied")
            if gate == "cross_lineage_te":
                raise ValueError(
                    "cross_lineage_te requires causal transfer receipt IDs")
            if gate not in _INDEPENDENT_AUTHORITY_GATES:
                raise ValueError(f"unsupported strict authority gate: {gate}")
            if raw_entries is None:
                continue
            if isinstance(raw_entries, Mapping):
                raw_entries = [raw_entries]
            elif isinstance(raw_entries, (str, bytes)):
                raise ValueError(f"{gate} evidence must be row mappings")
            else:
                try:
                    raw_entries = list(raw_entries)
                except TypeError as exc:
                    raise ValueError(f"{gate} evidence must be iterable") from exc
            for entry in raw_entries:
                if not isinstance(entry, Mapping):
                    raise ValueError(f"{gate} evidence row must be a mapping")
                evidence[gate].append(dict(entry))
    except (TypeError, ValueError) as exc:
        # An incomplete receipt is still useful audit evidence, but it cannot
        # satisfy the adapter-owned gates.  Never fall back to caller booleans.
        projection_error = str(exc)
        evidence = {gate: [] for gate in REQUIRED_GATES}
        if isinstance(independent_evidence, Mapping):
            for gate in _INDEPENDENT_AUTHORITY_GATES:
                raw_entries = independent_evidence.get(gate)
                if raw_entries is None:
                    continue
                if isinstance(raw_entries, Mapping):
                    evidence[gate] = [dict(raw_entries)]
                elif not isinstance(raw_entries, (str, bytes)):
                    try:
                        evidence[gate] = [dict(entry) for entry in raw_entries
                                          if isinstance(entry, Mapping)]
                    except TypeError:
                        pass

    receipt = record_rule_authority(
        conn, rule_id=rule_id, target_scope=target_scope, evidence=evidence,
        trial_id=trial_id, expected_status_version=expected_status_version,
        causal_transfer_receipt_ids=transfer_receipt_ids)
    return receipt, projection_error


def run_pending_orfs_trials(
        conn: sqlite3.Connection, store, *, base_entries: list[dict],
        run_flow_script: Path, fix_signoff_script: Path,
        n_designs: int = 1, repeats: int = 2,
        work_root: Path | None = None, env: dict | None = None,
        provided_bindings: dict[str, dict] | None = None,
        lifecycle_statuses: frozenset[str] = frozenset({"candidate"}),
        mutate_lifecycle: bool = True,
        promotion_gate_inputs: dict[str, dict] | None = None,
        production_authority: bool = False,
        promotion_authority_evidence: dict[str, dict] | None = None,
        causal_transfer_receipt_ids: dict[str, object] | None = None) -> list[dict]:
    """Execute selected admissible lifecycle rules on on-disk subjects.

    The default is the promotion-authority candidate drain.  A caller may
    explicitly select ``{"promoted"}`` with ``mutate_lifecycle=False`` for
    held-out revalidation: trial/activation/rollback evidence is persisted, but
    the registry status and version are immutable.
    ``production_authority=True`` enables the six-gate firewall for every
    lifecycle mutation.  This is required by the backend; the default remains
    compatibility mode for deterministic unit fixtures that exercise only the
    older A/B adapter contract.  In strict mode, rollback/obligation/registry
    evidence is projected from the persisted trial and activation rows, while
    independent harmful/conformal evidence may be supplied as payload-bearing
    rows through ``promotion_authority_evidence``.  Cross-lineage TE must be
    supplied as replay-verified causal-transfer receipt IDs.  The legacy
    ``promotion_gate_inputs`` map remains diagnostic only in strict mode.
    """
    if type(mutate_lifecycle) is not bool:
        raise ValueError("mutate_lifecycle must be a boolean")
    if type(production_authority) is not bool:
        raise ValueError("production_authority must be a boolean")
    allowed_statuses = frozenset(lifecycle_statuses)
    if not allowed_statuses or not allowed_statuses <= {"candidate", "promoted"}:
        raise ValueError("ORFS trials accept only candidate/promoted lifecycle statuses")
    if mutate_lifecycle and allowed_statuses != {"candidate"}:
        raise ValueError("lifecycle authority mutation is candidate-only")
    index = build_index(conn, lifecycle_statuses=allowed_statuses)
    provided_bindings = provided_bindings or {}
    if promotion_authority_evidence is None:
        promotion_authority_evidence = {}
    if causal_transfer_receipt_ids is None:
        causal_transfer_receipt_ids = {}
    if (not isinstance(promotion_authority_evidence, Mapping) or
            not isinstance(causal_transfer_receipt_ids, Mapping)):
        raise ValueError(
            "strict authority evidence and transfer selections must be mappings")
    if ((promotion_authority_evidence or causal_transfer_receipt_ids) and
            not (production_authority and mutate_lifecycle)):
        raise ValueError(
            "authority evidence selections require strict lifecycle mutation")
    placeholders = ",".join("?" for _ in allowed_statuses)
    rows = conn.execute(
        "SELECT rule_id, target_scope, status, status_version FROM tehm_rule_status "
        f"WHERE status IN ({placeholders}) ORDER BY rule_id, target_scope",
        tuple(sorted(allowed_statuses))).fetchall()
    # The selection query is only a cheap candidate scan.  Normalize each row
    # through the lifecycle reader before using its version/status in a trial
    # UUID or lifecycle decision; malformed derived state is skipped.
    validated_rows = []
    for raw_row in rows:
        try:
            status = get_status(
                conn, rule_id=raw_row["rule_id"],
                target_scope=raw_row["target_scope"])
        except RuleLifecycleError:
            continue
        if status is None or status["status"] not in allowed_statuses:
            continue
        normalized = dict(raw_row)
        normalized.update({
            "status": status["status"],
            "status_version": status["status_version"],
        })
        validated_rows.append(normalized)
    rows = validated_rows
    results: list[dict] = []
    subjects = [e for e in base_entries
                if e.get("kind", "normal") == "normal"
                and Path(e.get("project_path", "")).is_dir()]

    for row in rows:
        rule_id, scope = row["rule_id"], row["target_scope"]
        rule = index.get(rule_id)
        if rule is None or scope not in SUPPORTED_SCOPES:
            continue
        selected = subjects[:max(1, n_designs)]
        if not selected:
            continue
        source_digests = [_snapshot_digest(_snapshot_source(
            Path(e["project_path"]))) for e in selected]
        trial_uuid = hashlib.sha1(stable_dumps({
            "rule": rule_id, "scope": scope,
            "status_version": row["status_version"],
            "status": row["status"],
            "subjects": [str(Path(e["project_path"]).resolve()) for e in selected],
            "source_digests": source_digests,
            "repeats": repeats, "version": ORFS_TRIAL_VERSION,
            "authority_mode": "lifecycle" if mutate_lifecycle else "revalidation",
        }).encode()).hexdigest()[:20]

        # Idempotency: a completed external trial is never re-executed.
        existing = conn.execute(
            "SELECT verdict, metrics_json, status_version FROM tehm_trials "
            "WHERE trial_uuid=?",
            (trial_uuid,)).fetchone()
        if existing is not None:
            metrics = tehm_db.read_json(existing["metrics_json"])
            registry = metrics.get("registry_authority") or {}
            new_status = registry.get("authorized_status")
            # Crash recovery: record_external_trial intentionally lands before
            # the lifecycle mutation. If a process died in that window, replay
            # the same authority decision against the stamped status_version.
            if not registry and mutate_lifecycle:
                recovered_gates = metrics.get("promotion_gate_inputs")
                strict_recovery = (
                    production_authority is True or
                    _strict_authority_from_metrics(metrics))
                # The legacy map is diagnostic only when strict authority is
                # active.  Recovery must replay the DB-bound witness rows.
                recovered_inputs = ({} if strict_recovery
                                    else dict(recovered_gates or {}))
                recovered_inputs.update({
                    "rollback_verified": metrics.get("rollback_verified") is True,
                    "obligation_coverage": metrics.get("obligation_coverage"),
                    "registry_verified": (
                        row["status"] == "candidate" and
                        get_status(conn, rule_id=rule_id,
                                   target_scope=scope).get("status_version") ==
                        existing["status_version"]),
                })
                authority_receipt = None
                authority_projection_error = None
                if strict_recovery:
                    authority_receipt, authority_projection_error = (
                        _record_orfs_rule_authority(
                            conn, trial_id=f"trial_{trial_uuid}",
                            rule_id=rule_id, target_scope=scope,
                            expected_status_version=existing["status_version"],
                            independent_evidence=(
                                promotion_authority_evidence.get(rule_id)
                                if production_authority else None),
                            transfer_receipt_ids=(
                                causal_transfer_receipt_ids.get(rule_id)
                                if production_authority and rule_id in
                                causal_transfer_receipt_ids else None)))
                authority = (apply_production_trial_verdict
                             if strict_recovery else apply_trial_verdict)
                new_status = authority(
                    conn, rule_id=rule_id, target_scope=scope,
                    verdict=existing["verdict"],
                    obligation_coverage=metrics.get("obligation_coverage"),
                    created_regressions=metrics.get("created_regressions") or [],
                    arms_differ=(metrics.get("arms_differ") is True and
                                 metrics.get("rollback_verified") is True),
                    expected_status_version=existing["status_version"],
                    provenance={"trial_uuid": trial_uuid,
                                "executor": ORFS_TRIAL_VERSION,
                                "recovered": True},
                    promotion_gates=recovered_inputs,
                    strict_promotion_gates=strict_recovery,
                    authority_receipt=authority_receipt)
                after = get_status(conn, rule_id=rule_id, target_scope=scope) or {}
                registry = {
                    "before": {"status": "candidate",
                               "status_version": existing["status_version"]},
                    "after": after, "authorized_status": new_status,
                    "verified": (after.get("status_version") ==
                                 existing["status_version"] + (1 if new_status else 0)),
                }
                if authority_receipt is not None:
                    metrics["authority_receipt"] = authority_receipt.to_dict()
                    metrics["promotion_gates"] = {
                        "source": "db_bound_rule_authority",
                        "authority_receipt_id": (
                            authority_receipt.authority_receipt_id),
                        "eligible": authority_receipt.eligible,
                        "checks": dict(authority_receipt.checks),
                        "gate_status": dict(authority_receipt.gate_status),
                        "missing": list(authority_receipt.missing),
                        "failed": list(authority_receipt.failed),
                        "not_established": list(
                            authority_receipt.not_established),
                    }
                if authority_projection_error:
                    metrics["authority_projection_error"] = (
                        authority_projection_error)
                metrics["registry_authority"] = registry
                conn.execute(
                    "UPDATE tehm_trials SET metrics_json=? WHERE trial_uuid=?",
                    (stable_dumps(metrics), trial_uuid))
                conn.commit()
            elif not registry:
                after = get_status(conn, rule_id=rule_id, target_scope=scope) or {}
                registry = {
                    "before": {"status": row["status"],
                               "status_version": existing["status_version"]},
                    "after": after, "authorized_status": None,
                    "verified": (after.get("status") == row["status"] and
                                 after.get("status_version") ==
                                 existing["status_version"]),
                    "mode": "revalidation_no_mutation",
                }
                metrics["registry_authority"] = registry
                conn.execute(
                    "UPDATE tehm_trials SET metrics_json=? WHERE trial_uuid=?",
                    (stable_dumps(metrics), trial_uuid))
                conn.commit()
            results.append({"rule_id": rule_id, "trial_uuid": trial_uuid,
                            "verdict": existing["verdict"], "reused": True,
                            "new_status": new_status})
            continue

        pairs = []
        for subject in selected:
            for repeat in range(max(1, repeats)):
                pairs.append(_run_pair(
                    conn, rule=rule, rule_id=rule_id, scope=scope,
                    subject=subject, repeat=repeat, trial_uuid=trial_uuid,
                    run_flow_script=Path(run_flow_script),
                    fix_signoff_script=Path(fix_signoff_script),
                    work_root=work_root, env=env,
                    provided_binding=provided_bindings.get(rule_id)))

        a_samples = [1.0 if p["arm_a"]["success"] else 0.0 for p in pairs]
        b_samples = [1.0 if p["arm_b"]["success"] else 0.0 for p in pairs]
        verdict, reason = judge_trial(a_samples, b_samples)
        arms_differ = bool(pairs) and all(p["arms_differ"] for p in pairs)
        regressions = sorted({r for p in pairs
                              for r in p["created_regressions"]})
        coverages = [p["obligation_coverage"] for p in pairs
                     if p["obligation_coverage"] is not None]
        coverage = min(coverages) if coverages else 0.0
        rollback_ok = bool(pairs) and all(
            p["rollback_receipt"]["verified"] for p in pairs)
        infrastructure_failure = _infrastructure_failures(pairs)
        if infrastructure_failure:
            verdict, reason = "inconclusive", (
                "A/B infrastructure failure: " + ",".join(infrastructure_failure))
            for pair in pairs:
                if pair.get("activation_id"):
                    conn.execute(
                        "UPDATE tehm_activations SET verification_status='UNKNOWN', "
                        "outcome='INFRA_ERROR' WHERE activation_id=?",
                        (pair["activation_id"],))
        if not rollback_ok:
            verdict, reason = "inconclusive", "source rollback verification failed"

        metrics = {
            "executor_version": ORFS_TRIAL_VERSION,
            "lifecycle_mode": ("promotion_authority" if mutate_lifecycle
                               else "promoted_revalidation"),
            "production_authority": bool(production_authority and mutate_lifecycle),
            "reason": reason, "A_samples": a_samples, "B_samples": b_samples,
            "arms_differ": arms_differ,
            "obligation_coverage": coverage,
            "created_regressions": regressions,
            "rollback_verified": rollback_ok,
            "infrastructure_failure": infrastructure_failure,
            "pairs": pairs,
        }
        if (mutate_lifecycle and promotion_gate_inputs is not None and
                rule_id in promotion_gate_inputs):
            metrics["promotion_gate_inputs"] = dict(
                promotion_gate_inputs.get(rule_id) or {})
        trial = record_external_trial(
            conn, rule_id=rule_id, target_scope=scope, verdict=verdict,
            metrics=metrics, status_version=row["status_version"],
            trial_uuid=trial_uuid,
            arm_a_run_id=pairs[0]["arm_a"]["run_id"] if pairs else None,
            arm_b_run_id=pairs[0]["arm_b"]["run_id"] if pairs else None)
        new_status = None
        authority_receipt = None
        authority_projection_error = None
        if mutate_lifecycle:
            if production_authority:
                # Strict authority is built from immutable DB-bound evidence;
                # the legacy gate map remains diagnostic only.
                authority_receipt, authority_projection_error = (
                    _record_orfs_rule_authority(
                        conn, trial_id=trial["trial_id"], rule_id=rule_id,
                        target_scope=scope,
                        expected_status_version=row["status_version"],
                        independent_evidence=(
                            promotion_authority_evidence.get(rule_id)),
                        transfer_receipt_ids=(
                            causal_transfer_receipt_ids.get(rule_id)
                            if rule_id in causal_transfer_receipt_ids else None)))
                supplied_gates = {}
            else:
                supplied_gates = dict(
                    (promotion_gate_inputs or {}).get(rule_id) or {})
                # Compatibility-mode callers retain the historical adapter
                # measurements.  They are never used by strict production.
                supplied_gates.update({"rollback_verified": rollback_ok,
                                       "obligation_coverage": coverage})
                supplied_gates["registry_verified"] = (
                    row["status"] == "candidate" and
                    get_status(conn, rule_id=rule_id, target_scope=scope).get(
                        "status_version") == row["status_version"])
            strict_gates = (production_authority or
                            (promotion_gate_inputs is not None and
                             rule_id in promotion_gate_inputs))
            authority = (apply_production_trial_verdict
                         if production_authority else apply_trial_verdict)
            new_status = authority(
                conn, rule_id=rule_id, target_scope=scope, verdict=verdict,
                obligation_coverage=coverage, created_regressions=regressions,
                arms_differ=arms_differ and rollback_ok,
                expected_status_version=row["status_version"],
                provenance={"trial_uuid": trial_uuid,
                            "executor": ORFS_TRIAL_VERSION},
                promotion_gates=supplied_gates,
                strict_promotion_gates=strict_gates,
                authority_receipt=authority_receipt)
        registry_after = get_status(
            conn, rule_id=rule_id, target_scope=scope) or {}
        if not mutate_lifecycle:
            registry_verified = (
                registry_after.get("status") == row["status"] and
                registry_after.get("status_version") == row["status_version"])
        elif new_status is None:
            registry_verified = (
                registry_after.get("status") == "candidate" and
                registry_after.get("status_version") == row["status_version"])
        else:
            registry_verified = (
                registry_after.get("status") == new_status and
                registry_after.get("status_version") == row["status_version"] + 1)
        metrics["registry_authority"] = {
            "before": {"status": row["status"],
                       "status_version": row["status_version"]},
            "after": registry_after, "authorized_status": new_status,
            "verified": registry_verified,
            "mode": ("promotion_authority" if mutate_lifecycle
                     else "revalidation_no_mutation"),
        }
        if mutate_lifecycle and strict_gates:
            if production_authority and authority_receipt is not None:
                metrics["authority_receipt"] = authority_receipt.to_dict()
                metrics["promotion_gates"] = {
                    "source": "db_bound_rule_authority",
                    "authority_receipt_id": (
                        authority_receipt.authority_receipt_id),
                    "eligible": authority_receipt.eligible,
                    "checks": dict(authority_receipt.checks),
                    "gate_status": dict(authority_receipt.gate_status),
                    "missing": list(authority_receipt.missing),
                    "failed": list(authority_receipt.failed),
                    "not_established": list(
                        authority_receipt.not_established),
                }
            else:
                gate_inputs = dict(
                    (promotion_gate_inputs or {}).get(rule_id) or {})
                gate_inputs.update({"rollback_verified": rollback_ok,
                                    "registry_verified": registry_verified,
                                    "obligation_coverage": coverage})
                metrics["promotion_gates"] = evaluate_promotion_gates(
                    gate_inputs, strict=True, min_obligation_coverage=1.0)
        if authority_projection_error:
            metrics["authority_projection_error"] = authority_projection_error
        conn.execute(
            "UPDATE tehm_trials SET metrics_json=? WHERE trial_uuid=?",
            (stable_dumps(metrics), trial_uuid))
        conn.commit()
        trial["new_status"] = new_status
        trial["metrics"] = metrics
        results.append(trial)
    return results


def _infrastructure_failures(pairs: list[dict]) -> list[str]:
    """Classify explicit executor/environment failures, never design failures."""
    signatures = {
        "hierarchical_design_config_not_staged": (
            "platform variable net set", "platform variable not set"),
        "project_inputs_missing": ("project_inputs_missing", "r2g_inputs_missing"),
        "workspace_not_writable": ("read-only file system", "permission denied"),
    }
    text = "\n".join(
        str((pair.get(arm) or {}).get(key) or "").lower()
        for pair in pairs for arm in ("arm_a", "arm_b")
        for key in ("flow_stdout_tail", "flow_stderr_tail"))
    failures = {name for name, needles in signatures.items()
                if any(needle in text for needle in needles)}
    # A wall-clock kill is an executor/tool budget failure, not a measured
    # design outcome.  Without this gate an arm B success paired with an arm A
    # timeout could be incorrectly promoted as a causal win.
    if any((pair.get(arm) or {}).get("flow_rc") == 124
           for pair in pairs for arm in ("arm_a", "arm_b")) or "timed out" in text:
        failures.add("flow_timeout")
    return sorted(failures)


def reconcile_route_trial_evidence(
        conn: sqlite3.Connection, *, trial_uuid: str,
        extract_route_script: Path) -> dict:
    """Re-grade a completed route trial from its preserved arm evidence.

    This is a narrow crash/adapter-recovery path, not a way to change measured
    outcomes.  Each arm's collected ``stage_log.jsonl`` + ``flow.log`` is fed
    through the production route extractor again, activations are reconciled,
    and lifecycle authority is replayed against the trial's frozen status
    version.  It is useful when an extractor contract is repaired after an A/B
    run; no PnR command is re-executed and no report is fabricated.
    """
    row = conn.execute(
        "SELECT rule_id,target_scope,metrics_json,status_version FROM tehm_trials "
        "WHERE trial_uuid=?", (trial_uuid,)).fetchone()
    if row is None:
        raise KeyError(f"ORFS trial not found: {trial_uuid}")
    if row["target_scope"] != "route":
        raise ValueError("route evidence reconciliation only accepts route trials")
    metrics = tehm_db.read_json(row["metrics_json"])
    pairs = metrics.get("pairs") or []
    if not pairs:
        raise ValueError("route trial has no preserved arm pairs")

    for pair in pairs:
        sandbox = Path((pair.get("rollback_receipt") or {}).get("sandbox_root", ""))
        for arm_key, directory in (("arm_a", "arm_a"), ("arm_b", "arm_b")):
            project = sandbox / directory
            out = project / "reports" / "route.json"
            proc = subprocess.run(
                [sys.executable, str(Path(extract_route_script)), str(project), str(out)],
                capture_output=True, text=True)
            if proc.returncode != 0 or not out.is_file():
                raise RuntimeError(
                    f"route evidence extraction failed for {project}: {proc.stderr[-300:]}")
            reports = _load_reports(project)
            arm = pair[arm_key]
            arm["reports"] = reports
            arm["success"] = (arm.get("flow_rc") == 0 and
                              _scope_success("route", reports))
        pair["created_regressions"] = _created_regressions(
            pair["arm_a"]["reports"], pair["arm_b"]["reports"], "route")
        activation_id = pair.get("activation_id")
        if activation_id:
            success = pair["arm_b"].get("success") is True
            conn.execute(
                "UPDATE tehm_activations SET verification_status=?, verifier_json=?, "
                "outcome=?, created_regressions_json=? WHERE activation_id=?",
                ("PASS" if success else "FAIL",
                 stable_dumps({"oracle_type": "ORFS_SIGNOFF",
                               "arm_b": pair["arm_b"],
                               "reconciled": True}),
                 "PASS" if success else "FAIL",
                 stable_dumps(pair["created_regressions"]), activation_id))

    a_samples = [1.0 if p["arm_a"]["success"] else 0.0 for p in pairs]
    b_samples = [1.0 if p["arm_b"]["success"] else 0.0 for p in pairs]
    verdict, reason = judge_trial(a_samples, b_samples)
    regressions = sorted({item for p in pairs
                          for item in p.get("created_regressions", [])})
    rollback_ok = all((p.get("rollback_receipt") or {}).get("verified") is True
                      for p in pairs)
    arms_differ = all(p.get("arms_differ") is True for p in pairs)
    coverage = min((p.get("obligation_coverage") for p in pairs
                    if p.get("obligation_coverage") is not None), default=0.0)
    if not rollback_ok:
        verdict, reason = "inconclusive", "source rollback verification failed"
    metrics.update({
        "reason": reason, "A_samples": a_samples, "B_samples": b_samples,
        "arms_differ": arms_differ, "created_regressions": regressions,
        "obligation_coverage": coverage, "rollback_verified": rollback_ok,
        "pairs": pairs,
        "evidence_reconciliation": {
            "extractor": str(Path(extract_route_script).resolve()),
            "mode": "preserved_stage_log_and_flow_log",
            "no_pnr_reexecution": True,
        },
    })

    rule_id, base_version = row["rule_id"], row["status_version"]
    current = get_status(conn, rule_id=rule_id, target_scope="route") or {}
    persisted_gates = metrics.get("promotion_gate_inputs")
    reconciliation_gates = (
        dict(persisted_gates) if isinstance(persisted_gates, Mapping) else {})
    strict_reconciliation = _strict_authority_from_metrics(metrics)
    authority_receipt = None
    authority_projection_error = None
    new_status = None
    if (current.get("status") == "candidate" and
            current.get("status_version") == base_version):
        if strict_reconciliation:
            reconciliation_gates = {}
            authority_receipt, authority_projection_error = (
                _record_orfs_rule_authority(
                    conn, trial_id=f"trial_{trial_uuid}", rule_id=rule_id,
                    target_scope="route", expected_status_version=base_version))
        else:
            reconciliation_gates.update({
                "rollback_verified": rollback_ok,
                "obligation_coverage": coverage,
                "registry_verified": (
                    current.get("status") == "candidate" and
                    current.get("status_version") == base_version),
            })
        authority = (apply_production_trial_verdict
                     if strict_reconciliation
                     else apply_trial_verdict)
        new_status = authority(
            conn, rule_id=rule_id, target_scope="route", verdict=verdict,
            obligation_coverage=coverage, created_regressions=regressions,
            arms_differ=arms_differ and rollback_ok,
            expected_status_version=base_version,
            provenance={"trial_uuid": trial_uuid, "executor": ORFS_TRIAL_VERSION,
                        "evidence_reconciled": True},
            promotion_gates=reconciliation_gates,
            strict_promotion_gates=strict_reconciliation,
            authority_receipt=authority_receipt)
        current = get_status(conn, rule_id=rule_id, target_scope="route") or {}
    elif (current.get("status") == "promoted" and
          current.get("status_version") == base_version + 1):
        new_status = "promoted"
        strict_reconciliation = _strict_authority_from_metrics(metrics)
    metrics["registry_authority"] = {
        "before": {"status": "candidate", "status_version": base_version},
        "after": current, "authorized_status": new_status,
        "verified": bool(
            (new_status == "promoted" and current.get("status") == "promoted" and
             current.get("status_version") == base_version + 1) or
            (new_status is None and current.get("status") == "candidate" and
             current.get("status_version") == base_version)),
    }
    if strict_reconciliation:
        if authority_receipt is not None:
            metrics["authority_receipt"] = authority_receipt.to_dict()
            metrics["promotion_gates"] = {
                "source": "db_bound_rule_authority",
                "authority_receipt_id": authority_receipt.authority_receipt_id,
                "eligible": authority_receipt.eligible,
                "checks": dict(authority_receipt.checks),
                "gate_status": dict(authority_receipt.gate_status),
                "missing": list(authority_receipt.missing),
                "failed": list(authority_receipt.failed),
                "not_established": list(authority_receipt.not_established),
            }
        else:
            metrics["promotion_gates"] = evaluate_promotion_gates(
                reconciliation_gates, strict=True,
                min_obligation_coverage=1.0)
    if authority_projection_error:
        metrics["authority_projection_error"] = authority_projection_error
    conn.execute(
        "UPDATE tehm_trials SET verdict=?, metrics_json=? WHERE trial_uuid=?",
        (verdict, stable_dumps(metrics), trial_uuid))
    conn.commit()
    return {"trial_uuid": trial_uuid, "verdict": verdict,
            "new_status": new_status, "metrics": metrics}


def _run_pair(conn, *, rule: dict, rule_id: str, scope: str,
              subject: dict, repeat: int, trial_uuid: str,
              run_flow_script: Path, fix_signoff_script: Path,
              work_root: Path | None, env: dict | None,
              provided_binding: dict | None) -> dict:
    source = Path(subject["project_path"]).resolve()
    platform = subject.get("platform") or _parse_config(
        source / "constraints" / "config.mk").get("PLATFORM") or "asap7"
    root = Path(work_root) if work_root else source.parent / ".tehm_ab"
    pair_root = root / f"{source.name}_{rule_id[-10:]}_{trial_uuid}_{repeat}"
    if pair_root.exists():
        shutil.rmtree(pair_root)
    pair_root.mkdir(parents=True)
    arm_a, arm_b = pair_root / "arm_a", pair_root / "arm_b"

    source_snapshot = _snapshot_source(source)
    source_before = _snapshot_digest(source_snapshot)
    for arm in (arm_a, arm_b):
        shutil.copytree(source, arm, ignore=shutil.ignore_patterns(
            # Never carry a prior ORFS workspace into a fresh causal arm.  A
            # stale WORK_HOME/ORFS design staging tree can make ``make`` reuse
            # an old result and silently erase the A/B contrast.
            "backend", "reports", "drc", "lvs", "rcx", ".orfs-work",
            ".orfs-design", ".tehm_ab", "features", "*.gds"))
    baseline_a = _snapshot_digest(_snapshot_source(arm_a))
    baseline_b = _snapshot_digest(_snapshot_source(arm_b))

    reports = _load_reports(source)
    context = RepairContext(
        project_dir=arm_b, design_id=subject.get("design") or source.name,
        platform=platform, check=scope, reports=reports,
        cfg=_parse_config(arm_b / "constraints" / "config.mk"))
    applicable = check_applicability(rule, context)
    binding = bind_rule(rule, context, provided_binding=provided_binding)
    obligations = transfer_obligations(rule, context)
    action = instantiate_rewrite(rule, binding, context)
    edits = ((action.get("payload") or {}).get("config_edits") or {})
    executable = applicable == APPLICABLE and binding.status == "BOUND" and bool(edits)
    if executable:
        _apply_config_edits(arm_b / "constraints" / "config.mk", edits)
    if executable:
        # The variants are distinct (arm_a / arm_b), so the production ORFS
        # workspace lock keeps them isolated and both may run concurrently.
        # Repeats remain sequential because their variant basenames coincide.
        # High-utilization ORFS subjects can exceed the host memory budget when
        # two OpenROAD processes are launched together.  The explicit serial
        # switch preserves the default parallel path while making such trials
        # reproducibly distinguish infrastructure pressure from an A/B result.
        if os.environ.get("R2G_ORFS_SERIAL_AB") == "1":
            action_a = _execute_arm(
                arm_a, platform, scope, run_flow_script, fix_signoff_script, env)
            action_b = _execute_arm(
                arm_b, platform, scope, run_flow_script, fix_signoff_script, env)
        else:
            with ThreadPoolExecutor(max_workers=2) as pool:
                future_a = pool.submit(_execute_arm, arm_a, platform, scope,
                                       run_flow_script, fix_signoff_script, env)
                future_b = pool.submit(_execute_arm, arm_b, platform, scope,
                                       run_flow_script, fix_signoff_script, env)
                action_a, action_b = future_a.result(), future_b.result()
    else:
        action_a, action_b = _not_run("control"), _not_run("rule")
    arms_differ = baseline_a == baseline_b and _snapshot_digest(
        _snapshot_source(arm_a)) != _snapshot_digest(_snapshot_source(arm_b))
    regressions = _created_regressions(action_a["reports"], action_b["reports"], scope)

    # The source must remain byte-identical. Restore the scoped source snapshot
    # if an external command escaped its sandbox, then verify exact recovery.
    source_after_run = _snapshot_digest(_snapshot_source(source))
    restore_required = source_after_run != source_before
    if restore_required:
        _restore_source(source, source_snapshot)
    source_after_restore = _snapshot_digest(_snapshot_source(source))
    rollback = {
        "scope": ["constraints", "rtl"],
        "source_before_digest": source_before,
        "source_after_run_digest": source_after_run,
        "source_after_restore_digest": source_after_restore,
        "restore_required": restore_required,
        "restored": restore_required and source_after_restore == source_before,
        "verified": source_after_restore == source_before,
        "sandbox_root": str(pair_root),
        "registry_status_version": (get_status(
            conn, rule_id=rule_id, target_scope=scope) or {}).get("status_version"),
    }

    activation_id = "act_ab_" + hashlib.sha1(stable_dumps({
        "trial": trial_uuid, "subject": str(source), "repeat": repeat,
        "rule": rule_id}).encode()).hexdigest()[:18]
    activation = ActivationRecord(
        activation_id=activation_id, rule_id=rule_id,
        target_state_id=f"trial:{trial_uuid}:{repeat}",
        retrieval_receipt={"source": "tehm_rule", "forced_ab": True},
        applicability_status=applicable, binding_status=binding.status,
        binding=binding.to_dict(),
        executability_status="EXECUTABLE" if executable else "NOT_EXECUTABLE",
        obligation_transfer=obligations,
        obligation_coverage=obligations["obligation_coverage"],
        verification_status="PASS" if action_b["success"] else "FAIL",
        # Preserve the transferred-obligation result in the verifier receipt;
        # H7 audits all three copies (activation field, transfer, verifier),
        # so a real PASS cannot be mistaken for an unverified activation.
        verifier={"oracle_type": "ORFS_SIGNOFF", "arm_b": action_b,
                  "obligation_coverage": obligations["obligation_coverage"]},
        outcome="PASS" if action_b["success"] else "FAIL",
        created_regressions=regressions, rollback_receipt=rollback,
        trial_uuid=trial_uuid, created_at=tehm_db.now_local())
    persist_activation(conn, activation)

    return {
        "subject": str(source),
        "subject_lineage": subject.get("lineage_id") or subject.get("design") or source.name,
        "repeat": repeat,
        "applicability_status": applicable, "binding_status": binding.status,
        "action": action, "arm_a": action_a, "arm_b": action_b,
        "arms_differ": arms_differ, "created_regressions": regressions,
        "obligation_coverage": obligations["obligation_coverage"],
        "rollback_receipt": rollback, "activation_id": activation_id,
    }


def _execute_arm(project: Path, platform: str, scope: str,
                 run_flow_script: Path, fix_signoff_script: Path,
                 extra_env: dict | None) -> dict:
    trial_env = dict(os.environ)
    trial_env.update(extra_env or {})
    trial_env.update({
        "R2G_MEMORY_BACKEND": "tehm",
        "R2G_MEMORY_READ_ONLY_EVAL": "1",
        "R2G_JOURNAL": "0",
    })
    flow = subprocess.run(
        ["bash", str(run_flow_script), str(project), platform],
        capture_output=True, text=True, env=trial_env)
    fix = None
    if flow.returncode == 0:
        fix = subprocess.run(
            ["bash", str(fix_signoff_script), str(project), platform,
             "--check", scope, "--max-iters", "0"],
            capture_output=True, text=True, env=trial_env)
    reports = _load_reports(project)
    success = flow.returncode == 0 and _scope_success(scope, reports)
    run_id = "orfs_" + hashlib.sha1(stable_dumps({
        "project": str(project), "flow_rc": flow.returncode,
        "fix_rc": fix.returncode if fix else None, "reports": reports,
    }).encode()).hexdigest()[:18]
    return {
        "run_id": run_id, "success": success,
        "flow_rc": flow.returncode, "fix_rc": fix.returncode if fix else None,
        "reports": reports,
        "flow_stdout_tail": flow.stdout[-500:], "flow_stderr_tail": flow.stderr[-500:],
        "fix_stdout_tail": fix.stdout[-500:] if fix else "",
        "fix_stderr_tail": fix.stderr[-500:] if fix else "",
    }


def _not_run(arm: str) -> dict:
    return {"run_id": None, "success": False, "flow_rc": None,
            "fix_rc": None, "reports": {}, "not_run": arm}


def _scope_success(scope: str, reports: dict) -> bool:
    report = reports.get(scope) or {}
    if scope == "timing":
        return report.get("tier") in {"clean", "met"} or report.get("status") == "clean"
    if scope == "route":
        return report.get("status") in {"clean", "complete", "pass"}
    return report.get("status") in {"clean", "clean_beol", "pass"}


def _created_regressions(before: dict, after: dict, target_scope: str) -> list[str]:
    regressions = []
    for scope in ("drc", "lvs", "route", "timing"):
        if scope == target_scope:
            continue
        if _scope_success(scope, before) and not _scope_success(scope, after):
            regressions.append(scope)
    return regressions


def _load_reports(project: Path) -> dict:
    out = {}
    for name, filename in (("drc", "drc.json"), ("lvs", "lvs.json"),
                           ("route", "route.json"),
                           ("timing", "timing_check.json"),
                           ("ppa", "ppa.json")):
        try:
            out[name] = json.loads((project / "reports" / filename).read_text())
        except Exception:
            out[name] = {}
    return out


def _parse_config(path: Path) -> dict:
    out = {}
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return out
    for line in lines:
        match = _CFG_RE.match(line)
        if match:
            out[match.group(2)] = line.split("=", 1)[1].strip()
    return out


def _apply_config_edits(path: Path, edits: dict) -> None:
    lines = path.read_text().splitlines() if path.is_file() else []
    pending = {str(k): str(v) for k, v in edits.items()}
    out = []
    for line in lines:
        match = _CFG_RE.match(line)
        if match and match.group(2) in pending:
            key = match.group(2)
            out.append(f"{match.group(1)}{key}{match.group(3)}{pending.pop(key)}")
        else:
            out.append(line)
    out.extend(f"export {key} = {value}" for key, value in sorted(pending.items()))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out) + "\n")


def _snapshot_source(project: Path) -> dict[str, bytes]:
    snapshot = {}
    for dirname in ("constraints", "rtl"):
        root = project / dirname
        if not root.is_dir():
            continue
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            snapshot[str(path.relative_to(project))] = path.read_bytes()
    return snapshot


def _snapshot_digest(snapshot: dict[str, bytes]) -> str:
    h = hashlib.sha256()
    for rel, data in sorted(snapshot.items()):
        h.update(rel.encode()); h.update(b"\0"); h.update(data); h.update(b"\0")
    return h.hexdigest()


def _restore_source(project: Path, snapshot: dict[str, bytes]) -> None:
    expected = set(snapshot)
    for dirname in ("constraints", "rtl"):
        root = project / dirname
        if root.is_dir():
            for path in sorted((p for p in root.rglob("*") if p.is_file()), reverse=True):
                if str(path.relative_to(project)) not in expected:
                    path.unlink()
    for rel, data in snapshot.items():
        path = project / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
