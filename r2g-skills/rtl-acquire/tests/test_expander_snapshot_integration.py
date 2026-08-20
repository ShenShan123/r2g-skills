from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = (Path(__file__).parents[1] / "scripts" / "acquire" /
          "import_expander_snapshot.py")
SPEC = importlib.util.spec_from_file_location("import_expander_snapshot", SCRIPT)
bridge = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(bridge)


def _write_certified_snapshot(root: Path, *, certified: bool = True) -> tuple[Path, Path]:
    corpus = root / "corpus"
    source_root = corpus / "repositories" / "github" / "example" / "core" / ("a" * 40) / "source"
    source_root.mkdir(parents=True)
    rtl = source_root / "rtl" / "top.sv"
    rtl.parent.mkdir()
    rtl.write_text("module top(input logic a, output logic y); assign y=a; endmodule\n")
    header = source_root / "include" / "defs.svh"
    header.parent.mkdir()
    header.write_text("`define WIDTH 1\n")
    source_units = [
        {"path": "rtl/top.sv", "language": "systemverilog",
         "sha256": hashlib.sha256(rtl.read_bytes()).hexdigest()},
        {"path": "include/defs.svh", "language": "systemverilog",
         "sha256": hashlib.sha256(header.read_bytes()).hexdigest()},
    ]
    row = {
        "design_id": "d_" + "1" * 32,
        "family_id": "f_" + "2" * 32,
        "provenance": {
            "repository_url": "https://github.com/example/core",
            "commit_sha": "a" * 40,
        },
        "release": {
            "license_status": "PERMISSIVE_CONFIRMED",
            "release_policy": "PUBLIC_EXPORT_ALLOWED",
            "license_evidence": {"license_status": "PERMISSIVE_CONFIRMED"},
        },
        "build": {
            "top_module": "top",
            "compile_source_files": ["rtl/top.sv"],
            "include_dirs": [".", "include"],
            "parameters": {"WIDTH": 1},
        },
        "source": {
            "source_languages": ["systemverilog"],
            "source_units": source_units,
            "original_root": str(source_root),
            "repository_revision_key": "github:example/core@" + "a" * 40,
        },
        "synthesis": {"generic_pass": True},
        "quality": {"training_tier": "TRAINING_GOLD"},
        "resource": {"class": "SMALL"},
    }
    snapshot = corpus / "snapshots" / "release-001"
    manifests = snapshot / "manifests"
    manifests.mkdir(parents=True)
    manifest = manifests / "public_export_allowed.jsonl"
    manifest.write_bytes(bridge.canonical_bytes(row))
    empty = manifests / "provenance_complete_synthesis_valid.jsonl"
    empty.write_bytes(bridge.canonical_bytes(row))
    hashes = {
        "public_export_allowed.jsonl": bridge.sha256_file(manifest),
        "provenance_complete_synthesis_valid.jsonl": bridge.sha256_file(empty),
    }
    inputs = {"pipeline_schema": "rtl_corpus_state_v1", "manifest_hashes": hashes}
    identity = {
        "schema": bridge.RELEASE_SCHEMA,
        "corpus_snapshot_id": snapshot.name,
        "release_sha256": bridge.sha256_bytes(bridge.canonical_bytes(inputs)),
        "created_at": "2026-08-20T00:00:00+00:00",
        **inputs,
    }
    (snapshot / "release_identity.json").write_bytes(bridge.canonical_bytes(identity))
    completion = {
        "schema": bridge.CERTIFICATION_SCHEMA,
        "snapshot_id": snapshot.name,
        "status": "CERTIFIED" if certified else "NEEDS_HARDENING",
    }
    (snapshot / "completion.json").write_bytes(bridge.canonical_bytes(completion))
    (corpus / "snapshots" / "latest_release.json").write_bytes(
        bridge.canonical_bytes(identity))
    return corpus, rtl


def test_certified_snapshot_import_and_pre_synth_reverification(tmp_path: Path):
    corpus, _rtl = _write_certified_snapshot(tmp_path)
    output_csv = tmp_path / "candidates.csv"
    bridge_path = tmp_path / "bridge.json"
    result = bridge.import_snapshot(corpus, None, "public_export_allowed",
                                    output_csv, bridge_path)
    assert result["candidate_count"] == 1
    row = next(csv.DictReader(output_csv.open(newline="", encoding="utf-8")))
    source_paths = [Path(value) for value in row["rtl_files"].split(";")]
    include_dirs = [Path(value) for value in row["include_dirs"].split(";")]
    provenance = bridge.verified_candidate_provenance(row, source_paths, include_dirs)
    assert provenance["source_commit"] == "a" * 40
    assert provenance["license_status"] == "allow"
    assert provenance["expander_provenance"]["snapshot_id"] == "release-001"


def test_noncertified_snapshot_is_rejected(tmp_path: Path):
    corpus, _rtl = _write_certified_snapshot(tmp_path, certified=False)
    with pytest.raises(bridge.SnapshotError, match="not CERTIFIED"):
        bridge.import_snapshot(corpus, None, "public_export_allowed",
                               tmp_path / "out.csv", tmp_path / "bridge.json")


def test_manifest_tamper_is_rejected(tmp_path: Path):
    corpus, _rtl = _write_certified_snapshot(tmp_path)
    manifest = corpus / "snapshots" / "release-001" / "manifests" / "public_export_allowed.jsonl"
    manifest.write_text(manifest.read_text() + "{}\n")
    with pytest.raises(bridge.SnapshotError, match="manifest digest mismatch"):
        bridge.import_snapshot(corpus, None, "public_export_allowed",
                               tmp_path / "out.csv", tmp_path / "bridge.json")


def test_source_tamper_after_import_is_rejected(tmp_path: Path):
    corpus, rtl = _write_certified_snapshot(tmp_path)
    output_csv = tmp_path / "candidates.csv"
    bridge.import_snapshot(corpus, None, "public_export_allowed",
                           output_csv, tmp_path / "bridge.json")
    row = next(csv.DictReader(output_csv.open(newline="", encoding="utf-8")))
    rtl.write_text("module top; endmodule\n")
    with pytest.raises(bridge.SnapshotError, match="source digest mismatch|source changed after certification"):
        bridge.verified_candidate_provenance(
            row, [Path(value) for value in row["rtl_files"].split(";")],
            [Path(value) for value in row["include_dirs"].split(";")],
        )


def test_self_rehashed_forged_bridge_is_rejected_against_snapshot(tmp_path: Path):
    corpus, _rtl = _write_certified_snapshot(tmp_path)
    output_csv = tmp_path / "candidates.csv"
    bridge_path = tmp_path / "bridge.json"
    bridge.import_snapshot(corpus, None, "public_export_allowed", output_csv, bridge_path)
    payload = json.loads(bridge_path.read_text())
    payload["candidates"][0]["top_module"] = "forged_top"
    payload["bridge_sha256"] = bridge.bridge_digest(payload)
    bridge_path.write_bytes(bridge.canonical_bytes(payload))
    row = next(csv.DictReader(output_csv.open(newline="", encoding="utf-8")))
    with pytest.raises(bridge.SnapshotError, match="differs from certified manifest"):
        bridge.verified_candidate_provenance(
            row, [Path(value) for value in row["rtl_files"].split(";")],
            [Path(value) for value in row["include_dirs"].split(";")],
        )


def test_candidate_cannot_borrow_bridge_for_different_top(tmp_path: Path):
    corpus, _rtl = _write_certified_snapshot(tmp_path)
    output_csv = tmp_path / "candidates.csv"
    bridge.import_snapshot(corpus, None, "public_export_allowed",
                           output_csv, tmp_path / "bridge.json")
    row = next(csv.DictReader(output_csv.open(newline="", encoding="utf-8")))
    row["expected_top"] = "attacker_top"
    with pytest.raises(bridge.SnapshotError, match="top does not match"):
        bridge.verified_candidate_provenance(
            row, [Path(value) for value in row["rtl_files"].split(";")],
            [Path(value) for value in row["include_dirs"].split(";")],
        )
