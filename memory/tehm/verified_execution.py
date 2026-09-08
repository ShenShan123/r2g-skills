"""Shared verified-execution admission for learner-derived memory state.

Dataset membership answers *which partition* a transition belongs to.  It is
not evidence that the transition was actually executed under an executable
oracle.  Every learner consumer reuses this predicate so crystallization and
online event writers cannot provide weaker authority paths around one another.
"""
from __future__ import annotations

import sqlite3
import copy
import hashlib
from contextlib import contextmanager
from contextvars import ContextVar

from tehm.causal.mechanism import load_transition_facts
from tehm.ids import stable_dumps


_SCOPED_REPLAY = ContextVar("tehm_scoped_learning_replay", default=None)


@contextmanager
def scoped_learning_replay(conn: sqlite3.Connection, *, campaign_id: str,
                           acquisitions: dict, expected_digest: str):
    """Opt an isolated RAM experiment into live scoped measurement replay.

    This is not production admission or a timestamp attestation. The caller
    supplies its frozen acquisition mapping, never a producer's admitted flag.
    Each learner access rechecks raw evidence and the named training partition.
    The context cannot be used with a file-backed canonical database.
    """
    databases = conn.execute("PRAGMA database_list").fetchall()
    if any(row[2] for row in databases) or not any(row[1] == "main" for row in databases):
        raise ValueError("scoped learning replay requires an isolated RAM database")
    if type(campaign_id) is not str or not campaign_id.strip():
        raise ValueError("scoped learning replay requires a campaign")
    if type(acquisitions) is not dict or not acquisitions:
        raise ValueError("scoped learning replay requires frozen acquisitions")
    frozen = copy.deepcopy(acquisitions)
    digest = "sha256:" + hashlib.sha256(stable_dumps(frozen).encode()).hexdigest()
    if digest != expected_digest:
        raise ValueError("scoped acquisition freeze digest mismatch")
    token = _SCOPED_REPLAY.set((conn, campaign_id, frozen))
    try:
        yield
    finally:
        _SCOPED_REPLAY.reset(token)


def _require_scoped_replay(facts) -> None:
    context = _SCOPED_REPLAY.get()
    if context is None:
        raise ValueError("scoped_execution_replay_required")
    conn, campaign_id, acquisitions = context
    if any(row[2] for row in conn.execute("PRAGMA database_list")):
        raise ValueError("scoped_replay_requires_isolated_ram_database")
    acquisition = acquisitions.get(facts.transition_id)
    if not isinstance(acquisition, dict):
        raise ValueError("scoped_transition_not_in_acquisition_freeze")
    from tehm.dataset import validate_membership_row
    row = conn.execute(
        "SELECT split, learner_eligible FROM tehm_dataset_membership "
        "WHERE transition_id=? AND campaign_id=?", (facts.transition_id, campaign_id)).fetchone()
    if row is None or validate_membership_row(row) != (True, "training"):
        raise ValueError("scoped_replay_requires_explicit_training_membership")
    if load_transition_facts(conn, facts.transition_id) != facts:
        raise ValueError("scoped_replay_facts_mismatch")
    from tehm.adapters.orfs_scoped import replay_persisted_flow_feasibility
    replay_persisted_flow_feasibility(conn, facts.transition_id, acquisition=acquisition)


def require_verified_execution(facts) -> None:
    """Require a complete executable oracle before learner-derived updates."""
    verifier = facts.verifier
    reasons: list[str] = []
    # A stored scoped receipt is not an admission certificate. Only the
    # explicit RAM experiment context may independently reconstruct it.
    if verifier.get("scoped_execution") is not None:
        try:
            _require_scoped_replay(facts)
        except (ValueError, KeyError, OSError, TypeError) as exc:
            reasons.append(str(exc))
    if verifier.get("verdict") not in {"PASS", "FAIL"}:
        reasons.append("verifier_verdict_not_definitive")
    if verifier.get("oracle_complete") is not True:
        reasons.append("oracle_incomplete")
    if verifier.get("oracle_type") in {"UNKNOWN", "COMPILE", "LINT"}:
        reasons.append("oracle_type_not_executable")
    full_oracle = verifier.get("full_oracle")
    if full_oracle is not None:
        if not isinstance(full_oracle, dict):
            reasons.append("full_oracle_malformed")
        else:
            for arm in ("before", "after"):
                payload = full_oracle.get(arm)
                if not isinstance(payload, dict) or payload.get("complete") is not True:
                    reasons.append(f"full_oracle_{arm}_incomplete")
    if reasons:
        raise ValueError(
            "online learner observation requires complete verified execution: "
            + ",".join(sorted(set(reasons))))


def require_verified_transition(conn: sqlite3.Connection,
                                transition_id: str) -> None:
    """Load and validate one canonical transition's execution witness."""
    try:
        facts = load_transition_facts(conn, transition_id)
    except (KeyError, ValueError) as exc:
        raise ValueError(
            "learner-eligible event source transition is not a valid "
            "canonical execution witness") from exc
    try:
        context = _SCOPED_REPLAY.get()
        if facts.verifier.get("scoped_execution") is not None and context is not None and context[0] is not conn:
            raise ValueError("scoped_replay_connection_mismatch")
        require_verified_execution(facts)
    except ValueError as exc:
        raise ValueError(
            "learner-eligible event source lacks complete verified execution: "
            + str(exc)) from exc


__all__ = ["require_verified_execution", "require_verified_transition", "scoped_learning_replay"]
