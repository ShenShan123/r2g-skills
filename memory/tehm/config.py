"""TEHM configuration: env knobs, default paths, schema version lock.

Backend choice is locked at process start (design doc 17.3). TEHM never opens
the legacy ``knowledge.sqlite``; ``TEHM_DB`` points at the TEHM store only.
"""
from __future__ import annotations

import os
from pathlib import Path

# Package root: <memory>/tehm/
PACKAGE_DIR = Path(__file__).resolve().parent
SCHEMA_PATH = PACKAGE_DIR / "schema.sql"

# The one place to edit when bumping the DB schema (migrations are applied by
# db.ensure_schema against this version).  Runtime payloads import the public
# ``tehm.SCHEMA_VERSION`` constant, which must stay in lock-step with this
# value.
DB_SCHEMA_VERSION = 4

# Legacy isolation (honesty H5): pointing TEHM at the legacy DB is a fail-closed
# error, never a silent re-read.
LEGACY_KNOWLEDGE_DB_HINT = "knowledge.sqlite"


def default_db_path() -> Path:
    """``TEHM_DB`` env override, else <memory>/tehm.sqlite."""
    raw = os.environ.get("TEHM_DB")
    if raw:
        return Path(raw).expanduser().resolve()
    return PACKAGE_DIR.parent / "tehm.sqlite"


def default_artifact_root() -> Path:
    """``TEHM_ARTIFACTS_ROOT`` env override, else <memory>/artifacts/."""
    raw = os.environ.get("TEHM_ARTIFACTS_ROOT")
    if raw:
        return Path(raw).expanduser().resolve()
    return PACKAGE_DIR.parent / "artifacts"


def db_path_from_env_or(args_db: str | None) -> Path:
    """Resolve ``--db`` (highest priority) > ``TEHM_DB`` > default."""
    if args_db:
        return Path(args_db).expanduser().resolve()
    return default_db_path()


def validate_backend_lock(db_path: Path) -> None:
    """Fail-closed guard: TEHM must never be pointed at legacy memory files."""
    if db_path.name == LEGACY_KNOWLEDGE_DB_HINT or "signoff-loop" in str(db_path):
        raise ValueError(
            f"TEHM backend must be isolated from legacy memory (honesty H5); "
            f"refusing to use {db_path}. Set TEHM_DB to a dedicated path."
        )
