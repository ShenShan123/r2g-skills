#!/usr/bin/env python3
"""Build a portable, current-source-bound TEHM v4 development freeze.

Unlike the historical v3 exporter, this builder has no dependency on external
campaign directories.  It creates a small real-Icarus RTL training snapshot,
embeds the current ``memory/`` source tree, records the working-tree digest,
and verifies H9/H11 against the resulting content-addressed bundle.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MEMORY = REPO / "memory"
DEFAULT_OUTPUT = REPO / "evidence" / "tehm-evidence-freeze-v4-dev"
TRAINING_FIXTURES = (
    "p3_positive_valid_ready",
    "p3_positive_fifo_space",
    "p3_obligation_recovery_b",
    "p3_reset_restore_a",
    "p3_reset_restore_c",
    "p3_width_correct_a",
    "p3_width_correct_b",
    "p3_overlap_priority_a",
    "p3_overlap_priority_b",
)
HELDOUT_LINEAGES = (
    "p3_positive_credit_return",
    "p3_obligation_recovery",
    "p3_reset_restore_b",
    "p3_width_correct_c",
    "p3_overlap_priority_c",
)
# A content-addressed development freeze must not inherit wall-clock bytes
# from capture/crystallization.  This timestamp is metadata for the snapshot,
# not an experimental claim about when the RTL was observed.
FREEZE_MATERIALIZED_AT = "2026-08-22T00:00:00+00:00"

import sys

if str(MEMORY) not in sys.path:
    sys.path.insert(0, str(MEMORY))

from tehm import db, honesty  # noqa: E402
from tehm.artifact_store import ArtifactStore  # noqa: E402
from tehm.canonical.capture import capture  # noqa: E402
from tehm.crystallization.build_rules import crystallize_all  # noqa: E402
from tehm.rtl.rtl_evidence import capture_rtl_fix  # noqa: E402
from tehm.rtl.rtl_oracle import IcarusOracle  # noqa: E402
from tehm.sync import (  # noqa: E402
    canonical_json,
    export_bundle,
    import_bundle,
    reexport_bundle,
    verify_bundle,
)


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git(*args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(REPO), *args],
                                   stderr=subprocess.STDOUT)


def _source_state(output: Path) -> dict:
    # Evaluation reports and disposable A/B work trees are derived from the
    # freeze, not inputs to it.  Excluding them avoids a circular digest:
    # freeze -> report(bundle_digest) -> freeze.  Their own JSON/MD receipts
    # remain durable outside the content-addressed canonical bundle.
    excluded_roots = {
        output.resolve(),
        (REPO / "evidence" / "tehm-procedural-ab-v4-dev").resolve(),
        (REPO / "evidence" / "tehm-procedural-loco-v1").resolve(),
    }
    pointer_path = (MEMORY / "evaluation" /
                    "canonical_freeze_pointer_v1.json").resolve()
    head = _git("rev-parse", "HEAD").decode().strip()
    changed = [item.decode() for item in
               _git("diff", "HEAD", "--name-only", "-z").split(b"\0") if item]
    tracked = []
    for rel in sorted(changed):
        path = REPO / rel
        if path.is_file() and path.resolve() != pointer_path:
            tracked.append({
                "path": rel,
                "head_sha256": _sha_bytes(_git("show", f"HEAD:{rel}")),
                "working_sha256": _sha_bytes(path.read_bytes()),
            })
    raw = _git("ls-files", "--others", "--exclude-standard", "-z")
    untracked = []
    for raw_rel in raw.split(b"\0"):
        if not raw_rel:
            continue
        rel = raw_rel.decode()
        path = REPO / rel
        resolved = path.resolve()
        if (not path.is_file() or
                any(root == resolved or root in resolved.parents
                    for root in excluded_roots)):
            continue
        if "__pycache__" in path.parts:
            continue
        untracked.append({"path": rel, "sha256": _sha_bytes(path.read_bytes()),
                          "size": path.stat().st_size})
    tracked.sort(key=lambda item: item["path"])
    untracked.sort(key=lambda item: item["path"])
    status = {"tracked": tracked, "untracked": untracked}
    state = {
        "head": head,
        "dirty": bool(tracked or untracked),
        "status_sha256": _sha_bytes(canonical_json(status)),
        "tracked_diff_sha256": _sha_bytes(canonical_json(tracked)),
        "untracked_files": untracked,
    }
    state["workspace_state_sha256"] = _sha_bytes(canonical_json(state))
    return state


def _source_pairs() -> list[tuple[Path, str]]:
    pairs = []
    for path in sorted(MEMORY.rglob("*"), key=lambda item: item.as_posix()):
        if (not path.is_file() or "__pycache__" in path.parts or
                path.resolve() == (MEMORY / "evaluation" /
                                   "canonical_freeze_pointer_v1.json").resolve()):
            continue
        pairs.append((path, (Path("source") / "memory" /
                            path.relative_to(MEMORY)).as_posix()))
    # M1 is the committed legacy baseline.  Include the exact read-only
    # heuristic/schema/SQLite inputs so a fresh clone can replay the same
    # baseline without reaching back into a host-local r2g-skills tree.
    legacy = REPO / "r2g-skills" / "signoff-loop" / "knowledge"
    for name in ("heuristics.json", "schema.sql", "knowledge.sqlite"):
        path = legacy / name
        if path.is_file():
            pairs.append((path, (Path("source") / "r2g-skills" /
                                 "signoff-loop" / "knowledge" / name).as_posix()))
    return pairs


def _write_reproduce(staging: Path) -> Path:
    path = staging / "reproduce.sh"
    path.write_text("""#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")" && pwd)
export PYTHONPATH="$ROOT/source/memory${PYTHONPATH:+:$PYTHONPATH}"
python3 "$ROOT/source/memory/scripts/reproduce_v4_dev.py" --bundle "$ROOT"
""")
    path.chmod(0o755)
    return path


def _build_snapshot(work: Path) -> tuple[Path, Path, list[dict], dict]:
    db_path = work / "tehm.sqlite"
    artifact_root = work / "artifacts"
    conn = db.connect(db_path)
    db.ensure_schema(conn)
    store = ArtifactStore(artifact_root)
    oracle = IcarusOracle()
    if not oracle.available:
        conn.close()
        raise RuntimeError("v4 development freeze requires real Icarus")
    receipts = []
    # Capture and rule synthesis both use tehm.db.now_local().  Pin it for
    # this isolated build only; production/runtime paths retain real time.
    original_now_local = db.now_local
    db.now_local = lambda: FREEZE_MATERIALIZED_AT
    try:
        for name in TRAINING_FIXTURES:
            receipt = capture_rtl_fix(
                conn, store,
                MEMORY / "tests" / "fixtures" / "rtl_projects" / name,
                oracle=oracle)
            receipts.append(receipt.to_dict())
        rules = crystallize_all(conn, campaign_id="live")
        rule_summary = [{"rule_id": rule["rule_id"],
                         "validity_status": rule["validity_status"],
                         "domain": rule["domain"]} for rule in rules]
        conn.commit()
    finally:
        db.now_local = original_now_local
        conn.close()
    return db_path, artifact_root, receipts, {
        "oracle": "icarus-oracle-v0.1",
        "materialized_at": FREEZE_MATERIALIZED_AT,
        "training_fixtures": list(TRAINING_FIXTURES),
        "receipts": receipts,
        "rules": rule_summary,
    }


def _same_tree(left: Path, right: Path) -> bool:
    left_files = {p.relative_to(left).as_posix() for p in left.rglob("*") if p.is_file()}
    right_files = {p.relative_to(right).as_posix() for p in right.rglob("*") if p.is_file()}
    return left_files == right_files and all(
        (left / rel).read_bytes() == (right / rel).read_bytes()
        for rel in sorted(left_files))


def _audit(bundle: Path, firewall: dict) -> tuple[bool, dict]:
    conn = db.connect_read_only(bundle / "closed_loop" / "tehm.sqlite")
    try:
        return honesty.run_all(
            conn, ArtifactStore(bundle / "closed_loop" / "artifacts"),
            bundle / "closed_loop" / "tehm.sqlite", firewall=firewall,
            bundle_path=bundle)
    finally:
        conn.close()


def build(output: Path, *, overwrite: bool = False) -> dict:
    output = output.resolve()
    if output.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite freeze: {output}")
    if output.exists():
        shutil.rmtree(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    firewall = {
        "training_lineages": list(TRAINING_FIXTURES),
        "heldout_lineages": list(HELDOUT_LINEAGES),
        "ab_lineages": list(HELDOUT_LINEAGES),
        "disjoint": True,
        "mutation_policy": "held-out/A-B runs use copied snapshots only",
    }
    source_state = _source_state(output)
    legacy_root = REPO / "r2g-skills" / "signoff-loop" / "knowledge"
    legacy_baseline = {
        name: _sha_bytes((legacy_root / name).read_bytes())
        for name in ("heuristics.json", "schema.sql", "knowledge.sqlite")
        if (legacy_root / name).is_file()
    }
    metadata = {
        "evidence_contract": "tehm-evidence-freeze-v4-dev",
        "freeze_kind": "development",
        "schema_version": "tehm-v4",
        "source_state": source_state,
        "legacy_baseline": legacy_baseline,
        "firewall": firewall,
        "pytest_status": "not_run_dependency_missing_in_builder_environment",
    }

    with tempfile.TemporaryDirectory(prefix="tehm-v4-freeze-") as td:
        work = Path(td)
        db_path, artifacts, receipts, training = _build_snapshot(work)
        metadata["training"] = training
        source_state_path = work / "source_state.json"
        source_state_path.write_bytes(canonical_json(source_state))
        firewall_path = work / "firewall.json"
        firewall_path.write_bytes(canonical_json(firewall))
        training_path = work / "training_receipts.json"
        training_path.write_bytes(canonical_json(training))
        compile_path = work / "compileall_status.json"
        compile_proc = subprocess.run(
            [sys.executable, "-m", "compileall", "-q", str(MEMORY)],
            text=True, capture_output=True)
        compile_path.write_bytes(canonical_json({
            "command": "python3 -m compileall -q memory",
            "returncode": compile_proc.returncode,
            "status": "PASS" if compile_proc.returncode == 0 else "FAIL",
            "stderr": compile_proc.stderr,
        }))
        if compile_proc.returncode != 0:
            raise RuntimeError(compile_proc.stderr)
        reproduce = _write_reproduce(work)
        task_manifest = MEMORY / "evaluation" / "procedural_ab_v4_dev_manifest.json"
        base_pairs = _source_pairs() + [
            (source_state_path, "evidence/source_state.json"),
            (firewall_path, "evidence/firewall.json"),
            (training_path, "evidence/training_receipts.json"),
            (compile_path, "evidence/tests/compileall_status.json"),
            (task_manifest, "evaluation/procedural_ab_v4_dev_manifest.json"),
            (reproduce, "reproduce.sh"),
        ]
        provisional = output.parent / f".{output.name}.provisional"
        if provisional.exists():
            shutil.rmtree(provisional)
        export_bundle(output=provisional, db_path=db_path,
                      artifact_root=artifacts, evidence_files=base_pairs,
                      metadata=metadata)
        if not verify_bundle(provisional).get("ok"):
            raise RuntimeError("provisional freeze manifest verification failed")
        all_ok, audit = _audit(provisional, firewall)
        if not audit["H9"]["ok"] or not audit["H11"]["ok"]:
            raise RuntimeError(f"v4 required honesty gates failed: {audit}")
        if not all_ok:
            raise RuntimeError(f"v4 honesty audit failed: {audit}")
        audit_path = work / "honesty_report.json"
        audit_path.write_bytes(canonical_json(audit))
        pairs = base_pairs + [(audit_path, "evidence/audit/honesty_report.json")]
        # Include the audit row while iterating to a stable self-consistent
        # report.  H11's count is then identical on the next export.
        audit_payload = audit
        for _ in range(3):
            audit_path.write_bytes(canonical_json(audit_payload))
            export_bundle(output=output, db_path=db_path,
                          artifact_root=artifacts, evidence_files=pairs,
                          metadata=metadata, overwrite=True)
            final_ok, final_audit = _audit(output, firewall)
            if not final_ok:
                raise RuntimeError(f"final v4 honesty audit failed: {final_audit}")
            if final_audit == audit_payload:
                break
            audit_payload = final_audit
        else:
            raise RuntimeError("v4 honesty report did not reach a fixed point")
        final_manifest = verify_bundle(output)
        if not final_manifest.get("ok"):
            raise RuntimeError(final_manifest.get("detail"))
        with tempfile.TemporaryDirectory(prefix="tehm-v4-roundtrip-") as rt:
            imported = Path(rt) / "imported"
            reexported = Path(rt) / "reexported"
            import_bundle(bundle=output, output=imported)
            reexport_bundle(source_bundle=imported, output=reexported)
            if not _same_tree(output, reexported):
                raise RuntimeError("v4 export/import/export is not byte-stable")
        shutil.rmtree(provisional)
    return {
        "bundle": str(output),
        "bundle_manifest": str(output / "bundle_manifest.json"),
        "bundle_digest": final_manifest["manifest"].get("bundle_digest"),
        "manifest_digest": final_manifest["manifest"].get("manifest_digest"),
        "source_state": source_state,
        "training": training,
        "honesty": audit_payload,
        "receipts": receipts,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args(argv)
    result = build(args.output, overwrite=args.overwrite)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
