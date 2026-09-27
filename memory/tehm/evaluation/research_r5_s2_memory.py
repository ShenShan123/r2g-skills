"""C4 DEV public-context mapping over real frozen backends; not an Agent.

Caller owns read-only mounts, provenance pins and raw TRAIN replay dependencies.
Returned private receipts MUST NOT be placed in an Agent filesystem. This module
does not execute actions, authorize models, train Memory or certify held-out data.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sqlite3

POLICIES = ("no_persistent_memory", "legacy_memory", "tehm")
PROFILE = "rtl.skid.temp_payload.v7.dev"
FAMILY = "SKID_TEMP_PAYLOAD_RESTORE"
MEASUREMENT = "sha256:4f523241ef283a11947f6e2f671e171f66598979cbc66a6fd907ef1ae39248b6"
BUNDLE = "d4f12cf6c961854f9c359b2ab09f6b6e1cd0a983e5edcbdd7cb11a759d1860a3"
SOURCE = "caae468c6ed9f94e45958f7033d0abee3fa5f576d0279d61de1a0a2bf384c3db"
LEGACY_FILES = {
    "heuristics.json": "cfd39ec8120cce14bf0b5149695b17afae880170bab1f5f47c6777eaac1166d0",
    "schema.sql": "3f46697d2b23fdd62ab467214c8d66455ef48b265755461dd6259768e0ba9bd2",
    "knowledge.sqlite": "e5b46fe49c289b609c39df077be4cfcf444366767f54edc75686cc50f1d449f0",
}


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                   allow_nan=False).encode()).hexdigest()


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def public_task(source: str, parameters: dict, feedback: dict) -> dict:
    """Strictly scoped DEV mapping; reject extra fields, not just suspicious words."""
    if hashlib.sha256(source.encode()).hexdigest() != SOURCE:
        raise ValueError("C4 source is not the registered DEV input")
    if (type(parameters) is not dict or set(parameters) != {"DATA_WIDTH", "SKID_SLOTS"}
            or any(type(v) is not int for v in parameters.values())
            or parameters != {"DATA_WIDTH": 8, "SKID_SLOTS": 1}):
        raise ValueError("C4 public parameter scope mismatch")
    if type(feedback) is not dict or set(feedback) != {
            "schema", "obligations", "stop", "task_verdict"}:
        raise ValueError("unexpected feedback fields")
    obligations = feedback["obligations"]
    if (type(obligations) is not dict or set(obligations) != {"target", "preservation", "native"}
            or any(type(v) is not str or v not in {"PASS", "FAIL", "UNKNOWN"}
                   for v in obligations.values())):
        raise ValueError("invalid obligation enums")
    values = set(obligations.values())
    verdict = "FAIL" if "FAIL" in values else "UNKNOWN" if "UNKNOWN" in values else "PASS"
    if (feedback["schema"] != "tehm-r5-public-verdict-v1"
            or feedback["task_verdict"] != verdict or type(feedback["stop"]) is not bool
            or feedback["stop"] != (verdict in {"PASS", "UNKNOWN"})):
        raise ValueError("inconsistent public feedback")
    return {
        "schema": "tehm-r5-s2-public-task-c4-v1", "task_id": "dev_task_001", "role": "DEV",
        "source_sha256": SOURCE, "parameters": copy.deepcopy(parameters),
        "specification": {
            "target": "Preserve accepted payload and last sequence under backpressure.",
            "preservation": "Do not reject an offered input when downstream is always ready.",
            "scope": "Byte-wide single-slot skid buffer; only declared obligations are evaluated.",
        },
        "research_profile": {"mechanism_family": FAMILY, "compatibility_profile": PROFILE,
                             "measurement_contract_digest": MEASUREMENT},
        "feedback": copy.deepcopy(feedback),
    }


def queries(task: dict):
    from contracts import MemoryQuery, RepairContext
    context = RepairContext(design_id=task["task_id"], check="rtl_functional",
        reports={"rtl_functional": {"dominant_class": "payload_transfer", "status": {
            "FAIL": "failed", "PASS": "clean", "UNKNOWN": "unknown"
        }[task["feedback"]["obligations"]["target"]]}}, compatibility_profile=PROFILE)
    query = MemoryQuery(query_plan={"design_id": task["task_id"],
        "mechanism_family": FAMILY, "compatibility_profile": PROFILE,
        "target_scope": PROFILE, "transformation_family": "skid_payload_restore_shadow_v7",
        "measurement_contract_digest": MEASUREMENT})
    return context, query


def project_tehm(route, selection) -> dict:
    """Whitelist a registered template, never forward a bound target payload."""
    from tehm.assets.skid_binding_v7 import _template
    if route.decision not in {"APPLY", "CONSIDER", "ABSTAIN", "INAPPLICABLE", "NO_SKILL"}:
        raise ValueError("unknown routing enum")
    decision = selection.receipt.decision
    if decision not in {"SELECT", "ABSTAIN", "INAPPLICABLE", "NO_SKILL"}:
        raise ValueError("unknown selection enum")
    if (len(selection.assets) > 1 or len(selection.assets) != len(selection.receipt.selected_asset_ids)
            or bool(selection.assets) != (decision == "SELECT")):
        raise ValueError("inconsistent selection")
    entries = []
    for asset in selection.assets:
        template = asset.get("definition", {}).get("binding_template")
        if template != _template():
            raise ValueError("unreviewed template; public projection withheld")
        entries.append({"alias": "memory_asset_1", "binding_template": copy.deepcopy(template)})
    return {"route": route.decision, "selection": decision, "entries": entries}


def retrieve(policy: str, source: str, parameters: dict, feedback: dict, *,
             legacy_dir: Path | None = None, memory_root: Path | None = None) -> tuple[dict, dict]:
    if policy not in POLICIES:
        raise ValueError("unknown policy")
    task = public_task(source, parameters, feedback)
    context, tehm_query = queries(task)
    private = {"schema": "tehm-r5-s2-memory-private-c4-v1", "policy": policy,
               "task_digest": digest(task), "candidate_limit": 1}
    if policy in {"no_persistent_memory", "legacy_memory"}:
        if policy == "no_persistent_memory":
            from none_backend import NoneMemoryBackend
            backend = NoneMemoryBackend()
        else:
            from legacy_backend import LegacyMemoryBackend
            if legacy_dir is None:
                raise ValueError("actual Legacy snapshot required")
            for name, expected in LEGACY_FILES.items():
                path = legacy_dir / name
                if path.is_symlink() or file_sha(path) != expected:
                    raise ValueError("Legacy frozen input drift")
            for suffix in ("-wal", "-shm"):
                path = legacy_dir / ("knowledge.sqlite" + suffix)
                if path.is_symlink() or (suffix == "-wal" and path.exists() and path.stat().st_size):
                    raise ValueError("Legacy sidecar drift")
            backend = LegacyMemoryBackend(knowledge_dir=legacy_dir, read_only_eval=True)
            private["snapshot_files"] = dict(LEGACY_FILES)
        try:
            query = backend.build_query(context)
            candidates = backend.retrieve(query, limit=1)
            private.update(context=context.to_dict(), query=query.to_dict(),
                           candidates=[vars(c) for c in candidates])
        finally:
            backend.close()
        # A real nonempty Legacy result is not silently filtered to NO_MATCH.
        ready = not candidates
        memory = {"route": "NOT_APPLICABLE", "selection": "REVIEW_REQUIRED" if candidates else "EMPTY",
                  "entries": []}
    else:
        from tehm.sync import verify_bundle
        from tehm.verified_execution import scoped_learning_replay
        from tehm.retrieval.memory_router import route_memory
        from tehm.retrieval.asset_selector import select_knowledge_grounded_assets
        if memory_root is None:
            raise ValueError("actual TEHM TRAIN snapshot required")
        bundle_path = memory_root / "bundles/m-plus"
        verified = verify_bundle(bundle_path)
        if not verified["ok"] or verified["manifest"]["bundle_digest"] != BUNDLE:
            raise ValueError("TEHM frozen bundle drift")
        database = bundle_path / "closed_loop/tehm.sqlite"
        before = file_sha(database)
        disk = sqlite3.connect(database.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            disk.backup(conn)
            disk.close()
            acquisitions = json.loads((memory_root / "research/parent-acquisitions.json").read_text())
            with scoped_learning_replay(conn, campaign_id=acquisitions["campaign_id"],
                    acquisitions=acquisitions["acquisitions"], expected_digest=acquisitions["digest"]):
                route = route_memory(conn, tehm_query, no_memory_budget=1, memory_budget=1,
                                     persist_state=False, commit=False)
                selection = select_knowledge_grounded_assets(conn, tehm_query, routing=route,
                    rtl_source_text=source, design_id=task["task_id"], rtl_public_context=parameters)
                memory = project_tehm(route, selection)
                private.update(query=tehm_query.to_dict(), route=route.to_dict(),
                               selection=selection.to_dict(), bundle_digest=BUNDLE,
                               database_sha256=before, acquisitions_digest=acquisitions["digest"])
        finally:
            disk.close()
            conn.close()
        if file_sha(database) != before:
            raise ValueError("TEHM snapshot changed")
        ready = True
    public = {"schema": "tehm-r5-s2-memory-public-c4-v1", "policy": policy,
              "task": task, "memory": memory, "context_projection_ready": ready,
              "agent_ready": False, "action_execution_authorized": False}
    private.update(public_digest=digest(public), model_calls=0, candidate_executions=0)
    return public, private
