"""``legacy`` backend: wraps the legacy R2G memory plane (design doc 17.2, 20.x).

This adapter exposes the legacy ``signoff-loop/knowledge`` plane behind the
MemoryBackend seam WITHOUT changing any legacy algorithm: retrieval reads the
committed ``heuristics.json`` symptom-indexed recipes read-only (exactly what
``suggest_config`` / ``diagnose_signoff_fix`` consume), and ingest shells to the
real ``ingest_run.py`` when a project dir is available.

Incremental wiring (Phase 1): the legacy *runtime* call sites (engineer_loop,
suggest_config, diagnose_signoff_fix) are NOT yet routed through this seam; they
keep calling the legacy modules directly so legacy semantics are byte-identical.
This backend exists so ``R2G_MEMORY_BACKEND=legacy`` already resolves to a real,
read-only, testable adapter.

The legacy tree is opened READ-ONLY here. TEHM never reads these files (H5).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
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

SCHEMA_VERSION = "legacy-knowledge-v1"

# <memory>/../r2g-skills/signoff-loop/knowledge
DEFAULT_KNOWLEDGE_DIR = (
    Path(__file__).resolve().parent.parent / "r2g-skills" / "signoff-loop" / "knowledge"
)


class LegacyBackendError(RuntimeError):
    pass


class LegacyMemoryBackend:
    """Read-only adapter over the legacy knowledge plane."""

    name = "legacy"

    def __init__(self, *, knowledge_dir: Path | None = None,
                 db_path: Path | None = None,
                 experiment_root: Path | None = None,
                 read_only_eval: bool = False):
        import os
        env_dir = os.environ.get("R2G_LEGACY_KNOWLEDGE_DIR")
        self.knowledge_dir = Path(knowledge_dir or env_dir or DEFAULT_KNOWLEDGE_DIR)
        self._db_path = Path(db_path) if db_path else None
        self.experiment_root = experiment_root
        self.read_only_eval = read_only_eval
        if not self.knowledge_dir.is_dir():
            raise LegacyBackendError(
                f"legacy knowledge dir not found: {self.knowledge_dir}")

    # -- resolution ----------------------------------------------------------

    def db_path(self) -> Path:
        return self._db_path or (self.knowledge_dir / "knowledge.sqlite")

    def heuristics_path(self) -> Path:
        return self.knowledge_dir / "heuristics.json"

    def schema_path(self) -> Path:
        return self.knowledge_dir / "schema.sql"

    def _sha256(self, path: Path) -> str | None:
        if not path.exists():
            return None
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    # -- protocol ------------------------------------------------------------

    def ingest_execution(self, record: ExecutionRecord) -> IngestReceipt:
        """The Protocol ingest method, fail-closed for the legacy plane.

        Legacy memory's ingest unit is a whole project RUN (``ingest_run.py``),
        not a TEHM transition, so an ExecutionRecord cannot be absorbed as-is.
        Use ``ingest_project(project_dir)`` (the real legacy path). This method
        raises rather than silently mis-ingest (H12).
        """
        raise LegacyBackendError(
            "legacy memory is project-run indexed, not transition indexed; "
            "use ingest_project(project_dir) — runtime wiring is incremental (Phase 1)")

    def ingest_project(self, project_dir: Path | str) -> IngestReceipt:
        """Run the REAL legacy ``ingest_run.py`` on a project dir (read-write).

        This is the actual legacy ingest code, so legacy semantics are preserved
        byte-for-byte; the seam only decides when to invoke it.
        """
        project = Path(project_dir)
        if not project.is_dir():
            raise LegacyBackendError(f"project dir not found: {project}")
        ingest_py = self.knowledge_dir / "ingest_run.py"
        proc = subprocess.run(
            [sys.executable, str(ingest_py), str(project),
             "--db", str(self.db_path())],
            capture_output=True, text=True, timeout=600,
            cwd=str(self.knowledge_dir))
        if proc.returncode != 0:
            raise LegacyBackendError(
                f"legacy ingest failed rc={proc.returncode}: {proc.stderr[-500:]}")
        return IngestReceipt(record_id=project.name, backend="legacy",
                             outcome="ingested")

    def build_query(self, context: RepairContext) -> MemoryQuery:
        """Compute the legacy symptom signature (read-only, symptom.py logic)."""
        signature = {}
        symptom_id = None
        if context.check and context.reports:
            signature = _symptom_signature(context)
            symptom_id = signature.get("symptom_id")
        return MemoryQuery(
            query_plan={
                "legacy_symptom_id": symptom_id,
                "diagnostic_view": "high" if symptom_id else "cold_start",
            },
            dominant_dimensions={"structural": "high", "temporal": "medium"},
            context_ref=context.design_id,
        )

    def retrieve(self, query: MemoryQuery, *, limit: int) -> list[MemoryCandidate]:
        """Symptom-indexed recipe lookup from the committed heuristics.json.

        Read-only, mirrors the legacy decision-8 relaxation
        (``diagnose_signoff_fix.load_indexed_recipe``). Empty on cold start.
        """
        symptom_id = (query.query_plan or {}).get("legacy_symptom_id")
        if not symptom_id:
            return []
        heuristics = _load_json(self.heuristics_path())
        recipes = (heuristics.get("recipes") or {}).get(symptom_id) or {}
        candidates: list[MemoryCandidate] = []
        for design_class, by_platform in recipes.items():
            for platform, entry in by_platform.items():
                if not isinstance(entry, dict):
                    continue
                for strategy in (entry.get("strategies") or []):
                    candidates.append(MemoryCandidate(
                        candidate_id=f"legacy:{symptom_id}:{design_class}:{platform}:{strategy.get('strategy', '?')}",
                        source="legacy_memory",
                        payload={
                            "symptom_id": symptom_id,
                            "design_class": design_class,
                            "platform": platform,
                            "strategy": strategy,
                            "provenance_sources": entry.get("provenance_sources"),
                        },
                        score=_strategy_score(strategy),
                        provenance={"backend": "legacy", "heuristics_generation":
                                    heuristics.get("generation")},
                    ))
                    if len(candidates) >= limit:
                        return candidates
        return candidates

    def propose_activation(self, candidate: MemoryCandidate,
                           context: RepairContext) -> ActivationProposal | None:
        """Adapt a legacy recipe candidate into an activation proposal."""
        strategy = (candidate.payload or {}).get("strategy") or {}
        if not isinstance(strategy, dict):
            return None
        return ActivationProposal(
            candidate_id=candidate.candidate_id,
            activation_id=f"act:{candidate.candidate_id}",
            applicability_status="APPLICABLE",
            binding={
                "symptom_id": (candidate.payload or {}).get("symptom_id"),
                "config_edits": strategy.get("config_edits"),
                "rerun_from": strategy.get("rerun_from"),
                "recheck": strategy.get("recheck"),
            },
            obligations=[],
        )

    def record_activation(self, result: ActivationResult) -> None:
        # Legacy writes its own recipe_status / ab_trials elsewhere; the runtime
        # call sites are not yet routed through this seam (incremental Phase 1).
        return None

    def rebuild(self, *, frozen_source: bool = False) -> BuildReport:
        # Legacy learners run via the legacy CLI (learn_heuristics.py /
        # mine_rules.py); auto-running them here would change baseline semantics.
        return BuildReport(
            backend="legacy", frozen_source=frozen_source,
            rebuilt={}, ok=False,
            detail="legacy learn() runs via its own CLI; not auto-invoked by the seam")

    def snapshot(self) -> MemorySnapshot:
        counts = {}
        if self.db_path().exists():
            try:
                conn = sqlite3.connect(f"file:{self.db_path()}?mode=ro", uri=True)
                for table in ("runs", "failure_events", "fix_events",
                              "recipe_status", "ab_trials"):
                    try:
                        counts[table] = conn.execute(
                            f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                    except Exception:  # noqa: BLE001
                        counts[table] = None
                conn.close()
            except Exception:  # noqa: BLE001
                counts["error"] = "read failed"
        return MemorySnapshot(
            backend="legacy",
            snapshot_id=f"legacy:sc:{self._sha256(self.schema_path()) or '?'}:he:{self._sha256(self.heuristics_path()) or '?'}",
            schema_version=SCHEMA_VERSION,
            counts=counts,
        )

    def close(self) -> None:
        return None


# -- helpers -----------------------------------------------------------------

def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _strategy_score(strategy: dict) -> float | None:
    """Transparent legacy Beta-prior score (mirror of fix_model, read-only)."""
    successes = strategy.get("successes")
    attempts = strategy.get("attempts")
    if isinstance(successes, (int, float)) and isinstance(attempts, (int, float)):
        wins = strategy.get("wins", 0) if isinstance(strategy.get("wins"), (int, float)) else 0
        return (successes + 0.5 * wins + 1) / (attempts + 2)
    return None


def _symptom_signature(context: RepairContext) -> dict:
    """Compute the legacy symptom_id from the repair context (read-only).

    Mirrors symptom.canonical_signature + symptom.symptom_id without importing
    the legacy module (avoids coupling the seam to legacy internals).
    """
    import hashlib

    check = context.check
    report = (context.reports or {}).get(check) or {}
    vclass = report.get("dominant_class") or _first_category(report)
    predicates = {}
    if report.get("status") and str(report.get("status")).lower() not in (
            "clean", "clean_beol", "complete", "skipped"):
        predicates["active"] = True
    signature = {"check": check, "class": vclass, "predicates": predicates}
    payload = json.dumps([check, vclass, sorted(predicates.keys())],
                         sort_keys=True, separators=(",", ":"))
    return {"signature": signature,
            "symptom_id": hashlib.sha1(payload.encode()).hexdigest()[:16]}


def _first_category(report: dict) -> str | None:
    categories = report.get("categories") or {}
    if isinstance(categories, dict) and categories:
        return max(categories, key=lambda c: (categories[c].get("count") or 0))
    return None
