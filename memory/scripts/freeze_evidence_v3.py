#!/usr/bin/env python3
"""Create the Evidence Contract v3 freeze from the current verified inputs."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "memory") not in sys.path:
    sys.path.insert(0, str(REPO / "memory"))

from evaluation.freeze_pointer import resolve_bundle
from tehm import db, honesty
from tehm.artifact_store import ArtifactStore
from tehm.sync import canonical_json, export_bundle, verify_bundle


V2 = Path("/data1/zhangdy/tehm-evidence-freeze-v2")
CANONICAL_V3 = resolve_bundle(require_exists=False)
V3 = CANONICAL_V3
M0_M8_EVAL = Path("/data1/zhangdy/tehm-campaigns/m0-m8-v4-evaluation")
TRIAL_ROOT = Path("/data1/zhangdy/tehm-campaigns/orfs-v20-tehm-trial-final")
PHYSICAL = Path("/data1/zhangdy/tehm-campaigns/orfs-v5-heldout-sky130hs-gcd7")
CALIBRATION = Path("/data1/zhangdy/tehm-campaigns/orfs-v9-physical-calibration")
TRAINING_MANIFESTS = (
    Path("/data1/zhangdy/tehm-campaigns/orfs-v2-diversity/campaign_manifest.json"),
    Path("/data1/zhangdy/tehm-campaigns/orfs-v4-add-designs/campaign_manifest.json"),
    Path("/data1/zhangdy/tehm-campaigns/orfs-v6-density-sky130hs-gcd-low/campaign_manifest.json"),
    Path("/data1/zhangdy/tehm-campaigns/orfs-v7-density-sky130hs-gcd-low2/campaign_manifest.json"),
    Path("/data1/zhangdy/tehm-campaigns/orfs-v8-density-sky130hs-gcd-high/campaign_manifest.json"),
    Path("/data1/zhangdy/tehm-campaigns/orfs-v10-density-sky130hd-fifo/campaign_manifest.json"),
    Path("/data1/zhangdy/tehm-campaigns/orfs-v11-sky130hs-counter-density/campaign_manifest.json"),
    Path("/data1/zhangdy/tehm-campaigns/orfs-v14b-sky130hs-stream-density/campaign_manifest.json"),
    Path("/data1/zhangdy/tehm-campaigns/orfs-v15b-sky130hd-gcd-physical/campaign_manifest.json"),
    Path("/data1/zhangdy/tehm-campaigns/orfs-v15c-sky130hd-gcd-strict/campaign_manifest.json"),
    Path("/data1/zhangdy/tehm-campaigns/orfs-v17b-sky130hd-stream-lowutil/campaign_manifest.json"),
    Path("/data1/zhangdy/tehm-campaigns/orfs-v18-sky130hd-stream-alt-placement/campaign_manifest.json"),
    Path("/data1/zhangdy/tehm-campaigns/orfs-v19-ihp-stream-physical/campaign_manifest.json"),
    Path("/data1/zhangdy/tehm-campaigns/orfs-v20-ihp-stream-density/campaign_manifest.json"),
)
CURRENT_DB = REPO / "memory/tehm.sqlite"
V3_VERSION = "tehm-evidence-freeze-v3"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _git(*args: str, binary: bool = False) -> bytes:
    return subprocess.check_output(["git", "-C", str(REPO), *args],
                                   stderr=subprocess.STDOUT)


def _normalize_generated_docs(rel: str, data: bytes) -> bytes:
    """Exclude the manifest-rendered block from the source-state digest.

    The block is a derived view of this very manifest.  Hashing it as source
    would create an impossible self-reference (manifest digest -> docs ->
    source digest -> manifest digest).
    """
    if not rel.endswith(("memory/README.md", "memory/docs/Typed_Executable_Hardware_Memory_R2G.md")):
        return data
    text = data.decode("utf-8")
    text = re.sub(r"<!-- TEHM_EVIDENCE_V3_START -->.*?<!-- TEHM_EVIDENCE_V3_END -->",
                  "<!-- TEHM_EVIDENCE_V3_START -->\n<!-- TEHM_EVIDENCE_V3_END -->",
                  text, flags=re.S)
    return text.encode("utf-8")


def source_state() -> dict:
    head = _git("rev-parse", "HEAD").decode().strip()
    changed = [x.decode() for x in _git("diff", "HEAD", "--name-only", "-z").split(b"\0") if x]
    diff_rows = []
    for rel in sorted(changed):
        current = REPO / rel
        current_bytes = _normalize_generated_docs(rel, current.read_bytes()) if current.is_file() else None
        try:
            base_bytes = _git("show", f"HEAD:{rel}")
        except subprocess.CalledProcessError:
            base_bytes = None
        if base_bytes is not None:
            base_bytes = _normalize_generated_docs(rel, base_bytes)
        diff_rows.append({"path": rel,
                          "head_sha256": _sha256_bytes(base_bytes) if base_bytes is not None else None,
                          "working_sha256": _sha256_bytes(current_bytes) if current_bytes is not None else None})
    raw_untracked = _git("ls-files", "--others", "--exclude-standard", "-z")
    untracked = []
    for raw in raw_untracked.split(b"\0"):
        if not raw:
            continue
        rel = raw.decode()
        path = REPO / rel
        if not path.is_file():
            continue
        data = _normalize_generated_docs(rel, path.read_bytes())
        untracked.append({"path": rel, "sha256": _sha256_bytes(data),
                          "size": len(data)})
    untracked.sort(key=lambda item: item["path"])
    # A generated evidence block is a derived view, not source dirtiness.  It
    # is normalized above, so only rows that still differ logically contribute
    # to the checkpoint state.
    logical_diff_rows = [row for row in diff_rows
                         if row["head_sha256"] != row["working_sha256"]]
    logical_untracked = [row for row in untracked
                         if not row["path"].endswith(
                             "memory/docs/Typed_Executable_Hardware_Memory_R2G.md")]
    logical_status = {"tracked": logical_diff_rows, "untracked": logical_untracked}
    state = {
        "head": head,
        "dirty": bool(logical_diff_rows or logical_untracked),
        "status_sha256": _sha256_bytes(canonical_json(logical_status)),
        "tracked_diff_sha256": _sha256_bytes(canonical_json(logical_diff_rows)),
        "untracked_files": logical_untracked,
    }
    state["workspace_state_sha256"] = _sha256_bytes(canonical_json(state))
    return state


def run_tests() -> dict:
    proc = subprocess.run(
        ["python3", "-m", "pytest", "-q", "memory/tests"], cwd=REPO,
        text=True, capture_output=True)
    output = (proc.stdout + proc.stderr).strip()
    match = re.search(r"(?P<n>\d+) passed(?: in [^\n]+)?", output)
    passed = int(match.group("n")) if match else None
    if proc.returncode != 0 or passed is None or passed <= 0:
        raise RuntimeError(f"expected a non-empty passing test suite, got rc={proc.returncode}: {output}")
    return {"command": "python3 -m pytest -q memory/tests", "returncode": proc.returncode,
            "passed": passed, "output": output}


def _read(path: Path) -> dict:
    return json.loads(path.read_text())


def _snapshot_paths() -> tuple[Path, Path]:
    """Use the real-ORFS trial snapshot once it has been produced."""
    trial_db = TRIAL_ROOT / "tehm.sqlite"
    trial_artifacts = TRIAL_ROOT / "artifacts"
    if trial_db.is_file() and trial_artifacts.is_dir():
        return trial_db, trial_artifacts
    return V2 / "closed_loop/tehm.sqlite", V2 / "closed_loop/artifacts"


def _snapshot_manifest(snapshot_db: Path, staging: Path) -> Path:
    source = _read(V2 / "bundle_manifest.json")
    conn = db.connect_read_only(snapshot_db)
    try:
        for table in ("tehm_states", "tehm_transitions", "tehm_episodes", "tehm_views",
                      "tehm_rules", "tehm_rule_status", "tehm_activations",
                      "tehm_trials", "tehm_physical_effects"):
            source.setdefault("counts", {})[table] = conn.execute(
                f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()
    path = staging / "source_freeze_manifest.json"
    path.write_bytes(canonical_json(source))
    return path


def _evidence_inputs(source_manifest: Path) -> list[tuple[Path, str]]:
    eval_root = M0_M8_EVAL
    if not (eval_root / "m0_m8_v2_report.json").is_file():
        eval_root = V2 / "evaluation"
    pairs = [
        (eval_root / "m0_m8_v2_report.json", "evaluation/m0_m8_v2_report.json"),
        (eval_root / "m0_m8_v2_report.md", "evaluation/m0_m8_v2_report.md"),
        (eval_root / "heldout_task_manifest_v2.json", "evaluation/heldout_task_manifest_v2.json"),
        (source_manifest, "evaluation/source_freeze_v2_manifest.json"),
        (PHYSICAL / "heldout_lineage_report.json", "evidence/physical/heldout_lineage_report.json"),
        (PHYSICAL / "calibration_samples.json", "evidence/physical/calibration_samples.json"),
        (PHYSICAL / "campaign_manifest.json", "evidence/physical/campaign_manifest.json"),
        (PHYSICAL / "campaign_state.json", "evidence/physical/campaign_state.json"),
        (PHYSICAL / "features_report.json", "evidence/physical/features_report.json"),
        (CALIBRATION / "calibration_report.json", "evidence/physical/calibration_report.json"),
        (CALIBRATION / "parametric_readiness.json", "evidence/physical/parametric_readiness.json"),
        (CALIBRATION / "campaign_manifest.json", "evidence/physical/calibration_manifest.json"),
        (REPO / "memory/evaluation/orfs_infra_recovery_v1.json", "evidence/campaigns/orfs_infra_recovery_v1.json"),
        (Path("/data1/zhangdy/tehm-campaigns/orfs-v3-contexts/context_coverage_audit.json"),
         "evidence/campaigns/context_coverage_audit.json"),
        (Path("/data1/zhangdy/tehm-campaigns/orfs-v3-contexts/physical_graph_contexts.json"),
         "evidence/campaigns/physical_graph_contexts.json"),
        (Path("/data1/zhangdy/tehm-campaigns/orfs-v4-add-designs/add_designs_report.json"),
         "evidence/campaigns/add_designs_report.json"),
        (Path("/data1/zhangdy/tehm-campaigns/orfs-v10-density-sky130hd-fifo/campaign_manifest.json"),
         "evidence/campaigns/orfs-v10_density_sky130hd_fifo_manifest.json"),
        (Path("/data1/zhangdy/tehm-campaigns/orfs-v10-density-sky130hd-fifo/physical_graph_contexts.json"),
         "evidence/campaigns/orfs-v10_density_sky130hd_fifo_graph_contexts.json"),
        (Path("/data1/zhangdy/tehm-campaigns/orfs-v10-density-sky130hd-fifo/add_designs_report.json"),
         "evidence/campaigns/orfs-v10_density_sky130hd_fifo_report.json"),
        (Path("/data1/zhangdy/tehm-campaigns/orfs-v11-sky130hs-counter-density/campaign_manifest.json"),
         "evidence/campaigns/orfs-v11_sky130hs_counter_density_manifest.json"),
        (Path("/data1/zhangdy/tehm-campaigns/orfs-v11-sky130hs-counter-density/physical_graph_contexts.json"),
         "evidence/campaigns/orfs-v11_sky130hs_counter_density_graph_contexts.json"),
        (Path("/data1/zhangdy/tehm-campaigns/orfs-v11-sky130hs-counter-density/add_designs_report.json"),
         "evidence/campaigns/orfs-v11_sky130hs_counter_density_report.json"),
        (Path("/data1/zhangdy/tehm-campaigns/orfs-v14b-sky130hs-stream-density/campaign_manifest.json"),
         "evidence/campaigns/orfs-v14b_sky130hs_stream_density_manifest.json"),
        (Path("/data1/zhangdy/tehm-campaigns/orfs-v14b-sky130hs-stream-density/physical_graph_contexts.json"),
         "evidence/campaigns/orfs-v14b_sky130hs_stream_density_graph_contexts.json"),
        (Path("/data1/zhangdy/tehm-campaigns/orfs-v14b-sky130hs-stream-density/add_designs_report.json"),
         "evidence/campaigns/orfs-v14b_sky130hs_stream_density_report.json"),
        (Path("/data1/zhangdy/tehm-campaigns/orfs-v15b-sky130hd-gcd-physical/campaign_manifest.json"),
         "evidence/campaigns/orfs-v15b_sky130hd_gcd_physical_manifest.json"),
        (Path("/data1/zhangdy/tehm-campaigns/orfs-v15b-sky130hd-gcd-physical/physical_graph_contexts.json"),
         "evidence/campaigns/orfs-v15b_sky130hd_gcd_physical_graph_contexts.json"),
        (Path("/data1/zhangdy/tehm-campaigns/orfs-v15b-sky130hd-gcd-physical/add_designs_report.json"),
         "evidence/campaigns/orfs-v15b_sky130hd_gcd_physical_report.json"),
        (Path("/data1/zhangdy/tehm-campaigns/orfs-v15c-sky130hd-gcd-strict/campaign_manifest.json"),
         "evidence/campaigns/orfs-v15c_sky130hd_gcd_strict_manifest.json"),
        (Path("/data1/zhangdy/tehm-campaigns/orfs-v15c-sky130hd-gcd-strict/physical_graph_contexts.json"),
         "evidence/campaigns/orfs-v15c_sky130hd_gcd_strict_graph_contexts.json"),
        (Path("/data1/zhangdy/tehm-campaigns/orfs-v15c-sky130hd-gcd-strict/add_designs_report.json"),
         "evidence/campaigns/orfs-v15c_sky130hd_gcd_strict_report.json"),
        (Path("/data1/zhangdy/tehm-campaigns/orfs-v17b-sky130hd-stream-lowutil/campaign_manifest.json"),
         "evidence/campaigns/orfs-v17b_sky130hd_stream_lowutil_manifest.json"),
        (Path("/data1/zhangdy/tehm-campaigns/orfs-v17b-sky130hd-stream-lowutil/physical_graph_contexts.json"),
         "evidence/campaigns/orfs-v17b_sky130hd_stream_lowutil_graph_contexts.json"),
        (Path("/data1/zhangdy/tehm-campaigns/orfs-v17b-sky130hd-stream-lowutil/add_designs_report.json"),
         "evidence/campaigns/orfs-v17b_sky130hd_stream_lowutil_report.json"),
        (Path("/data1/zhangdy/tehm-campaigns/orfs-v18-sky130hd-stream-alt-placement/campaign_manifest.json"),
         "evidence/campaigns/orfs-v18_sky130hd_stream_alt_placement_manifest.json"),
        (Path("/data1/zhangdy/tehm-campaigns/orfs-v18-sky130hd-stream-alt-placement/physical_graph_contexts.json"),
         "evidence/campaigns/orfs-v18_sky130hd_stream_alt_placement_graph_contexts.json"),
        (Path("/data1/zhangdy/tehm-campaigns/orfs-v18-sky130hd-stream-alt-placement/add_designs_report.json"),
         "evidence/campaigns/orfs-v18_sky130hd_stream_alt_placement_report.json"),
        (Path("/data1/zhangdy/tehm-campaigns/orfs-v19-ihp-stream-physical/campaign_manifest.json"),
         "evidence/campaigns/orfs-v19_ihp_stream_physical_manifest.json"),
        (Path("/data1/zhangdy/tehm-campaigns/orfs-v19-ihp-stream-physical/physical_graph_contexts.json"),
         "evidence/campaigns/orfs-v19_ihp_stream_physical_graph_contexts.json"),
        (Path("/data1/zhangdy/tehm-campaigns/orfs-v19-ihp-stream-physical/add_designs_report.json"),
         "evidence/campaigns/orfs-v19_ihp_stream_physical_report.json"),
        (Path("/data1/zhangdy/tehm-campaigns/orfs-v20-ihp-stream-density/campaign_manifest.json"),
         "evidence/campaigns/orfs-v20_ihp_stream_density_manifest.json"),
        (Path("/data1/zhangdy/tehm-campaigns/orfs-v20-ihp-stream-density/physical_graph_contexts.json"),
         "evidence/campaigns/orfs-v20_ihp_stream_density_graph_contexts.json"),
        (Path("/data1/zhangdy/tehm-campaigns/orfs-v20-ihp-stream-density/add_designs_report.json"),
         "evidence/campaigns/orfs-v20_ihp_stream_density_report.json"),
    ]
    trial_report = TRIAL_ROOT / "trial_report.json"
    if trial_report.is_file():
        pairs.append((trial_report, "evidence/trials/real_orfs_tehm_trial_report.json"))
        for receipt in sorted((TRIAL_ROOT / "receipts").rglob("*")):
            if receipt.is_file():
                pairs.append((receipt, "evidence/trials/" + receipt.relative_to(TRIAL_ROOT).as_posix()))
    missing = [str(src) for src, _ in pairs if not src.is_file()]
    if missing:
        raise FileNotFoundError(f"missing freeze evidence: {missing}")
    return pairs


def _firewall() -> dict:
    v2 = _read(V2 / "bundle_manifest.json")
    cal = _read(CALIBRATION / "campaign_manifest.json")
    training = sorted(set(v2.get("training_lineages", [])) |
                      set(cal.get("firewall", {}).get("training_lineages", [])))
    for manifest_path in TRAINING_MANIFESTS:
        if manifest_path.is_file():
            manifest = _read(manifest_path)
            training = sorted(set(training) |
                              set(manifest.get("firewall", {}).get("training_lineages", [])))
    heldout = sorted(set(v2.get("heldout_lineages", [])) |
                     set(cal.get("firewall", {}).get("heldout_lineages", [])))
    physical_lineage = "orfs-heldout-v5:sky130hs:gcd:base3"
    heldout = sorted(set(heldout) | {physical_lineage})
    ab_lineages = {"req_ack_bug", "req_ack_bug2", "req_ack_bug3", "req_ack_bug4",
                   "valid_ready_bug", "fifo_space_bug"}
    trial_report = TRIAL_ROOT / "trial_report.json"
    if trial_report.is_file():
        trial = _read(trial_report)
        ab_lineages.update(trial.get("firewall", {}).get("ab_lineages", []))
    return {
        "training_lineages": training,
        "heldout_lineages": heldout,
        "ab_lineages": sorted(ab_lineages),
        "disjoint": not bool(set(training) & set(heldout)),
        "heldout_not_captured": True,
        "mutation_policy": "held-out evaluation and calibration may mutate only temporary copies",
    }


def _same_tree(left: Path, right: Path) -> bool:
    left_files = {p.relative_to(left).as_posix() for p in left.rglob("*") if p.is_file()}
    right_files = {p.relative_to(right).as_posix() for p in right.rglob("*") if p.is_file()}
    return left_files == right_files and all(
        (left / rel).read_bytes() == (right / rel).read_bytes()
        for rel in sorted(left_files))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", type=Path,
                    default=CANONICAL_V3)
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args(argv)
    output = args.output.resolve()
    if output.exists() and not args.overwrite:
        raise SystemExit(f"refusing to overwrite existing freeze: {output}; use --overwrite")
    if output.exists():
        shutil.rmtree(output)
    staging = output.parent / f".{output.name}.staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    tests = run_tests()
    firewall = _firewall()
    calibration = _read(CALIBRATION / "calibration_report.json")
    if calibration.get("physical_memory_count_before") != calibration.get("physical_memory_count_after"):
        raise RuntimeError("calibration input reports a physical-memory mutation")
    metadata = {
        "evidence_contract": V3_VERSION,
        "source_state": source_state(),
        "firewall": firewall,
        "expected": {
            "tests_passed": tests["passed"],
            "calibration_memory_count": calibration.get("physical_memory_count_before"),
        },
        "inputs": {
            "rtl_freeze": "tehm-evidence-freeze-v2",
            "physical_heldout": "orfs-heldout-v5:sky130hs:gcd:base3",
            "calibration_version": calibration.get("version"),
        },
    }
    snapshot_db, snapshot_artifacts = _snapshot_paths()
    source_manifest = _snapshot_manifest(snapshot_db, staging)
    test_path = staging / "pytest_memory_tests.json"
    test_path.write_bytes(canonical_json(tests))
    base_pairs = _evidence_inputs(source_manifest) + [(test_path, "evidence/tests/pytest_memory_tests.json")]

    # Build a provisional deterministic bundle so H11 can be audited before
    # its report is added as a content-addressed evidence entry.
    provisional = output.parent / f".{output.name}.provisional"
    if provisional.exists():
        shutil.rmtree(provisional)
    export_bundle(output=provisional, db_path=snapshot_db,
                  artifact_root=snapshot_artifacts,
                  evidence_files=base_pairs, metadata=metadata)
    checked = verify_bundle(provisional)
    if not checked["ok"]:
        raise RuntimeError(checked["detail"])
    conn = db.connect_read_only(provisional / "closed_loop/tehm.sqlite")
    try:
        all_ok, audit = honesty.run_all(
            conn, ArtifactStore(provisional / "closed_loop/artifacts"),
            provisional / "closed_loop/tehm.sqlite", firewall=firewall,
            bundle_path=provisional)
    finally:
        conn.close()
    if not all_ok:
        raise RuntimeError(f"H1-H12 audit failed: {audit}")
    audit_path = staging / "honesty_report.json"
    audit_path.write_bytes(canonical_json(audit))

    reproduce = staging / "reproduce.sh"
    reproduce.write_text("""#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")" && pwd)
REPO=${TEHM_REPO_ROOT:-/data1/zhangdy/r2g-skills}
export PYTHONPATH="$REPO/memory${PYTHONPATH:+:$PYTHONPATH}"
pytest_log=$(mktemp)
replay_log=$(mktemp)
m0_report=$(mktemp)
trap 'rm -f "$pytest_log" "$replay_log" "$m0_report"' EXIT
python3 -m pytest "$REPO/memory/tests" -q | tee "$replay_log" | tee "$pytest_log"
expected_tests=$(python3 - "$ROOT/evidence/tests/pytest_memory_tests.json" <<'PY'
import json, sys
print(json.loads(open(sys.argv[1]).read())["passed"])
PY
)
grep -Eq "(^|[[:space:]])${expected_tests} passed([[:space:]]|$)" "$pytest_log"
python3 "$REPO/memory/scripts/run_controlled_m0_m8_v2.py" \
  --snapshot "$ROOT" --output "$m0_report" >/dev/null
python3 "$ROOT/tools/verify_evidence_freeze_v3.py" \
  --bundle "$ROOT" --m0-m8-report "$m0_report"
""")
    reproduce.chmod(0o755)
    final_pairs = base_pairs + [
        (audit_path, "evidence/audit/honesty_report.json"),
        (reproduce, "reproduce.sh"),
        (REPO / "memory/scripts/verify_evidence_freeze_v3.py",
         "tools/verify_evidence_freeze_v3.py"),
    ]
    export_bundle(output=output, db_path=snapshot_db,
                  artifact_root=snapshot_artifacts,
                  evidence_files=final_pairs, metadata=metadata)
    final = verify_bundle(output)
    if not final["ok"]:
        raise RuntimeError(final["detail"])
    # Verify the report against the final bundle and prove an actual round trip.
    import tempfile
    with tempfile.TemporaryDirectory(prefix="tehm-v3-export-") as temp:
        temp = Path(temp)
        imported = temp / "imported"
        reexported = temp / "reexported"
        from tehm.sync import import_bundle, reexport_bundle
        import_bundle(bundle=output, output=imported)
        reexport_bundle(source_bundle=imported, output=reexported)
        if not _same_tree(output, reexported):
            raise RuntimeError("provisional/final export round trip is not byte-stable")
    # The final verifier is deliberately run using the copied report and code.
    subprocess.check_call(["python3", str(REPO / "memory/scripts/verify_evidence_freeze_v3.py"),
                           "--bundle", str(output)])
    shutil.rmtree(staging)
    shutil.rmtree(provisional)
    print(json.dumps({"bundle": str(output), "manifest": str(output / "bundle_manifest.json"),
                      "audit": audit, "source_state": metadata["source_state"]},
                     indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
