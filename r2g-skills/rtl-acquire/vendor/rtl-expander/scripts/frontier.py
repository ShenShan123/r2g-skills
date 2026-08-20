#!/usr/bin/env python3
"""SQLite frontier and stable repository identity for the RTL corpus factory."""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import re
import sqlite3
import time
import urllib.parse
from pathlib import Path
from typing import Any, Iterator


FRONTIER_SCHEMA = "rtl_frontier_v1"
PROVIDER_COOLDOWN_SCHEMA = "rtl_provider_cooldown_v1"
KNOWN_HOSTS = {
    "github.com": "github",
    "gitlab.com": "gitlab",
    "codeberg.org": "codeberg",
    "gitee.com": "gitee",
}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def digest(value: str, length: int = 32) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:length]


def canonical_repository_identity(url: str, provider_hint: str | None = None) -> dict[str, str]:
    value = url.strip()
    if not value:
        raise ValueError("empty repository URL")
    scp = re.fullmatch(r"(?:[^@/]+@)?([^:/]+):(.+)", value)
    if scp and "://" not in value:
        host, path = scp.group(1), scp.group(2)
    else:
        parsed = urllib.parse.urlsplit(value if "://" in value else "https://" + value)
        host, path = parsed.hostname or "", parsed.path
    host = host.lower().removeprefix("www.")
    clean_path = urllib.parse.unquote(path).strip("/")
    clean_path = re.sub(r"\.git$", "", clean_path, flags=re.I)
    parts = [part for part in clean_path.split("/") if part]
    if len(parts) < 2:
        raise ValueError(f"repository URL lacks namespace/name: {url}")
    provider = (provider_hint or KNOWN_HOSTS.get(host) or host.split(".")[0]).lower()
    if provider in {"github", "codeberg", "gitee"}:
        parts = parts[:2]
    elif provider == "gitlab" and "-" in parts:
        parts = parts[:parts.index("-")]
    if len(parts) < 2:
        raise ValueError(f"repository URL does not identify a repository root: {url}")
    if any(not re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in parts):
        raise ValueError(f"repository URL contains an invalid namespace/name slug: {url}")
    if provider == "github" and parts[0].lower() in {"user-attachments", "settings", "features", "topics", "marketplace"}:
        raise ValueError(f"GitHub URL is not a repository: {url}")
    namespace = "/".join(parts[:-1]).lower()
    repo_name = parts[-1].lower()
    repository_key = f"{provider}:{namespace}/{repo_name}"
    canonical_host = next((name for name, kind in KNOWN_HOSTS.items() if kind == provider), host)
    canonical_url = f"https://{canonical_host}/{namespace}/{repo_name}"
    return {
        "repository_key": repository_key,
        "provider": provider,
        "namespace": namespace,
        "repo_name": repo_name,
        "canonical_url": canonical_url,
    }


def repository_revision_key(repository_key: str, commit_sha: str) -> str:
    revision = commit_sha.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40,64}", revision):
        raise ValueError(f"revision is not an immutable commit hash: {commit_sha}")
    return f"{repository_key}@{revision}"


SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=30000;
CREATE TABLE IF NOT EXISTS repositories (
  repository_key TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  namespace TEXT NOT NULL,
  repo_name TEXT NOT NULL,
  canonical_url TEXT NOT NULL UNIQUE,
  default_branch TEXT,
  state TEXT NOT NULL DEFAULT 'DISCOVERED',
  priority REAL NOT NULL DEFAULT 0,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  acquired_revision TEXT,
  acquisition_status TEXT NOT NULL DEFAULT 'NOT_ACQUIRED',
  discovery_score REAL NOT NULL DEFAULT 0,
  diversity_score REAL NOT NULL DEFAULT 0,
  design_likelihood REAL NOT NULL DEFAULT 0,
  estimated_cost REAL NOT NULL DEFAULT 0,
  license_hint TEXT,
  size_bytes INTEGER,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  next_retry_at TEXT,
  claimed_by TEXT,
  claim_started_at TEXT
);
CREATE INDEX IF NOT EXISTS repositories_queue_idx
  ON repositories(state, acquisition_status, priority DESC, first_seen);
CREATE TABLE IF NOT EXISTS discovery_events (
  event_id TEXT PRIMARY KEY,
  repository_key TEXT NOT NULL REFERENCES repositories(repository_key),
  provider TEXT NOT NULL,
  strategy TEXT NOT NULL,
  query_id TEXT,
  source_repository_key TEXT,
  evidence TEXT,
  discovered_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS queries (
  query_id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  strategy TEXT NOT NULL,
  query_text TEXT NOT NULL,
  cursor TEXT,
  state TEXT NOT NULL DEFAULT 'READY',
  priority REAL NOT NULL DEFAULT 0,
  budget INTEGER NOT NULL DEFAULT 0,
  attempts INTEGER NOT NULL DEFAULT 0,
  results_seen INTEGER NOT NULL DEFAULT 0,
  new_repositories INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  next_run_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(provider, strategy, query_text)
);
CREATE TABLE IF NOT EXISTS repo_edges (
  edge_id TEXT PRIMARY KEY,
  source_repository_key TEXT NOT NULL,
  target_repository_key TEXT NOT NULL,
  edge_type TEXT NOT NULL,
  evidence TEXT,
  discovered_at TEXT NOT NULL,
  UNIQUE(source_repository_key, target_repository_key, edge_type)
);
CREATE TABLE IF NOT EXISTS acquisition_attempts (
  attempt_id TEXT PRIMARY KEY,
  repository_key TEXT NOT NULL REFERENCES repositories(repository_key),
  repository_revision_key TEXT,
  method TEXT NOT NULL,
  state TEXT NOT NULL,
  started_at TEXT NOT NULL,
  completed_at TEXT,
  bytes_downloaded INTEGER NOT NULL DEFAULT 0,
  files_extracted INTEGER NOT NULL DEFAULT 0,
  artifact_path TEXT,
  error_class TEXT,
  error_detail TEXT
);
CREATE TABLE IF NOT EXISTS provider_state (
  provider TEXT PRIMARY KEY,
  cursor TEXT,
  rate_limit_remaining INTEGER,
  rate_limit_reset TEXT,
  backoff_until TEXT,
  state_json TEXT NOT NULL DEFAULT '{}',
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_yield (
  source_key TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  strategy TEXT NOT NULL,
  candidates INTEGER NOT NULL DEFAULT 0,
  acquired INTEGER NOT NULL DEFAULT 0,
  new_design_instances INTEGER NOT NULL DEFAULT 0,
  new_families INTEGER NOT NULL DEFAULT 0,
  synthesis_valid_families INTEGER NOT NULL DEFAULT 0,
  cpu_hours REAL NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scheduler_state (
  key TEXT PRIMARY KEY,
  value_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS repository_revisions (
  repository_revision_key TEXT PRIMARY KEY,
  repository_key TEXT NOT NULL REFERENCES repositories(repository_key),
  commit_sha TEXT NOT NULL,
  archive_sha256 TEXT,
  source_path TEXT NOT NULL,
  acquired_at TEXT NOT NULL,
  UNIQUE(repository_key, commit_sha)
);
CREATE TABLE IF NOT EXISTS graph_expansions (
  repository_key TEXT PRIMARY KEY REFERENCES repositories(repository_key),
  state TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS round_acquisition_budget (
  round_id TEXT PRIMARY KEY,
  revision_target INTEGER NOT NULL,
  exploration_cap INTEGER NOT NULL,
  exploration_fraction REAL NOT NULL,
  round_started_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS acquisition_executor_budget (
  executor_id TEXT PRIMARY KEY,
  round_id TEXT NOT NULL REFERENCES round_acquisition_budget(round_id),
  max_attempts INTEGER NOT NULL,
  attempts_claimed INTEGER NOT NULL DEFAULT 0,
  state TEXT NOT NULL DEFAULT 'ACTIVE',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS round_acquisition_claims (
  round_id TEXT NOT NULL REFERENCES round_acquisition_budget(round_id),
  repository_key TEXT NOT NULL REFERENCES repositories(repository_key),
  acquisition_lane TEXT NOT NULL,
  size_lane TEXT NOT NULL,
  executor_id TEXT REFERENCES acquisition_executor_budget(executor_id),
  worker_id TEXT,
  claim_state TEXT NOT NULL,
  result_state TEXT,
  claimed_at TEXT NOT NULL,
  completed_at TEXT,
  PRIMARY KEY(round_id, repository_key)
);
CREATE INDEX IF NOT EXISTS round_acquisition_claims_budget_idx
  ON round_acquisition_claims(round_id, acquisition_lane, claim_state);
CREATE INDEX IF NOT EXISTS round_acquisition_claims_executor_idx
  ON round_acquisition_claims(executor_id, claim_state);
"""


class FrontierDB:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=30)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA_SQL)
        self._migrate_schema()
        self.connection.execute(f"PRAGMA user_version={int(digest(FRONTIER_SCHEMA, 6), 16) % 2147483647}")
        self.connection.commit()

    def _migrate_schema(self) -> None:
        """Apply idempotent additive frontier migrations before live use."""
        executor_columns = {
            str(row[1]) for row in self.connection.execute(
                "PRAGMA table_info(acquisition_executor_budget)"
            ).fetchall()
        }
        if "attempts_claimed" not in executor_columns:
            self.connection.execute(
                """ALTER TABLE acquisition_executor_budget
                   ADD COLUMN attempts_claimed INTEGER NOT NULL DEFAULT 0"""
            )

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "FrontierDB":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    @contextlib.contextmanager
    def immediate(self) -> Iterator[sqlite3.Connection]:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield self.connection
        except Exception:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

    def add_query(self, provider: str, strategy: str, query_text: str, priority: float, budget: int) -> str:
        query_id = "q_" + digest(f"{provider}\0{strategy}\0{query_text}")
        now = utc_now()
        self.connection.execute(
            """INSERT INTO queries(query_id,provider,strategy,query_text,priority,budget,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(provider,strategy,query_text) DO UPDATE SET
               priority=MAX(priority,excluded.priority), budget=MAX(budget,excluded.budget), updated_at=excluded.updated_at""",
            (query_id, provider, strategy, query_text, priority, budget, now, now),
        )
        self.connection.commit()
        return query_id

    def upsert_repository(
        self, candidate: dict[str, Any], strategy: str, query_id: str | None = None,
        source_repository_key: str | None = None, priority: float = 0,
    ) -> tuple[str, bool]:
        identity = canonical_repository_identity(candidate["url"], candidate.get("provider"))
        now = utc_now()
        metadata = dict(candidate)
        metadata.pop("url", None)
        existing_row = self.connection.execute(
            "SELECT metadata_json FROM repositories WHERE repository_key=?", (identity["repository_key"],)
        ).fetchone()
        existed = existing_row is not None
        if existing_row:
            try:
                previous = json.loads(existing_row["metadata_json"] or "{}")
            except json.JSONDecodeError:
                previous = {}
            previous.update({key: value for key, value in metadata.items() if value not in (None, "", [], {})})
            metadata = previous
        self.connection.execute(
            """INSERT INTO repositories(repository_key,provider,namespace,repo_name,canonical_url,default_branch,
               state,priority,first_seen,last_seen,discovery_score,diversity_score,design_likelihood,
               estimated_cost,license_hint,size_bytes,metadata_json)
               VALUES(?,?,?,?,?,?, 'FRONTIER',?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(repository_key) DO UPDATE SET last_seen=excluded.last_seen,
               priority=MAX(repositories.priority,excluded.priority),
               discovery_score=MAX(repositories.discovery_score,excluded.discovery_score),
               diversity_score=MAX(repositories.diversity_score,excluded.diversity_score),
               design_likelihood=MAX(repositories.design_likelihood,excluded.design_likelihood),
               default_branch=COALESCE(repositories.default_branch,excluded.default_branch),
               size_bytes=COALESCE(repositories.size_bytes,excluded.size_bytes),
               metadata_json=excluded.metadata_json""",
            (
                identity["repository_key"], identity["provider"], identity["namespace"], identity["repo_name"],
                identity["canonical_url"], candidate.get("default_branch"), priority, now, now,
                float(candidate.get("discovery_score", 0)), float(candidate.get("diversity_score", 0)),
                float(candidate.get("design_likelihood", 0)), float(candidate.get("estimated_cost", 0)),
                candidate.get("license_hint"), candidate.get("size_bytes"), json.dumps(metadata, sort_keys=True),
            ),
        )
        event_material = json.dumps(
            [identity["repository_key"], identity["provider"], strategy, query_id, source_repository_key, candidate.get("evidence")],
            sort_keys=True,
        )
        self.connection.execute(
            """INSERT OR IGNORE INTO discovery_events(event_id,repository_key,provider,strategy,query_id,
               source_repository_key,evidence,discovered_at) VALUES(?,?,?,?,?,?,?,?)""",
            ("evt_" + digest(event_material), identity["repository_key"], identity["provider"], strategy,
             query_id, source_repository_key, candidate.get("evidence"), now),
        )
        self.connection.commit()
        return identity["repository_key"], not existed

    def add_edge(self, source: str, target_url: str, edge_type: str, evidence: str = "") -> str:
        target = canonical_repository_identity(target_url)
        if target["repository_key"] == source:
            return source
        edge_priority = {"upstream": 3.0, "fork": 2.5, "submodule": 3.0, "dependency": 2.5, "organization_sibling": 1.5, "readme_reference": 0.75}.get(edge_type, 0.5)
        self.upsert_repository({"url": target_url, "provider": target["provider"], "evidence": evidence}, edge_type, source_repository_key=source, priority=edge_priority)
        edge_id = "edge_" + digest(f"{source}\0{target['repository_key']}\0{edge_type}")
        self.connection.execute(
            "INSERT OR IGNORE INTO repo_edges(edge_id,source_repository_key,target_repository_key,edge_type,evidence,discovered_at) VALUES(?,?,?,?,?,?)",
            (edge_id, source, target["repository_key"], edge_type, evidence, utc_now()),
        )
        self.connection.commit()
        return target["repository_key"]

    def reprioritize_from_edges(self) -> int:
        weights = {"upstream": 3.0, "fork": 2.5, "submodule": 3.0, "dependency": 2.5, "organization_sibling": 1.5, "readme_reference": 0.75}
        changed = 0
        with self.immediate() as connection:
            for edge_type, priority in weights.items():
                changed += connection.execute(
                    """UPDATE repositories SET priority=MAX(priority,?) WHERE repository_key IN
                       (SELECT target_repository_key FROM repo_edges WHERE edge_type=?)""",
                    (priority, edge_type),
                ).rowcount
        return changed

    def discovery_precision_policy(self) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT value_json FROM scheduler_state WHERE key='discovery_precision_policy'"
        ).fetchone()
        if not row:
            return None
        try:
            value = json.loads(row[0])
        except (TypeError, json.JSONDecodeError):
            return None
        return value if value.get("status") == "ACTIVE" else None

    def _verified_rtl_repository_keys(self) -> set[str]:
        """Return repositories proven to contain usable HDL by processing."""
        manifest = self.path.parent.parent / "manifests/repositories.jsonl"
        verified: set[str] = set()
        if not manifest.is_file():
            return verified
        for line in manifest.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
                if row.get("classification") in {None, "NO_RTL"} or row.get("state") == "NO_RTL":
                    continue
                url = str(row.get("repository_url") or "")
                if url and url != "UNKNOWN":
                    verified.add(canonical_repository_identity(url)["repository_key"])
            except (json.JSONDecodeError, ValueError):
                continue
        return verified

    def reprioritize_hardware_likelihood(self, precision_policy: dict[str, Any] | None = None) -> int:
        # Rebuild likelihood from all persisted evidence.  Missing metadata is
        # neutral; query and graph evidence must survive future reprioritization.
        from discovery_evidence import effective_provider_query, score_discovery_evidence

        rows = self.connection.execute("SELECT repository_key,namespace,repo_name,priority,metadata_json FROM repositories WHERE state='FRONTIER'").fetchall()
        events: dict[str, list[tuple[str, str]]] = {}
        for event in self.connection.execute(
            """SELECT de.repository_key,de.provider,de.strategy,COALESCE(q.query_text,'') AS query_text
               FROM discovery_events de LEFT JOIN queries q ON q.query_id=de.query_id"""
        ).fetchall():
            events.setdefault(str(event["repository_key"]), []).append((
                str(event["provider"]), str(event["strategy"]), str(event["query_text"]),
            ))
        verified_sources = self._verified_rtl_repository_keys() if precision_policy else set()
        verified_graph_targets: dict[str, str] = {}
        graph_anchor = {
            "dependency": "VERIFIED_RTL_DEPENDENCY",
            "submodule": "VERIFIED_RTL_SUBMODULE",
            "readme_reference": "VERIFIED_RTL_PROJECT_REFERENCE",
        }
        if precision_policy:
            for edge in self.connection.execute(
                "SELECT source_repository_key,target_repository_key,edge_type FROM repo_edges"
            ).fetchall():
                if str(edge["source_repository_key"]) not in verified_sources:
                    continue
                kind = graph_anchor.get(str(edge["edge_type"]))
                if kind:
                    verified_graph_targets[str(edge["target_repository_key"])] = kind
        else:
            verified_graph_targets = {
                str(row[0]): "VERIFIED_RTL_GRAPH_NEIGHBOR" for row in self.connection.execute(
                    """SELECT DISTINCT e.target_repository_key FROM repo_edges e
                       JOIN repositories source ON source.repository_key=e.source_repository_key
                       WHERE source.acquired_revision IS NOT NULL OR source.acquisition_status='ACQUIRED'"""
                ).fetchall()
            }
        changed = 0
        for row in rows:
            try:
                metadata = json.loads(row["metadata_json"] or "{}")
            except json.JSONDecodeError:
                metadata = {}
            candidate = dict(metadata)
            candidate.setdefault("url", f"{row['namespace']}/{row['repo_name']}")
            # Events and edges are the durable source of truth.  Recompute from
            # them so policy calibration can lower an old over-broad score.
            evidence = None
            contexts = events.get(str(row["repository_key"]), [("", "", "")])
            for provider, strategy, query_text in contexts:
                evidence = score_discovery_evidence(
                    candidate, query_text=effective_provider_query(provider, query_text), strategy=strategy,
                    graph_source_trusted=str(row["repository_key"]) in verified_graph_targets,
                    graph_evidence_kind=verified_graph_targets.get(str(row["repository_key"])),
                    precision_policy=precision_policy, existing=evidence,
                )
            metadata["discovery_evidence"] = evidence
            likelihood = float(evidence["score"])
            priority = max(float(row["priority"]), float(evidence.get("priority_bonus", 0.0)))
            self.connection.execute(
                "UPDATE repositories SET design_likelihood=?,priority=?,metadata_json=? WHERE repository_key=?",
                (likelihood, priority, json.dumps(metadata, sort_keys=True), row["repository_key"]),
            )
            changed += 1
        self.connection.commit()
        return changed

    def quarantine_malformed_repositories(self) -> int:
        patterns = ["%/blob/%", "%/tree/%", "%/raw/%", "%/-/blob/%", "%/-/tree/%"]
        with self.immediate() as connection:
            changed = 0
            for pattern in patterns:
                changed += connection.execute(
                    """UPDATE repositories SET state='INVALID',acquisition_status='EXCLUDED',
                       claimed_by=NULL,claim_started_at=NULL WHERE repository_key LIKE ? AND state!='INVALID'""",
                    (pattern,),
                ).rowcount
            changed += connection.execute(
                """UPDATE repositories SET state='INVALID',acquisition_status='EXCLUDED',
                   claimed_by=NULL,claim_started_at=NULL
                   WHERE provider IN ('github','codeberg','gitee')
                   AND repository_key GLOB '*:*/*/*' AND state!='INVALID'"""
            ).rowcount
            changed += connection.execute(
                """UPDATE repositories SET state='INVALID',acquisition_status='EXCLUDED',
                   claimed_by=NULL,claim_started_at=NULL
                   WHERE repository_key LIKE 'github:user-attachments/%' AND state!='INVALID'"""
            ).rowcount
            for row in connection.execute("SELECT repository_key,canonical_url FROM repositories WHERE state!='INVALID'").fetchall():
                try:
                    canonical_repository_identity(row["canonical_url"], row["provider"] if "provider" in row.keys() else None)
                except ValueError:
                    changed += connection.execute(
                        """UPDATE repositories SET state='INVALID',acquisition_status='EXCLUDED',
                           claimed_by=NULL,claim_started_at=NULL WHERE repository_key=?""",
                        (row["repository_key"],),
                    ).rowcount
        return changed

    def ready_queries(self, limit: int, providers: list[str] | None = None, query_texts: list[str] | None = None) -> list[dict[str, Any]]:
        clauses = ["state IN ('READY','RETRY')", "(next_run_at IS NULL OR next_run_at<=?)"]
        values: list[Any] = [utc_now()]
        if providers:
            clauses.append(f"provider IN ({','.join('?' for _ in providers)})")
            values.extend(providers)
        if query_texts:
            clauses.append(f"query_text IN ({','.join('?' for _ in query_texts)})")
            values.extend(query_texts)
        values.append(limit)
        rows = self.connection.execute(
            f"SELECT * FROM queries WHERE {' AND '.join(clauses)} ORDER BY priority DESC, created_at LIMIT ?",
            values,
        ).fetchall()
        return [dict(row) for row in rows]

    def update_query(self, query_id: str, **values: Any) -> None:
        allowed = {"cursor", "state", "attempts", "results_seen", "new_repositories", "last_error", "next_run_at"}
        fields = {key: value for key, value in values.items() if key in allowed}
        fields["updated_at"] = utc_now()
        assignments = ",".join(f"{key}=?" for key in fields)
        self.connection.execute(f"UPDATE queries SET {assignments} WHERE query_id=?", (*fields.values(), query_id))
        self.connection.commit()

    def update_provider_state(self, provider: str, **values: Any) -> None:
        # Parallel acquisition workers can report a provider canary at nearly
        # the same instant.  Keep this as a short, retried write transaction so
        # a harmless status refresh cannot terminate an acquisition worker.
        for attempt in range(8):
            try:
                with self.immediate() as connection:
                    now = utc_now()
                    current = connection.execute(
                        "SELECT * FROM provider_state WHERE provider=?", (provider,)
                    ).fetchone()
                    merged = dict(current) if current else {"provider": provider, "state_json": "{}"}
                    merged.update(values)
                    connection.execute(
                        """INSERT INTO provider_state(provider,cursor,rate_limit_remaining,rate_limit_reset,backoff_until,state_json,updated_at)
                           VALUES(?,?,?,?,?,?,?) ON CONFLICT(provider) DO UPDATE SET cursor=excluded.cursor,
                           rate_limit_remaining=excluded.rate_limit_remaining, rate_limit_reset=excluded.rate_limit_reset,
                           backoff_until=excluded.backoff_until, state_json=excluded.state_json, updated_at=excluded.updated_at""",
                        (provider, merged.get("cursor"), merged.get("rate_limit_remaining"),
                         merged.get("rate_limit_reset"), merged.get("backoff_until"),
                         merged.get("state_json", "{}"), now),
                    )
                return
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == 7:
                    raise
                time.sleep(0.05 * (attempt + 1))

    @staticmethod
    def quota_provider(provider: str) -> str:
        # FuseSoC discovery uses GitHub's API and therefore shares its quota.
        return "github" if provider == "fusesoc" else provider

    def set_provider_rate_limit(
        self, provider: str, *, retry_after: int = 0, reset_at: str | None = None,
        source: str = "provider", detail: str = "",
    ) -> dict[str, Any]:
        quota_provider = self.quota_provider(provider)
        now = dt.datetime.now(dt.timezone.utc)
        if reset_at:
            try:
                reset = dt.datetime.fromisoformat(reset_at.replace("Z", "+00:00"))
                if reset.tzinfo is None:
                    reset = reset.replace(tzinfo=dt.timezone.utc)
            except ValueError:
                reset = now + dt.timedelta(seconds=max(60, retry_after))
        else:
            reset = now + dt.timedelta(seconds=max(60, retry_after))
        reset_at = reset.replace(microsecond=0).isoformat()
        retry_after = max(0, int((reset - now).total_seconds()))
        evidence = {
            "schema": PROVIDER_COOLDOWN_SCHEMA,
            "status": "RATE_LIMITED",
            "provider": quota_provider,
            "reported_by": provider,
            "retry_after_seconds": retry_after,
            "reset_at": reset_at,
            "source": source,
            "detail": detail[:1000],
            "updated_at": utc_now(),
        }
        self.update_provider_state(
            quota_provider, backoff_until=reset_at, rate_limit_reset=reset_at,
            rate_limit_remaining=0, state_json=json.dumps(evidence, sort_keys=True),
        )
        return evidence

    def record_provider_success(
        self, provider: str, *, rate_limit_remaining: int | None = None,
        rate_limit_reset: str | None = None, source: str = "provider",
    ) -> None:
        quota_provider = self.quota_provider(provider)
        current = self.connection.execute(
            "SELECT * FROM provider_state WHERE provider=?", (quota_provider,)
        ).fetchone()
        evidence = {
            "schema": PROVIDER_COOLDOWN_SCHEMA,
            "status": "HEALTHY",
            "provider": quota_provider,
            "source": source,
            "updated_at": utc_now(),
        }
        # A successful acquisition canary is stronger evidence than an expired
        # quota snapshot.  Do not preserve a stale remaining=0 forever when the
        # archive/revision endpoint has just succeeded without quota headers.
        canary_recovered = source.startswith("acquisition_canary")
        self.update_provider_state(
            quota_provider,
            backoff_until=None,
            rate_limit_remaining=(
                rate_limit_remaining if rate_limit_remaining is not None
                else (None if canary_recovered else (current["rate_limit_remaining"] if current else None))
            ),
            rate_limit_reset=(
                rate_limit_reset if rate_limit_reset is not None
                else (None if canary_recovered else (current["rate_limit_reset"] if current else None))
            ),
            state_json=json.dumps(evidence, sort_keys=True),
        )

    def provider_statuses(
        self, providers: list[str], quota_reserve: int = 100,
    ) -> dict[str, dict[str, Any]]:
        now = dt.datetime.now(dt.timezone.utc)
        result: dict[str, dict[str, Any]] = {}
        for provider in providers:
            quota_provider = self.quota_provider(provider)
            row = self.connection.execute(
                "SELECT * FROM provider_state WHERE provider=?", (quota_provider,)
            ).fetchone()
            data: dict[str, Any] = {}
            if row:
                try:
                    data = json.loads(row["state_json"] or "{}")
                except json.JSONDecodeError:
                    data = {}
            backoff_until = str(row["backoff_until"]) if row and row["backoff_until"] else None
            cooldown = False
            if backoff_until:
                try:
                    cooldown = dt.datetime.fromisoformat(backoff_until.replace("Z", "+00:00")) > now
                except ValueError:
                    cooldown = False
            remaining = row["rate_limit_remaining"] if row else None
            rate_reset_at = None
            rate_reset_future = False
            if row and row["rate_limit_reset"]:
                try:
                    raw_reset = str(row["rate_limit_reset"])
                    reset = (
                        dt.datetime.fromtimestamp(int(float(raw_reset)), dt.timezone.utc)
                        if re.fullmatch(r"\d+(?:\.\d+)?", raw_reset)
                        else dt.datetime.fromisoformat(raw_reset.replace("Z", "+00:00"))
                    )
                    if reset.tzinfo is None:
                        reset = reset.replace(tzinfo=dt.timezone.utc)
                    rate_reset_at = reset.replace(microsecond=0).isoformat()
                    rate_reset_future = reset > now
                except (TypeError, ValueError, OverflowError, OSError):
                    pass
            successful_expired_canary = (
                remaining == 0
                and not rate_reset_future
                and data.get("status") == "HEALTHY"
                and str(data.get("source", "")).startswith("acquisition_canary")
            )
            if successful_expired_canary:
                # v4.3.1 migration: an acquisition canary already proved that
                # the shared quota provider recovered.  Persistently discard
                # the older zero/reset snapshot instead of requiring another
                # eligible candidate to repeat the canary.
                data = {
                    **data,
                    "schema": PROVIDER_COOLDOWN_SCHEMA,
                    "status": "HEALTHY",
                    "provider": quota_provider,
                    "recovered_from_expired_zero_quota": True,
                    "updated_at": utc_now(),
                }
                self.update_provider_state(
                    quota_provider, backoff_until=None,
                    rate_limit_remaining=None, rate_limit_reset=None,
                    state_json=json.dumps(data, sort_keys=True),
                )
                remaining = None
                rate_reset_at = None
                backoff_until = None
                cooldown = False
            if remaining == 0 and rate_reset_future:
                cooldown = True
            if cooldown:
                status = "RATE_LIMITED"
            elif remaining == 0 or data.get("status") == "RATE_LIMITED":
                status = "CANARY_READY"
            elif remaining is not None and 0 < int(remaining) <= max(0, quota_reserve) and rate_reset_future:
                status = "QUOTA_RESERVED"
            else:
                status = "HEALTHY"
            effective_reset = backoff_until or rate_reset_at
            result[provider] = {
                "provider": provider,
                "quota_provider": quota_provider,
                "status": status,
                "cooldown": cooldown,
                "retry_after_seconds": max(
                    0, int((dt.datetime.fromisoformat(effective_reset.replace("Z", "+00:00")) - now).total_seconds())
                ) if status in {"RATE_LIMITED", "QUOTA_RESERVED"} and effective_reset else 0,
                "reset_at": effective_reset if status != "HEALTHY" else None,
                "rate_limit_remaining": remaining,
                "rate_limit_reset": (
                    None if successful_expired_canary
                    else (row["rate_limit_reset"] if row else None)
                ),
                "evidence": data,
            }
        return result

    def acquisition_eligible_providers(self, providers: list[str]) -> list[str]:
        statuses = self.provider_statuses(providers)
        return [provider for provider in providers if statuses[provider]["status"] != "RATE_LIMITED"]

    def discovery_eligible_providers(self, providers: list[str], quota_reserve: int = 100) -> list[str]:
        statuses = self.provider_statuses(providers, quota_reserve)
        return [provider for provider in providers if statuses[provider]["status"] == "HEALTHY"]

    def record_source_yield(self, provider: str, strategy: str, query_text: str, candidates: int, acquired: int = 0) -> None:
        source_key = f"{provider}:{strategy}:{query_text}"
        now = utc_now()
        self.connection.execute(
            """INSERT INTO source_yield(source_key,provider,strategy,candidates,acquired,updated_at)
               VALUES(?,?,?,?,?,?) ON CONFLICT(source_key) DO UPDATE SET
               candidates=source_yield.candidates+excluded.candidates,
               acquired=source_yield.acquired+excluded.acquired,updated_at=excluded.updated_at""",
            (source_key, provider, strategy, candidates, acquired, now),
        )
        self.connection.commit()

    def initialize_round_acquisition_budget(
        self, round_id: str, revision_target: int, exploration_fraction: float,
        round_started_at: str,
    ) -> dict[str, Any]:
        """Create/reconcile one restart-safe round-wide acquisition budget."""
        if not round_id:
            raise ValueError("round acquisition budget requires a round_id")
        if revision_target <= 0:
            raise ValueError("round acquisition budget requires a positive revision target")
        fraction = max(0.0, min(0.20, float(exploration_fraction)))
        exploration_cap = int(revision_target * fraction)
        now = utc_now()
        with self.immediate() as connection:
            existing = connection.execute(
                "SELECT * FROM round_acquisition_budget WHERE round_id=?", (round_id,)
            ).fetchone()
            if existing:
                identity = (
                    int(existing["revision_target"]), int(existing["exploration_cap"]),
                    str(existing["round_started_at"]),
                )
                requested = (revision_target, exploration_cap, round_started_at)
                if identity != requested:
                    raise ValueError("saved round acquisition budget identity differs from requested policy")
            else:
                connection.execute(
                    """INSERT INTO round_acquisition_budget(
                         round_id,revision_target,exploration_cap,exploration_fraction,
                         round_started_at,created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?)""",
                    (round_id, revision_target, exploration_cap, fraction,
                     round_started_at, now, now),
                )

            # Hot-upgrade/bootstrap: retain every revision already acquired in
            # this round and classify its first successful attempt.  This never
            # revokes data; it only prevents future claims from resetting the cap.
            acquired_rows = connection.execute(
                """SELECT rr.repository_key,rr.repository_revision_key,
                          MIN(a.completed_at) AS completed_at
                   FROM repository_revisions rr
                   JOIN acquisition_attempts a
                     ON a.repository_revision_key=rr.repository_revision_key
                    AND a.state='ACQUIRED'
                   WHERE rr.acquired_at>=?
                   GROUP BY rr.repository_key,rr.repository_revision_key""",
                (round_started_at,),
            ).fetchall()
            for acquired in acquired_rows:
                attempt = connection.execute(
                    """SELECT method FROM acquisition_attempts
                       WHERE repository_revision_key=? AND state='ACQUIRED'
                       ORDER BY completed_at,started_at LIMIT 1""",
                    (acquired["repository_revision_key"],),
                ).fetchone()
                lane = "exploration" if attempt and "exploration" in str(attempt["method"]) else "production"
                connection.execute(
                    """INSERT OR IGNORE INTO round_acquisition_claims(
                         round_id,repository_key,acquisition_lane,size_lane,executor_id,
                         worker_id,claim_state,result_state,claimed_at,completed_at
                       ) VALUES(?,?,?,'legacy',NULL,NULL,'ACQUIRED','ACQUIRED',?,?)""",
                    (round_id, acquired["repository_key"], lane,
                     acquired["completed_at"] or round_started_at,
                     acquired["completed_at"] or round_started_at),
                )
        return self.round_acquisition_budget_status(round_id)

    def initialize_acquisition_executor(
        self, executor_id: str, round_id: str, max_attempts: int,
    ) -> None:
        if not executor_id or max_attempts <= 0:
            raise ValueError("executor budget requires an id and positive max_attempts")
        now = utc_now()
        with self.immediate() as connection:
            existing = connection.execute(
                "SELECT round_id,max_attempts FROM acquisition_executor_budget WHERE executor_id=?",
                (executor_id,),
            ).fetchone()
            if existing and (str(existing["round_id"]), int(existing["max_attempts"])) != (round_id, max_attempts):
                raise ValueError("saved acquisition executor identity differs from requested policy")
            connection.execute(
                """INSERT INTO acquisition_executor_budget(
                     executor_id,round_id,max_attempts,attempts_claimed,state,created_at,updated_at
                   ) VALUES(?,?,?,0,'ACTIVE',?,?)
                   ON CONFLICT(executor_id) DO UPDATE SET state='ACTIVE',updated_at=excluded.updated_at""",
                (executor_id, round_id, max_attempts, now, now),
            )

    def finish_acquisition_executor(self, executor_id: str, state: str) -> None:
        with self.immediate() as connection:
            connection.execute(
                "UPDATE acquisition_executor_budget SET state=?,updated_at=? WHERE executor_id=?",
                (state, utc_now(), executor_id),
            )

    def round_acquisition_budget_status(self, round_id: str) -> dict[str, Any]:
        budget = self.connection.execute(
            "SELECT * FROM round_acquisition_budget WHERE round_id=?", (round_id,)
        ).fetchone()
        if not budget:
            return {}
        counts = {
            (str(row["acquisition_lane"]), str(row["claim_state"])): int(row["count"])
            for row in self.connection.execute(
                """SELECT acquisition_lane,claim_state,COUNT(*) AS count
                   FROM round_acquisition_claims WHERE round_id=?
                   GROUP BY acquisition_lane,claim_state""",
                (round_id,),
            ).fetchall()
        }
        production_acquired = counts.get(("production", "ACQUIRED"), 0)
        exploration_acquired = counts.get(("exploration", "ACQUIRED"), 0)
        production_active = counts.get(("production", "ACTIVE"), 0)
        exploration_active = counts.get(("exploration", "ACTIVE"), 0)
        cap = int(budget["exploration_cap"])
        low_value_cap = max(1, int(cap * 0.20)) if cap else 0
        low_value_used = int(self.connection.execute(
            """SELECT COUNT(*) FROM round_acquisition_claims c
               JOIN repositories r ON r.repository_key=c.repository_key
               WHERE c.round_id=? AND c.acquisition_lane='exploration'
                 AND c.claim_state IN ('ACTIVE','ACQUIRED')
                 AND COALESCE(json_extract(r.metadata_json,
                     '$.discovery_evidence.admission_anchor'),'UNANCHORED')='GRAPH_ONLY'""",
            (round_id,),
        ).fetchone()[0])
        return {
            "schema": "rtl_round_acquisition_budget_v1",
            "round_id": round_id,
            "revision_target": int(budget["revision_target"]),
            "exploration_fraction": float(budget["exploration_fraction"]),
            "exploration_cap": cap,
            "low_value_exploration_cap": low_value_cap,
            "low_value_exploration_used": low_value_used,
            "production_acquired": production_acquired,
            "production_active_claims": production_active,
            "exploration_acquired": exploration_acquired,
            "exploration_active_claims": exploration_active,
            "exploration_remaining_claim_capacity": max(
                0, cap - exploration_acquired - exploration_active
            ),
            "round_acquired": production_acquired + exploration_acquired,
        }

    def claim_repository(
        self, worker_id: str, min_priority: float = 1.0,
        min_design_likelihood: float = 0.5, providers: list[str] | None = None,
        exploration: bool = False, precision_policy: bool = False,
        size_lane: str = "any", size_threshold_bytes: int = 64 * 1024 * 1024,
        round_id: str = "", executor_id: str = "",
    ) -> dict[str, Any] | None:
        with self.immediate() as connection:
            acquisition_lane = "exploration" if exploration else "production"
            if round_id:
                budget = connection.execute(
                    "SELECT * FROM round_acquisition_budget WHERE round_id=?", (round_id,)
                ).fetchone()
                if not budget:
                    raise RuntimeError("round acquisition budget is not initialized")
                round_acquired = connection.execute(
                    """SELECT COUNT(*) FROM round_acquisition_claims
                       WHERE round_id=? AND claim_state='ACQUIRED'""",
                    (round_id,),
                ).fetchone()[0]
                if int(round_acquired) >= int(budget["revision_target"]):
                    return None
                if exploration:
                    exploration_used = connection.execute(
                        """SELECT COUNT(*) FROM round_acquisition_claims
                           WHERE round_id=? AND acquisition_lane='exploration'
                             AND claim_state IN ('ACTIVE','ACQUIRED')""",
                        (round_id,),
                    ).fetchone()[0]
                    if int(exploration_used) >= int(budget["exploration_cap"]):
                        return None
            if executor_id:
                executor = connection.execute(
                    "SELECT * FROM acquisition_executor_budget WHERE executor_id=?",
                    (executor_id,),
                ).fetchone()
                if not executor or str(executor["state"]) != "ACTIVE":
                    return None
                if int(executor["attempts_claimed"]) >= int(executor["max_attempts"]):
                    return None
            provider_clause = f" AND provider IN ({','.join('?' for _ in providers)})" if providers else ""
            now = utc_now()
            evidence_clause = (
                " AND design_likelihood<? AND design_likelihood>=0.30 "
                "AND json_extract(metadata_json,'$.discovery_evidence.exploration_eligible')=1"
                if exploration else " AND design_likelihood>=?"
            )
            if precision_policy:
                requested_tier = "EXPLORATION" if exploration else "PRODUCTION"
                evidence_clause += (
                    " AND COALESCE(json_extract(metadata_json,'$.discovery_evidence.admission_tier'),'EXPLORATION')=?"
                )
            if exploration and round_id:
                exploration_cap = int(budget["exploration_cap"])
                low_value_cap = max(1, int(exploration_cap * 0.20)) if exploration_cap else 0
                low_value_used = int(connection.execute(
                    """SELECT COUNT(*) FROM round_acquisition_claims c
                       JOIN repositories r ON r.repository_key=c.repository_key
                       WHERE c.round_id=? AND c.acquisition_lane='exploration'
                         AND c.claim_state IN ('ACTIVE','ACQUIRED')
                         AND COALESCE(json_extract(r.metadata_json,
                             '$.discovery_evidence.admission_anchor'),'UNANCHORED')='GRAPH_ONLY'""",
                    (round_id,),
                ).fetchone()[0])
                if low_value_used >= low_value_cap:
                    evidence_clause += (
                        " AND COALESCE(json_extract(metadata_json,'$.discovery_evidence.admission_anchor'),"
                        "'UNANCHORED')!='GRAPH_ONLY'"
                    )
            estimated_size = (
                "COALESCE(NULLIF(size_bytes,0),"
                "NULLIF(CAST(json_extract(metadata_json,'$.discovery_evidence.provider_repository_bytes') AS INTEGER),0),0)"
            )
            if size_lane == "fast":
                size_clause = f" AND {estimated_size}>0 AND {estimated_size}<=?"
            elif size_lane == "unknown":
                size_clause = f" AND {estimated_size}=0"
            elif size_lane == "slow":
                size_clause = f" AND {estimated_size}>?"
            elif size_lane == "any":
                size_clause = ""
            else:
                raise ValueError(f"unsupported acquisition size lane: {size_lane}")
            row = connection.execute(
                f"""SELECT * FROM repositories WHERE state='FRONTIER'
                   AND acquisition_status IN ('NOT_ACQUIRED','RETRY')
                   AND priority>=?
                   {evidence_clause}
                   AND (next_retry_at IS NULL OR next_retry_at<=?)
                   AND NOT EXISTS (
                     SELECT 1 FROM provider_state ps
                     WHERE ps.provider=repositories.provider
                     AND ps.backoff_until IS NOT NULL AND ps.backoff_until>?
                   )
                   {size_clause}
                   {provider_clause}
                   ORDER BY
                     CASE WHEN COALESCE(json_extract(metadata_json,
                       '$.discovery_evidence.admission_anchor'),'UNANCHORED')='GRAPH_ONLY'
                       THEN 1 ELSE 0 END,
                     priority DESC, discovery_score DESC, first_seen LIMIT 1""",
                (min_priority, min_design_likelihood, *( [requested_tier] if precision_policy else [] ), now, now,
                 *( [size_threshold_bytes] if size_lane in {"fast", "slow"} else [] ), *(providers or [])),
            ).fetchone()
            if row is None:
                return None
            changed = connection.execute(
                """UPDATE repositories SET acquisition_status='ACQUIRING',claimed_by=?,claim_started_at=?
                   WHERE repository_key=? AND acquisition_status IN ('NOT_ACQUIRED','RETRY')""",
                (worker_id, utc_now(), row["repository_key"]),
            ).rowcount
            if changed != 1:
                return None
            if round_id:
                connection.execute(
                    """INSERT INTO round_acquisition_claims(
                         round_id,repository_key,acquisition_lane,size_lane,executor_id,
                         worker_id,claim_state,claimed_at
                       ) VALUES(?,?,?,?,?,?, 'ACTIVE',?)
                       ON CONFLICT(round_id,repository_key) DO UPDATE SET
                         acquisition_lane=excluded.acquisition_lane,size_lane=excluded.size_lane,
                         executor_id=excluded.executor_id,worker_id=excluded.worker_id,
                         claim_state='ACTIVE',result_state=NULL,claimed_at=excluded.claimed_at,
                         completed_at=NULL""",
                    (round_id, row["repository_key"], acquisition_lane, size_lane,
                     executor_id or None, worker_id, now),
                )
            if executor_id:
                connection.execute(
                    """UPDATE acquisition_executor_budget
                       SET attempts_claimed=attempts_claimed+1,updated_at=?
                       WHERE executor_id=?""",
                    (now, executor_id),
                )
            result = dict(row)
            result["selection_lane"] = acquisition_lane
            result["acquisition_size_lane"] = size_lane
            return result

    def requeue_stale_claims(self, max_age_seconds: int = 3600) -> int:
        cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=max_age_seconds)).replace(microsecond=0).isoformat()
        with self.immediate() as connection:
            changed = connection.execute(
                """UPDATE repositories SET acquisition_status='RETRY',claimed_by=NULL,claim_started_at=NULL
                   WHERE acquisition_status='ACQUIRING' AND claim_started_at<?""",
                (cutoff,),
            ).rowcount
            connection.execute(
                """UPDATE round_acquisition_claims
                   SET claim_state='ABANDONED',result_state='STALE_CLAIM',completed_at=?
                   WHERE claim_state='ACTIVE' AND repository_key IN (
                     SELECT repository_key FROM repositories
                     WHERE acquisition_status='RETRY' AND claimed_by IS NULL
                   )""",
                (utc_now(),),
            )
        return changed

    def reconcile_abandoned_attempts(self) -> int:
        """Terminalize RUNNING attempts whose repository no longer has a live claim."""
        with self.immediate() as connection:
            return connection.execute(
                """UPDATE acquisition_attempts SET state='FAILED',completed_at=?,
                   error_class='InterruptedError',error_detail='ABANDONED_WORKER_ATTEMPT'
                   WHERE state='RUNNING' AND repository_key IN (
                     SELECT repository_key FROM repositories
                     WHERE claimed_by IS NULL AND acquisition_status!='ACQUIRING'
                   )""",
                (utc_now(),),
            ).rowcount

    def release_claim(self, repository_key: str, worker_id: str) -> int:
        with self.immediate() as connection:
            changed = connection.execute(
                """UPDATE repositories SET acquisition_status='RETRY',claimed_by=NULL,claim_started_at=NULL
                   WHERE repository_key=? AND claimed_by=? AND acquisition_status='ACQUIRING'""",
                (repository_key, worker_id),
            ).rowcount
            if changed:
                connection.execute(
                    """UPDATE round_acquisition_claims
                       SET claim_state='RELEASED',result_state='INTERRUPTED',completed_at=?
                       WHERE repository_key=? AND worker_id=? AND claim_state='ACTIVE'""",
                    (utc_now(), repository_key, worker_id),
                )
            return changed

    def start_attempt(self, repository_key: str, method: str) -> str:
        attempt_id = "acq_" + digest(f"{repository_key}\0{method}\0{time.time_ns()}")
        self.connection.execute(
            "INSERT INTO acquisition_attempts(attempt_id,repository_key,method,state,started_at) VALUES(?,?,?,?,?)",
            (attempt_id, repository_key, method, "RUNNING", utc_now()),
        )
        self.connection.commit()
        return attempt_id

    def finish_acquisition(
        self, attempt_id: str, repository_key: str, state: str, *, commit_sha: str | None = None,
        source_path: str | None = None, archive_sha256: str | None = None, bytes_downloaded: int = 0,
        files_extracted: int = 0, error_class: str | None = None, error_detail: str | None = None,
        retry: bool = True, retry_delay_minutes: int = 60, retry_at: str | None = None,
    ) -> None:
        revision_key = repository_revision_key(repository_key, commit_sha) if commit_sha else None
        with self.immediate() as connection:
            connection.execute(
                """UPDATE acquisition_attempts SET repository_revision_key=?,state=?,completed_at=?,bytes_downloaded=?,
                   files_extracted=?,artifact_path=?,error_class=?,error_detail=? WHERE attempt_id=?""",
                (revision_key, state, utc_now(), bytes_downloaded, files_extracted, source_path,
                 error_class, (error_detail or "")[:4000], attempt_id),
            )
            if state == "ACQUIRED" and revision_key and source_path and commit_sha:
                connection.execute(
                    """INSERT OR IGNORE INTO repository_revisions(repository_revision_key,repository_key,commit_sha,
                       archive_sha256,source_path,acquired_at) VALUES(?,?,?,?,?,?)""",
                    (revision_key, repository_key, commit_sha.lower(), archive_sha256, source_path, utc_now()),
                )
                connection.execute(
                    """UPDATE repositories SET state='ACQUIRED',acquisition_status='ACQUIRED',acquired_revision=?,
                       claimed_by=NULL,claim_started_at=NULL WHERE repository_key=?""",
                    (revision_key, repository_key),
                )
            elif retry:
                connection.execute(
                    """UPDATE repositories SET acquisition_status='RETRY',claimed_by=NULL,claim_started_at=NULL,
                       next_retry_at=? WHERE repository_key=?""",
                    (retry_at or (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=retry_delay_minutes)).replace(microsecond=0).isoformat(), repository_key),
                )
            else:
                connection.execute(
                    """UPDATE repositories SET acquisition_status='EXCLUDED',claimed_by=NULL,
                       claim_started_at=NULL,next_retry_at=NULL WHERE repository_key=?""",
                    (repository_key,),
                )
            connection.execute(
                """UPDATE round_acquisition_claims
                   SET claim_state=?,result_state=?,completed_at=?
                   WHERE repository_key=? AND claim_state='ACTIVE'""",
                ("ACQUIRED" if state == "ACQUIRED" else "TERMINAL", state,
                 utc_now(), repository_key),
            )

    def counts(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for table in ["repositories", "discovery_events", "queries", "repo_edges", "acquisition_attempts", "repository_revisions", "graph_expansions"]:
            result[table] = self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        result["repository_states"] = {
            row[0]: row[1] for row in self.connection.execute("SELECT state,COUNT(*) FROM repositories GROUP BY state")
        }
        result["acquisition_states"] = {
            row[0]: row[1] for row in self.connection.execute("SELECT acquisition_status,COUNT(*) FROM repositories GROUP BY acquisition_status")
        }
        return result


def default_frontier_path(corpus_root: Path) -> Path:
    return corpus_root / "state" / "frontier.sqlite"
