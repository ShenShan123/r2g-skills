#!/usr/bin/env python3
"""Re-root `project_path` in the memory DBs after the repo tree moves.

WHY THIS EXISTS
---------------
`project_path` is not a convenience field — it is the PROJECT IDENTITY KEY across the
whole knowledge layer:

  * `ingest_run.py`  UPDATEs the latest run row `WHERE project_path = ?`, and looks up
    a project's previous `cell_count` the same way;
  * `journal_db.py`  back-fills `actions.run_id` `WHERE project_path = ?` (the J2
    cross-DB linkage invariant);
  * `learn_heuristics.py` groups per-project evidence by it;
  * `check_ledger_signoff_backed.py` joins the ledger to `runs` on it EXACTLY (a
    deliberate choice — a `LIKE '%'||basename` join once cried wolf on ~197/593 rows
    and masked ~500 real gaps).

So when the repo tree is renamed or moved (2026-07-25: `agent-r2g` -> `r2g-skills`),
every stored path silently stops naming a real project. Nothing errors. Instead the
next campaign ingests each design as a BRAND NEW project with no history, the journal
back-fill stops linking, and the ledger<->DB join reports honest-looking "not ingested"
for a corpus that was ingested all along. See failure-patterns.md #57.

A move is a pure rename, so the repair is a pure prefix rewrite: everything at
`<any-root>/design_cases/<name>` belongs at `<live-root>/design_cases/<name>`. The old
root is DISCOVERED from the data (the last `/design_cases/` segment), never hardcoded,
so this works for the next move too.

USAGE
-----
    python3 tools/reroot_project_paths.py                 # dry run: report only
    python3 tools/reroot_project_paths.py --apply         # rewrite (backs up first)
    python3 tools/reroot_project_paths.py --apply --live-root /new/repo

Exit codes: 0 = nothing stale / applied cleanly, 1 = stale rows found (dry run),
2 = the post-apply honesty gate failed and the change was ROLLED BACK.
"""
from __future__ import annotations

import argparse
import datetime
import json
import shutil
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
KDB = REPO / "r2g-skills" / "signoff-loop" / "knowledge" / "knowledge.sqlite"
JDB = REPO / "r2g-skills" / "signoff-loop" / "knowledge" / "journal.sqlite"
MARKER = "/design_cases/"


def reroot(value: str, live_cases: Path) -> str | None:
    """Return the re-rooted path, or None when `value` needs no change.

    Splits on the LAST `/design_cases/` so a repo that itself sits under a directory
    of that name still re-roots onto its own corpus.
    """
    if not value or MARKER not in value:
        return None
    tail = value.rsplit(MARKER, 1)[1]
    new = str(live_cases / tail)
    return None if new == value else new



# config.mk references BOTH the corpus (VERILOG_FILES / SDC_FILE /
# VERILOG_INCLUDE_DIRS) and the skill tree (POST_GLOBAL_PLACE_TCL), so a repo move
# breaks it on two markers, not one.
_CONFIG_MARKERS = (MARKER, "/r2g-skills/")


def reroot_any(value: str, live_repo: Path) -> str | None:
    """Re-root a path on whichever known marker it carries, or None if unchanged."""
    for marker in _CONFIG_MARKERS:
        if marker in value:
            tail = value.rsplit(marker, 1)[1]
            new = str(live_repo / marker.strip("/") / tail)
            return None if new == value else new
    return None


def _reroot_config_text(text: str, live_repo: Path) -> tuple[str, int]:
    """Rewrite every DEAD absolute path in a config.mk. Returns (text, n_changed).

    Only paths that do NOT exist are touched, so a design deliberately pointing at a
    live file outside the tree keeps it. Whole-token replacement (make lists are
    space-separated), so a multi-file VERILOG_FILES line is handled entry by entry.
    """
    out, changed = [], 0
    for line in text.splitlines(keepends=True):
        if "/" not in line:
            out.append(line)
            continue
        stripped = line.rstrip("\n")
        toks = stripped.split(" ")
        new_toks = []
        for tok in toks:
            bare = tok.rstrip("\\")
            trail = tok[len(bare):]
            if bare.startswith("/") and not Path(bare).exists():
                new = reroot_any(bare, live_repo)
                if new and Path(new).exists():
                    new_toks.append(new + trail)
                    changed += 1
                    continue
            new_toks.append(tok)
        out.append(" ".join(new_toks) + line[len(stripped):])
    return "".join(out), changed


def plan_configs(cases: Path, live_repo: Path) -> dict[Path, int]:
    """{config.mk: n_paths_needing_reroot} across the corpus."""
    out = {}
    for cfg in sorted(cases.glob("*/constraints/config.mk")):
        _, n = _reroot_config_text(cfg.read_text(errors="replace"), live_repo)
        if n:
            out[cfg] = n
    return out


def plan_ledgers(cases: Path, live_cases: Path) -> dict[Path, int]:
    """{ledger: n_designs_needing_reroot} for every campaign ledger under _batch/.

    The DB and the ledgers MUST be re-rooted together. `check_ledger_signoff_backed.py`
    joins them on the EXACT `project_path`, so migrating one side alone turns every
    design into a join miss: a half-done migration reported fabricated=6/backed=0 on a
    corpus that was 32-backed a minute earlier (2026-07-25). Consistency is the point.
    """
    out = {}
    batch = cases / "_batch"
    if not batch.is_dir():
        return out
    for led in sorted(batch.glob("*.jsonl")):
        # Count against the MERGED latest state, exactly as apply_ledger does. Counting
        # raw lines instead would keep reporting the superseded pre-repair events
        # forever — the ledger is append-only, so a repaired design still has its old
        # line on disk — and the dry run would never reach CLEAN.
        n = len(_reroot_events(led, live_cases))
        if n:
            out[led] = n
    return out


def _reroot_events(led: Path, live_cases: Path) -> list[dict]:
    """The re-root events this ledger still needs, from its merged latest state."""
    merged: dict[str, dict] = {}
    for ln in led.read_text(encoding="utf-8", errors="replace").splitlines():
        if not ln.strip():
            continue
        try:
            e = json.loads(ln)
        except ValueError:
            continue
        merged.setdefault(e["design"], {}).update(e)
    events = []
    for design, cur in merged.items():
        new = reroot(cur.get("project_path", ""), live_cases)
        if new:
            events.append({"design": design, "project_path": new,
                           "reason": f"project_path_reroot:"
                                     f"{Path(cur['project_path']).parent}"})
    return events


def apply_ledger(led: Path, live_cases: Path) -> int:
    """Append a re-root event per stale design.

    APPEND, never rewrite: the ledger is immutable history and the loop's own reader
    merges events cumulatively. The event carries no `state`, so a design's real state
    survives the repair (mirrors Ledger.reroot_project_paths in engineer_loop.py).
    """
    stamp = datetime.datetime.now().astimezone().replace(microsecond=0).isoformat()
    events = _reroot_events(led, live_cases)
    for e in events:
        e["ts"] = stamp
    if events:
        with led.open("a", encoding="utf-8") as f:
            for e in events:
                f.write(json.dumps(e, sort_keys=True) + "\n")
    return len(events)


def _tables_with_project_path(con: sqlite3.Connection) -> list[str]:
    out = []
    for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({t})")]
        if "project_path" in cols:
            out.append(t)
    return out


def plan_db(db: Path, live_cases: Path) -> dict[str, list[tuple[str, str]]]:
    """{table: [(old, new), ...]} for every DISTINCT value needing a rewrite."""
    if not db.exists():
        return {}
    con = sqlite3.connect(db)
    try:
        plan = {}
        for t in _tables_with_project_path(con):
            pairs = []
            for (val,) in con.execute(
                    f"SELECT DISTINCT project_path FROM {t} "
                    f"WHERE project_path IS NOT NULL"):
                new = reroot(val, live_cases)
                if new:
                    pairs.append((val, new))
            if pairs:
                plan[t] = pairs
        return plan
    finally:
        con.close()


def apply_db(db: Path, plan: dict[str, list[tuple[str, str]]]) -> int:
    """Rewrite in ONE transaction. Returns rows changed."""
    con = sqlite3.connect(db)
    changed = 0
    try:
        with con:
            for table, pairs in plan.items():
                for old, new in pairs:
                    cur = con.execute(
                        f"UPDATE {table} SET project_path = ? WHERE project_path = ?",
                        (new, old))
                    changed += cur.rowcount
    finally:
        con.close()
    return changed


def _honesty_ok(db: Path) -> tuple[bool, str]:
    """Re-run the five knowledge gates over the migrated store."""
    sys.path.insert(0, str(KDB.parent))
    try:
        import honesty  # noqa: PLC0415  (path-dependent import, by design)
    except ImportError as e:                     # pragma: no cover
        return True, f"honesty gates unavailable ({e}) — skipped"
    con = sqlite3.connect(db)
    try:
        ok, results = honesty.run_all(con)
        bad = [r for r in results if not r.get("ok")]
        return ok, "5/5 pass" if ok else f"FAILED: {bad}"
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="rewrite the DBs (default is a dry run)")
    ap.add_argument("--live-root", default=str(REPO),
                    help="repo root whose design_cases/ is the truth (default: this repo)")
    args = ap.parse_args()

    live_cases = (Path(args.live_root).resolve() / "design_cases")
    print(f"live corpus: {live_cases}")

    total_rows = 0
    plans = {}
    for db in (KDB, JDB):
        plan = plan_db(db, live_cases)
        plans[db] = plan
        if not plan:
            print(f"  [ok] {db.name}: nothing to re-root")
            continue
        print(f"  [--] {db.name}:")
        for table, pairs in plan.items():
            con = sqlite3.connect(db)
            rows = sum(con.execute(
                f"SELECT COUNT(*) FROM {table} WHERE project_path = ?", (o,)
            ).fetchone()[0] for o, _ in pairs)
            con.close()
            total_rows += rows
            print(f"        {table:20s} {rows:6d} rows / {len(pairs)} distinct paths")
            o, n = pairs[0]
            print(f"          e.g. {o}\n            -> {n}")

    live_repo = live_cases.parent
    cfg_plan = plan_configs(live_cases, live_repo)
    if cfg_plan:
        print(f"  [--] config.mk: {len(cfg_plan)} design(s), "
              f"{sum(cfg_plan.values())} dead path(s) "
              f"(VERILOG_FILES / SDC_FILE / include dirs / stage hooks)")
    else:
        print("  [ok] config.mk: nothing to re-root")

    led_plan = plan_ledgers(live_cases.parent / "design_cases", live_cases)
    for led, n in led_plan.items():
        print(f"  [--] {led.name:32s} {n:5d} design(s) to re-root")
    if not led_plan:
        print("  [ok] ledgers: nothing to re-root")

    if not total_rows and not led_plan and not cfg_plan:
        print("verdict: CLEAN — every project_path already names the live corpus")
        return 0
    if not args.apply:
        print(f"verdict: {total_rows} stale DB row(s), {sum(led_plan.values())} stale "
              f"ledger design(s), {sum(cfg_plan.values())} stale config.mk path(s) "
              f"— re-run with --apply to rewrite")
        return 1

    for db, plan in plans.items():
        if not plan:
            continue
        backup = db.with_suffix(db.suffix + ".pre_reroot.bak")
        shutil.copy2(db, backup)
        changed = apply_db(db, plan)
        print(f"  [ok] {db.name}: {changed} row(s) re-rooted (backup: {backup.name})")
        if db == KDB:
            ok, detail = _honesty_ok(db)
            print(f"       honesty gates: {detail}")
            if not ok:
                shutil.copy2(backup, db)
                print("       ROLLED BACK — the migration must never leave the store "
                      "in a state the honesty gates reject", file=sys.stderr)
                return 2

    if cfg_plan:
        total = 0
        for cfg in cfg_plan:
            new, n = _reroot_config_text(cfg.read_text(errors="replace"), live_repo)
            cfg.write_text(new)
            total += n
        print(f"  [ok] config.mk: {total} path(s) re-rooted across "
              f"{len(cfg_plan)} design(s) — knobs and PLATFORM untouched")

    for led in led_plan:
        n = apply_ledger(led, live_cases)
        print(f"  [ok] {led.name}: {n} design(s) re-rooted (appended, history intact)")
    print("verdict: APPLIED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
