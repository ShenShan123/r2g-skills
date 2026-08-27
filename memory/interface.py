"""MemoryBackend interface (design doc 17.1).

The single seam every runtime memory touchpoint goes through. The three
implementations (none / legacy / tehm) are mutually isolated: TEHM never reads
legacy authority and legacy never reads ``tehm.sqlite`` (H5/H8).
"""
from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

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


@runtime_checkable
class MemoryBackend(Protocol):
    """The memory-plane contract (design doc 17.1).

    Implementations MUST be selected once per process (factory) and MUST fail
    closed: a backend error is recorded as ``memory_unavailable`` and degrades
    to no-memory / static cold-start, never to a different backend.
    """

    name: str

    def ingest_execution(self, record: ExecutionRecord) -> IngestReceipt:
        """Absorb one real execution (a repair transition) into this backend."""
        ...

    def build_query(self, context: RepairContext) -> MemoryQuery:
        """Turn a RepairContext into a typed query plan (design doc 9.2)."""
        ...

    def retrieve(self, query: MemoryQuery, *, limit: int) -> Sequence[MemoryCandidate]:
        """Return retrieval candidates; never raises (fail-closed -> empty)."""
        ...

    def propose_activation(self, candidate: MemoryCandidate,
                           context: RepairContext) -> ActivationProposal | None:
        """Propose one activation of a candidate against the context, or None."""
        ...

    def record_activation(self, result: ActivationResult) -> None:
        """Persist an activation outcome (utility/risk update)."""
        ...

    def rebuild(self, *, frozen_source: bool = False) -> BuildReport:
        """Rebuild derived state (views/rules/heuristics) from canonical data."""
        ...

    def snapshot(self) -> MemorySnapshot:
        """Backend-scoped snapshot used by resume (design doc 17.3)."""
        ...

    def close(self) -> None:
        """Release resources. Idempotent."""
        ...
