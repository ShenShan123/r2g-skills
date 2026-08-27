#!/usr/bin/env python3
"""Verify a TEHM Evidence Contract v3 bundle without mutating its database."""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

MEMORY_ROOT = Path(__file__).resolve().parents[1]
if str(MEMORY_ROOT) not in sys.path:
    sys.path.insert(0, str(MEMORY_ROOT))

from tehm import db, honesty
from tehm.artifact_store import ArtifactStore
from tehm.sync import import_bundle, reexport_bundle, verify_bundle


def _json(root: Path, rel: str) -> dict:
    return json.loads((root / rel).read_text())


def _same_tree(left: Path, right: Path) -> bool:
    left_files = {p.relative_to(left).as_posix() for p in left.rglob("*") if p.is_file()}
    right_files = {p.relative_to(right).as_posix() for p in right.rglob("*") if p.is_file()}
    if left_files != right_files:
        return False
    return all((left / rel).read_bytes() == (right / rel).read_bytes()
               for rel in sorted(left_files))


def _verify_m0_m8(root: Path, recomputed_path: Path) -> dict:
    """Compare replayed M0--M8 outcomes with the frozen report.

    The replay writes host-specific paths and carries a firewall snapshot that
    is intentionally broader in v3 than in the v2 pilot.  The result-bearing
    fields below are therefore the contract; path-bearing metadata is not.
    """
    frozen = _json(root, "evaluation/m0_m8_v2_report.json")
    recomputed = json.loads(Path(recomputed_path).read_text())
    contract_keys = (
        "arms", "task_rows", "summary", "lineage_summary", "memory_funnel",
        "pilot_scope",
    )
    mismatches = [key for key in contract_keys if frozen.get(key) != recomputed.get(key)]
    if mismatches:
        raise RuntimeError(f"M0-M8 replay differs in contract fields: {mismatches}")
    return {"ok": True, "fields": list(contract_keys)}


def verify(root: Path, *, m0_m8_report: Path | None = None) -> dict:
    root = Path(root).resolve()
    checked = verify_bundle(root)
    if not checked["ok"]:
        raise RuntimeError(checked["detail"])
    manifest = checked["manifest"]
    expected = manifest.get("metadata", {}).get("expected", {})
    db_path = root / "closed_loop" / "tehm.sqlite"
    artifact_root = root / "closed_loop" / "artifacts"
    firewall = manifest.get("metadata", {}).get("firewall")
    conn = db.connect_read_only(db_path)
    try:
        all_ok, report = honesty.run_all(
            conn, ArtifactStore(artifact_root), db_path,
            firewall=firewall, bundle_path=root)
    finally:
        conn.close()
    if not all_ok:
        raise RuntimeError(f"H1-H12 audit failed: {report}")
    stored_report = _json(root, "evidence/audit/honesty_report.json")
    if stored_report != report:
        raise RuntimeError("stored H1-H12 report differs from a fresh audit")

    calibration = _json(root, "evidence/physical/calibration_report.json")
    before = calibration.get("physical_memory_count_before")
    after = calibration.get("physical_memory_count_after")
    if before != after or calibration.get("heldout_memory_mutation") != 0:
        raise RuntimeError("calibration evidence reports a memory mutation")
    if before != expected.get("calibration_memory_count"):
        raise RuntimeError("calibration count does not match the freeze contract")

    test_result = _json(root, "evidence/tests/pytest_memory_tests.json")
    expected_tests = (manifest.get("metadata", {}).get("expected", {})
                      .get("tests_passed"))
    if test_result.get("returncode") != 0 or test_result.get("passed") != expected_tests:
        raise RuntimeError(f"test contract failed: {test_result}")

    # A real import/export round trip is part of H11, not merely a manifest
    # serialization check.  Temporary directories are outside the freeze.
    with tempfile.TemporaryDirectory(prefix="tehm-v3-roundtrip-") as temp:
        temp = Path(temp)
        imported = temp / "imported"
        reexported = temp / "reexported"
        import_bundle(bundle=root, output=imported)
        reexport_bundle(source_bundle=imported, output=reexported)
        if not _same_tree(root, reexported):
            raise RuntimeError("export -> import -> export is not byte-stable")

    m0_m8 = None
    if m0_m8_report is not None:
        m0_m8 = _verify_m0_m8(root, m0_m8_report)

    return {
        "ok": True,
        "bundle_version": manifest["metadata"].get("evidence_contract"),
        "manifest_digest": manifest.get("manifest_digest"),
        "bundle_digest": manifest.get("bundle_digest"),
        "test_passed": test_result.get("passed"),
        "calibration_memory_before": before,
        "calibration_memory_after": after,
        "h1_h12_all_green": all_ok,
        "roundtrip_byte_stable": True,
        "m0_m8_replay": m0_m8,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bundle", type=Path, required=True)
    ap.add_argument("--m0-m8-report", type=Path,
                    help="recomputed M0-M8 JSON whose contract fields must match")
    args = ap.parse_args(argv)
    result = verify(args.bundle, m0_m8_report=args.m0_m8_report)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
