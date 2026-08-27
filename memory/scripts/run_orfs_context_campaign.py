#!/usr/bin/env python3
"""Build the >=3-context physical-memory matrix with real ORFS executions.

Three successful, content-distinct source DEFs are shared across three action
families on each platform.  Sky130HD sources receive full strict signoff;
Sky130HS/IHP-SG13G2 sources remain explicitly research tier.  Separate routing
stress pairs probe fail->pass recovery without pretending a failed source has a
usable physical graph; platforms that still route are retained as neutral probes.
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

from run_orfs_diversity_campaign import (  # noqa: E402
    _latest_successful_final_def, _load, _materialize, _write, run_projects,
)
from tehm import db as tehm_db  # noqa: E402
from tehm.adapters.orfs_pair import build_orfs_pair_record  # noqa: E402
from tehm.artifact_store import ArtifactStore  # noqa: E402
from tehm.canonical.capture import capture  # noqa: E402
from tehm.physical.graph_context import load_defgraph_context  # noqa: E402
from tehm.physical.memory import PhysicalEffectMemory  # noqa: E402
from tehm.batch_lane import require_staging_destination  # noqa: E402
from orfs_storage import default_work_root, enforce_work_root, storage_policy  # noqa: E402

VERSION = "orfs-context-v0.1"
FAMILIES = (
    "DENSITY_RELIEF",
    "ROUTING_CAPACITY_RECOVERY",
    "PLACEMENT_DENSITY_RECOVERY",
)
PLATFORMS = {
    "sky130hd": {"design": "gcd", "tier": "strict_clean",
                 "min_layer": "met1", "max_layer": "met5"},
    "sky130hs": {"design": "gcd", "tier": "research",
                 "min_layer": "met1", "max_layer": "met5",
                 "stress_knob": "ROUTING_LAYER_ADJUSTMENT",
                 "stress_before": "0.99", "stress_expected_failure": False},
    # GF180 detail route currently reaches zero violations and then aborts in
    # OpenROAD while updating dbAccessPoint/_dbITerm.  That infrastructure
    # crash is not a physical-design outcome, so use the native IHP GCD flow as
    # the third platform and retain the failed GF180 evidence out of band.
    "ihp-sg13g2": {"design": "gcd", "tier": "research",
              "min_layer": "Metal2", "max_layer": "Metal5"},
}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path,
                    default=default_work_root("orfs-v3-contexts"))
    ap.add_argument("--orfs-root", type=Path,
                    default=Path(os.environ.get("ORFS_ROOT", "/opt/EDA4AI/OpenROAD-flow-scripts")))
    ap.add_argument("--staging-db", type=Path, default=None)
    ap.add_argument("--staging-artifacts", type=Path, default=None)
    ap.add_argument("--phase", choices=("all", "prepare", "run", "signoff", "capture", "graph", "audit"),
                    default="all")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--cpus-per-run", type=int, default=6)
    ap.add_argument("--timeout", type=int, default=1800)
    args = ap.parse_args(argv)
    root = enforce_work_root(args.root)
    root.mkdir(parents=True, exist_ok=True)
    staging_db = (args.staging_db or root / "staging" / "tehm.sqlite").resolve()
    staging_artifacts = (args.staging_artifacts or root / "staging" / "artifacts").resolve()
    manifest_path = root / "campaign_manifest.json"
    manifest = prepare(root, args.orfs_root.resolve()) if args.phase in ("all", "prepare") else _load(manifest_path)
    if not manifest:
        raise RuntimeError(f"manifest missing: {manifest_path}")
    if args.phase == "prepare":
        return 0
    if args.phase in ("all", "run"):
        run_projects(root, manifest, workers=max(1, args.workers),
                     cpus=max(1, args.cpus_per_run), timeout=args.timeout)
    if args.phase == "run":
        return 0
    if args.phase in ("all", "signoff"):
        signoff_and_features(root, manifest, timeout=args.timeout)
    if args.phase == "signoff":
        return 0
    if args.phase in ("all", "capture"):
        capture_pairs(manifest_path, manifest, staging_db, staging_artifacts)
        manifest = _load(manifest_path)
    if args.phase == "capture":
        return 0
    if args.phase in ("all", "graph"):
        attach_contexts(root, manifest, staging_db)
    if args.phase == "graph":
        return 0
    audit(root, manifest, staging_db)
    return 0


def prepare(root: Path, orfs_root: Path) -> dict:
    projects = root / "projects"
    projects.mkdir(parents=True, exist_ok=True)
    items, baselines = [], []
    core_utils = (30, 35, 40)
    # Keep the three successful context baselines inside the platform's normal
    # routing envelope.  Fail->pass pressure is supplied separately by the
    # MAX_ROUTING_LAYER stress arms, so the context quota does not depend on a
    # 10+ minute near-timeout detailed route.
    route_before = ("0.20", "0.25", "0.30")
    route_after = ("0.05", "0.10", "0.15")
    place_after = ("0.50", "0.55", "0.60")
    for platform, spec in PLATFORMS.items():
        design = spec["design"]
        template = orfs_root / "flow" / "designs" / spec.get("template_platform", platform) / design
        for index, util in enumerate(core_utils):
            common = {"CORE_UTILIZATION": str(util),
                      "PLACE_DENSITY_LB_ADDON": "0.25",
                      "ROUTING_LAYER_ADJUSTMENT": route_before[index]}
            base = _project(projects, platform, design, f"base_{index}",
                            template, common, relax_clock=(platform == "sky130hd"))
            baseline_id = f"{platform}:{design}:base{index}"
            baselines.append({"baseline_id": baseline_id, "platform": platform,
                              "design": design, "index": index,
                              "project": str(base), "expected_tier": spec["tier"]})

            actions = (
                ("DENSITY_RELIEF", "CORE_UTILIZATION", str(util - 10)),
                ("ROUTING_CAPACITY_RECOVERY", "ROUTING_LAYER_ADJUSTMENT", route_after[index]),
                ("PLACEMENT_DENSITY_RECOVERY", "PLACE_DENSITY", place_after[index]),
            )
            for family, knob, value in actions:
                edits = {**common, knob: value}
                after = _project(projects, platform, design,
                                 f"base_{index}_{family.lower()}_after",
                                 template, edits, relax_clock=(platform == "sky130hd"))
                items.append(_item(platform, design, index, family, knob,
                                   common[knob] if knob in common else "default",
                                   value, base, after, spec["tier"], stress=False))

            # A deliberately under-provisioned routing setup probes fail->pass
            # recovery.  A failed source is captured but never counted as graph
            # support; a platform that still routes is labeled as a neutral probe.
            stress_knob = spec.get("stress_knob", "MAX_ROUTING_LAYER")
            stress_before = spec.get("stress_before", spec["min_layer"])
            stress_after = (route_before[index] if stress_knob == "ROUTING_LAYER_ADJUSTMENT"
                            else spec["max_layer"])
            stress_edits = {**common, stress_knob: stress_before}
            stress = _project(projects, platform, design, f"base_{index}_routing_stress",
                              template, stress_edits,
                              relax_clock=(platform == "sky130hd"))
            stress_item = _item(
                platform, design, index, "ROUTING_CAPACITY_RECOVERY",
                stress_knob, stress_before, stress_after,
                stress, base, spec["tier"], stress=True)
            if not spec.get("stress_expected_failure", True):
                # The tiny HS GCD still routes even at 99% capacity derating.
                # Preserve that real neutral probe, but do not label or count it
                # as fail->pass recovery evidence.
                stress_item["role"] = "routing_stress_probe"
            items.append(stress_item)

    heldout = {"lineage_id": "orfs-heldout-v3:ihp-sg13g2:spi", "platform": "ihp-sg13g2",
               "design": "spi", "role": "calibration_only", "capturable": False}
    manifest = {
        "campaign_version": VERSION,
        "orfs_root": str(orfs_root),
        "items": items,
        "baselines": baselines,
        "storage_policy": storage_policy(root),
        "families": list(FAMILIES),
        "heldout": heldout,
        "firewall": {
            "training_lineages": sorted({x["lineage_id"] for x in items}),
            "heldout_lineages": [heldout["lineage_id"]],
            "disjoint": heldout["lineage_id"] not in {x["lineage_id"] for x in items},
        },
    }
    _write(root / "campaign_manifest.json", manifest)
    return manifest


def _project(root: Path, platform: str, design: str, slug: str, template: Path,
             edits: dict[str, str], *, relax_clock: bool) -> Path:
    path = _materialize(root / f"{platform}_{design}_{slug}",
                        template / "config.mk", template / "constraint.sdc", edits)
    if relax_clock:
        sdc = path / "constraints" / "constraint.sdc"
        text, count = re.subn(r"(?m)^(\s*set\s+clk_period\s+).*$", r"\g<1>5.0", sdc.read_text())
        if count != 1:
            raise RuntimeError(f"cannot stamp relaxed clock in {sdc}")
        sdc.write_text(text)
    return path


def _item(platform, design, index, family, knob, before_value, after_value,
          before, after, tier, *, stress):
    role = "routing_positive_stress" if stress else "training"
    return {
        "case_id": f"{platform}:{design}:{index}:{family}:{knob}:{before_value}->{after_value}",
        "lineage_id": f"orfs-v3:{platform}:{design}:base{index}",
        "platform": platform, "design": design, "family": family,
        "check": "route", "knob": knob, "before_value": before_value,
        "after_value": after_value, "config_edits": {knob: after_value},
        "before_project": str(before), "after_project": str(after),
        "expected_tier": tier, "role": role,
    }


def signoff_and_features(root: Path, manifest: dict, *, timeout: int) -> None:
    strict = REPO_ROOT / "r2g-skills/signoff-loop/scripts/flow/run_strict_signoff.sh"
    features = REPO_ROOT / "r2g-skills/def-graph/scripts/flow/run_features.sh"
    results = []
    for base in manifest["baselines"]:
        project = Path(base["project"])
        final_def = _latest_successful_final_def(project)
        if final_def is None:
            results.append({**base, "status": "missing_successful_def"})
            continue
        if base["expected_tier"] == "strict_clean" and _strict_context_reusable(
                project, final_def):
            results.append({
                **base, "status": "features_complete", "def": str(final_def),
                "signoff_rc": 0, "features_rc": 0, "reused": True,
            })
            continue
        if base["expected_tier"] == "strict_clean":
            log = project / "strict_signoff.log"
            env = dict(os.environ, NETGEN_TIMEOUT=str(timeout))
            with log.open("w") as out:
                proc = subprocess.run(["bash", str(strict), str(project),
                                       base["platform"], project.name],
                                      stdout=out, stderr=subprocess.STDOUT, env=env)
            gate_mode = "strict"
            signoff_rc = proc.returncode
        else:
            gate_mode, signoff_rc = "warn", None
        log = project / "def_graph_features.log"
        env = dict(os.environ, R2G_SIGNOFF_GATE=gate_mode)
        with log.open("w") as out:
            proc = subprocess.run(["bash", str(features), str(project),
                                   base["platform"], project.name],
                                  stdout=out, stderr=subprocess.STDOUT, env=env)
        results.append({**base, "status": "features_complete" if proc.returncode == 0 else "features_failed",
                        "def": str(final_def), "signoff_rc": signoff_rc,
                        "features_rc": proc.returncode})
    _write(root / "signoff_and_features.json", {"version": VERSION, "results": results})


def _strict_context_reusable(project: Path, final_def: Path) -> bool:
    """Resume only an exact DEF-bound pass plus complete strict graph context."""
    try:
        gate = _load(project / "reports" / "signoff_gate.json")
        expected = gate["checks"]["binding"]["def_fingerprint"]["sha256"]
        actual = hashlib.sha256(final_def.read_bytes()).hexdigest()
        context = load_defgraph_context(project, def_path=final_def)
    except (OSError, KeyError, TypeError, ValueError):
        return False
    return (gate.get("status") == "pass" and expected == actual and
            context.status == "complete" and context.dataset_tier == "strict_clean")


def capture_pairs(manifest_path: Path, manifest: dict, db_path: Path,
                  artifacts: Path) -> None:
    db_path = require_staging_destination(db_path, campaign_root=manifest_path.parent)
    conn = tehm_db.connect(db_path)
    tehm_db.ensure_schema(conn)
    store, physical = ArtifactStore(artifacts), PhysicalEffectMemory(conn)
    captured = {x["case_id"]: x for x in manifest.get("captured", [])}
    for item in manifest["items"]:
        record = build_orfs_pair_record(
            Path(item["before_project"]), Path(item["after_project"]),
            lineage_id=item["lineage_id"], target_check=item["check"],
            config_edits=item["config_edits"], transformation_family=item["family"])
        receipt = capture(conn, store, record)
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
            "role": item["role"], "expected_tier": item["expected_tier"],
        }
    conn.close()
    manifest["captured"] = [captured[k] for k in sorted(captured)]
    assert manifest["firewall"]["disjoint"]
    assert manifest["heldout"]["lineage_id"] not in {x["lineage_id"] for x in manifest["captured"]}
    _write(manifest_path, manifest)


def attach_contexts(root: Path, manifest: dict, db_path: Path) -> None:
    conn = tehm_db.connect(db_path)
    tehm_db.ensure_schema(conn)
    physical = PhysicalEffectMemory(conn)
    contexts = {}
    for base in manifest["baselines"]:
        project = Path(base["project"])
        final_def = _latest_successful_final_def(project)
        if final_def is None:
            continue
        try:
            context = load_defgraph_context(project, def_path=final_def)
            contexts[base["baseline_id"]] = context
        except (OSError, ValueError):
            continue
    captured = {x["case_id"]: x for x in manifest.get("captured", [])}
    results = []
    for item in manifest["items"]:
        row = captured.get(item["case_id"])
        baseline_id = f"{item['platform']}:{item['design']}:base{item['case_id'].split(':')[2]}"
        context = contexts.get(baseline_id)
        if not row or context is None or item["role"] != "training":
            results.append({"case_id": item["case_id"], "status": "not_attached"})
            continue
        digest = physical.attach_graph_context(row["transition_id"], context, replace=True)
        results.append({"case_id": item["case_id"], "status": "complete",
                        "transition_id": row["transition_id"], "digest": digest,
                        "dataset_tier": context.dataset_tier})
    conn.close()
    _write(root / "physical_graph_contexts.json", {"version": VERSION, "results": results})


def audit(root: Path, manifest: dict, db_path: Path) -> dict:
    conn = tehm_db.connect(db_path)
    rows = conn.execute(
        "SELECT t.transition_id,t.outcome,p.transformation_family,p.graph_context_digest,"
        "p.graph_context_json FROM tehm_transitions t JOIN tehm_physical_effects p "
        "ON p.transition_id=t.transition_id WHERE t.transition_id IN (%s)" %
        ",".join("?" for _ in manifest.get("captured", [])),
        [x["transition_id"] for x in manifest.get("captured", [])]).fetchall()
    by_transition = {row["transition_id"]: row for row in rows}
    counts, routing_positive, unclassified = {}, [], []
    for captured in manifest.get("captured", []):
        row = by_transition.get(captured["transition_id"])
        if row is None:
            continue
        if (captured["family"] == "ROUTING_CAPACITY_RECOVERY" and
                captured["role"] == "routing_positive_stress" and
                row["outcome"] in {"PASS", "PARTIAL"}):
            routing_positive.append(captured["transition_id"])
        if not row["graph_context_digest"]:
            continue
        context = tehm_db.read_json(row["graph_context_json"])
        tier = str(context.get("dataset_tier") or "")
        if not tier:
            # A graph digest without a dataset tier is retained for forensic
            # review but is not an admissible training context. In particular,
            # routing-stress probes may have a physical graph while lacking a
            # complete strict/research signoff classification.
            unclassified.append({"transition_id": captured["transition_id"],
                                 "platform": captured["platform"],
                                 "family": captured["family"]})
            continue
        key = (captured["platform"], captured["family"], tier)
        counts.setdefault(key, set()).add(row["graph_context_digest"])
    conn.close()
    strata = {"|".join(key): {"unique_successful_def_contexts": len(value),
                               "meets_minimum_3": len(value) >= 3}
              for key, value in sorted(counts.items())}
    report = {
        "version": VERSION, "strata": strata,
        "all_observed_strata_meet_minimum_3": bool(strata) and all(x["meets_minimum_3"] for x in strata.values()),
        "routing_positive": len(set(routing_positive)),
        "routing_positive_transition_ids": sorted(set(routing_positive)),
        "unclassified_contexts": unclassified,
        "unclassified_context_count": len(unclassified),
        "firewall": manifest["firewall"],
    }
    _write(root / "context_coverage_audit.json", report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return report


if __name__ == "__main__":
    raise SystemExit(main())
