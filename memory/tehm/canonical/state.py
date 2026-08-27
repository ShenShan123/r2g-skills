"""Canonical verified state (design doc 4.1 ``S_t``).

A state is content-addressed: identical content -> identical ``state_id``
(re-ingest dedups), any content change -> a new state. ``created_at`` and other
volatile metadata are excluded from the digest on purpose.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from tehm import SCHEMA_VERSION
from tehm.ids import stable_dumps, state_id


def source_digest(source_content: dict) -> str:
    """Digest over the raw source content of a state.

    ``source_content`` is the canonical representation of everything that makes
    up the design state at that point (config + reports + rtl slice refs +
    artifact digests), WITHOUT timestamps.
    """
    payload = stable_dumps(source_content)
    return f"src_{hashlib.sha1(payload.encode()).hexdigest()[:16]}"


@dataclass
class CanonicalState:
    """A verified design state ``S_t``.

    ``context_graph_digest`` references the semantic LocalDesignGraph artifact
    (tehm/views semantic view); ``verifier_snapshot`` is the state's toolchain /
    oracle availability snapshot (not a verdict); ``artifact_manifest`` maps
    artifact kinds -> store manifest digests.
    """

    domain: str
    project_id: str | None = None
    design_id: str | None = None
    lineage_id: str | None = None
    repository_ref: str | None = None
    source_digest: str = ""
    context_graph_digest: str = ""
    verifier_snapshot: dict = field(default_factory=dict)
    artifact_manifest: dict = field(default_factory=dict)
    created_at: str = ""
    schema_version: str = SCHEMA_VERSION

    @property
    def state_id(self) -> str:
        return state_id(
            domain=self.domain,
            source_digest=self.source_digest,
            context_graph_digest=self.context_graph_digest,
            verifier_snapshot=self.verifier_snapshot,
            artifact_manifest=self.artifact_manifest,
        )

    def to_dict(self) -> dict:
        return {
            "state_id": self.state_id,
            "domain": self.domain,
            "project_id": self.project_id,
            "design_id": self.design_id,
            "lineage_id": self.lineage_id,
            "repository_ref": self.repository_ref,
            "source_digest": self.source_digest,
            "context_graph_digest": self.context_graph_digest,
            "verifier_snapshot": self.verifier_snapshot,
            "artifact_manifest": self.artifact_manifest,
            "created_at": self.created_at,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CanonicalState":
        return cls(
            domain=str(data["domain"]),
            project_id=data.get("project_id"),
            design_id=data.get("design_id"),
            lineage_id=data.get("lineage_id"),
            repository_ref=data.get("repository_ref"),
            source_digest=str(data.get("source_digest", "")),
            context_graph_digest=str(data.get("context_graph_digest", "")),
            verifier_snapshot=dict(data.get("verifier_snapshot", {})),
            artifact_manifest=dict(data.get("artifact_manifest", {})),
            created_at=str(data.get("created_at", "")),
            schema_version=str(data.get("schema_version", SCHEMA_VERSION)),
        )

    def to_row(self) -> dict:
        from tehm.ids import stable_dumps

        return {
            "state_id": self.state_id,
            "domain": self.domain,
            "project_id": self.project_id,
            "design_id": self.design_id,
            "lineage_id": self.lineage_id,
            "repository_ref": self.repository_ref,
            "source_digest": self.source_digest,
            "context_graph_digest": self.context_graph_digest,
            "verifier_snapshot_json": stable_dumps(self.verifier_snapshot),
            "artifact_manifest_json": stable_dumps(self.artifact_manifest),
            "created_at": self.created_at,
            "schema_version": self.schema_version,
        }
