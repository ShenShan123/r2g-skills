#!/usr/bin/env python3
"""Extract SIGNOFF-grade Calibre DRC results into a JSON summary.

Parses the Calibre ASCII DRC *results database* (`<design>.drc.results`, written by
`DRC RESULTS DATABASE` in the runset) — and, as a fallback, the human-readable DRC
*summary report* — into the SAME schema as extract_drc.py so ingest_run.py can consume
either engine's verdict:

    {status, total_violations, raw_marker_count, categories, engine, log_info, ...}

`engine: "calibre"` distinguishes an authoritative asap7 verdict from the KLayout
`asap7.lydrc` one (which carries a known false-violation floor; see failure-patterns.md
"ASAP7 residual-DRC-by-design"). status ∈ {clean, fail, stale, skipped, unknown}.

Calibre ASCII DRC results-database layout (the only part we need is the per-rulecheck
HEADER line — we never parse the coordinate records):

    <primary_cell_name>
    <precision_int> <precision_int>
    <RULECHECK_NAME_1>
    <result_count> <original_count> <runtime>     <- header; result_count is what we sum
    <result records ...>
    <RULECHECK_NAME_2>
    <result_count> <original_count> <runtime>
    ...

Honesty: this extractor ships with the mtime freshness guard from day one (the DRC leg
of the 2026-06-30 fabricated-clean bug taught us extractors must refuse to certify clean
from artifacts older than the run that just executed).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# Atomic report writes: a kill -9/OOM mid-write must never leave a torn
# reports/*.json for ingest to misread (2026-07-04 robustness audit M1).
import report_io

# A results-DB rulecheck header: "<count> <orig> <runtime>" (3 numeric tokens, the
# 3rd may be float / scientific). Coordinate records in the DB come in 2- or 4-token
# lines, so a strict 3-token match rarely collides.
_HEADER_RE = re.compile(r'^\s*(\d+)\s+(\d+)\s+[\d.eE+-]+\s*$')
# A plausible rulecheck NAME: contains a letter (e.g. "M1.S.1", "V2.M3.AUX.2").
_NAME_RE = re.compile(r'[A-Za-z]')


def parse_results_db(db_path: Path) -> dict:
    """Return {rulecheck_name: violation_count} from a Calibre ASCII DRC results DB."""
    if not db_path.exists():
        return {}
    lines = db_path.read_text(encoding='utf-8', errors='ignore').splitlines()
    cats: dict[str, int] = {}
    prev_name = None
    for ln in lines:
        stripped = ln.strip()
        m = _HEADER_RE.match(ln)
        if m and prev_name is not None:
            # `prev_name` is the rulecheck this header belongs to.
            count = int(m.group(1))
            cats[prev_name] = cats.get(prev_name, 0) + count
            prev_name = None  # consumed; next name resets it
            continue
        # Track the most recent line that looks like a rulecheck NAME (letters,
        # not itself a numeric/coordinate line and not a p/e result header).
        if stripped and _NAME_RE.search(stripped) and not stripped[:1] in ('p', 'e') \
                and not _HEADER_RE.match(ln):
            prev_name = stripped
        elif not stripped:
            # blank line does not clear a pending name (names may precede a header
            # after a blank), but a coordinate/record line does.
            pass
    return cats


def parse_summary_total(summary_path: Path) -> int:
    """Fallback: pull a total from the Calibre DRC summary report. -1 if unknown."""
    if not summary_path.exists():
        return -1
    text = summary_path.read_text(encoding='utf-8', errors='ignore')
    # Sum explicit per-check "TOTAL Result Count = N" if present.
    per = [int(n) for n in re.findall(r'TOTAL Result Count\s*=\s*(\d+)', text)]
    if per:
        return sum(per)
    m = re.search(r'TOTAL DRC RESULTS GENERATED[^\d]*(\d+)', text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return -1


def artifacts_stale(cal_dir: Path, tol: float = 2.0) -> bool:
    """True when a fresh calibre_drc_run.log post-dates the results DB/summary.

    Mirrors extract_drc.artifacts_stale: run_calibre_drc.sh writes the run log, then
    Calibre writes the results DB during the run, so in a healthy run the DB is not
    older than the log. A stale DB under a fresh log means the run produced no fresh
    results here — never certify clean from it. Returns False when no run log exists
    (unit fixtures) so those callers are unaffected.
    """
    runlog = cal_dir / 'calibre_drc_run.log'
    if not runlog.exists():
        return False
    run_m = runlog.stat().st_mtime
    arts = list(cal_dir.glob('*.drc.results')) + list(cal_dir.glob('*.drc.summary'))
    present = [p.stat().st_mtime for p in arts if p.exists()]
    if not present:
        return True
    return max(present) < run_m - tol


def main():
    if len(sys.argv) < 3:
        print('usage: extract_calibre_drc.py <project-root> <output.json>', file=sys.stderr)
        sys.exit(1)
    project_root = Path(sys.argv[1])
    out_path = Path(sys.argv[2])

    drc_dir = project_root / 'drc'
    cal_dir = drc_dir / 'calibre'

    # Honor a fresh skip/incompatible/timeout marker from run_calibre_drc.sh: it is the
    # authoritative status when Calibre could not run (deck missing, version-incompatible,
    # timed out). Only defer to it when it is at least as fresh as the run artifacts.
    marker = drc_dir / 'calibre_drc_result.json'
    runlog = cal_dir / 'calibre_drc_run.log'
    if marker.exists():
        try:
            md = json.loads(marker.read_text(encoding='utf-8'))
        except (ValueError, OSError):
            md = None
        run_m = runlog.stat().st_mtime if runlog.exists() else 0.0
        if md and md.get('status') in ('skipped', 'incompatible', 'timeout') \
                and marker.stat().st_mtime >= run_m - 2.0:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            report_io.write_json_atomic(out_path, md)
            print(out_path)
            return

    db = next(iter(sorted(cal_dir.glob('*.drc.results'))), None)
    summary = next(iter(sorted(cal_dir.glob('*.drc.summary'))), None)

    categories: dict[str, dict] = {}
    total = -1
    if db is not None:
        cats = parse_results_db(db)
        categories = {k: {'count': v, 'description': ''} for k, v in cats.items()}
        total = sum(cats.values())
    if total < 0 and summary is not None:
        total = parse_summary_total(summary)

    stale = artifacts_stale(cal_dir)

    if total == 0:
        status = 'clean'
    elif total > 0:
        status = 'fail'
    else:
        status = 'unknown'

    # HONESTY GUARD: never certify clean from Calibre artifacts older than the run.
    if stale and status == 'clean':
        status = 'stale'
        total = -1

    result = {
        'status': status,
        'total_violations': total if total >= 0 else None,
        'raw_marker_count': total if total >= 0 else None,
        'categories': categories,
        'engine': 'calibre',
        'drc_mode': 'full_signoff',
        'log_info': {'results_db': str(db) if db else None,
                     'summary_report': str(summary) if summary else None},
    }
    if stale:
        result['note'] = ('stale Calibre DRC artifacts (older than calibre_drc_run.log); '
                          're-run signoff')

    out_path.parent.mkdir(parents=True, exist_ok=True)
    report_io.write_json_atomic(out_path, result)
    print(out_path)


if __name__ == '__main__':
    main()
