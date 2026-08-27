#!/usr/bin/env python3
"""Resumable real-ORFS TEHM campaign (30 ordinary transitions + held-out A/B).

The training corpus consists only of ordinary production runs.  Held-out A/B
arm outcomes go exclusively to ``tehm_trials`` / ``tehm_activations`` (H9).
No synthetic report or fix-log row is created.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

MEMORY_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = MEMORY_ROOT.parent
sys.path.insert(0, str(MEMORY_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tehm import db as tehm_db  # noqa: E402
from tehm.adapters.orfs_pair import build_orfs_pair_record  # noqa: E402
from tehm.artifact_store import ArtifactStore  # noqa: E402
from tehm.canonical.capture import capture  # noqa: E402
from tehm.evaluation.campaign_metrics import evaluate_campaign, to_markdown  # noqa: E402
from tehm.lifecycle.orfs_trial import reconcile_route_trial_evidence  # noqa: E402
from tehm.physical.graph_context import load_defgraph_context  # noqa: E402
from tehm.physical.effects import PHYSICAL_METRICS  # noqa: E402
from tehm.physical.memory import PhysicalEffectMemory  # noqa: E402
from tehm_backend import TehmMemoryBackend  # noqa: E402
from tehm.batch_lane import require_staging_destination  # noqa: E402
from orfs_storage import default_work_root, enforce_work_root, storage_policy  # noqa: E402

CAMPAIGN_VERSION = "orfs-campaign-v0.1"
DEFAULT_DESIGNS = ("gcd", "aes", "ibex", "jpeg", "riscv32i")
DEFAULT_HIGH_UTILS = (65, 70, 75, 80, 85, 90)
SAFE_UTIL = 20
HELDOUT_UTIL = 73
_CFG_RE = re.compile(r"^(\s*(?:export\s+)?)([A-Z0-9_]+)(\s*[:?]?=\s*).*$")


@dataclass(frozen=True)
class CampaignPaths:
    root: Path
    db: Path
    artifacts: Path
    cases: Path
    arms: Path
    state: Path
    manifest: Path
    metrics_json: Path
    metrics_md: Path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path,
                    default=default_work_root("orfs-v1"))
    ap.add_argument("--orfs-root", type=Path,
                    default=Path(os.environ.get("ORFS_ROOT", "/opt/EDA4AI/OpenROAD-flow-scripts")))
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--cpus-per-run", type=int, default=8)
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--designs", default=",".join(DEFAULT_DESIGNS))
    ap.add_argument("--high-utils", default=",".join(map(str, DEFAULT_HIGH_UTILS)))
    ap.add_argument("--phase", choices=("all", "prepare", "run", "capture", "ab",
                                         "reconcile", "report", "graph", "ppa"),
                    default="all")
    ap.add_argument("--ab-repeats", type=int, default=2)
    args = ap.parse_args(argv)
    designs = tuple(x.strip() for x in args.designs.split(",") if x.strip())
    high_utils = tuple(int(x) for x in args.high_utils.split(",") if x.strip())
    paths = _paths(enforce_work_root(args.root))
    _mkdirs(paths)

    if args.phase in ("all", "prepare", "run"):
        manifest = prepare_cases(paths, args.orfs_root, designs, high_utils)
    else:
        manifest = _load(paths.manifest)
        if not manifest:
            raise RuntimeError(f"campaign manifest missing: {paths.manifest}")
    if args.phase == "prepare":
        return 0

    if args.phase in ("all", "run"):
        run_cases(paths, manifest, workers=max(1, args.workers),
                  cpus=max(1, args.cpus_per_run), timeout=args.timeout)
    if args.phase == "run":
        return 0

    if args.phase in ("all", "capture"):
        capture_pairs(paths, manifest)
    if args.phase == "capture":
        return 0

    if args.phase in ("all", "ab"):
        run_ab(paths, manifest, repeats=max(1, args.ab_repeats),
               cpus=max(1, args.cpus_per_run), timeout=args.timeout)
    if args.phase == "ab":
        return 0

    if args.phase == "reconcile":
        result = reconcile_ab(paths)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    if args.phase == "graph":
        result = build_graph_contexts(paths, manifest)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.phase == "ppa":
        result = backfill_physical_ppa(paths, manifest)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    report = write_report(paths, manifest)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def _paths(root: Path) -> CampaignPaths:
    root = Path(root).resolve()
    return CampaignPaths(root=root, db=root / "staging" / "tehm.sqlite",
                         artifacts=root / "staging" / "artifacts", cases=root / "cases",
                         arms=root / "tehm_ab", state=root / "campaign_state.json",
                         manifest=root / "campaign_manifest.json",
                         metrics_json=root / "campaign_metrics.json",
                         metrics_md=root / "campaign_metrics.md")


def _mkdirs(paths: CampaignPaths) -> None:
    for path in (paths.root, paths.artifacts, paths.cases, paths.arms):
        path.mkdir(parents=True, exist_ok=True)


def prepare_cases(paths: CampaignPaths, orfs_root: Path, designs: tuple[str, ...],
                  high_utils: tuple[int, ...]) -> dict:
    items = []
    for design in designs:
        template = orfs_root / "flow" / "designs" / "sky130hd" / design
        cfg_path, sdc_path = template / "config.mk", template / "constraint.sdc"
        if not cfg_path.is_file() or not sdc_path.is_file():
            raise FileNotFoundError(f"ORFS template incomplete: {template}")
        safe = _materialize(paths.cases / f"{design}_safe_u{SAFE_UTIL}",
                            cfg_path, sdc_path, util=SAFE_UTIL)
        for util in high_utils:
            before = _materialize(paths.cases / f"{design}_train_u{util}",
                                  cfg_path, sdc_path, util=util)
            items.append({"case_id": f"{design}:u{util}->u{SAFE_UTIL}",
                          "lineage_id": f"orfs:{design}", "design": design,
                          "platform": "sky130hd", "check": "route",
                          "before_project": str(before), "after_project": str(safe),
                          "config_edits": {"CORE_UTILIZATION": str(SAFE_UTIL)}})
        heldout = _materialize(paths.cases / f"{design}_heldout_u{HELDOUT_UTIL}",
                               cfg_path, sdc_path, util=HELDOUT_UTIL)
        items[-1]["heldout_project"] = str(heldout)
    manifest = {"campaign_version": CAMPAIGN_VERSION,
                "orfs_root": str(Path(orfs_root).resolve()),
                "designs": list(designs), "high_utils": list(high_utils),
                "safe_util": SAFE_UTIL, "heldout_util": HELDOUT_UTIL,
                "transition_target": len(items), "items": items,
                "storage_policy": storage_policy(paths.root)}
    _write_json(paths.manifest, manifest)
    return manifest


def _materialize(project: Path, cfg_template: Path, sdc_template: Path, *, util: int) -> Path:
    for name in ("constraints", "rtl", "reports", "backend", "drc", "lvs", "rcx"):
        (project / name).mkdir(parents=True, exist_ok=True)
    sdc = project / "constraints" / "constraint.sdc"
    if not sdc.exists() or sdc.read_bytes() != sdc_template.read_bytes():
        sdc.write_bytes(sdc_template.read_bytes())
    edits = {"CORE_UTILIZATION": str(util),
             "PLACE_DENSITY_LB_ADDON": "0.35",
             "SDC_FILE": str(sdc.resolve())}
    text = _apply_edits(cfg_template.read_text(), edits)
    cfg = project / "constraints" / "config.mk"
    if not cfg.exists() or cfg.read_text() != text:
        cfg.write_text(text)
    return project.resolve()


def _apply_edits(text: str, edits: dict) -> str:
    pending = dict(edits)
    lines = []
    for line in text.splitlines():
        match = _CFG_RE.match(line)
        if match and match.group(2) in pending:
            key = match.group(2)
            lines.append(f"{match.group(1)}{key}{match.group(3)}{pending.pop(key)}")
        else:
            lines.append(line)
    lines.extend(f"export {key} = {value}" for key, value in sorted(pending.items()))
    return "\n".join(lines) + "\n"


def run_cases(paths: CampaignPaths, manifest: dict, *, workers: int,
              cpus: int, timeout: int) -> None:
    projects = sorted({item[side] for item in manifest["items"]
                       for side in ("before_project", "after_project")})
    state = _load(paths.state) or {"campaign_version": CAMPAIGN_VERSION, "runs": {}}
    lock = threading.Lock()

    def one(project_text: str):
        project = Path(project_text)
        digest = _sha(project / "constraints" / "config.mk")
        prior = state["runs"].get(str(project), {})
        if (prior.get("config_sha256") == digest
                and prior.get("completed") is True
                and _has_production_evidence(project)):
            return project, prior
        flow_log = project / "campaign_flow.log"
        env = dict(os.environ, ORFS_TIMEOUT=str(timeout), ORFS_MAX_CPUS=str(cpus))
        cmd = ["bash", str(_run_flow_script()), str(project), "sky130hd", project.name]
        started_at = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
        with flow_log.open("w") as out:
            proc = subprocess.run(cmd, stdout=out, stderr=subprocess.STDOUT, env=env)
        ended_at = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
        stage_logs = sorted((project / "backend").glob("RUN_*/stage_log.jsonl"))
        receipt = {
            "receipt_version": CAMPAIGN_VERSION, "command": cmd,
            "returncode": proc.returncode, "config_sha256": digest,
            "started_at": started_at, "ended_at": ended_at,
            "stdout_log": str(flow_log.resolve()), "stdout_sha256": _sha(flow_log),
            "stage_log": str(stage_logs[-1].resolve()) if stage_logs else None,
            "stage_log_sha256": _sha(stage_logs[-1]) if stage_logs else None,
        }
        _write_json(project / "campaign-run-receipt.json", receipt)
        drc_rc = None
        if proc.returncode == 0:
            drc_log = project / "campaign_drc.log"
            denv = dict(env, DRC_TIMEOUT=str(timeout))
            with drc_log.open("w") as out:
                drc = subprocess.run(["bash", str(_run_drc_script()), str(project),
                                      "sky130hd", project.name], stdout=out,
                                     stderr=subprocess.STDOUT, env=denv)
            drc_rc = drc.returncode
        result = {"config_sha256": digest, "flow_rc": proc.returncode,
                  "drc_rc": drc_rc,
                  "completed": _has_production_evidence(project)}
        with lock:
            state["runs"][str(project)] = result
            _write_json(paths.state, state)
        return project, result

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(one, p) for p in projects]
        for future in as_completed(futures):
            project, result = future.result()
            print(f"[campaign] {project.name}: flow={result['flow_rc']} drc={result['drc_rc']}",
                  flush=True)


def capture_pairs(paths: CampaignPaths, manifest: dict) -> None:
    db_path = require_staging_destination(paths.db, campaign_root=paths.root)
    conn = tehm_db.connect(db_path)
    tehm_db.ensure_schema(conn)
    store = ArtifactStore(paths.artifacts)
    physical = PhysicalEffectMemory(conn)
    captured = []
    for item in manifest["items"]:
        record = build_orfs_pair_record(
            Path(item["before_project"]), Path(item["after_project"]),
            lineage_id=item["lineage_id"], target_check=item["check"],
            config_edits=item["config_edits"], transformation_family="DENSITY_RELIEF")
        receipt = capture(conn, store, record)
        before_ppa = record.before.get("reports", {}).get("ppa") or {}
        after_ppa = record.after.get("reports", {}).get("ppa") or {}
        physical.record(transition_id=receipt.transition_id,
                        action_domain=record.action["domain"],
                        transformation_family=record.action["transformation_family"],
                        before_ppa=before_ppa, after_ppa=after_ppa,
                        effect_key=receipt.primary_effect_key,
                        evidence_refs=record.verification.get("evidence_refs"))
        captured.append({"case_id": item["case_id"],
                         "transition_id": receipt.transition_id,
                         "outcome": receipt.outcome})
    conn.close()
    out = dict(manifest)
    out["captured"] = captured
    _write_json(paths.manifest, out)


def run_ab(paths: CampaignPaths, manifest: dict, *, repeats: int,
           cpus: int, timeout: int) -> None:
    backend = TehmMemoryBackend(db_path=paths.db, artifact_root=paths.artifacts)
    report = backend.rebuild()
    if not report.ok:
        backend.close()
        raise RuntimeError(f"TEHM rebuild/honesty failed: {report.detail}")
    # Exactly one held-out lineage keeps the expensive production verdict scoped
    # while still testing transfer from the other source episodes.
    design = _select_heldout_design(paths, manifest)
    heldout = paths.cases / f"{design}_heldout_u{HELDOUT_UTIL}"
    entries = [{"design": f"orfs-heldout:{design}",
                "lineage_id": f"orfs-heldout:{design}",
                "project_path": str(heldout), "platform": "sky130hd",
                "kind": "normal"}]
    trials = backend.run_orfs_trials(
        base_entries=entries, run_flow_script=_run_flow_script(),
        fix_signoff_script=_fix_script(), n_designs=1, repeats=repeats,
        work_root=paths.arms,
        env={"ORFS_TIMEOUT": str(timeout), "ORFS_MAX_CPUS": str(cpus),
             "DRC_TIMEOUT": str(timeout), "LVS_TIMEOUT": str(timeout)})
    backend.close()
    _write_json(paths.root / "ab_result.json", {"trials": trials})


def reconcile_ab(paths: CampaignPaths) -> dict:
    conn = tehm_db.connect(paths.db)
    tehm_db.ensure_schema(conn)
    row = conn.execute(
        "SELECT trial_uuid FROM tehm_trials WHERE target_scope='route' "
        "ORDER BY created_at DESC LIMIT 1").fetchone()
    if row is None:
        conn.close()
        raise RuntimeError("no completed production route trial to reconcile")
    result = reconcile_route_trial_evidence(
        conn, trial_uuid=row["trial_uuid"],
        extract_route_script=(REPO_ROOT /
                              "r2g-skills/signoff-loop/scripts/extract/extract_route.py"))
    conn.close()
    _write_json(paths.root / "ab_reconciliation.json", result)
    _write_json(paths.root / "ab_result.json",
                {"trials": [result], "evidence_reconciled": True})
    return result


def build_graph_contexts(paths: CampaignPaths, manifest: dict) -> dict:
    """Run def-graph X extraction and attach compact contexts to effects."""
    conn = tehm_db.connect(paths.db)
    tehm_db.ensure_schema(conn)
    memory = PhysicalEffectMemory(conn)
    orfs_root = Path(manifest["orfs_root"])
    extractor = REPO_ROOT / "r2g-skills/def-graph/scripts/flow/run_features.sh"
    results = []
    attached_total = 0
    for design in manifest["designs"]:
        project = paths.cases / f"{design}_safe_u{SAFE_UTIL}"
        final_def = (orfs_root / "flow" / "results" / "sky130hd" /
                     design / project.name / "6_final.def")
        if not final_def.is_file():
            results.append({"design": design, "status": "not_available",
                            "reason": "no successful 6_final.def", "attached": 0})
            continue
        log = project / "def_graph_features.log"
        env = dict(os.environ, ORFS_ROOT=str(orfs_root), R2G_DEF=str(final_def),
                   R2G_SIGNOFF_GATE="warn")
        with log.open("w") as out:
            proc = subprocess.run(
                ["bash", str(extractor), str(project), "sky130hd", project.name],
                stdout=out, stderr=subprocess.STDOUT, env=env)
        try:
            context = load_defgraph_context(project, def_path=final_def)
        except (OSError, ValueError) as exc:
            results.append({"design": design, "status": "degraded",
                            "reason": str(exc), "extractor_rc": proc.returncode,
                            "log": str(log), "attached": 0})
            continue
        rows = conn.execute(
            "SELECT pe.transition_id FROM tehm_physical_effects pe "
            "JOIN tehm_transitions t ON t.transition_id=pe.transition_id "
            "JOIN tehm_states s ON s.state_id=t.source_state_id "
            "WHERE s.lineage_id=?", (f"orfs:{design}",)).fetchall()
        for row in rows:
            memory.attach_graph_context(row["transition_id"], context, replace=True)
        attached_total += len(rows)
        results.append({"design": design, "status": context.status,
                        "context_digest": context.digest(),
                        "extractor_version": context.extractor_version,
                        "dataset_tier": context.dataset_tier,
                        "signoff_status": context.signoff_health.get("status"),
                        "extractor_rc": proc.returncode, "log": str(log),
                        "attached": len(rows)})
    total = memory.count()
    covered = conn.execute(
        "SELECT COUNT(*) FROM tehm_physical_effects "
        "WHERE graph_context_digest IS NOT NULL AND graph_context_digest != ''"
    ).fetchone()[0]
    conn.close()
    report = {"results": results, "physical_effects": total,
              "context_attached_this_run": attached_total,
              "context_covered": covered,
              "context_coverage": (covered / total if total else None)}
    _write_json(paths.root / "physical_graph_contexts.json", report)
    return report


def backfill_physical_ppa(paths: CampaignPaths, manifest: dict) -> dict:
    """Derive PPA from preserved production logs and enrich existing effects."""
    extractor = REPO_ROOT / "r2g-skills/signoff-loop/scripts/extract/extract_ppa.py"
    projects = sorted({Path(item[side]) for item in manifest["items"]
                       for side in ("before_project", "after_project")})
    extraction = []
    for project in projects:
        out = project / "reports" / "ppa.json"
        proc = subprocess.run(
            [sys.executable, str(extractor), str(project), str(out)],
            capture_output=True, text=True)
        extraction.append({"project": str(project), "returncode": proc.returncode,
                           "ppa": str(out), "sha256": _sha(out) if out.is_file() else None})

    captured = {row["case_id"]: row["transition_id"]
                for row in manifest.get("captured", [])}
    conn = tehm_db.connect(paths.db)
    tehm_db.ensure_schema(conn)
    memory = PhysicalEffectMemory(conn)
    updated = []
    for item in manifest["items"]:
        transition_id = captured.get(item["case_id"])
        if not transition_id:
            continue
        before_path = Path(item["before_project"]) / "reports" / "ppa.json"
        after_path = Path(item["after_project"]) / "reports" / "ppa.json"
        before_ppa, after_ppa = _load(before_path), _load(after_path)
        refs = [{"side": side, "oracle": "ppa", "path": str(path.resolve()),
                 "sha256": _sha(path), "extractor": str(extractor.resolve())}
                for side, path in (("before", before_path), ("after", after_path))
                if path.is_file()]
        deltas = memory.backfill_ppa(
            transition_id, before_ppa=before_ppa, after_ppa=after_ppa,
            evidence_refs=refs, replace=True)
        updated.append({"case_id": item["case_id"],
                        "transition_id": transition_id, "deltas": deltas})
    rows = conn.execute("SELECT deltas_json FROM tehm_physical_effects").fetchall()
    coverage = {metric: sum(
        1 for row in rows if tehm_db.read_json(row["deltas_json"]).get(metric) is not None)
        for metric in PHYSICAL_METRICS}
    conn.close()
    report = {"extracted_projects": len(extraction), "updated_effects": len(updated),
              "total_effects": len(rows), "metric_coverage": coverage,
              "extraction": extraction}
    _write_json(paths.root / "physical_ppa_backfill.json", report)
    return report


def write_report(paths: CampaignPaths, manifest: dict) -> dict:
    conn = tehm_db.connect(paths.db)
    tehm_db.ensure_schema(conn)
    cases = [{"case_id": item["case_id"], "design_id": item["lineage_id"],
              "project_path": item["before_project"], "platform": item["platform"],
              "check": item["check"], "cfg": {"CORE_UTILIZATION":
                                                 item["case_id"].split("u", 1)[-1].split("->", 1)[0]}}
             for item in manifest["items"]]
    report = evaluate_campaign(conn, cases)
    report["campaign"] = {"version": CAMPAIGN_VERSION,
                          "transition_target": manifest["transition_target"],
                          "transitions": conn.execute(
                              "SELECT COUNT(*) FROM tehm_transitions").fetchone()[0],
                          "positive_transitions": conn.execute(
                              "SELECT COUNT(*) FROM tehm_transitions WHERE outcome IN ('PASS','PARTIAL')"
                          ).fetchone()[0],
                          "rules": conn.execute("SELECT COUNT(*) FROM tehm_rules").fetchone()[0],
                          "trials": conn.execute("SELECT COUNT(*) FROM tehm_trials").fetchone()[0],
                          "physical_effects": conn.execute(
                              "SELECT COUNT(*) FROM tehm_physical_effects").fetchone()[0],
                          "physical_graph_contexts": conn.execute(
                              "SELECT COUNT(*) FROM tehm_physical_effects "
                              "WHERE graph_context_digest IS NOT NULL AND "
                              "graph_context_digest != ''").fetchone()[0]}
    conn.close()
    _write_json(paths.metrics_json, report)
    paths.metrics_md.write_text(to_markdown(report))
    return report


def _run_flow_script() -> Path:
    return REPO_ROOT / "r2g-skills/signoff-loop/scripts/flow/run_orfs.sh"


def _select_heldout_design(paths: CampaignPaths, manifest: dict) -> str:
    state = (_load(paths.state).get("runs") or {})
    eligible = []
    for design in manifest["designs"]:
        safe = (paths.cases / f"{design}_safe_u{SAFE_UTIL}").resolve()
        if state.get(str(safe), {}).get("flow_rc") != 0:
            continue
        logs = sorted((safe / "backend").glob("RUN_*/stage_log.jsonl"))
        elapsed = 10**12
        if logs:
            try:
                elapsed = sum(json.loads(line).get("elapsed_s", 0)
                              for line in logs[-1].read_text().splitlines()
                              if line.strip())
            except (OSError, json.JSONDecodeError):
                pass
        eligible.append((elapsed, design))
    if not eligible:
        raise RuntimeError("no successful safe ORFS lineage is available for held-out A/B")
    return min(eligible)[1]


def _run_drc_script() -> Path:
    return REPO_ROOT / "r2g-skills/signoff-loop/scripts/flow/run_drc.sh"


def _fix_script() -> Path:
    return REPO_ROOT / "r2g-skills/signoff-loop/scripts/flow/fix_signoff.sh"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _has_production_evidence(project: Path) -> bool:
    has_stage = any((run / "stage_log.jsonl").is_file()
                    for run in (project / "backend").glob("RUN_*"))
    has_orfs_meta = any((run / "run-meta.json").is_file()
                        for run in (project / "backend").glob("RUN_*"))
    return has_stage and (has_orfs_meta or
                          (project / "campaign-run-receipt.json").is_file())


def _load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n")
    tmp.replace(path)


if __name__ == "__main__":
    raise SystemExit(main())
