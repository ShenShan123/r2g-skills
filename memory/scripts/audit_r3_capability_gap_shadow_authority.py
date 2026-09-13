"""Actual controlled-L3 strict Knowledge review in a disposable RAM copy.

The review is not lifecycle validation or production admission. It retains
historical provenance explicitly and rolls back its authority ledger writes.
"""
from pathlib import Path
import argparse
import sqlite3

from scripts.audit_r3_capability_gap_runtime_bridge import ROOT, SCRIPTS, _git, verify_epoch_maps
from scripts.build_p13_interference_policy_views import _runtime_code_binding, _self_digest
from scripts.build_p13_interference_source_bound_plan import _load_json, _sha256
from scripts.build_r3_orfs_interference_source_bound_inputs import _digest, _write
from tehm import db
from tehm.knowledge.registry import get_knowledge_by_object_id
from tehm.knowledge.authority import record_knowledge_authority, verify_knowledge_authority
import hashlib


def _ref(path):
    return {"path": str(path), "sha256": _sha256(path)}


def audit_shadow_authority(bridge_path, controlled_path, cold_path, *, output):
    bridge_path, controlled_path, cold_path, output = [Path(p).resolve() for p in (
        bridge_path, controlled_path, cold_path, output)]
    if output.exists():
        raise ValueError("authority audit output must be new")
    bridge, controlled, cold = [_load_json(path, "source-bound review input") for path in (
        bridge_path, controlled_path, cold_path)]
    for report in (bridge, controlled, cold):
        _self_digest(report, "report_digest")
    if (bridge.get("version") != "r3-capability-gap-runtime-origin-bridge-v1" or bridge.get("status") != "PASS" or
            controlled.get("version") != "r3-source-bound-controlled-gap-shadow-add-v2" or
            cold.get("version") != "r3-source-bound-controlled-gap-cold-replay-v1" or cold.get("status") != "PASS" or
            cold.get("source_report_digest") != controlled["report_digest"] or
            len(cold.get("checks", {})) != 19 or any(value is not True for value in cold["checks"].values())):
        raise ValueError("exact historical controlled-L3 and independent cold replay required")
    freeze = _load_json(Path(bridge["historical_input_freeze"]["path"]), "historical input freeze")
    _self_digest(freeze, "report_digest")
    current = _runtime_code_binding()
    if (bridge["origin_runtime_binding"] != freeze["runtime_generation_binding"] or
            bridge["current_runtime_binding"] != current or
            controlled["execution_freeze"]["runtime_generation_digest"] != freeze["runtime_generation_binding"]["binding_digest"] or
            controlled["execution_freeze"]["source_database"] != freeze["source_database"] or
            controlled["execution_freeze"]["heldout_consumed_by_learner"] is not False):
        raise ValueError("origin/current runtime or training source provenance mismatch")
    commit = bridge["origin_commit"]
    names = _git(ROOT.parent, commit, "ls-tree", "-rz", "--name-only", commit, "--", "memory").split(b"\0")
    corpus = [name.decode() for name in names if name and (name == b"memory/contracts.py" or
        (name.startswith(b"memory/tehm/") and name.endswith(b".py")) or
        name.decode().removeprefix("memory/") in SCRIPTS)]
    git_files = {name.removeprefix("memory/"): "sha256:" + hashlib.sha256(
        _git(ROOT.parent, commit, "show", commit + ":" + name)).hexdigest() for name in corpus}
    verify_epoch_maps(bridge["origin_runtime_binding"], current, git_files,
        origin_root=str(Path(freeze["compiler_source_binding"]["path"]).parent.parent), current_root=str(ROOT))
    refs = [_ref(p) for p in (bridge_path, controlled_path, cold_path)] + [
        bridge["historical_input_freeze"], bridge["bridge_source_binding"], bridge["backend_adapter_binding"],
        cold["auditor_source_binding"], controlled["execution_freeze"]["producer_source"],
        controlled["execution_freeze"]["original_l1_projection"], freeze["source_database"],
        controlled["base_snapshot"], controlled["shadow_snapshot"], controlled["restored_snapshot"],
        *controlled["execution_freeze"]["acquisitions"]]
    for ref in refs:
        if _sha256(Path(ref["path"])) != ref["sha256"]:
            raise ValueError("historical source, receipt or audit producer drift")
    disk = db.connect_read_only(Path(controlled["shadow_snapshot"]["path"]))
    ram = sqlite3.connect(":memory:")
    ram.row_factory = sqlite3.Row
    try:
        disk.backup(ram)
        original = _digest("\n".join(ram.iterdump()))
        if original != controlled["shadow_snapshot"]["logical_digest"]:
            raise ValueError("historical shadow snapshot logical digest mismatch")
        claim = get_knowledge_by_object_id(ram, controlled["knowledge"]["object_id"], target_scope="rtl_target")
        if claim.status != "candidate" or claim.content_digest != controlled["knowledge"]["content_digest"]:
            raise ValueError("review must start from original controlled candidate")
        ram.execute("SAVEPOINT disposable_strict_review")
        authority = record_knowledge_authority(ram, claim, target_scope="rtl_target")
        verified = verify_knowledge_authority(ram, authority)
        if not authority.eligible or verified.get("eligible") is not True:
            raise ValueError("strict database-bound Knowledge authority rejected actual evidence")
        ram.execute("ROLLBACK TO disposable_strict_review")
        ram.execute("RELEASE disposable_strict_review")
        if _digest("\n".join(ram.iterdump())) != original:
            raise ValueError("disposable strict review failed full-state rollback")
    finally:
        ram.close()
        disk.close()
    if _runtime_code_binding() != current or any(_sha256(Path(r["path"])) != r["sha256"] for r in refs):
        raise ValueError("runtime or provenance drift during strict review")
    report = {"version": "r3-controlled-gap-disposable-strict-authority-audit-v1", "status": "PASS",
        "inputs": refs, "authority_receipt": authority.to_dict(), "authority_replay": verified,
        "auditor_source_binding": _ref(Path(__file__).resolve()),
        "current_runtime_binding": current, "historical_origin_commit": commit,
        "full_state_rollback_verified": True, "read_only_disk_audit": True,
        "authority_receipt_persisted_in_canonical_memory": False, "knowledge_validated": False,
        "asset_promoted": False, "heldout_oracle_executed": False, "production_runtime_imported": False,
        "promotion_attempted": False, "provider_calls": 0,
        "required_next_evidence": ["strict receipt recorded and consumed in disposable policy staging",
            "prospective source-only policy freeze", "actual heldout/remove-delta/non-target/rollback",
            "independent capability attribution"]}
    report["report_digest"] = _digest(report)
    _write(output, report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-bridge", required=True)
    parser.add_argument("--controlled-report", required=True)
    parser.add_argument("--cold-replay", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    report = audit_shadow_authority(args.runtime_bridge, args.controlled_report, args.cold_replay, output=args.output)
    print(report["report_digest"])


if __name__ == "__main__":
    main()
