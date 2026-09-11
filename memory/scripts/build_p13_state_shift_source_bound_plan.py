#!/usr/bin/env python3
"""Bind an admitted StateShift plan to a deterministic source snapshot.

The source snapshot is opened read-only and copied to RAM for state replay.
If the historical plan resolution differs, an explicit typed rebase receipt
derives a new shadow-only plan.  No anti-forgetting witness is fabricated and
no shadow update, canonical write, lifecycle change, or production import is
performed here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tehm.evolution import (  # noqa: E402
    LocalizedUpdatePlan,
    StateResolutionRebaseError,
    rebase_localized_update_plan,
    state_semantic_digest,
)
from tehm.ids import stable_dumps  # noqa: E402
from tehm.state import resolve_current_state  # noqa: E402


REPORT_VERSION = "p13-state-shift-source-bound-plan-v1"
SOURCE_REPORT_VERSION = "p13-state-shift-source-snapshot-v1"
PLAN_REPORT_VERSION = "p13-state-shift-plan-report-v1"


class P13StateShiftSourceBoundPlanError(ValueError):
    """A source snapshot cannot authorize a rebased shadow plan."""


def _digest(payload: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(payload).encode()).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _load(path: Path, name: str) -> dict:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise P13StateShiftSourceBoundPlanError(
            f"cannot read {name}: {path}") from exc
    if not isinstance(payload, dict):
        raise P13StateShiftSourceBoundPlanError(f"{name} must be a JSON object")
    return payload


def _content_digest(payload: Mapping, field: str, name: str) -> str:
    supplied = payload.get(field)
    if type(supplied) is not str or not supplied.startswith("sha256:"):
        raise P13StateShiftSourceBoundPlanError(f"{name} requires {field}")
    replay = dict(payload)
    replay.pop(field, None)
    if supplied != _digest(replay):
        raise P13StateShiftSourceBoundPlanError(f"{name} {field} mismatch")
    return supplied


def _bound_path(raw: object, *, relative_to: Path, name: str) -> Path:
    if type(raw) is not str or not raw.strip():
        raise P13StateShiftSourceBoundPlanError(f"{name} path is missing")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = relative_to.parent / path
    path = path.resolve()
    if not path.is_file():
        raise P13StateShiftSourceBoundPlanError(f"{name} is not a file")
    return path


def _logical_digest(conn: sqlite3.Connection) -> str:
    return _digest("\n".join(conn.iterdump()))


def _source_state(
    source_db: Path,
    *,
    scope: Mapping,
    expected_logical_digest: str,
):
    frozen = sqlite3.connect(f"file:{source_db}?mode=ro", uri=True)
    frozen.row_factory = sqlite3.Row
    ram = sqlite3.connect(":memory:")
    ram.row_factory = sqlite3.Row
    try:
        frozen.backup(ram)
    finally:
        frozen.close()
    try:
        if _logical_digest(ram) != expected_logical_digest:
            raise P13StateShiftSourceBoundPlanError(
                "source database logical digest mismatch")
        state = resolve_current_state(
            ram, scope, mode="shadow", persist=False)
        return state, ram
    except Exception:
        ram.close()
        raise


def _training_memberships(
    conn: sqlite3.Connection,
    transition_ids: Sequence[str],
    campaign_id: str,
) -> dict[str, str]:
    if (not isinstance(transition_ids, Sequence) or
            isinstance(transition_ids, (str, bytes)) or not transition_ids or
            len(set(transition_ids)) != len(transition_ids)):
        raise P13StateShiftSourceBoundPlanError(
            "source-bound plan transition IDs are malformed")
    result = {}
    for transition_id in transition_ids:
        if type(transition_id) is not str or not transition_id:
            raise P13StateShiftSourceBoundPlanError(
                "source-bound plan transition ID is invalid")
        row = conn.execute(
            """SELECT split, learner_eligible
                 FROM tehm_dataset_membership
                WHERE transition_id=? AND campaign_id=?""",
            (transition_id, campaign_id)).fetchone()
        if row is None or row["split"] != "training" or row[
                "learner_eligible"] != 1:
            raise P13StateShiftSourceBoundPlanError(
                "source transition lacks learner-eligible training membership")
        result[transition_id] = campaign_id
    return dict(sorted(result.items()))


def build_p13_state_shift_source_bound_plan(
    source_snapshot_report: Path | str,
    *,
    output: Path | str,
) -> dict:
    """Produce a source-bound plan while leaving execution gates closed."""
    source_path = Path(source_snapshot_report).expanduser().resolve()
    output_path = Path(output).expanduser().resolve()
    if output_path == source_path:
        raise P13StateShiftSourceBoundPlanError(
            "source-bound plan output must be separate from source report")
    source = _load(source_path, "source snapshot report")
    if source.get("version") != SOURCE_REPORT_VERSION:
        raise P13StateShiftSourceBoundPlanError(
            "source snapshot report version mismatch")
    source_report_digest = _content_digest(
        source, "report_digest", "source snapshot report")
    if (source.get("source_database_present") is not True or
            source.get("source_database_mutated_after_freeze") is not False or
            source.get("scoped_replay_required_for_shadow") is not True or
            source.get("anti_forgetting_present") is not False or
            source.get("shadow_update_attempted") is not False or
            source.get("shadow_execution_ready") is not False or
            source.get("evaluation_only") is not True or
            source.get("canonical_memory_mutation") != "none" or
            source.get("production_runtime_imported") is not False or
            source.get("production_integration") != "not_attempted" or
            source.get("memory_docs_submitted") is not False):
        raise P13StateShiftSourceBoundPlanError(
            "source snapshot crossed a pre-execution authority boundary")

    plan_binding = source.get("plan_report")
    if not isinstance(plan_binding, Mapping):
        raise P13StateShiftSourceBoundPlanError(
            "source snapshot lacks plan report binding")
    plan_path = _bound_path(
        plan_binding.get("path"), relative_to=source_path,
        name="StateShift plan report")
    if plan_binding.get("sha256") != _sha256(plan_path):
        raise P13StateShiftSourceBoundPlanError(
            "source snapshot plan file binding mismatch")
    plan_report = _load(plan_path, "StateShift plan report")
    if plan_report.get("version") != PLAN_REPORT_VERSION:
        raise P13StateShiftSourceBoundPlanError("plan report version mismatch")
    plan_report_digest = _content_digest(
        plan_report, "report_digest", "StateShift plan report")
    try:
        admitted_plan = LocalizedUpdatePlan.from_dict(
            plan_report.get("localized_update_plan"))
    except (TypeError, ValueError) as exc:
        raise P13StateShiftSourceBoundPlanError(
            f"admitted plan is invalid: {exc}") from exc
    if (plan_binding.get("report_digest") != plan_report_digest or
            plan_binding.get("plan_digest") != admitted_plan.plan_digest or
            plan_report.get("localized_update_plan", {}).get(
                "plan_digest") != admitted_plan.plan_digest):
        raise P13StateShiftSourceBoundPlanError(
            "source snapshot plan content binding mismatch")

    database = source.get("source_database")
    if not isinstance(database, Mapping):
        raise P13StateShiftSourceBoundPlanError(
            "source snapshot lacks database binding")
    database_path = _bound_path(
        database.get("path"), relative_to=source_path,
        name="source database")
    if (database.get("sha256") != _sha256(database_path) or
            database.get("sidecar_free") is not True):
        raise P13StateShiftSourceBoundPlanError(
            "source database file binding mismatch")
    replay = source.get("replay")
    expected_state = replay.get("resolved_source_state") if isinstance(
        replay, Mapping) else None
    if not isinstance(expected_state, Mapping):
        raise P13StateShiftSourceBoundPlanError(
            "source snapshot lacks resolved source state")
    scope = expected_state.get("scope")
    if not isinstance(scope, Mapping):
        raise P13StateShiftSourceBoundPlanError(
            "source snapshot state scope is malformed")
    state, conn = _source_state(
        database_path, scope=scope,
        expected_logical_digest=database.get("logical_digest"))
    try:
        if stable_dumps(state.to_dict()) != stable_dumps(expected_state):
            raise P13StateShiftSourceBoundPlanError(
                "source state replay differs from snapshot report")
        semantic_digest = state_semantic_digest(state)
        if semantic_digest != replay.get("resolved_source_semantic_digest"):
            raise P13StateShiftSourceBoundPlanError(
                "source state semantic digest mismatch")
        campaign = source.get("campaign_binding")
        training_campaign = campaign.get(
            "training_evidence_campaign_id") if isinstance(
                campaign, Mapping) else None
        if (type(training_campaign) is not str or not training_campaign or
                campaign.get("training_membership_relabelled") is not False):
            raise P13StateShiftSourceBoundPlanError(
                "source training campaign binding is invalid")
        transition_ids = plan_report.get("proposal_transition_ids")
        if not set(transition_ids or ()) <= set(replay.get("transition_ids") or ()):
            raise P13StateShiftSourceBoundPlanError(
                "planned transitions are absent from source replay")
        transition_campaigns = _training_memberships(
            conn, transition_ids, training_campaign)
        state_binding = source.get("state_binding")
        if (not isinstance(state_binding, Mapping) or
                state_binding.get("source_resolution_id") != state.resolution_id or
                state_binding.get("source_semantic_digest") != semantic_digest or
                state_binding.get("resolution_rebase_required") is not True or
                state_binding.get("resolution_rebase_authorized") is not False or
                state_binding.get("plan_resolution_id") !=
                admitted_plan.state_resolution_id):
            raise P13StateShiftSourceBoundPlanError(
                "source state binding is not eligible for explicit rebase")
        rebase, rebased_plan = rebase_localized_update_plan(
            admitted_plan, state,
            source_database_digest=database["logical_digest"],
            evidence_refs=(
                admitted_plan.plan_digest,
                source_report_digest,
                database["sha256"],
                database["logical_digest"],
            ),
        )
    finally:
        conn.close()

    report = {
        "version": REPORT_VERSION,
        "campaign_id": admitted_plan.campaign_id,
        "source_snapshot_report": {
            "path": str(source_path),
            "sha256": _sha256(source_path),
            "report_digest": source_report_digest,
        },
        "admitted_plan_report": {
            "path": str(plan_path),
            "sha256": _sha256(plan_path),
            "report_digest": plan_report_digest,
            "plan_digest": admitted_plan.plan_digest,
        },
        "source_database": {
            "path": str(database_path),
            "sha256": database["sha256"],
            "logical_digest": database["logical_digest"],
            "opened_read_only": True,
        },
        "resolution_rebase": rebase.to_dict(),
        "source_bound_localized_update_plan": {
            **rebased_plan.to_dict(), "plan_digest": rebased_plan.plan_digest,
        },
        "transition_campaigns": transition_campaigns,
        "cross_campaign_training_membership_verified": True,
        "training_membership_relabelled": False,
        "source_resolution_rebase_authorized_for_shadow": True,
        "eligible_for_anti_forgetting": True,
        "scoped_verified_execution_replay_pending": True,
        "anti_forgetting_present": False,
        "shadow_update_attempted": False,
        "shadow_execution_ready": False,
        "remaining_gates": [
            "scoped_training_execution_replay",
            "anti_forgetting_witness",
        ],
        "evaluation_only": True,
        "canonical_memory_mutation": "none",
        "production_runtime_imported": False,
        "production_integration": "not_attempted",
        "memory_docs_submitted": False,
    }
    report["report_digest"] = _digest(report)
    if output_path.exists():
        raise P13StateShiftSourceBoundPlanError(
            f"immutable source-bound plan already exists: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x") as stream:
        stream.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-snapshot-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = build_p13_state_shift_source_bound_plan(
            args.source_snapshot_report, output=args.output)
    except (OSError, sqlite3.Error, StateResolutionRebaseError,
            P13StateShiftSourceBoundPlanError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    plan = report["source_bound_localized_update_plan"]
    print(json.dumps({
        "output": str(args.output.expanduser().resolve()),
        "campaign_id": report["campaign_id"],
        "admitted_plan_digest": report[
            "admitted_plan_report"]["plan_digest"],
        "source_bound_plan_digest": plan["plan_digest"],
        "source_resolution_id": plan["state_resolution_id"],
        "rebase_receipt_digest": report[
            "resolution_rebase"]["receipt_digest"],
        "eligible_for_anti_forgetting": report[
            "eligible_for_anti_forgetting"],
        "shadow_update_attempted": report["shadow_update_attempted"],
        "canonical_memory_mutation": report["canonical_memory_mutation"],
        "report_digest": report["report_digest"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
