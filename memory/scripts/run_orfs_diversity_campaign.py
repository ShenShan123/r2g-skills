#!/usr/bin/env python3
"""Production ORFS diversity campaign for TEHM physical/action transfer.

Adds ordinary training transitions across two action families and two platforms,
while materializing a truly unseen SPI lineage on a third platform only as an
A/B subject (H9).
The script is resumable: a project is reused only when its config digest and
preserved production run evidence agree.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import os
import re
import signal
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

MEMORY_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = MEMORY_ROOT.parent
sys.path.insert(0, str(MEMORY_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tehm import db as tehm_db  # noqa: E402
from tehm.dataset import SPLITS  # noqa: E402
from tehm.adapters.orfs_pair import build_orfs_pair_record  # noqa: E402
from tehm.artifact_store import ArtifactStore  # noqa: E402
from tehm.canonical.capture import capture  # noqa: E402
from tehm.evaluation.campaign_metrics import evaluate_campaign, to_markdown  # noqa: E402
from tehm.physical.graph_context import load_defgraph_context  # noqa: E402
from tehm.physical.memory import PhysicalEffectMemory  # noqa: E402
from tehm.physical.orfs_preflight import (  # noqa: E402
    inspect_routing_layer_adjustment, inspect_signoff_platform_scope,
    parse_orfs_config, preflight_digest)
from tehm_backend import TehmMemoryBackend  # noqa: E402
from tehm.batch_lane import (  # noqa: E402
    BatchLaneError,
    _input_binding,
    _input_binding_matches,
    _timing_contract,
    _timing_contract_matches,
    assess_full_oracle,
    require_staging_destination,
)
from orfs_storage import default_work_root, enforce_work_root, storage_policy  # noqa: E402

VERSION = "orfs-diversity-v0.3"
SUPERVISOR_GRACE_S = 90
_CFG_RE = re.compile(r"^(\s*(?:export\s+)?)([A-Z0-9_]+)(\s*[:?]?=\s*).*$")
MATRIX = (
    ("sky130hs", "gcd", "DENSITY_RELIEF", "CORE_UTILIZATION", "70", "35"),
    ("sky130hs", "aes", "DENSITY_RELIEF", "CORE_UTILIZATION", "70", "35"),
    ("gf180", "jpeg", "DENSITY_RELIEF", "CORE_UTILIZATION", "70", "35"),
    ("gf180", "riscv32i", "DENSITY_RELIEF", "CORE_UTILIZATION", "70", "35"),
    ("sky130hs", "gcd", "ROUTING_CAPACITY_RECOVERY",
     "ROUTING_LAYER_ADJUSTMENT", "0.55", "0.15"),
    ("sky130hs", "aes", "ROUTING_CAPACITY_RECOVERY",
     "ROUTING_LAYER_ADJUSTMENT", "0.55", "0.15"),
    ("gf180", "jpeg", "ROUTING_CAPACITY_RECOVERY",
     "ROUTING_LAYER_ADJUSTMENT", "0.55", "0.15"),
    ("gf180", "riscv32i", "ROUTING_CAPACITY_RECOVERY",
     "ROUTING_LAYER_ADJUSTMENT", "0.55", "0.15"),
)

TOOLCHAIN_PREFLIGHT_VERSION = "orfs-toolchain-preflight-v1"
PLATFORM_SCOPE_PREFLIGHT_VERSION = "orfs-signoff-platform-scope-v1"


def preflight_orfs_platform_scope(manifest: dict) -> dict:
    """Resolve the signoff wrapper's product-scope platform policy.

    ``run_orfs.sh`` has an explicit unsupported-platform guard, but launching
    the wrapper first turns that deterministic policy decision into a generic
    ``FLOW_FAILURE`` receipt after an executor has already been scheduled.  A
    campaign must bind the same policy before any EDA process starts.  The
    capability table is loaded from the tracked signoff-loop source of truth;
    its digest is persisted so a later replay can prove which policy was used.

    This is intentionally a *scope* check, not a claim that an in-scope
    platform has complete DRC/LVS capability.  The strict signoff phase still
    owns those per-platform oracle checks.  The check also ignores the
    wrapper's ``R2G_ALLOW_UNSUPPORTED_PLATFORM`` escape hatch: that variable is
    for deliberate diagnostic experiments and cannot silently turn an
    unsupported platform into production evidence.
    """
    platforms = [item.get("platform") for item in manifest.get("items", [])]
    report = inspect_signoff_platform_scope(platforms)
    report["version"] = PLATFORM_SCOPE_PREFLIGHT_VERSION
    report["orfs_root"] = str(Path(manifest.get("orfs_root") or "").resolve())
    return report


def _require_orfs_platform_scope(manifest: dict, *, root: Path) -> dict:
    report = preflight_orfs_platform_scope(manifest)
    _write(root / "platform_scope_preflight.json", report)
    if report.get("status") != "pass":
        raise BatchLaneError(
            "ORFS signoff platform preflight blocked before EDA execution: "
            + str(report.get("error") or "platform scope is unavailable"))
    return report


def preflight_orfs_toolchain(manifest: dict, *, env: dict | None = None) -> dict:
    """Resolve the ORFS executor without allowing a silent host fallback.

    ``_env.sh`` intentionally has a broad discovery fallback for interactive
    use.  A campaign receipt cannot use that policy: a source freeze rooted at
    one ORFS tree must not silently execute another tree's host binaries.  The
    campaign therefore accepts only a binary packaged below ``ORFS_ROOT`` or
    an explicit caller override (``OPENROAD_EXE``/``YOSYS_EXE``).  External
    overrides remain auditable and are marked as such; their semantic
    compatibility is not inferred by this preflight.
    """
    supplied = dict(os.environ if env is None else env)
    raw_root = manifest.get("orfs_root")
    root = Path(str(raw_root)).expanduser().resolve() if raw_root else Path()
    report = {
        "version": TOOLCHAIN_PREFLIGHT_VERSION,
        "orfs_root": str(root),
        "status": "blocked",
        "tools": {},
        "environment": {},
        "reasons": [],
    }
    if not raw_root or not (root / "flow" / "Makefile").is_file():
        report["reasons"].append(
            f"ORFS_ROOT is missing or has no flow/Makefile: {root}")
        report["error"] = "; ".join(report["reasons"])
        return report

    specs = {
        "openroad": {
            "variable": "OPENROAD_EXE",
            "switch": "-version",
            "packaged": (
                root / "tools" / "install" / "OpenROAD" / "bin" / "openroad",
                root / "tools" / "install" / "openroad" / "bin" / "openroad",
            ),
        },
        "yosys": {
            "variable": "YOSYS_EXE",
            "switch": "-V",
            "packaged": (
                root / "tools" / "install" / "yosys" / "bin" / "yosys",
                root / "tools" / "install" / "Yosys" / "bin" / "yosys",
            ),
        },
    }
    external = False
    for name, spec in specs.items():
        variable = spec["variable"]
        configured = str(supplied.get(variable) or "").strip()
        candidate = None
        source = None
        if configured:
            # Support both an absolute path and an explicitly named executable
            # on PATH, but never search PATH when the variable is absent.
            candidate = (Path(configured).expanduser()
                         if ("/" in configured or configured.startswith("."))
                         else Path(shutil.which(configured) or configured))
            candidate = candidate.resolve()
            source = "explicit_override"
            external = True
            if not (candidate.is_file() and candidate.stat().st_mode & 0o111):
                report["reasons"].append(
                    f"{variable} does not name an executable: {configured}")
                candidate = None
        else:
            # A symlink placed below ``tools/install`` must not turn a host
            # executable into an apparently tree-packaged tool.  Resolve the
            # candidate first and require the actual file to remain below the
            # frozen ORFS root.  Symlinks to a binary built inside this tree
            # remain valid; links escaping to /usr/bin, /opt, or another
            # campaign are rejected before any EDA process starts.
            escaped = []
            candidate = None
            for path in spec["packaged"]:
                if not path.exists():
                    continue
                resolved = path.resolve()
                try:
                    resolved.relative_to(root)
                except ValueError:
                    escaped.append(f"{path} -> {resolved}")
                    continue
                if resolved.is_file() and resolved.stat().st_mode & 0o111:
                    candidate = resolved
                    break
            if candidate is not None:
                source = "orfs_packaged"
            else:
                if escaped:
                    report["reasons"].append(
                        f"{root} packaged {name} escapes ORFS_ROOT: "
                        + ", ".join(escaped))
                else:
                    report["reasons"].append(
                        f"{root} has no packaged {name}; set {variable} explicitly")

        tool = {"variable": variable, "path": str(candidate) if candidate else None,
                "source": source, "sha256": _sha(candidate) if candidate else None}
        if candidate:
            tool["version"] = _tool_version(candidate, spec["switch"])
            report["environment"][variable] = str(candidate)
            if name == "yosys":
                capability = _probe_yosys_capabilities(
                    candidate, orfs_root=root,
                    version=tool["version"])
                tool["capabilities"] = capability
                if capability["status"] == "FAIL":
                    report["reasons"].append(
                        f"{variable} is incompatible with this ORFS flow: "
                        + str(capability["reason"]))
        report["tools"][name] = tool

    if not report["reasons"]:
        report["status"] = "bound_external" if external else "bound_internal"
        report["compatibility"] = (
            "operator_bound_unverified" if external else "tree_packaged")
        report["fingerprint"] = hashlib.sha256(
            json.dumps({"orfs_root": str(root), "tools": report["tools"]},
                       sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    else:
        report["error"] = "; ".join(report["reasons"])
    return report


def _tool_version(path: Path, switch: str) -> str:
    try:
        proc = subprocess.run([str(path), switch], capture_output=True,
                              text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"UNAVAILABLE:{exc}"
    output = (proc.stdout + proc.stderr).strip()
    if proc.returncode != 0:
        return f"RC={proc.returncode}:{output}"
    return output[:4096]


def _probe_yosys_capabilities(path: Path, *, orfs_root: Path,
                              version: str) -> dict:
    """Fail closed when a real Yosys lacks options used by this ORFS tree.

    A binary existing and returning ``-V`` is not enough for evidence.  The
    current ORFS canonicalization script invokes ``read_liberty -unit_delay``;
    Yosys 0.9 accepts the executable but rejects that option only after a flow
    has started.  Probe the command's help surface before any EDA work.  Fake
    test shims with non-semver version text remain ``UNKNOWN`` so unit tests
    can exercise path binding without pretending to prove compatibility.
    """
    result = {"status": "UNKNOWN", "required": [], "supported": []}
    if not re.match(r"^Yosys\s+\d+\.\d+", str(version)):
        result["reason"] = "version format is not a real Yosys release"
        return result
    canonicalize = orfs_root / "flow" / "scripts" / "synth_canonicalize.tcl"
    if not canonicalize.is_file():
        result["reason"] = "synth_canonicalize.tcl is missing"
        return result
    required = ["read_liberty -unit_delay"]
    result["required"] = required
    try:
        proc = subprocess.run(
            [str(path), "-p", "help read_liberty"],
            capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        result["status"] = "FAIL"
        result["reason"] = f"capability probe failed: {exc}"
        return result
    output = proc.stdout + proc.stderr
    if proc.returncode != 0:
        result["status"] = "FAIL"
        result["reason"] = f"capability probe rc={proc.returncode}"
        return result
    supported = [item for item in required if "-unit_delay" in output]
    result["supported"] = supported
    if len(supported) != len(required):
        result["status"] = "FAIL"
        result["reason"] = "missing read_liberty -unit_delay"
    else:
        result["status"] = "PASS"
    return result


def _require_orfs_toolchain(manifest: dict, *, root: Path) -> dict:
    binding = preflight_orfs_toolchain(manifest)
    _write(root / "toolchain_preflight.json", binding)
    if binding.get("status") not in {"bound_internal", "bound_external"}:
        raise BatchLaneError(
            "ORFS toolchain preflight blocked before EDA execution: "
            + str(binding.get("error") or "missing toolchain binding"))
    return binding


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path,
                    default=default_work_root("orfs-v2-diversity"))
    ap.add_argument("--orfs-root", type=Path,
                    default=Path(os.environ.get("ORFS_ROOT", "/opt/EDA4AI/OpenROAD-flow-scripts")))
    ap.add_argument("--staging-db", type=Path, default=None)
    ap.add_argument("--staging-artifacts", type=Path, default=None)
    ap.add_argument("--phase", choices=("all", "prepare", "heldout", "run", "capture", "graph", "ab", "predict", "report"),
                    default="all")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--cpus-per-run", type=int, default=8)
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--supervisor-grace", type=int, default=SUPERVISOR_GRACE_S,
                    help="seconds after ORFS_TIMEOUT before killing a hung wrapper")
    ap.add_argument("--projects", nargs="+", default=None,
                    help="optional absolute project paths to run; all manifest projects otherwise")
    ap.add_argument("--ab-repeats", type=int, default=2)
    args = ap.parse_args(argv)
    root = enforce_work_root(args.root)
    staging_db = require_staging_destination(
        args.staging_db or root / "staging" / "tehm.sqlite", campaign_root=root)
    staging_artifacts = (args.staging_artifacts or root / "staging" / "artifacts").resolve()
    for sub in (root, root / "cases", root / "heldout", root / "tehm_ab"):
        sub.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "campaign_manifest.json"

    if args.phase in ("all", "prepare"):
        manifest = prepare(root, args.orfs_root.resolve())
    else:
        manifest = _load(manifest_path)
        if not manifest:
            raise RuntimeError(f"manifest missing: {manifest_path}")
    if args.phase == "prepare":
        return 0
    if args.phase == "heldout":
        refresh_heldout(root, args.orfs_root.resolve(), manifest)
        return 0
    if args.phase in ("all", "run"):
        run_projects(root, manifest, workers=max(1, args.workers),
                     cpus=max(1, args.cpus_per_run), timeout=args.timeout,
                     supervisor_grace=max(1, args.supervisor_grace),
                     project_allowlist={str(Path(p).resolve()) for p in args.projects}
                     if args.projects else None)
    if args.phase == "run":
        return 0
    if args.phase in ("all", "capture"):
        capture_pairs(manifest_path, manifest, staging_db, staging_artifacts)
        manifest = _load(manifest_path)
    if args.phase == "capture":
        return 0
    if args.phase in ("all", "graph"):
        attach_graph_contexts(root, manifest, staging_db)
    if args.phase == "graph":
        return 0
    if args.phase in ("all", "ab"):
        run_ab(root, manifest, staging_db, staging_artifacts,
               repeats=max(1, args.ab_repeats), cpus=max(1, args.cpus_per_run),
               timeout=args.timeout)
    if args.phase == "ab":
        return 0
    if args.phase in ("all", "predict"):
        evaluate_physical_retrieval(root, manifest, staging_db)
    if args.phase == "predict":
        return 0
    report(root, manifest, staging_db)
    return 0


def prepare(root: Path, orfs_root: Path) -> dict:
    items = []
    for platform, design, family, knob, before_value, after_value in MATRIX:
        template = orfs_root / "flow" / "designs" / platform / design
        cfg, sdc = template / "config.mk", template / "constraint.sdc"
        if not cfg.is_file() or not sdc.is_file():
            raise FileNotFoundError(f"ORFS template incomplete: {template}")
        slug = f"{platform}_{design}_{family.lower()}"
        common = {"PLACE_DENSITY_LB_ADDON": "0.25"}
        # The installed OpenROAD predates exclude_io_pin_region, which appears
        # in the stock gf180 example io.tcl files.  Emptying IO_CONSTRAINTS uses
        # ORFS's ordinary pin-placement path for both A/B arms and avoids
        # turning a tool/flow version mismatch into a learned design outcome.
        if platform == "gf180":
            common["IO_CONSTRAINTS"] = ""
        if family == "ROUTING_CAPACITY_RECOVERY":
            common["CORE_UTILIZATION"] = "65"
        before = _materialize(root / "cases" / f"{slug}_before", cfg, sdc,
                              {**common, knob: before_value})
        after = _materialize(root / "cases" / f"{slug}_after", cfg, sdc,
                             {**common, knob: after_value})
        items.append({
            "case_id": f"{platform}:{design}:{family}:{before_value}->{after_value}",
            "lineage_id": f"orfs-v2:{design}", "platform": platform,
            "design": design, "family": family, "check": "route",
            "knob": knob, "before_value": before_value, "after_value": after_value,
            "config_edits": {knob: after_value},
            "before_project": str(before), "after_project": str(after),
            "role": "training",
        })

    manifest = {
        "campaign_version": VERSION, "orfs_root": str(orfs_root),
        "items": items, "transition_target": len(items),
        "storage_policy": storage_policy(root),
        "firewall": {"training_lineages": sorted({x["lineage_id"] for x in items}),
                     "heldout_lineages": [], "disjoint": True},
    }
    return refresh_heldout(root, orfs_root, manifest)


def refresh_heldout(root: Path, orfs_root: Path, manifest: dict) -> dict:
    """Refresh only H9's held-out subject without touching captured training data."""
    # SPI appears nowhere in MATRIX.  IHP-SG13G2 is also absent from training,
    # giving the production A/B a simultaneously unseen RTL and platform.  The
    # high-utilization baseline makes the promoted 20% density action material.
    template = orfs_root / "flow" / "designs" / "ihp-sg13g2" / "spi"
    heldout = _materialize(root / "heldout" / "ihp_sg13g2_spi_u70",
                           template / "config.mk", template / "constraint.sdc",
                           {"CORE_UTILIZATION": "70"})
    lineage = "orfs-heldout:spi"
    training = sorted({x["lineage_id"] for x in manifest["items"]})
    if lineage in training:
        raise RuntimeError(f"held-out lineage leaked into training: {lineage}")
    manifest["campaign_version"] = VERSION
    manifest["orfs_root"] = str(orfs_root)
    manifest["heldout"] = {
        "lineage_id": lineage, "platform": "ihp-sg13g2", "design": "spi",
        "project_path": str(heldout), "baseline_core_utilization": 70,
        "role": "heldout_ab_only", "capturable": False,
    }
    manifest["firewall"] = {
        "training_lineages": training, "heldout_lineages": [lineage],
        "disjoint": set(training).isdisjoint({lineage}),
    }
    if not manifest["firewall"]["disjoint"]:
        raise RuntimeError("H9 held-out firewall is not disjoint")
    _write(root / "campaign_manifest.json", manifest)
    return manifest


def _materialize(project: Path, cfg_template: Path, sdc_template: Path,
                 edits: dict[str, str]) -> Path:
    for name in ("constraints", "rtl", "reports", "backend", "drc", "lvs", "rcx"):
        (project / name).mkdir(parents=True, exist_ok=True)
    sdc = project / "constraints" / "constraint.sdc"
    sdc.write_bytes(sdc_template.read_bytes())
    all_edits = {**edits, "SDC_FILE": str(sdc.resolve())}
    (project / "constraints" / "config.mk").write_text(
        _apply_edits(cfg_template.read_text(), all_edits))
    return project.resolve()


def _workspace_key(project: Path, platform: str) -> tuple[str, str]:
    """Return the workspace identity protected by ``run_orfs.sh``.

    ``run_orfs.sh`` includes the generated flow variant in its fd lock, but
    all variants still share the logical ORFS design tree and tool inputs.
    Resolve that logical identity from the materialized config so a campaign
    serializes same-design variants conservatively while keeping unrelated
    designs parallel.  Fall back to the project name for an old/incomplete
    project so the scheduler never silently drops serialization.
    """
    values: dict[str, str] = {}
    try:
        config = (Path(project) / "constraints" / "config.mk").read_text(
            errors="replace")
    except OSError:
        config = ""
    for line in config.splitlines():
        match = re.match(
            r"^\s*(?:export\s+)?([A-Z0-9_]+)\s*[:?]?=\s*(.*?)\s*$",
            line)
        if match:
            values[match.group(1)] = match.group(2).strip()
    # run_orfs.sh keys results and its lock by DESIGN_NICKNAME, falling back
    # to DESIGN_NAME for legacy configs; mirror that precedence exactly.
    design = values.get("DESIGN_NICKNAME") or values.get("DESIGN_NAME")
    return str(platform), str(design or Path(project).name)


def _apply_edits(text: str, edits: dict[str, str | None]) -> str:
    pending, lines = dict(edits), []
    skip_replaced_continuation = False
    for line in text.splitlines():
        if skip_replaced_continuation:
            skip_replaced_continuation = line.rstrip().endswith("\\")
            continue
        match = _CFG_RE.match(line)
        if match and match.group(2) in pending:
            key = match.group(2)
            value = pending.pop(key)
            skip_replaced_continuation = line.rstrip().endswith("\\")
            if value is not None:
                lines.append(f"{match.group(1)}{key}{match.group(3)}{value}")
            # value is None => drop the assignment line entirely (e.g. so a
            # PLACE_DENSITY knob takes precedence over PLACE_DENSITY_LB_ADDON).
        else:
            lines.append(line)
    lines.extend(f"export {key} = {value}"
                 for key, value in sorted(pending.items()) if value is not None)
    return "\n".join(lines) + "\n"


def run_projects(root: Path, manifest: dict, *, workers: int, cpus: int,
                 timeout: int, supervisor_grace: int = SUPERVISOR_GRACE_S,
                 project_allowlist: set[str] | None = None) -> None:
    _require_orfs_platform_scope(manifest, root=root)
    toolchain = _require_orfs_toolchain(manifest, root=root)
    tool_env = toolchain["environment"]
    state_path = root / "campaign_state.json"
    state = _load(state_path) or {"version": VERSION, "runs": {}}
    state.setdefault("version", VERSION)
    state.setdefault("runs", {})
    # ``runs`` remains the latest attempt for compatibility with older
    # campaign consumers. ``attempts`` is append-only per project and is the
    # audit source for retries after timeout/interruption.
    state.setdefault("attempts", {})
    projects = {(item[side], item["platform"])
                for item in manifest["items"] for side in ("before_project", "after_project")}
    if project_allowlist is not None:
        projects = {(project, platform) for project, platform in projects
                    if str(Path(project).resolve()) in project_allowlist}
    expected_failures = {
        str(Path(item["before_project"]))
        for item in manifest["items"]
        if item.get("role") == "routing_positive_stress"
    }
    lock = threading.Lock()
    # Even though run_orfs.sh's fd lock includes FLOW_VARIANT, before/after
    # variants share the logical ORFS design tree and tool inputs.  A manifest
    # legitimately contains both arms of one design, so keep those arms
    # serialized while allowing unrelated designs to run in parallel.  This
    # avoids contaminating evidence with scheduler-induced FLOW_FAILUREs.
    workspace_locks: dict[tuple[str, str], threading.Lock] = {}
    workspace_locks_guard = threading.Lock()
    runner = REPO_ROOT / "r2g-skills/signoff-loop/scripts/flow/run_orfs.sh"
    extract_route = REPO_ROOT / "r2g-skills/signoff-loop/scripts/extract/extract_route.py"
    extract_ppa = REPO_ROOT / "r2g-skills/signoff-loop/scripts/extract/extract_ppa.py"

    def one(entry):
        project_text, platform = entry
        project = Path(project_text)
        digest = _sha(project / "constraints" / "config.mk")
        old = state["runs"].get(str(project), {})
        # Legacy states used ``completed=true`` for timeout/interrupted runs.
        # A cached success is admissible only when the receipt itself says rc=0
        # (or this is a new-format SUCCESS record) and a frozen final DEF exists.
        if _reusable_success(old, digest, project):
            return project, old
        if (str(project) in expected_failures and
                old.get("config_sha256") == digest and
                isinstance(old.get("flow_rc"), int) and old["flow_rc"] != 0 and
                Path(old.get("log") or "").is_file()):
            # A deliberately under-provisioned source is evidence for the
            # fail->pass pair, not a successful reusable DEF.  Preserve and
            # reuse that failure receipt without weakening _has_run for normal
            # projects.
            return project, old
        log = project / "campaign_flow.log"
        # Bind the executor to the exact ORFS tree frozen in the manifest.
        # Without this explicit export, _env.sh may silently select a host
        # installation (for example /opt/EDA4AI) while source/config digests
        # were prepared from --orfs-root, invalidating tool provenance.
        env = dict(os.environ, ORFS_ROOT=str(Path(manifest["orfs_root"]).resolve()),
                   ORFS_TIMEOUT=str(timeout), ORFS_MAX_CPUS=str(cpus), **tool_env)
        previous_checkpoint = _stage_checkpoint(project)
        # A changed frontend/toolchain must never reuse artifacts from the
        # previous failed attempt.  Operators can request an auditable clean
        # rebuild without deleting append-only attempt history.
        force_clean_run = os.environ.get("R2G_FORCE_CLEAN_RUN") == "1"
        resume_from = (None if force_clean_run else
                       _resume_stage(previous_checkpoint, project=project))
        if resume_from:
            # Preserve successful upstream artifacts and restart only the last
            # incomplete stage. This is a crash-recovery operation, not a new
            # training observation; the receipt records the source checkpoint.
            env.update(FROM_STAGE=resume_from, R2G_RESUME_NO_CLEAN="1")
        cmd = ["bash", str(runner), str(project), platform, project.name]
        started = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
        workspace_key = _workspace_key(project, platform)
        with workspace_locks_guard:
            workspace_lock = workspace_locks.setdefault(
                workspace_key, threading.Lock())
        with workspace_lock:
            returncode, supervisor_timeout = _run_bounded(
                cmd, log, env=env, timeout=max(1, timeout),
                grace=max(1, supervisor_grace))
            for extractor, name in ((extract_route, "route.json"),
                                     (extract_ppa, "ppa.json")):
                subprocess.run([sys.executable, str(extractor), str(project),
                                str(project / "reports" / name)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        checkpoint = _stage_checkpoint(project)
        completed = returncode == 0 and _has_run(project)
        failure_class, failure_domain = _classify_attempt(
            returncode, supervisor_timeout, completed, checkpoint, log)
        attempts = state["attempts"].get(str(project), [])
        attempt_no = len(attempts) + 1
        result = {"config_sha256": digest, "platform": platform,
                  "flow_rc": returncode, "completed": completed,
                  "attempt": attempt_no, "resumable": completed,
                  "supervisor_timeout": supervisor_timeout,
                  "force_clean_run": force_clean_run,
                  "resume_from": resume_from,
                  "failure_class": failure_class,
                  "failure_domain": failure_domain,
                  "last_checkpoint": checkpoint,
                  "expected_failure_observed": (
                      str(project) in expected_failures and returncode != 0),
                  "started_at": started,
                  "ended_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                  "log": str(log), "log_sha256": _sha(log)}
        receipt = {**result, "receipt_version": VERSION,
                   "toolchain_binding": toolchain,
                   "command": cmd, "timeout_s": timeout,
                   "supervisor_grace_s": supervisor_grace,
                   "force_clean_run": force_clean_run,
                   "resume_from": resume_from,
                   "stage_log": (checkpoint or {}).get("path"),
                   "stage_log_sha256": _sha(Path((checkpoint or {}).get("path", "")))
                   if (checkpoint or {}).get("path") else None}
        _write(project / "campaign-run-receipt.json", receipt)
        with lock:
            state["runs"][str(project)] = result
            state["attempts"].setdefault(str(project), []).append(result)
            _write(state_path, state)
        return project, result

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(one, entry) for entry in sorted(projects)]
        for future in as_completed(futures):
            project, result = future.result()
            print(f"[diversity] {project.name}: rc={result['flow_rc']} "
                  f"class={result.get('failure_class', 'SUCCESS')}", flush=True)
    _write(root / "campaign_recovery_report.json",
           _recovery_report(state, project_allowlist=project_allowlist))


def capture_pairs(manifest_path: Path, manifest: dict, db_path: Path,
                  artifacts: Path, *,
                  dataset_campaign_id: str = "live",
                  require_complete_oracle: bool = True,
                  require_full_oracle: bool = False,
                  default_dataset_split: str | None = None) -> None:
    """Capture pair observations with an explicit learner-admission boundary.

    A route-clean ORFS run is not automatically a complete observation: the
    adapter also requires definitive before/after evidence for the target,
    DRC and timing obligations.  Incomplete pairs remain useful diagnostic
    observations, but they are assigned to the calibration split and can
    never be marked learner eligible.  This keeps campaign capture aligned
    with the design document's fail-closed oracle contract while preserving
    the immutable transition/effect evidence for audit.
    """
    db_path = require_staging_destination(db_path, campaign_root=manifest_path.parent)
    conn = tehm_db.connect(db_path)
    tehm_db.ensure_schema(conn)
    store, physical = ArtifactStore(artifacts), PhysicalEffectMemory(conn)
    captured = {x["case_id"]: x for x in manifest.get("captured", [])}
    for item in manifest["items"]:
        record = build_orfs_pair_record(
            Path(item["before_project"]), Path(item["after_project"]),
            lineage_id=item["lineage_id"], target_check=item["check"],
            config_edits=item["config_edits"], transformation_family=item["family"],
            semantic_oracle=item.get("semantic_oracle"))
        complete = record.verification.get("oracle_complete") is True
        # Re-check the executed ORFS hook at capture time.  A route pair can
        # pass the historical presence oracle while the platform hook ignores
        # the changed config value; such an observation remains immutable
        # audit evidence but is never learner support.
        before_project = Path(item["before_project"])
        preflight = inspect_routing_layer_adjustment(
            item.get("platform", ""), item.get("config_edits") or {},
            config=parse_orfs_config(
                before_project / "constraints" / "config.mk"),
            project_dir=before_project,
            orfs_root=manifest.get("orfs_root"))
        preflight["digest"] = preflight_digest(preflight)
        preflight_blocked = preflight["status"] in {"NO_OP", "UNKNOWN"}
        record.verification["execution_preflight"] = preflight
        if preflight_blocked:
            complete = False
            record.verification["oracle_complete"] = False
        expected_bindings = item.get("input_bindings")
        expected_timing = item.get("timing_contract")
        if isinstance(expected_bindings, dict) or isinstance(expected_timing, dict):
            rtl_files = [Path(path) for path in item.get("rtl_files") or []]
            actual_bindings = {
                "before": _input_binding(Path(item["before_project"]), rtl_files),
                "after": _input_binding(Path(item["after_project"]), rtl_files),
            }
            binding_ok = True
            if isinstance(expected_bindings, dict):
                binding_ok = all(
                    isinstance(expected_bindings.get(side), dict) and
                    _input_binding_matches(actual_bindings[side],
                                           expected_bindings[side])
                    for side in ("before", "after"))
            actual_timing = {
                "before": _timing_contract(Path(item["before_project"])),
                "after": _timing_contract(Path(item["after_project"])),
            }
            timing_ok = True
            if isinstance(expected_timing, dict):
                timing_ok = all(
                    isinstance(expected_timing.get(side), dict) and
                    _timing_contract_matches(actual_timing[side],
                                             expected_timing[side])
                    for side in ("before", "after"))
            record.verification["input_binding"] = {
                "expected": expected_bindings,
                "actual": actual_bindings,
                "verified": binding_ok,
            }
            record.verification["timing_contract"] = {
                "expected": expected_timing,
                "actual": actual_timing,
                "verified": timing_ok,
            }
            complete = bool(complete and binding_ok and timing_ok)
            # Persist the effective admission decision inside the canonical
            # transition as well as the campaign manifest.  A consumer must
            # not see ``oracle_complete=true`` alongside a failed provenance
            # binding merely because the original adapter oracle passed.
            record.verification["oracle_complete"] = complete
        if require_full_oracle:
            # The pair adapter's historical completeness predicate covers the
            # target/DRC/timing obligations.  Add-designs promotion candidates
            # must additionally prove equivalence, aggregate strict signoff,
            # complete graph, toolchain and artifact provenance on both arms.
            rtl_files = [Path(path).resolve() for path in item.get("rtl_files") or []]
            full = {
                "before": assess_full_oracle(
                    Path(item["before_project"]), rtl_files=rtl_files,
                    expected_input_binding=(expected_bindings or {}).get("before")
                    if isinstance(expected_bindings, dict) else None,
                    expected_timing_contract=(expected_timing or {}).get("before")
                    if isinstance(expected_timing, dict) else None),
                "after": assess_full_oracle(
                    Path(item["after_project"]), rtl_files=rtl_files,
                    expected_input_binding=(expected_bindings or {}).get("after")
                    if isinstance(expected_bindings, dict) else None,
                    expected_timing_contract=(expected_timing or {}).get("after")
                    if isinstance(expected_timing, dict) else None),
            }
            record.verification["full_oracle"] = full
            complete = bool(complete and full["before"]["complete"] and
                            full["after"]["complete"])
            record.verification["oracle_complete"] = complete
        # A prepared manifest may explicitly classify a pair as held-out or
        # calibration.  This is needed for a real transfer lane: the pair is
        # still captured as immutable audit evidence, but it must never become
        # learner support merely because its full oracle passed.  Legacy
        # manifests omit the field and retain the historical
        # complete->training / incomplete->calibration behavior.
        requested_split = item.get("dataset_split")
        if requested_split is None:
            requested_split = default_dataset_split
        if requested_split is not None:
            requested_split = str(requested_split)
            if requested_split not in SPLITS:
                raise BatchLaneError(
                    f"invalid dataset split for {item.get('case_id')}: "
                    f"{requested_split!r}")
        if requested_split is None:
            dataset_split = "training" if complete else "calibration"
        elif requested_split == "training":
            # Training admission still requires a complete oracle.  Do not
            # silently create a training membership for a failed pair.
            dataset_split = "training" if complete else "calibration"
        else:
            dataset_split = requested_split
        learner_eligible = bool(dataset_split == "training" and complete)
        if require_complete_oracle and not complete:
            stale = conn.execute(
                "SELECT dm.campaign_id, dm.split "
                "FROM tehm_dataset_membership dm "
                "JOIN tehm_transitions t ON t.transition_id=dm.transition_id "
                "WHERE dm.learner_eligible=1 AND t.provenance_json LIKE ?",
                (f'%\"record_id\":\"{record.record_id}\"%',),
            ).fetchall()
            if stale:
                campaigns = ", ".join(
                    f"{row['campaign_id']}:{row['split']}" for row in stale)
                raise BatchLaneError(
                    "incomplete ORFS pair conflicts with existing learner "
                    f"membership ({campaigns}); use a fresh staging DB or "
                    "audit the prior campaign before recapture")
        receipt = capture(
            conn, store, record, dataset_campaign_id=dataset_campaign_id,
            dataset_split=dataset_split,
            dataset_learner_eligible=learner_eligible)
        physical.record(
            transition_id=receipt.transition_id, action_domain=record.action["domain"],
            transformation_family=item["family"],
            before_ppa=record.before.get("reports", {}).get("ppa") or {},
            after_ppa=record.after.get("reports", {}).get("ppa") or {},
            effect_key=receipt.primary_effect_key,
            evidence_refs=record.verification.get("evidence_refs"))
        captured[item["case_id"]] = {
            "case_id": item["case_id"], "transition_id": receipt.transition_id,
            "family": item["family"], "platform": item["platform"],
            "lineage_id": item["lineage_id"], "outcome": receipt.outcome,
            "oracle_complete": complete,
            "dataset_split": dataset_split,
            "learner_eligible": learner_eligible,
            "execution_preflight": preflight,
            "execution_preflight_blocked": preflight_blocked,
        }
    conn.close()
    manifest["captured"] = [captured[k] for k in sorted(captured)]
    # Explicitly prove the held-out subject did not enter ordinary support.
    # Item-level held-out rows are allowed in a dedicated transfer campaign,
    # but the sentinel lineage carried by legacy add-designs manifests must
    # never be captured as training evidence.
    sentinel = (manifest.get("heldout") or {}).get("lineage_id")
    if sentinel:
        assert sentinel not in {
            row["lineage_id"] for row in manifest["captured"]
            if row.get("dataset_split") == "training"
        }
    for row in manifest["captured"]:
        if row.get("dataset_split") != "training":
            assert row.get("learner_eligible") is False
    _write(manifest_path, manifest)


def run_ab(root: Path, manifest: dict, db_path: Path, artifacts: Path, *,
           repeats: int, cpus: int, timeout: int) -> None:
    _require_orfs_platform_scope(manifest, root=root)
    toolchain = _require_orfs_toolchain(manifest, root=root)
    backend = TehmMemoryBackend(db_path=db_path, artifact_root=artifacts)
    rebuilt = backend.rebuild()
    if not rebuilt.ok:
        backend.close()
        raise RuntimeError(f"TEHM rebuild/honesty failed: {rebuilt.detail}")
    held = manifest["heldout"]
    trials = backend.run_orfs_trials(
        base_entries=[{"design": held["lineage_id"],
                       "lineage_id": held["lineage_id"],
                       "project_path": held["project_path"],
                       "platform": held["platform"], "kind": "normal"}],
        run_flow_script=REPO_ROOT / "r2g-skills/signoff-loop/scripts/flow/run_orfs.sh",
        fix_signoff_script=REPO_ROOT / "r2g-skills/signoff-loop/scripts/flow/fix_signoff.sh",
        n_designs=1, repeats=repeats, work_root=root / "tehm_ab",
        env={"ORFS_ROOT": str(Path(manifest["orfs_root"]).resolve()),
             **toolchain["environment"],
             "ORFS_TIMEOUT": str(timeout), "ORFS_MAX_CPUS": str(cpus),
             "DRC_TIMEOUT": str(timeout), "LVS_TIMEOUT": str(timeout),
             "R2G_DISABLE_IO_CONSTRAINTS": "1"},
        lifecycle_statuses=frozenset({"promoted"}), mutate_lifecycle=False)
    backend.close()
    _write(root / "ab_result.json", {"version": VERSION, "trials": trials,
                                      "heldout": held})


def attach_graph_contexts(root: Path, manifest: dict, db_path: Path) -> dict:
    """Attach the source arm's real final-DEF context where it exists."""
    toolchain = _require_orfs_toolchain(manifest, root=root)
    conn = tehm_db.connect(db_path)
    tehm_db.ensure_schema(conn)
    physical = PhysicalEffectMemory(conn)
    transition_by_case = {row["case_id"]: row["transition_id"]
                          for row in manifest.get("captured", [])}
    runner = REPO_ROOT / "r2g-skills/def-graph/scripts/flow/run_features.sh"
    results = []
    for item in manifest["items"]:
        transition_id = transition_by_case.get(item["case_id"])
        project = Path(item["before_project"])
        final_def = _latest_successful_final_def(project)
        if not transition_id or final_def is None:
            results.append({"case_id": item["case_id"], "status": "not_available",
                            "reason": "source arm has no successful frozen final DEF"})
            continue
        log = project / "def_graph_features.log"
        # Preserve an already verified strict-signoff tier.  Re-running the
        # feature extractor with the default warn gate would overwrite the
        # authoritative signoff snapshot and silently downgrade a clean
        # sky130hd context to research.  Projects without a passing strict
        # receipt remain warn/research as before.
        strict_receipt = _load(project / "reports" / "strict_signoff.json")
        gate = "strict" if strict_receipt.get("status") == "pass" else "warn"
        # Let run_features.sh discover the DEF from the backend run so its
        # signoff gate can retain the run binding.  Passing R2G_DEF explicitly
        # is treated as an external override and necessarily downgrades strict
        # provenance to warn.  The selected run is checked below against the
        # authoritative DEF we resolved before launching the extractor.
        env = dict(os.environ,
                   ORFS_ROOT=str(Path(manifest["orfs_root"]).resolve()),
                   R2G_SIGNOFF_GATE=gate, **toolchain["environment"])
        with log.open("w") as out:
            proc = subprocess.run(
                ["bash", str(runner), str(project), item["platform"], project.name],
                stdout=out, stderr=subprocess.STDOUT, env=env)
        stats = _load(project / "reports" / "features_stats.json")
        selected_sha = ((stats.get("provenance") or {}).get("def_fingerprint") or
                        {}).get("sha256")
        # A failed/restarted production attempt can leave an unrecorded
        # RUN_* directory newer than the successful run.  The feature flow
        # then refuses to bind the explicit variant and may emit an empty
        # metadata set even though the authoritative DEF is intact.  For
        # warn/research evidence, retry once with that already-resolved DEF
        # explicitly; this preserves the DEF fingerprint while keeping the
        # strict tier fail-closed (strict receipts never take this path).
        metadata_status = ((stats.get("features") or {}).get("metadata") or {}).get("status")
        if (gate != "strict" and metadata_status != "ok"):
            fallback_env = dict(env, R2G_DEF=str(final_def))
            with log.open("a") as out:
                out.write("\n[attach_graph_contexts] fallback: explicit resolved DEF\n")
                fallback = subprocess.run(
                    ["bash", str(runner), str(project), item["platform"], project.name],
                    stdout=out, stderr=subprocess.STDOUT, env=fallback_env)
            proc = fallback
            stats = _load(project / "reports" / "features_stats.json")
            selected_sha = ((stats.get("provenance") or {}).get("def_fingerprint") or
                            {}).get("sha256")
        if selected_sha != _sha(final_def):
            results.append({"case_id": item["case_id"], "status": "degraded",
                            "reason": "feature extractor DEF provenance is missing or different",
                            "extractor_rc": proc.returncode, "log": str(log)})
            continue
        try:
            context = load_defgraph_context(project, def_path=final_def)
            digest = physical.attach_graph_context(transition_id, context, replace=True)
        except (OSError, ValueError) as exc:
            results.append({"case_id": item["case_id"], "status": "degraded",
                            "reason": str(exc), "extractor_rc": proc.returncode,
                            "log": str(log)})
            continue
        results.append({"case_id": item["case_id"], "status": context.status,
                        "dataset_tier": context.dataset_tier,
                        "context_digest": digest, "extractor_rc": proc.returncode,
                        "transition_id": transition_id, "def": str(final_def),
                        "log": str(log)})
    covered = conn.execute(
        "SELECT COUNT(*) FROM tehm_physical_effects WHERE "
        "graph_context_digest IS NOT NULL AND graph_context_digest != ''").fetchone()[0]
    unique = conn.execute(
        "SELECT COUNT(DISTINCT graph_context_digest) FROM tehm_physical_effects "
        "WHERE graph_context_digest IS NOT NULL AND graph_context_digest != ''").fetchone()[0]
    conn.close()
    report = {"version": VERSION, "results": results,
              "context_covered": covered, "unique_graph_contexts": unique}
    _write(root / "physical_graph_contexts.json", report)
    return report


def evaluate_physical_retrieval(root: Path, manifest: dict, db_path: Path) -> dict:
    """Exercise conservative graph retrieval on real in-domain and OOD DEFs."""
    toolchain = _require_orfs_toolchain(manifest, root=root)
    ab = _load(root / "ab_result.json")
    pairs = [pair for trial in ab.get("trials", [])
             for pair in (trial.get("metrics") or {}).get("pairs", [])]
    successful = next((pair for pair in reversed(pairs)
                       if (pair.get("arm_b") or {}).get("success")), None)
    if successful is None:
        raise RuntimeError("no successful held-out rule arm is available for graph query")
    sandbox = Path(successful["rollback_receipt"]["sandbox_root"])
    project = sandbox / "arm_b"
    final_def = _latest_successful_final_def(project)
    if final_def is None:
        raise RuntimeError(f"successful held-out arm has no frozen final DEF: {project}")
    runner = REPO_ROOT / "r2g-skills/def-graph/scripts/flow/run_features.sh"
    log = project / "def_graph_features.log"
    env = dict(os.environ, ORFS_ROOT=str(Path(manifest["orfs_root"]).resolve()),
               R2G_DEF=str(final_def), R2G_SIGNOFF_GATE="warn",
               **toolchain["environment"])
    with log.open("w") as out:
        proc = subprocess.run(
            ["bash", str(runner), str(project), manifest["heldout"]["platform"],
             manifest["heldout"]["design"]],
            stdout=out, stderr=subprocess.STDOUT, env=env)
    held_context = load_defgraph_context(project, def_path=final_def)

    conn = tehm_db.connect(db_path)
    tehm_db.ensure_schema(conn)
    physical = PhysicalEffectMemory(conn)
    ood = physical.predict(
        family="DENSITY_RELIEF", graph_context=held_context,
        k=5, min_unique_contexts=3, max_distance=3.0)
    row = conn.execute(
        "SELECT transition_id,graph_context_json FROM tehm_physical_effects "
        "WHERE transformation_family='DENSITY_RELIEF' "
        "AND graph_context_digest IS NOT NULL AND graph_context_digest != '' "
        "ORDER BY transition_id LIMIT 1").fetchone()
    sparse = None
    if row is not None:
        sparse = physical.predict(
            family="DENSITY_RELIEF",
            graph_context=tehm_db.read_json(row["graph_context_json"]),
            k=5, min_unique_contexts=3, max_distance=3.0)
    conn.close()
    result = {
        "version": VERSION,
        "heldout_query": {
            "lineage_id": manifest["heldout"]["lineage_id"],
            "platform": manifest["heldout"]["platform"],
            "def": str(final_def), "extractor_rc": proc.returncode,
            "log": str(log), "prediction": ood,
        },
        "compatible_sparse_query": {
            "transition_id": row["transition_id"] if row is not None else None,
            "prediction": sparse,
        },
        "policy": {"k": 5, "min_unique_contexts": 3, "max_distance": 3.0,
                   "gradient_claimed": False},
    }
    _write(root / "physical_prediction_report.json", result)
    return result


def report(root: Path, manifest: dict, db_path: Path) -> dict:
    rows = manifest.get("captured", [])
    strata = {}
    for key_name in ("family", "platform"):
        grouped = {}
        for row in rows:
            grouped.setdefault(row[key_name], []).append(row)
        strata[key_name] = {}
        for key, values in sorted(grouped.items()):
            positive = sum(v["outcome"] in {"PASS", "PARTIAL"} for v in values)
            strata[key_name][key] = {
                "transitions": len(values), "positive": positive,
                "positive_rate": positive / len(values),
                "wilson_95": _wilson(positive, len(values)),
                "lineages": sorted({v["lineage_id"] for v in values}),
            }
    # Retrieval evaluation may persist receipts; use the canonical TEHM store,
    # never a legacy authority, but do not incorrectly open it SQLite read-only.
    conn = tehm_db.connect(db_path)
    tehm_db.ensure_schema(conn)
    total = conn.execute("SELECT COUNT(*) FROM tehm_transitions").fetchone()[0]
    funnel_cases = [{"case_id": item["case_id"], "design_id": item["lineage_id"],
                     "project_path": item["before_project"],
                     "platform": item["platform"], "check": item["check"],
                     "cfg": {item["knob"]: item["before_value"]}}
                    for item in manifest["items"]]
    funnel = evaluate_campaign(conn, funnel_cases)
    conn.close()
    result = {"campaign_version": VERSION, "captured": len(rows),
              "canonical_transition_total": total, "strata": strata,
              "heldout": manifest["heldout"], "firewall": manifest["firewall"],
              "funnel": funnel}
    _write(root / "diversity_report.json", result)
    _write(root / "campaign_metrics.json", funnel)
    (root / "campaign_metrics.md").write_text(to_markdown(funnel))
    return result


def _wilson(k: int, n: int) -> list[float] | None:
    if not n:
        return None
    z, p = 1.959963984540054, k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [round(max(0.0, centre - half), 6), round(min(1.0, centre + half), 6)]


def _has_run(project: Path) -> bool:
    """Return true only for a completed production run with a frozen DEF.

    Merely having run-meta/stage-log files is not completion: timeout and failed
    runs preserve both for diagnosis.  Treating those as resumable successes
    caused the diversity campaign to reuse rc=124 evidence indefinitely.
    """
    runs = sorted((project / "backend").glob("RUN_*"),
                  key=_run_mtime, reverse=True)
    for run in runs:
        meta = _load(run / "run-meta.json")
        if meta.get("make_status") != 0:
            return False
        return any((run / sub / "6_final.def").is_file() for sub in ("final", "results"))
    return False


def _run_mtime(run: Path) -> int:
    """Use receipt/stage writes, not the leaf directory mtime, for ordering."""
    mtimes = []
    for name in ("run-meta.json", "stage_log.jsonl", "flow.log",
                 "final/6_final.def", "results/6_final.def", "final/6_final.odb",
                 "results/6_final.odb"):
        path = run / name
        try:
            mtimes.append(path.stat().st_mtime_ns)
        except OSError:
            pass
    if mtimes:
        return max(mtimes)
    try:
        return run.stat().st_mtime_ns
    except OSError:
        return 0


def _reusable_success(old: dict, digest: str, project: Path) -> bool:
    """Gate cache reuse on an honest rc=0 receipt plus a frozen final DEF."""
    old_success = (old.get("flow_rc") == 0 and
                   old.get("failure_class", "SUCCESS") == "SUCCESS")
    return bool(old.get("config_sha256") == digest and old.get("completed") and
                old_success and _has_run(project))


def _run_bounded(cmd: list[str], log: Path, *, env: dict, timeout: int,
                 grace: int) -> tuple[int, bool]:
    """Run a flow in its own process group and reap the whole group on timeout.

    ``run_orfs.sh`` already bounds each ORFS stage.  This outer supervisor
    handles a stuck wrapper, interrupted campaign, or a descendant that keeps
    stdout open after the inner timeout.  The boolean distinguishes an outer
    supervisor kill from an ordinary ORFS ``124`` receipt.
    """
    log.parent.mkdir(parents=True, exist_ok=True)
    supervisor_timeout = False
    with log.open("w") as output:
        proc = subprocess.Popen(cmd, stdout=output, stderr=subprocess.STDOUT,
                                env=env, start_new_session=True)
        try:
            returncode = proc.wait(timeout=max(1, timeout) + max(1, grace))
        except subprocess.TimeoutExpired:
            supervisor_timeout = True
            _terminate_process_group(proc, grace=max(1, grace))
            returncode = 124
        output.flush()
    return returncode, supervisor_timeout


def _terminate_process_group(proc: subprocess.Popen, *, grace: int) -> None:
    """TERM then KILL a session created by ``start_new_session=True``."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + min(max(1, grace), 30)
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return
        time.sleep(0.05)
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def _stage_checkpoint(project: Path) -> dict | None:
    """Return the last parseable stage record from the newest ORFS run."""
    logs = sorted((project / "backend").glob("RUN_*/stage_log.jsonl"),
                  key=lambda path: _run_mtime(path.parent))
    for path in reversed(logs):
        try:
            lines = path.read_text().splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                return {"path": str(path.resolve()),
                        "stage": row.get("stage"),
                        "status": row.get("status"),
                        "elapsed_s": row.get("elapsed_s")}
    return None


def _resume_stage(checkpoint: dict | None, *, project: Path | None = None) -> str | None:
    """Return a valid ORFS stage for crash-resume, otherwise force a clean run."""
    if not checkpoint:
        return None
    stage = checkpoint.get("stage")
    status = checkpoint.get("status")
    if status in (0, "0", "pass", "ok"):
        return None
    if stage not in {"synth", "floorplan", "place", "cts", "route", "finish"}:
        return None
    if project is not None and not _resume_artifacts_available(project, stage):
        # The flow's resume-lineage verifier is fail-closed. Avoid launching a
        # doomed resume when an old run predates the artifact manifest or the
        # installed ORFS uses a different canonical filename; the next attempt
        # will be a clean run and will retain this checkpoint in its receipt.
        return None
    return stage


def _resume_artifacts_available(project: Path, from_stage: str) -> bool:
    """Check the actual canonical artifacts needed before ``FROM_STAGE``."""
    order = ("synth", "floorplan", "place", "cts", "route", "finish")
    required = order[:order.index(from_stage)]
    if not required:
        return True
    try:
        config = (project / "constraints" / "config.mk").read_text()
    except OSError:
        return False
    values = {}
    for line in config.splitlines():
        match = re.match(r"^\s*export\s+([A-Z0-9_]+)\s*=\s*(.*?)\s*$", line)
        if match:
            values[match.group(1)] = match.group(2)
    platform = values.get("PLATFORM")
    nickname = values.get("DESIGN_NICKNAME") or values.get("DESIGN_NAME")
    if not platform or not nickname:
        return False
    results_root = project / ".orfs-work" / "results" / platform
    candidates = sorted(results_root.glob(f"*/{project.name}"))
    artifacts = {
        "synth": "1_synth.odb", "floorplan": "2_floorplan.odb",
        "place": "3_place.odb", "cts": "4_cts.odb",
        "route": "5_route.odb", "finish": "6_final.odb",
    }
    return bool(candidates) and all(
        (candidates[-1] / artifacts[stage]).is_file() for stage in required)


def _classify_attempt(returncode: int, supervisor_timeout: bool,
                      completed: bool, checkpoint: dict | None,
                      log: Path) -> tuple[str, str]:
    """Classify runner health without turning a failed flow into evidence."""
    if completed:
        return "SUCCESS", "none"
    text = ""
    try:
        text = log.read_text(errors="replace").lower()
    except OSError:
        pass
    if supervisor_timeout or returncode == 124 or "timed out" in text:
        return "TIMEOUT", "infrastructure"
    if returncode in (130, 143) or "campaign interrupted" in text:
        return "INTERRUPTED", "campaign"
    infrastructure_markers = (
        "r2g_inputs_missing", "platform variable not set",
        "project_inputs_missing", "read-only file system",
        "permission denied", "orfs not found", "resume lineage",
        "resume verification failed", "canonical artifact",
    )
    if any(marker in text for marker in infrastructure_markers):
        return "INFRASTRUCTURE_FAILURE", "infrastructure"
    # A non-zero ORFS stage with complete inputs is retained as a design/tool
    # failure. It is never classified as a successful transition by this lane.
    return "FLOW_FAILURE", "design_or_tool"


def _recovery_report(state: dict, *, project_allowlist: set[str] | None = None) -> dict:
    """Summarize retries without treating any non-success as a transition."""
    rows = []
    for project, latest in sorted((state.get("runs") or {}).items()):
        if project_allowlist is not None and str(Path(project).resolve()) not in project_allowlist:
            continue
        attempts = (state.get("attempts") or {}).get(project, [])
        rows.append({
            "project": project,
            "attempts": len(attempts),
            "latest": latest,
            "classes": sorted({a.get("failure_class") for a in attempts if a.get("failure_class")}),
            "domains": sorted({a.get("failure_domain") for a in attempts if a.get("failure_domain")}),
        })
    counts = {}
    for row in rows:
        key = (row.get("latest") or {}).get("failure_class") or "UNKNOWN"
        counts[key] = counts.get(key, 0) + 1
    return {"version": VERSION, "projects": rows,
            "latest_class_counts": dict(sorted(counts.items()))}


def _latest_successful_final_def(project: Path) -> Path | None:
    for run in sorted((project / "backend").glob("RUN_*"), reverse=True):
        meta = _load(run / "run-meta.json")
        if meta.get("make_status") != 0:
            continue
        candidates = sorted((run / "final").glob("*final*.def"))
        if candidates:
            return candidates[-1].resolve()
    return None


def _sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n")
    tmp.replace(path)


if __name__ == "__main__":
    raise SystemExit(main())
