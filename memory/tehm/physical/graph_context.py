"""Compact, provenance-bound context from the def-graph feature stage.

Physical Effect Memory stores the empirical downstream delta, not a second copy
of the large DEF/PyG graph.  This adapter preserves a content digest, compact
graph/topology statistics, and byte-addressed references to the authoritative
def-graph outputs.  Missing or degraded feature sets remain explicit.
"""
from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from tehm.ids import stable_dumps

GRAPH_CONTEXT_VERSION = "def-graph-feature-context-v0.2"
_FEATURE_FILES = (
    "metadata", "nodes_gate", "nodes_net", "nodes_iopin", "nodes_pin",
    "edges_gate_pin", "edges_pin_net", "edges_iopin_net",
)


@dataclass(frozen=True)
class PhysicalGraphContext:
    design: str
    platform: str
    status: str
    graph_features: dict
    topology_rows: dict
    feature_health: dict
    signoff_health: dict
    dataset_tier: str
    def_sha256: str
    feature_digests: dict
    source_refs: list[dict] = field(default_factory=list)
    extractor_version: str = GRAPH_CONTEXT_VERSION

    def identity_payload(self) -> dict:
        """Path-independent content used as the context identity."""
        return {
            "extractor_version": self.extractor_version,
            "design": self.design,
            "platform": self.platform,
            "status": self.status,
            "graph_features": self.graph_features,
            "topology_rows": self.topology_rows,
            "feature_health": self.feature_health,
            "signoff_health": self.signoff_health,
            "dataset_tier": self.dataset_tier,
            "def_sha256": self.def_sha256,
            "feature_digests": self.feature_digests,
        }

    def digest(self) -> str:
        return hashlib.sha256(
            stable_dumps(self.identity_payload()).encode()).hexdigest()

    def to_dict(self) -> dict:
        return {**self.identity_payload(), "digest": self.digest(),
                "source_refs": self.source_refs}


def load_defgraph_context(project_dir: Path, *, def_path: Path,
                          stats_path: Path | None = None) -> PhysicalGraphContext:
    """Load one completed def-graph feature extraction, fail-closed on identity.

    A partially degraded extraction is usable as context and stamped
    ``degraded``; a missing/invalid metadata table is rejected because graph
    identity and geometry would otherwise be ungrounded.
    """
    project = Path(project_dir).resolve()
    def_file = Path(def_path).resolve()
    stats_file = (Path(stats_path).resolve() if stats_path else
                  project / "reports" / "features_stats.json")
    if not def_file.is_file():
        raise FileNotFoundError(f"def-graph context DEF missing: {def_file}")
    try:
        stats = json.loads(stats_file.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"def-graph feature stats unavailable: {stats_file}") from exc
    health = stats.get("features") or {}
    if (health.get("metadata") or {}).get("status") != "ok":
        raise ValueError("def-graph metadata is not a valid completed feature set")

    metadata_path = project / "features" / "metadata.csv"
    metadata = _metadata(metadata_path)
    if not metadata:
        raise ValueError(f"def-graph metadata row missing: {metadata_path}")
    topology = {name: int((health.get(name) or {}).get("rows", 0) or 0)
                for name in _FEATURE_FILES if name != "metadata"}
    compact_health = {
        name: {k: v for k, v in (health.get(name) or {}).items()
               if k in {"status", "reason"}}
        for name in _FEATURE_FILES
    }
    statuses = [item.get("status") for item in compact_health.values()]
    status = "complete" if statuses and all(s == "ok" for s in statuses) else "degraded"
    signoff_file = project / "reports" / "signoff_gate.json"
    try:
        signoff = json.loads(signoff_file.read_text())
    except (OSError, json.JSONDecodeError):
        signoff = {"status": "unknown", "blockers": ["signoff_provenance_missing"],
                   "caveats": []}
    signoff_health = {
        "status": signoff.get("status", "unknown"),
        "blockers": list(signoff.get("blockers") or []),
        "caveats": list(signoff.get("caveats") or []),
        "mode": signoff.get("mode"),
        "def_overridden": bool(signoff.get("def_overridden")),
    }
    dataset_tier = ("strict_clean" if signoff_health["status"] == "pass"
                    else "research")

    files = {"def": def_file, "features_stats": stats_file}
    if signoff_file.is_file():
        files["signoff_gate"] = signoff_file
    files.update({name: project / "features" / f"{name}.csv"
                  for name in _FEATURE_FILES})
    digests = {name: _sha(path) for name, path in files.items() if path.is_file()}
    refs = [{"kind": name, "path": str(path.resolve()), "sha256": digests[name]}
            for name, path in files.items() if name in digests]
    return PhysicalGraphContext(
        design=str(stats.get("design") or metadata.get("graph_id") or project.name),
        platform=str(stats.get("platform") or "unknown"),
        status=status,
        graph_features={k: v for k, v in metadata.items() if k != "graph_id"},
        topology_rows=topology,
        feature_health=compact_health,
        signoff_health=signoff_health,
        dataset_tier=dataset_tier,
        def_sha256=digests["def"],
        feature_digests={k: v for k, v in digests.items() if k != "def"},
        source_refs=refs,
    )


def _metadata(path: Path) -> dict:
    try:
        with path.open(newline="") as fh:
            row = next(csv.DictReader(fh), None)
    except OSError:
        return {}
    if not row:
        return {}
    return {key: _scalar(value) for key, value in row.items()}


def _scalar(value):
    if value is None:
        return None
    text = str(value).strip()
    try:
        return float(text)
    except ValueError:
        return text


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
