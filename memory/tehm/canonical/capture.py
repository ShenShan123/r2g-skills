"""Canonical evidence capture adapter (design doc 21.2).

``capture(conn, store, record)`` turns one ``ExecutionRecord`` (a real executed
repair step: before/after states + action + observation delta + verification)
into the canonical store: content-addressed states, one transition, episode
membership, experience-graph edges, and the five-view materialization.

Everything is deterministic and idempotent: re-capturing identical evidence
yields identical IDs (dedup) and no duplicate rows.

First sources (Phase 2): R2G flow/signoff repair trajectories, whose actions
are already structured (config delta / sdc edit / stage rerun / repair action)
and whose oracles are executable.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from functools import wraps

import tehm.db as tehm_db
from tehm.canonical.episode import CanonicalEpisode, trajectory_summary
from tehm.canonical.state import CanonicalState, source_digest
from tehm.canonical.transition import (
    Action,
    CanonicalTransition,
    ObservationDelta,
    classify_outcome,
    primary_effect_key,
)
from tehm.canonical.verifier import VerifierSnapshot, toolchain_snapshot
from tehm.graph.local_design_graph import (
    build_run_context_graph,
    load_graph_from_artifact,
)
from tehm.graph.roles import RoleProjector
from tehm.graph.predicates import extract_predicates
from tehm.dataset import SPLITS
from tehm.views import materialize as views_materialize
from tehm.views.diagnostic import extract_diagnostic_signature
from tehm.ids import stable_dumps

_REQUIRED_RECORD_KEYS = ("domain", "before", "action", "after",
                         "observation_delta", "verification")


class ExecutionRecordError(ValueError):
    pass


@dataclass
class ExecutionRecord:
    """The input contract for one captured repair step.

    ``before`` / ``after``: ``{repository_ref?, config, reports, failure_signature?,
    artifacts?}`` — the state content. ``action``: ``{domain,
    transformation_family, payload}``. ``observation_delta`` / ``verification``:
    mirror the canonical Transition shapes. ``episode`` (optional):
    ``{episode_id?, mechanism_family, lineage_id, step_index, terminal_status?}``.
    """

    record_id: str
    domain: str
    before: dict
    action: dict
    after: dict
    observation_delta: dict
    verification: dict
    project_id: str | None = None
    design_id: str | None = None
    lineage_id: str | None = None
    repository_ref: str | None = None
    episode: dict | None = None

    @classmethod
    def from_dict(cls, data: dict) -> "ExecutionRecord":
        missing = [k for k in _REQUIRED_RECORD_KEYS if not data.get(k)]
        if missing:
            raise ExecutionRecordError(
                f"ExecutionRecord missing required keys: {missing}")
        record = cls(
            record_id=str(data.get("record_id") or ""),
            domain=str(data["domain"]),
            before=dict(data["before"]),
            action=dict(data["action"]),
            after=dict(data["after"]),
            observation_delta=dict(data["observation_delta"]),
            verification=dict(data["verification"]),
            project_id=data.get("project_id"),
            design_id=data.get("design_id"),
            lineage_id=data.get("lineage_id"),
            repository_ref=data.get("repository_ref"),
            episode=dict(data["episode"]) if data.get("episode") else None,
        )
        record.validate()
        return record

    def validate(self) -> None:
        for side, content in (("before", self.before), ("after", self.after)):
            if "reports" not in content:
                raise ExecutionRecordError(f"{side}.reports is required")
        if "config" not in self.before and "config" not in self.after:
            raise ExecutionRecordError("at least one state must carry config")
        # Fail fast at parse time on malformed action / delta / verification.
        Action.from_dict(self.action)
        ObservationDelta.from_dict(self.observation_delta)
        VerifierSnapshot.from_dict(self.verification)


@dataclass
class CaptureReceipt:
    record_id: str
    state_ids: dict = field(default_factory=dict)   # {"before": id, "after": id}
    transition_id: str = ""
    episode_id: str | None = None
    outcome: str = ""
    primary_effect_key: str = ""
    views_materialized: int = 0
    artifacts_written: int = 0
    edge_kinds: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "record_id": self.record_id,
            "state_ids": self.state_ids,
            "transition_id": self.transition_id,
            "episode_id": self.episode_id,
            "outcome": self.outcome,
            "primary_effect_key": self.primary_effect_key,
            "views_materialized": self.views_materialized,
            "artifacts_written": self.artifacts_written,
            "edge_kinds": self.edge_kinds,
        }


def _atomic_capture(fn):
    """Wrap canonical rows and derived views in one caller-safe savepoint.

    Capture may be called inside a larger transaction (for example a batch
    import).  In that case release only the local savepoint; otherwise commit
    the completed capture.  A view materializer failure therefore cannot leave
    canonical rows without their corresponding typed views, nor can it roll
    back an unrelated outer transaction.
    """
    @wraps(fn)
    def wrapped(conn: sqlite3.Connection, *args, **kwargs):
        had_outer_transaction = conn.in_transaction
        savepoint = "tehm_canonical_capture_v1"
        conn.execute(f"SAVEPOINT {savepoint}")
        savepoint_active = True
        try:
            result = fn(conn, *args, **kwargs)
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            savepoint_active = False
            if not had_outer_transaction:
                conn.commit()
            return result
        except Exception:
            if savepoint_active:
                conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise

    return wrapped


@_atomic_capture
def capture(conn: sqlite3.Connection, store, record: ExecutionRecord,
            *, materialized_at: str | None = None,
            dataset_campaign_id: str = "live",
            dataset_split: str = "training",
            dataset_learner_eligible: bool = True,
            frozen_snapshot_digest: str | None = None) -> CaptureReceipt:
    """Capture one execution record into the canonical store + views."""
    from tehm.db import now_local

    if not dataset_campaign_id:
        raise ValueError("dataset_campaign_id is required")
    if dataset_split not in SPLITS:
        raise ValueError(f"unknown dataset_split: {dataset_split!r}")
    if dataset_split != "training" and dataset_learner_eligible:
        raise ValueError(
            "only training evidence may be marked learner_eligible")
    materialized_at = materialized_at or now_local()

    # --- 1. build before/after canonical states -----------------------------
    before_content = _state_content(record.before, record.repository_ref)
    after_content = _state_content(record.after, record.repository_ref)

    cfg = dict(record.before.get("config") or {}) or dict(record.after.get("config") or {})
    platform = cfg.get("PLATFORM") or cfg.get("platform")
    run_tag = _find_run_tag(record.before) or _find_run_tag(record.after)

    # Prefer an adapter-supplied structural graph (for example RTL
    # MODULE/FSM semantics), with the report-only flow graph as fallback.
    before_graph = _state_graph(
        before_content, record.design_id, platform=platform, run_tag=run_tag)
    after_graph = _state_graph(
        after_content, record.design_id, platform=platform, run_tag=run_tag)

    before_manifest = {
        "before_graph": store.put_json("graph", before_graph.to_dict(),
                                       producer="tehm-capture"),
        **record.before.get("artifacts", {}),
    }
    after_manifest = {
        "after_graph": store.put_json("graph", after_graph.to_dict(),
                                      producer="tehm-capture"),
        **record.after.get("artifacts", {}),
    }

    before_state = CanonicalState(
        domain=record.domain,
        project_id=record.project_id,
        design_id=record.design_id,
        lineage_id=record.lineage_id,
        repository_ref=record.repository_ref,
        source_digest=source_digest(before_content),
        context_graph_digest=before_graph.digest(),
        verifier_snapshot=toolchain_snapshot(
            tool_versions=_tool_versions(record),
            oracle_available=_oracle_available(before_content)),
        artifact_manifest=before_manifest,
        created_at=materialized_at,
    )
    after_state = CanonicalState(
        domain=record.domain,
        project_id=record.project_id,
        design_id=record.design_id,
        lineage_id=record.lineage_id,
        repository_ref=record.repository_ref,
        source_digest=source_digest(after_content),
        context_graph_digest=after_graph.digest(),
        verifier_snapshot=toolchain_snapshot(
            tool_versions=_tool_versions(record),
            oracle_available=_oracle_available(after_content)),
        artifact_manifest=after_manifest,
        created_at=materialized_at,
    )

    # --- 2. action + delta + verification -----------------------------------
    action = Action.from_dict(record.action)
    delta = ObservationDelta.from_dict(record.observation_delta)
    verifier = VerifierSnapshot.from_dict(record.verification)
    outcome = classify_outcome(delta, verifier)
    effect_key = primary_effect_key(action, delta, verifier)

    # Preserve an execution/run witness on the immutable transition.  ORFS
    # causal replication gates require distinct run identities; keeping this
    # field in transition provenance makes that requirement auditable without
    # changing the content-addressed transition identity.
    run_id = _find_run_tag(record.before) or _find_run_tag(record.after)
    transition_provenance = {
        "record_id": record.record_id,
        "episode_id": (record.episode or {}).get("episode_id"),
        "source": "canonical-capture-v1",
    }
    if run_id:
        transition_provenance["run_id"] = str(run_id)
    transition = CanonicalTransition(
        source_state_id=before_state.state_id,
        target_state_id=after_state.state_id,
        action=action,
        observation_delta=delta,
        verifier=verifier,
        outcome=outcome,
        primary_effect_key=effect_key,
        created_regressions=list(delta.created_regressions),
        newly_observed=list(delta.newly_observed_failures),
        provenance=transition_provenance,
    )
    transition.validate()

    # --- 3. episode (session-accumulated repair episode graph) ---------------
    ep = record.episode or {}
    session_id = ep.get("episode_id")
    head = _session_head(conn, session_id) if session_id else None
    if head is not None:
        head_steps, head_outcomes = _session_transitions(conn, head["episode_id"])
        if transition.transition_id in head_steps:
            # Idempotent re-capture of a step already in this session.
            episode = CanonicalEpisode(
                domain=record.domain,
                initial_state_id=head["initial_state_id"],
                mechanism_family=ep.get("mechanism_family") or head["mechanism_family"],
                lineage_id=ep.get("lineage_id") or record.lineage_id,
                terminal_state_id=head["terminal_state_id"],
                terminal_status=head["terminal_status"],
                trajectory_summary_json=tehm_db.read_json(head["trajectory_summary_json"]),
                provenance={"record_id": record.record_id, "session_id": session_id},
                ordered_transition_ids=head_steps,
            )
        else:
            ordered = head_steps + [transition.transition_id]
            outcomes = head_outcomes + [outcome]
            episode = CanonicalEpisode(
                domain=record.domain,
                initial_state_id=head["initial_state_id"],
                mechanism_family=ep.get("mechanism_family") or head["mechanism_family"],
                lineage_id=ep.get("lineage_id") or record.lineage_id,
                terminal_state_id=after_state.state_id,
                terminal_status=ep.get("terminal_status", "OPEN"),
                trajectory_summary_json=trajectory_summary(outcomes),
                provenance={"record_id": record.record_id, "session_id": session_id},
                ordered_transition_ids=ordered,
            )
            _carry_forward_steps(conn, head["episode_id"], episode.episode_id)
    else:
        episode = CanonicalEpisode(
            domain=record.domain,
            initial_state_id=before_state.state_id,
            mechanism_family=ep.get("mechanism_family"),
            lineage_id=ep.get("lineage_id") or record.lineage_id,
            terminal_state_id=after_state.state_id,
            terminal_status=ep.get("terminal_status", "OPEN"),
            trajectory_summary_json=trajectory_summary([outcome]),
            provenance={"record_id": record.record_id, "session_id": session_id},
            ordered_transition_ids=[transition.transition_id],
        )

    # --- 4. persist (idempotent) --------------------------------------------
    for st in (before_state, after_state):
        conn.execute(
            """INSERT OR IGNORE INTO tehm_states (
                   state_id, domain, project_id, design_id, lineage_id,
                   repository_ref, source_digest, context_graph_digest,
                   verifier_snapshot_json, artifact_manifest_json,
                   created_at, schema_version)
               VALUES (:state_id, :domain, :project_id, :design_id, :lineage_id,
                   :repository_ref, :source_digest, :context_graph_digest,
                   :verifier_snapshot_json, :artifact_manifest_json,
                   :created_at, :schema_version)""",
            st.to_row())

    conn.execute(
        """INSERT OR IGNORE INTO tehm_transitions (
               transition_id, source_state_id, target_state_id, action_domain,
               action_json, observation_delta_json, verifier_json,
               primary_effect_key, outcome, created_regressions_json,
               newly_observed_json, provenance_json, schema_version)
           VALUES (:transition_id, :source_state_id, :target_state_id, :action_domain,
               :action_json, :observation_delta_json, :verifier_json,
               :primary_effect_key, :outcome, :created_regressions_json,
               :newly_observed_json, :provenance_json, :schema_version)""",
        transition.to_row())

    conn.execute(
        """INSERT OR REPLACE INTO tehm_episodes (
               episode_id, domain, initial_state_id, terminal_state_id,
               terminal_status, mechanism_family, lineage_id,
               trajectory_summary_json, provenance_json, schema_version)
           VALUES (:episode_id, :domain, :initial_state_id, :terminal_state_id,
               :terminal_status, :mechanism_family, :lineage_id,
               :trajectory_summary_json, :provenance_json, :schema_version)""",
        episode.to_row())

    step_index = int((record.episode or {}).get("step_index", 0))
    conn.execute(
        """INSERT OR REPLACE INTO tehm_episode_steps
               (episode_id, step_index, transition_id, branch_id)
           VALUES (?, ?, ?, 'main')""",
        (episode.episode_id, step_index, transition.transition_id))

    # Dataset role is explicit at the capture boundary.  Evaluation/A-B
    # callers pass a non-training campaign and ``dataset_learner_eligible``
    # false; this prevents a later learner query from seeing the row through
    # an implicit ``live`` membership.
    conn.execute(
        """INSERT OR REPLACE INTO tehm_dataset_membership
               (transition_id, campaign_id, split, learner_eligible,
                frozen_snapshot_digest, assigned_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (transition.transition_id, dataset_campaign_id, dataset_split,
         int(bool(dataset_learner_eligible)), frozen_snapshot_digest,
         materialized_at))

    # --- 5. experience-graph edges ------------------------------------------
    edge_kinds: list[str] = []
    for rel, src, dst in (
        ("EXECUTED_FROM", transition.transition_id, before_state.state_id),
        ("PRODUCED_STATE", transition.transition_id, after_state.state_id),
        ("PART_OF_EPISODE", transition.transition_id, episode.episode_id),
    ):
        conn.execute(
            """INSERT OR IGNORE INTO tehm_edges
                   (source_id, relation_type, target_id, metadata_json)
               VALUES (?, ?, ?, ?)""",
            (src, rel, dst, stable_dumps({"capture": record.record_id})))
        edge_kinds.append(rel)
    # --- 6. materialize the five views (parametric stays NOT_IMPLEMENTED) ----
    role_map = RoleProjector().project_all(before_graph)
    views = views_materialize.materialize_all(
        conn,
        state_before=before_state,
        state_after=after_state,
        transition=transition,
        episode=episode,
        before_graph=before_graph,
        after_graph=after_graph,
        before_signature=extract_diagnostic_signature(before_content),
        transition_delta_signature=views_materialize.transition_delta_signature(transition),
        role_map=role_map,
        materialized_at=materialized_at,
        commit=False,
    )

    # Sanity: predicates (H3) materialized alongside — they ride the semantic
    # view as an evidence snapshot; run the extractor for a determinism check.
    extract_predicates(before_graph)

    return CaptureReceipt(
        record_id=record.record_id,
        state_ids={"before": before_state.state_id, "after": after_state.state_id},
        transition_id=transition.transition_id,
        episode_id=episode.episode_id,
        outcome=outcome,
        primary_effect_key=effect_key,
        views_materialized=len(views),
        artifacts_written=4,
        edge_kinds=edge_kinds,
    )


# -- episode-graph helpers ---------------------------------------------------

def _session_head(conn: sqlite3.Connection, session_id: str) -> dict | None:
    """The current head episode of a repair session (most steps so far)."""
    if not session_id:
        return None
    rows = conn.execute(
        "SELECT episode_id, initial_state_id, terminal_state_id, "
        "terminal_status, mechanism_family, trajectory_summary_json, "
        "provenance_json FROM tehm_episodes").fetchall()
    head: dict | None = None
    for r in rows:
        prov = tehm_db.read_json(r["provenance_json"])
        if prov.get("session_id") != session_id:
            continue
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM tehm_episode_steps WHERE episode_id=?",
            (r["episode_id"],)).fetchone()["n"]
        if head is None or n > head["steps"]:
            head = {
                "episode_id": r["episode_id"],
                "initial_state_id": r["initial_state_id"],
                "terminal_state_id": r["terminal_state_id"],
                "terminal_status": r["terminal_status"],
                "mechanism_family": r["mechanism_family"],
                "trajectory_summary_json": r["trajectory_summary_json"],
                "steps": n,
            }
    return head


def _session_transitions(conn: sqlite3.Connection, episode_id: str) -> tuple[list, list]:
    """Ordered (transition_ids, outcomes) of one episode's main branch."""
    rows = conn.execute(
        """SELECT t.transition_id AS tid, t.outcome AS outcome
           FROM tehm_episode_steps s
           JOIN tehm_transitions t ON t.transition_id = s.transition_id
           WHERE s.episode_id = ? AND s.branch_id = 'main'
           ORDER BY s.step_index""", (episode_id,)).fetchall()
    return [r["tid"] for r in rows], [r["outcome"] for r in rows]


def _carry_forward_steps(conn: sqlite3.Connection, from_episode: str, to_episode: str) -> None:
    """Copy the old episode's steps onto the new accumulated episode id."""
    for r in conn.execute(
            "SELECT step_index, transition_id, branch_id FROM tehm_episode_steps "
            "WHERE episode_id = ?", (from_episode,)).fetchall():
        conn.execute(
            """INSERT OR REPLACE INTO tehm_episode_steps
                   (episode_id, step_index, transition_id, branch_id)
               VALUES (?, ?, ?, ?)""",
            (to_episode, r["step_index"], r["transition_id"], r["branch_id"]))


# -- helpers -----------------------------------------------------------------

def _state_content(state: dict, repository_ref: str | None) -> dict:
    content = {
        "repository_ref": repository_ref or state.get("repository_ref"),
        "config": dict(state.get("config") or {}),
        "reports": dict(state.get("reports") or {}),
        "artifacts": dict(state.get("artifacts") or {}),
        "failure_signature": dict(state.get("failure_signature") or {}),
    }
    if state.get("structural_graph") is not None:
        content["structural_graph"] = dict(state["structural_graph"])
    return content


def _state_graph(content: dict, design_id: str | None, *,
                 platform: str | None, run_tag: str | None):
    """Build the semantic graph owned by one canonical state.

    A supplied ``{nodes, edges}`` artifact is authoritative.  Malformed graph
    data fails closed instead of silently downgrading to a report-only graph;
    adapters that do not provide structural semantics use the flow fallback.
    """
    structural = content.get("structural_graph")
    if structural is not None:
        if (not isinstance(structural, dict)
                or not isinstance(structural.get("nodes"), list)
                or not isinstance(structural.get("edges"), list)):
            raise ExecutionRecordError(
                "structural_graph must contain nodes and edges lists")
        return load_graph_from_artifact(structural)
    return build_run_context_graph(
        content.get("reports", {}), content.get("config", {}),
        design_id=design_id, platform=platform, run_tag=run_tag)


def _find_run_tag(state: dict) -> str | None:
    # Failed production runs often have no report provenance (the flow stops
    # before route/signoff reports are emitted), while the adapter still
    # records the authoritative run tag in its content-addressed artifact
    # manifest.  Prefer report provenance, then fall back to that manifest so
    # a matched control/treatment pair shares the same source graph context.
    for report in (state.get("reports") or {}).values():
        provenance = report.get("provenance") or {}
        if provenance.get("run_tag"):
            return str(provenance["run_tag"])
    def nested(value):
        if isinstance(value, dict):
            if value.get("run_tag"):
                return str(value["run_tag"])
            for child in value.values():
                found = nested(child)
                if found:
                    return found
        elif isinstance(value, (list, tuple)):
            for child in value:
                found = nested(child)
                if found:
                    return found
        return None
    return nested(state.get("artifacts") or {})


def _tool_versions(record: ExecutionRecord) -> dict:
    verification = record.verification or {}
    tool_versions = verification.get("tool_versions") or {}
    if isinstance(tool_versions, dict):
        return tool_versions
    return {}


def _oracle_available(state_content: dict) -> dict:
    reports = state_content.get("reports", {})
    return {key: bool(report) for key, report in reports.items()}
