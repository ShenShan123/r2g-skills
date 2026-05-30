"""r2g run-ledger writer package (TEACHING_POLICY §12)."""

from .canonical import (
    canonical_json_bytes,
    compute_record_hash,
    verify_record_hash,
)
from .append_ledger import (
    append_record,
    ALLOWED_TRIGGERS,
    FORBIDDEN_TRIGGER,
)
from .metrics_parsers import METRICS_PARSERS, get_parser

__all__ = [
    "canonical_json_bytes",
    "compute_record_hash",
    "verify_record_hash",
    "append_record",
    "ALLOWED_TRIGGERS",
    "FORBIDDEN_TRIGGER",
    "METRICS_PARSERS",
    "get_parser",
]
