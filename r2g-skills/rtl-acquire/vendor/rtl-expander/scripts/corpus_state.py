#!/usr/bin/env python3
"""Append-only corpus truth ledger, rebuildable SQLite index, and snapshots."""

from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Iterable


STATE_SCHEMA = "rtl_corpus_state_v1"
EVENT_SCHEMA = "rtl_corpus_event_v1"
RELEASE_SCHEMA = "rtl_corpus_release_identity_v1"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def digest_file(path: Path) -> str:
    return digest_bytes(path.read_bytes()) if path.is_file() else "MISSING"


def digest_tree(root: Path) -> str:
    entries = []
    if root.is_dir():
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            entries.append((path.relative_to(root).as_posix(), digest_file(path)))
    return digest_bytes(canonical(entries))


def recorded_view_digest(path: Path) -> str:
    receipt = path.with_name(path.name + ".admission.json")
    if not receipt.is_file():
        return "MISSING_ADMISSION_DIGEST"
    value = json.loads(receipt.read_text(encoding="utf-8"))
    digest = str(value.get("sha256") or "").lower()
    if (
        value.get("schema") != "rtl_materialized_view_admission_v1"
        or value.get("object_id") != path.name
        or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
        or value.get("rehash_required") is not False
    ):
        return "INVALID_ADMISSION_DIGEST"
    return digest


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def provenance_complete(row: dict[str, Any]) -> bool:
    source = row.get("source", {})
    provenance = row.get("provenance", {})
    return bool(
        source.get("repository_revision_key")
        and provenance.get("repository_url") not in {None, "", "UNKNOWN"}
        and provenance.get("commit_sha") not in {None, "", "UNKNOWN"}
    )


class CorpusState:
    def __init__(self, corpus: Path):
        self.corpus = corpus
        self.database = corpus / "state" / "corpus.sqlite"
        self.ledger = corpus / "ledger"
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.ledger.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.database, timeout=60)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self._pending_events: list[tuple[Path, dict[str, Any]]] | None = None
        self._schema()
        self._recover_event_identities()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "CorpusState":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def _schema(self) -> None:
        self.connection.executescript("""
        CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events(
          event_id TEXT PRIMARY KEY,stream TEXT NOT NULL,event_type TEXT NOT NULL,
          object_id TEXT NOT NULL,payload_sha256 TEXT NOT NULL,occurred_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS repositories(
          repo_id TEXT PRIMARY KEY,repository_revision_key TEXT,content_sha256 TEXT NOT NULL,
          state TEXT,classification TEXT,payload_json TEXT NOT NULL,updated_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS repositories_revision_idx
          ON repositories(repository_revision_key) WHERE repository_revision_key IS NOT NULL;
        CREATE TABLE IF NOT EXISTS repository_aliases(
          alias_repo_id TEXT PRIMARY KEY,canonical_repo_id TEXT NOT NULL,
          repository_revision_key TEXT NOT NULL,content_sha256 TEXT NOT NULL,
          payload_json TEXT NOT NULL,updated_at TEXT NOT NULL,
          FOREIGN KEY(canonical_repo_id) REFERENCES repositories(repo_id)
        );
        CREATE INDEX IF NOT EXISTS repository_alias_revision_idx
          ON repository_aliases(repository_revision_key);
        CREATE TABLE IF NOT EXISTS designs(
          design_id TEXT PRIMARY KEY,family_id TEXT NOT NULL,repository_revision_key TEXT,
          content_sha256 TEXT NOT NULL,synthesis_valid INTEGER NOT NULL,
          provenance_complete INTEGER NOT NULL,release_policy TEXT,training_tier TEXT,
          split TEXT,split_group_id TEXT,payload_json TEXT NOT NULL,updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS designs_family_idx ON designs(family_id);
        CREATE INDEX IF NOT EXISTS designs_revision_idx ON designs(repository_revision_key);
        CREATE TABLE IF NOT EXISTS family_membership(
          design_id TEXT PRIMARY KEY,family_id TEXT NOT NULL,content_sha256 TEXT NOT NULL,
          FOREIGN KEY(design_id) REFERENCES designs(design_id)
        );
        CREATE INDEX IF NOT EXISTS family_membership_family_idx ON family_membership(family_id);
        CREATE TABLE IF NOT EXISTS split_membership(
          family_id TEXT PRIMARY KEY,split_group_id TEXT NOT NULL,split TEXT NOT NULL,
          content_sha256 TEXT NOT NULL
        );
        """)
        self.connection.execute(
            "INSERT OR REPLACE INTO metadata(key,value) VALUES('schema',?)", (STATE_SCHEMA,)
        )
        self.connection.commit()

    def _append_event(self, stream: str, event_type: str, object_id: str, payload: dict[str, Any]) -> bool:
        payload_hash = digest_bytes(canonical(payload))
        event_id = digest_bytes(canonical([EVENT_SCHEMA, stream, event_type, object_id, payload_hash]))
        if self.connection.execute("SELECT 1 FROM events WHERE event_id=?", (event_id,)).fetchone():
            return False
        event = {
            "schema": EVENT_SCHEMA, "event_id": event_id, "stream": stream,
            "event_type": event_type, "object_id": object_id,
            "payload_sha256": payload_hash, "occurred_at": utc_now(), "payload": payload,
        }
        path = self.ledger / f"{stream}_events.jsonl"
        if self._pending_events is not None:
            self._pending_events.append((path, event))
        else:
            self._write_event_batch([(path, event)])
        self.connection.execute(
            "INSERT INTO events VALUES(?,?,?,?,?,?)",
            (event_id, stream, event_type, object_id, payload_hash, event["occurred_at"]),
        )
        return True

    def _write_event_batch(self, events: list[tuple[Path, dict[str, Any]]]) -> None:
        by_path: dict[Path, list[dict[str, Any]]] = {}
        for path, event in events:
            by_path.setdefault(path, []).append(event)
        lock = self.ledger / ".ledger.lock"
        with lock.open("a+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            for path, rows in by_path.items():
                with path.open("ab", buffering=0) as output:
                    for row in rows:
                        output.write(canonical(row))
                    os.fsync(output.fileno())
                self.connection.execute(
                    "INSERT OR REPLACE INTO metadata(key,value) VALUES(?,?)",
                    (f"ledger_offset:{path.name}", str(path.stat().st_size)),
                )

    def record_processing_event(
        self, event_type: str, repository_revision_key: str, payload: dict[str, Any]
    ) -> bool:
        """Append one revision-local processing fact without publishing corpus identity."""
        if event_type not in {
            "REVISION_ACQUIRED", "PROCESSING_STARTED", "PROCESSING_TERMINAL",
            "PROCESSING_RETRY_SCHEDULED",
        }:
            raise ValueError(f"unsupported processing event: {event_type}")
        with self.connection:
            return self._append_event(
                "processing", event_type, repository_revision_key,
                {"repository_revision_key": repository_revision_key, **payload},
            )

    def record_processing_events(
        self, events: Iterable[tuple[str, str, dict[str, Any]]]
    ) -> int:
        """Durably append a processing microbatch with one ledger fsync/DB commit."""
        allowed = {
            "REVISION_ACQUIRED", "PROCESSING_STARTED", "PROCESSING_TERMINAL",
            "PROCESSING_RETRY_SCHEDULED",
        }
        changed = 0
        with self.connection:
            self._pending_events = []
            try:
                for event_type, repository_revision_key, payload in events:
                    if event_type not in allowed:
                        raise ValueError(f"unsupported processing event: {event_type}")
                    changed += int(self._append_event(
                        "processing", event_type, repository_revision_key,
                        {"repository_revision_key": repository_revision_key, **payload},
                    ))
                self._write_event_batch(self._pending_events)
            finally:
                self._pending_events = None
        return changed

    def _recover_event_identities(self) -> None:
        """Index ledger tails after a crash between durable append and DB commit."""
        with self.connection:
            for path in sorted(self.ledger.glob("*_events.jsonl")):
                key = f"ledger_offset:{path.name}"
                row = self.connection.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
                offset = int(row[0]) if row and int(row[0]) <= path.stat().st_size else 0
                with path.open("rb") as handle:
                    handle.seek(offset)
                    for line in handle:
                        if not line.strip():
                            continue
                        event = json.loads(line)
                        self.connection.execute(
                            "INSERT OR IGNORE INTO events VALUES(?,?,?,?,?,?)",
                            (event["event_id"], event["stream"], event["event_type"],
                             event["object_id"], event["payload_sha256"], event["occurred_at"]),
                        )
                    offset = handle.tell()
                self.connection.execute(
                    "INSERT OR REPLACE INTO metadata(key,value) VALUES(?,?)", (key, str(offset))
                )

    def upsert_repositories(self, rows: Iterable[dict[str, Any]]) -> int:
        changed = 0
        for row in rows:
            repo_id = str(row["repo_id"])
            content_hash = digest_bytes(canonical(row))
            prior = self.connection.execute(
                "SELECT content_sha256 FROM repositories WHERE repo_id=?", (repo_id,)
            ).fetchone()
            if prior is None:
                prior = self.connection.execute(
                    "SELECT content_sha256 FROM repository_aliases WHERE alias_repo_id=?", (repo_id,)
                ).fetchone()
            if prior and prior[0] == content_hash:
                continue
            event_type = "REPOSITORY_REVISION_CREATED" if prior is None else "REPOSITORY_STATE_UPDATED"
            self._append_event("repository", event_type, repo_id, row)
            revision = row.get("source", {}).get("repository_revision_key") or row.get("repository_revision_key")
            if not revision and row.get("repository_url") not in {None, "", "UNKNOWN"} and row.get("commit_sha") not in {None, "", "UNKNOWN"}:
                try:
                    from frontier import canonical_repository_identity
                    revision = canonical_repository_identity(str(row["repository_url"]))["repository_key"] + "@" + str(row["commit_sha"]).lower()
                except ValueError:
                    revision = None
            canonical_repo = None
            if revision:
                canonical_repo = self.connection.execute(
                    "SELECT repo_id FROM repositories WHERE repository_revision_key=?", (revision,)
                ).fetchone()
            if canonical_repo and canonical_repo[0] != repo_id:
                self._append_event("repository", "REPOSITORY_ALIAS_OBSERVED", repo_id, {
                    "alias_repo_id": repo_id, "canonical_repo_id": canonical_repo[0],
                    "repository_revision_key": revision, "record": row,
                })
                self.connection.execute(
                    """INSERT INTO repository_aliases VALUES(?,?,?,?,?,?)
                       ON CONFLICT(alias_repo_id) DO UPDATE SET
                       canonical_repo_id=excluded.canonical_repo_id,
                       repository_revision_key=excluded.repository_revision_key,
                       content_sha256=excluded.content_sha256,payload_json=excluded.payload_json,
                       updated_at=excluded.updated_at""",
                    (repo_id, canonical_repo[0], revision, content_hash,
                     canonical(row).decode().strip(), utc_now()),
                )
                changed += 1
                continue
            self.connection.execute(
                """INSERT INTO repositories VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(repo_id) DO UPDATE SET repository_revision_key=excluded.repository_revision_key,
                   content_sha256=excluded.content_sha256,state=excluded.state,
                   classification=excluded.classification,payload_json=excluded.payload_json,
                   updated_at=excluded.updated_at""",
                (repo_id, revision, content_hash, row.get("state"), row.get("classification"),
                 canonical(row).decode().strip(), utc_now()),
            )
            changed += 1
        return changed

    def upsert_designs(self, rows: Iterable[dict[str, Any]]) -> int:
        changed = 0
        for row in rows:
            design_id, family_id = str(row["design_id"]), str(row["family_id"])
            content_hash = digest_bytes(canonical(row))
            prior = self.connection.execute(
                "SELECT content_sha256,family_id,split_group_id FROM designs WHERE design_id=?", (design_id,)
            ).fetchone()
            if prior and prior[0] == content_hash:
                continue
            event_type = "DESIGN_CREATED" if prior is None else "DESIGN_UPDATED"
            self._append_event("design", event_type, design_id, row)
            self._append_event("license", "LICENSE_STATE_OBSERVED", design_id, {
                "design_id": design_id, "license_status": row.get("release", {}).get("license_status"),
                "release_policy": row.get("release", {}).get("release_policy"),
            })
            self._append_event("quality", "QUALITY_STATE_OBSERVED", design_id, {
                "design_id": design_id, "training_tier": row.get("quality", {}).get("training_tier"),
                "engineering_quality": row.get("quality", {}).get("engineering_quality"),
                "functional_confidence": row.get("verification", {}).get("functional_confidence"),
            })
            if prior is None:
                self._append_event("family", "FAMILY_MEMBERSHIP_ASSIGNED", design_id, {
                    "design_id": design_id, "family_id": family_id,
                })
            if prior and prior[1] != family_id:
                self._append_event("family", "FAMILY_MEMBERSHIP_CHANGED", design_id, {
                    "design_id": design_id, "old_family_id": prior[1], "new_family_id": family_id,
                })
            split_group = str(row.get("split_group_id") or "UNASSIGNED")
            if prior and prior[2] != split_group:
                self._append_event("split", "SPLIT_MEMBERSHIP_CHANGED", family_id, {
                    "family_id": family_id, "old_split_group_id": prior[2],
                    "new_split_group_id": split_group, "split": row.get("split"),
                })
            elif prior is None:
                self._append_event("split", "SPLIT_MEMBERSHIP_ASSIGNED", family_id, {
                    "family_id": family_id, "split_group_id": split_group, "split": row.get("split"),
                })
            revision = row.get("source", {}).get("repository_revision_key")
            complete = provenance_complete(row)
            self.connection.execute(
                """INSERT INTO designs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(design_id) DO UPDATE SET family_id=excluded.family_id,
                   repository_revision_key=excluded.repository_revision_key,
                   content_sha256=excluded.content_sha256,synthesis_valid=excluded.synthesis_valid,
                   provenance_complete=excluded.provenance_complete,release_policy=excluded.release_policy,
                   training_tier=excluded.training_tier,split=excluded.split,
                   split_group_id=excluded.split_group_id,payload_json=excluded.payload_json,
                   updated_at=excluded.updated_at""",
                (design_id, family_id, revision, content_hash,
                 int(bool(row.get("synthesis", {}).get("generic_pass"))), int(complete),
                 row.get("release", {}).get("release_policy"),
                 row.get("quality", {}).get("training_tier"), row.get("split"), split_group,
                 canonical(row).decode().strip(), utc_now()),
            )
            membership_hash = digest_bytes(canonical([design_id, family_id]))
            self.connection.execute(
                "INSERT OR REPLACE INTO family_membership VALUES(?,?,?)",
                (design_id, family_id, membership_hash),
            )
            split_hash = digest_bytes(canonical([family_id, split_group, row.get("split")]))
            self.connection.execute(
                "INSERT OR REPLACE INTO split_membership VALUES(?,?,?,?)",
                (family_id, split_group, str(row.get("split")), split_hash),
            )
            changed += 1
        return changed

    def retire_designs(self, design_ids: Iterable[str], reason: str) -> int:
        retired = 0
        for design_id in design_ids:
            prior = self.connection.execute(
                "SELECT family_id,payload_json FROM designs WHERE design_id=?", (design_id,)
            ).fetchone()
            if prior is None:
                continue
            self._append_event("design", "DESIGN_RETIRED", design_id, {
                "design_id": design_id, "family_id": prior[0], "reason": reason,
            })
            self.connection.execute("DELETE FROM family_membership WHERE design_id=?", (design_id,))
            self.connection.execute("DELETE FROM designs WHERE design_id=?", (design_id,))
            if not self.connection.execute(
                "SELECT 1 FROM family_membership WHERE family_id=? LIMIT 1", (prior[0],)
            ).fetchone():
                self.connection.execute("DELETE FROM split_membership WHERE family_id=?", (prior[0],))
            retired += 1
        return retired

    def apply_incremental(
        self, *, repositories: Iterable[dict[str, Any]] = (),
        designs: Iterable[dict[str, Any]] = (), retired_design_ids: Iterable[str] = (),
        retirement_reason: str = "SUPERSEDED",
    ) -> dict[str, int]:
        with self.connection:
            self._pending_events = []
            try:
                repository_changes = self.upsert_repositories(repositories)
                retired = self.retire_designs(retired_design_ids, retirement_reason)
                design_changes = self.upsert_designs(designs)
                self._write_event_batch(self._pending_events)
            finally:
                self._pending_events = None
        return {"repository_changes": repository_changes, "design_changes": design_changes,
                "retired_designs": retired}

    def sync_materialized_views(self) -> dict[str, Any]:
        manifests = self.corpus / "manifests"
        changes = self.apply_incremental(
            repositories=read_jsonl(manifests / "repositories.jsonl"),
            designs=read_jsonl(manifests / "all_designs.jsonl"),
        )
        return {"schema": STATE_SCHEMA, **changes, **self.metrics()}

    def metrics(self) -> dict[str, Any]:
        row = self.connection.execute("""
          SELECT COUNT(*) design_instances,
                 COUNT(DISTINCT family_id) design_families,
                 COUNT(DISTINCT CASE WHEN synthesis_valid=1 THEN family_id END) synthesis_valid_families,
                 COUNT(DISTINCT CASE WHEN synthesis_valid=1 AND provenance_complete=1 THEN family_id END) formal_synthesis_valid_families,
                 SUM(CASE WHEN provenance_complete=0 THEN 1 ELSE 0 END) unresolved_provenance_designs,
                 SUM(CASE WHEN provenance_complete=0 AND release_policy!='QUARANTINE' THEN 1 ELSE 0 END) unresolved_provenance_unquarantined
          FROM designs
        """).fetchone()
        return dict(row)

    def payloads(self, *, formal_only: bool = False) -> list[dict[str, Any]]:
        clause = "WHERE synthesis_valid=1 AND provenance_complete=1" if formal_only else ""
        return [json.loads(row[0]) for row in self.connection.execute(
            f"SELECT payload_json FROM designs {clause} ORDER BY design_id"
        )]

    def iter_payloads(self, where: str = "", parameters: tuple[Any, ...] = ()) -> Iterable[dict[str, Any]]:
        query = "SELECT payload_json FROM designs"
        if where:
            query += " WHERE " + where
        query += " ORDER BY design_id"
        for row in self.connection.execute(query, parameters):
            yield json.loads(row[0])

    def repository_payloads(self) -> list[dict[str, Any]]:
        rows = [json.loads(row[0]) for row in self.connection.execute(
            "SELECT payload_json FROM repositories ORDER BY repo_id"
        )]
        rows.extend(json.loads(row[0]) for row in self.connection.execute(
            "SELECT payload_json FROM repository_aliases ORDER BY alias_repo_id"
        ))
        return rows

    def populated(self) -> bool:
        return bool(self.connection.execute("SELECT 1 FROM designs LIMIT 1").fetchone())


def materialize_snapshot(corpus: Path, snapshot_id: str | None = None) -> dict[str, Any]:
    from split_consumption_contract import snapshot_metadata

    with CorpusState(corpus) as state:
        sync = state.sync_materialized_views() if not state.populated() else {
            "schema": STATE_SCHEMA, "repository_changes": 0, "design_changes": 0,
            **state.metrics(),
        }
        snapshot_id = snapshot_id or ("rtl-corpus-" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
        final = corpus / "snapshots" / snapshot_id
        if final.exists():
            identity = final / "release_identity.json"
            if identity.is_file():
                return json.loads(identity.read_text(encoding="utf-8"))
            raise FileExistsError(f"incomplete existing snapshot: {final}")
        temporary = final.with_name(f".{final.name}.tmp.{os.getpid()}")
        manifests = temporary / "manifests"
        manifests.mkdir(parents=True, exist_ok=False)
        def write(name: str, rows: Iterable[dict[str, Any]]) -> str:
            path = manifests / name
            with path.open("wb") as output:
                for row in rows:
                    output.write(canonical(row))
            return digest_file(path)

        manifest_hashes = {
            "all_designs.jsonl": write("all_designs.jsonl", state.iter_payloads()),
            "provenance_complete_synthesis_valid.jsonl": write(
                "provenance_complete_synthesis_valid.jsonl",
                state.iter_payloads("synthesis_valid=1 AND provenance_complete=1"),
            ),
            "training_gold.jsonl": write(
                "training_gold.jsonl",
                state.iter_payloads(
                    "synthesis_valid=1 AND provenance_complete=1 AND training_tier='TRAINING_GOLD'"
                ),
            ),
            "public_export_allowed.jsonl": write(
                "public_export_allowed.jsonl",
                state.iter_payloads(
                    "synthesis_valid=1 AND provenance_complete=1 "
                    "AND release_policy='PUBLIC_EXPORT_ALLOWED' "
                    "AND COALESCE(json_extract(payload_json,'$.contamination.benchmark_contaminated'),0)=0"
                ),
            ),
        }
        skill_root = Path(__file__).parents[1]
        policy_paths = [skill_root / "SKILL.md", *sorted((skill_root / "references").glob("*"))]
        policy_hash = digest_bytes(canonical([(p.name, digest_file(p)) for p in policy_paths if p.is_file()]))
        logical_index_hash = digest_bytes(canonical([
            tuple(row) for row in state.connection.execute(
                "SELECT design_id,content_sha256,family_id,split_group_id FROM designs ORDER BY design_id"
            )
        ]))
        tool_paths = sorted({
            str(row.get("synthesis", {}).get("tool")) for row in state.iter_payloads()
            if row.get("synthesis", {}).get("tool")
        })
        toolchain_identity = {
            "controlled_tool_hashes": {
                path: digest_file(Path(path)) for path in tool_paths
            },
            "synthesis_driver_hash": digest_file(skill_root / "scripts/run_expansion_round.py"),
            "repair_policy_hash": digest_file(skill_root / "scripts/run_online_r1_recovery.py"),
            "training_view_generator_hash": digest_file(skill_root / "scripts/run_expansion_round.py"),
            "liberty_hash": "NOT_APPLICABLE_GENERIC_SYNTHESIS_ONLY",
        }
        split_profiles = read_jsonl(corpus / "manifests/split_profiles.jsonl") or [{
            "schema": "rtl_split_profile_v1",
            "profile_id": "rtl_split_profile_v1",
            "split_schema": "rtl_split_v1",
            "split_epoch": "initial_frozen_v1",
            "status": "CURRENT",
        }]
        current_split_profiles = [row for row in split_profiles if row.get("status") == "CURRENT"]
        current_split_profile = current_split_profiles[0] if len(current_split_profiles) == 1 else {}
        consumption = snapshot_metadata(corpus)
        identity_inputs = {
            "pipeline_schema": STATE_SCHEMA,
            "skill_source_hash": digest_tree(skill_root / "scripts"),
            "policy_hash": policy_hash,
            "corpus_logical_index_sha256": logical_index_hash,
            "repository_ledger_hash": digest_file(state.ledger / "repository_events.jsonl"),
            "design_ledger_hash": digest_file(state.ledger / "design_events.jsonl"),
            "family_ledger_hash": digest_file(state.ledger / "family_events.jsonl"),
            "split_ledger_hash": digest_file(state.ledger / "split_events.jsonl"),
            "split_profile_id": current_split_profile.get("profile_id", "INVALID"),
            "split_profile_hash": recorded_view_digest(corpus / "manifests/split_profiles.jsonl")
            if (corpus / "manifests/split_profiles.jsonl").is_file()
            else digest_bytes(canonical(split_profiles)),
            "split_epoch": current_split_profile.get("split_epoch", "INVALID"),
            "split_reconciliation_lineage_hash": digest_file(
                corpus / "manifests/split_reconciliations.jsonl"
            ),
            "benchmark_registry_hash": digest_tree(corpus / "benchmark_registry"),
            "license_policy_hash": digest_file(skill_root / "references" / "policy.json"),
            "quality_policy_hash": policy_hash,
            "toolchain_identity": toolchain_identity,
            "gold_manifest_hash": manifest_hashes["training_gold.jsonl"],
            "contamination_audit_hash": digest_file(corpus / "quality/phase1_5/benchmark_contamination_audit.json"),
            "manifest_hashes": manifest_hashes,
            "metrics": sync,
            "split_profile_consumption": consumption,
        }
        release_hash = digest_bytes(canonical(identity_inputs))
        identity = {
            "schema": RELEASE_SCHEMA, "corpus_snapshot_id": snapshot_id,
            "release_sha256": release_hash, "created_at": utc_now(), **identity_inputs,
        }
        (temporary / "release_identity.json").write_bytes(canonical(identity))
        scale_path = corpus / "quality/scale_pilot_summary.json"
        scale = json.loads(scale_path.read_text()) if scale_path.is_file() else {}
        contamination_path = corpus / "quality/phase1_5/benchmark_contamination_audit.json"
        contamination = json.loads(contamination_path.read_text()) if contamination_path.is_file() else {}
        conservation = [
            value for group in scale.get("stage_conservation", {}).values()
            if isinstance(group, dict) for value in group.values()
            if isinstance(value, dict) and "residual" in value
        ]
        conservation_valid = bool(conservation) and all(
            item.get("conserved") is True and item.get("residual") == 0 for item in conservation
        )
        contamination_current = contamination.get("benchmark_registry_hash") == identity["benchmark_registry_hash"]
        gold_meta_path = corpus / "manifests/training_gold.meta.json"
        gold_meta = json.loads(gold_meta_path.read_text()) if gold_meta_path.is_file() else {}
        gold_manifest_current = gold_meta.get("manifest_sha256") == recorded_view_digest(
            corpus / "manifests/training_gold.jsonl"
        )
        integrity = scale.get("integrity", {})
        publish = integrity.get("publish_invariants", {})
        contamination_exports_clean = all(
            not row.get("contamination", {}).get("benchmark_contaminated", False)
            for row in state.iter_payloads(
                "synthesis_valid=1 AND provenance_complete=1 AND "
                "(training_tier='TRAINING_GOLD' OR (release_policy='PUBLIC_EXPORT_ALLOWED' "
                "AND COALESCE(json_extract(payload_json,'$.contamination.benchmark_contaminated'),0)=0))"
            )
        )
        checks = {
            "single_current_split_profile": len(current_split_profiles) == 1,
            "funnel_conservation_valid": conservation_valid,
            "unquarantined_unresolved_provenance_zero": sync["unresolved_provenance_unquarantined"] == 0,
            "contamination_audit_current": contamination_current,
            "contamination_exports_clean": contamination_exports_clean,
            "gold_manifest_current": gold_manifest_current,
            "corrupt_manifest_rows_zero": integrity.get("corrupt_manifest_rows") == 0,
            "duplicate_design_ids_zero": integrity.get("duplicate_design_ids") == 0,
            "duplicate_repository_revisions_zero": integrity.get("frontier", {}).get("duplicate_revision_rows") == 0,
            "immutable_source_hash_mismatches_zero": integrity.get("immutable_source_hash_mismatches") == 0,
            "family_split_violations_zero": integrity.get("family_split_violations") == 0,
            "split_group_violations_zero": integrity.get("split_group_violations") == 0,
            "published_without_elaboration_zero": integrity.get("published_without_elaboration") == 0,
            "storage_layout_complete": integrity.get("storage_layout_missing_design_json") == 0,
            "publish_invariants_valid": publish.get("valid") is True,
        }
        certified = all(checks.values())
        completion = {
            "schema": "rtl_corpus_certification_v1", "snapshot_id": snapshot_id,
            "status": "CERTIFIED" if certified else "NEEDS_HARDENING",
            "checks": checks,
            "failed": sorted(key for key, value in checks.items() if not value),
            "formal_unresolved_provenance_designs": 0,
            "data_lake_unresolved_provenance_designs": sync["unresolved_provenance_designs"],
            "unquarantined_unresolved_provenance_designs": sync["unresolved_provenance_unquarantined"],
            "consumption_scope": consumption["consumption_scope"],
            "consumption_state": consumption.get("consumption_state", "UNDECLARED"),
            "external_training_eligible": consumption["external_training_eligible"],
            "external_evaluation_eligible": consumption["external_evaluation_eligible"],
        }
        (temporary / "completion.json").write_bytes(canonical(completion))
        os.replace(temporary, final)
        if certified:
            latest = corpus / "snapshots" / "latest_release.json"
            temp_latest = latest.with_name(f".{latest.name}.tmp.{os.getpid()}")
            temp_latest.write_bytes(canonical(identity))
            os.replace(temp_latest, latest)
        return identity
