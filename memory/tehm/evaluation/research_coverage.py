"""Read-only memory route preflight over audited real-design fixed flows.

This is a coverage diagnostic before Controlled Action Pilot, not an action
experiment.  Queries are derived from independently audited terminal failures;
passing or unknown flows remain in the registered denominator without being
turned into artificial repair tasks.  No asset is executed and M0 is immutable.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from contracts import RepairContext
from tehm import db
from tehm.evaluation.research_epoch import verify_research_epoch
from tehm.evaluation.research_flow import verify_flow_audit
from tehm.retrieval.memory_router import route_memory
from tehm.retrieval.query_planner import plan_query


COVERAGE_SCHEMA = "tehm-research-memory-route-coverage-v1"


class ResearchCoverageError(ValueError):
    """Frozen coverage inputs cannot be audited safely."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _load(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResearchCoverageError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise ResearchCoverageError(f"{label} must be an object")
    return value


def _verify_code_binding(epoch: Path) -> None:
    """Require the executing coverage and routing code to match frozen bytes."""
    binding = _load(epoch / "bindings/oracle-binding.json", "oracle binding")
    files = binding.get("files")
    if not isinstance(files, list):
        raise ResearchCoverageError("oracle binding files are malformed")
    memory_root = Path(__file__).resolve().parents[2]
    critical = (
        Path(__file__).resolve(),
        memory_root / "tehm/retrieval/memory_router.py",
        memory_root / "tehm/retrieval/query_planner.py",
        memory_root / "tehm/state/resolver.py",
        memory_root / "tehm/state/schema.py",
        memory_root / "tehm/db.py",
    )
    indexed: dict[Path, str] = {}
    for item in files:
        if not isinstance(item, dict):
            raise ResearchCoverageError("oracle binding entry is malformed")
        source = Path(str(item.get("source_path") or "")).resolve()
        if source in indexed:
            raise ResearchCoverageError("oracle binding source is duplicated")
        indexed[source] = str(item.get("sha256") or "")
    for source in critical:
        if indexed.get(source) != _file_digest(source):
            raise ResearchCoverageError(
                f"executing coverage code differs from frozen oracle: {source}")


def _report(*, epoch: Path, flow_audits: Sequence[Path]) -> dict[str, Any]:
    if not flow_audits:
        raise ResearchCoverageError("at least one flow audit is required")
    if len(flow_audits) != len(set(flow_audits)):
        raise ResearchCoverageError("flow audit paths must be distinct")
    checked_epoch = verify_research_epoch(epoch)
    if (not checked_epoch.get("valid") or
            not checked_epoch.get("research_evaluation_ready")):
        raise ResearchCoverageError("research epoch is not evaluation-ready")
    _verify_code_binding(epoch)
    frozen = _load(epoch / "research-epoch.json", "research epoch")
    authority = frozen.get("authority") or {}
    if (authority.get("research_only") is not True or
            authority.get("online_memory_update") is not False or
            authority.get("production_authority") is not False):
        raise ResearchCoverageError("research epoch authority is not read-only")
    snapshot = Path(str((frozen.get("memory_snapshot") or {}).get("bundle_path") or ""))
    database = snapshot / "closed_loop" / "tehm.sqlite"
    if not database.is_file():
        raise ResearchCoverageError("frozen M0 database is missing")
    before = _file_digest(database)
    budget = frozen.get("budget") or {}
    budget_path = epoch / str(budget.get("frozen_path") or "")
    frozen_budget = _load(budget_path, "frozen budget")
    candidate_limit = frozen_budget.get("candidate_limit")
    if type(candidate_limit) is not int or candidate_limit < 1:
        raise ResearchCoverageError("frozen candidate budget is invalid")
    memory_slots = 1 if candidate_limit >= 2 else 0
    no_memory_slots = candidate_limit - memory_slots

    rows = []
    seen_designs: set[str] = set()
    connection = db.connect_read_only(database)
    try:
        for root in flow_audits:
            checked = verify_flow_audit(root)
            if not checked.get("valid"):
                raise ResearchCoverageError(f"flow audit is invalid: {root}")
            audit = _load(root / "flow-audit.json", "flow audit")
            design_id = audit.get("design_id")
            if type(design_id) is not str or not design_id:
                raise ResearchCoverageError("flow audit design ID is invalid")
            if design_id in seen_designs:
                raise ResearchCoverageError(f"duplicate design in coverage: {design_id}")
            seen_designs.add(design_id)
            if (audit.get("source_mutation") != "none" or
                    audit.get("memory_update") != "none" or
                    audit.get("toolchain_manifest_digest") !=
                    checked_epoch["toolchain_manifest_digest"]):
                raise ResearchCoverageError("flow audit authority is incompatible")
            project = Path(str(audit.get("project_path") or ""))
            staged = _load(project / "stage-receipt.json", "stage receipt")
            if (staged.get("receipt_digest") != audit.get("stage_receipt_digest") or
                    staged.get("design_id") != design_id):
                raise ResearchCoverageError("flow audit stage binding drifted")
            platform = staged.get("platform")
            if type(platform) is not str or not platform:
                raise ResearchCoverageError("staged platform is missing")
            row: dict[str, Any] = {
                "design_id": design_id,
                "flow_audit_path": str(root),
                "flow_audit_digest": checked["audit_digest"],
                "flow_verdict": checked["oracle_verdict"],
                "flow_reason": checked["oracle_reason"],
                "terminal_stage": checked["terminal_stage"],
                "platform": platform,
                "source_bundle_digest": staged["source_bundle_digest"],
                "route_status": "NOT_APPLICABLE_NO_TARGET_FAILURE",
                "query": None,
                "routing_decision": None,
            }
            if (checked["oracle_verdict"] == "FAIL" and
                    checked["failure_layer"] == "FLOW_TARGET_FAILURE"):
                check = checked["terminal_stage"]
                summary = {
                    "audit_digest": checked["audit_digest"],
                    "oracle_verdict": checked["oracle_verdict"],
                    "oracle_reason": checked["oracle_reason"],
                    "failure_layer": checked["failure_layer"],
                    "terminal_stage": check,
                }
                context = RepairContext(
                    design_id=design_id, platform=platform, check=check,
                    reports={"fixed_flow_audit": summary},
                    symptom_signature={
                        "check": check,
                        "reason": checked["oracle_reason"],
                        "failure_layer": checked["failure_layer"],
                    },
                )
                query = plan_query(context)
                decision = route_memory(
                    connection, query, no_memory_budget=no_memory_slots,
                    memory_budget=memory_slots, mode="shadow",
                    persist_state=False, commit=False,
                )
                row["route_status"] = decision.decision
                row["query"] = query.to_dict()
                row["routing_decision"] = {
                    **decision.to_dict(),
                    "decision_digest": decision.decision_digest,
                }
            elif checked["oracle_verdict"] != "PASS":
                row["route_status"] = "NOT_ROUTABLE_UNVERIFIED_FLOW"
            rows.append(row)
    finally:
        connection.close()
    after = _file_digest(database)
    if before != after:
        raise ResearchCoverageError("frozen M0 changed during coverage audit")
    if any(database.with_name(database.name + suffix).exists()
           for suffix in ("-wal", "-shm")):
        raise ResearchCoverageError("frozen M0 gained SQLite sidecar files")
    post_epoch = verify_research_epoch(epoch)
    if (not post_epoch.get("valid") or
            post_epoch.get("epoch_digest") != checked_epoch["epoch_digest"]):
        raise ResearchCoverageError("research epoch drifted during coverage audit")
    routable = [row for row in rows if row["query"] is not None]
    payload = {
        "schema": COVERAGE_SCHEMA,
        "epoch_id": frozen["epoch_id"],
        "epoch_digest": checked_epoch["epoch_digest"],
        "epoch_path": str(epoch),
        "memory_bundle_digest": checked_epoch["memory_bundle_digest"],
        "memory_db_sha256_before": before,
        "memory_db_sha256_after": after,
        "budget": {
            "candidate_limit": candidate_limit,
            "no_memory_slots": no_memory_slots,
            "memory_advisor_slots": memory_slots,
            "eda_calls": 0,
            "model_calls": 0,
        },
        "denominator": {
            "registered_flows": len(rows),
            "routable_terminal_failures": len(routable),
            "route_selected": sum(row["route_status"] in {"APPLY", "CONSIDER"}
                                  for row in routable),
            "no_match": sum(row["route_status"] == "NO_SKILL" and
                            row["routing_decision"]["no_skill_reason"] == "NO_MATCH"
                            for row in routable),
            "abstained": sum(row["route_status"] == "ABSTAIN" for row in routable),
            "no_target_failure": sum(row["route_status"] ==
                                     "NOT_APPLICABLE_NO_TARGET_FAILURE" for row in rows),
            "unverified_flow": sum(row["route_status"] ==
                                   "NOT_ROUTABLE_UNVERIFIED_FLOW" for row in rows),
        },
        "rows": rows,
        "authority": {
            "coverage_only": True,
            "action_selected_or_executed": False,
            "memory_update": "none",
            "production_authority": False,
        },
        "claim_boundary": (
            "Audited read-only route coverage only; no asset selection, runtime binding, "
            "candidate execution, action effect, agent comparison, or production claim."
        ),
    }
    payload["coverage_digest"] = _digest(payload)
    return payload


def audit_memory_route_coverage(
    *, epoch: str | Path, flow_audits: Sequence[str | Path], output: str | Path,
) -> dict[str, Any]:
    """Record a non-overwriting, replayable route-only diagnostic."""
    destination = Path(output).expanduser().resolve()
    if destination.exists():
        raise ResearchCoverageError(f"refusing to overwrite coverage: {destination}")
    epoch_root = Path(epoch).expanduser().resolve()
    roots = [Path(item).expanduser().resolve() for item in flow_audits]
    payload = _report(epoch=epoch_root, flow_audits=roots)
    destination.mkdir(parents=True)
    temporary = destination / f"coverage.json.tmp.{os.getpid()}"
    temporary.write_bytes(_canonical(payload) + b"\n")
    temporary.replace(destination / "coverage.json")
    return {"valid": True, "coverage_digest": payload["coverage_digest"],
            "denominator": payload["denominator"], "output": str(destination)}


def verify_memory_route_coverage(coverage: str | Path) -> dict[str, Any]:
    """Recompute routes against the same frozen M0 and raw flow audits."""
    root = Path(coverage).expanduser().resolve()
    saved = _load(root / "coverage.json", "coverage report")
    if saved.get("schema") != COVERAGE_SCHEMA:
        raise ResearchCoverageError("coverage schema mismatch")
    digest = saved.get("coverage_digest")
    if digest != _digest({key: value for key, value in saved.items()
                          if key != "coverage_digest"}):
        raise ResearchCoverageError("coverage digest mismatch")
    rows = saved.get("rows")
    if not isinstance(rows, list):
        raise ResearchCoverageError("coverage rows are malformed")
    paths = [row.get("flow_audit_path") for row in rows if isinstance(row, dict)]
    if len(paths) != len(rows) or any(type(path) is not str for path in paths):
        raise ResearchCoverageError("coverage flow paths are malformed")
    fresh = _report(
        epoch=Path(str(saved.get("epoch_path") or "")).expanduser().resolve(),
        flow_audits=[Path(path).expanduser().resolve() for path in paths],
    )
    if fresh != saved:
        raise ResearchCoverageError("coverage replay diverged")
    return {"valid": True, "coverage_digest": digest,
            "denominator": saved["denominator"], "output": str(root)}


__all__ = [
    "COVERAGE_SCHEMA", "ResearchCoverageError", "audit_memory_route_coverage",
    "verify_memory_route_coverage",
]
