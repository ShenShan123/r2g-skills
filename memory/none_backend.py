"""``none`` backend: no-memory baseline (design doc 17.2).

Records execution evidence (as a receipt) but never retrieves historical
experience. Used as the M0 / no-memory experimental arm.
"""
from __future__ import annotations

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

SCHEMA_VERSION = "none-v1"


class NoneMemoryBackend:
    """No historical memory: everything is a cold start."""

    name = "none"

    def ingest_execution(self, record: ExecutionRecord) -> IngestReceipt:
        return IngestReceipt(
            record_id=record.record_id,
            outcome="UNKNOWN",
            backend="none",
        )

    def build_query(self, context: RepairContext) -> MemoryQuery:
        return MemoryQuery(query_plan={}, dominant_dimensions={}, context_ref=None)

    def retrieve(self, query: MemoryQuery, *, limit: int) -> list[MemoryCandidate]:
        return []

    def propose_activation(self, candidate: MemoryCandidate,
                           context: RepairContext) -> ActivationProposal | None:
        return None

    def record_activation(self, result: ActivationResult) -> None:
        return None

    def rebuild(self, *, frozen_source: bool = False) -> BuildReport:
        return BuildReport(backend="none", frozen_source=frozen_source,
                           rebuilt={}, ok=True, detail="no-memory backend")

    def snapshot(self) -> MemorySnapshot:
        return MemorySnapshot(
            backend="none", snapshot_id="none", schema_version=SCHEMA_VERSION,
            counts={})

    def close(self) -> None:
        return None
