#!/usr/bin/env python3
"""Re-ingest every finished design in design_cases/ into the knowledge store.

Phase-F (Task 20) enrichment driver: the fix-learning schema added run_violations
(one snapshot per run) + fix_events (where reports/fix_log.jsonl exists). Most of
the corpus was ingested BEFORE those tables existed, so run_violations is sparse.
This pass re-ingests every design dir that already has reports/ppa.json — pure
Python, no EDA compute — to populate run_violations corpus-wide and refresh
live fix_events under the corrected family namespace.

SQLite is single-writer, so this is intentionally a SERIAL loop over one
connection (parallel workers would only contend on the write lock). It does NOT
auto-learn per design (that would re-derive Tier-2/Tier-3 hundreds of times);
run learn_heuristics.py once after the pass.

Idempotent: ingest_run.ingest uses INSERT OR REPLACE on run_id and
INSERT OR IGNORE on fix_events, so re-running changes nothing new.

Usage:
  python3 tools/reingest_corpus.py                 # all dirs with reports/ppa.json
  python3 tools/reingest_corpus.py --only a b c    # just these design dirs
  python3 tools/reingest_corpus.py --limit 20      # first 20 (smoke test)
  python3 tools/reingest_corpus.py --db <path>     # override store
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "r2g-rtl2gds"
for p in (SKILL / "knowledge", SKILL / "scripts" / "reports"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import knowledge_db  # noqa: E402
import ingest_run  # noqa: E402


def _targets(cases_root: Path, only: list[str] | None) -> list[Path]:
    if only:
        return [cases_root / name for name in only]
    return sorted(
        d for d in cases_root.iterdir()
        if d.is_dir() and not d.name.startswith("_") and not d.name.startswith(".")
        and (d / "reports" / "ppa.json").exists()
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cases-root", type=Path, default=ROOT / "design_cases")
    ap.add_argument("--db", type=Path, default=knowledge_db.DEFAULT_DB_PATH)
    ap.add_argument("--families", type=Path, default=knowledge_db.DEFAULT_FAMILIES_PATH)
    ap.add_argument("--only", nargs="*", default=None, help="specific design dir names")
    ap.add_argument("--limit", type=int, default=None, help="cap number of designs")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    targets = _targets(args.cases_root, args.only)
    if args.limit:
        targets = targets[: args.limit]

    conn = knowledge_db.connect(args.db)
    knowledge_db.ensure_schema(conn)

    ok = 0
    failed: list[tuple[str, str]] = []
    skipped = 0
    t0 = time.time()
    for i, proj in enumerate(targets, 1):
        if not (proj / "reports" / "ppa.json").exists():
            skipped += 1
            continue
        try:
            ingest_run.ingest(proj, conn, families_path=args.families)
            ok += 1
        except Exception as exc:  # never let one bad design halt the corpus pass
            failed.append((proj.name, f"{type(exc).__name__}: {exc}"))
        if not args.quiet and (i % 50 == 0 or i == len(targets)):
            print(f"  [{i}/{len(targets)}] ok={ok} failed={len(failed)} "
                  f"skipped={skipped}  ({time.time() - t0:.0f}s)", flush=True)
    conn.close()

    rv = knowledge_db.connect(args.db)
    n_rv = rv.execute("SELECT COUNT(*) FROM run_violations").fetchone()[0]
    n_fe = rv.execute("SELECT COUNT(*) FROM fix_events").fetchone()[0]
    rv.close()

    print(f"\nre-ingest: {ok} ingested, {len(failed)} failed, {skipped} skipped "
          f"of {len(targets)} targets in {time.time() - t0:.0f}s")
    print(f"store now: run_violations={n_rv}  fix_events={n_fe}")
    if failed:
        print("FAILURES (design: error):")
        for name, err in failed[:40]:
            print(f"  {name}: {err}")
        if len(failed) > 40:
            print(f"  ... and {len(failed) - 40} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
