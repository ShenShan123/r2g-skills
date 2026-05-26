#!/usr/bin/env python3
"""Detect family/platform degradation by comparing recent runs to historical baseline.

Usage:
  monitor_health.py [--db <path>] [--window N] [--threshold F]

Inspired by OpenSpace's quality monitoring with cascade evolution triggers.
Outputs a JSON array of alerts for families whose recent success rate has
dropped below the historical baseline by more than the threshold.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import sys
from pathlib import Path

import knowledge_db

_DRC_OK = {None, "clean", "skipped"}
_LVS_OK = {None, "clean", "skipped"}
_RCX_OK = {None, "complete", "skipped"}


def _is_success(row: dict) -> bool:
    return (
        row.get("orfs_status") == "pass"
        and row.get("drc_status") in _DRC_OK
        and row.get("lvs_status") in _LVS_OK
        and row.get("rcx_status") in _RCX_OK
    )


def _fetch_all(conn) -> list[dict]:
    cur = conn.execute(
        "SELECT design_family, platform, orfs_status, drc_status, "
        "lvs_status, rcx_status, ingested_at "
        "FROM runs ORDER BY ingested_at ASC"
    )
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def check(db_path: Path | str,
          window: int = 5,
          threshold: float = 0.3) -> list[dict]:
    """Check for degraded family/platform pairs.

    Args:
        db_path: Path to runs.sqlite.
        window: Number of most recent runs to evaluate per family/platform.
        threshold: Minimum drop in success rate (recent vs historical) to alert.

    Returns:
        List of alert dicts with keys: family, platform, recent_success_rate,
        historical_success_rate, recent_window, total_runs, severity,
        recent_failures.
    """
    with contextlib.closing(knowledge_db.connect(db_path)) as conn:
        rows = _fetch_all(conn)

    groups: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        fam = r.get("design_family") or "unknown"
        plat = r.get("platform") or "unknown"
        groups.setdefault((fam, plat), []).append(r)

    alerts = []
    for (fam, plat), group in sorted(groups.items()):
        if len(group) < window:
            continue

        recent = group[-window:]
        historical = group[:-window] if len(group) > window else group

        recent_successes = sum(1 for r in recent if _is_success(r))
        recent_rate = recent_successes / len(recent)

        hist_successes = sum(1 for r in historical if _is_success(r))
        hist_rate = hist_successes / len(historical) if historical else 1.0

        drop = hist_rate - recent_rate
        if drop < threshold:
            continue

        recent_failures = [
            r.get("orfs_status", "unknown")
            for r in recent if not _is_success(r)
        ]

        alerts.append({
            "family": fam,
            "platform": plat,
            "recent_success_rate": round(recent_rate, 3),
            "historical_success_rate": round(hist_rate, 3),
            "recent_window": window,
            "total_runs": len(group),
            "severity": "degraded" if recent_rate < 0.5 else "warning",
            "recent_failures": recent_failures,
        })

    return alerts


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--db", type=Path, default=knowledge_db.DEFAULT_DB_PATH)
    p.add_argument("--window", type=int, default=5,
                   help="Number of recent runs to evaluate (default: 5)")
    p.add_argument("--threshold", type=float, default=0.3,
                   help="Minimum success rate drop to alert (default: 0.3)")
    p.add_argument("--out", type=Path, default=None,
                   help="Write alerts JSON to file (default: stdout)")
    args = p.parse_args()

    alerts = check(args.db, window=args.window, threshold=args.threshold)

    output = json.dumps(alerts, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output, encoding="utf-8")
        print(f"Wrote {len(alerts)} alert(s) to {args.out}")
    else:
        print(output)

    if alerts:
        for a in alerts:
            print(f"  [{a['severity'].upper()}] {a['family']}/{a['platform']}: "
                  f"recent {a['recent_success_rate']:.0%} vs "
                  f"historical {a['historical_success_rate']:.0%}",
                  file=sys.stderr)
    else:
        print("All family/platform pairs healthy.", file=sys.stderr)

    return 1 if alerts else 0


if __name__ == "__main__":
    sys.exit(main())
