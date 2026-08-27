"""``tehm`` backend: the TEHM replacement memory plane (design doc 17.2, 21.2).

Completely bypasses the legacy learner / ranking / lifecycle. Ingestion goes
through the canonical capture adapter into ``tehm.sqlite`` + content-addressed
artifacts. Retrieval and activation proposals use the typed Phase 7/8 modules;
they never consult legacy authority.
"""
from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path

from contracts import (
    ActivationProposal,
    ActivationResult,
    BuildReport,
    ExecutionRecord,
    IngestReceipt,
    MemoryCandidate,
    MemoryQuery,
    MemorySnapshot,
    RepairContext,
)
from tehm import config as tehm_config
from tehm import db as tehm_db
from tehm import honesty as tehm_honesty
from tehm.artifact_store import ArtifactStore
from tehm.canonical.capture import capture as tehm_capture
from tehm.canonical.transition import OUTCOMES
from tehm.ids import stable_dumps

SCHEMA_VERSION = "tehm-v4"


def _backend_crystallize(crystallize_all, conn):
    """Call a crystallizer without breaking older injected seams.

    The backend owns the rebuild savepoint and therefore passes ``commit=False``
    to the v4 crystallizer.  A few downstream integrations (and deliberately
    small test doubles) still expose the pre-v4 ``crystallize_all(conn)``
    signature.  Inspect the callable before invoking it so that compatibility
    does not depend on catching an arbitrary ``TypeError`` from inside the
    crystallizer, which could otherwise hide a real rebuild failure.
    """
    try:
        parameters = inspect.signature(crystallize_all).parameters.values()
        accepts_commit = any(
            parameter.name == "commit" or
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters)
    except (TypeError, ValueError):
        # Unknown callables are treated conservatively as legacy seams.  The
        # surrounding backend savepoint still protects the derived projection.
        accepts_commit = False
    return (crystallize_all(conn, commit=False) if accepts_commit
            else crystallize_all(conn))


def _validate_activation_result(result: ActivationResult) -> None:
    """Validate the backend-neutral result before it reaches SQLite."""
    if not result.activation_id:
        raise ValueError("activation result requires activation_id")
    if result.outcome not in OUTCOMES:
        raise ValueError(
            f"activation outcome must be one of {OUTCOMES}, got {result.outcome!r}")
    if not isinstance(result.created_regressions, (list, tuple)):
        raise ValueError("created_regressions must be a list")
    if any(not isinstance(item, str) or not item
           for item in result.created_regressions):
        raise ValueError("created_regressions must contain non-empty strings")
    if result.produced_transition_id is not None and (
            not isinstance(result.produced_transition_id, str)
            or not result.produced_transition_id):
        raise ValueError("produced_transition_id must be a non-empty string or None")
    if result.rollback_receipt is not None and not isinstance(
            result.rollback_receipt, dict):
        raise ValueError("rollback_receipt must be a dict or None")


def _stored_activation_list(raw: str | None, *, field: str) -> list:
    if raw is None:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"activation {field} is not valid JSON") from exc
    if not isinstance(value, list) or any(
            not isinstance(item, str) or not item for item in value):
        raise ValueError(f"activation {field} must be a list of non-empty strings")
    return value


def _stored_activation_rollback(raw: str | None) -> dict | None:
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("activation rollback_receipt_json is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("activation rollback_receipt_json must be a JSON object")
    return value


def _require_transition(conn, transition_id: str, activation_id: str) -> None:
    row = conn.execute(
        "SELECT transition_id FROM tehm_transitions WHERE transition_id=?",
        (transition_id,)).fetchone()
    if row is None:
        raise ValueError(
            "activation result references missing canonical transition "
            f"{transition_id} ({activation_id})")


class TehmMemoryBackend:
    """The complete-replacement TEHM backend."""

    name = "tehm"

    def __init__(self, *, db_path: Path | None = None,
                 artifact_root: Path | None = None,
                 read_only_eval: bool = False):
        self.db_path = Path(db_path) if db_path else tehm_config.default_db_path()
        self.artifact_root = (Path(artifact_root) if artifact_root
                              else tehm_config.default_artifact_root())
        self.read_only_eval = read_only_eval
        self._conn = None
        self._store: ArtifactStore | None = None

    def _open(self):
        if self._conn is None:
            if self.read_only_eval:
                self._conn = tehm_db.connect_read_only(self.db_path)
            else:
                self._conn = tehm_db.connect(self.db_path)
                tehm_db.ensure_schema(self._conn)
        if self._store is None:
            self._store = ArtifactStore(self.artifact_root)
        return self._conn, self._store

    def ingest_execution(self, record: ExecutionRecord) -> IngestReceipt:
        if self.read_only_eval:
            raise RuntimeError("cannot ingest into a read-only TEHM evaluation snapshot")
        conn, store = self._open()
        receipt = tehm_capture(conn, store, record)
        return IngestReceipt(
            record_id=receipt.record_id,
            transition_id=receipt.transition_id,
            state_ids=receipt.state_ids,
            episode_id=receipt.episode_id,
            outcome=receipt.outcome,
            backend="tehm",
        )

    def ingest_project(self, project_dir: Path | str) -> list[IngestReceipt]:
        """Capture a real R2G project without touching legacy ``runs`` tables."""
        if self.read_only_eval:
            raise RuntimeError("cannot ingest into a read-only TEHM evaluation snapshot")
        from tehm.adapters.r2g_evidence import capture_r2g_project

        conn, store = self._open()
        receipts = capture_r2g_project(conn, store, Path(project_dir))
        return [IngestReceipt(
            record_id=r.record_id, transition_id=r.transition_id,
            state_ids=r.state_ids, episode_id=r.episode_id,
            outcome=r.outcome, backend="tehm") for r in receipts]

    def build_query(self, context: RepairContext) -> MemoryQuery:
        """Typed query plan (design doc 9.2, Stage 0)."""
        from tehm.retrieval.query_planner import plan_query

        return plan_query(context)

    def retrieve(self, query: MemoryQuery, *, limit: int) -> list[MemoryCandidate]:
        """Run the typed retrieval pipeline (Phase 7) over admissible rules.

        The already-planned query is passed through unchanged so structural and
        compatibility context cannot be lost at the backend seam. Honest empty
        result only when no admissible rule matches (never fabricated).
        """
        from tehm.retrieval.pipeline import retrieve_query

        conn, _ = self._open()
        receipt = retrieve_query(conn, query, limit=limit)
        return [
            MemoryCandidate(
                candidate_id=r.candidate_id,
                source="tehm_rule",
                payload={
                    "rule_id": r.rule_id,
                    "transformation_family": r.transformation_family,
                    "applicability_status": r.applicability_status,
                    "similarity": r.similarity,
                },
                score=r.score,
                provenance={"tehm_rule": r.rule_id,
                            "source_episodes": r.source_episodes},
            )
            for r in receipt.results
        ]

    def get_causal_paths(self, query: MemoryQuery, *, campaign_id: str = "live",
                         limit: int = 10, evaluation_only: bool = False) -> list[dict]:
        """Read causal shadow paths only through an explicit evaluation lane.

        This deliberately cannot be mistaken for ``retrieve``: production
        runtime remains promoted-rule-only and never consumes causal score as
        lifecycle authority.
        """
        if not evaluation_only:
            raise RuntimeError("causal path recall is evaluation-only")
        from tehm.retrieval.causal_recall import retrieve_causal_paths

        conn, _ = self._open()
        return [match.to_dict() for match in retrieve_causal_paths(
            conn, query, campaign_id=campaign_id, limit=limit,
            include_shadow=True)]

    def observe_transition(self, transition_id: str, *,
                          campaign_id: str = "live"):
        """Append an online shadow observation; never promote or mutate rules."""
        if self.read_only_eval:
            raise RuntimeError("cannot evolve a read-only TEHM evaluation snapshot")
        from tehm.evolution.manager import observe_transition

        conn, _ = self._open()
        return observe_transition(conn, transition_id, campaign_id)

    def get_capability_snapshot(self, policy_snapshot_id: str) -> dict:
        """Read a content-addressed policy snapshot for attribution audits."""
        from tehm.capability.policy_snapshot import load_policy_snapshot

        conn, _ = self._open()
        return load_policy_snapshot(conn, policy_snapshot_id)

    def record_policy_load(self, policy_snapshot_id: str, *, runtime_id: str,
                           loaded: bool = True, receipt: dict | None = None):
        """Persist a runtime policy-load receipt for capability attribution."""
        if self.read_only_eval:
            raise RuntimeError("cannot record policy load in a read-only snapshot")
        from tehm.capability.policy_snapshot import record_policy_load

        conn, _ = self._open()
        return record_policy_load(
            conn, policy_snapshot_id=policy_snapshot_id, runtime_id=runtime_id,
            loaded=loaded, receipt=receipt)

    def propose_activation(self, candidate: MemoryCandidate,
                           context: RepairContext) -> ActivationProposal | None:
        from tehm.activation.binding import bind_rule
        from tehm.activation.instantiate import instantiate_rewrite
        from tehm.activation.obligation_transfer import transfer_obligations
        from tehm.ids import activation_id, stable_dumps
        from tehm.retrieval.index import build_index

        if candidate.source != "tehm_rule":
            return None
        conn, _ = self._open()
        rule_id = (candidate.payload or {}).get("rule_id") or candidate.candidate_id
        rule = build_index(
            conn, lifecycle_statuses=frozenset({"promoted"})).get(rule_id)
        if rule is None:
            return None
        applicability = (candidate.payload or {}).get(
            "applicability_status", "UNRESOLVED")
        if applicability != "APPLICABLE":
            return None
        binding = bind_rule(rule, context)
        action = instantiate_rewrite(rule, binding, context)
        transfer = transfer_obligations(rule, context)
        target_ref = "query_state_" + hashlib.sha1(
            stable_dumps(context.to_dict()).encode()).hexdigest()[:16]
        act_id = activation_id(
            rule_id_=rule_id, target_state_id=target_ref,
            retrieval_receipt={"candidate_id": candidate.candidate_id,
                               "score": candidate.score})
        return ActivationProposal(
            candidate_id=candidate.candidate_id,
            activation_id=act_id,
            applicability_status=applicability,
            binding={"status": binding.status,
                     "substitutions": binding.substitutions,
                     "bound_entities": binding.bound_entities,
                     "proof": binding.proof,
                     "action": action},
            obligations=transfer["results"],
            obligation_coverage=transfer["obligation_coverage"],
        )

    def record_activation(self, result: ActivationResult) -> None:
        """Record a backend-level activation result idempotently.

        The full Phase-8 pipeline owns the rich activation row.  This seam is
        still authoritative for callers that only have the backend-neutral
        result: it finalizes an UNKNOWN row and bumps utility exactly once
        when the row has not already been finalized by the pipeline.  A
        finalized receipt is immutable at this seam: exact replays are a
        no-op, while changed outcome, regression, rollback, or transition
        linkage is rejected rather than silently erased.
        """
        if self.read_only_eval:
            raise RuntimeError("cannot record activation in a read-only TEHM evaluation snapshot")
        _validate_activation_result(result)
        conn, _ = self._open()
        row = conn.execute(
            """SELECT rule_id, outcome, created_regressions_json,
                      produced_transition_id, rollback_receipt_json
                 FROM tehm_activations WHERE activation_id=?""",
            (result.activation_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown TEHM activation: {result.activation_id}")

        prior = row["outcome"]
        if prior is not None and prior not in OUTCOMES:
            raise ValueError(
                f"activation {result.activation_id} has invalid stored outcome")
        stored_regressions = _stored_activation_list(
            row["created_regressions_json"],
            field="created_regressions_json")
        stored_rollback = _stored_activation_rollback(
            row["rollback_receipt_json"])
        stored_transition = row["produced_transition_id"]
        if stored_transition is not None:
            _require_transition(conn, str(stored_transition), result.activation_id)

        incoming = (
            result.outcome,
            stable_dumps(result.created_regressions),
            result.produced_transition_id,
            stable_dumps(result.rollback_receipt)
            if result.rollback_receipt is not None else None,
        )
        existing = (
            prior,
            stable_dumps(stored_regressions),
            stored_transition,
            stable_dumps(stored_rollback)
            if stored_rollback is not None else None,
        )
        # Activation IDs are replay keys, never overwrite keys.  UNKNOWN is
        # the sole provisional state that may be finalized by this seam.
        if prior not in (None, "UNKNOWN"):
            if incoming != existing:
                raise ValueError(
                    "activation replay conflicts with finalized receipt "
                    f"{result.activation_id}")
            return
        if incoming == existing:
            return
        if result.produced_transition_id is not None:
            _require_transition(
                conn, result.produced_transition_id, result.activation_id)

        had_outer_transaction = conn.in_transaction
        savepoint = "tehm_backend_activation_v1"
        conn.execute(f"SAVEPOINT {savepoint}")
        savepoint_active = True
        try:
            conn.execute(
                """UPDATE tehm_activations
                      SET outcome=?, created_regressions_json=?,
                          produced_transition_id=?, rollback_receipt_json=?
                    WHERE activation_id=?""",
                (result.outcome,
                 stable_dumps(result.created_regressions),
                 result.produced_transition_id,
                 stable_dumps(result.rollback_receipt)
                 if result.rollback_receipt is not None else None,
                 result.activation_id))
            if prior in (None, "UNKNOWN") and result.outcome != "UNKNOWN":
                from tehm.activation.update import update_rule_utility
                update_rule_utility(
                    conn, row["rule_id"], result.outcome,
                    activation_id=result.activation_id,
                    created_regressions=result.created_regressions,
                    commit=False)
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            savepoint_active = False
            if not had_outer_transaction:
                conn.commit()
        except Exception:
            if savepoint_active:
                conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise


    def rebuild(self, *, frozen_source: bool = False) -> BuildReport:
        conn, _ = self._open()
        if self.read_only_eval:
            ok, report = tehm_honesty.run_all(conn, self._store, self.db_path)
            return BuildReport(
                backend="tehm", frozen_source=True,
                rebuilt={"honesty": report, "rules": "frozen"}, ok=ok,
                detail="read-only evaluation snapshot: rebuild skipped")

        from tehm.crystallization.build_rules import crystallize_all
        from tehm.lifecycle.rule_status import enter_shadow, get_status, set_status

        # Rebuild and lifecycle enrollment are one derived projection.  A
        # failure while enrolling a later rule must not leave a prefix of
        # rules/status rows visible, and an existing caller transaction must
        # remain owned by that caller.
        had_outer_transaction = conn.in_transaction
        savepoint = "tehm_backend_rebuild_v1"
        conn.execute(f"SAVEPOINT {savepoint}")
        savepoint_active = True
        try:
            rules = _backend_crystallize(crystallize_all, conn)
            entered = skipped_inadmissible = 0
            for rule in rules:
                rule_id = rule["rule_id"]
                # H6 is a lifecycle admission rule, not an exception-driven
                # assertion.  Crystallization intentionally returns auditable
                # INSTANCE/UNSTABLE/REJECTED candidates too; they remain in
                # tehm_rules but must never be enrolled into shadow/candidate.
                if rule.get("validity_status") not in {
                        "PROVISIONAL_VALID", "VALIDATED"}:
                    skipped_inadmissible += 1
                    continue
                scope = str((rule.get("before_pattern") or {}).get(
                    "target_check") or "signoff")
                source_profile = _source_outcome_profile(conn, rule_id)
                current = get_status(conn, rule_id=rule_id, target_scope=scope)
                if current is None:
                    enter_shadow(
                        conn, rule_id=rule_id, target_scope=scope,
                        provenance={"authority": "backend_rebuild",
                                    "source_outcomes": source_profile},
                        commit=False)
                    if (not source_profile["observed"] or
                            source_profile["positive"] > 0):
                        set_status(
                            conn, rule_id=rule_id, target_scope=scope,
                            status="candidate",
                            provenance={"authority": "backend_rebuild",
                                        "source_outcomes": source_profile},
                            commit=False)
                        entered += 1
                elif (current["status"] == "candidate" and
                      source_profile["observed"] and
                      source_profile["positive"] == 0):
                    set_status(
                        conn, rule_id=rule_id, target_scope=scope,
                        status="shadow",
                        provenance={"authority": "backend_rebuild",
                                    "reason": "no_positive_source_outcome",
                                    "source_outcomes": source_profile},
                        commit=False)
            ok, report = tehm_honesty.run_all(conn, self._store, self.db_path)
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            savepoint_active = False
            if not had_outer_transaction:
                conn.commit()
            return BuildReport(
                backend="tehm", frozen_source=frozen_source,
                rebuilt={"honesty": report, "rules": len(rules),
                         "lifecycle_candidates_entered": entered,
                         "lifecycle_inadmissible_skipped": skipped_inadmissible},
                ok=ok,
                detail="crystallized canonical experience and enrolled new valid rules")
        except Exception:
            if savepoint_active:
                conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise

    def run_orfs_trials(self, *, base_entries: list[dict],
                        run_flow_script: Path, fix_signoff_script: Path,
                        n_designs: int = 1, repeats: int = 2,
                        work_root: Path | None = None,
                        env: dict | None = None,
                        provided_bindings: dict[str, dict] | None = None,
                        lifecycle_statuses: frozenset[str] = frozenset({"candidate"}),
                        mutate_lifecycle: bool = True,
                        promotion_gate_inputs: dict[str, dict] | None = None,
                        production_authority: bool = True) -> list[dict]:
        """Execute pending TEHM candidates on the shared real ORFS base."""
        if self.read_only_eval:
            raise RuntimeError("cannot run lifecycle trials on a read-only snapshot")
        from tehm.lifecycle.orfs_trial import run_pending_orfs_trials

        conn, store = self._open()
        return run_pending_orfs_trials(
            conn, store, base_entries=base_entries,
            run_flow_script=run_flow_script,
            fix_signoff_script=fix_signoff_script,
            n_designs=n_designs, repeats=repeats, work_root=work_root,
            env=env, provided_bindings=provided_bindings,
            lifecycle_statuses=lifecycle_statuses,
            mutate_lifecycle=mutate_lifecycle,
            promotion_gate_inputs=promotion_gate_inputs,
            production_authority=production_authority)

    def snapshot(self) -> MemorySnapshot:
        conn, _ = self._open()
        counts = {
            "states": tehm_db.count_rows(conn, "tehm_states"),
            "transitions": tehm_db.count_rows(conn, "tehm_transitions"),
            "episodes": tehm_db.count_rows(conn, "tehm_episodes"),
            "views": tehm_db.count_rows(conn, "tehm_views"),
            "rules": tehm_db.count_rows(conn, "tehm_rules"),
            "activations": tehm_db.count_rows(conn, "tehm_activations"),
            "causal_nodes": tehm_db.count_rows(conn, "tehm_causal_nodes"),
            "causal_edges": tehm_db.count_rows(conn, "tehm_causal_edges"),
            "causal_paths": tehm_db.count_rows(conn, "tehm_causal_paths"),
            "memory_events": tehm_db.count_rows(conn, "tehm_memory_events"),
            "rule_revisions": tehm_db.count_rows(conn, "tehm_rule_revisions"),
            "assets": tehm_db.count_rows(conn, "tehm_assets"),
            "capabilities": tehm_db.count_rows(conn, "tehm_capabilities"),
        }
        return MemorySnapshot(
            backend="tehm",
            snapshot_id=f"tehm:db:{self.db_path}:sha256:{self._store_digest()}",
            schema_version=SCHEMA_VERSION,
            counts=counts,
        )

    def _store_digest(self) -> str:
        """Digest the SQLite image plus WAL for frozen-resume identity."""
        h = hashlib.sha256()
        for path in (self.db_path, Path(str(self.db_path) + "-wal")):
            if path.is_file():
                h.update(path.name.encode())
                with path.open("rb") as fh:
                    for chunk in iter(lambda: fh.read(65536), b""):
                        h.update(chunk)
        return h.hexdigest()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


def _source_outcome_profile(conn, rule_id: str) -> dict:
    rows = conn.execute(
        """SELECT t.outcome
           FROM tehm_rule_sources rs
           JOIN tehm_episode_steps es ON es.episode_id=rs.episode_id
           JOIN tehm_transitions t ON t.transition_id=es.transition_id
           WHERE rs.rule_id=?""", (rule_id,)).fetchall()
    outcomes = [r["outcome"] for r in rows]
    return {"observed": bool(outcomes),
            "positive": sum(o in {"PASS", "PARTIAL"} for o in outcomes),
            "harmful": sum(o in {"FAIL", "REGRESSION"} for o in outcomes),
            "neutral": sum(o in {"NEUTRAL", "UNKNOWN"} for o in outcomes),
            "outcomes": sorted(set(outcomes))}
