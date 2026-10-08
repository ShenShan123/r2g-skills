#!/usr/bin/env python3
"""Materialize readable DesignInstance catalogs over immutable revision snapshots."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

from frontier import canonical_repository_identity, default_frontier_path
from corpus_state import CorpusState


STORAGE_SCHEMA = "rtl_storage_layout_v2"


def slug(value: str, limit: int = 48) -> str:
    result = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "design"
    return result[:limit].rstrip("-")


def repository_slug(record: dict[str, Any]) -> str:
    """Prefer canonical repository identity over a disposable intake name."""
    repository_url = str(record.get("provenance", {}).get("repository_url") or "")
    try:
        return slug(canonical_repository_identity(repository_url)["repo_name"])
    except ValueError:
        return slug(str(record.get("identity", {}).get("repository_name") or "repository"))


def design_display_name(record: dict[str, Any]) -> str:
    suffix = str(record["design_id"]).removeprefix("d_")[:16]
    top = record.get("build", {}).get("top_module", "top")
    return f"{repository_slug(record)}__{slug(str(top))}__{suffix}"


def verified_source_units(record: dict[str, Any], source_root: Path) -> bool:
    """Require every referenced source unit to exist under the revision with its recorded hash."""
    units = record.get("source", {}).get("source_units") or []
    if not units or not source_root.is_dir():
        return False
    resolved_root = source_root.resolve()
    for unit in units:
        candidate = source_root / str(unit.get("path") or "")
        try:
            candidate.resolve(strict=True).relative_to(resolved_root)
        except (OSError, ValueError):
            return False
        if not candidate.is_file():
            return False
        expected = str(unit.get("sha256") or "").lower()
        if not expected or hashlib.sha256(candidate.read_bytes()).hexdigest() != expected:
            return False
    return True


def admitted_source_units(record: dict[str, Any], source_root: Path) -> bool:
    """Validate immutable references without re-reading content admitted earlier."""
    units = record.get("source", {}).get("source_units") or []
    if not units or not source_root.is_dir():
        return False
    resolved_root = source_root.resolve()
    for unit in units:
        candidate = source_root / str(unit.get("path") or "")
        try:
            candidate.resolve(strict=True).relative_to(resolved_root)
        except (OSError, ValueError):
            return False
        if not candidate.is_file() or not re.fullmatch(
            r"[0-9a-f]{64}", str(unit.get("sha256") or "").lower()
        ):
            return False
    return True


def archive_directory(path: Path, allowed_root: Path, archive_root: Path) -> bool:
    """Move an owned legacy directory to a recoverable quarantine location."""
    try:
        path.resolve(strict=True).relative_to(allowed_root.resolve(strict=True))
    except (OSError, ValueError):
        return False
    destination = archive_root / path.name
    if destination.exists():
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.replace(path, destination)
    return True


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def load_processor(skill_root: Path):
    script = skill_root / "scripts" / "run_expansion_round.py"
    spec = importlib.util.spec_from_file_location("rtl_expander_processor", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def revision_lookup(frontier_path: Path) -> dict[tuple[str, str], dict[str, str]]:
    if not frontier_path.exists():
        return {}
    connection = sqlite3.connect(frontier_path)
    connection.row_factory = sqlite3.Row
    rows = connection.execute("SELECT repository_key,commit_sha,repository_revision_key,source_path FROM repository_revisions").fetchall()
    connection.close()
    return {(row["repository_key"], row["commit_sha"].lower()): dict(row) for row in rows}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-root", type=Path, default=Path.home() / "work" / "data" / "rtl_corpus")
    parser.add_argument("--skill-root", type=Path, default=Path(__file__).parents[1])
    parser.add_argument("--archive-linked-legacy", action="store_true", help="Move verified v1 per-design copies into recoverable quarantine")
    parser.add_argument(
        "--design-id-file", type=Path,
        help="Materialize only these changed DesignIDs (one per line)",
    )
    parser.add_argument(
        "--trust-admission-hashes", action="store_true",
        help="For an explicit changed-ID set, validate immutable references without rehashing source bytes",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    processor = load_processor(args.skill_root)
    if args.trust_admission_hashes and args.design_id_file is None:
        raise SystemExit("--trust-admission-hashes requires --design-id-file")
    manifest = args.corpus_root / "manifests" / "all_designs.jsonl"
    designs = processor.load_jsonl(manifest, "design_id")
    selected_ids = (
        {line.strip() for line in args.design_id_file.read_text(encoding="utf-8").splitlines() if line.strip()}
        if args.design_id_file is not None else set(designs)
    )
    unknown = selected_ids - set(designs)
    if unknown:
        raise RuntimeError(f"unknown DesignIDs in selection: {sorted(unknown)[:5]}")
    selected = [designs[design_id] for design_id in sorted(selected_ids)]
    revisions = revision_lookup(default_frontier_path(args.corpus_root))
    linked = missing_revision = verified = legacy_sources_eligible = legacy_facts_eligible = 0
    legacy_sources_archived = legacy_facts_archived = stale_catalogs_archived = 0
    migration_needed = any(record.get("storage", {}).get("storage_schema") != STORAGE_SCHEMA for record in selected)
    backup_path: Path | None = None
    with processor.FileLock(args.corpus_root / "locks" / "manifest.lock", blocking=True):
        if (
            migration_needed and args.design_id_file is None
            and not args.dry_run and manifest.is_file()
        ):
            stamp = processor.utc_now().replace(":", "").replace("+00:00", "Z").replace("-", "")
            backup_path = args.corpus_root / "snapshots" / "storage-migrations" / f"{stamp}-{os.getpid()}" / "all_designs.v1.jsonl"
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            os.link(manifest, backup_path)
        for record in selected:
            provenance = record.get("provenance", {})
            try:
                repository_key = canonical_repository_identity(provenance.get("repository_url", ""))["repository_key"]
            except ValueError:
                repository_key = ""
            revision = revisions.get((repository_key, str(provenance.get("commit_sha", "")).lower()))
            display_name = design_display_name(record)
            design_dir = args.corpus_root / "designs" / display_name
            previous_value = str(record.get("storage", {}).get("design_path") or "")
            previous_design_path = Path(previous_value) if previous_value else None
            legacy_original = args.corpus_root / "original_rtl" / record["design_id"]
            legacy_facts = args.corpus_root / "recovered_designs" / record["design_id"]
            storage = {
                "storage_schema": STORAGE_SCHEMA,
                "display_name": display_name,
                "design_path": str(design_dir),
                "identity_source": "design_id",
            }
            if revision:
                source_root = Path(revision["source_path"])
                source_valid = (
                    admitted_source_units(record, source_root)
                    if args.trust_admission_hashes
                    else verified_source_units(record, source_root)
                )
                if not source_valid:
                    raise RuntimeError(f"immutable source verification failed for {record['design_id']}")
                verified += 1
                record.setdefault("source", {})["original_root"] = str(source_root)
                record["source"]["repository_revision_key"] = revision["repository_revision_key"]
                record["source"]["source_storage"] = "IMMUTABLE_REPOSITORY_REVISION"
                storage["repository_revision_key"] = revision["repository_revision_key"]
                storage["repository_source_path"] = str(source_root)
                linked += 1
            else:
                storage["legacy_original_root"] = record.get("source", {}).get("original_root")
                missing_revision += 1
                record.setdefault("provenance", {})["provenance_status"] = "LEGACY_PROVENANCE_UNRESOLVED"
                record.setdefault("source", {})["source_storage"] = "LEGACY_PROVENANCE_QUARANTINE"
                record.setdefault("release", {})["release_policy"] = "QUARANTINE"
                flags = record.setdefault("quality", {}).setdefault("quality_flags", [])
                if "LEGACY_PROVENANCE_UNRESOLVED" not in flags:
                    flags.append("LEGACY_PROVENANCE_UNRESOLVED")
                record["quality"]["training_tier"] = "TRAINING_AUXILIARY"
            record["storage"] = storage
            facts = Path(record.get("semantic_facts_path", ""))
            if facts.is_file():
                target = design_dir / "semantic_facts.json"
                if not args.dry_run:
                    atomic_json(target, json.loads(facts.read_text()))
                record["semantic_facts_path"] = str(target)
            archive_legacy_source = bool(args.archive_linked_legacy and revision and legacy_original.is_dir())
            archive_legacy_facts = bool(args.archive_linked_legacy and revision and legacy_facts.is_dir())
            legacy_sources_eligible += int(archive_legacy_source)
            legacy_facts_eligible += int(archive_legacy_facts)
            if not args.dry_run:
                atomic_json(design_dir / "design.json", record)
                if previous_design_path and previous_design_path != design_dir and previous_design_path.is_dir():
                    try:
                        previous_record = json.loads((previous_design_path / "design.json").read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        previous_record = {}
                    if previous_record.get("design_id") == record["design_id"]:
                        stale_catalogs_archived += int(archive_directory(
                            previous_design_path, args.corpus_root / "designs",
                            args.corpus_root / "quarantine" / "storage-layout-v1" / "designs",
                        ))
                if args.archive_linked_legacy and revision:
                    if archive_legacy_source:
                        legacy_sources_archived += int(archive_directory(
                            legacy_original, args.corpus_root / "original_rtl",
                            args.corpus_root / "quarantine" / "storage-layout-v1" / "original_rtl",
                        ))
                    if archive_legacy_facts:
                        legacy_facts_archived += int(archive_directory(
                            legacy_facts, args.corpus_root / "recovered_designs",
                            args.corpus_root / "quarantine" / "storage-layout-v1" / "recovered_designs",
                        ))
        if not args.dry_run:
            processor.validate_publish_invariants(args.corpus_root, designs)
            with CorpusState(args.corpus_root) as state:
                state.apply_incremental(designs=selected)
            processor.write_manifests(args.corpus_root, designs)
    summary = {
        "schema": STORAGE_SCHEMA, "designs": len(selected), "corpus_designs": len(designs),
        "selection_mode": "DESIGN_ID_FILE" if args.design_id_file else "FULL_CORPUS",
        "source_verification": "ADMISSION_HASH_REFERENCE" if args.trust_admission_hashes else "FULL_REHASH",
        "repository_revision_linked": linked,
        "repository_sources_verified": verified, "missing_repository_revision": missing_revision,
        "legacy_sources_eligible": legacy_sources_eligible, "legacy_facts_eligible": legacy_facts_eligible,
        "legacy_sources_archived": legacy_sources_archived, "legacy_facts_archived": legacy_facts_archived,
        "stale_catalogs_archived": stale_catalogs_archived, "migration_backup": str(backup_path) if backup_path else None,
        "dry_run": args.dry_run,
    }
    if not args.dry_run:
        atomic_json(args.corpus_root / "snapshots" / "storage_layout_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
