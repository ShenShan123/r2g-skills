"""Evidence-preserving ORFS consumer of the existing TEHM Agent router.

No promotion, provider, execution, or online write-back authority lives here.
An observed intervention remains L1/shadow. Only paths selected by the existing
authority/applicability router may contribute bounded context to a repair call.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import time

VERSION = "tehm-orfs-agent-consumer-v1"
MAX_CLAIMS = 2
MAX_EXAMPLES = 4
CONTEXT_BYTES = 16000
NO_MEMORY = "Memory: no applicable validated knowledge."


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def stable(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value):
    return hashlib.sha256(stable(value).encode()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    Path(path).write_text(json.dumps(value, sort_keys=True, indent=1) + "\n")


def read_only(path):
    conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def enrich_store(old_fold, destination):
    """Build shadow paths/knowledge using native constructors on a new copy.

    Only an explicitly bound real fix can seed a fragment. Summary rows, unused
    proposals and unbound outcomes stay in canonical memory, not causal claims.
    """
    from tehm.snapshot import bind_snapshot, seal_store
    from tehm.causal.path_builder import build_transition_causal_fragment, consolidate_causal_path
    from tehm.knowledge.builder import build_knowledge_from_path
    from tehm.knowledge.registry import register_knowledge
    from tehm_backend import TehmMemoryBackend

    old_fold, destination = Path(old_fold), Path(destination)
    started, cpu = time.time(), time.process_time()
    source = old_fold / "shared/shared_units.jsonl"
    manifest = load(old_fold / "shared/manifest.json")
    if sha(source) != manifest["units_sha256"]:
        raise ValueError("Shared history digest mismatch")
    exclusions = load(old_fold / "exclusions.json")
    units = [json.loads(line) for line in source.read_text().splitlines() if line]
    by_unit = {u["unit_id"]: u for u in units}
    if len(by_unit) != len(units) or set(by_unit) != set(exclusions["kept_unit_ids"]):
        raise ValueError("History/exclusion identity mismatch")
    if any(u["source_handoff_id"] in exclusions["excluded_origins"] for u in units):
        raise ValueError("Excluded source entered knowledge construction")
    parity = load(old_fold / "store/import-parity.json")
    if {p["unit_id"] for p in parity} != set(by_unit):
        raise ValueError("Import parity does not cover shared history")
    before = sha(old_fold / "store/tehm.sqlite")
    bind_snapshot(old_fold / "store", destination)
    backend = TehmMemoryBackend(db_path=destination / "tehm.sqlite",
                               artifact_root=destination / "artifacts", read_only_eval=False)
    conn, _ = backend._open()
    rows = []
    try:
        for p in sorted(parity, key=lambda x: x["unit_id"]):
            u = by_unit[p["unit_id"]]
            raw, label = u["raw_row"], u["shared_label"]
            entry = {**p, "source_handoff_id": u["source_handoff_id"],
                     "signature": label.get("signature"), "source_refs": u["provenance"],
                     "raw_row": raw, "shared_label": label}
            bound = label.get("binding") or {}
            if not (raw.get("from_stage") and raw.get("pre_run_tag") and raw.get("post_run_tag")
                    and bound.get("pre") == "KNOWN" and bound.get("post") == "KNOWN"):
                entry["construction"] = "CANONICAL_ONLY_NO_BOUND_EXECUTED_STEP"
                rows.append(entry)
                continue
            fragment = build_transition_causal_fragment(conn, p["transition_id"])
            path = consolidate_causal_path(conn, [fragment])
            claim = build_knowledge_from_path(conn, path.path_id)
            if claim.status != "shadow" or claim.evidence_level not in (
                    "L0_ASSOCIATION", "L1_EXECUTED_INTERVENTION"):
                raise ValueError("A single NB observation must not grant causal authority")
            scope = raw.get("check")
            if not isinstance(scope, str) or not scope:
                raise ValueError("Bound action lacks a target check")
            register_knowledge(conn, claim, target_scope=scope, provenance={
                "consumer_version": VERSION, "shared_units_sha256": sha(source),
                "unit_id": u["unit_id"], "authority": "observed-step-shadow-only"})
            entry.update(construction="SHADOW_KNOWLEDGE", path_id=path.path_id,
                         knowledge_id=claim.object_id, evidence_level=claim.evidence_level,
                         status=claim.status, scope=scope, mechanism_family=claim.mechanism_family)
            rows.append(entry)
        conn.commit()
    finally:
        backend.close()
    if sha(old_fold / "store/tehm.sqlite") != before:
        raise RuntimeError("Source store changed")
    receipt = {"version": VERSION, "source_store_sha256": before,
               "shared_units_sha256": sha(source), "target": exclusions["target"],
               "excluded_origins": exclusions["excluded_origins"], "units": rows,
               "promotions": 0, "controlled_pairs_created": 0,
               "assets_created": 0, "asset_reason": "no validated controlled knowledge",
               "model_requests": 0, "tokens": 0, "eda_flows": 0,
               "wall_s": time.time() - started, "cpu_s": time.process_time() - cpu}
    save(destination / "agent-construction.json", receipt)
    receipt["sealed_db_sha256"] = seal_store(destination)
    return receipt


def selected_claims(conn, decision, situation):
    """Re-resolve selected state with the router's own authority/scope rules.

    Path IDs alone do not identify a claim: old/superseded claims can reference
    the same path. Reuse the resolver, including its exact/global scope rules.
    """
    from contracts import MemoryQuery
    from tehm.state import resolve_current_state
    from tehm.retrieval.memory_router import scope_for_query, _knowledge_for_state
    scope = scope_for_query(MemoryQuery(query_plan=situation))
    state = resolve_current_state(conn, scope, mode="shadow", persist=False, commit=False)
    if state.resolution_id != decision.resolved_state_id:
        raise ValueError("Resolved memory changed between routing and rendering")
    eligible, vetoes, insufficient, integrity = _knowledge_for_state(
        conn, state=state, scope=scope, context=situation)
    if vetoes or integrity:
        raise ValueError("Selected knowledge lost applicability/authority: " + stable([vetoes, integrity]))
    paths = set(decision.selected_path_ids)
    return [item["claim"] for item in eligible if paths.intersection(item["path_ids"])]


def render_selected(conn, decision, situation, source_units):
    """Render selected validated claims and bounded witnesses, never all views."""
    from tehm.causal.evidence_level import evidence_rank
    from tehm.knowledge.applicability import evaluate_applicability
    from tehm.views.base import payload_digest
    if decision.decision not in ("CONSIDER", "APPLY"):
        return NO_MEMORY, [], []
    selected_paths = set(decision.selected_path_ids)
    claims = []
    # Never scan/render all validated rows or stale claims sharing a path.
    for claim in selected_claims(conn, decision, situation):
        if claim.status != "validated":
            continue
        applicability = evaluate_applicability(claim, situation)
        if not applicability.eligible:
            continue
        if evidence_rank(claim.evidence_level) < evidence_rank("L2_CONTROLLED_INTERVENTION"):
            raise ValueError("Selected knowledge is below the existing causal threshold")
        claims.append(claim)
    claims.sort(key=lambda c: (-evidence_rank(c.evidence_level), c.object_id))
    claims = claims[:MAX_CLAIMS]
    if not claims:
        raise ValueError("Router selected memory without renderable validated knowledge")
    unit_map = {u["transition_id"]: u for u in source_units}
    examples, claim_rows, used = [], [], set()
    for claim in claims:
        claim_rows.append(claim.to_dict())
        for path_id in claim.causal_path_ids:
            if path_id not in selected_paths:
                continue
            row = conn.execute("SELECT source_transitions_json FROM tehm_causal_paths WHERE path_id=?",
                               (path_id,)).fetchone()
            for tid in sorted(json.loads(row[0])):
                if tid in used or len(examples) >= MAX_EXAMPLES:
                    continue
                if tid not in unit_map:
                    raise ValueError("Selected witness has no eligible shared-history source")
                unit = unit_map[tid]
                # The caller never fetches unseen-target records or arbitrary raw
                # history. Include only a selected witness's existing typed views.
                t = conn.execute("SELECT source_state_id,target_state_id FROM tehm_transitions WHERE transition_id=?",
                                 (tid,)).fetchone()
                owners = {tid, t[0], t[1]}
                owners.update(r[0] for r in conn.execute(
                    "SELECT episode_id FROM tehm_episode_steps WHERE transition_id=?", (tid,)))
                views = []
                for owner in sorted(owners):
                    for view in conn.execute("SELECT * FROM tehm_views WHERE owner_id=? ORDER BY view_type",
                                             (owner,)):
                        payload = json.loads(view["payload_json"])
                        if payload_digest(view["schema_version"], view["extractor_version"], payload) != view["payload_digest"]:
                            raise ValueError("Selected typed view digest mismatch")
                        # Payloads can contain large graphs. Preserve their IDs in
                        # the receipt; render task-relevant evidence fields only.
                        views.append({"type": view["view_type"], "digest": view["payload_digest"]})
                raw = unit["raw_row"]
                examples.append({"unit_id": unit["unit_id"], "transition_id": tid,
                    "source": unit["source_handoff_id"], "evidence_level": unit.get("evidence_level"),
                    "situation": raw.get("situation"), "edits": unit["shared_label"].get("edits"),
                    "result": unit["shared_label"], "views": views})
                used.add(tid)
    block = {"knowledge": claim_rows, "selected_examples": examples,
             "limits": "Historical evidence only. Not proof of success here. Obey the current task "
                       "contract; all proposed edits require fresh target verification and rollback.",
             "write_back": "disabled for this frozen-history comparison"}
    context = "Memory: applicable validated mechanism knowledge.\n" + stable(block)
    if len(context.encode()) > CONTEXT_BYTES:
        raise ValueError("Selected TEHM context exceeds its declared byte limit")
    return context, [e["unit_id"] for e in examples], [c.object_id for c in claims]


class AgentMemory:
    """Read-only, no-provider adapter for the real strict repair consumer."""
    arm = "TM0"

    def __init__(self, project, construction_path):
        self.project = Path(project)
        self.construction = load(construction_path)
        self.db = self.project / ".tehm-store/tehm.sqlite"
        self.db_sha = sha(self.db)
        self.last_receipt = None

    def context(self, observation, situation, *, auxiliary=None, max_bytes=CONTEXT_BYTES):
        from contracts import MemoryQuery
        from tehm.retrieval.memory_router import route_memory
        from tehm.causal.mechanism import orfs_signature
        started, cpu = time.time(), time.process_time()
        # No mechanism inferred from a repair action name. Current check and
        # typed situation are the query; the native applicability layer decides.
        family, profile = orfs_signature(situation)
        plan = {**situation, "target_scope": situation["check"],
                "mechanism_family": family, "compatibility_profile": profile}
        with read_only(self.db) as conn:
            decision = route_memory(conn, MemoryQuery(query_plan=plan),
                no_memory_budget=2, memory_budget=1, persist_state=False, commit=False)
            data = decision.to_dict()
            if data["resolved_state_id"] == "UNRESOLVED" or data["applicability"].get("integrity_errors"):
                raise ValueError("TEHM routing integrity failure: " + stable(data))
            # OOD/veto/insufficient support may legitimately abstain; malformed
            # state, schema and authority are technical faults, never no-skill.
            if data["causal_support"].get("status") == "INVALID":
                raise ValueError("TEHM authority integrity failure: " + stable(data))
            context, ids, claims = render_selected(conn, decision, plan, self.construction["units"])
        if len(context.encode()) > max_bytes:
            raise ValueError("TEHM context does not fit the common request policy")
        self.last_receipt = {
            "version": VERSION, "query": plan, "decision": data,
            "decision_digest": decision.decision_digest, "selected_unit_ids": ids,
            "selected_knowledge_ids": claims, "context_sha256": hashlib.sha256(context.encode()).hexdigest(),
            "context_bytes": len(context.encode()), "store_sha256": self.db_sha,
            "shared_units_sha256": self.construction["shared_units_sha256"],
            "construction_sha256": digest(self.construction), "write_back": False,
            "wall_s": time.time() - started, "cpu_s": time.process_time() - cpu}
        return context, ids

    def request_receipt(self, messages, turn):
        if self.last_receipt is None:
            raise ValueError("Agent request without preceding route")
        return {**self.last_receipt, "turn": turn,
                "messages_sha256": digest(messages), "request_prefix": "repair-turn-%d" % turn}


def initial_route(memory, H, T, project):
    """Exercise/log the same router before the common deterministic repairs."""
    check, vclass, count = H._task_failure(project)
    import importlib.util
    path = H.SKILL / "knowledge/situation.py"
    spec = importlib.util.spec_from_file_location("tehm_agent_situation", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    context, _ = memory.context(stable(H.observation(project)),
                                module.from_project(project, check, vclass, count))
    T.event(H, project, "tehm_agent_route", {**memory.last_receipt,
            "phase": "before_shared_repair", "model_request_sent": False})
    return context


def verify_request(project, receipt):
    """Bind memory to the serialized provider body; no request means no consumption."""
    paths = sorted(Path(project).glob(receipt["request_prefix"] + ".try*.request.json"))
    records = []
    for path in paths:
        body = load(path)
        if digest(body["messages"]) != receipt["messages_sha256"]:
            raise ValueError("Actual model request differs from routed TEHM messages")
        records.append({"path": path.name, "sha256": sha(path)})
    return {**receipt, "request_files": records, "serialized_request_present": bool(records),
            "provider_received_not_proven": True}
