"""Real, isolated RTL A/B trial adapter.

This is the RTL counterpart of :mod:`orfs_trial`: both arms run the real
Icarus target/regression oracle in disposable workspaces, the B arm is driven
through the normal TEHM activation pipeline, and the source tree is restored
and digest-checked before the pair is accepted.  Trial evidence is written to
``tehm_trials`` and activation receipts to ``tehm_activations``; no legacy
registry is consulted.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
from collections.abc import Mapping
from pathlib import Path

from contracts import RepairContext
from tehm import db as tehm_db
from tehm.activation.pipeline import activate
from tehm.artifact_store import ArtifactStore
from tehm.ids import stable_dumps
from tehm.lifecycle.authority import (
    apply_production_trial_verdict, apply_trial_verdict)
from tehm.lifecycle.promotion_gates import evaluate_promotion_gates
from tehm.lifecycle.rule_status import get_status
from tehm.lifecycle.trial_adapter import judge_trial, record_external_trial
from tehm.rtl.rtl_actions import apply_rtl_action
from tehm.rtl.rtl_graph import build_rtl_graph
from tehm.rtl.rtl_oracle import IcarusOracle
from tehm.rtl.verilog_parse import parse_verilog
from tehm.retrieval.index import build_index


RTL_EXTERNAL_TRIAL_VERSION = "rtl-external-trial-v1"


def _derive_rtl_utility_verdict(control_result: Mapping,
                                candidate_result: Mapping) -> str:
    """Derive a paired RTL utility verdict from the two immutable oracle arms.

    The target/regression oracle establishes whether an arm is executable and
    verified; it does not by itself establish utility.  Utility is therefore
    derived only after both paired verdicts are known, with an explicit
    fail-closed result for UNKNOWN/compile-incomplete observations.  This
    keeps the harmful-rate authority projection independent of a caller's
    hand-authored gate map while preserving ``UNKNOWN`` when the pair cannot
    support a comparison.
    """
    control_verdict = control_result.get("verdict")
    candidate_verdict = candidate_result.get("verdict")
    if control_verdict not in {"PASS", "FAIL"} or \
            candidate_verdict not in {"PASS", "FAIL"}:
        return "UNKNOWN"
    if candidate_result.get("created_regressions"):
        return "HARMFUL"
    if control_verdict == "FAIL" and candidate_verdict == "PASS":
        return "PARETO_SAFE"
    if control_verdict == "PASS" and candidate_verdict == "FAIL":
        return "HARMFUL"
    # Equal definitive outcomes establish no paired worsening.  Keep this
    # explicitly neutral rather than inferring a positive repair effect.
    return "NEUTRAL"


def run_rtl_external_trial(
        conn: sqlite3.Connection, store: ArtifactStore, *, rule_id: str,
        target_scope: str, status_version: int, fixture: Path,
        oracle: IcarusOracle, compatibility_profile: str,
        repeats: int = 3, trial_uuid: str,
        promotion_gates: dict | None = None,
        production_authority: bool = True) -> dict:
    """Run a fixed-budget real RTL trial and authorize its lifecycle result.

    ``fixture`` is never used as an execution target.  Each repeat copies it
    into independent arm-A/arm-B sandboxes.  The B sandbox is mutated only by
    the activation executor and is byte-restored before the rollback receipt
    is stamped.  The returned report contains the same pair shape consumed by
    ``evaluate_campaign`` so TE/rollback/registry rates are measurable rather
    than left as N/A.
    """
    fixture = Path(fixture).resolve()
    manifest = json.loads((fixture / "manifest.json").read_text())
    fix = dict(manifest["fix"])
    rule = build_index(conn, lifecycle_statuses=frozenset(
        {"candidate", "promoted"})).get(rule_id)
    if rule is None:
        raise ValueError(f"rule is not admissible for external trial: {rule_id}")
    rtl_files = sorted((fixture / "rtl").glob("*.v"))
    if len(rtl_files) != 1:
        raise ValueError("RTL external trial requires exactly one rtl/*.v")
    source_name = rtl_files[0].name
    verification = manifest.get("verification") or {}
    target_rel = Path(verification["target_test"])
    regression_rel = Path(verification["frozen_regression"])
    original_source = rtl_files[0].read_bytes()
    original_digest = _digest(original_source)
    pairs = []

    with tempfile.TemporaryDirectory(prefix="tehm-rtl-external-") as td:
        root = Path(td)
        for repeat in range(max(1, int(repeats))):
            arm_a_root = root / f"r{repeat + 1}" / "arm_a"
            arm_b_root = root / f"r{repeat + 1}" / "arm_b"
            shutil.copytree(fixture, arm_a_root)
            shutil.copytree(fixture, arm_b_root)
            arm_a_source = arm_a_root / "rtl" / source_name
            arm_b_source = arm_b_root / "rtl" / source_name
            arm_a_before = _digest(arm_a_source.read_bytes())
            arm_b_before = _digest(arm_b_source.read_bytes())
            arm_a_result = oracle.verify(
                [arm_a_source], target_tb=arm_a_root / target_rel,
                regression_tb=arm_a_root / regression_rel)

            modules = parse_verilog(original_source.decode())
            if not modules:
                raise ValueError(f"no parseable RTL module: {fixture}")
            graph_before = build_rtl_graph(
                modules[0], design_id=manifest.get("design"),
                compatibility_profile=compatibility_profile).to_dict()
            context = RepairContext(
                check="rtl", design_id=manifest.get("design"),
                reports={"rtl": {"status": "violations",
                                 "total_violations": 1}},
                structural_graph=graph_before,
                compatibility_profile=compatibility_profile,
                cfg={"rtl_external_trial_repeat": repeat + 1},
                symptom_signature={"transformation_family": fix.get(
                    "transformation_family")})
            state = {"fixed_source": None, "edit": None,
                     "verification": None}

            def executor(action, _context):
                payload = {**action.get("payload", {}), **fix,
                           "domain": action.get("domain")}
                fixed_source, edit = apply_rtl_action(
                    original_source.decode(), payload)
                arm_b_source.write_text(fixed_source)
                result = oracle.verify(
                    [arm_b_source], target_tb=arm_b_root / target_rel,
                    regression_tb=arm_b_root / regression_rel)
                state.update({"fixed_source": fixed_source, "edit": edit,
                              "verification": result})
                fixed_modules = parse_verilog(fixed_source)
                graph_after = build_rtl_graph(
                    fixed_modules[0], design_id=manifest.get("design"),
                    compatibility_profile=compatibility_profile).to_dict()
                return {
                    "before_state": {"config": {},
                                     "reports": context.reports,
                                     "structural_graph": graph_before,
                                     "rtl_slice": original_source.decode()[:160]},
                    "after_state": {"config": {},
                                    "reports": {"rtl": {
                                        "status": "clean" if result.get("verdict") == "PASS"
                                        else "violations",
                                        "total_violations": 0 if result.get("verdict") == "PASS" else 1}},
                                    "structural_graph": graph_after,
                                    "rtl_slice": fixed_source[:160]},
                    "observation_delta": {
                        "original_failure": "REMOVED" if result.get("verdict") == "PASS"
                        else "PRESENT",
                        "first_divergence": {"before": 1, "after": 0},
                        "failing_tests": {"before": 1, "after": 0},
                        "created_regressions": result.get("created_regressions", []),
                        "newly_observed_failures": result.get("newly_observed_failures", []),
                        "experiment_kind": "REPAIR",
                        "utility_verdict": _derive_rtl_utility_verdict(
                            arm_a_result, result),
                        "rewrite": edit,
                    },
                    "tool_versions": {"icarus": result.get("extractor_version")},
                    "verification": result,
                }

            activation = activate(
                conn, store, rule_id=rule_id, context=context,
                provided_binding=_binding(rule, fix), executor=executor, oracle=None,
                authority_mode="evaluation", trial_uuid=trial_uuid)
            # The activation pipeline persists before rollback is known.  Stamp
            # the verified receipt into the same authority row afterwards.
            before_restore = _digest(arm_b_source.read_bytes())
            arm_b_source.write_bytes(original_source)
            after_restore = _digest(arm_b_source.read_bytes())
            rollback = {
                "version": "rtl-rollback-receipt-v1",
                "sandbox": str(arm_b_root),
                "source_before_digest": original_digest,
                "arm_a_before_digest": arm_a_before,
                "arm_b_after_execute_digest": before_restore,
                "source_after_restore_digest": after_restore,
                "verified": (after_restore == original_digest and
                              arm_b_before == original_digest),
            }
            conn.execute(
                "UPDATE tehm_activations SET rollback_receipt_json=? "
                "WHERE activation_id=?",
                (stable_dumps(rollback), activation.activation_id))
            conn.commit()
            pairs.append({
                "subject_lineage": str(manifest.get("design")),
                "repeat": repeat + 1,
                "activation_id": activation.activation_id,
                "obligation_coverage": activation.obligation_coverage,
                "created_regressions": list(activation.created_regressions),
                "rollback_receipt": rollback,
                "utility_verdict": _derive_rtl_utility_verdict(
                    arm_a_result, state.get("verification") or {}),
                "arms_differ": arm_a_result.get("verdict") !=
                               (state.get("verification") or {}).get("verdict"),
                "arm_a": {"success": arm_a_result.get("verdict") == "PASS",
                           "verdict": arm_a_result.get("verdict"),
                           "run_id": f"{trial_uuid}:r{repeat + 1}:a"},
                "arm_b": {"success": activation.outcome == "PASS",
                           "verdict": (state.get("verification") or {}).get("verdict"),
                           "run_id": f"{trial_uuid}:r{repeat + 1}:b"},
            })

    a_samples = [1.0 if row["arm_a"]["success"] else 0.0 for row in pairs]
    b_samples = [1.0 if row["arm_b"]["success"] else 0.0 for row in pairs]
    verdict, reason = judge_trial(a_samples, b_samples)
    rollback_ok = bool(pairs) and all(
        row["rollback_receipt"].get("verified") is True for row in pairs)
    regressions = sorted({item for row in pairs
                          for item in row.get("created_regressions", [])})
    coverage = min((row.get("obligation_coverage") for row in pairs
                    if row.get("obligation_coverage") is not None),
                   default=0.0)
    arms_differ = bool(pairs) and all(row["arms_differ"] for row in pairs)
    if not rollback_ok:
        verdict, reason = "inconclusive", "source rollback verification failed"
    metrics = {
        "executor_version": RTL_EXTERNAL_TRIAL_VERSION,
        "reason": reason,
        "A_samples": a_samples, "B_samples": b_samples,
        "arms_differ": arms_differ, "rollback_verified": rollback_ok,
        "obligation_coverage": coverage, "created_regressions": regressions,
        "pairs": pairs,
        "registry_authority": {"verified": False, "mode": "pending"},
    }
    record_external_trial(
        conn, rule_id=rule_id, target_scope=target_scope, verdict=verdict,
        metrics=metrics, status_version=status_version,
        trial_uuid=trial_uuid,
        arm_a_run_id=pairs[0]["arm_a"]["run_id"] if pairs else None,
        arm_b_run_id=pairs[0]["arm_b"]["run_id"] if pairs else None)
    # RTL production adapters always use the complete six-gate conjunction.
    # ``production_authority=False`` is reserved for isolated deterministic
    # evaluation callers that intentionally do not mutate production state.
    authority = (apply_production_trial_verdict if production_authority
                 else apply_trial_verdict)
    current_before_authority = get_status(
        conn, rule_id=rule_id, target_scope=target_scope) or {}
    registry_precondition = (
        current_before_authority.get("status") == "candidate" and
        current_before_authority.get("status_version") == status_version)
    if production_authority or promotion_gates is not None:
        gate_inputs = dict(promotion_gates or {})
        # Adapter-owned measurements cannot be overridden by an external
        # calibration map.  Only cross-lineage TE, harmful-rate, and conformal
        # coverage are supplied by the independent evidence cohort.
        gate_inputs.update({"rollback_verified": rollback_ok,
                            "obligation_coverage": coverage,
                            "registry_verified": registry_precondition})
    else:
        gate_inputs = None
    new_status = authority(
        conn, rule_id=rule_id, target_scope=target_scope, verdict=verdict,
        obligation_coverage=coverage, created_regressions=regressions,
        arms_differ=arms_differ and rollback_ok,
        expected_status_version=status_version,
        provenance={"trial_uuid": trial_uuid,
                    "executor": RTL_EXTERNAL_TRIAL_VERSION},
        promotion_gates=gate_inputs,
        strict_promotion_gates=(production_authority or
                                promotion_gates is not None))
    after = get_status(conn, rule_id=rule_id, target_scope=target_scope) or {}
    registry = {
        "before": {"status": "candidate", "status_version": status_version},
        "after": after, "authorized_status": new_status,
        "verified": (
            (new_status == "promoted" and
             after.get("status") == "promoted" and
             after.get("status_version") == status_version + 1) or
            (new_status is None and after.get("status") == "candidate" and
             after.get("status_version") == status_version)),
        "mode": "promotion_authority",
    }
    metrics["registry_authority"] = registry
    if production_authority or promotion_gates is not None:
        gate_inputs = dict(gate_inputs or {})
        gate_inputs.update({"rollback_verified": rollback_ok,
                            "obligation_coverage": coverage,
                            "registry_verified": registry["verified"]})
        metrics["promotion_gates"] = evaluate_promotion_gates(
            gate_inputs, strict=True, min_obligation_coverage=1.0)
    conn.execute("UPDATE tehm_trials SET metrics_json=? WHERE trial_uuid=?",
                 (stable_dumps(metrics), trial_uuid))
    conn.commit()
    return {"trial_uuid": trial_uuid, "verdict": verdict,
            "reason": reason, "new_status": new_status,
            "metrics": metrics}


def _binding(rule: dict, fix: dict) -> dict:
    """Use manifest witnesses to bind all typed RTL holes."""
    # Holes are named by slot identity in the crystallized rule.  The normal
    # activation binder accepts these concrete values; profile is explicit.
    binding = {}
    for pattern in (rule.get("before_pattern") or {},
                    rule.get("after_pattern") or {}):
        for path, value in pattern.items():
            if not (isinstance(value, str) and value.startswith("$H")):
                continue
            key = path.rsplit(".", 1)[-1]
            if key in fix:
                binding[value] = fix[key]
    return binding


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
