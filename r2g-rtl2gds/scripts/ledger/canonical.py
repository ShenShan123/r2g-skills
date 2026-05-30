"""Canonical JSON serialization and record hashing.

This module is the *single source of truth* for how a ledger record is turned
into bytes for hashing. Both the writer (``append_ledger.py``) and the
autograder verifier MUST use this exact implementation; any divergence breaks
chain verification.

The canonical form is defined as:

- ``json.dumps`` with ``sort_keys=True``
- compact separators ``(",", ":")`` (no spaces)
- ``ensure_ascii=False`` (UTF-8 preserved, non-ASCII not escaped)
- ``allow_nan=False`` (NaN / inf disallowed; metric parsers must emit None)
- newline characters NOT permitted anywhere in the canonical bytes

The record hash is ``sha256_hex(canonical_json(record_without_record_hash))``.

Design notes:

* We treat the record dict as opaque key/value structure. Keys are strings;
  values may be str / int / float / bool / None / list / dict. NaN and inf
  are forbidden — if a metric parser cannot determine a value, it must emit
  ``None``, not ``float("nan")``.
* We deliberately do NOT include trailing newline in canonical output. The
  JSONL line separator is appended by the writer, not the canonicalizer.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping

CANONICAL_SEPARATORS = (",", ":")
HASH_EXCLUDED_FIELD = "record_hash"


def _validate_no_nan(obj: Any, path: str = "") -> None:
    """Recursively reject NaN / inf. Float specials would round-trip in JSON
    only with allow_nan=True, which is non-portable and dangerous for hashing.
    Metric parsers must convert unknown values to None instead.
    """
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            raise ValueError(
                f"NaN/inf not allowed in canonical JSON at {path or '<root>'}; "
                "metric parsers must emit None for unknown values"
            )
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if not isinstance(k, str):
                raise TypeError(
                    f"non-string key at {path}: {type(k).__name__}"
                )
            _validate_no_nan(v, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _validate_no_nan(v, f"{path}[{i}]")
    elif obj is None or isinstance(obj, (bool, int, str)):
        return
    else:
        raise TypeError(
            f"unsupported type at {path}: {type(obj).__name__}"
        )


def canonical_json_bytes(record: Mapping[str, Any]) -> bytes:
    """Return the canonical UTF-8 byte encoding of ``record``.

    The ``record_hash`` field, if present, is excluded — it's the field we are
    *computing* and cannot recursively include itself.
    """
    payload = {k: v for k, v in record.items() if k != HASH_EXCLUDED_FIELD}
    _validate_no_nan(payload)
    text = json.dumps(
        payload,
        sort_keys=True,
        separators=CANONICAL_SEPARATORS,
        ensure_ascii=False,
        allow_nan=False,
    )
    if "\n" in text or "\r" in text:
        # Should never happen with the above flags, but defensive.
        raise ValueError("canonical JSON must not contain newline characters")
    return text.encode("utf-8")


def compute_record_hash(record: Mapping[str, Any]) -> str:
    """SHA-256 hex digest of the canonical bytes of ``record``."""
    return hashlib.sha256(canonical_json_bytes(record)).hexdigest()


def verify_record_hash(record: Mapping[str, Any]) -> bool:
    """Return True iff record['record_hash'] matches the recomputed digest."""
    claimed = record.get(HASH_EXCLUDED_FIELD)
    if not isinstance(claimed, str) or len(claimed) != 64:
        return False
    return compute_record_hash(record) == claimed
