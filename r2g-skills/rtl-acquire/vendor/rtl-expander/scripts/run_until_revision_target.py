#!/usr/bin/env python3
"""Resume one Phase-2 round until its acquired-revision target is complete."""

from __future__ import annotations

import argparse
import ast
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
import re
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from frontier import FrontierDB, default_frontier_path
from phase2_round_delta import (
    COHORT_LOCK_SCHEMA,
    atomic,
    capture as capture_round,
    cohort_lock_path,
)
from process_runner import run_streamed
from split_consumption_contract import INTERNAL, load_and_validate


STATE_SCHEMA = "rtl_revision_target_controller_v4_8"
PIPELINE_SCHEMA = "rtl_pipelined_processing_v1"
EARLY_CLOSE_REASON = "ELIGIBLE_PRODUCTION_FRONTIER_EXHAUSTED"
TRAIN_VAL_CONFLICT_RE = re.compile(
    r"frozen split conflict: closure/hierarchy/project component would merge groups "
    r"(?P<groups>\[[^\n]+?\]) across splits (?P<splits>\[[^\n]+?\])"
)
TEST_CONFLICT_RE = re.compile(
    r"frozen split conflict involving test: closure/hierarchy/project component would merge "
    r"groups (?P<groups>\[[^\n]+?\]) across splits (?P<splits>\[[^\n]+?\])"
)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def revision_keys(corpus: Path) -> set[str]:
    database = corpus / "state/frontier.sqlite"
    if not database.is_file():
        return set()
    connection = sqlite3.connect(database)
    try:
        return {str(row[0]) for row in connection.execute("SELECT repository_revision_key FROM repository_revisions")}
    finally:
        connection.close()


def acquisition_watermark(corpus: Path) -> int:
    connection = sqlite3.connect(corpus / "state/frontier.sqlite")
    try:
        return int(connection.execute("SELECT COALESCE(MAX(rowid),0) FROM acquisition_attempts").fetchone()[0])
    finally:
        connection.close()


def acquisition_stats_since(corpus: Path, watermark: int) -> dict[str, Any]:
    connection = sqlite3.connect(corpus / "state/frontier.sqlite")
    try:
        rows = connection.execute(
            "SELECT state,COUNT(*),COUNT(DISTINCT repository_revision_key) "
            "FROM acquisition_attempts WHERE rowid>? GROUP BY state", (watermark,)
        ).fetchall()
        attempted = int(connection.execute(
            "SELECT COUNT(*) FROM acquisition_attempts WHERE rowid>?", (watermark,)
        ).fetchone()[0])
    finally:
        connection.close()
    return {
        "attempts": attempted,
        "states": {str(state): int(count) for state, count, _ in rows},
        "unique_revision_successes": sum(
            int(unique) for state, _, unique in rows if state in {"ACQUIRED", "CACHE_HIT"}
        ),
        "failures": sum(int(count) for state, count, _ in rows if state == "FAILED"),
        "duplicates_or_cache_hits": sum(int(count) for state, count, _ in rows if state == "CACHE_HIT"),
    }


def frontier_inventory(
    corpus: Path, providers: list[str], *, min_priority: float = 1.0,
    min_design_likelihood: float = 0.5, quota_reserve: int = 100,
) -> dict[str, Any]:
    now = utc_now()
    with FrontierDB(default_frontier_path(corpus)) as db:
        statuses = db.provider_statuses(providers, quota_reserve)
        available = db.acquisition_eligible_providers(providers)
        discovery_available = db.discovery_eligible_providers(providers, quota_reserve)
        repository_providers = sorted({db.quota_provider(provider) for provider in providers})
        placeholders = ",".join("?" for _ in repository_providers)
        raw = int(db.connection.execute(
            f"""SELECT COUNT(*) FROM repositories WHERE state='FRONTIER'
                AND acquisition_status IN ('NOT_ACQUIRED','RETRY')
                AND provider IN ({placeholders})""",
            repository_providers,
        ).fetchone()[0]) if repository_providers else 0
        acquire_eligible = int(db.connection.execute(
            f"""SELECT COUNT(*) FROM repositories WHERE state='FRONTIER'
                AND acquisition_status IN ('NOT_ACQUIRED','RETRY')
                AND priority>=? AND design_likelihood>=?
                AND (next_retry_at IS NULL OR next_retry_at<=?)
                AND provider IN ({placeholders})
                AND NOT EXISTS (
                  SELECT 1 FROM provider_state ps WHERE ps.provider=repositories.provider
                  AND ps.backoff_until IS NOT NULL AND ps.backoff_until>?
                )""",
            (min_priority, min_design_likelihood, now, *repository_providers, now),
        ).fetchone()[0]) if repository_providers else 0
        exploration_eligible = int(db.connection.execute(
            f"""SELECT COUNT(*) FROM repositories WHERE state='FRONTIER'
                AND acquisition_status IN ('NOT_ACQUIRED','RETRY') AND priority>=?
                AND design_likelihood>=0.30 AND design_likelihood<?
                AND json_extract(metadata_json,'$.discovery_evidence.exploration_eligible')=1
                AND (next_retry_at IS NULL OR next_retry_at<=?)
                AND provider IN ({placeholders})""",
            (min_priority, min_design_likelihood, now, *repository_providers),
        ).fetchone()[0]) if repository_providers else 0
    unique_quota = {status["quota_provider"]: status for status in statuses.values()}
    cooldowns = [status for status in unique_quota.values() if status["status"] == "RATE_LIMITED"]
    all_cooldown = bool(unique_quota) and len(cooldowns) == len(unique_quota)
    unavailable = [
        status for status in unique_quota.values()
        if status["status"] in {"RATE_LIMITED", "QUOTA_RESERVED"}
    ]
    resets = sorted(status["reset_at"] for status in unavailable if status.get("reset_at"))
    return {
        "schema": "rtl_provider_aware_frontier_inventory_v1",
        "raw_frontier": raw,
        "acquisition_eligible_frontier": acquire_eligible,
        "exploration_eligible_frontier": exploration_eligible,
        "providers": statuses,
        "available_providers": available,
        "healthy_discovery_providers": discovery_available,
        "quota_reserved_providers": [
            provider for provider, status in statuses.items()
            if status["status"] == "QUOTA_RESERVED"
        ],
        "all_providers_in_cooldown": all_cooldown,
        "earliest_reset_at": resets[0] if resets else None,
    }


def frontier_no_progress_category(inventory: dict[str, Any]) -> str | None:
    if inventory.get("all_providers_in_cooldown"):
        return "PROVIDER_RATE_LIMIT"
    if int(inventory.get("raw_frontier", 0)) == 0:
        return "FRONTIER_EXHAUSTED"
    if int(inventory.get("acquisition_eligible_frontier", 0)) == 0:
        return "PROVIDER_SCOPED_FRONTIER_EXHAUSTED"
    return None


def no_progress_category(
    corpus: Path, cycle: dict[str, Any], providers: list[str],
    min_priority: float = 1.0, min_design_likelihood: float = 0.5,
    quota_reserve: int = 100,
) -> str:
    inventory = frontier_inventory(
        corpus, providers, min_priority=min_priority,
        min_design_likelihood=min_design_likelihood, quota_reserve=quota_reserve,
    )
    frontier_category = frontier_no_progress_category(inventory)
    if frontier_category == "PROVIDER_RATE_LIMIT":
        return frontier_category
    text = "\n".join(
        str(stage.get("stdout_tail", "")) + "\n" + str(stage.get("stderr_tail", ""))
        for stage in cycle.get("stages", [])
    ).lower()
    if any(token in text for token in ("401", "unauthorized", "bad credentials", "authentication")):
        return "AUTH"
    if any(token in text for token in ("timed out", "timeout", "temporary failure", "connection reset", "network is unreachable", "name resolution")):
        return "NETWORK"
    if frontier_category:
        return frontier_category
    return "NO_ACQUISITION_YIELD"


def starvation_watchdog(state: dict[str, Any], args: argparse.Namespace) -> dict[str, Any] | None:
    cycles = state.get("cycles", [])
    if not cycles:
        return None
    if cycles[-1].get("no_progress_category") in {
        "PROVIDER_RATE_LIMIT", "PROVIDER_SCOPED_FRONTIER_EXHAUSTED",
    }:
        return None
    last_event_cycle = max(
        (int(event.get("cycle", 0)) for event in state.get("starvation_events", [])),
        default=0,
    )
    if len(cycles) - last_event_cycle < args.starvation_cooldown_cycles:
        return None
    window = cycles[-args.starvation_window_cycles:]
    attempts = sum(int(cycle.get("acquisition", {}).get("attempts", 0)) for cycle in window)
    acquired = sum(int(cycle.get("acquisition", {}).get("unique_revision_successes", 0)) for cycle in window)
    acquisition_yield = acquired / max(1, attempts)
    reasons = []
    if (
        len(window) >= args.starvation_window_cycles
        and attempts >= args.starvation_min_attempts
        and acquisition_yield < args.starvation_yield_threshold
    ):
        reasons.append("SLIDING_WINDOW_LOW_YIELD")
    if int(state.get("consecutive_no_progress_cycles", 0)) >= args.max_no_progress_cycles:
        reasons.append("CONSECUTIVE_NO_PROGRESS")
    if args.max_cycles > 0 and len(cycles) % args.max_cycles == 0:
        reasons.append("CYCLE_WATCHDOG_INTERVAL")
    if not reasons:
        return None
    return {
        "schema": "rtl_frontier_starvation_event_v1", "cycle": len(cycles),
        "reasons": reasons, "window_cycles": len(window), "attempts": attempts,
        "unique_revision_successes": acquired,
        "acquisition_yield": round(acquisition_yield, 6),
    }


def active_acquisition_claims(corpus: Path) -> int:
    connection = sqlite3.connect(corpus / "state/frontier.sqlite")
    try:
        return int(connection.execute(
            """SELECT COUNT(*) FROM repositories
               WHERE claimed_by IS NOT NULL OR acquisition_status='ACQUIRING'"""
        ).fetchone()[0])
    finally:
        connection.close()


def early_close_eligibility(
    state: dict[str, Any], inventory: dict[str, Any], *, acquired: int,
    requested_target: int, exploration_remaining: int, active_claims: int,
    minimum_count: int, minimum_fraction: float, required_cycles: int,
) -> dict[str, Any]:
    """Return explicit evidence for a safe useful-frontier early close."""
    streak = 0
    evidence_cycles: list[int] = []
    for cycle in reversed(state.get("cycles", [])):
        decision = cycle.get("discovery_decision") or {}
        after = cycle.get("provider_status_after") or {}
        qualifies = (
            int(cycle.get("new_acquired_revisions", 0)) == 0
            and cycle.get("no_progress_category") == "PROVIDER_SCOPED_FRONTIER_EXHAUSTED"
            and decision.get("run") is True
            and decision.get("targeted") is True
            and int(after.get("acquisition_eligible_frontier", -1)) == 0
        )
        if not qualifies:
            break
        streak += 1
        evidence_cycles.append(int(cycle.get("cycle", 0)))
    provider_statuses = (inventory.get("providers") or {}).values()
    rate_limited = sorted({
        str(row.get("quota_provider") or row.get("provider"))
        for row in provider_statuses if row.get("status") == "RATE_LIMITED"
    })
    minimum_useful = max(int(minimum_count), math.ceil(requested_target * minimum_fraction))
    checks = {
        "production_eligible_frontier_zero": int(
            inventory.get("acquisition_eligible_frontier", -1)
        ) == 0,
        "exploration_budget_remaining_zero": int(exploration_remaining) == 0,
        "targeted_discovery_no_yield_streak_met": streak >= required_cycles,
        "providers_not_rate_limited": not rate_limited,
        "active_acquisition_claims_zero": int(active_claims) == 0,
        "minimum_useful_cohort_met": acquired >= minimum_useful,
        "requested_target_not_reached": acquired < requested_target,
    }
    return {
        "schema": "rtl_eligible_frontier_early_close_evidence_v1",
        "eligible": all(checks.values()),
        "reason": EARLY_CLOSE_REASON,
        "requested_revision_target": requested_target,
        "actual_acquired_revisions": acquired,
        "minimum_useful_cohort": minimum_useful,
        "minimum_fraction": minimum_fraction,
        "required_no_yield_cycles": required_cycles,
        "observed_no_yield_cycles": streak,
        "evidence_cycle_ids": sorted(evidence_cycles),
        "rate_limited_quota_providers": rate_limited,
        "exploration_budget_remaining": int(exploration_remaining),
        "active_acquisition_claims": int(active_claims),
        "checks": checks,
        "evaluated_at": utc_now(),
    }


def stable_hash(values: list[str] | set[str]) -> str:
    material = "\n".join(sorted(values)) + "\n"
    return hashlib.sha256(material.encode()).hexdigest()


def batch_sequence(round_id: str) -> int:
    match = re.search(r"_batch(\d+)$", round_id)
    return int(match.group(1)) if match else 0


def incremental_finalization_enabled(round_id: str) -> bool:
    """Batch 7 is the frozen legacy baseline; Batch 8+ uses staged commit."""
    return batch_sequence(round_id) >= 8


def stage_diagnostics(stage: dict[str, Any]) -> str:
    diagnostics = [
        str(stage.get("stderr_tail") or ""),
        str(stage.get("stdout_tail") or ""),
    ]
    # run_factory_round reports a child-stage failure inside its JSON stdout,
    # while the outer process may legitimately have an empty stderr.  Inspect
    # the bounded end of both streamed logs so deterministic reconciliation is
    # not disabled merely by that reporting boundary.
    for field in ("stderr_log", "stdout_log"):
        path_value = stage.get(field)
        if not path_value:
            continue
        path = Path(str(path_value))
        if not path.is_file():
            continue
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 1024 * 1024))
            diagnostics.append(handle.read().decode("utf-8", errors="replace"))
    return "\n".join(diagnostics)


def deterministic_split_conflict(stage: dict[str, Any]) -> dict[str, Any] | None:
    diagnostic = stage_diagnostics(stage)
    match = TEST_CONFLICT_RE.search(diagnostic) or TRAIN_VAL_CONFLICT_RE.search(diagnostic)
    if not match:
        return None
    try:
        groups = list(map(str, ast.literal_eval(match.group("groups"))))
        splits = set(map(str, ast.literal_eval(match.group("splits"))))
    except (SyntaxError, ValueError):
        return None
    if (
        not groups or len(groups) != len(set(groups))
        or len(splits) < 2 or not splits.issubset({"train", "val", "test"})
    ):
        return None
    return {"old_split_groups": sorted(groups), "old_splits": sorted(splits)}


def deterministic_train_val_conflict(stage: dict[str, Any]) -> list[str] | None:
    conflict = deterministic_split_conflict(stage)
    if not conflict or set(conflict["old_splits"]) != {"train", "val"}:
        return None
    return list(conflict["old_split_groups"])


def assert_auto_split_preconditions(
    corpus: Path, round_dir: Path, round_id: str, cohort: dict[str, Any],
) -> None:
    cohort_keys = set(map(str, cohort.get("revision_keys", [])))
    start = json.loads((round_dir / "start.json").read_text(encoding="utf-8"))
    if revision_keys(corpus) - set(map(str, start.get("revision_keys", []))) != cohort_keys:
        raise RuntimeError("automatic split evolution refuses post-lock acquisition")
    frontier = sqlite3.connect(corpus / "state/frontier.sqlite")
    try:
        active_acquisition = int(frontier.execute(
            "SELECT COUNT(*) FROM repositories WHERE acquisition_status='ACQUIRING'"
        ).fetchone()[0])
    finally:
        frontier.close()
    state_db = sqlite3.connect(corpus / "state/corpus.sqlite")
    try:
        rows = state_db.execute(
            "SELECT repository_revision_key,state,source_path FROM processing_queue WHERE round_id=?",
            (round_id,),
        ).fetchall()
    finally:
        state_db.close()
    terminal_keys = {str(key) for key, state, _ in rows if state == "TERMINAL"}
    unresolved_revision_provenance = sum(not str(source or "") for _, _, source in rows)
    if (
        active_acquisition != 0 or terminal_keys != cohort_keys
        or len(rows) != len(cohort_keys) or unresolved_revision_provenance != 0
    ):
        raise RuntimeError("automatic split evolution preconditions are not satisfied")


def write_auto_train_val_plan(
    corpus: Path, round_dir: Path, round_id: str, cohort: dict[str, Any], groups: list[str],
) -> Path:
    assert_auto_split_preconditions(corpus, round_dir, round_id, cohort)
    cohort_path = round_dir / "cohort_lock.json"
    plan_path = round_dir / "split_reconciliation_plan.json"
    components = normalize_reconciliation_components(corpus, [{
        "old_split_groups": groups,
        "old_splits": ["train", "val"],
    }])
    plan = {
        "schema": "rtl_split_reconciliation_v1",
        "reconciliation_mode": "AUTO_RECONCILE_TRAIN_VAL_V1",
        "reason": "NEW_CLOSURE_EVIDENCE",
        "round_id": round_id,
        "split_epoch": f"{round_id}_auto_train_val_v1",
        "policy": "TRAIN_VAL_COMPONENT_TO_VAL",
        "cohort_revision_count": int(cohort["acquired_revision_count"]),
        "cohort_lock_sha256": cohort_admission_digest(round_dir),
        "component_normalization": "GLOBAL_MAXIMAL_TRANSITIVE_SPLITGROUP_COMPONENT_V1",
        "components": components,
    }
    finalize_canonical_plan(plan)
    atomic(plan_path, plan)
    return plan_path


def canonical_component_hash(groups: list[str] | set[str]) -> str:
    material = "\n".join(sorted(set(map(str, groups)))) + "\n"
    return hashlib.sha256(material.encode()).hexdigest()


def canonical_authorization_hash(
    groups: list[str] | set[str], splits: list[str] | set[str], target_split: str,
) -> str:
    value = {
        "authorization_scope": "FULL_TRANSITIVE_COMPONENT",
        "canonical_component_members": sorted(set(map(str, groups))),
        "input_splits": sorted(set(map(str, splits))),
        "target_split": str(target_split),
    }
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def finalize_canonical_plan(plan: dict[str, Any]) -> None:
    groups = [
        set(map(str, component.get("old_split_groups", [])))
        for component in plan.get("components", [])
    ]
    overlap_count = sum(
        bool(left & right)
        for index, left in enumerate(groups)
        for right in groups[index + 1:]
    )
    plan["reconciliation_plan_components_pairwise_disjoint"] = overlap_count == 0
    plan["component_overlap_count"] = overlap_count
    plan["component_boundary_identity_edges"] = 0
    plan["component_member_loss"] = 0
    material = dict(plan)
    material.pop("plan_sha256", None)
    plan["plan_sha256"] = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def normalize_reconciliation_components(
    corpus: Path, conflicts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Canonicalize raw split conflicts into maximal disjoint components.

    Raw audit records are edges, not authorization units.  Union every touched
    active SplitGroup before classifying the resulting component.  Historical
    supersession/merge lineage is expanded to a fixed point and retained as
    explanatory scope; exact authorization remains the active group set that
    the finalizer recomputes from the full identity graph.
    """
    assignment_path = corpus / "manifests/split_assignments.jsonl"
    assignments: dict[str, dict[str, Any]] = {}
    if assignment_path.is_file():
        for line in assignment_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                assignments[str(row["split_group_id"])] = row

    parent: dict[str, str] = {}

    def find(group: str) -> str:
        parent.setdefault(group, group)
        if parent[group] != group:
            parent[group] = find(parent[group])
        return parent[group]

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    raw_by_group: dict[str, list[dict[str, Any]]] = {}
    for conflict in conflicts:
        groups = sorted(set(map(str, conflict.get("old_split_groups", []))))
        if not groups:
            raise RuntimeError("split conflict lacks historical SplitGroups")
        for group in groups:
            find(group)
            raw_by_group.setdefault(group, []).append(conflict)
        for group in groups[1:]:
            union(groups[0], group)

    active_components: dict[str, set[str]] = {}
    for group in parent:
        active_components.setdefault(find(group), set()).add(group)

    lineage_adjacency: dict[str, set[str]] = {}
    for group, assignment in assignments.items():
        linked = set(map(str, assignment.get("merged_from", [])))
        if assignment.get("superseded_by"):
            linked.add(str(assignment["superseded_by"]))
        for other in linked:
            lineage_adjacency.setdefault(group, set()).add(other)
            lineage_adjacency.setdefault(other, set()).add(group)

    # Historical lineage can reveal that two apparently separate raw-conflict
    # components are one identity component.  Expand every touched group to a
    # fixed point, then union active groups whose historical closures overlap.
    lineage_owner: dict[str, str] = {}
    for active_group in list(parent):
        closure = {active_group}
        pending = [active_group]
        while pending:
            group = pending.pop()
            for linked in lineage_adjacency.get(group, set()):
                if linked not in closure:
                    closure.add(linked)
                    pending.append(linked)
        for historical_group in closure:
            owner = lineage_owner.setdefault(historical_group, active_group)
            union(active_group, owner)

    active_components = {}
    for group in parent:
        active_components.setdefault(find(group), set()).add(group)

    normalized: list[dict[str, Any]] = []
    for groups in active_components.values():
        historical = set(groups)
        pending = list(groups)
        while pending:
            group = pending.pop()
            for linked in lineage_adjacency.get(group, set()):
                if linked not in historical:
                    historical.add(linked)
                    pending.append(linked)
        raw_rows = {
            id(row): row for group in groups for row in raw_by_group.get(group, [])
        }.values()
        raw_splits = {
            str(split) for row in raw_rows for split in row.get("old_splits", [])
        }
        assignment_splits = {
            str(assignments[group].get("split"))
            for group in groups
            if group in assignments and assignments[group].get("split")
        }
        splits = assignment_splits or raw_splits
        if len(splits) < 2 or not splits.issubset({"train", "val", "test"}):
            raise RuntimeError(
                f"canonical split component has an unknown/nonconflicting split set: {sorted(splits)}"
            )
        target = "test" if "test" in splits else "val"
        members = sorted(groups)
        component_hash = canonical_component_hash(members)
        normalized.append({
            "component_id": component_hash,
            "canonical_component_hash": component_hash,
            "canonical_component_members": members,
            "old_split_groups": members,
            "historical_lineage_split_groups": sorted(historical),
            "old_splits": sorted(splits),
            "input_splits": sorted(splits),
            "target_split": target,
            "authorization_scope": "FULL_TRANSITIVE_COMPONENT",
            "authorized_component_hash": canonical_authorization_hash(
                members, splits, target,
            ),
            "component_boundary_identity_edges": 0,
            "component_member_loss": 0,
            "component_split_set_exactly_known": True,
            "evidence_types": ["shared_source", "hierarchy", "project_closure"],
        })
    return sorted(normalized, key=lambda row: row["component_id"])


def current_split_profile(corpus: Path) -> dict[str, Any]:
    path = corpus / "manifests/split_profiles.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    current = [row for row in rows if row.get("status") == "CURRENT"]
    if len(current) != 1:
        raise RuntimeError("automatic profile rollover requires exactly one CURRENT profile")
    return current[0]


def next_versioned_identity(value: str, prefix: str) -> str:
    match = re.fullmatch(re.escape(prefix) + r"(\d+)", value)
    if not match:
        raise RuntimeError(f"non-versioned split identity: {value}")
    return f"{prefix}{int(match.group(1)) + 1}"


def campaign_consumption_contract(corpus: Path, round_id: str) -> tuple[Path, dict[str, Any]]:
    match = re.match(r"p2f_\d+_(.+)_batch\d+$", round_id)
    objective_id = match.group(1) if match else ""
    path, contract, _ = load_and_validate(corpus, objective_id, allowed_states={INTERNAL})
    return path, contract


def write_consumption_audit(
    corpus: Path, round_dir: Path, round_id: str, cohort_hash: str,
) -> tuple[Path, dict[str, Any]]:
    contract_path, contract = campaign_consumption_contract(corpus, round_id)
    profile = current_split_profile(corpus)
    connection = sqlite3.connect(corpus / "state/corpus.sqlite")
    try:
        recorded = connection.execute(
            """SELECT event_type,COUNT(*) FROM events
               WHERE UPPER(event_type) LIKE '%TRAINING_CONSUMED%'
                  OR UPPER(event_type) LIKE '%EVALUATION_CONSUMED%'
                  OR UPPER(event_type) LIKE '%PROFILE_PINNED%'
               GROUP BY event_type"""
        ).fetchall()
        event_count = int(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0])
    finally:
        connection.close()
    if recorded:
        raise RuntimeError("split profile has recorded downstream consumption")
    audit_path = round_dir / "split_profile_consumption_audit.json"
    audit = {
        "schema": "rtl_split_profile_consumption_audit_v1",
        "status": "NO_RECORDED_CONSUMPTION",
        "profile_id": profile["profile_id"],
        "round_id": round_id,
        "cohort_lock_sha256": cohort_hash,
        "event_count_checked": event_count,
        "recorded_training_consumers": 0,
        "recorded_benchmark_evaluation_consumers": 0,
        "campaign_contract": {
            "path": str(contract_path), "sha256": file_sha256(contract_path),
            "contract_sha256": contract["contract_sha256"],
            "state": contract["consumption_state"],
        },
        "external_consumption_assertion": "PROHIBITED_BY_CAMPAIGN_CONTRACT",
        "audited_at": utc_now(),
    }
    atomic(audit_path, audit)
    audit_hash = file_sha256(audit_path)
    receipt_path = audit_path.with_name(f"{audit_path.name}.admission.json")
    if not receipt_path.exists():
        atomic(receipt_path, {
            "schema": "rtl_immutable_artifact_admission_v1",
            "object_id": audit_path.name,
            "path": str(audit_path),
            "sha256": audit_hash,
            "size": audit_path.stat().st_size,
            "producer": "run_until_revision_target.py",
            "recorded_at": utc_now(),
            "rehash_required": False,
        })
    return audit_path, audit


def write_auto_profile_rollover_plan(
    corpus: Path, round_dir: Path, round_id: str, cohort: dict[str, Any],
    components: list[dict[str, Any]],
) -> Path:
    assert_auto_split_preconditions(corpus, round_dir, round_id, cohort)
    cohort_path = round_dir / "cohort_lock.json"
    cohort_hash = cohort_admission_digest(round_dir)
    old = current_split_profile(corpus)
    new_profile_id = next_versioned_identity(str(old["profile_id"]), "rtl_split_profile_v")
    new_split_schema = next_versioned_identity(str(old["split_schema"]), "rtl_split_v")
    audit_path, _ = write_consumption_audit(corpus, round_dir, round_id, cohort_hash)
    plan_path = round_dir / "split_reconciliation_plan.json"
    normalized = normalize_reconciliation_components(corpus, components)
    plan = {
        "schema": "rtl_split_profile_transition_v1",
        "reconciliation_mode": "AUTO_SPLIT_PROFILE_ROLLOVER_V1",
        "reason": "NEW_CROSS_TEST_CLOSURE_EVIDENCE",
        "round_id": round_id,
        "split_epoch": f"{round_id}_{new_profile_id}",
        "policy": "CONSERVATIVE_SPLIT_PROMOTION_V1",
        "cohort_revision_count": int(cohort["acquired_revision_count"]),
        "cohort_lock_sha256": cohort_hash,
        "old_profile": {
            "profile_id": old["profile_id"], "split_schema": old["split_schema"],
            "status_after": "SUPERSEDED",
        },
        "new_profile": {
            "profile_id": new_profile_id, "split_schema": new_split_schema,
            "status": "CURRENT",
        },
        "consumption_audit": {"path": str(audit_path), "sha256": json.loads(
            audit_path.with_name(f"{audit_path.name}.admission.json").read_text(
                encoding="utf-8"
            )
        )["sha256"]},
        "component_normalization": "GLOBAL_MAXIMAL_TRANSITIVE_SPLITGROUP_COMPONENT_V1",
        "components": normalized,
    }
    finalize_canonical_plan(plan)
    atomic(plan_path, plan)
    return plan_path


def write_plan_from_staged_audit(
    corpus: Path, round_dir: Path, round_id: str, cohort: dict[str, Any],
) -> Path | None:
    path = round_dir / "staged_split_audit.json"
    if not path.is_file():
        return None
    audit = json.loads(path.read_text(encoding="utf-8"))
    if audit.get("error") or audit.get("unknown_split_groups"):
        raise RuntimeError("staged split audit contains a hard-stop condition")
    conflicts = list(audit.get("conflicts", []))
    if not conflicts:
        return None
    normalized = normalize_reconciliation_components(corpus, conflicts)
    if any("test" in set(map(str, row.get("old_splits", []))) for row in normalized):
        return write_auto_profile_rollover_plan(
            corpus, round_dir, round_id, cohort, normalized,
        )
    assert_auto_split_preconditions(corpus, round_dir, round_id, cohort)
    cohort_path = round_dir / "cohort_lock.json"
    plan_path = round_dir / "split_reconciliation_plan.json"
    plan = {
        "schema": "rtl_split_reconciliation_v1",
        "reconciliation_mode": "AUTO_RECONCILE_TRAIN_VAL_V1",
        "reason": "NEW_CLOSURE_EVIDENCE", "round_id": round_id,
        "split_epoch": f"{round_id}_auto_train_val_v1",
        "policy": "TRAIN_VAL_COMPONENT_TO_VAL",
        "cohort_revision_count": int(cohort["acquired_revision_count"]),
        "cohort_lock_sha256": cohort_admission_digest(round_dir),
        "component_normalization": "GLOBAL_MAXIMAL_TRANSITIVE_SPLITGROUP_COMPONENT_V1",
        "components": normalized,
    }
    finalize_canonical_plan(plan)
    atomic(plan_path, plan)
    return plan_path


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cohort_admission_digest(round_dir: Path) -> str:
    receipt_path = round_dir / "cohort_lock.admission.json"
    if receipt_path.is_file():
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        recorded = str(receipt.get("sha256") or "")
        if (
            receipt.get("schema") == "rtl_immutable_artifact_admission_v1"
            and receipt.get("object_id") == "cohort_lock.json"
            and re.fullmatch(r"[0-9a-f]{64}", recorded)
            and receipt.get("rehash_required") is False
        ):
            return recorded
        raise RuntimeError("invalid cohort-lock admission receipt")
    controller_path = round_dir / "target_controller.json"
    if controller_path.is_file():
        controller = json.loads(controller_path.read_text(encoding="utf-8"))
        recorded = str(controller.get("cohort_lock_sha256") or "")
        if re.fullmatch(r"[0-9a-f]{64}", recorded):
            return recorded
    raise RuntimeError("REHASH_REQUIRED: cohort lock lacks an admission digest")


def validate_cohort_lock(
    lock: dict[str, Any], start: dict[str, Any], target: int
) -> None:
    keys = [str(value) for value in lock.get("revision_keys", [])]
    if lock.get("schema") != COHORT_LOCK_SCHEMA:
        raise ValueError("existing cohort lock has an unsupported schema")
    if lock.get("factory_round_id") != start.get("factory_round_id"):
        raise ValueError("existing cohort lock belongs to a different round")
    if int(lock.get("target_new_acquired_revisions", -1)) != target:
        raise ValueError("existing cohort lock target differs from requested target")
    if len(keys) != len(set(keys)) or keys != sorted(keys):
        raise ValueError("existing cohort lock keys are not unique and sorted")
    if int(lock.get("acquired_revision_count", -1)) != len(keys):
        raise ValueError("existing cohort lock count does not match its key set")
    if int(lock.get("actual_cohort_size", len(keys))) != len(keys):
        raise ValueError("existing cohort lock actual size does not match its key set")
    early_close = bool(lock.get("early_close", False))
    if early_close:
        if lock.get("early_close_reason") != EARLY_CLOSE_REASON:
            raise ValueError("existing cohort lock has an unsupported early-close reason")
        if len(keys) >= target:
            raise ValueError("early-close cohort unexpectedly reached its requested target")
        evidence = lock.get("early_close_evidence") or {}
        if evidence.get("eligible") is not True or not all(
            (evidence.get("checks") or {}).values()
        ):
            raise ValueError("existing cohort lock lacks valid early-close evidence")
    elif len(keys) < target:
        raise ValueError("ordinary cohort lock is below its requested target")
    if stable_hash(keys) != lock.get("revision_keys_sha256"):
        raise ValueError("existing cohort lock key hash mismatch")
    if stable_hash(set(start["revision_keys"])) != lock.get("start_revision_keys_sha256"):
        raise ValueError("existing cohort lock start boundary mismatch")


def lock_acquisition_cohort(
    corpus: Path, start: dict[str, Any], target: int,
    early_close_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path = cohort_lock_path(corpus, str(start["factory_round_id"]))
    if path.is_file():
        lock = json.loads(path.read_text(encoding="utf-8"))
        validate_cohort_lock(lock, start, target)
        return lock
    freeze_path = corpus / "state/acquisition_freeze.lock"
    freeze_path.parent.mkdir(parents=True, exist_ok=True)
    database = corpus / "state/frontier.sqlite"
    with freeze_path.open("a+") as freeze_handle:
        fcntl.flock(freeze_handle, fcntl.LOCK_EX)
        connection = sqlite3.connect(database)
        try:
            connection.execute("BEGIN IMMEDIATE")
            active = connection.execute(
                "SELECT repository_key,claimed_by,acquisition_status FROM repositories "
                "WHERE claimed_by IS NOT NULL OR acquisition_status='ACQUIRING'"
            ).fetchall()
            if active:
                raise RuntimeError(f"cannot lock cohort with {len(active)} active acquisition claims")
            current = {
                str(row[0])
                for row in connection.execute(
                    "SELECT repository_revision_key FROM repository_revisions"
                )
            }
            acquired = sorted(current - set(start["revision_keys"]))
            early_close = bool(early_close_evidence and early_close_evidence.get("eligible"))
            if len(acquired) < target and not early_close:
                raise ValueError(f"cannot lock cohort before target: {len(acquired)} < {target}")
            if early_close and int(early_close_evidence.get("actual_acquired_revisions", -1)) != len(acquired):
                raise ValueError("early-close evidence no longer matches the acquired key set")
            lock = {
                "schema": COHORT_LOCK_SCHEMA,
                "factory_round_id": start["factory_round_id"],
                "locked_at": utc_now(),
                "target_new_acquired_revisions": target,
                "requested_revision_target": target,
                "acquired_revision_count": len(acquired),
                "actual_cohort_size": len(acquired),
                "cohort_size": len(acquired),
                "early_close": early_close,
                "early_close_reason": EARLY_CLOSE_REASON if early_close else None,
                "early_close_evidence": early_close_evidence if early_close else None,
                "start_revision_count": len(start["revision_keys"]),
                "start_revision_keys_sha256": stable_hash(set(start["revision_keys"])),
                "revision_keys_sha256": stable_hash(acquired),
                "revision_keys": acquired,
            }
            # The DB write lock remains held until the immutable file is fully
            # replaced, so the enumerated key set and its evidence are one cut.
            atomic(path, lock)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
    validate_cohort_lock(lock, start, target)
    return lock


def run_command(
    stage: str, command: list[str], log_dir: Path,
    heartbeat: Any | None = None,
) -> dict[str, Any]:
    result = run_streamed(command, log_dir, stage, heartbeat=heartbeat)
    return {
        "stage": stage,
        "started_at": result["started_at"],
        "completed_at": result["completed_at"],
        "returncode": result["returncode"],
        "state": "PASS" if result["returncode"] == 0 else "FAIL",
        "command": command,
        "stdout_tail": result["stdout_tail"][-8000:],
        "stderr_tail": result["stderr_tail"][-8000:],
        "stdout_log": result["stdout_log"],
        "stderr_log": result["stderr_log"],
        "last_output_at": result["last_output_at"],
        "child_pid": result["child_pid"],
    }


def run_tracked_command(
    state_path: Path, state: dict[str, Any], stage: str, command: list[str],
) -> dict[str, Any]:
    """Persist a heartbeat around a blocking component invocation."""
    state.update({
        "active_stage": stage,
        "active_stage_started_at": utc_now(),
        "active_stage_command": command,
        "updated_at": utc_now(),
    })
    atomic(state_path, state)
    def heartbeat(progress: dict[str, Any]) -> None:
        state.update({
            "controller_heartbeat_at": progress["heartbeat_at"],
            "active_child_pid": progress["child_pid"],
            "active_child_last_output_at": progress["last_output_at"],
            "active_child_elapsed_seconds": progress["elapsed_seconds"],
            "active_child_stdout_log": progress["stdout_log"],
            "active_child_stderr_log": progress["stderr_log"],
            "updated_at": progress["heartbeat_at"],
        })
        pipeline_path = state.get("pipeline_status_path")
        if pipeline_path:
            state["pipeline_status"] = read_json(Path(str(pipeline_path)))
        atomic(state_path, state)
    try:
        return run_command(stage, command, state_path.parent / "logs", heartbeat)
    finally:
        state.pop("active_stage", None)
        state.pop("active_stage_started_at", None)
        state.pop("active_stage_command", None)
        for key in list(state):
            if key.startswith("active_child_"):
                state.pop(key, None)
        state["updated_at"] = utc_now()
        atomic(state_path, state)


def read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def start_processing_pipeline(
    args: argparse.Namespace, scripts: Path, round_dir: Path,
) -> tuple[subprocess.Popen[Any], Any, Any, Path]:
    status_path = round_dir / "pipeline_status.json"
    log_dir = round_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_handle = (log_dir / "pipeline_manager.stdout.log").open("ab")
    stderr_handle = (log_dir / "pipeline_manager.stderr.log").open("ab")
    command = [
        sys.executable, str(scripts / "run_pipelined_processing.py"),
        "--corpus-root", str(args.corpus_root), "--round-id", args.factory_round_id,
        "--start-json", str(round_dir / "start.json"),
        "--workers", str(args.pipeline_workers),
        "--max-repo-seconds", str(args.max_repo_seconds),
        "--poll-seconds", str(args.pipeline_poll_seconds),
        "--parent-pid", str(os.getpid()), "--status-path", str(status_path),
    ]
    process = subprocess.Popen(command, stdout=stdout_handle, stderr=stderr_handle)
    return process, stdout_handle, stderr_handle, status_path


def stop_processing_pipeline(
    process: subprocess.Popen[Any] | None, stdout_handle: Any | None, stderr_handle: Any | None,
) -> None:
    if process is not None and process.poll() is None:
        process.send_signal(signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
    if stdout_handle is not None:
        stdout_handle.close()
    if stderr_handle is not None:
        stderr_handle.close()


def completion_is_final(summary: dict[str, Any]) -> bool:
    return (
        summary.get("state") == "PASS"
        and summary.get("completion_invariants", {}).get("valid") is True
        and any(
        stage.get("stage") == "phase2_round_delta"
        and stage.get("state") == "PASS"
        and stage.get("yield_status") == "FINAL"
        for stage in summary.get("stages", [])
        )
    )


def discovery_decision(
    inventory: dict[str, Any], cycle_number: int, *,
    low_watermark: int | None = None, high_watermark: int | None = None,
    cadence_cycles: int, frontier_threshold: int | None = None,
) -> dict[str, Any]:
    raw = int(inventory.get("raw_frontier", 0))
    eligible = int(inventory.get("acquisition_eligible_frontier", 0))
    healthy = list(inventory.get("healthy_discovery_providers", []))
    # frontier_threshold remains a compatibility alias for v4.2 callers/tests.
    low = max(0, int(low_watermark if low_watermark is not None else frontier_threshold or 250))
    high = max(low, int(high_watermark if high_watermark is not None else max(low, 1000)))
    below_threshold = eligible < low
    cadence_due = cadence_cycles > 0 and cycle_number % cadence_cycles == 0
    run = bool(healthy) and (below_threshold or cadence_due)
    if raw > 0 and eligible == 0 and healthy:
        reason = "PROVIDER_SCOPED_FRONTIER_EXHAUSTED"
    elif raw == 0 and healthy:
        reason = "FRONTIER_EXHAUSTED_REFRESH"
    elif below_threshold and healthy:
        reason = "ELIGIBLE_FRONTIER_BELOW_THRESHOLD"
    elif cadence_due and healthy:
        reason = "CADENCE_REFRESH"
    elif not healthy:
        reason = "NO_DISCOVERY_PROVIDER_AVAILABLE"
    else:
        reason = "ELIGIBLE_FRONTIER_SUFFICIENT_ACQUISITION_FIRST"
    return {
        "run": run,
        "reason": reason,
        "targeted": bool(run and below_threshold),
        "providers": healthy,
        "raw_frontier": raw,
        "acquisition_eligible_frontier": eligible,
        "eligible_low_watermark": low,
        "eligible_high_watermark": high,
        "cadence_cycles": cadence_cycles,
    }


def seconds_until(timestamp: str | None) -> int:
    if not timestamp:
        return 0
    try:
        reset = dt.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if reset.tzinfo is None:
            reset = reset.replace(tzinfo=dt.timezone.utc)
        return max(0, math.ceil((reset - dt.datetime.now(dt.timezone.utc)).total_seconds()))
    except ValueError:
        return 0


def provider_scoped_backoff(
    inventory: dict[str, Any], refresh_seconds: int,
) -> dict[str, Any]:
    now = dt.datetime.now(dt.timezone.utc)
    refresh_at = now + dt.timedelta(seconds=max(1, refresh_seconds))
    candidates = [(refresh_at, "HEALTHY_PROVIDER_DISCOVERY_REFRESH")]
    reset_at = inventory.get("earliest_reset_at")
    if reset_at:
        try:
            reset = dt.datetime.fromisoformat(str(reset_at).replace("Z", "+00:00"))
            if reset.tzinfo is None:
                reset = reset.replace(tzinfo=dt.timezone.utc)
            if reset > now:
                candidates.append((reset, "PROVIDER_RESET"))
        except ValueError:
            pass
    wake_at, reason = min(candidates, key=lambda item: item[0])
    return {
        "wake_at": wake_at.replace(microsecond=0).isoformat(),
        "seconds": max(1, math.ceil((wake_at - now).total_seconds())),
        "wake_reason": reason,
        "next_healthy_provider_discovery_at": refresh_at.replace(microsecond=0).isoformat(),
        "earliest_provider_reset_at": reset_at,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-root", type=Path, default=Path.home() / "work/data/rtl_corpus")
    parser.add_argument("--local-source-root", type=Path, default=Path.home() / "work/_downloads")
    parser.add_argument("--providers", default="github,gitlab,codeberg,fusesoc")
    parser.add_argument("--factory-round-id", required=True)
    parser.add_argument("--target-new-acquired", type=int, required=True)
    parser.add_argument("--marginal-large-xlarge-target", type=float)
    parser.add_argument("--marginal-large-xlarge-stretch", type=float)
    parser.add_argument("--discover-budget", type=int, default=20_000)
    parser.add_argument("--acquire-budget", type=int, default=2_000, help="Maximum acquisition attempts per controller cycle")
    parser.add_argument("--process-budget", type=int, default=600)
    parser.add_argument("--max-repo-seconds", type=int, default=900)
    parser.add_argument("--r1-budget", type=int, default=10)
    parser.add_argument("--max-cycles", type=int, default=500, help="Watchdog interval; no longer a hard acquisition stop")
    parser.add_argument("--max-total-cycles", type=int, default=0, help="Emergency-only hard stop; zero means unlimited")
    parser.add_argument("--max-no-progress-cycles", type=int, default=5)
    parser.add_argument("--starvation-window-cycles", type=int, default=5)
    parser.add_argument("--starvation-min-attempts", type=int, default=100)
    parser.add_argument("--starvation-yield-threshold", type=float, default=0.05)
    parser.add_argument("--starvation-cooldown-cycles", type=int, default=3)
    parser.add_argument("--refresh-query-budget", type=int, default=100)
    parser.add_argument("--refresh-graph-budget", type=int, default=200)
    parser.add_argument("--retry-delay-seconds", type=int, default=30)
    parser.add_argument("--discovery-frontier-threshold", type=int, default=None,
                        help="Deprecated v4.2 alias for --discovery-eligible-low-watermark")
    parser.add_argument("--discovery-eligible-low-watermark", type=int, default=250)
    parser.add_argument("--discovery-eligible-high-watermark", type=int, default=1000)
    parser.add_argument("--targeted-discovery-query-budget", type=int, default=8)
    parser.add_argument("--targeted-discovery-graph-budget", type=int, default=4)
    parser.add_argument("--targeted-discovery-seed-budget", type=int, default=5000)
    parser.add_argument("--targeted-discovery-request-wall-seconds", type=int, default=30)
    parser.add_argument("--post-discovery-acquire-budget", type=int, default=500)
    parser.add_argument("--discovery-cadence-cycles", type=int, default=15)
    parser.add_argument("--discovery-quota-reserve", type=int, default=100)
    parser.add_argument("--provider-backoff-jitter-seconds", type=int, default=5)
    parser.add_argument("--provider-scoped-refresh-seconds", type=int, default=300)
    parser.add_argument("--exploration-fraction", type=float, default=0.15)
    parser.add_argument("--early-close-no-yield-cycles", type=int, default=5)
    parser.add_argument("--early-close-minimum-revisions", type=int, default=1000)
    parser.add_argument("--early-close-minimum-fraction", type=float, default=0.75)
    parser.add_argument(
        "--pipelined-processing", action=argparse.BooleanOptionalAction, default=True,
        help="Stage revision-local processing during acquisition; cohort lock remains the publication barrier",
    )
    parser.add_argument("--pipeline-workers", type=int, default=2)
    parser.add_argument("--pipeline-poll-seconds", type=float, default=2.0)
    parser.add_argument(
        "--acquisition-execution-mode",
        choices=("auto", "sequential", "bounded_parallel"), default="auto",
        help="Auto preserves Batch 5 as a sequential baseline and enables bounded parallel acquisition for Batch 6+",
    )
    parser.add_argument("--parallel-acquisition-activation-batch", type=int, default=6)
    parser.add_argument("--parallel-acquisition-fast-workers", type=int, default=3)
    parser.add_argument("--parallel-acquisition-unknown-workers", type=int, default=1)
    parser.add_argument("--parallel-acquisition-slow-workers", type=int, default=1)
    parser.add_argument("--parallel-acquisition-slow-fraction", type=float, default=0.20)
    parser.add_argument("--parallel-acquisition-size-threshold-bytes", type=int, default=64 * 1024 * 1024)
    parser.add_argument(
        "--auto-reconcile-train-val-activation-batch", type=int, default=6,
        help="Enable deterministic train+val-to-val reconciliation retry for Batch 6+ only",
    )
    return parser.parse_args()


def acquisition_execution_mode(args: argparse.Namespace) -> str:
    if args.acquisition_execution_mode != "auto":
        return args.acquisition_execution_mode
    match = re.search(r"_batch(\d+)$", args.factory_round_id)
    batch = int(match.group(1)) if match else 0
    return "bounded_parallel" if batch >= args.parallel_acquisition_activation_batch else "sequential"


def acquisition_execution_args(args: argparse.Namespace) -> list[str]:
    if acquisition_execution_mode(args) != "bounded_parallel":
        return []
    return [
        "--bounded-parallel-acquisition",
        "--parallel-fast-workers", str(args.parallel_acquisition_fast_workers),
        "--parallel-unknown-workers", str(args.parallel_acquisition_unknown_workers),
        "--parallel-slow-workers", str(args.parallel_acquisition_slow_workers),
        "--parallel-slow-fraction", str(args.parallel_acquisition_slow_fraction),
        "--parallel-size-threshold-bytes", str(args.parallel_acquisition_size_threshold_bytes),
    ]


def main() -> int:
    args = parse_args()
    if args.target_new_acquired <= 0:
        raise SystemExit("--target-new-acquired must be positive")
    selected_providers = [value.strip() for value in args.providers.split(",") if value.strip()]
    scripts = Path(__file__).parent
    round_dir = args.corpus_root / "quality/phase2/rounds" / args.factory_round_id
    round_dir.mkdir(parents=True, exist_ok=True)
    state_path = round_dir / "target_controller.json"
    process_lock = round_dir / ".target_controller.lock"
    owner_path = args.corpus_root / "state/acquisition_target_owner.lock"
    owner_path.parent.mkdir(parents=True, exist_ok=True)
    with process_lock.open("a+") as lock_handle, owner_path.open("a+") as owner_handle:
        try:
            fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({"schema": STATE_SCHEMA, "state": "ALREADY_RUNNING", "factory_round_id": args.factory_round_id}, indent=2))
            return 2
        try:
            fcntl.flock(owner_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({"schema": STATE_SCHEMA, "state": "ACQUISITION_OWNER_BUSY", "factory_round_id": args.factory_round_id}, indent=2))
            return 2

        start_path = round_dir / "start.json"
        start = json.loads(start_path.read_text(encoding="utf-8")) if start_path.is_file() else capture_round(args.corpus_root, args.factory_round_id)
        state: dict[str, Any]
        if state_path.is_file():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if state.get("schema") in {
                "rtl_revision_target_controller_v1", "rtl_revision_target_controller_v2",
                "rtl_revision_target_controller_v3", "rtl_revision_target_controller_v4",
                "rtl_revision_target_controller_v4_1", "rtl_revision_target_controller_v4_2",
                "rtl_revision_target_controller_v4_3", "rtl_revision_target_controller_v4_3_1",
                "rtl_revision_target_controller_v4_4", "rtl_revision_target_controller_v4_4_1",
                "rtl_revision_target_controller_v4_5",
                "rtl_revision_target_controller_v4_6",
                "rtl_revision_target_controller_v4_7",
            }:
                prior_schema = state["schema"]
                state["schema"] = STATE_SCHEMA
                if state.get("migrated_from_schema"):
                    state["upgraded_from_schema"] = prior_schema
                else:
                    state["migrated_from_schema"] = prior_schema
            elif state.get("schema") != STATE_SCHEMA:
                raise ValueError("unsupported target-controller state schema")
            if int(state.get("target_new_acquired_revisions", -1)) != args.target_new_acquired:
                raise ValueError("saved controller target differs from requested target")
        else:
            state = {
                "schema": STATE_SCHEMA,
                "factory_round_id": args.factory_round_id,
                "target_new_acquired_revisions": args.target_new_acquired,
                "start_revision_count": len(start["revision_keys"]),
                "start_revision_keys_sha256": stable_hash(set(start["revision_keys"])),
                "created_at": utc_now(),
                "cycles": [],
                "consecutive_no_progress_cycles": 0,
                "starvation_events": [],
                "objectives": {
                    "marginal_large_xlarge_design_instance_share": {
                        "target": args.marginal_large_xlarge_target,
                        "stretch_target": args.marginal_large_xlarge_stretch,
                        "hard_completion_gate": False,
                    },
                    "quality_and_completion_gates": "UNCHANGED",
                },
            }

        execution_mode = acquisition_execution_mode(args)
        execution_contract = {
            "schema": "bounded_parallel_acquisition_v2" if execution_mode == "bounded_parallel" else "sequential_acquisition_v1",
            "mode": execution_mode,
            "activation_batch": args.parallel_acquisition_activation_batch,
            "fast_workers": args.parallel_acquisition_fast_workers if execution_mode == "bounded_parallel" else 1,
            "unknown_workers": args.parallel_acquisition_unknown_workers if execution_mode == "bounded_parallel" else 0,
            "slow_workers": args.parallel_acquisition_slow_workers if execution_mode == "bounded_parallel" else 0,
            "slow_fraction": args.parallel_acquisition_slow_fraction if execution_mode == "bounded_parallel" else 0.0,
            "size_threshold_bytes": args.parallel_acquisition_size_threshold_bytes,
        }
        existing_execution = state.get("acquisition_execution")
        if existing_execution and existing_execution != execution_contract:
            safe_v2_upgrade = (
                existing_execution.get("schema") == "bounded_parallel_acquisition_v1"
                and execution_contract.get("schema") == "bounded_parallel_acquisition_v2"
                and state.get("state") in {None, "ACQUIRING", "ACQUIRING_PIPELINED", "ACQUIRING_BACKOFF"}
                and not (round_dir / "cohort_lock.json").exists()
            )
            if not safe_v2_upgrade:
                raise ValueError("saved acquisition execution policy differs from requested policy")
            state["previous_acquisition_execution"] = existing_execution
            state["acquisition_execution_upgraded_at"] = utc_now()
        state["acquisition_execution"] = execution_contract

        if state.get("state") == "COMPLETE":
            completed = args.corpus_root / "runs/factory" / f"{args.factory_round_id}.json"
            if completed.is_file() and completion_is_final(json.loads(completed.read_text(encoding="utf-8"))):
                state["idempotent_cache_hit"] = True
                print(json.dumps(state, indent=2, sort_keys=True))
                return 0

        pipeline_process: subprocess.Popen[Any] | None = None
        pipeline_stdout = pipeline_stderr = None
        pipeline_status_path: Path | None = None
        if args.pipelined_processing:
            pipeline_process, pipeline_stdout, pipeline_stderr, pipeline_status_path = (
                start_processing_pipeline(args, scripts, round_dir)
            )
            state.update({
                "processing_mode": "pipelined_processing_v1",
                "pipeline_schema": PIPELINE_SCHEMA,
                "pipeline_manager_pid": pipeline_process.pid,
                "pipeline_status_path": str(pipeline_status_path),
                "updated_at": utc_now(),
            })
            atomic(state_path, state)

        try:
            early_close_evidence: dict[str, Any] | None = None
            while not cohort_lock_path(args.corpus_root, args.factory_round_id).is_file():
                current = revision_keys(args.corpus_root) - set(start["revision_keys"])
                if len(current) >= args.target_new_acquired:
                    state.update({
                        "state": "TARGET_REACHED_PENDING_LOCK",
                        "target_reached_at": state.get("target_reached_at") or utc_now(),
                        "target_reached_revision_count": len(current),
                        "new_acquired_revisions": len(current),
                        "remaining": 0,
                        "updated_at": utc_now(),
                    })
                    atomic(state_path, state)
                    break
                if state.get("state") == "TARGET_REACHED_PENDING_LOCK":
                    raise RuntimeError("target-reached state persisted but revision count fell below target")
                state.update({"state": "ACQUIRING_PIPELINED" if args.pipelined_processing else "ACQUIRING", "new_acquired_revisions": len(current), "remaining": args.target_new_acquired - len(current), "updated_at": utc_now()})
                atomic(state_path, state)
                if args.max_total_cycles > 0 and len(state["cycles"]) >= args.max_total_cycles:
                    state.update({"state": "BLOCKED_LOOP_LIMIT", "updated_at": utc_now()})
                    atomic(state_path, state)
                    print(json.dumps(state, indent=2, sort_keys=True))
                    return 2

                inventory = frontier_inventory(
                    args.corpus_root, selected_providers,
                    quota_reserve=args.discovery_quota_reserve,
                )
                state["provider_status"] = inventory
                with FrontierDB(default_frontier_path(args.corpus_root)) as frontier_db:
                    round_budget = frontier_db.round_acquisition_budget_status(
                        args.factory_round_id
                    )
                exploration_remaining = int(
                    round_budget.get("exploration_remaining_claim_capacity", 0)
                )
                early_close_evidence = early_close_eligibility(
                    state, inventory, acquired=len(current),
                    requested_target=args.target_new_acquired,
                    exploration_remaining=exploration_remaining,
                    active_claims=active_acquisition_claims(args.corpus_root),
                    minimum_count=args.early_close_minimum_revisions,
                    minimum_fraction=args.early_close_minimum_fraction,
                    required_cycles=args.early_close_no_yield_cycles,
                )
                state["early_close_evaluation"] = early_close_evidence
                if early_close_evidence["eligible"]:
                    state.update({
                        "state": "PRODUCTION_FRONTIER_EXHAUSTED_PENDING_CLOSE",
                        "requested_revision_target": args.target_new_acquired,
                        "actual_cohort_size": len(current),
                        "early_close": True,
                        "early_close_reason": EARLY_CLOSE_REASON,
                        "early_close_evidence": early_close_evidence,
                        "early_close_pending_at": utc_now(),
                        "new_acquired_revisions": len(current),
                        "remaining": args.target_new_acquired - len(current),
                        "updated_at": utc_now(),
                    })
                    atomic(state_path, state)
                    break
                if inventory["all_providers_in_cooldown"]:
                    delay = seconds_until(inventory.get("earliest_reset_at"))
                    jitter = min(max(0, args.provider_backoff_jitter_seconds), 5)
                    wait_seconds = delay + jitter
                    state.update({
                        "state": "ACQUIRING_BACKOFF",
                        "backoff_started_at": utc_now(),
                        "backoff_until": inventory.get("earliest_reset_at"),
                        "backoff_seconds": wait_seconds,
                        "backoff_reason": "ALL_PROVIDERS_RATE_LIMITED",
                        "updated_at": utc_now(),
                    })
                    atomic(state_path, state)
                    if wait_seconds > 0:
                        time.sleep(wait_seconds)
                    state.update({
                        "state": "ACQUIRING_PIPELINED" if args.pipelined_processing else "ACQUIRING",
                        "backoff_completed_at": utc_now(),
                        "updated_at": utc_now(),
                    })
                    atomic(state_path, state)
                    continue

                remaining = args.target_new_acquired - len(current)
                attempt_budget = min(args.acquire_budget, max(1, math.ceil(remaining * 1.25)))
                cycle_number = len(state["cycles"]) + 1
                cycle = {
                    "cycle": cycle_number, "started_at": utc_now(),
                    "before_new_acquired_revisions": len(current), "stages": [],
                    "provider_status_before": inventory,
                }
                attempt_watermark = acquisition_watermark(args.corpus_root)
                cycle["stages"].append(run_tracked_command(state_path, state, "acquisition", [
                    sys.executable, str(scripts / "acquire_frontier.py"),
                    "--corpus-root", str(args.corpus_root), "--providers", args.providers,
                    "--max-repos", str(attempt_budget), "--max-repo-seconds", str(args.max_repo_seconds),
                    "--controller-round-id", args.factory_round_id,
                    "--round-started-at", str(start["captured_at"]),
                    "--round-revision-target", str(args.target_new_acquired),
                    "--enable-large-repository-lane",
                    "--exploration-fraction", str(args.exploration_fraction),
                    *acquisition_execution_args(args),
                ]))
                # Acquisition may drain or replenish provider state.  Make the
                # discovery decision from the post-acquisition inventory.
                inventory_after_acquisition = frontier_inventory(
                    args.corpus_root, selected_providers,
                    quota_reserve=args.discovery_quota_reserve,
                )
                cycle["provider_status_after_acquisition"] = inventory_after_acquisition
                current_after_acquisition = revision_keys(args.corpus_root) - set(start["revision_keys"])
                decision = discovery_decision(
                    inventory_after_acquisition, cycle_number,
                    # A v4.2 resume may still pass the old raw-frontier value
                    # (commonly 10,000).  It may lower, but must never inflate,
                    # the v4.3 eligible-frontier refill watermark.
                    low_watermark=(min(args.discovery_eligible_low_watermark,
                                       args.discovery_frontier_threshold)
                                   if args.discovery_frontier_threshold is not None
                                   else args.discovery_eligible_low_watermark),
                    high_watermark=args.discovery_eligible_high_watermark,
                    cadence_cycles=args.discovery_cadence_cycles,
                )
                if len(current_after_acquisition) >= args.target_new_acquired:
                    decision.update({"run": False, "reason": "TARGET_REACHED_AFTER_ACQUISITION"})
                cycle["discovery_decision"] = decision
                if decision["run"]:
                    discovery_providers = ",".join(decision["providers"])
                    cycle["stages"].append(run_tracked_command(state_path, state, "discovery", [
                        sys.executable, str(scripts / "discover_repositories.py"),
                        "--corpus-root", str(args.corpus_root), "--source-root", str(args.local_source_root),
                        "--providers", discovery_providers,
                        "--budget", str(min(args.discover_budget, args.targeted_discovery_seed_budget)),
                        "--query-budget", str(args.targeted_discovery_query_budget),
                        "--graph-budget", str(args.targeted_discovery_graph_budget),
                        "--request-wall-seconds", str(args.targeted_discovery_request_wall_seconds),
                        "--quota-reserve", str(args.discovery_quota_reserve),
                        "--controller-round-id", args.factory_round_id,
                    ]))
                    refreshed = frontier_inventory(
                        args.corpus_root, selected_providers,
                        quota_reserve=args.discovery_quota_reserve,
                    )
                    cycle["provider_status_after_discovery"] = refreshed
                    current_after_discovery = revision_keys(args.corpus_root) - set(start["revision_keys"])
                    if (
                        len(current_after_discovery) < args.target_new_acquired
                        and int(refreshed.get("acquisition_eligible_frontier", 0)) > 0
                    ):
                        post_remaining = args.target_new_acquired - len(current_after_discovery)
                        post_budget = min(
                            args.post_discovery_acquire_budget,
                            max(1, math.ceil(post_remaining * 1.25)),
                        )
                        cycle["stages"].append(run_tracked_command(
                            state_path, state, "post_discovery_acquisition", [
                                sys.executable, str(scripts / "acquire_frontier.py"),
                                "--corpus-root", str(args.corpus_root), "--providers", args.providers,
                                "--max-repos", str(post_budget),
                                "--max-repo-seconds", str(args.max_repo_seconds),
                                "--controller-round-id", args.factory_round_id,
                                "--round-started-at", str(start["captured_at"]),
                                "--round-revision-target", str(args.target_new_acquired),
                                "--enable-large-repository-lane",
                                "--exploration-fraction", str(args.exploration_fraction),
                                *acquisition_execution_args(args),
                            ],
                        ))
                else:
                    cycle["stages"].append({
                        "stage": "discovery", "state": "SKIPPED",
                        "reason": decision["reason"], "completed_at": utc_now(),
                    })
                after = revision_keys(args.corpus_root) - set(start["revision_keys"])
                gained = len(after) - len(current)
                cycle.update({"completed_at": utc_now(), "after_new_acquired_revisions": len(after), "new_acquired_revisions": gained, "acquisition": acquisition_stats_since(args.corpus_root, attempt_watermark)})
                if gained == 0:
                    cycle["no_progress_category"] = no_progress_category(
                        args.corpus_root, cycle, selected_providers,
                        quota_reserve=args.discovery_quota_reserve,
                    )
                cycle["provider_status_after"] = frontier_inventory(
                    args.corpus_root, selected_providers,
                    quota_reserve=args.discovery_quota_reserve,
                )
                state["cycles"].append(cycle)
                totals = {"attempts": 0, "unique_revision_successes": 0, "failures": 0, "duplicates_or_cache_hits": 0, "states": {}}
                for prior_cycle in state["cycles"]:
                    stats = prior_cycle.get("acquisition", {})
                    for key in ("attempts", "unique_revision_successes", "failures", "duplicates_or_cache_hits"):
                        totals[key] += int(stats.get(key, 0))
                    for key, value in stats.get("states", {}).items():
                        totals["states"][key] = totals["states"].get(key, 0) + int(value)
                state["acquisition_totals"] = totals
                rate_limited_pause = cycle.get("no_progress_category") == "PROVIDER_RATE_LIMIT"
                state["consecutive_no_progress_cycles"] = (
                    0 if gained > 0 or rate_limited_pause
                    else int(state.get("consecutive_no_progress_cycles", 0)) + 1
                )
                state["provider_status"] = cycle["provider_status_after"]
                state.update({"new_acquired_revisions": len(after), "remaining": max(0, args.target_new_acquired - len(after)), "updated_at": utc_now()})
                atomic(state_path, state)
                provider_scoped_empty = (
                    gained == 0
                    and cycle.get("no_progress_category") == "PROVIDER_SCOPED_FRONTIER_EXHAUSTED"
                    and decision["run"]
                    and int(cycle["provider_status_after"].get("acquisition_eligible_frontier", 0)) == 0
                )
                if provider_scoped_empty:
                    backoff = provider_scoped_backoff(
                        cycle["provider_status_after"], args.provider_scoped_refresh_seconds,
                    )
                    jitter = min(max(0, args.provider_backoff_jitter_seconds), 5)
                    state.update({
                        "state": "ACQUIRING_BACKOFF",
                        "backoff_started_at": utc_now(),
                        "backoff_until": backoff["wake_at"],
                        "backoff_seconds": backoff["seconds"] + jitter,
                        "backoff_reason": "HEALTHY_PROVIDER_FRONTIER_EMPTY_AFTER_TARGETED_DISCOVERY",
                        "backoff_wake_reason": backoff["wake_reason"],
                        "next_healthy_provider_discovery_at": backoff["next_healthy_provider_discovery_at"],
                        "updated_at": utc_now(),
                    })
                    atomic(state_path, state)
                    time.sleep(backoff["seconds"] + jitter)
                    state.update({
                        "state": "ACQUIRING_PIPELINED" if args.pipelined_processing else "ACQUIRING",
                        "backoff_completed_at": utc_now(),
                        "updated_at": utc_now(),
                    })
                    atomic(state_path, state)
                    continue
                watchdog = starvation_watchdog(state, args)
                if watchdog:
                    state["state"] = "ACQUIRING_PIPELINED" if args.pipelined_processing else "ACQUIRING"
                    state.setdefault("starvation_events", []).append(watchdog)
                    atomic(state_path, state)
                    watchdog["stages"] = [run_tracked_command(state_path, state, "mid_round_scheduler_recalibration", [
                        sys.executable, str(scripts / "scheduler.py"),
                        "--corpus-root", str(args.corpus_root), "--mid-round-recalibrate",
                        "--factory-round-id", args.factory_round_id,
                        "--round-started-at", str(start["captured_at"]),
                        "--providers", args.providers, "--seed-budget", str(args.discover_budget),
                    ]), run_tracked_command(state_path, state, "fresh_frontier_discovery", [
                        sys.executable, str(scripts / "discover_repositories.py"),
                        "--corpus-root", str(args.corpus_root), "--source-root", str(args.local_source_root),
                        "--providers", args.providers, "--budget", str(args.discover_budget), "--seed-local",
                        "--query-budget", str(args.refresh_query_budget),
                        "--graph-budget", str(args.refresh_graph_budget),
                        "--quota-reserve", str(args.discovery_quota_reserve),
                        "--controller-round-id", args.factory_round_id,
                    ])]
                    watchdog["completed_at"] = utc_now()
                    state["consecutive_no_progress_cycles"] = 0
                    state["updated_at"] = utc_now()
                    atomic(state_path, state)
                if gained == 0 and args.retry_delay_seconds:
                    time.sleep(args.retry_delay_seconds)

            if early_close_evidence and early_close_evidence.get("eligible") and args.pipelined_processing:
                expected_keys = revision_keys(args.corpus_root) - set(start["revision_keys"])
                while True:
                    pipeline_at_close = read_json(pipeline_status_path) if pipeline_status_path else {}
                    connection = sqlite3.connect(args.corpus_root / "state/corpus.sqlite")
                    try:
                        terminal_keys = {
                            str(row[0]) for row in connection.execute(
                                """SELECT repository_revision_key FROM processing_queue
                                   WHERE round_id=? AND state='TERMINAL'""",
                                (args.factory_round_id,),
                            )
                        }
                    finally:
                        connection.close()
                    if terminal_keys == expected_keys:
                        break
                    if int(pipeline_at_close.get("queue_counts", {}).get("BLOCKED", 0)) > 0:
                        state.update({"state": "HARD_FAIL_EARLY_CLOSE_PIPELINE_DRAIN", "updated_at": utc_now()})
                        atomic(state_path, state)
                        return 1
                    state.update({
                        "state": "PRODUCTION_FRONTIER_EXHAUSTED_PENDING_CLOSE",
                        "early_close_processing_terminal": len(terminal_keys),
                        "actual_cohort_size": len(expected_keys),
                        "updated_at": utc_now(),
                    })
                    atomic(state_path, state)
                    time.sleep(max(0.25, args.pipeline_poll_seconds))
            pipeline_at_lock = read_json(pipeline_status_path) if pipeline_status_path else {}
            terminal_at_lock = int(pipeline_at_lock.get("queue_counts", {}).get("TERMINAL", 0))
            drain_started = dt.datetime.now(dt.timezone.utc)
            cohort = lock_acquisition_cohort(
                args.corpus_root, start, args.target_new_acquired,
                early_close_evidence=early_close_evidence,
            )
            cohort_path = cohort_lock_path(args.corpus_root, args.factory_round_id)
            cohort_file_hash = file_sha256(cohort_path)
            cohort_receipt_path = round_dir / "cohort_lock.admission.json"
            if not cohort_receipt_path.is_file():
                atomic(cohort_receipt_path, {
                    "schema": "rtl_immutable_artifact_admission_v1",
                    "object_id": "cohort_lock.json",
                    "path": str(cohort_path.resolve()),
                    "sha256": cohort_file_hash,
                    "size": cohort_path.stat().st_size,
                    "producer": "lock_acquisition_cohort",
                    "recorded_at": utc_now(),
                    "rehash_required": False,
                })
            saved_hash = state.get("cohort_lock_sha256")
            if saved_hash and saved_hash != cohort_file_hash:
                state.update({"state": "HARD_FAIL_COHORT_LOCK_HASH_CHANGED", "updated_at": utc_now()})
                atomic(state_path, state)
                return 1
            state.update({"state": "COHORT_LOCKED_DRAINING" if args.pipelined_processing else "PROCESSING", "cohort_locked": True, "cohort_revision_count": cohort["acquired_revision_count"], "cohort_revision_keys_sha256": cohort["revision_keys_sha256"], "cohort_lock_sha256": cohort_file_hash, "processing_terminal_before_lock": terminal_at_lock, "processing_remaining_at_lock": max(0, int(cohort["acquired_revision_count"]) - terminal_at_lock), "updated_at": utc_now()})
            atomic(state_path, state)
            if args.pipelined_processing:
                while True:
                    pipeline_status = read_json(pipeline_status_path) if pipeline_status_path else {}
                    state["pipeline_status"] = pipeline_status
                    blocked = int(pipeline_status.get("queue_counts", {}).get("BLOCKED", 0))
                    if pipeline_status.get("state") == "DRAINED" and pipeline_status.get("terminal_set_matches_cohort") is True:
                        break
                    if blocked and int(pipeline_status.get("active_workers", 0)) == 0:
                        state.update({"state": "HARD_FAIL_PIPELINE_DRAIN", "pipeline_blocked_revisions": blocked, "updated_at": utc_now()})
                        atomic(state_path, state)
                        return 1
                    if pipeline_process is None or pipeline_process.poll() is not None:
                        stop_processing_pipeline(pipeline_process, pipeline_stdout, pipeline_stderr)
                        pipeline_process, pipeline_stdout, pipeline_stderr, pipeline_status_path = (
                            start_processing_pipeline(args, scripts, round_dir)
                        )
                        state["pipeline_manager_pid"] = pipeline_process.pid
                    state["controller_heartbeat_at"] = utc_now()
                    state["updated_at"] = utc_now()
                    atomic(state_path, state)
                    time.sleep(max(1.0, args.pipeline_poll_seconds))
                drained = read_json(pipeline_status_path) if pipeline_status_path else {}
                drain_seconds = max(0.0, (dt.datetime.now(dt.timezone.utc) - drain_started).total_seconds())
                state["pipeline_metrics"] = {
                    "schema": "rtl_pipelined_processing_metrics_v1",
                    "acquired_revision_count": int(cohort["acquired_revision_count"]),
                    "processing_started_before_lock": int(pipeline_at_lock.get("processing_started_count", 0)),
                    "processing_terminal_before_lock": terminal_at_lock,
                    "processing_remaining_at_lock": max(0, int(cohort["acquired_revision_count"]) - terminal_at_lock),
                    "terminal_before_lock_fraction": round(terminal_at_lock / max(1, int(cohort["acquired_revision_count"])), 6),
                    "post_lock_drain_hours": round(drain_seconds / 3600.0, 6),
                    "pipeline_overlap_hours": float(drained.get("pipeline_overlap_hours", 0.0)),
                    "processing_idle_due_to_acquisition_seconds": float(
                        drained.get("processing_idle_due_to_acquisition_seconds", 0.0)
                    ),
                    "acquisition_idle_due_to_processing_seconds": float(
                        drained.get("acquisition_idle_due_to_processing_seconds", 0.0)
                    ),
                    "processing_queue_peak": int(drained.get("processing_queue_peak", 0)),
                    "processing_queue_p50_seconds": float(drained.get("processing_queue_p50_seconds", 0.0)),
                    "processing_queue_p95_seconds": float(drained.get("processing_queue_p95_seconds", 0.0)),
                    "terminal_set_matches_cohort": True,
                }
                state["state"] = "RECONCILING"
                state["reconciliation_started_at"] = utc_now()
                state["updated_at"] = utc_now()
                atomic(state_path, state)
                staged_audit_stage = run_tracked_command(
                    state_path, state, "staged_closure_audit_final_verification", [
                        sys.executable, str(scripts / "staged_closure_audit.py"),
                        "--corpus-root", str(args.corpus_root),
                        "--round-id", args.factory_round_id,
                        "--output", str(round_dir / "staged_split_audit.json"),
                    ],
                )
                state["staged_closure_audit"] = staged_audit_stage
                if staged_audit_stage["state"] != "PASS":
                    state.update({"state": "HARD_FAIL_STAGED_CLOSURE_AUDIT", "updated_at": utc_now()})
                    atomic(state_path, state)
                    return 1
                if (
                    not (round_dir / "split_reconciliation_plan.json").is_file()
                    and batch_sequence(args.factory_round_id)
                    >= args.auto_reconcile_train_val_activation_batch
                ):
                    staged_plan = write_plan_from_staged_audit(
                        args.corpus_root, round_dir, args.factory_round_id, cohort,
                    )
                    if staged_plan:
                        state.update({
                            "split_reconciliation_plan": str(staged_plan),
                            "split_reconciliation_state": "PREBUILT_FROM_STAGED_AUDIT",
                            "updated_at": utc_now(),
                        })
                        atomic(state_path, state)
            final_command = [
                sys.executable, str(scripts / "run_factory_round.py"),
                "--corpus-root", str(args.corpus_root), "--local-source-root", str(args.local_source_root),
                "--providers", args.providers, "--factory-round-id", args.factory_round_id,
                "--skip-discovery", "--skip-acquisition", "--skip-local-processing", "--process-all-acquired",
                "--process-budget", str(args.process_budget), "--max-repo-seconds", str(args.max_repo_seconds),
                "--r1-budget", str(args.r1_budget),
            ]
            if incremental_finalization_enabled(args.factory_round_id):
                final_command.extend([
                    "--incremental-finalization",
                    "--cohort-lock", str(cohort_path),
                    "--incremental-shadow-compare",
                ])
                state["finalization_contract"] = "incremental_finalization_v1"
                state["legacy_full_materialization_shadow"] = "STATE_MANIFEST_SEMANTIC_COMPARE"
            else:
                state["finalization_contract"] = "legacy_full_materialization_v1"
            reconciliation_plan = round_dir / "split_reconciliation_plan.json"
            if reconciliation_plan.is_file():
                final_command.extend(["--split-reconciliation-plan", str(reconciliation_plan)])
                state["split_reconciliation_plan"] = str(reconciliation_plan)
                state["split_reconciliation_state"] = "APPLYING"
                atomic(state_path, state)
            state["state"] = "FINALIZING"
            state["finalization_started_at"] = utc_now()
            atomic(state_path, state)
            final_stage = run_tracked_command(state_path, state, "factory_completion", final_command)
            if state.get("pipeline_metrics"):
                final_started = dt.datetime.fromisoformat(
                    str(state["finalization_started_at"]).replace("Z", "+00:00")
                )
                state["pipeline_metrics"]["finalization_hours"] = round(
                    max(0.0, (dt.datetime.now(dt.timezone.utc) - final_started).total_seconds())
                    / 3600.0,
                    6,
                )
            state["factory_completion"] = final_stage
            if (
                final_stage["state"] != "PASS"
                and not reconciliation_plan.is_file()
                and batch_sequence(args.factory_round_id) >= args.auto_reconcile_train_val_activation_batch
            ):
                conflict = deterministic_split_conflict(final_stage)
                if conflict:
                    if set(conflict["old_splits"]) == {"train", "val"}:
                        reconciliation_plan = write_auto_train_val_plan(
                            args.corpus_root, round_dir, args.factory_round_id, cohort,
                            list(conflict["old_split_groups"]),
                        )
                        auto_policy = "AUTO_RECONCILE_TRAIN_VAL_V1"
                    elif "test" in set(conflict["old_splits"]):
                        reconciliation_plan = write_auto_profile_rollover_plan(
                            args.corpus_root, round_dir, args.factory_round_id, cohort,
                            [conflict],
                        )
                        auto_policy = "AUTO_SPLIT_PROFILE_ROLLOVER_V1"
                    else:
                        reconciliation_plan = None
                        auto_policy = "HARD_STOP_UNKNOWN_SPLIT_CONFLICT"
                if conflict and reconciliation_plan is not None:
                    state.update({
                        "state": "AUTO_RECONCILING",
                        "split_reconciliation_plan": str(reconciliation_plan),
                        "split_reconciliation_state": "AUTO_RECONCILING",
                        "auto_reconciliation_policy": auto_policy,
                        "updated_at": utc_now(),
                    })
                    atomic(state_path, state)
                    retry_command = [
                        *final_command,
                        "--split-reconciliation-plan", str(reconciliation_plan),
                    ]
                    state["state"] = "FINALIZING"
                    state["updated_at"] = utc_now()
                    atomic(state_path, state)
                    final_stage = run_tracked_command(
                        state_path, state, "factory_completion_after_auto_reconciliation",
                        retry_command,
                    )
                    state["factory_completion"] = final_stage
            report_path = round_dir / "phase2_round_delta_summary.json"
            completion_path = args.corpus_root / "runs/factory" / f"{args.factory_round_id}.json"
            report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {}
            completion = json.loads(completion_path.read_text(encoding="utf-8")) if completion_path.is_file() else {}
            complete = (
                final_stage["state"] == "PASS"
                and completion_is_final(completion)
                and report.get("yield_status") == "FINAL"
                and report.get("acquisition_cohort", {}).get("new_acquired_revisions") == cohort["acquired_revision_count"]
                and report.get("acquisition_cohort", {}).get("processing_coverage") == 1.0
            )
            state.update({"state": "COMPLETE" if complete else "FAILED_FINALIZATION", "completed_at": utc_now() if complete else None, "updated_at": utc_now(), "final_report": str(report_path), "factory_manifest": str(completion_path)})
            if complete and reconciliation_plan.is_file():
                state["split_reconciliation_state"] = "APPLIED"
            atomic(state_path, state)
            if complete:
                dashboard_stage = run_tracked_command(state_path, state, "post_completion_phase2_dashboard", [
                    sys.executable, str(scripts / "summarize_phase2.py"),
                    "--corpus-root", str(args.corpus_root),
                ])
                state["post_completion_phase2_dashboard"] = dashboard_stage
                if dashboard_stage["state"] != "PASS":
                    complete = False
                    state.update({"state": "FAILED_FINALIZATION", "completed_at": None})
                state["updated_at"] = utc_now()
                atomic(state_path, state)
            print(json.dumps(state, indent=2, sort_keys=True))
            return 0 if complete else 1
        except KeyboardInterrupt:
            state.update({"state": "INTERRUPTED_RECOVERABLE", "updated_at": utc_now()})
            atomic(state_path, state)
            raise
        finally:
            stop_processing_pipeline(pipeline_process, pipeline_stdout, pipeline_stderr)


if __name__ == "__main__":
    raise SystemExit(main())
