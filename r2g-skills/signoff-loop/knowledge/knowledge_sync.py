#!/usr/bin/env python3
"""Git-shareable, MERGEABLE knowledge store: export / import / merge / status.

WHY this exists
---------------
`knowledge.sqlite` ships pre-trained so a fresh clone inherits the skill's
accumulated experience (CLAUDE.md "Closed Learning Loop"). But a binary SQLite
blob is a poor git citizen and CANNOT be combined across operators:

  * git can only 3-way-merge text. Two operators who both ran campaigns get a
    binary conflict — one operator's experience silently CLOBBERS the other's.
  * every commit rewrites the whole multi-MB blob (history bloat).
  * the diff is opaque: a reviewer cannot see what knowledge changed.

This module makes the store transferable as a deterministic, line-oriented TEXT
bundle (one NDJSON file per table) that git diffs and 3-way-merges cleanly, AND
provides a real cross-operator `merge` that UNIONS two stores by natural content
key instead of clobbering.

The load-bearing schema fact
----------------------------
Three key families live in the schema:

  * `symptom_id = sha1(check, class, predicates)` is GENUINELY content-addressed and
    machine-portable — the SAME symptom dedups across operators. This is what actually
    carries cross-operator learning (recipes/symptoms/fix experience pool under it).
  * `run_id = sha1(str(project.resolve()) : ppa.json st_mtime_ns)` (ingest_run.py
    `_compute_run_id`) is content-addressed but NOT machine-portable: it embeds the
    operator's ABSOLUTE path and a per-filesystem mtime. So two operators running the
    SAME design get DIFFERENT run_ids — cross-operator `runs` rows are normally ADDITIVE
    (re-imported as new rows), which is SAFE under the additive honesty-gated merge.
    run_id only dedups when the path AND ppa.json mtime are byte-identical (shared
    filesystem, or a preserved-mtime copy / re-merge of the same machine's own bundle).
  * machine-local AUTOINCREMENT surrogate keys COLLIDE outright: `failure_events.id`,
    `fix_events.fix_event_id`, `ab_trials.trial_id`, `escalations.escalation_id`,
    `config_lineage.id` — operator A's id=1 is a DIFFERENT row from operator B's.

So the bundle DROPS surrogate ids (they are re-assigned locally on import), and the
merge dedups by each table's NATURAL CONTENT KEY (``TABLE_ORDER`` / ``DEDUP_FULL_ROW``)
— never a surrogate id, and never relying on run_id portability for honesty (the
post-merge honesty gate is the real guarantee, not the dedup).

Honesty (this is the whole point of the project)
------------------------------------------------
A merge is ADDITIVE (insert a row only when its natural key is absent locally;
never overwrite a local row) and is wrapped in ONE transaction that is ROLLED BACK
if the post-merge store fails any honesty gate (``honesty.run_all``). A merge that
would make the store lie (e.g. import a fail run without its `failure_events`, or
leave fail/partial rows with an empty `ab_trials`) is REFUSED, not silently applied.
`recipe_status` lifecycle disagreements are REPORTED for operator review, never
auto-resolved — the A/B loop re-validates after the next `learn()`.

Determinism
-----------
Export is a pure function of the DB content: rows sorted by natural key, JSON object
keys sorted, surrogate ids dropped, NO wall-clock stamp in the manifest (data-row
timestamps like `ts`/`snapshot_ts` ARE preserved — they are part of the row). So the
SAME DB always produces a BYTE-IDENTICAL bundle: re-exporting after no change yields
a zero-line git diff, and `status` can detect bundle<->DB drift by digest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

_KNOWLEDGE_DIR = Path(__file__).resolve().parent
if str(_KNOWLEDGE_DIR) not in sys.path:
    sys.path.insert(0, str(_KNOWLEDGE_DIR))
import knowledge_db  # noqa: E402
import honesty  # noqa: E402

BUNDLE_FORMAT_VERSION = 1
DEFAULT_BUNDLE_DIR = _KNOWLEDGE_DIR / "store"

# Machine-local AUTOINCREMENT surrogate ids — DROPPED on export (re-assigned by
# SQLite on import). They collide across operators, so they can never be a merge key.
SURROGATE_COLS: dict[str, set[str]] = {
    "failure_events": {"id"},
    "config_lineage": {"id"},
    "fix_events": {"fix_event_id"},
    "escalations": {"escalation_id"},
    "ab_trials": {"trial_id"},
}

# Each knowledge table's NATURAL CONTENT KEY: the minimal set of columns that
# identifies a row ACROSS machines (for dedup on merge + a stable export sort).
# Built ONLY from portable columns (content-addressed ids + content), never a
# surrogate id and never a machine-time-only column. Ordering of the list is also
# the IMPORT order — `runs` MUST come first because failure_events/config_lineage/
# run_violations carry a FK to runs(run_id) (PRAGMA foreign_keys=ON).
TABLE_ORDER: list[tuple[str, tuple[str, ...]]] = [
    ("runs", ("run_id",)),
    ("symptoms", ("symptom_id",)),
    ("meta", ("key",)),
    ("lessons", ("lesson_id",)),
    ("failure_events", ("run_id", "stage", "signature", "detail")),
    ("run_violations", ("run_id",)),
    ("config_lineage",
     ("design_name", "platform", "current_run_id", "previous_run_id", "diff_json")),
    ("fix_events", ("fix_session_id", "iter", "strategy")),
    ("fix_events_archive", ("fix_session_id", "iter", "strategy")),
    ("fix_trajectories", ("fix_session_id", "check_type")),
    ("recipe_status", ("symptom_id", "design_class", "platform", "strategy")),
    ("ab_trials", ("symptom_id", "design_class", "platform", "strategy",
                   "arm_a_run_id", "arm_b_run_id", "ts")),
    ("escalations", ("design", "run_id", "symptom_id", "reason")),
]
_KEY = dict(TABLE_ORDER)

# Tables with NO enforced PK/UNIQUE (only a machine-local surrogate id) AND NULL-prone
# identity columns (e.g. ab_trials.arm_*_run_id, escalations.run_id are often NULL).
# These dedup on FULL exported-row CONTENT, which is simultaneously lossless (a genuinely
# distinct row is ALWAYS kept — never collapsed by a shared NULL) and idempotent (an exact
# duplicate is skipped on re-merge), and cannot violate a constraint that does not exist.
# Every other table dedups on its natural key (a real PK/UNIQUE or content-addressed id,
# which is structurally non-NULL — runs.run_id, symptoms.symptom_id, fix_events' UNIQUE
# (fix_session_id,iter,strategy), config_lineage's content key sans the machine-local
# created_at, etc.). The TABLE_ORDER key is still the EXPORT SORT key for every table.
DEDUP_FULL_ROW = {"failure_events", "ab_trials", "escalations"}


# ── helpers ──────────────────────────────────────────────────────────────────
def _ro_connect(db_path: Path | str) -> sqlite3.Connection:
    """A read connection that WAITS on a busy lock instead of erroring instantly —
    parity with knowledge_db.connect's 30s busy_timeout. export/status/merge_db read
    the DB while a campaign may be running a pool of ingest subprocesses against the
    same file; an unguarded 'database is locked' would crash a status/export.
    (knowledge_sync review 2026-06-23, finding #6.)"""
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    """Live column list from PRAGMA (so migrated columns like runs.outcome_score,
    added by knowledge_db._migrate_add_columns, are included). Empty => table absent."""
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def _export_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    drop = SURROGATE_COLS.get(table, set())
    return [c for c in _table_columns(conn, table) if c not in drop]


def _sort_key(row: dict, key_cols: tuple[str, ...]) -> tuple:
    """Total order over the natural key, NULL-safe (None sorts before any value)."""
    out = []
    for c in key_cols:
        v = row.get(c)
        out.append((0, "") if v is None else (1, str(v)))
    return tuple(out)


def _row_natural_key(row: dict, key_cols: tuple[str, ...]) -> tuple:
    """Hashable dedup key. Uses repr-stable str() so 15 and '15' are kept distinct
    only if the source stored them distinctly (they don't — ids are TEXT)."""
    return tuple(None if row.get(c) is None else str(row.get(c)) for c in key_cols)


def _dumps(obj) -> str:
    """Deterministic compact JSON for one NDJSON row. ensure_ascii=True is LOAD-BEARING:
    it escapes every non-ASCII char (incl. the Unicode line separators U+2028/U+2029),
    so a row can NEVER contain a literal line-break that `str.splitlines()` would split
    on at import — a tool-error `detail` carrying a stray U+2028 must round-trip intact.
    It also makes the bundle pure-ASCII (maximally portable across git/editors)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _read_generation(conn: sqlite3.Connection) -> int | None:
    try:
        row = conn.execute(
            "SELECT value FROM meta WHERE key='generation'").fetchone()
    except sqlite3.OperationalError:
        return None
    if not row:
        return None
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return None


# ── export ───────────────────────────────────────────────────────────────────
def export_bundle(db_path: Path | str,
                  out_dir: Path | str = DEFAULT_BUNDLE_DIR) -> dict:
    """Serialize `db_path` into a deterministic NDJSON bundle under `out_dir`.

    Returns the manifest dict. Pure read over the DB (no schema mutation)."""
    db_path = Path(db_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    conn = _ro_connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        manifest_tables: dict[str, dict] = {}
        present = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for table, key_cols in TABLE_ORDER:
            path = out_dir / f"{table}.ndjson"
            if table not in present:
                # Legacy DB without this table: write an empty file so the bundle
                # shape is stable and the digest is well-defined.
                path.write_text("", encoding="utf-8")
                manifest_tables[table] = {"rows": 0,
                                          "sha256": _sha256_bytes(b"")}
                continue
            cols = _export_columns(conn, table)
            sel = ", ".join(f'"{c}"' for c in cols)
            rows = [dict(r) for r in conn.execute(f"SELECT {sel} FROM {table}")]
            # TOTAL order: natural key first, then FULL row content as the tie-break.
            # Without the tie-break, two rows sharing the natural key but differing on a
            # non-key column (ab_trials.metrics_json/verdict, config_lineage.current_
            # outcome/created_at, escalations.status/notes) would serialize in physical
            # rowid order — so the SAME content could export to a DIFFERENT bundle
            # depending on insert order, breaking the byte-identical invariant the drift
            # gate relies on. (knowledge_sync review 2026-06-23, finding #4.)
            all_cols = tuple(cols)
            rows.sort(key=lambda r: (_sort_key(r, key_cols), _sort_key(r, all_cols)))
            lines = [_dumps({c: r[c] for c in cols}) for r in rows]
            blob = ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")
            path.write_bytes(blob)
            manifest_tables[table] = {"rows": len(rows),
                                      "sha256": _sha256_bytes(blob)}
        manifest = {
            "bundle_format_version": BUNDLE_FORMAT_VERSION,
            "generation": _read_generation(conn),
            "tables": manifest_tables,
            "digest": _bundle_digest(manifest_tables),
        }
    finally:
        conn.close()

    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return manifest


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _bundle_digest(manifest_tables: dict[str, dict]) -> str:
    """One fingerprint over the whole bundle: sha256 of the per-table digests in
    sorted table order. Two stores are content-identical iff their digests match."""
    joined = "\n".join(f"{t}:{manifest_tables[t]['sha256']}"
                       for t in sorted(manifest_tables))
    return _sha256_bytes(joined.encode("utf-8"))


# ── bundle reading ───────────────────────────────────────────────────────────
def _load_manifest(bundle_dir: Path) -> dict:
    p = Path(bundle_dir) / "manifest.json"
    if not p.exists():
        raise FileNotFoundError(f"no manifest.json in bundle {bundle_dir}")
    return json.loads(p.read_text(encoding="utf-8"))


def _iter_bundle_rows(bundle_dir: Path, table: str):
    """Yield row dicts for a table from its NDJSON file (empty/absent => nothing)."""
    p = Path(bundle_dir) / f"{table}.ndjson"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            yield json.loads(line)


# ── import (full rebuild) ────────────────────────────────────────────────────
def import_bundle(bundle_dir: Path | str,
                  db_path: Path | str,
                  *, overwrite: bool = False) -> dict:
    """Rebuild a FRESH knowledge.sqlite from a bundle (e.g. a new user bootstrapping
    off the committed text store). Refuses a non-empty existing DB unless overwrite —
    use `merge` to fold a bundle into an existing store. Returns per-table counts."""
    bundle_dir = Path(bundle_dir)
    db_path = Path(db_path)
    _load_manifest(bundle_dir)  # validate shape early
    if db_path.exists() and not overwrite:
        conn = _ro_connect(db_path)
        try:
            has_runs = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='runs'"
            ).fetchone()
            n = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] if has_runs else 0
        finally:
            conn.close()
        if n:
            raise FileExistsError(
                f"{db_path} already has {n} runs; use merge (or --overwrite to rebuild)")
    if overwrite and db_path.exists():
        db_path.unlink()

    conn = knowledge_db.connect(db_path)
    counts: dict[str, int] = {}
    try:
        knowledge_db.ensure_schema(conn)
        conn.execute("PRAGMA foreign_keys = OFF")  # bulk load; FK re-checked below
        for table, _key in TABLE_ORDER:
            counts[table] = _insert_rows(conn, table, _iter_bundle_rows(bundle_dir, table))
        _assert_fk_ok(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        raise
    conn.close()
    return counts


def _insert_rows(conn: sqlite3.Connection, table: str, rows) -> int:
    """Insert bundle rows, restricting to columns that actually exist in the live
    schema (forward/backward compatible). Surrogate ids absent from the bundle are
    auto-assigned by SQLite. Returns the number inserted."""
    live_cols = set(_table_columns(conn, table))
    if not live_cols:
        return 0
    n = 0
    for row in rows:
        cols = [c for c in row if c in live_cols]
        if not cols:
            continue
        placeholders = ", ".join("?" for _ in cols)
        collist = ", ".join(f'"{c}"' for c in cols)
        conn.execute(f"INSERT INTO {table} ({collist}) VALUES ({placeholders})",
                     [row[c] for c in cols])
        n += 1
    return n


def _assert_fk_ok(conn: sqlite3.Connection) -> None:
    bad = conn.execute("PRAGMA foreign_key_check").fetchall()
    if bad:
        raise ValueError(f"bundle has dangling foreign keys: {bad[:5]} "
                         f"({len(bad)} total) — refusing to build a corrupt store")


# ── merge (cross-operator union) ─────────────────────────────────────────────
def merge_bundle(bundle_dir: Path | str,
                 db_path: Path | str,
                 *, dry_run: bool = False) -> dict:
    """UNION a bundle into an existing knowledge.sqlite by natural content key.

    ADDITIVE: a row is inserted only when its natural key is ABSENT locally; a key
    already present is left exactly as the local store has it (local wins — runs are
    immutable history, and a local lifecycle decision is never silently overwritten).
    The whole merge runs in ONE transaction that is ROLLED BACK if the post-merge
    store fails any honesty gate, OR if dry_run is set. Returns a report dict.
    """
    return _merge_from_source(db_path, dry_run=dry_run,
                              source_rows=lambda t: _iter_bundle_rows(Path(bundle_dir), t),
                              source_label=str(bundle_dir))


def merge_db(other_db: Path | str, db_path: Path | str, *, dry_run: bool = False) -> dict:
    """Merge another knowledge.sqlite directly (no intermediate bundle)."""
    other = _ro_connect(other_db)
    other.row_factory = sqlite3.Row

    def source_rows(table: str):
        present = other.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,)).fetchone()
        if not present:
            return
        cols = _export_columns(other, table)
        sel = ", ".join(f'"{c}"' for c in cols)
        for r in other.execute(f"SELECT {sel} FROM {table}"):
            yield {c: r[c] for c in cols}

    try:
        return _merge_from_source(db_path, dry_run=dry_run,
                                  source_rows=source_rows, source_label=str(other_db))
    finally:
        other.close()


def _merge_from_source(db_path, *, dry_run, source_rows, source_label) -> dict:
    conn = knowledge_db.connect(db_path)
    report: dict = {"source": source_label, "tables": {}, "recipe_conflicts": [],
                    "recipe_imports": [], "fk_violations": [], "honest": None,
                    "applied": False}
    try:
        knowledge_db.ensure_schema(conn)  # commits; leaves us outside a txn
        # FK OFF for the bulk union (mirrors import_bundle): a dangling reference in a
        # partial/corrupt bundle must surface as a clean REFUSED report (rolled back),
        # NOT a bare IntegrityError mid-insert (review 2026-06-23, finding #5). The
        # first INSERT then auto-opens the implicit transaction wrapping the whole
        # merge, so a post-merge honesty breach OR an FK violation rolls back ALL of it.
        conn.execute("PRAGMA foreign_keys = OFF")
        for table, key_cols in TABLE_ORDER:
            if table == "meta":
                added, skipped = _merge_meta(conn, source_rows(table))
            elif table == "recipe_status":
                added, skipped, conflicts, imports = _merge_recipe_status(
                    conn, source_rows(table), source_label)
                report["recipe_conflicts"] = conflicts
                report["recipe_imports"] = imports
            else:
                added, skipped = _merge_table(conn, table, key_cols, source_rows(table))
            report["tables"][table] = {"added": added, "skipped": skipped}
        fk_bad = conn.execute("PRAGMA foreign_key_check").fetchall()
        report["fk_violations"] = [list(r) for r in fk_bad[:20]]
        honest, hreport = honesty.run_all(conn)
        report["honesty_report"] = hreport
        # A merge is honest ONLY if every gate passes AND no dangling FK remains.
        report["honest"] = honest and not fk_bad
        if dry_run or not report["honest"]:
            conn.rollback()
            report["applied"] = False
        else:
            conn.commit()
            report["applied"] = True
    except Exception:
        conn.rollback()
        conn.close()
        raise
    conn.close()
    return report


def _merge_table(conn, table, key_cols, rows) -> tuple[int, int]:
    """Insert source rows whose dedup key is absent locally. Returns (added, skipped).
    Local rows are NEVER modified (additive union). The dedup key is the FULL exported
    row for DEDUP_FULL_ROW tables, else the natural key. A natural-key row carrying a
    NULL in any key column is treated as un-dedupable -> always inserted (never silently
    collapsed into another row by a shared NULL), so the merge can never LOSE data."""
    live_cols = set(_table_columns(conn, table))
    if not live_cols:
        return (0, 0)
    if table in DEDUP_FULL_ROW:
        dedup_cols = [c for c in _export_columns(conn, table) if c in live_cols]
    else:
        dedup_cols = [c for c in key_cols if c in live_cols]

    existing = set()
    if dedup_cols:
        sel = ", ".join(f'"{c}"' for c in dedup_cols)
        for r in conn.execute(f"SELECT {sel} FROM {table}"):
            existing.add(tuple(None if v is None else str(v) for v in r))

    added = skipped = 0
    for row in rows:
        nk = _row_natural_key(row, tuple(dedup_cols))
        dedupable = bool(dedup_cols) and (table in DEDUP_FULL_ROW or None not in nk)
        if dedupable and nk in existing:
            skipped += 1
            continue
        cols = [c for c in row if c in live_cols]
        if not cols:
            skipped += 1
            continue
        placeholders = ", ".join("?" for _ in cols)
        collist = ", ".join(f'"{c}"' for c in cols)
        conn.execute(f"INSERT INTO {table} ({collist}) VALUES ({placeholders})",
                     [row[c] for c in cols])
        if dedupable:
            existing.add(nk)
        added += 1
    return (added, skipped)


def _merge_meta(conn, rows) -> tuple[int, int]:
    """meta is store metadata, not experience. The only key in practice is the
    monotonic 'generation' counter. It is a PER-MACHINE rebuild count (not comparable
    across operators), but the merged store must keep generation >= the generation of
    any recipe_status row it now contains (so detect_contradictions' generation-
    supersession is not poisoned by an imported recipe stamped at a higher gen than the
    local meta). So 'generation' merges as MAX(local, incoming); any other meta key is
    insert-if-absent (local kept). This is the ONE place a merge touches a local row,
    and only for store-metadata bookkeeping."""
    local = {k: v for k, v in conn.execute("SELECT key, value FROM meta")}
    added = skipped = 0
    for row in rows:
        k, v = row.get("key"), row.get("value")
        if k == "generation":
            try:
                merged = max(int(local.get("generation") or -1), int(v))
            except (TypeError, ValueError):
                skipped += 1
                continue
            if str(merged) != str(local.get("generation")):
                conn.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES ('generation', ?)",
                    (str(merged),))
                added += 1
            else:
                skipped += 1
        elif k in local:
            skipped += 1
        else:
            conn.execute("INSERT INTO meta (key, value) VALUES (?, ?)", (k, v))
            added += 1
    return added, skipped


def _merge_recipe_status(conn, rows, source_label) -> tuple[int, int, list, list]:
    """recipe_status is a LOCAL lifecycle pointer, not transferable truth. The project's
    'no special trust' rule (recipe_lifecycle decision 7: an agent-authored strategy must
    win ITS OWN A/B before it can affect live ranking) applies to imported verdicts too.
    So on merge:
      * key PRESENT locally, status differs -> KEEP LOCAL, report a `conflict` (a local
        promote/demote is NEVER silently flipped by an incoming bundle; cross-machine
        `generation` is not comparable, so it cannot pick a winner);
      * key ABSENT locally -> import the strategy but FORCE status='shadow' (inert,
        outside the live pool) with provenance 'merge:<original-status>', so B's verdict
        arrives as a HYPOTHESIS that A's next learn()+ab-drain re-validates on A's
        platform, NEVER as installed truth. This also fixes the asymmetry the review
        flagged: a bare imported 'demoted'/'promoted' would otherwise silently suppress
        or boost a strategy on A. (review 2026-06-23, finding #3.)
    NOTE: import_bundle restores recipe_status VERBATIM — that path rebuilds YOUR OWN
    store; merge is for folding in ANOTHER operator's experience. Returns
    (added, skipped, conflicts, imports)."""
    key_cols = _KEY["recipe_status"]
    live_cols = set(_table_columns(conn, "recipe_status"))
    local: dict[tuple, str] = {}
    for r in conn.execute(
            "SELECT symptom_id, design_class, platform, strategy, status "
            "FROM recipe_status"):
        local[(r[0], r[1], r[2], r[3])] = r[4]
    added = skipped = 0
    conflicts: list[dict] = []
    imports: list[dict] = []
    for row in rows:
        k = tuple(row.get(c) for c in key_cols)
        inc = row.get("status")
        loc = local.get(k)
        if loc is not None:
            if inc is not None and inc != loc:
                conflicts.append({"key": list(k), "local": loc, "incoming": inc})
            skipped += 1
            continue
        new_row = dict(row)
        new_row["status"] = "shadow"   # inert until A's own A/B promotes it
        prov = row.get("provenance")
        new_row["provenance"] = f"merge:{inc or '?'}" + (f":{prov}" if prov else "")
        cols = [c for c in new_row if c in live_cols]
        placeholders = ", ".join("?" for _ in cols)
        collist = ", ".join(f'"{c}"' for c in cols)
        conn.execute(
            f"INSERT INTO recipe_status ({collist}) VALUES ({placeholders})",
            [new_row[c] for c in cols])
        local[k] = "shadow"
        imports.append({"key": list(k), "incoming": inc})
        added += 1
    conflicts.sort(key=lambda c: c["key"])
    imports.sort(key=lambda c: c["key"])
    return added, skipped, conflicts, imports


# ── status / drift ───────────────────────────────────────────────────────────
def bundle_digest_of_db(db_path: Path | str) -> str:
    """Export `db_path` to a temp bundle and return its digest, WITHOUT writing the
    committed bundle. Lets `status` detect whether the committed bundle is stale."""
    with tempfile.TemporaryDirectory() as td:
        m = export_bundle(db_path, Path(td) / "store")
        return m["digest"]


def status(db_path: Path | str, bundle_dir: Path | str = DEFAULT_BUNDLE_DIR) -> dict:
    """Compare committed bundle vs live DB and run honesty gates. Returns a dict with
    `in_sync` (digest match), `drift` (per-table), and `honest`."""
    db_path, bundle_dir = Path(db_path), Path(bundle_dir)
    out: dict = {"db": str(db_path), "bundle": str(bundle_dir)}
    live_digest = bundle_digest_of_db(db_path)
    out["db_digest"] = live_digest
    try:
        manifest = _load_manifest(bundle_dir)
        out["bundle_digest"] = manifest.get("digest")
        out["in_sync"] = (manifest.get("digest") == live_digest)
        # Per-table drift (compare freshly-exported digests).
        with tempfile.TemporaryDirectory() as td:
            fresh = export_bundle(db_path, Path(td) / "store")
        drift = []
        for t in sorted(fresh["tables"]):
            a = manifest.get("tables", {}).get(t, {}).get("sha256")
            b = fresh["tables"][t]["sha256"]
            if a != b:
                drift.append({"table": t,
                              "bundle_rows": manifest.get("tables", {}).get(t, {}).get("rows"),
                              "db_rows": fresh["tables"][t]["rows"]})
        out["drift"] = drift
    except FileNotFoundError:
        out["bundle_digest"] = None
        out["in_sync"] = False
        out["drift"] = "no committed bundle"
    conn = knowledge_db.connect(db_path)
    try:
        honest, hreport = honesty.run_all(conn)
    finally:
        conn.close()
    out["honest"] = honest
    out["honesty_report"] = hreport
    return out


# ── CLI ──────────────────────────────────────────────────────────────────────
def _print_merge_report(rep: dict) -> None:
    print(f"merge source: {rep['source']}")
    total_added = sum(t["added"] for t in rep["tables"].values())
    for table, t in rep["tables"].items():
        if t["added"] or t["skipped"]:
            print(f"  {table:<20} +{t['added']} new, {t['skipped']} already present")
    print(f"  total new rows: {total_added}")
    if rep["recipe_conflicts"]:
        print(f"  recipe_status conflicts (kept LOCAL, review): "
              f"{len(rep['recipe_conflicts'])}")
        for c in rep["recipe_conflicts"][:10]:
            print(f"    {c['key']}: local={c['local']} incoming={c['incoming']}")
    if rep.get("recipe_imports"):
        print(f"  recipe_status imported as inert 'shadow' (re-validate via ab-drain): "
              f"{len(rep['recipe_imports'])}")
    if rep.get("fk_violations"):
        print(f"  DANGLING FOREIGN KEYS in bundle: {len(rep['fk_violations'])} "
              f"(merge refused) e.g. {rep['fk_violations'][:3]}")
    print(honesty.format_report(rep.get("honesty_report", [])))
    if not rep["honest"]:
        print("  MERGE REFUSED: post-merge store fails an honesty gate or has dangling "
              "FKs (rolled back).")
    elif rep["applied"]:
        print("  MERGE APPLIED (honesty gates green).")
    else:
        print("  DRY RUN: nothing written.")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    pe = sub.add_parser("export", help="write a deterministic NDJSON bundle")
    pe.add_argument("--db", type=Path, default=knowledge_db.DEFAULT_DB_PATH)
    pe.add_argument("--out", type=Path, default=DEFAULT_BUNDLE_DIR)

    pi = sub.add_parser("import", help="rebuild a fresh DB from a bundle")
    pi.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE_DIR)
    pi.add_argument("--db", type=Path, required=True)
    pi.add_argument("--overwrite", action="store_true")

    pm = sub.add_parser("merge", help="union a bundle into an existing DB")
    pm.add_argument("--bundle", type=Path)
    pm.add_argument("--from-db", type=Path, help="merge another knowledge.sqlite directly")
    pm.add_argument("--db", type=Path, default=knowledge_db.DEFAULT_DB_PATH)
    pm.add_argument("--dry-run", action="store_true")

    ps = sub.add_parser("status", help="bundle<->DB drift + honesty gates")
    ps.add_argument("--db", type=Path, default=knowledge_db.DEFAULT_DB_PATH)
    ps.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE_DIR)

    args = p.parse_args(argv)

    if args.cmd == "export":
        m = export_bundle(args.db, args.out)
        print(f"exported {sum(t['rows'] for t in m['tables'].values())} rows "
              f"across {len(m['tables'])} tables to {args.out}")
        print(f"digest {m['digest']}  generation {m['generation']}")
        return 0

    if args.cmd == "import":
        counts = import_bundle(args.bundle, args.db, overwrite=args.overwrite)
        print(f"rebuilt {args.db}: " +
              ", ".join(f"{t}={n}" for t, n in counts.items() if n))
        return 0

    if args.cmd == "merge":
        if not args.bundle and not args.from_db:
            p.error("merge needs --bundle or --from-db")
        if args.from_db:
            rep = merge_db(args.from_db, args.db, dry_run=args.dry_run)
        else:
            rep = merge_bundle(args.bundle, args.db, dry_run=args.dry_run)
        _print_merge_report(rep)
        return 0 if rep["honest"] else 1

    if args.cmd == "status":
        out = status(args.db, args.bundle)
        sync = "IN SYNC" if out.get("in_sync") else "DRIFT"
        print(f"bundle vs db: {sync}")
        print(f"  db_digest     {out['db_digest']}")
        print(f"  bundle_digest {out.get('bundle_digest')}")
        if out.get("drift"):
            print(f"  drift: {out['drift']}")
        print(honesty.format_report(out.get("honesty_report", [])))
        ok = bool(out.get("in_sync")) and bool(out.get("honest"))
        return 0 if ok else 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
