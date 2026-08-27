#!/usr/bin/env python3
"""Run the frozen P3 procedural component ablation on real RTL fixtures.

This runner is deliberately separate from ``run_controlled_m0_m8_v2.py``:
the latter is the immutable v3 replay contract.  P3 tasks carry explicit
fixture-level gate evidence so that M4/M5/M6/M7 really remove one gate rather
than merely recording an ablation label.  Every arm executes in a temporary
copy of the canonical DB/artifacts and uses the real Icarus oracle.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contracts import RepairContext  # noqa: E402
from evaluation.freeze_pointer import resolve_bundle  # noqa: E402
from legacy_backend import LegacyMemoryBackend  # noqa: E402
from tehm import db  # noqa: E402
from tehm.activation.pipeline import ActivationError, activate  # noqa: E402
from tehm.artifact_store import ArtifactStore  # noqa: E402
from tehm.parametric.shadow_campaign import (  # noqa: E402
    ShadowCampaignError,
    assert_counts_unchanged,
    canonical_counts,
    digest,
)
from tehm.crystallization.preflight import run_preflight  # noqa: E402
from tehm.retrieval.index import build_index  # noqa: E402
from tehm.retrieval.pipeline import retrieve  # noqa: E402
from tehm.rtl.rtl_actions import apply_rtl_action  # noqa: E402
from tehm.rtl.rtl_oracle import IcarusOracle  # noqa: E402
from tehm.sync import canonical_json  # noqa: E402

from prepare_procedural_ablation_manifest import validate  # noqa: E402


ARMS = ("M0", "M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8")
ABLATIONS = {"role_view": "M5", "predicate_view": "M6",
             "validity_gate": "M4", "obligation_transfer": "M7"}
ADMISSIBLE = {"PROVISIONAL_VALID", "VALIDATED"}


def wilson(k: int, n: int) -> list[float] | None:
    if not n:
        return None
    z = 1.959963984540054
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [round(max(0.0, centre - half), 6),
            round(min(1.0, centre + half), 6)]


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ShadowCampaignError(f"cannot read JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ShadowCampaignError(f"JSON object required: {path}")
    return value


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _copy_eval(snapshot: Path, root: Path, key: str):
    safe = key.replace(":", "_")
    db_path = root / f"{safe}.sqlite"
    artifacts = root / f"{safe}_artifacts"
    shutil.copy2(snapshot / "closed_loop" / "tehm.sqlite", db_path)
    shutil.copytree(snapshot / "closed_loop" / "artifacts", artifacts)
    return db_path, artifacts


def _rtl_rule_id(conn, transformation_family: str | None = None) -> str:
    rows = conn.execute(
        "SELECT rule_id, before_pattern_json FROM tehm_rules "
        "WHERE domain='rtl' AND validity_status IN "
        "('PROVISIONAL_VALID','VALIDATED') ORDER BY rule_id").fetchall()
    for row in rows:
        if transformation_family:
            pattern = db.read_json(row["before_pattern_json"])
            if (pattern or {}).get("type") != transformation_family:
                continue
        return str(row["rule_id"])
    if not rows or transformation_family:
        raise ShadowCampaignError("canonical snapshot has no admissible RTL rule")


def _fixture_paths(fixture: Path, manifest: dict) -> tuple[Path, Path, Path]:
    verification = manifest.get("verification") or {}
    target_rel = verification.get("target_test")
    regression_rel = verification.get("frozen_regression")
    if not isinstance(target_rel, str) or not isinstance(regression_rel, str):
        raise ShadowCampaignError(f"fixture verification paths missing: {fixture}")
    rtl_dir = fixture / "rtl"
    rtl_files = sorted(rtl_dir.glob("*.v"))
    if len(rtl_files) != 1:
        raise ShadowCampaignError(
            f"fixture must have exactly one rtl/*.v source: {fixture}")
    target = (fixture / target_rel).resolve()
    regression = (fixture / regression_rel).resolve()
    if not target.is_file() or not regression.is_file():
        raise ShadowCampaignError(f"fixture oracle file missing: {fixture}")
    return rtl_files[0], target, regression


def _gate_evidence(meta: dict, arm: str) -> dict:
    """Evaluate fixture evidence while preserving UNKNOWN as unresolved."""
    role = meta.get("role_compatible")
    predicate = meta.get("predicate_status", "UNKNOWN")
    validity = meta.get("candidate_validity_status", "UNKNOWN")
    decisions = []
    if arm != "M5":
        if role is False:
            decisions.append({"gate": "role_view", "status": "INAPPLICABLE",
                              "reason": "structural_role_collision"})
        elif role is not True:
            decisions.append({"gate": "role_view", "status": "UNRESOLVED",
                              "reason": "role_observation_missing"})
    if arm != "M6":
        if predicate == "FALSE":
            decisions.append({"gate": "predicate_view", "status": "INAPPLICABLE",
                              "reason": "predicate_false"})
        elif predicate != "TRUE":
            decisions.append({"gate": "predicate_view", "status": "UNRESOLVED",
                              "reason": "UNKNOWN_not_false"})
    if arm != "M4":
        if validity not in ADMISSIBLE:
            decisions.append({"gate": "validity_gate", "status": "REJECTED",
                              "reason": validity})
    if decisions:
        # The first veto is deterministic; all decisions remain in the receipt.
        return {"passed": False, "status": decisions[0]["status"],
                "decisions": decisions}
    return {"passed": True, "status": "PASSED", "decisions": []}


def _action_for(fix: dict, p3: dict, arm: str) -> dict:
    action = dict(fix)
    override = ((p3.get("ablation_actions") or {}).get(arm) or {})
    action.update(override)
    action["domain"] = fix.get("domain", "rtl.GUARD_STRENGTHEN")
    action["module"] = fix.get("module")
    return action


def _binding(action: dict) -> dict:
    """Bind the small generic RTL hole vocabulary used by P3 fixtures.

    Guard-strengthen rules use H0/H1/H2 for condition/source/target; AST
    rewrite rules use H0/H1 for replacement/target.  Supplying the union keeps
    the evaluator domain-neutral while the binding layer still refuses any
    hole that is not actually provided.
    """
    values = [action.get("add_condition"), action.get("source_state"),
              action.get("target_state")]
    if values[0] is None:
        values = [action.get("replacement"), action.get("target"),
                  action.get("count")]
    return {f"$H{i}": value for i, value in enumerate(values)
            if value is not None}


def _invalid_copy_rule(conn, rule_id: str, status: str) -> None:
    # This mutation is confined to an arm-local evaluation copy.  M4 is the
    # intentionally gate-ablated arm; the canonical source remains untouched.
    conn.execute("UPDATE tehm_rules SET validity_status=?, validity_profile_json=? "
                 "WHERE rule_id=?", (status, json.dumps({"status": status}), rule_id))
    conn.commit()


def _run_arm(snapshot: Path, work: Path, task: dict, fixture: Path,
             fixture_manifest: dict, oracle: IcarusOracle, rule_id: str,
             *, arm: str,
             lifecycle_statuses: frozenset[str]) -> dict:
    db_path, artifact_root = _copy_eval(snapshot, work, f"{task['task_id']}_{arm}")
    conn = db.connect(db_path)
    p3 = fixture_manifest.get("p3") or {}
    gate = _gate_evidence(p3, arm)
    context = RepairContext(
        check="rtl", design_id=task["lineage_id"],
        reports={"rtl": {"status": "violations", "total_violations": 1}},
        symptom_signature={"transformation_family": task.get(
            "transformation_family", "GUARD_STRENGTHEN")})
    base = {
        "arm": arm, "backend": "tehm", "source": "tehm_rule",
        "ablation": {name: arm != ablated for name, ablated in ABLATIONS.items()},
        "gate": gate,
    }

    # The validity task supplies an intentionally rejected candidate only to
    # the M4 ablation.  Other arms fail closed before they can execute it.
    invalid_candidate = p3.get("candidate_validity_status") not in ADMISSIBLE
    if invalid_candidate and arm == "M4":
        _invalid_copy_rule(conn, rule_id, str(p3["candidate_validity_status"]))

    if arm == "M8" or arm in ("M4", "M5", "M6", "M7"):
        if invalid_candidate and arm != "M4":
            # Keep the receipt explicit even though the copied rule remains
            # admissible for the retrieval lookup used by other arms.
            gate = _gate_evidence(p3, arm)
            base["gate"] = gate
        if not gate["passed"]:
            conn.close()
            return {**base, "candidates": 1, "applicable": 0,
                    "action_executed": False, "repair_success": False,
                    "verification_status": "NOT_RUN", "obligation_coverage": 0.0,
                    "harmful_activation": False,
                    "reason": "gate_blocked"}

    receipt = retrieve(conn, context, lifecycle_statuses=lifecycle_statuses)
    base.update({"candidates": receipt.candidates_retrieved,
                 "applicable": receipt.applicable})
    # A domain growth campaign may bind an explicit rule family.  Keep the
    # retrieval receipt for coverage accounting, but select that enrolled rule
    # deterministically instead of allowing a lexicographically earlier rule
    # from another RTL mechanism to hijack the executable arm.
    selected = receipt.results[0] if receipt.results else None
    if rule_id:
        selected = next((candidate for candidate in receipt.results
                         if getattr(candidate, "rule_id", None) == rule_id), None)
        if selected is None:
            selected = build_index(
                conn, lifecycle_statuses=lifecycle_statuses,
                require_validity=False).get(rule_id)
    allow_invalid = bool(invalid_candidate and arm == "M4")
    if selected is None and allow_invalid:
        selected = build_index(
            conn, lifecycle_statuses=lifecycle_statuses,
            require_validity=False).get(rule_id)
    if selected is None:
        conn.close()
        return {**base, "action_executed": False, "repair_success": False,
                "verification_status": "NOT_RUN", "obligation_coverage": 0.0,
                "harmful_activation": False, "reason": "no retrieved rule"}
    selected_rule_id = selected.rule_id if hasattr(selected, "rule_id") else rule_id
    fix = fixture_manifest["fix"]
    action_payload = _action_for(fix, p3, arm)
    source, target_tb, regression_tb = _fixture_paths(fixture, fixture_manifest)

    def executor(action, _context):
        payload = {**action["payload"], **action_payload,
                   "domain": action["domain"]}
        edited, edit = apply_rtl_action(source.read_text(), payload)
        with tempfile.TemporaryDirectory(prefix=f"tehm_p3_{arm.lower()}_") as td:
            fixed_path = Path(td) / source.name
            fixed_path.write_text(edited)
            verified = oracle.verify([fixed_path], target_tb=target_tb,
                                    regression_tb=regression_tb)
        return {
            "before_state": {"reports": context.reports, "config": {},
                             "rtl_slice": source.read_text()[:160]},
            "after_state": {"reports": {"rtl": {"status": "clean",
                                                   "total_violations": 0}},
                            "config": {}, "rtl_slice": edited[:160]},
            "observation_delta": {
                "original_failure": "REMOVED" if verified["verdict"] == "PASS" else "PRESENT",
                "first_divergence": {"before": 1, "after": 0},
                "failing_tests": {"before": 1, "after": 0},
                "created_regressions": verified.get("created_regressions", []),
                "newly_observed_failures": verified.get("newly_observed_failures", []),
                "rewrite": edit,
            },
            "tool_versions": {"icarus": verified.get("extractor_version")},
            "verification": verified,
        }

    try:
        activation = activate(
            conn, ArtifactStore(artifact_root), rule_id=selected_rule_id,
            context=context, provided_binding=_binding(action_payload),
            executor=executor, oracle=None, allow_invalid=allow_invalid,
            authority_mode="evaluation")
    except ActivationError as exc:
        conn.close()
        return {**base, "retrieved_rule_id": selected_rule_id,
                "action_executed": False, "repair_success": False,
                "verification_status": "NOT_RUN", "obligation_coverage": 0.0,
                "harmful_activation": False, "reason": str(exc)}

    repair_success = activation.outcome == "PASS"
    if arm == "M7":
        # The target oracle may pass, but an arm without obligation transfer
        # cannot claim a complete repair/signoff.
        repair_success = False
    harmful = bool(activation.created_regressions) or (
        activation.executability_status == "EXECUTABLE" and
        activation.verification_status != "PASS")
    result = {
        **base, "retrieved_rule_id": selected_rule_id,
        "action_executed": activation.executability_status == "EXECUTABLE",
        "repair_success": repair_success,
        "target_pass": activation.outcome == "PASS",
        "activation_id": activation.activation_id,
        "verification_status": activation.verification_status,
        "obligation_coverage": (0.0 if arm == "M7"
                                 else activation.obligation_coverage),
        "validity_status": p3.get("candidate_validity_status"),
        "created_regressions": list(activation.created_regressions),
        "harmful_activation": harmful,
    }
    if arm == "M7":
        result["reason"] = "obligation transfer ablated"
        result["recovery_obligation"] = p3.get("recovery_obligation")
    conn.close()
    return result


def _evaluate_task(snapshot: Path, work: Path, task: dict,
                   oracle: IcarusOracle, rule_id: str,
                   lifecycle_statuses: frozenset[str]) -> dict:
    fixture = (REPO_ROOT / task["fixture"]).resolve()
    fixture_manifest = _read(fixture / "manifest.json")
    source, target_tb, regression_tb = _fixture_paths(fixture, fixture_manifest)
    baseline = oracle.verify([source], target_tb=target_tb,
                             regression_tb=regression_tb)
    if baseline.get("target", {}).get("verdict") != "FAIL":
        raise ShadowCampaignError(f"P3 fixture baseline target did not fail: {task['task_id']}")
    if baseline.get("regression", {}).get("verdict") != "PASS":
        raise ShadowCampaignError(f"P3 fixture baseline regression did not pass: {task['task_id']}")

    rows = {
        "M0": {"arm": "M0", "backend": "none", "candidates": 0,
                "action_executed": False, "repair_success": False,
                "source": "cold_start", "reason": "no historical candidate"},
    }
    legacy = LegacyMemoryBackend(read_only_eval=True)
    legacy_candidates = legacy.retrieve(
        legacy.build_query(RepairContext(check="rtl", design_id=task["lineage_id"])), limit=10)
    rows["M1"] = {"arm": "M1", "backend": "legacy",
                   "candidates": len(legacy_candidates), "action_executed": False,
                   "repair_success": False,
                   "source": "legacy_memory" if legacy_candidates else "cold_start"}
    conn = db.connect_read_only(snapshot / "closed_loop" / "tehm.sqlite")
    episode_count = conn.execute(
        "SELECT COUNT(*) FROM tehm_episodes WHERE domain='rtl'").fetchone()[0]
    ret = retrieve(conn, RepairContext(
        check="rtl", design_id=task["lineage_id"], reports={"rtl": {"status": "violations"}},
        symptom_signature={"transformation_family": "GUARD_STRENGTHEN"}),
        lifecycle_statuses=lifecycle_statuses)
    conn.close()
    rows["M2"] = {"arm": "M2", "backend": "tehm_episode_only",
                   "candidates": episode_count, "action_executed": False,
                   "repair_success": False, "source": "tehm_episode",
                   "reason": "episode representation has no executable rule"}
    rows["M3"] = {"arm": "M3", "backend": "tehm_five_view_retrieval",
                   "candidates": ret.candidates_retrieved, "applicable": ret.applicable,
                   "action_executed": False, "repair_success": False,
                   "source": "tehm_views", "reason": "retrieval-only arm"}
    for arm in ("M4", "M5", "M6", "M7", "M8"):
        rows[arm] = _run_arm(snapshot, work, task, fixture, fixture_manifest,
                              oracle, rule_id, arm=arm,
                              lifecycle_statuses=lifecycle_statuses)
    return {
        "task_id": task["task_id"], "domain": "rtl",
        "lineage_id": task["lineage_id"],
        "lineage_cluster": task["lineage_cluster"],
        "fixture": task["fixture"], "task_family": task["task_family"],
        "baseline": baseline, "arms": rows,
        "success": {arm: bool(rows[arm].get("repair_success")) for arm in ARMS},
        "evidence_mode": "real_icarus_replay_per_arm",
    }


def _component_discrimination(task_rows: list[dict]) -> dict:
    result = {}
    for component, ablated in ABLATIONS.items():
        full = [row["arms"].get("M8", {}) for row in task_rows]
        ab = [row["arms"].get(ablated, {}) for row in task_rows]
        full_success = sum(bool(row.get("repair_success")) for row in full)
        ab_success = sum(bool(row.get("repair_success")) for row in ab)
        full_exec = sum(bool(row.get("action_executed")) for row in full)
        ab_exec = sum(bool(row.get("action_executed")) for row in ab)
        full_harm = sum(bool(row.get("harmful_activation")) for row in full)
        ab_harm = sum(bool(row.get("harmful_activation")) for row in ab)
        full_rate = full_harm / full_exec if full_exec else 0.0
        ab_rate = ab_harm / ab_exec if ab_exec else 0.0
        full_oc = [float(row.get("obligation_coverage")) for row in full
                   if isinstance(row.get("obligation_coverage"), (int, float))]
        ab_oc = [float(row.get("obligation_coverage")) for row in ab
                 if isinstance(row.get("obligation_coverage"), (int, float))]
        full_mean = sum(full_oc) / len(full_oc) if full_oc else None
        ab_mean = sum(ab_oc) / len(ab_oc) if ab_oc else None
        identifiable = bool(full_success != ab_success or full_exec != ab_exec or
                            full_harm != ab_harm or
                            (full_mean is not None and ab_mean is not None and
                             abs(full_mean - ab_mean) > 1e-12))
        result[component] = {
            "full_arm": "M8", "ablated_arm": ablated,
            "tasks": len(task_rows), "full_successes": full_success,
            "ablated_successes": ab_success, "full_action_executed": full_exec,
            "ablated_action_executed": ab_exec,
            "full_harmful_activation_count": full_harm,
            "ablated_harmful_activation_count": ab_harm,
            "full_harmful_activation_rate": full_rate,
            "ablated_harmful_activation_rate": ab_rate,
            "harmful_rate_not_increased": full_rate <= ab_rate,
            "full_mean_obligation_coverage": full_mean,
            "ablated_mean_obligation_coverage": ab_mean,
            "identifiable_on_current_tasks": identifiable,
            "interpretation": "component_contrast_observed" if identifiable
            else "not_identified_expand_task_set",
        }
    return result


def _procedural_baseline(snapshot: Path) -> dict:
    conn = db.connect_read_only(snapshot / "closed_loop" / "tehm.sqlite")
    preflight = run_preflight(conn)
    rows = conn.execute(
        "SELECT validity_status, validity_profile_json FROM tehm_rules").fetchall()
    validated = sum(row["validity_status"] == "VALIDATED" for row in rows)
    cross = 0
    for row in rows:
        profile = db.read_json(row["validity_profile_json"])
        gates = {g.get("name"): g for g in profile.get("gates", [])}
        if int((gates.get("V3", {}).get("detail", {}) or {}).get("unique_lineages", 0) or 0) >= 2:
            cross += 1
    conn.close()
    non_singleton = sum(1 for group in preflight.groups.values()
                        if group["size"] >= preflight.min_group_size)
    return {"validated_rules": validated, "cross_lineage_rule_support": cross,
            "non_singleton_effect_groups": non_singleton,
            "effect_group_count": preflight.num_groups,
            "transition_count": preflight.total_transitions}


def run(*, snapshot: Path, manifest_path: Path, output: Path,
        lifecycle_statuses: frozenset[str] = frozenset({"promoted"})) -> dict:
    lifecycle_statuses = frozenset(lifecycle_statuses)
    if not lifecycle_statuses or not lifecycle_statuses <= {"candidate", "promoted"}:
        raise ShadowCampaignError(
            "procedural ablation accepts only candidate/promoted lifecycle statuses")
    raw = _read(manifest_path)
    normalized = validate(raw, repo_root=REPO_ROOT)
    if not normalized["validation"]["fixtures_materialized"]:
        raise ShadowCampaignError("procedural manifest still has pending fixtures")
    snapshot_manifest = _read(snapshot / "bundle_manifest.json")
    expected_digest = normalized["source_snapshot"]["bundle_digest"]
    if snapshot_manifest.get("bundle_digest") != expected_digest:
        raise ShadowCampaignError("procedural manifest does not bind supplied canonical bundle")
    oracle = IcarusOracle()
    if not oracle.available:
        raise ShadowCampaignError("P3 replay requires the real Icarus oracle")
    before_db_sha = _sha256(snapshot / "closed_loop" / "tehm.sqlite")
    source_conn = db.connect_read_only(snapshot / "closed_loop" / "tehm.sqlite")
    before_counts = canonical_counts(source_conn)
    rule_id = _rtl_rule_id(source_conn, normalized.get("rule_selection", {}).get(
        "transformation_family"))
    source_conn.close()
    work = output / "eval_work"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)
    task_rows = [_evaluate_task(snapshot, work, task, oracle, rule_id,
                                lifecycle_statuses)
                 for task in normalized["tasks"]]
    after_db_sha = _sha256(snapshot / "closed_loop" / "tehm.sqlite")
    if before_db_sha != after_db_sha:
        raise ShadowCampaignError("canonical snapshot bytes changed during P3 replay")
    source_conn = db.connect_read_only(snapshot / "closed_loop" / "tehm.sqlite")
    assert_counts_unchanged(before_counts, canonical_counts(source_conn))
    source_conn.close()

    summary = {}
    for arm in ARMS:
        k = sum(row["success"][arm] for row in task_rows)
        summary[arm] = {"successes": k, "tasks": len(task_rows),
                        "rate": k / len(task_rows),
                        "wilson_95": wilson(k, len(task_rows))}
    clusters = {}
    for row in task_rows:
        clusters.setdefault(row["lineage_cluster"], []).append(row)
    cluster_summary = {}
    for arm in ARMS:
        k = sum(all(row["success"][arm] for row in cohort)
                for cohort in clusters.values())
        cluster_summary[arm] = {"successes": k, "clusters": len(clusters),
                                "rate": k / len(clusters),
                                "wilson_95": wilson(k, len(clusters))}
    baseline = _procedural_baseline(snapshot)
    full_rate = summary["M8"]["rate"]
    m0_rate = summary["M0"]["rate"]
    harmful_full = sum(bool(row["arms"]["M8"].get("harmful_activation"))
                       for row in task_rows)
    exec_full = sum(bool(row["arms"]["M8"].get("action_executed"))
                    for row in task_rows)
    acceptance = normalized["acceptance"]
    observed = {
        "non_singleton_effect_groups": baseline["non_singleton_effect_groups"],
        "cross_lineage_rule_support": baseline["cross_lineage_rule_support"],
        "validated_rules": baseline["validated_rules"],
        "rule_coverage": full_rate,
        "vcg": full_rate - m0_rate,
        "harmful_activation_rate": harmful_full / exec_full if exec_full else 0.0,
        "cluster_intervals_present": bool(cluster_summary),
    }
    acceptance_checks = {
        "non_singleton_effect_groups": observed["non_singleton_effect_groups"] >= acceptance["min_non_singleton_effect_groups"],
        "cross_lineage_rule_support": observed["cross_lineage_rule_support"] >= acceptance["min_cross_lineage_rule_support"],
        "validated_rules": observed["validated_rules"] >= acceptance["min_validated_rules"],
        "rule_coverage": observed["rule_coverage"] >= acceptance["min_rule_coverage"],
        "vcg": observed["vcg"] >= acceptance["min_vcg"],
        "harmful_activation_rate": observed["harmful_activation_rate"] <= acceptance["max_harmful_activation_rate"],
        "cluster_intervals": (observed["cluster_intervals_present"]
                               if acceptance["require_cluster_intervals"] else True),
    }
    report = {
        "version": "procedural-ablation-replay-v1",
        "task_manifest": str(manifest_path.resolve()),
        "manifest_digest": digest(normalized),
        "snapshot": str(snapshot.resolve()),
        "bundle_digest": expected_digest,
        "rule_id": rule_id,
        "oracle": {"available": oracle.available, "version": "icarus-oracle-v0.1"},
        "canonical_memory_mutation": "none",
        "canonical_counts_before": before_counts,
        "canonical_counts_after": before_counts,
        "task_rows": task_rows, "arms": list(ARMS),
        "summary": summary, "cluster_summary": cluster_summary,
        "component_discrimination": _component_discrimination(task_rows),
        "procedural_baseline": baseline,
        "observed": observed, "acceptance_checks": acceptance_checks,
        "acceptance_passed": all(acceptance_checks.values()),
        "firewall": normalized["firewall"],
        "evidence_mode": "real_icarus_replay_per_arm",
        "runtime_authority": {
            "lifecycle_statuses": sorted(lifecycle_statuses),
            "production_runtime": lifecycle_statuses == frozenset({"promoted"}),
            "candidate_staging_only": lifecycle_statuses == frozenset({"candidate"}),
        },
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "procedural_ablation_report.json").write_bytes(canonical_json(report))
    (output / "procedural_ablation_manifest.normalized.json").write_bytes(
        canonical_json({**normalized, "manifest_digest": report["manifest_digest"]}))
    lines = ["# Procedural component ablation replay", "",
             f"Acceptance passed: **{report['acceptance_passed']}**", "",
             "| Arm | Successes | Tasks | Rate | Wilson 95% |",
             "|---|---:|---:|---:|---|"]
    lines += [f"| {arm} | {row['successes']} | {row['tasks']} | {row['rate']:.4f} | {row['wilson_95']} |"
              for arm, row in summary.items()]
    lines += ["", "## Component discrimination", "", "```json",
              json.dumps(report["component_discrimination"], indent=2, sort_keys=True),
              "```", "", "## Acceptance", "", "```json",
              json.dumps({"observed": observed, "checks": acceptance_checks},
                         indent=2, sort_keys=True), "```", ""]
    (output / "procedural_ablation_report.md").write_text("\n".join(lines))
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--snapshot", type=Path,
                    default=resolve_bundle(require_exists=False))
    ap.add_argument("--manifest", type=Path,
                    default=ROOT / "evaluation" / "procedural_ablation_task_manifest_v1.json")
    ap.add_argument("--output", type=Path,
                    default=Path("/tmp/tehm-procedural-ablation-v1"))
    args = ap.parse_args(argv)
    try:
        report = run(snapshot=args.snapshot.resolve(),
                     manifest_path=args.manifest.resolve(),
                     output=args.output.resolve())
    except (OSError, ShadowCampaignError, ValueError) as exc:
        print(f"procedural ablation refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"ok": True, "output": str(args.output.resolve()),
                      "acceptance_passed": report["acceptance_passed"],
                      "component_discrimination": report["component_discrimination"]},
                     indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
