#!/usr/bin/env python3
"""Cross-check that every LEDGER-clean design is signoff-backed — the "bug-#7" gate
the two memory DBs structurally cannot see.

`honesty.py` / `check_db_integrity.py` audit knowledge (+ journal); NEITHER audits
the campaign ledger. So a `clean` the LEDGER claims that no signoff can back slips
past both — exactly the 2026-07-02 "fabricated clean via cleared route abort" class.
This tool closes that blind spot: for every ledger-clean design it demands evidence,
and it EXITS NON-ZERO when a clean has no honest backing.

WHY A DEDICATED, TESTED TOOL (added 2026-07-07)
-----------------------------------------------
This gate previously lived only as an inline heredoc in the /r2g-debug command and
rotted untested. Its join

    SELECT drc_status, lvs_status FROM runs
    WHERE project_path LIKE '%'||<basename>  ORDER BY ingested_at DESC LIMIT 1

broke the moment the sky130 campaign store was unioned into `main` (commit ad81aec):

  1. Design names are FULL of `_`, and in SQL `LIKE` an underscore is a single-char
     wildcard -> `%foo_bar` also matches `fooXbar`, so the "latest" row could be a
     DIFFERENT design entirely.
  2. NO `platform=` scope + `ORDER BY ingested_at DESC` -> after the union, a newer
     OTHER-PLATFORM row wins. e.g. DMA_Controller_DMA_fsm grabbed its nangate45
     (None,None) row and was reported "FABRICATED-CLEAN" though its sky130hd run is
     clean/clean. The gate cried wolf on ~197/593 designs.
  3. The SAME fragility MASKED the real gap: a stale prior-round `<design>__sky130hd`
     dir (wiped from disk, still in knowledge) counted as "backed", hiding ~500
     sky130 cleans that were signed off on disk but never (re)ingested into `main`.

So this tool joins on the EXACT `project_path` scoped to `--platform`, and separates
the THREE cases the old lumped "bad" conflated. It deliberately does NOT fall back to
a `<path>__<platform>` variant dir: those are a PRIOR round's physical runs (the
current campaign builds at the plain `<design>` path), so a June `<design>__sky130hd`
run — clean OR null — is the wrong run to judge a July ledger claim by. Consulting it
both over-credited (~385 stale June cleans counted as "backed") and false-alarmed (~12
null June runs flagged fabricated though the design is on-disk clean). Only the plain
path's own platform run, else the plain path's on-disk reports, may back the claim:

On-disk tool truth WINS the "is-the-loop-lying" call (an intermediate mismatch run
can be ingested and then superseded on disk by a clean re-sign-off that was never
re-ingested — same ppa.json-purge root cause — so a stale non-clean knowledge row is
NOT proof of a fabrication):

  backed        knowledge has a fresh <platform> run and drc+lvs are clean.  OK.
  not_ingested  the on-disk reports/{drc,lvs}.json ARE clean but knowledge does not
                reflect it — the run signed off clean yet was never (re)ingested
                (ppa.json purged so ingest's run_id key is gone, or an incomplete
                store union). Tagged `stale_knowledge` (a non-clean knowledge run
                exists) or `no_run`.  WARN (exit 0).  Remediate:
                tools/reconcile_sky130_campaign.py --apply <design>
  fabricated    NO clean evidence anywhere: knowledge non-clean/absent AND the on-disk
                reports are non-clean or missing, yet the ledger says clean  ->  the
                loop is lying.                                        ALARM (exit!=0)

See references/failure-patterns.md -> "Ledger-signoff gate mis-join (LIKE/platform)".

Usage:
  python3 tools/check_ledger_signoff_backed.py [--platform sky130hd] \
      [--ledger design_cases/_batch/sky130hd_campaign.jsonl] \
      [--db r2g-rtl2gds/knowledge/knowledge.sqlite] [--verbose]
Exit status: 0 if no fabrication (WARN-only is still 0); non-zero if any fabrication.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEF_DB = ROOT / "r2g-rtl2gds/knowledge/knowledge.sqlite"

# A legitimately skipped check IS clean (fail-closed whitelist mirrors the loop's
# _mark_clean gate). clean_beol = FEOL-hang BEOL-only DRC pass; lvs never has it.
CLEAN_DRC = {"clean", "clean_beol", "skipped"}
CLEAN_LVS = {"clean", "skipped"}


def _load_ledger(ledger_path: Path) -> dict:
    """Last-writer-wins per design (the ledger is append-only state transitions)."""
    last: dict = {}
    with open(ledger_path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            if d.get("design"):
                last.setdefault(d["design"], {}).update(d)
    return last


def _latest_run(con: sqlite3.Connection, project_path: str, platform: str):
    return con.execute(
        "SELECT drc_status, lvs_status FROM runs WHERE project_path=? AND platform=? "
        "ORDER BY ingested_at DESC LIMIT 1",
        (project_path, platform),
    ).fetchone()


def _ondisk_status(proj_dir: Path, check: str):
    try:
        return json.loads((proj_dir / "reports" / f"{check}.json").read_text()).get("status")
    except Exception:
        return None


def _clean(drc, lvs) -> bool:
    return (drc or "") in CLEAN_DRC and (lvs or "") in CLEAN_LVS


def classify(rows: dict, con: sqlite3.Connection, platform: str, root: Path) -> dict:
    """Bucket every ledger-clean, non-ab_arm design. Pure over its inputs (testable).

    On-disk tool truth WINS the "is-the-loop-lying" call: `fabricated` (ALARM) means
    NO clean evidence anywhere. If knowledge shows non-clean/absent but the on-disk
    `reports/{drc,lvs}.json` ARE clean, the design signed off clean and knowledge is
    merely stale/behind -> `not_ingested` (WARN), tagged `stale_knowledge` (a knowledge
    run exists but is non-clean) or `no_run` (no knowledge run at all).
    """
    res = {"backed": [], "fabricated": [], "not_ingested": []}
    for name, row in rows.items():
        if row.get("state") != "clean" or row.get("kind") == "ab_arm":
            continue
        pp = row.get("project_path") or str(root / "design_cases" / name)
        r = _latest_run(con, pp, platform)
        if r is not None and _clean(r[0], r[1]):
            res["backed"].append(name)
            continue
        # Knowledge does not FRESHLY show clean -> the on-disk reports decide honesty.
        proj = Path(pp) if Path(pp).is_dir() else (root / "design_cases" / name)
        ddrc, dlvs = _ondisk_status(proj, "drc"), _ondisk_status(proj, "lvs")
        if _clean(ddrc, dlvs):
            note = "stale_knowledge" if r is not None else "no_run"
            res["not_ingested"].append((name, ddrc, dlvs, note))
        else:
            kd = f"knowledge drc={r[0]} lvs={r[1]}" if r is not None else "no knowledge run"
            res["fabricated"].append((name, kd, ddrc, dlvs))
    return res


def report(res: dict, verbose: bool) -> int:
    backed, fab, ni = res["backed"], res["fabricated"], res["not_ingested"]
    total = len(backed) + len(fab) + len(ni)
    print(f"== ledger-clean signoff backing ({total} clean designs) ==")
    print(f"  [ok  ] backed by a fresh knowledge signoff : {len(backed)}")
    stale = sum(1 for e in ni if e[3] == "stale_knowledge")
    tag_ni = "warn" if ni else "ok  "
    print(f"  [{tag_ni}] ledger-clean but NOT ingested        : {len(ni)}  "
          f"({stale} stale-knowledge, {len(ni)-stale} no-run; on-disk clean — "
          f"run reconcile_sky130_campaign.py --apply)")
    tag_fab = "ALARM" if fab else "ok  "
    print(f"  [{tag_fab}] FABRICATED clean (no honest backing) : {len(fab)}")
    if verbose and ni:
        print("  -- not-ingested (honest-but-incomplete) --")
        for name, ddrc, dlvs, note in ni[:40]:
            print(f"       {name}: on-disk drc={ddrc} lvs={dlvs} ({note})")
    if fab:
        print("  -- FABRICATED (the loop is lying — fix before trusting this round) --")
        for name, kd, ddrc, dlvs in fab[:60]:
            print(f"       {name}: {kd}; on-disk drc={ddrc} lvs={dlvs}")
    verdict = "ALARM" if fab else ("WARN" if ni else "PASS")
    print(f"== verdict: {verdict}  (fabricated={len(fab)}, not_ingested={len(ni)}, backed={len(backed)}) ==")
    return 1 if fab else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--platform", default="sky130hd")
    ap.add_argument("--ledger", type=Path, default=None,
                    help="default: design_cases/_batch/<platform>_campaign.jsonl")
    ap.add_argument("--db", type=Path, default=DEF_DB)
    ap.add_argument("--root", type=Path, default=ROOT,
                    help="repo root holding design_cases/ (for on-disk report fallback)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    ledger = args.ledger or (args.root / "design_cases/_batch" / f"{args.platform}_campaign.jsonl")
    if not ledger.exists():
        print(f"no ledger at {ledger} — nothing to check (fresh platform round?)")
        return 0
    rows = _load_ledger(ledger)
    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    try:
        res = classify(rows, con, args.platform, args.root)
    finally:
        con.close()
    return report(res, args.verbose)


if __name__ == "__main__":
    sys.exit(main())
