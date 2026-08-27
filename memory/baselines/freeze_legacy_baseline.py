#!/usr/bin/env python3
"""Phase 0: freeze the legacy R2G memory baseline.

Captures an immutable, reproducible snapshot of the *legacy* memory plane
(`signoff-loop/knowledge/`) so that the TEHM replacement can later be compared
against an un-moved baseline (design doc 15.2 / 18.1 / 26 Phase 0).

This script is strictly READ-ONLY against the legacy tree: it never writes into
the legacy `knowledge/` directory. It reads the committed `knowledge.sqlite`
(ro), hashes the shipped schema/heuristics, records the git commit and the
legacy honesty-gate verdict (via `honesty.py --db` in a subprocess), and writes
everything under ``baselines/r2g_legacy/``.

Usage:
    python3 baselines/freeze_legacy_baseline.py [--out baselines/r2g_legacy]
        [--knowledge-dir PATH] [--skip-honesty]

Output (all deterministic where possible):
    commit.txt                 git HEAD of the repo
    schema_digest.txt          sha256 of legacy schema.sql
    heuristics_digest.txt      sha256 of legacy heuristics.json
    knowledge_db_fingerprint.json   table/column inventory + row counts (ro)
    honesty_report.json        verdict of the legacy honesty gates (best-effort)
    baseline_manifest.json     the aggregate snapshot record
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]  # .../memory/../ = r2g-skills repo root
DEFAULT_KNOWLEDGE_DIR = REPO_ROOT / "r2g-skills" / "signoff-loop" / "knowledge"
DEFAULT_OUT = Path(__file__).resolve().parent / "r2g_legacy"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def capture_commit(repo_root: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=30, check=True,
        )
        return out.stdout.strip()
    except Exception as exc:  # noqa: BLE001 - best-effort
        return f"<unavailable: {exc}>"


def capture_knowledge_fingerprint(db_path: Path) -> dict:
    """Read-only table/column inventory + row counts of knowledge.sqlite."""
    fingerprint = {"db_path": str(db_path), "db_sha256": None, "tables": {}}
    if not db_path.exists():
        fingerprint["error"] = "knowledge.sqlite not found"
        return fingerprint
    fingerprint["db_sha256"] = sha256_file(db_path)
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        for row in rows:
            table = row["name"]
            cols = [
                c["name"] for c in conn.execute(f"PRAGMA table_info('{table}')")
            ]
            try:
                count = conn.execute(f'SELECT COUNT(*) AS n FROM "{table}"').fetchone()["n"]
            except Exception:  # noqa: BLE001
                count = None
            fingerprint["tables"][table] = {"columns": cols, "row_count": count}
        conn.close()
    except Exception as exc:  # noqa: BLE001
        fingerprint["error"] = f"read failed: {exc}"
    return fingerprint


def run_legacy_honesty(knowledge_dir: Path) -> dict:
    """Run the legacy honesty gates read-only via `honesty.py --db`."""
    honesty_py = knowledge_dir / "honesty.py"
    db_path = knowledge_dir / "knowledge.sqlite"
    report = {"ran": False}
    if not honesty_py.exists() or not db_path.exists():
        report["reason"] = "honesty.py or knowledge.sqlite missing"
        return report
    try:
        proc = subprocess.run(
            [sys.executable, str(honesty_py), "--db", str(db_path)],
            capture_output=True, text=True, timeout=120,
        )
        report.update({
            "ran": True,
            "exit_code": proc.returncode,
            "stdout_tail": proc.stdout[-4000:],
            "stderr_tail": proc.stderr[-2000:],
        })
    except Exception as exc:  # noqa: BLE001
        report["error"] = str(exc)
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--knowledge-dir", type=Path, default=DEFAULT_KNOWLEDGE_DIR)
    ap.add_argument("--skip-honesty", action="store_true")
    args = ap.parse_args(argv)

    kdir = args.knowledge_dir
    out = args.out
    out.mkdir(parents=True, exist_ok=True)

    schema_path = kdir / "schema.sql"
    heur_path = kdir / "heuristics.json"
    db_path = kdir / "knowledge.sqlite"

    commit = capture_commit(REPO_ROOT)
    schema_digest = sha256_file(schema_path) if schema_path.exists() else None
    heur_digest = sha256_file(heur_path) if heur_path.exists() else None
    fingerprint = capture_knowledge_fingerprint(db_path)
    honesty = ({"ran": False, "reason": "skipped via --skip-honesty"}
               if args.skip_honesty else run_legacy_honesty(kdir))

    manifest = {
        "snapshot_id": "r2g-legacy-baseline",
        "commit": commit,
        "schema_digest": schema_digest,
        "heuristics_digest": heur_digest,
        "knowledge_db_fingerprint": fingerprint,
        "legacy_honesty": honesty,
        "schema_schema_version": "legacy-knowledge-v1",
        "captured_at_utc": None,  # stamped by the caller to keep files deterministic
    }

    # Deterministic, byte-stable manifest (sort_keys, no timestamp inside).
    manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode()
    manifest_digest = sha256_bytes(manifest_bytes)
    manifest["manifest_digest"] = manifest_digest
    manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode()

    (out / "commit.txt").write_text(commit + "\n")
    (out / "schema_digest.txt").write_text((schema_digest or "<missing>") + "\n")
    (out / "heuristics_digest.txt").write_text((heur_digest or "<missing>") + "\n")
    (out / "knowledge_db_fingerprint.json").write_text(
        json.dumps(fingerprint, indent=2, sort_keys=True) + "\n")
    (out / "honesty_report.json").write_text(
        json.dumps(honesty, indent=2, sort_keys=True) + "\n")
    (out / "baseline_manifest.json").write_text(manifest_bytes.decode() + "\n")

    print(f"froze legacy baseline -> {out}")
    print(f"  commit            : {commit}")
    print(f"  schema_digest     : {schema_digest}")
    print(f"  heuristics_digest : {heur_digest}")
    print(f"  manifest_digest   : {manifest_digest}")
    print(f"  tables captured   : {len(fingerprint.get('tables', {}))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
