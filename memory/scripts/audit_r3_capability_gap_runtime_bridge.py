"""Explicit historical-origin/current-selector epoch bridge for Revision3.

Never replaces an old whole-corpus pin with current code. Historical origin
bytes are checked against a full Git commit; current changes are restricted
to this selector integration. This is evidence provenance, not a new unseen
study, L4 attribution, calibration or permission to enter production.
"""
from pathlib import Path, PurePosixPath
import argparse
import hashlib
import re
import subprocess

from scripts.build_p13_interference_policy_views import _runtime_code_binding, _self_digest
from scripts.build_p13_interference_source_bound_plan import _load_json, _sha256
from scripts.build_r3_orfs_interference_source_bound_inputs import _digest, _write
from scripts.prepare_r3_source_bound_capability_gap import _actual_routes

ROOT = Path(__file__).resolve().parents[1]
MODIFIED = frozenset({"tehm/retrieval/asset_selector.py",
    "tehm/retrieval/structured_candidate.py", "tehm/evaluation/rtl_candidate_oracle.py"})
ADDED = frozenset({"tehm/assets/source_selection.py"})
SCRIPTS = frozenset({"scripts/" + name for name in (
    "build_r3_orfs_interference_source_bound_inputs.py", "build_p13_interference_reason_bundle.py",
    "build_p13_shadow_trigger_report.py", "build_p13_interference_source_bound_plan.py",
    "audit_p13_interference_shadow_view.py", "build_p13_interference_policy_views.py",
    "run_orfs_interference_policy_view.py", "run_orfs_p12_cohort.py")})


def _git(repo, commit, *args):
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise ValueError("origin requires an immutable full commit SHA")
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, timeout=60).stdout


def _map(binding, recorded_root):
    _self_digest(binding, "binding_digest")
    if binding.get("version") != "p13-interference-runtime-generation-binding-v1":
        raise ValueError("unsupported runtime binding version")
    root = PurePosixPath(recorded_root)
    mapped = {}
    for ref in binding["files"]:
        path = PurePosixPath(ref["path"])
        if ".." in path.parts or not path.is_absolute():
            raise ValueError("runtime origin path must be absolute and normalized")
        relative = str(path.relative_to(root))
        if relative in mapped:
            raise ValueError("duplicate runtime origin file")
        mapped[relative] = ref["sha256"]
    return mapped


def verify_epoch_maps(origin, current, git_files, *, origin_root, current_root):
    """Verify exact old corpus coverage and restricted new code differences."""
    old, new = _map(origin, origin_root), _map(current, current_root)
    if old != git_files:
        raise ValueError("historical whole-corpus binding does not match Git origin")
    removed = set(old) - set(new)
    added = set(new) - set(old)
    changed = {name for name in set(old) & set(new) if old[name] != new[name]}
    if removed or added != ADDED or changed != MODIFIED:
        raise ValueError("current generation is not the exact selector-only integration")
    return {"modified_files": sorted(changed), "added_files": sorted(added),
            "unchanged_file_count": len(set(old) & set(new)) - len(changed)}


def audit_runtime_bridge(freeze_path, *, origin_commit, output):
    freeze_path, output = Path(freeze_path).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("bridge output must be new; consumed evidence is immutable")
    freeze = _load_json(freeze_path, "historical input freeze")
    freeze_sha = _sha256(freeze_path)
    _self_digest(freeze, "report_digest")
    if freeze.get("version") != "r3-source-bound-capability-gap-input-freeze-v3":
        raise ValueError("requires exact consumed r3 input generation")
    repo = ROOT.parent
    commit_bytes = _git(repo, origin_commit, "rev-parse", origin_commit + "^{commit}")
    if commit_bytes.decode().strip() != origin_commit:
        raise ValueError("origin commit identity mismatch")
    tree = _git(repo, origin_commit, "ls-tree", "-rz", "--name-only", origin_commit, "--", "memory")
    names = [name.decode() for name in tree.split(b"\0") if name]
    corpus = [name for name in names if name == "memory/contracts.py" or
        (name.startswith("memory/tehm/") and name.endswith(".py")) or name.removeprefix("memory/") in SCRIPTS]
    git_files = {name.removeprefix("memory/"): "sha256:" + hashlib.sha256(
        _git(repo, origin_commit, "show", origin_commit + ":" + name)).hexdigest() for name in corpus}
    origin_root = str(Path(freeze["compiler_source_binding"]["path"]).parent.parent)
    current = _runtime_code_binding()
    differences = verify_epoch_maps(freeze["runtime_generation_binding"], current, git_files,
                                   origin_root=origin_root, current_root=str(ROOT))
    refs = [freeze["preregistration"], freeze["compiler_source_binding"], freeze["source_database"],
            *freeze["oracle_tool_bindings"].values()]
    for case in freeze["cases"]:
        refs.extend([case["project_manifest"], *case["rtl_inputs"], *case["verification_inputs"].values()])
        if case["learner_eligible"] is not (case["role"] == "training"):
            raise ValueError("historical case learner boundary mismatch")
    for ref in refs:
        if _sha256(Path(ref["path"])) != ref["sha256"]:
            raise ValueError("historical source, tool, compiler or case input drift")
    routes, logical = _actual_routes(Path(freeze["source_database"]["path"]), freeze["cases"])
    if (routes != freeze["actual_preexecution_routing"] or
            logical != freeze["source_database"]["logical_digest"]):
        raise ValueError("actual source routing or logical state does not replay")
    if (_runtime_code_binding() != current or _sha256(freeze_path) != freeze_sha or
            any(_sha256(Path(ref["path"])) != ref["sha256"] for ref in refs)):
        raise ValueError("evidence or runtime drift during bridge audit")
    report = {"version": "r3-capability-gap-runtime-origin-bridge-v1", "status": "PASS",
        "historical_input_freeze": {"path": str(freeze_path), "sha256": _sha256(freeze_path),
                                    "report_digest": freeze["report_digest"]},
        "origin_commit": origin_commit, "origin_runtime_binding": freeze["runtime_generation_binding"],
        "current_runtime_binding": current, "differences": differences,
        "bridge_source_binding": {"path": str(Path(__file__).resolve()), "sha256": _sha256(Path(__file__))},
        "backend_adapter_binding": {"path": str(ROOT / "tehm_backend.py"),
                                    "sha256": _sha256(ROOT / "tehm_backend.py")},
        "historical_evidence_not_rewritten": True, "current_runtime_identical_to_origin": False,
        "shared_parser_operator_locator_oracle_unchanged": True,
        "actual_original_source_route_replayed": True, "source_database_unchanged": True,
        "fresh_unseen_study": False, "heldout_oracle_executed": False,
        "knowledge_validated": False, "asset_promoted": False,
        "production_runtime_imported": False, "promotion_attempted": False, "provider_calls": 0,
        "required_next_evidence": ["strict Knowledge review and disposable shadow policy views",
            "prospective policy freeze bound to this epoch and historical origin receipts",
            "actual source-only heldout, remove-delta, non-target and full rollback",
            "independent attribution and portable evidence bundle"]}
    report["report_digest"] = _digest(report)
    _write(output, report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--historical-input-freeze", required=True)
    parser.add_argument("--origin-commit", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    report = audit_runtime_bridge(args.historical_input_freeze, origin_commit=args.origin_commit, output=args.output)
    print(report["report_digest"])


if __name__ == "__main__":
    main()
