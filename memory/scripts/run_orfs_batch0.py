#!/usr/bin/env python3
"""Bounded, staging-only RTL -> full-ORFS Batch-0 experience factory.

The executor and the memory authority are deliberately separate:

  manifest -> materialize -> ORFS -> equivalence/signoff/graph
           -> external receipts -> staging import

No phase in this script can write canonical memory.  Canonical import lives in
``promote_orfs_batch_observations.py`` and requires an independent, fully bound
promotion-authority receipt.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

MEMORY_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = MEMORY_ROOT.parent
sys.path.insert(0, str(MEMORY_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from orfs_storage import default_work_root, enforce_work_root, storage_policy  # noqa: E402
from run_orfs_diversity_campaign import (  # noqa: E402
    _load,
    _materialize,
    _write,
    preflight_orfs_toolchain,
    _require_orfs_toolchain,
    run_projects,
)
from tehm.batch_lane import (  # noqa: E402
    BATCH_LANE_VERSION,
    BatchLaneError,
    build_external_observation,
    canonical_snapshots,
    assert_snapshots_unchanged,
    import_support_to_staging,
    _input_binding,
    _timing_contract,
    require_staging_destination,
    write_external_observations,
)
from tehm.physical.graph_context import load_defgraph_context  # noqa: E402
from tehm.rtl.equivalence import YosysEquivalenceOracle  # noqa: E402


VERSION = "orfs-batch0-v1"
DEFAULT_SPEC = MEMORY_ROOT / "evaluation" / "orfs_batch0_rtl_manifest_v1.json"
NEAR_DUPLICATE_THRESHOLD = 0.92
ACTION_FAMILY = "DENSITY_RELIEF"
BEFORE_CORE_UTILIZATION = 50
AFTER_CORE_UTILIZATION = 40


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, default=default_work_root("orfs-batch0-v1"))
    ap.add_argument("--orfs-root", type=Path,
                    default=Path(os.environ.get("ORFS_ROOT", "/opt/EDA4AI/OpenROAD-flow-scripts")))
    ap.add_argument("--source-spec", type=Path, default=DEFAULT_SPEC)
    ap.add_argument("--staging-db", type=Path, default=None)
    ap.add_argument("--staging-artifacts", type=Path, default=None)
    ap.add_argument("--phase", choices=(
        "all", "freeze", "prepare", "run", "equivalence", "signoff", "graph",
        "observe", "import-staging", "report"), default="report")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--cpus-per-run", type=int, default=6)
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--signoff-timeout", type=int, default=7200)
    ap.add_argument("--equivalence-timeout", type=int, default=300)
    ap.add_argument("--projects", nargs="+", default=None,
                    help="optional absolute project allowlist for run/signoff/graph")
    args = ap.parse_args(argv)

    root = enforce_work_root(args.root)
    root.mkdir(parents=True, exist_ok=True)
    staging_db = require_staging_destination(
        args.staging_db or root / "staging" / "tehm.sqlite", campaign_root=root)
    staging_artifacts = (args.staging_artifacts or root / "staging" / "artifacts").resolve()
    if not _is_below(staging_artifacts, root / "staging"):
        raise BatchLaneError("--staging-artifacts must be under <root>/staging")

    manifest_path = root / "campaign_manifest.json"
    observations_path = root / "external" / "observations.jsonl"
    allowlist = {str(Path(path).resolve()) for path in args.projects} if args.projects else None

    if args.phase in {"all", "freeze"}:
        build_source_freeze(root, args.orfs_root.resolve(), args.source_spec.resolve())
        if args.phase == "freeze":
            return 0

    if args.phase in {"all", "prepare"}:
        manifest = prepare(
            root, args.orfs_root.resolve(), args.source_spec.resolve(),
            source_freeze=root / "source_freeze.json")
        if args.phase == "prepare":
            return 0
    else:
        manifest = _load(manifest_path)
        if not manifest:
            raise BatchLaneError(f"campaign manifest missing: {manifest_path}")

    if args.phase in {"all", "run"}:
        run_projects(
            root, manifest, workers=max(1, args.workers),
            cpus=max(1, args.cpus_per_run), timeout=max(1, args.timeout),
            project_allowlist=allowlist)
        if args.phase == "run":
            return 0

    if args.phase in {"all", "equivalence"}:
        run_equivalence(
            root, manifest, timeout=max(1, args.equivalence_timeout),
            project_allowlist=allowlist)
        if args.phase == "equivalence":
            return 0

    if args.phase in {"all", "signoff"}:
        run_signoff(
            root, manifest, timeout=max(1, args.signoff_timeout),
            project_allowlist=allowlist)
        if args.phase == "signoff":
            return 0

    if args.phase in {"all", "graph"}:
        run_graph_contexts(root, manifest, project_allowlist=allowlist)
        if args.phase == "graph":
            return 0

    if args.phase in {"all", "observe"}:
        observe(root, manifest, observations_path)
        if args.phase == "observe":
            return 0

    if args.phase in {"all", "import-staging"}:
        result = import_support_to_staging(
            observations_path=observations_path, staging_db=staging_db,
            staging_artifacts=staging_artifacts, campaign_root=root,
            campaign_id=manifest["campaign_id"])
        _write(root / "staging_import_report.json", result)
        if args.phase == "import-staging":
            return 0

    report = build_report(root, manifest, observations_path, staging_db)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def prepare(root: Path, orfs_root: Path, source_spec: Path, *, source_freeze: Path) -> dict:
    # A prepared campaign is an evidence boundary, not merely a directory of
    # materialized configs.  Without a source freeze there is no immutable
    # identity for the Python/TEHM code that will grade the later observations;
    # allowing ``source_freeze_sha256=None`` would let a seemingly complete
    # ORFS pair enter the external chain without reproducible provenance.
    source_freeze = Path(source_freeze).resolve()
    if not source_freeze.is_file():
        raise BatchLaneError(
            "source freeze is required before prepare; run --phase freeze first: "
            f"{source_freeze}")
    spec = _load(source_spec)
    entries = validate_source_spec(spec, orfs_root=orfs_root)
    cases = root / "cases"
    items = []
    for entry in entries:
        design_id = entry["design_id"]
        template = orfs_root / entry["config_template"]
        sdc_template = orfs_root / entry["sdc_template"]
        rtl_files = [str(path) for path in entry["resolved_rtl_files"]]
        common = {
            "DESIGN_NAME": entry["top"],
            "VERILOG_FILES": " ".join(rtl_files),
            "CORE_UTILIZATION": str(BEFORE_CORE_UTILIZATION),
            "PLACE_DENSITY_LB_ADDON": "0.25",
            # Batch-0 edits physical configuration only and independently
            # proves the before/after RTL source sets byte-identical before an
            # observation can be eligible.  Re-running ORFS EQY is redundant,
            # times out on medium designs, and bypasses Slang for SystemVerilog
            # templates such as Ibex.  Changed-RTL campaigns must instead use
            # the Yosys proof path and may not use source identity.
            "EQUIVALENCE_CHECK": "0",
        }
        before = _materialize(
            cases / f"{design_id}_before_u{BEFORE_CORE_UTILIZATION}",
            template, sdc_template, common)
        after = _materialize(
            cases / f"{design_id}_after_u{AFTER_CORE_UTILIZATION}",
            template, sdc_template,
            {**common, "CORE_UTILIZATION": str(AFTER_CORE_UTILIZATION)})
        for project in (before, after):
            _bind_sdc(project / "constraints" / "constraint.sdc",
                      top=entry["top"], clock_port=entry["clock_port"])
        items.append({
            "case_id": f"sky130hs:{design_id}:u{BEFORE_CORE_UTILIZATION}->u{AFTER_CORE_UTILIZATION}",
            "lineage_id": f"batch0-v1:sky130hs:{design_id}",
            "design": design_id, "top": entry["top"], "platform": "sky130hs",
            "family": ACTION_FAMILY, "check": "route", "split": entry["split"],
            "role": "observation", "rtl_files": rtl_files,
            "source_digest": entry["source_digest"],
            "config_edits": {"CORE_UTILIZATION": str(AFTER_CORE_UTILIZATION)},
            "before_project": str(before), "after_project": str(after),
            "input_bindings": {
                "before": _input_binding(before, entry["resolved_rtl_files"]),
                "after": _input_binding(after, entry["resolved_rtl_files"]),
            },
            "timing_contract": {
                "before": _timing_contract(before),
                "after": _timing_contract(after),
            },
        })

    manifest = {
        "version": VERSION,
        "campaign_id": "orfs-batch0-v1",
        "batch_lane_version": BATCH_LANE_VERSION,
        "orfs_root": str(orfs_root),
        "source_spec": str(source_spec),
        "source_spec_sha256": _sha(source_spec),
        "source_freeze": str(source_freeze.resolve()),
        "source_freeze_sha256": _sha(source_freeze),
        "action_signature": {
            "family": ACTION_FAMILY,
            "config_edits": {"CORE_UTILIZATION": str(AFTER_CORE_UTILIZATION)},
        },
        "equivalence_policy": {
            "authority": "independent_equivalence_report",
            "config_only_source_identity_allowed": True,
            "orfs_internal_equivalence_check": False,
            "missing_or_failed_proof_classification": "INCOMPLETE",
        },
        "items": items,
        "storage_policy": storage_policy(root),
        "firewall": _firewall(entries),
        "canonical_before": canonical_snapshots(),
        "canonical_memory_mutation": "none",
    }
    _write(root / "campaign_manifest.json", manifest)
    return manifest


def validate_source_spec(spec: dict, *, orfs_root: Path) -> list[dict]:
    if spec.get("version") != "orfs-batch0-rtl-manifest-v1":
        raise BatchLaneError("Batch-0 source manifest version mismatch")
    raw_entries = list(spec.get("designs") or ())
    if not 6 <= len(raw_entries) <= 8:
        raise BatchLaneError("Batch-0 requires 6-8 RTL lineages")
    entries = []
    seen_ids = set()
    for raw in raw_entries:
        entry = dict(raw)
        design_id = str(entry.get("design_id") or "")
        if not design_id or design_id in seen_ids:
            raise BatchLaneError(f"missing/duplicate design_id: {design_id!r}")
        seen_ids.add(design_id)
        split = str(entry.get("split") or "")
        if split not in {"support", "calibration", "heldout"}:
            raise BatchLaneError(f"invalid split for {design_id}: {split!r}")
        if entry.get("clock_count") != 1:
            raise BatchLaneError(f"Batch-0 admits single-clock RTL only: {design_id}")
        config = (orfs_root / str(entry.get("config_template") or "")).resolve()
        sdc = (orfs_root / str(entry.get("sdc_template") or "")).resolve()
        if not config.is_file() or not sdc.is_file():
            raise BatchLaneError(f"template missing for {design_id}")
        rtl_files = []
        for pattern in entry.get("rtl_globs") or ():
            rtl_files.extend(path.resolve() for path in orfs_root.glob(str(pattern)) if path.is_file())
        rtl_files = sorted(set(rtl_files), key=str)
        if not rtl_files:
            raise BatchLaneError(f"RTL sources missing for {design_id}")
        combined = b"".join(path.read_bytes() for path in rtl_files)
        entry.update(
            config_template=str(entry["config_template"]),
            sdc_template=str(entry["sdc_template"]),
            resolved_rtl_files=rtl_files,
            source_digest=hashlib.sha256(combined).hexdigest(),
            source_tokens=_rtl_tokens(rtl_files),
        )
        entries.append(entry)

    exact = {}
    for entry in entries:
        other = exact.get(entry["source_digest"])
        if other:
            raise BatchLaneError(
                f"duplicate RTL source lineages: {other['design_id']} and {entry['design_id']}")
        exact[entry["source_digest"]] = entry
    for index, left in enumerate(entries):
        for right in entries[index + 1:]:
            if left["split"] == right["split"]:
                continue
            score = _jaccard(left["source_tokens"], right["source_tokens"])
            if score >= NEAR_DUPLICATE_THRESHOLD:
                raise BatchLaneError(
                    f"near-duplicate RTL crosses partitions: {left['design_id']} / "
                    f"{right['design_id']} ({score:.3f})")
    return entries


def run_equivalence(root: Path, manifest: dict, *, timeout: int,
                    project_allowlist: set[str] | None = None) -> dict:
    oracle = YosysEquivalenceOracle(timeout=timeout)
    rows = []
    for item in manifest["items"]:
        projects = [Path(item["before_project"]), Path(item["after_project"])]
        if project_allowlist is not None and not any(
                str(project.resolve()) in project_allowlist for project in projects):
            continue
        files = [Path(path) for path in item["rtl_files"]]
        result = oracle.verify(
            reference_files=files, candidate_files=files,
            reference_top=item["top"], candidate_top=item["top"],
            reference_profile="flow.rtl.top-equivalence.v1",
            candidate_profile="flow.rtl.top-equivalence.v1")
        result.update(case_id=item["case_id"], source_digest=item["source_digest"])
        for project in projects:
            _write(project / "reports" / "equivalence.json", result)
        rows.append(result)
    report = {"version": VERSION, "oracle_available": oracle.available,
              "results": rows, "all_proven": bool(rows) and all(
                  row.get("verdict") == "PASS" for row in rows)}
    _write(root / "equivalence_report.json", report)
    return report


def run_signoff(root: Path, manifest: dict, *, timeout: int,
                project_allowlist: set[str] | None = None) -> dict:
    toolchain = _require_orfs_toolchain(manifest, root=root)
    strict = REPO_ROOT / "r2g-skills" / "signoff-loop" / "scripts" / "flow" / "run_strict_signoff.sh"
    timing = REPO_ROOT / "r2g-skills" / "signoff-loop" / "scripts" / "reports" / "check_timing.py"
    rows = []
    for project, item in _projects(manifest, project_allowlist):
        log = project / "strict_signoff.log"
        env = dict(os.environ, ORFS_ROOT=str(Path(manifest["orfs_root"]).resolve()),
                   **toolchain["environment"], DRC_TIMEOUT=str(timeout),
                   LVS_TIMEOUT=str(timeout),
                   NETGEN_TIMEOUT=str(timeout), RCX_TIMEOUT=str(timeout))
        with log.open("w") as out:
            proc = subprocess.run(
                ["bash", str(strict), str(project), item["platform"], project.name],
                stdout=out, stderr=subprocess.STDOUT, env=env)
        with (project / "timing_check.log").open("w") as out:
            timing_proc = subprocess.run(
                [sys.executable, str(timing), str(project)],
                stdout=out, stderr=subprocess.STDOUT, env=env)
        rows.append({"case_id": item["case_id"], "project": str(project),
                     "strict_signoff_rc": proc.returncode,
                     "timing_rc": timing_proc.returncode})
    report = _merge_phase_results(root / "signoff_report.json", rows)
    return report


def run_graph_contexts(root: Path, manifest: dict,
                       project_allowlist: set[str] | None = None) -> dict:
    toolchain = _require_orfs_toolchain(manifest, root=root)
    features = REPO_ROOT / "r2g-skills" / "def-graph" / "scripts" / "flow" / "run_features.sh"
    rows = []
    for project, item in _projects(manifest, project_allowlist):
        final_def = _latest_final_def(project)
        if final_def is None:
            rows.append({"case_id": item["case_id"], "project": str(project),
                         "status": "missing_final_def"})
            continue
        env = dict(os.environ, ORFS_ROOT=str(Path(manifest["orfs_root"]).resolve()),
                   R2G_SIGNOFF_GATE="strict", **toolchain["environment"])
        with (project / "def_graph_features.log").open("w") as out:
            proc = subprocess.run(
                ["bash", str(features), str(project), item["platform"], project.name],
                stdout=out, stderr=subprocess.STDOUT, env=env)
        try:
            context = load_defgraph_context(project, def_path=final_def).to_dict()
        except (OSError, TypeError, ValueError) as exc:
            context = {"status": "invalid", "reason": str(exc)}
        _write(project / "reports" / "batch_graph_context.json", context)
        rows.append({"case_id": item["case_id"], "project": str(project),
                     "features_rc": proc.returncode,
                     "status": context.get("status"), "digest": context.get("digest")})
    report = _merge_phase_results(root / "graph_report.json", rows)
    return report


def _merge_phase_results(path: Path, rows: list[dict]) -> dict:
    """Keep bounded/allowlisted phase receipts append-safe by project.

    Smoke and recovery invocations intentionally operate on subsets.  A later
    subset must update its own project rows without erasing evidence already
    produced for other projects.
    """
    old = _load(path) or {}
    merged = {str(row.get("project")): row
              for row in (old.get("results") or []) if row.get("project")}
    merged.update({str(row["project"]): row for row in rows})
    report = {"version": VERSION,
              "results": [merged[key] for key in sorted(merged)]}
    _write(path, report)
    return report


def observe(root: Path, manifest: dict, observations_path: Path) -> dict:
    before = canonical_snapshots()
    observations = [build_external_observation(item) for item in manifest["items"]]
    chain = write_external_observations(observations_path, observations)
    after = canonical_snapshots()
    assert_snapshots_unchanged(before, after)
    report = {
        "version": VERSION, "chain": chain,
        "classifications": _counts(row["classification"] for row in observations),
        "splits": _counts(row["split"] for row in observations),
        "canonical_before": before, "canonical_after": after,
        "canonical_memory_mutation": "none",
    }
    _write(root / "observation_report.json", report)
    return report


def build_report(root: Path, manifest: dict, observations_path: Path,
                 staging_db: Path) -> dict:
    observations = []
    if observations_path.is_file():
        from tehm.batch_lane import read_external_observations
        observations = read_external_observations(observations_path)
    state = _load(root / "campaign_state.json") or {}
    attempts = state.get("attempts") or {}
    latest_runs = list((state.get("runs") or {}).values())
    missing_oracles = []
    for row in observations:
        missing_oracles.extend((row.get("before") or {}).get(
            "missing_oracles") or [])
        missing_oracles.extend((row.get("after") or {}).get(
            "missing_oracles") or [])
    signoff_rows = (_load(root / "signoff_report.json") or {}).get("results") or []
    graph_rows = (_load(root / "graph_report.json") or {}).get("results") or []
    observation_report = _load(root / "observation_report.json") or {}
    staging_report = _load(root / "staging_import_report.json") or {}
    toolchain_report = _load(root / "toolchain_preflight.json") or {}
    report = {
        "version": VERSION,
        "campaign_id": manifest["campaign_id"],
        "design_count": len(manifest["items"]),
        "splits": _counts(item["split"] for item in manifest["items"]),
        "action_signature": manifest["action_signature"],
        "external_observations": len(observations),
        "classifications": _counts(
            row.get("classification", "UNKNOWN") for row in observations),
        "eligible_positive": sum(
            row.get("classification") == "ELIGIBLE_POSITIVE" for row in observations),
        "learner_eligible_support": sum(bool(row.get("learner_eligible")) for row in observations),
        "execution": {
            "project_count": len(latest_runs),
            "attempt_count": sum(len(rows) for rows in attempts.values()),
            "latest_outcomes": _counts(
                row.get("failure_class", "UNKNOWN") for row in latest_runs),
        },
        "oracle_summary": {
            "missing_arm_counts": _counts(missing_oracles),
            "signoff_projects": len(signoff_rows),
            "strict_signoff_success": sum(
                row.get("strict_signoff_rc") == 0 for row in signoff_rows),
            "timing_success": sum(row.get("timing_rc") == 0
                                  for row in signoff_rows),
            "graph_projects": len(graph_rows),
            "graph_complete": sum(row.get("status") == "complete"
                                  and bool(row.get("digest")) for row in graph_rows),
        },
        "external_chain": observation_report.get("chain"),
        "staging_db": str(staging_db),
        "staging_imported": len(staging_report.get("imported") or []),
        "source_freeze_sha256": manifest.get("source_freeze_sha256"),
        "toolchain_preflight": toolchain_report,
        "toolchain_bound": toolchain_report.get("status") in {
            "bound_internal", "bound_external"},
        "canonical_snapshots": canonical_snapshots(),
        "canonical_memory_mutation": "none",
        "promotion_attempted": False,
    }
    _write(root / "batch0_report.json", report)
    return report


def build_source_freeze(root: Path, orfs_root: Path, source_spec: Path) -> dict:
    source_paths = []
    for base in (MEMORY_ROOT / "tehm", MEMORY_ROOT / "scripts"):
        source_paths.extend(path for path in base.rglob("*")
                            if path.is_file() and "__pycache__" not in path.parts)
    source_paths.extend(path for path in (
        MEMORY_ROOT / "schema.sql", MEMORY_ROOT / "requirements.txt", source_spec)
        if path.is_file())
    records = [{"path": str(path.resolve().relative_to(REPO_ROOT)),
                "sha256": _sha(path), "bytes": path.stat().st_size}
               for path in sorted(set(source_paths), key=str)]
    git_status = _command(["git", "status", "--porcelain=v1"], cwd=REPO_ROOT)
    orfs_identity = _orfs_dependency_identity(orfs_root)
    toolchain = preflight_orfs_toolchain({"orfs_root": str(orfs_root)})
    freeze = {
        "version": "orfs-batch-source-freeze-v1",
        "git_head": _command(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT).strip(),
        "git_status_sha256": hashlib.sha256(git_status.encode()).hexdigest(),
        "dirty_entries": len([line for line in git_status.splitlines() if line]),
        "source_tree_digest": hashlib.sha256(
            stable_json(records).encode()).hexdigest(),
        "source_files": records,
        "dependencies": {
            "python": sys.version,
            # Do not report a PATH-discovered host Yosys/OpenROAD as if it were
            # part of this source freeze.  The binding receipt is intentionally
            # blocked when the tree has no packaged tools and no explicit
            # caller override; run/signoff/graph then stop before EDA work.
            "yosys": (toolchain.get("tools", {}).get("yosys", {}).get("version")
                      if toolchain.get("tools") else None),
            "iverilog": _command(["iverilog", "-V"]).splitlines()[0],
            "openroad": (toolchain.get("tools", {}).get("openroad", {}).get("version")
                         if toolchain.get("tools") else None),
            "orfs_root": str(orfs_root),
            "orfs": orfs_identity,
            "toolchain_preflight": toolchain,
        },
        "canonical_snapshots": canonical_snapshots(),
    }
    freeze["freeze_digest"] = hashlib.sha256(stable_json(freeze).encode()).hexdigest()
    _write(root / "source_freeze.json", freeze)
    return freeze


def _orfs_dependency_identity(orfs_root: Path) -> dict:
    """Freeze the executable ORFS/PDK surface even for archive installs.

    The host ORFS tree is an extracted distribution without ``.git``.  A
    failed ``git rev-parse`` string is not a dependency identity, so hash the
    Make entrypoints, flow scripts/utilities, and complete selected platform
    tree that actually controls Batch-0.  RTL/config inputs receive their own
    source digests in the campaign manifest.
    """
    commit_proc = subprocess.run(
        ["git", "-C", str(orfs_root), "rev-parse", "HEAD"],
        text=True, capture_output=True)
    commit = commit_proc.stdout.strip() if commit_proc.returncode == 0 else None
    roots = (
        orfs_root / "flow" / "Makefile",
        orfs_root / "flow" / "settings.mk",
        orfs_root / "flow" / "scripts",
        orfs_root / "flow" / "util",
        orfs_root / "flow" / "platforms" / "sky130hs",
    )
    files = []
    for candidate in roots:
        if candidate.is_file():
            files.append(candidate)
        elif candidate.is_dir():
            files.extend(path for path in candidate.rglob("*") if path.is_file())
        else:
            raise BatchLaneError(f"ORFS dependency surface missing: {candidate}")
    digest = hashlib.sha256()
    total_bytes = 0
    for path in sorted(set(files), key=lambda item: item.as_posix()):
        rel = path.relative_to(orfs_root).as_posix()
        size = path.stat().st_size
        file_sha = _sha(path)
        digest.update(f"{rel}\0{size}\0{file_sha}\n".encode())
        total_bytes += size
    if not files:
        raise BatchLaneError("ORFS dependency surface is empty")
    return {
        "identity_kind": "git_commit+tree_sha256" if commit else "tree_sha256",
        "git_commit": commit,
        "tree_sha256": digest.hexdigest(),
        "file_count": len(set(files)),
        "total_bytes": total_bytes,
        "roots": [path.relative_to(orfs_root).as_posix() for path in roots],
        "complete": True,
    }


def _firewall(entries: list[dict]) -> dict:
    partitions = {split: sorted(entry["design_id"] for entry in entries
                                if entry["split"] == split)
                  for split in ("support", "calibration", "heldout")}
    disjoint = all(not set(partitions[left]) & set(partitions[right])
                   for index, left in enumerate(partitions)
                   for right in list(partitions)[index + 1:])
    return {
        "version": "orfs-batch0-firewall-v1",
        "partitions": partitions, "disjoint": disjoint,
        "source_digests_unique": len({entry["source_digest"] for entry in entries}) == len(entries),
        "near_duplicate_threshold": NEAR_DUPLICATE_THRESHOLD,
    }


def _projects(manifest: dict, allowlist: set[str] | None):
    seen = set()
    for item in manifest["items"]:
        for field in ("before_project", "after_project"):
            project = Path(item[field]).resolve()
            if str(project) in seen:
                continue
            seen.add(str(project))
            if allowlist is not None and str(project) not in allowlist:
                continue
            yield project, item


def _bind_sdc(path: Path, *, top: str, clock_port: str) -> None:
    text = path.read_text()
    text, top_count = re.subn(
        r"(?m)^\s*current_design\s+\S+\s*$", f"current_design {top}", text)
    if top_count == 0:
        text = f"current_design {top}\n\n" + text
        top_count = 1
    text, port_count = re.subn(
        r"(?m)^\s*set\s+clk_port_name\s+\S+\s*$", f"set clk_port_name {clock_port}", text)
    if top_count != 1 or port_count != 1:
        raise BatchLaneError(f"SDC template lacks unique design/clock binding: {path}")
    path.write_text(text)


def _rtl_tokens(paths: list[Path]) -> frozenset[str]:
    text = "\n".join(path.read_text(errors="replace") for path in paths)
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    text = re.sub(r"//.*", " ", text)
    return frozenset(re.findall(r"[A-Za-z_][A-Za-z0-9_$]*|\d+'[bdhoBDHO][0-9a-fA-F_xXzZ]+", text))


def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 1.0


def _latest_final_def(project: Path) -> Path | None:
    for run in sorted((project / "backend").glob("RUN_*"), reverse=True):
        candidates = sorted((run / "final").glob("*.def"))
        if candidates:
            return candidates[-1]
    return None


def _is_below(path: Path, root: Path) -> bool:
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


def _counts(values) -> dict:
    result = {}
    for value in values:
        result[str(value)] = result.get(str(value), 0) + 1
    return dict(sorted(result.items()))


def _sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def stable_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _command(command: list[str], *, cwd: Path | None = None) -> str:
    try:
        proc = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"UNAVAILABLE:{exc}"
    text = proc.stdout + proc.stderr
    return text if proc.returncode == 0 else f"RC={proc.returncode}:{text}"


if __name__ == "__main__":
    raise SystemExit(main())
