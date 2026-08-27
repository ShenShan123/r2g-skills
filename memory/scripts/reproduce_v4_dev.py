#!/usr/bin/env python3
"""Reproduce the portable TEHM v4 development freeze.

The script intentionally uses only the source tree embedded in the bundle and
the bundled canonical SQLite/artifact snapshot.  It verifies the content
manifest, runs compile-time regression smoke, and executes all applicable
honesty gates with a non-empty H9 firewall and H11 bundle check.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bundle", type=Path,
                    default=Path(__file__).resolve().parents[3])
    args = ap.parse_args(argv)
    bundle = args.bundle.resolve()
    source_root = bundle / "source" / "memory"
    if not source_root.is_dir():
        raise SystemExit(f"v4 source snapshot missing: {source_root}")
    # Imports below must not create bytecode inside the immutable bundle.
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(source_root))

    from tehm import db, honesty  # noqa: E402
    from tehm.artifact_store import ArtifactStore  # noqa: E402
    from tehm.sync import verify_bundle  # noqa: E402

    checked = verify_bundle(bundle)
    if not checked.get("ok"):
        raise SystemExit(f"bundle verification failed: {checked.get('detail')}")
    metadata = checked["manifest"].get("metadata") or {}
    firewall = metadata.get("firewall") or {}
    if not firewall.get("heldout_lineages") and not firewall.get("ab_lineages"):
        raise SystemExit("refusing v4 reproduction with an empty evaluation firewall")

    # compileall writes __pycache__; never mutate the supposedly immutable
    # bundle while reproducing it.  Compile a disposable source copy instead.
    with tempfile.TemporaryDirectory(prefix="tehm-v4-compile-") as td:
        compile_root = Path(td) / "memory"
        shutil.copytree(source_root, compile_root)
        compile_result = subprocess.run(
            [sys.executable, "-m", "compileall", "-q", str(compile_root)],
            text=True, capture_output=True)
    if compile_result.returncode != 0:
        raise SystemExit(compile_result.stdout + compile_result.stderr)

    p0_result = subprocess.run(
        [sys.executable, str(source_root / "scripts" / "p0_regression_smoke.py")],
        text=True, capture_output=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1",
             "PYTHONPATH": str(source_root)},
    )
    if p0_result.returncode != 0:
        raise SystemExit(p0_result.stdout + p0_result.stderr)

    db_path = bundle / "closed_loop" / "tehm.sqlite"
    artifact_root = bundle / "closed_loop" / "artifacts"
    conn = db.connect_read_only(db_path)
    try:
        ok, report = honesty.run_all(
            conn, ArtifactStore(artifact_root), db_path,
            firewall=firewall, bundle_path=bundle)
    finally:
        conn.close()
    if not report["H9"]["ok"] or not report["H11"]["ok"]:
        raise SystemExit(json.dumps({"H9": report["H9"], "H11": report["H11"]},
                                     ensure_ascii=False, sort_keys=True))
    if not ok:
        raise SystemExit(json.dumps(report, ensure_ascii=False, sort_keys=True))
    print(json.dumps({
        "ok": True,
        "bundle": str(bundle),
        "bundle_digest": checked["manifest"].get("bundle_digest"),
        "h9": report["H9"],
        "h11": report["H11"],
        "compileall": "PASS",
        "p0_regression_smoke": p0_result.stdout.strip(),
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
