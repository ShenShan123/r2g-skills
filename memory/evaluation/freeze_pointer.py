"""Resolve the canonical TEHM freeze without host-specific paths."""
from __future__ import annotations

import json
import os
from pathlib import Path


POINTER_PATH = Path(__file__).resolve().parent / "canonical_freeze_pointer_v1.json"


def load_pointer(path: Path | None = None) -> dict:
    path = Path(path or POINTER_PATH).resolve()
    data = json.loads(path.read_text())
    if data.get("version") != "tehm-canonical-freeze-pointer-v1":
        raise ValueError(f"unsupported canonical freeze pointer: {data.get('version')!r}")
    return data


def resolve_bundle(path: Path | None = None, *, require_exists: bool = True) -> Path:
    """Resolve ``TEHM_CANONICAL_BUNDLE`` or the pointer's relative locator."""
    pointer_path = Path(path or POINTER_PATH).resolve()
    pointer = load_pointer(pointer_path)
    override = os.environ.get("TEHM_CANONICAL_BUNDLE")
    raw = override or pointer.get("canonical_bundle")
    if not raw:
        raise ValueError("canonical freeze pointer has no canonical_bundle")
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = (pointer_path.parent / candidate).resolve()
    else:
        candidate = candidate.resolve()
    if require_exists and not candidate.is_dir():
        raise FileNotFoundError(
            f"canonical TEHM bundle is unavailable: {candidate}; "
            "provide TEHM_CANONICAL_BUNDLE or restore the portable evidence directory")
    return candidate
