"""Freeze and replay the TEHM SQLite schema contract.

Revision3 P16 is deliberately a contract boundary, not a lifecycle gate.  A
freeze records the shipped ``schema.sql`` bytes, the forward migration chain,
and the SQLite objects observed in an optional TEHM store.  Replay recomputes
all of those values before an external consumer can treat the store as a
stable evidence input.  The operation is read-only with respect to an
existing database and never changes canonical memory or production authority.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from tehm import config, migrations
from tehm.db import connect_read_only
from tehm.ids import stable_dumps
from tehm.state.schema import STATE_SCHEMA_SQL, STATE_SCHEMA_VERSION


SCHEMA_CONTRACT_FREEZE_VERSION = "tehm-schema-contract-freeze-v1"


class SchemaContractError(ValueError):
    """Raised when a schema contract cannot be frozen or replayed."""


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    try:
        return _sha256_bytes(path.read_bytes())
    except OSError as exc:
        raise SchemaContractError(f"schema contract input is unreadable: {path}") from exc


def _digest(value: object) -> str:
    return _sha256_bytes(stable_dumps(value).encode())


def _text(value: object, field: str) -> str:
    if type(value) is not str or not value.strip():
        raise SchemaContractError(f"schema contract {field} is required")
    return value.strip()


def _digest_text(value: object, field: str) -> str:
    value = _text(value, field)
    if not value.startswith("sha256:") or len(value) != len("sha256:") + 64:
        raise SchemaContractError(f"schema contract {field} must be sha256 digest")
    return value


def _normal_sql(value: object) -> str | None:
    if value is None:
        return None
    # SQLite preserves the CREATE statement text.  Whitespace is not a
    # semantic contract, so normalize it before hashing/introspection.
    return " ".join(str(value).split())


def _introspect(conn: sqlite3.Connection) -> tuple[dict, ...]:
    rows = conn.execute(
        """SELECT type, name, tbl_name, sql
             FROM sqlite_master
            WHERE type IN ('table', 'index', 'trigger', 'view')
              AND name NOT LIKE 'sqlite_%'
            ORDER BY type, name"""
    ).fetchall()
    return tuple({"type": row[0], "name": row[1], "table": row[2],
                  "sql": _normal_sql(row[3])} for row in rows)


def _schema_objects(schema_path: Path) -> tuple[dict, ...]:
    try:
        script = schema_path.read_text()
    except OSError as exc:
        raise SchemaContractError(f"schema contract input is unreadable: {schema_path}") from exc
    conn = sqlite3.connect(":memory:")
    try:
        try:
            conn.executescript(script)
            # State resolution keeps one additive receipt table outside the
            # frozen v4 migration chain.  Include its shipped contract in the
            # P16 inventory so a real P14 store is not misclassified as
            # having an unexpected object.
            conn.executescript(STATE_SCHEMA_SQL)
        except sqlite3.DatabaseError as exc:
            raise SchemaContractError("schema.sql cannot be executed in SQLite") from exc
        return _introspect(conn)
    finally:
        conn.close()


def _extension_objects() -> tuple[dict, ...]:
    conn = sqlite3.connect(":memory:")
    try:
        try:
            conn.executescript(STATE_SCHEMA_SQL)
        except sqlite3.DatabaseError as exc:
            raise SchemaContractError("state schema extension cannot be executed in SQLite") from exc
        return _introspect(conn)
    finally:
        conn.close()


def _migration_contract() -> tuple[dict, ...]:
    entries = []
    current = "tehm-v1"
    for migration in migrations.MIGRATIONS:
        if migration.from_version != current:
            raise SchemaContractError(
                f"migration chain is not linear at {migration.from_version!r}")
        entries.append({
            "from_version": migration.from_version,
            "to_version": migration.to_version,
            "name": migration.name,
            "statements_sha256": _sha256_bytes(migration.statements.encode()),
        })
        current = migration.to_version
    expected = f"tehm-v{config.DB_SCHEMA_VERSION}"
    if current != expected:
        raise SchemaContractError(
            f"migration chain ends at {current!r}, expected {expected!r}")
    return tuple(entries)


def _schema_extension_contract() -> tuple[dict, ...]:
    return ({
        "module": "tehm.state.schema",
        "version": STATE_SCHEMA_VERSION,
        "sql_sha256": _sha256_bytes(STATE_SCHEMA_SQL.encode()),
    },)


def _sidecars(db_path: Path) -> tuple[str, ...]:
    return tuple(str(path) for path in (
        Path(str(db_path) + "-wal"), Path(str(db_path) + "-shm")) if path.exists())


@dataclass(frozen=True)
class SchemaContractFreezeReceipt:
    """Content-addressed P16 schema/contract freeze receipt."""

    schema_path: str
    schema_sql_sha256: str
    schema_objects: tuple[dict, ...]
    schema_objects_digest: str
    schema_extensions: tuple[dict, ...]
    migrations: tuple[dict, ...]
    migration_chain_digest: str
    db_path: str | None
    observed_objects: tuple[dict, ...] | None
    observed_objects_digest: str | None
    missing_objects: tuple[str, ...]
    unexpected_objects: tuple[str, ...]
    db_schema_version: str | None
    version: str = SCHEMA_CONTRACT_FREEZE_VERSION
    memory_docs_submitted: bool = False
    canonical_memory_mutation: str = "none"
    production_runtime_imported: bool = False
    promotion_attempted: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_path", _text(self.schema_path, "schema_path"))
        object.__setattr__(self, "schema_sql_sha256",
                           _digest_text(self.schema_sql_sha256, "schema_sql_sha256"))
        object.__setattr__(self, "schema_objects_digest",
                           _digest_text(self.schema_objects_digest, "schema_objects_digest"))
        if not isinstance(self.schema_extensions, tuple) or not self.schema_extensions:
            raise SchemaContractError("schema contract extensions are invalid")
        object.__setattr__(self, "migration_chain_digest",
                           _digest_text(self.migration_chain_digest, "migration_chain_digest"))
        if self.observed_objects_digest is not None:
            object.__setattr__(self, "observed_objects_digest",
                               _digest_text(self.observed_objects_digest,
                                            "observed_objects_digest"))
        if self.db_path is not None:
            object.__setattr__(self, "db_path", _text(self.db_path, "db_path"))
        if self.db_schema_version is not None:
            object.__setattr__(self, "db_schema_version",
                               _text(self.db_schema_version, "db_schema_version"))
        if self.version != SCHEMA_CONTRACT_FREEZE_VERSION:
            raise SchemaContractError("schema contract freeze version is invalid")
        if self.memory_docs_submitted is not False:
            raise SchemaContractError("memory/docs cannot be submitted")
        if self.canonical_memory_mutation != "none":
            raise SchemaContractError("schema contract freeze must not mutate canonical memory")
        if self.production_runtime_imported is not False or self.promotion_attempted is not False:
            raise SchemaContractError("schema contract freeze cannot enter production authority")
        if not isinstance(self.schema_objects, tuple) or not self.schema_objects:
            raise SchemaContractError("schema contract has no schema objects")
        if not isinstance(self.migrations, tuple):
            raise SchemaContractError("schema contract migrations are invalid")
        if self.observed_objects is None and self.db_path is not None:
            raise SchemaContractError("db_path requires observed schema objects")
        if self.observed_objects is not None and self.observed_objects_digest is None:
            raise SchemaContractError("observed schema objects require a digest")
        if self.missing_objects or self.unexpected_objects:
            raise SchemaContractError("observed DB schema differs from frozen schema")
        if self.db_path is not None and self.db_schema_version != f"tehm-v{config.DB_SCHEMA_VERSION}":
            raise SchemaContractError("observed DB schema version is not current")

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "schema_path": self.schema_path,
            "schema_sql_sha256": self.schema_sql_sha256,
            "schema_objects": list(self.schema_objects),
            "schema_objects_digest": self.schema_objects_digest,
            "schema_extensions": list(self.schema_extensions),
            "migrations": list(self.migrations),
            "migration_chain_digest": self.migration_chain_digest,
            "db_path": self.db_path,
            "observed_objects": (list(self.observed_objects)
                                  if self.observed_objects is not None else None),
            "observed_objects_digest": self.observed_objects_digest,
            "missing_objects": list(self.missing_objects),
            "unexpected_objects": list(self.unexpected_objects),
            "db_schema_version": self.db_schema_version,
            "memory_docs_submitted": self.memory_docs_submitted,
            "canonical_memory_mutation": self.canonical_memory_mutation,
            "production_runtime_imported": self.production_runtime_imported,
            "promotion_attempted": self.promotion_attempted,
        }

    @property
    def receipt_digest(self) -> str:
        return _digest(self.to_dict())

    @property
    def receipt_id(self) -> str:
        return "schema_contract_freeze_" + self.receipt_digest.split(":", 1)[1][:24]

    @classmethod
    def from_dict(cls, payload: Mapping) -> "SchemaContractFreezeReceipt":
        if not isinstance(payload, Mapping):
            raise SchemaContractError("schema contract receipt must be an object")
        required = ("schema_path", "schema_sql_sha256", "schema_objects",
                    "schema_objects_digest", "schema_extensions", "migrations",
                    "migration_chain_digest",
                    "db_path", "observed_objects", "observed_objects_digest",
                    "missing_objects", "unexpected_objects", "db_schema_version")
        missing = [field for field in required if field not in payload]
        if missing:
            raise SchemaContractError("schema contract receipt missing " + ", ".join(missing))
        observed = payload["observed_objects"]
        return cls(
            schema_path=payload["schema_path"], schema_sql_sha256=payload["schema_sql_sha256"],
            schema_objects=tuple(payload["schema_objects"]),
            schema_objects_digest=payload["schema_objects_digest"],
            schema_extensions=tuple(payload["schema_extensions"]),
            migrations=tuple(payload["migrations"]),
            migration_chain_digest=payload["migration_chain_digest"], db_path=payload["db_path"],
            observed_objects=(tuple(observed) if observed is not None else None),
            observed_objects_digest=payload["observed_objects_digest"],
            missing_objects=tuple(payload["missing_objects"]),
            unexpected_objects=tuple(payload["unexpected_objects"]),
            db_schema_version=payload["db_schema_version"], version=payload.get("version"),
            memory_docs_submitted=payload.get("memory_docs_submitted"),
            canonical_memory_mutation=payload.get("canonical_memory_mutation"),
            production_runtime_imported=payload.get("production_runtime_imported"),
            promotion_attempted=payload.get("promotion_attempted"),
        )


def _build_receipt(*, schema_path: Path, db_path: Path | None) -> SchemaContractFreezeReceipt:
    schema_objects = _schema_objects(schema_path)
    schema_extensions = _schema_extension_contract()
    migrations_contract = _migration_contract()
    observed = None
    observed_digest = None
    db_schema_version = None
    missing: tuple[str, ...] = ()
    unexpected: tuple[str, ...] = ()
    if db_path is not None:
        if not db_path.is_file():
            raise SchemaContractError(f"TEHM DB is not a file: {db_path}")
        sidecars = _sidecars(db_path)
        if sidecars:
            raise SchemaContractError("TEHM DB has WAL/SHM sidecars: " + ", ".join(sidecars))
        try:
            conn = connect_read_only(db_path)
        except (OSError, RuntimeError, ValueError) as exc:
            raise SchemaContractError(f"cannot open TEHM DB read-only: {db_path}") from exc
        try:
            observed = _introspect(conn)
            row = conn.execute("SELECT value FROM tehm_meta WHERE key='schema_version'").fetchone()
            db_schema_version = row[0] if row else None
        finally:
            conn.close()
        observed_digest = _digest(observed)
        declared_by_name = {(item["type"], item["name"]): item for item in schema_objects}
        observed_by_name = {(item["type"], item["name"]): item for item in observed}
        # Additive state tables are created lazily by relation/resolution
        # writers.  They are part of the contract when present, but their
        # absence on an otherwise complete v4 store is valid.
        extension_keys = {(item["type"], item["name"])
                          for item in _extension_objects()}
        missing = tuple(sorted(
            f"{key[0]}:{key[1]}" for key in declared_by_name.keys() - observed_by_name.keys()
            if key not in extension_keys))
        unexpected = tuple(sorted(f"{key[0]}:{key[1]}" for key in observed_by_name.keys() - declared_by_name.keys()))
        altered = tuple(sorted(key[1] for key in declared_by_name.keys() & observed_by_name.keys()
                               if declared_by_name[key] != observed_by_name[key]))
        if altered:
            missing = tuple(sorted((*missing, *("altered:" + name for name in altered))))
    return SchemaContractFreezeReceipt(
        schema_path=str(schema_path.resolve()), schema_sql_sha256=_sha256_file(schema_path),
        schema_objects=schema_objects, schema_objects_digest=_digest(schema_objects),
        schema_extensions=schema_extensions,
        migrations=migrations_contract, migration_chain_digest=_digest(migrations_contract),
        db_path=str(db_path.resolve()) if db_path is not None else None,
        observed_objects=observed, observed_objects_digest=observed_digest,
        missing_objects=missing, unexpected_objects=unexpected,
        db_schema_version=db_schema_version,
    )


def freeze_schema_contract(*, schema_path: Path | None = None,
                           db_path: Path | None = None, output: Path | None = None) -> dict:
    """Create a P16 schema contract, optionally bound to a read-only TEHM DB."""
    schema_path = (schema_path or config.SCHEMA_PATH).expanduser().resolve()
    db_path = db_path.expanduser().resolve() if db_path is not None else None
    if output is not None:
        output_resolved = output.expanduser().resolve()
        if output_resolved == schema_path:
            raise SchemaContractError("schema contract output cannot overwrite schema.sql")
        if db_path is not None and output_resolved == db_path:
            raise SchemaContractError("schema contract output cannot overwrite the TEHM DB")
    receipt = _build_receipt(schema_path=schema_path, db_path=db_path)
    report = {"receipt": {**receipt.to_dict(), "receipt_id": receipt.receipt_id,
                           "receipt_digest": receipt.receipt_digest},
              "receipt_id": receipt.receipt_id, "receipt_digest": receipt.receipt_digest,
              "schema_contract": receipt.to_dict(),
              "memory_docs_submitted": False, "canonical_memory_mutation": "none",
              "production_runtime_imported": False, "promotion_attempted": False}
    if output is not None:
        output = output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def replay_schema_contract(report_path: Path, *, schema_path: Path | None = None,
                           db_path: Path | None = None) -> SchemaContractFreezeReceipt:
    """Replay an external schema contract and fail closed on drift."""
    report_path = report_path.expanduser().resolve()
    try:
        report = json.loads(report_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise SchemaContractError(f"cannot read schema contract report: {report_path}") from exc
    if not isinstance(report, Mapping):
        raise SchemaContractError("schema contract report must be an object")
    payload = report.get("schema_contract") or report.get("receipt")
    receipt = SchemaContractFreezeReceipt.from_dict(payload)
    duplicate = report.get("receipt")
    if duplicate is not None and report.get("schema_contract") is not None:
        if not isinstance(duplicate, Mapping):
            raise SchemaContractError("schema contract receipt copy is malformed")
        duplicate_core = dict(duplicate)
        duplicate_id = duplicate_core.pop("receipt_id", None)
        duplicate_digest = duplicate_core.pop("receipt_digest", None)
        if duplicate_core != dict(payload) or duplicate_id != receipt.receipt_id or \
                duplicate_digest != receipt.receipt_digest:
            raise SchemaContractError("schema contract receipt copies disagree")
    expected_digest = report.get("receipt_digest")
    if expected_digest != receipt.receipt_digest:
        raise SchemaContractError("schema contract receipt digest mismatch")
    for field, expected in (("memory_docs_submitted", False),
                            ("canonical_memory_mutation", "none"),
                            ("production_runtime_imported", False),
                            ("promotion_attempted", False)):
        if report.get(field) != expected:
            raise SchemaContractError(f"schema contract report violates {field}")
    schema = (schema_path or Path(receipt.schema_path)).expanduser().resolve()
    if _sha256_file(schema) != receipt.schema_sql_sha256:
        raise SchemaContractError("schema.sql digest drifted since freeze")
    fresh = _build_receipt(schema_path=schema,
                           db_path=(db_path.expanduser().resolve() if db_path is not None
                                    else (Path(receipt.db_path) if receipt.db_path else None)))
    if fresh.to_dict() != receipt.to_dict():
        raise SchemaContractError("schema contract replay mismatch")
    return receipt
