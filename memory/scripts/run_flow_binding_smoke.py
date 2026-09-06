"""Run one retained baseline/treatment smoke, not an evolution campaign.

Inputs and outputs are explicit; existing artifact directories are rejected.
No model calls, database writes, promotion, or held-out claims are made.
"""
import argparse
import json
from pathlib import Path

from audit_knowledge_router_bootstrap import audit
from run_orfs_diversity_campaign import preflight_orfs_toolchain
from tehm.assets.flow_config_probe import probe_flow_config
from tehm.evaluation.orfs_candidate_oracle import (
    execute_orfs_candidate, _source_inputs, _source_binding, _file_sha256, _digest,
)
from tehm.retrieval.structured_candidate import StructuredRepairCandidate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "source-sha256", "path-id", "campaign-id", "project",
                 "manifest", "python", "make", "artifacts"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--external-input", action="append", default=[])
    args = parser.parse_args()
    root = Path(args.artifacts).resolve()
    root.mkdir(parents=True, exist_ok=False)

    def save(name, value):
        (root / name).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")

    manifest = json.loads(Path(args.manifest).read_text())
    project = Path(args.project).resolve()
    preflight = preflight_orfs_toolchain({"orfs_root": manifest["orfs"]["root"],
        "toolchain_manifest": args.manifest, "pdk_root": manifest["pdk"]["root"]})
    save("preflight.json", preflight)
    if preflight["status"] != "bound_internal" or not preflight["manifest_validation"]["valid"]:
        raise RuntimeError("toolchain preflight rejected")
    pins = {"make_exe": Path(args.make), "python_exe": Path(args.python),
        "openroad_exe": Path(manifest["tools"]["openroad"]["path"]),
        "yosys_exe": Path(manifest["tools"]["yosys"]["path"])}
    observation = probe_flow_config(project, Path(manifest["orfs"]["root"]),
                                    keys=("ROUTING_LAYER_ADJUSTMENT",), **pins)
    save("configuration.json", observation)
    report = audit(Path(args.source), source_sha256=args.source_sha256,
        path_id=args.path_id, campaign_id=args.campaign_id,
        flow_config={"ROUTING_LAYER_ADJUSTMENT": observation["values"]["ROUTING_LAYER_ADJUSTMENT"]},
        flow_design_id=observation["values"]["DESIGN_NAME"])
    save("bootstrap.json", report)
    candidate = StructuredRepairCandidate.from_dict(report["flow_binding_probe"]["candidate"])
    scripts = Path(__file__).resolve().parents[2] / "r2g-skills/signoff-loop/scripts/flow"
    inputs = _source_inputs([{"path": str(Path(p).resolve()), "sha256": _file_sha256(Path(p))}
                             for p in args.external_input])
    case = {"case_id": "flow-binding-smoke", "project_dir": str(project),
        "platform": observation["values"]["PLATFORM"], "target_check": "route",
        "orfs_root": manifest["orfs"]["root"], **{k: str(v) for k, v in pins.items()},
        "pdk_root": manifest["pdk"]["root"], "toolchain_root": manifest["toolchain_root"],
        "toolchain_manifest": str(Path(args.manifest).resolve()),
        "toolchain_digest": "sha256:" + manifest["manifest_digest"],
        "run_flow_script": str(scripts / "run_orfs.sh"),
        "fix_signoff_script": str(scripts / "fix_signoff.sh"),
        "oracle_digest": _digest({p.name: _file_sha256(p) for p in
            (scripts / "run_orfs.sh", scripts / "fix_signoff.sh")}),
        "source_inputs": list(inputs), "source_digest": _source_binding(project, inputs),
        "flow_config_observation": observation,
        "environment": {"ORFS_TIMEOUT": "300", "NUM_CORES": "2"}}
    save("case.json", case)
    for arm, proposal in (("baseline", None), ("treatment", candidate)):
        frozen = {**case, "execution_artifacts_dir": str(root / arm)}
        print("starting " + arm, flush=True)
        result = execute_orfs_candidate(proposal, frozen, 1)
        save(arm + "-result.json", result)
        print(arm + ": " + result["outcome"], flush=True)
    save("scope.json", {"scope": "training_design_execution_smoke_only",
        "full_signoff_established": False,
        "heldout_gain_established": False, "p13_evolution_established": False,
        "canonical_memory_mutation": "none", "production_mutation": "none"})


if __name__ == "__main__":
    main()
