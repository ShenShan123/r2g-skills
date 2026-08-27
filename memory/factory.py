"""MemoryBackend factory (design doc 17.2-17.4).

Backend selection is locked at process start via ``R2G_MEMORY_BACKEND``
(``none`` | ``legacy`` | ``tehm``, default ``legacy``). Switching mid-run is
forbidden; an invalid name is a hard error, never a silent fallback (H12).
"""
from __future__ import annotations

import os
from pathlib import Path

from contracts import DEFAULT_BACKEND, BACKEND_NAMES

_opened_backend: str | None = None
_opened_signature: str | None = None


class BackendLockError(RuntimeError):
    """Raised when a process tries to open a different backend mid-run."""


def open_memory_backend(
    name: str | None = None,
    *,
    experiment_root: Path | None = None,
    read_only_eval: bool | None = None,
    db_path: Path | None = None,
    artifact_root: Path | None = None,
    _lock: bool = True,
):
    """Open the memory backend named by ``name`` or ``R2G_MEMORY_BACKEND``.

    ``_lock=False`` is for tests only: it allows a fresh backend per fixture and
    never switches semantics. Production code must leave it True.
    """
    global _opened_backend, _opened_signature
    if read_only_eval is None:
        read_only_eval = os.environ.get("R2G_MEMORY_READ_ONLY_EVAL", "0") == "1"
    name = (name or os.environ.get("R2G_MEMORY_BACKEND") or DEFAULT_BACKEND).strip().lower()
    if name not in BACKEND_NAMES:
        raise ValueError(
            f"R2G_MEMORY_BACKEND must be one of {BACKEND_NAMES}, got {name!r} "
            f"(fail-closed; no silent fallback, H12)")

    if _lock and _opened_backend is not None and _opened_backend != name:
        raise BackendLockError(
            f"backend already opened as {_opened_backend!r}; refusing to switch "
            f"to {name!r} mid-run (design doc 17.3)")

    if name == "none":
        from none_backend import NoneMemoryBackend
        backend = NoneMemoryBackend()
    elif name == "legacy":
        from legacy_backend import LegacyMemoryBackend
        backend = LegacyMemoryBackend(
            knowledge_dir=None,
            experiment_root=experiment_root,
            read_only_eval=read_only_eval,
        )
    else:  # tehm
        from tehm_backend import TehmMemoryBackend
        backend = TehmMemoryBackend(
            db_path=db_path,
            artifact_root=artifact_root,
            read_only_eval=read_only_eval,
        )

    _opened_backend = name
    _opened_signature = f"{name}:{_fingerprint(backend)}"
    return backend


def opened_backend() -> str | None:
    return _opened_backend


def opened_signature() -> str | None:
    return _opened_signature


def reset() -> None:
    """Test-only: clear the process backend lock."""
    global _opened_backend, _opened_signature
    _opened_backend = None
    _opened_signature = None


def _fingerprint(backend) -> str:
    try:
        snap = backend.snapshot()
        return f"{snap.schema_version}:{snap.snapshot_id}"
    except Exception:  # noqa: BLE001
        return "n/a"
