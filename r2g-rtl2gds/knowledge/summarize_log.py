#!/usr/bin/env python3
"""Deterministic, stdlib-only log/report summarizer (engineer-loop decision 10).

Produces the log_summaries digest rows and tool_bugs detections for the Tier-0
journal. NEVER an LLM call — pure text extraction, fully reproducible. Raw log
files may rotate; the digest stored in journal.sqlite survives.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# knowledge/ sibling import (works as script or test module)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import symptom  # noqa: E402

TAIL_LINES = 25
EXCERPT_CHARS = 2000

_ERROR_RE = re.compile(r"^\s*(\[ERROR\b|ERROR[: ]|.*\[ERROR )", re.I)
_WARN_RE = re.compile(r"^\s*(\[WARNING\b|WARNING[: ]|.*\[WARNING )", re.I)
# EDA-tool bug signatures -> normalized signature text (orfs_stage symptoms).
_BUG_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"signal 1[01]\b|SIGSEGV|Segmentation fault", re.I), "sigsegv"),
    (re.compile(r"assert(ion)? (fail|violat)", re.I), "internal_assertion"),
    (re.compile(r"std::bad_alloc|out of memory|OOM killer", re.I), "oom"),
    (re.compile(r"Killed\b.*timeout|TIMEOUT reached", re.I), "timeout"),
]


def summarize_text(text: str, *, status_hint: str | None = None) -> dict:
    lines = text.splitlines()
    errors = [ln for ln in lines if _ERROR_RE.match(ln)]
    warnings = [ln for ln in lines if _WARN_RE.match(ln)]
    status = status_hint or ("fail" if errors else "pass")
    failed = status not in ("pass", "clean", "complete")
    digest = (f"{status}: {len(errors)} errors, {len(warnings)} warnings, "
              f"{len(lines)} lines")
    if errors:
        digest += f"; first_error={errors[0].strip()[:120]}"
    return {
        "status": status,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "first_error": errors[0].strip()[:300] if errors else None,
        "last_lines": "\n".join(lines[-TAIL_LINES:]) if failed else None,
        "digest": digest,
    }


def summarize_file(path: Path | str, *, status_hint: str | None = None) -> dict:
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return {"status": "unknown", "error_count": None, "warning_count": None,
                "first_error": None, "last_lines": None,
                "digest": f"unreadable: {p}"}
    return summarize_text(text, status_hint=status_hint)


def summarize_report(report: dict, *, kind: str) -> dict:
    """Digest a parsed reports/<kind>.json (drc/lvs/rcx/ppa/timing_check)."""
    metrics: dict = {}
    for k in ("total_violations", "mismatch_count", "status", "tier",
              "wns_ns", "setup_wns"):
        if report.get(k) is not None:
            metrics[k] = report[k]
    cats = report.get("categories") or {}
    top = sorted(cats, key=lambda c: -(cats[c].get("count") or 0))[:5]
    digest = f"{kind} {report.get('status', 'unknown')}"
    if top:
        digest += " top:" + ",".join(f"{c}={cats[c].get('count')}" for c in top)
    return {"status": report.get("status"), "metrics": metrics, "digest": digest}


def detect_bugs(text: str, *, check: str = "orfs_stage",
                vclass: str | None = None) -> list[dict]:
    """Scan a log for EDA-tool bug signatures; tag each with its symptom_id so
    the journal-side bug links to knowledge-side symptoms (decision 11)."""
    bugs: list[dict] = []
    for ln in text.splitlines():
        for pat, label in _BUG_PATTERNS:
            if pat.search(ln):
                sig = symptom.canonical_signature(check, vclass or label, None)
                bugs.append({
                    "signature": f"{label}: {ln.strip()[:200]}",
                    "symptom_id": symptom.symptom_id(sig),
                    "signature_json": json.dumps(sig, sort_keys=True),
                    "log_excerpt": ln.strip()[:EXCERPT_CHARS],
                })
                break
    # One bug row per distinct label (first occurrence wins) — keep it bounded.
    seen, uniq = set(), []
    for b in bugs:
        lab = b["signature"].split(":", 1)[0]
        if lab not in seen:
            seen.add(lab)
            uniq.append(b)
    return uniq
