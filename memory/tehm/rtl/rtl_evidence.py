"""RTL-evidence capture adapter (design doc 26 Phase 10, 21.2).

Reads an RTL project (``rtl/*.v`` + ``tb/*.v`` + ``manifest.json`` describing
the bug and its canonical rtl.* fix), applies the fix, verifies it with the
Icarus oracle (when available), and builds one canonical ExecutionRecord.

    before = buggy RTL source + failing target test
    action = rtl.GUARD_STRENGTHEN (the fix)
    after  = fixed RTL source + passing target + preserved regression
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from tehm.canonical.capture import CaptureReceipt, ExecutionRecord, capture
from tehm.rtl.rtl_actions import apply_rtl_action
from tehm.rtl.compatibility import profile_for_action
from tehm.rtl.rtl_graph import build_rtl_graph
from tehm.rtl.rtl_oracle import IcarusOracle
from tehm.rtl.verilog_parse import parse_verilog

RTL_EVIDENCE_VERSION = "rtl-evidence-v0.1"


def capture_rtl_fix(conn, store, project: Path, *,
                    oracle: IcarusOracle | None = None,
                    materialized_at: str | None = None) -> CaptureReceipt:
    """Capture one real RTL repair into the canonical store (verified)."""
    record = build_rtl_execution_record(project, oracle=oracle, store=store)
    return capture(conn, store, record, materialized_at=materialized_at)


def build_rtl_execution_record(project: Path, *, oracle: IcarusOracle | None = None,
                               store=None) -> ExecutionRecord:
    """Build the ExecutionRecord for an RTL project's canonical fix.

    ``store`` (optional artifact store) persists the RTL source slices as
    content-addressed artifacts referenced by the states.
    """
    project = Path(project)
    manifest = json.loads((project / "manifest.json").read_text())
    rtl_files = sorted((project / "rtl").glob("*.v"))
    if not rtl_files:
        raise ValueError(f"no rtl/*.v under {project}")
    buggy_source = rtl_files[0].read_text()

    fix = dict(manifest["fix"])
    parsed_modules = parse_verilog(buggy_source)
    if not parsed_modules:
        raise ValueError(f"no parseable RTL module under {project}")
    # Module identity is part of the structural action witness even when an
    # old fixture omitted it from manifest.json.  This lets crystallization
    # anti-unify the module slot and lets activation bind it explicitly.
    module_name = fix.get("module") or parsed_modules[0].name
    compatibility_profile = profile_for_action({
        "domain": fix.get("domain", "rtl.GUARD_STRENGTHEN"),
        "compatibility_profile": fix.get("compatibility_profile"),
    })
    action = {
        "domain": fix.get("domain", "rtl.GUARD_STRENGTHEN"),
        "transformation_family": fix.get("transformation_family", "GUARD_STRENGTHEN"),
        "payload": {k: fix[k] for k in (
                        "module", "source_state", "target_state", "add_condition",
                        "reg", "target", "replacement", "count",
                        "reset_signal", "signal", "case_expr",
                        "higher_label", "lower_label",
                        "compatibility_profile")
                    if k in fix},
    }
    action["payload"].setdefault("module", module_name)
    action["payload"]["compatibility_profile"] = compatibility_profile
    fixed_source, edit = apply_rtl_action(buggy_source, dict(fix))
    if edit.get("rewritten") == 0:
        raise ValueError(f"RTL fix did not rewrite the source: {edit}")

    verification = _verify_fixed(project, rtl_files, fixed_source, oracle)
    verdict = verification.get("verdict", "UNKNOWN")

    # Preserve parser-backed MODULE/FSM semantics in both state snapshots so
    # binding can later prove holes from a real structural context.
    before_graph = _rtl_structural_graph(
        buggy_source, design_id=manifest.get("design"),
        compatibility_profile=compatibility_profile)
    after_graph = _rtl_structural_graph(
        fixed_source, design_id=manifest.get("design"),
        compatibility_profile=compatibility_profile)

    before_artifacts = _store_source(store, "rtl", buggy_source)
    after_artifacts = _store_source(store, "rtl", fixed_source)

    before = {
        "repository_ref": None,
        "config": {},
        "reports": _rtl_report(verdict="fail"),
        "failure_signature": dict(manifest["bug"]),
        "artifacts": before_artifacts,
        "structural_graph": before_graph,
    }
    after = {
        "repository_ref": None,
        "config": {},
        "reports": _rtl_report(verdict="pass" if verdict == "PASS" else "fail"),
        "artifacts": after_artifacts,
        "structural_graph": after_graph,
    }
    delta = {
        "original_failure": "REMOVED" if verdict == "PASS" else "UNKNOWN",
        "first_divergence": {"before": 1, "after": 0} if verdict == "PASS"
                            else {"before": 1, "after": 1},
        "failing_tests": {"before": 1, "after": 0} if verdict == "PASS"
                         else {"before": 1, "after": 1},
        "created_regressions": verification.get("created_regressions") or [],
        "newly_observed_failures": verification.get("newly_observed_failures") or [],
    }
    return ExecutionRecord(
        record_id=f"rtl:{manifest['design']}",
        domain="rtl",
        project_id=manifest["design"],
        design_id=manifest["design"],
        lineage_id=manifest["design"],
        repository_ref=None,
        before=before,
        action=action,
        after=after,
        observation_delta=delta,
        verification=verification,
        episode={
            "episode_id": f"rtl_ep:{manifest['design']}",
            "mechanism_family": manifest.get("mechanism_family", "RTL_REPAIR"),
            "lineage_id": manifest["design"],
            "step_index": 0,
            "terminal_status": "VERIFIED_REPAIR" if verdict == "PASS" else "PARTIAL",
        },
    )


def _verify_fixed(project: Path, rtl_files: list, fixed_source: str,
                  oracle: IcarusOracle | None) -> dict:
    if oracle is None or not oracle.available:
        return {"verdict": "UNKNOWN", "oracle_type": "UNKNOWN",
                "reason": "icarus oracle not available"}
    verification_cfg = _load_json(project / "manifest.json").get("verification") or {}
    with tempfile.TemporaryDirectory(prefix="tehm_rtl_") as tmp:
        fixed_path = Path(tmp) / rtl_files[0].name
        fixed_path.write_text(fixed_source)
        other_rtl = [p for p in rtl_files if p.name != rtl_files[0].name]
        target_tb = project / verification_cfg.get("target_test", "tb/tb_handshake.v")
        regression_tb = project / verification_cfg.get("frozen_regression",
                                                       "tb/tb_basic.v")
        return oracle.verify([fixed_path, *other_rtl],
                             target_tb=target_tb if target_tb.exists() else None,
                             regression_tb=regression_tb if regression_tb.exists() else None)


def _rtl_report(verdict: str) -> dict:
    if verdict == "pass":
        return {"rtl": {"status": "clean", "total_violations": 0}}
    if verdict == "fail":
        return {"rtl": {"status": "violations", "total_violations": 1}}
    return {"rtl": {"status": "unknown"}}


def _store_source(store, kind: str, source: str) -> dict:
    if store is None:
        return {"rtl_slice": {"digest": None, "kind": kind, "inline": source[:200]}}
    manifest = store.put(kind, source.encode(), producer="tehm-rtl")
    return {"rtl_slice": manifest}


def _rtl_structural_graph(source: str, *, design_id: str | None,
                          compatibility_profile: str | None = None) -> dict:
    """Parse and merge all RTL modules into one deterministic graph artifact."""
    modules = parse_verilog(source)
    if not modules:
        raise ValueError("RTL source produced no parseable module for structural graph")
    graph = build_rtl_graph(modules[0], design_id=design_id,
                            compatibility_profile=compatibility_profile)
    for module in modules[1:]:
        other = build_rtl_graph(module, design_id=design_id)
        for node in other.nodes:
            graph.add_node(node["id"], node["kind"], node.get("label", ""),
                           node.get("attrs"))
        for edge in other.edges:
            graph.add_edge(edge["src"], edge["dst"], edge["kind"],
                           edge.get("attrs"))
    return graph.to_dict()


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
