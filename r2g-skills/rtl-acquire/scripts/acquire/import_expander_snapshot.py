#!/usr/bin/env python3
"""Import a certified rtl-expander release into the rtl-acquire CSV boundary.

The expander corpus is a mutable factory.  R2G consumes only an immutable,
CERTIFIED snapshot and re-verifies both release metadata and source bytes before
emitting candidates.  The companion bridge manifest lets expand_candidates.py
repeat that verification immediately before ORFS synthesis.
"""

from __future__ import annotations

import argparse
import csv
import functools
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BRIDGE_SCHEMA = "r2g_rtl_expander_bridge_v2"
LEGACY_BRIDGE_SCHEMA = "r2g_rtl_expander_bridge_v1"
RELEASE_SCHEMA = "rtl_corpus_release_identity_v1"
CERTIFICATION_SCHEMA = "rtl_corpus_certification_v1"
VIEWS = {
    "public_export_allowed": "public_export_allowed.jsonl",
}
DEFAULT_LANGUAGES = {"verilog", "systemverilog"}
CSV_FIELDS = [
    "source", "design", "priority", "expected_top", "source_path",
    "rtl_files", "include_dirs", "top_parameters", "resource_tier", "notes",
    "function_category", "expander_bridge_manifest", "expander_design_id",
]


class SnapshotError(ValueError):
    """The supplied snapshot cannot be trusted or represented by R2G."""


class UnsupportedDesign(SnapshotError):
    """A valid snapshot row is outside this R2G handoff's declared language scope."""


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False) + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SnapshotError(f"invalid JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SnapshotError(f"expected JSON object: {path}")
    return value


def _under(path: Path, root: Path, *, kind: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise SnapshotError(f"{kind} escapes or is missing under {root}: {path}") from exc
    return resolved


def resolve_snapshot(corpus_root: Path, snapshot: str | None) -> Path:
    snapshots = corpus_root.resolve() / "snapshots"
    if snapshot:
        requested = Path(snapshot)
        candidate = requested if requested.is_absolute() else snapshots / requested
    else:
        latest = _read_json(snapshots / "latest_release.json")
        candidate = snapshots / str(latest.get("corpus_snapshot_id") or "")
    return _under(candidate, snapshots, kind="snapshot")


def verify_snapshot(snapshot: Path, view: str) -> tuple[dict[str, Any], Path, str]:
    identity_path = snapshot / "release_identity.json"
    completion_path = snapshot / "completion.json"
    identity = _read_json(identity_path)
    completion = _read_json(completion_path)
    if identity.get("schema") != RELEASE_SCHEMA:
        raise SnapshotError("unsupported rtl-expander release identity schema")
    if identity.get("corpus_snapshot_id") != snapshot.name:
        raise SnapshotError("snapshot directory and release identity disagree")
    if completion.get("schema") != CERTIFICATION_SCHEMA:
        raise SnapshotError("unsupported rtl-expander certification schema")
    if completion.get("snapshot_id") != snapshot.name:
        raise SnapshotError("completion record belongs to a different snapshot")
    if completion.get("status") != "CERTIFIED":
        raise SnapshotError("rtl-expander snapshot is not CERTIFIED")

    release_inputs = {
        key: value for key, value in identity.items()
        if key not in {"schema", "corpus_snapshot_id", "release_sha256", "created_at"}
    }
    if sha256_bytes(canonical_bytes(release_inputs)) != identity.get("release_sha256"):
        raise SnapshotError("release identity digest mismatch")

    filename = VIEWS.get(view)
    if filename is None:
        raise SnapshotError(f"unsupported snapshot view: {view}")
    manifest = _under(snapshot / "manifests" / filename, snapshot,
                      kind="snapshot manifest")
    expected = (identity.get("manifest_hashes") or {}).get(filename)
    actual = sha256_file(manifest)
    if not expected or actual != expected:
        raise SnapshotError(f"manifest digest mismatch: {filename}")
    return identity, manifest, actual


def _safe_relative(value: Any, *, field: str) -> Path:
    path = Path(str(value or ""))
    if not value or path.is_absolute() or ".." in path.parts:
        raise SnapshotError(f"invalid relative {field}: {value!r}")
    return path


def _priority(record: dict[str, Any]) -> str:
    tier = str((record.get("quality") or {}).get("training_tier") or "")
    return "high" if tier == "TRAINING_GOLD" else "medium"


def _resource_tier(record: dict[str, Any]) -> str:
    resource = str((record.get("resource") or {}).get("class") or "").upper()
    return "high" if resource in {"LARGE", "XLARGE"} else "normal"


def _candidate_from_record(record: dict[str, Any], corpus_root: Path,
                           snapshot_id: str, view: str) -> tuple[dict[str, str], dict[str, Any]]:
    design_id = str(record.get("design_id") or "")
    if not re.fullmatch(r"d_[0-9a-f]+", design_id):
        raise SnapshotError(f"invalid design_id: {design_id!r}")
    provenance = record.get("provenance") or {}
    build = record.get("build") or {}
    source = record.get("source") or {}
    release = record.get("release") or {}
    synthesis = record.get("synthesis") or {}
    top = str(build.get("top_module") or "")
    repository_url = str(provenance.get("repository_url") or "")
    commit = str(provenance.get("commit_sha") or "").lower()
    revision_key = str(source.get("repository_revision_key") or "")
    if not top or not repository_url or repository_url == "UNKNOWN":
        raise SnapshotError(f"{design_id}: incomplete top/repository provenance")
    if not re.fullmatch(r"[0-9a-f]{40,64}", commit):
        raise SnapshotError(f"{design_id}: invalid immutable commit")
    if not revision_key or not revision_key.endswith("@" + commit):
        raise SnapshotError(f"{design_id}: repository revision does not bind commit")
    if synthesis.get("generic_pass") is not True:
        raise SnapshotError(f"{design_id}: snapshot row is not generic-synthesis-valid")
    if release.get("release_policy") != "PUBLIC_EXPORT_ALLOWED":
        raise SnapshotError(f"{design_id}: row is not public-export eligible")

    languages = {str(item).lower() for item in (source.get("source_languages") or [])}
    if not languages:
        languages = {str(unit.get("language") or "").lower()
                     for unit in (source.get("source_units") or [])}
    if not languages or not languages.issubset(DEFAULT_LANGUAGES):
        raise UnsupportedDesign(f"{design_id}: unsupported source languages {sorted(languages)}")

    source_root = _under(Path(str(source.get("original_root") or "")),
                         corpus_root / "repositories", kind=f"{design_id} source root")
    unit_hashes: dict[str, str] = {}
    for unit in source.get("source_units") or []:
        rel = _safe_relative(unit.get("path"), field="source unit")
        path = _under(source_root / rel, source_root, kind=f"{design_id} source unit")
        expected = str(unit.get("sha256") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", expected) or sha256_file(path) != expected:
            raise SnapshotError(f"{design_id}: source digest mismatch: {rel}")
        unit_hashes[str(rel)] = expected

    compile_rel = [_safe_relative(value, field="compile source")
                   for value in (build.get("compile_source_files") or [])]
    if not compile_rel:
        raise SnapshotError(f"{design_id}: empty compile source closure")
    if any(str(path) not in unit_hashes for path in compile_rel):
        raise SnapshotError(f"{design_id}: compile source missing from source-unit manifest")
    compile_paths = [_under(source_root / rel, source_root,
                            kind=f"{design_id} compile source") for rel in compile_rel]
    include_paths = [
        _under(source_root / _safe_relative(value, field="include directory"), source_root,
               kind=f"{design_id} include directory")
        for value in (build.get("include_dirs") or [])
    ]
    if any(not path.is_dir() for path in include_paths):
        raise SnapshotError(f"{design_id}: include path is not a directory")

    parameters = build.get("parameters") or {}
    if not isinstance(parameters, dict):
        raise SnapshotError(f"{design_id}: parameters must be an object")
    top_parameters = ";".join(f"{key}={parameters[key]}" for key in sorted(parameters))
    function_category = str((record.get("functional_ontology") or {}).get("label") or "other")
    design = f"exp_{design_id}"
    notes = (
        f"rtl-expander snapshot={snapshot_id}; design_id={design_id}; "
        f"family_id={record.get('family_id', '')}; release_policy={release.get('release_policy', '')}"
    )
    row = {
        "source": "rtl-expander",
        "design": design,
        "priority": _priority(record),
        "expected_top": top,
        "source_path": str(compile_paths[0]),
        "rtl_files": ";".join(map(str, compile_paths)),
        "include_dirs": ";".join(map(str, include_paths)),
        "top_parameters": top_parameters,
        "resource_tier": _resource_tier(record),
        "notes": notes,
        "function_category": function_category,
        "expander_bridge_manifest": "",  # filled after the output path is known
        "expander_design_id": design_id,
    }
    bridge_candidate = {
        "design": design,
        "expander_design_id": design_id,
        "family_id": record.get("family_id"),
        "repository_revision_key": revision_key,
        "repository_url": repository_url,
        "commit_sha": commit,
        "license_status": "allow",
        "license_evidence": release.get("license_evidence") or {},
        "release_policy": release.get("release_policy"),
        "top_module": top,
        "top_parameters": top_parameters,
        "function_category": function_category,
        "source_root": str(source_root),
        "compile_source_files": [str(path) for path in compile_paths],
        "include_dirs": [str(path) for path in include_paths],
        "source_manifest": [
            {"path": str(_under(source_root / Path(rel), source_root,
                                kind=f"{design_id} source unit")), "sha256": digest}
            for rel, digest in sorted(unit_hashes.items())
        ],
    }
    return row, bridge_candidate


def load_records(manifest: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with manifest.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SnapshotError(f"invalid JSONL at {manifest}:{line_number}") from exc
            if not isinstance(value, dict):
                raise SnapshotError(f"non-object JSONL row at {manifest}:{line_number}")
            records.append(value)
    return records


def bridge_digest(payload: dict[str, Any]) -> str:
    material = dict(payload)
    material.pop("bridge_sha256", None)
    return sha256_bytes(canonical_bytes(material))


@functools.lru_cache(maxsize=16)
def _verified_bridge(path_text: str, content_sha256: str) -> dict[str, Any]:
    """Bind a bridge back to the certified source release, not just to itself."""
    bridge_path = Path(path_text)
    if sha256_file(bridge_path) != content_sha256:
        raise SnapshotError("rtl-expander bridge changed while being loaded")
    bridge = _read_json(bridge_path)
    schema = bridge.get("schema")
    if schema not in {BRIDGE_SCHEMA, LEGACY_BRIDGE_SCHEMA} or bridge_digest(bridge) != bridge.get("bridge_sha256"):
        raise SnapshotError("rtl-expander bridge digest mismatch")
    corpus_root = Path(str(bridge.get("corpus_root") or "")).resolve(strict=True)
    snapshot = resolve_snapshot(corpus_root, str(bridge.get("snapshot_id") or ""))
    identity, manifest, manifest_digest = verify_snapshot(snapshot, str(bridge.get("view") or ""))
    if identity.get("release_sha256") != bridge.get("release_sha256"):
        raise SnapshotError("rtl-expander bridge release identity mismatch")
    if manifest_digest != bridge.get("manifest_sha256"):
        raise SnapshotError("rtl-expander bridge manifest identity mismatch")

    records: dict[str, dict[str, Any]] = {}
    for record in load_records(manifest):
        design_id = str(record.get("design_id") or "")
        if design_id in records:
            raise SnapshotError(f"duplicate design in certified manifest: {design_id}")
        records[design_id] = record
    seen: set[str] = set()
    for candidate in bridge.get("candidates") or []:
        design_id = str(candidate.get("expander_design_id") or "")
        if design_id in seen or design_id not in records:
            raise SnapshotError("bridge candidate is absent or duplicated in certified manifest")
        seen.add(design_id)
        _row, expected = _candidate_from_record(
            records[design_id], corpus_root, snapshot.name, str(bridge["view"]))
        if schema == LEGACY_BRIDGE_SCHEMA:
            expected.pop("function_category", None)
        if canonical_bytes(expected) != canonical_bytes(candidate):
            raise SnapshotError(f"bridge candidate differs from certified manifest: {design_id}")
    if not seen:
        raise SnapshotError("rtl-expander bridge contains no candidates")
    return bridge


def import_snapshot(corpus_root: Path, snapshot_arg: str | None, view: str,
                    output_csv: Path, bridge_path: Path, limit: int = 0) -> dict[str, Any]:
    corpus_root = corpus_root.resolve(strict=True)
    snapshot = resolve_snapshot(corpus_root, snapshot_arg)
    identity, manifest, manifest_digest = verify_snapshot(snapshot, view)
    records = sorted(load_records(manifest), key=lambda row: str(row.get("design_id") or ""))
    if limit > 0:
        records = records[:limit]
    rows: list[dict[str, str]] = []
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    excluded: list[dict[str, str]] = []
    for record in records:
        try:
            row, candidate = _candidate_from_record(record, corpus_root, snapshot.name, view)
        except UnsupportedDesign as exc:
            excluded.append({"design_id": str(record.get("design_id") or ""), "reason": str(exc)})
            continue
        if row["design"] in seen:
            raise SnapshotError(f"duplicate design identity: {row['design']}")
        seen.add(row["design"])
        rows.append(row)
        candidates.append(candidate)
    if not rows:
        raise SnapshotError("snapshot produced no R2G-compatible candidates")

    bridge_path = bridge_path.resolve()
    bridge = {
        "schema": BRIDGE_SCHEMA,
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "corpus_root": str(corpus_root),
        "snapshot_id": snapshot.name,
        "release_sha256": identity["release_sha256"],
        "view": view,
        "manifest_sha256": manifest_digest,
        "candidates": candidates,
        "excluded": excluded,
    }
    bridge["bridge_sha256"] = bridge_digest(bridge)
    bridge_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_bridge = bridge_path.with_name(f".{bridge_path.name}.tmp.{os.getpid()}")
    temporary_bridge.write_bytes(canonical_bytes(bridge))
    os.replace(temporary_bridge, bridge_path)

    for row in rows:
        row["expander_bridge_manifest"] = str(bridge_path)
    output_csv = output_csv.resolve()
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    temporary_csv = output_csv.with_name(f".{output_csv.name}.tmp.{os.getpid()}")
    with temporary_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary_csv, output_csv)
    return {
        "schema": "r2g_rtl_expander_import_result_v1",
        "snapshot_id": snapshot.name,
        "view": view,
        "candidate_count": len(rows),
        "excluded_count": len(excluded),
        "candidate_csv": str(output_csv),
        "bridge_manifest": str(bridge_path),
        "bridge_sha256": bridge["bridge_sha256"],
    }


def verified_candidate_provenance(candidate: dict[str, str], source_paths: list[Path],
                                  include_dirs: list[Path]) -> dict[str, Any] | None:
    """Verify and return expander provenance, or None for a normal candidate."""
    raw_bridge = (candidate.get("expander_bridge_manifest") or "").strip()
    raw_design = (candidate.get("expander_design_id") or "").strip()
    if not raw_bridge and not raw_design:
        return None
    if not raw_bridge or not raw_design:
        raise SnapshotError("incomplete rtl-expander bridge fields")
    bridge_path = Path(os.path.expandvars(os.path.expanduser(raw_bridge))).resolve(strict=True)
    bridge = _verified_bridge(str(bridge_path), sha256_file(bridge_path))
    matches = [row for row in (bridge.get("candidates") or [])
               if row.get("expander_design_id") == raw_design]
    if len(matches) != 1:
        raise SnapshotError("rtl-expander design is absent or duplicated in bridge")
    record = matches[0]
    if record.get("design") != candidate.get("design"):
        raise SnapshotError("candidate design does not match rtl-expander bridge")
    if record.get("top_module") != (candidate.get("expected_top") or "").strip():
        raise SnapshotError("candidate top does not match rtl-expander bridge")
    if record.get("top_parameters", "") != (candidate.get("top_parameters") or "").strip():
        raise SnapshotError("candidate parameters differ from rtl-expander bridge")
    candidate_category = (candidate.get("function_category") or "").strip()
    if candidate_category and candidate_category != str(record.get("function_category") or ""):
        raise SnapshotError("candidate function category differs from rtl-expander bridge")
    expected_sources = [str(Path(value).resolve()) for value in record.get("compile_source_files") or []]
    actual_sources = [str(Path(value).resolve()) for value in source_paths]
    if actual_sources != expected_sources:
        raise SnapshotError("candidate compile closure differs from rtl-expander bridge")
    expected_includes = [str(Path(value).resolve()) for value in record.get("include_dirs") or []]
    actual_includes = [str(Path(value).resolve()) for value in include_dirs]
    if actual_includes != expected_includes:
        raise SnapshotError("candidate include search path differs from rtl-expander bridge")
    expected_hashes = {str(Path(item["path"]).resolve()): item["sha256"]
                       for item in record.get("source_manifest") or []}
    if not expected_hashes:
        raise SnapshotError("rtl-expander bridge has no source manifest")
    for path, expected in expected_hashes.items():
        if sha256_file(Path(path)) != expected:
            raise SnapshotError(f"rtl-expander source changed after certification: {path}")
    if record.get("release_policy") != "PUBLIC_EXPORT_ALLOWED" or record.get("license_status") != "allow":
        raise SnapshotError("rtl-expander candidate is not public-export eligible")
    return {
        "source_kind": "cloned_repo",
        "source_commit": record["commit_sha"],
        "license_status": "allow",
        "license_evidence": json.dumps(record.get("license_evidence") or {}, sort_keys=True),
        "source_url": record["repository_url"],
        "expander_provenance": {
            "snapshot_id": bridge["snapshot_id"],
            "release_sha256": bridge["release_sha256"],
            "manifest_sha256": bridge["manifest_sha256"],
            "bridge_sha256": bridge["bridge_sha256"],
            "design_id": raw_design,
            "family_id": record.get("family_id"),
            "repository_revision_key": record.get("repository_revision_key"),
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-root", type=Path, required=True)
    parser.add_argument("--snapshot", help="snapshot ID or absolute snapshot directory; default latest certified")
    parser.add_argument("--view", choices=sorted(VIEWS), default="public_export_allowed")
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--bridge-manifest", type=Path,
                        help="default: OUTPUT_CSV with .expander-bridge.json suffix")
    parser.add_argument("--limit", type=int, default=0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    bridge = args.bridge_manifest or args.output_csv.with_suffix(".expander-bridge.json")
    try:
        result = import_snapshot(args.corpus_root, args.snapshot, args.view,
                                 args.output_csv, bridge, args.limit)
    except (OSError, SnapshotError) as exc:
        print(f"rtl-expander import rejected: {exc}", file=os.sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
