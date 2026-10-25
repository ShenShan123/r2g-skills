"""Read-only evidence review. A producer's status or a model claim is not authority.

The required EDA scope is DRC, LVS and RCX. Static timing, simulation and dataset
checks remain separate observations; this policy does not require Fmax search.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import stat
import xml.etree.ElementTree as ET

from .input_snapshots import capture, digest, reference
from . import artifact_snapshots
from .paths import signoff_scripts

VERDICTS = {"PASS", "FAIL", "UNKNOWN", "STALE"}
REQUIRED = ("drc", "lvs", "rcx")
LAYER_IDS = ("final_result", "quantitative", "technical")
POLICY = {"version": 1, "required": REQUIRED, "unknown_is_pass": False,
          "simulation_requires_simulator_evidence": True, "fmax_search_required": False}


def _combine(verdicts):
    values = list(verdicts)
    return next((v for v in ("STALE", "FAIL", "UNKNOWN") if v in values),
                "PASS" if values and all(v == "PASS" for v in values) else "UNKNOWN")


def _integer(value):
    return type(value) is int and value >= 0


def _object(value):
    return value if isinstance(value, dict) else {}


class _Evidence:
    def __init__(self, project):
        self.project = Path(project).absolute()
        self.files = {}
        self.changed = set()
        self.input_snapshot = None
        self.output_snapshot = None

    def read(self, relative, limit=8 * 1024 * 1024):
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise ValueError("evidence_path_outside_project")
        fd = os.open(self.project, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in path.parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            child = os.open(path.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=fd)
        finally:
            os.close(fd)
        with os.fdopen(child, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
                raise ValueError("evidence_type_or_size_unsupported")
            data = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_size, after.st_mtime_ns, after.st_ctime_ns) or len(data) != before.st_size:
                raise ValueError("evidence_changed_during_read")
        name = path.as_posix()
        observed = {"path": name, "sha256": hashlib.sha256(data).hexdigest()}
        if name in self.files and self.files[name] != observed:
            self.changed.add(name)
            raise ValueError("evidence_changed_during_review")
        self.files.setdefault(name, observed)
        return data

    def document(self, relative):
        try:
            value = json.loads(self.read(relative))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def references(self, *names):
        return [self.files[name] for name in names if name in self.files]


def _check(method, verdict="UNKNOWN", *, metrics=None, evidence=None, reasons=None, level="unverified"):
    return {"verdict": verdict, "method": method, "verification_level": level,
            "metrics": metrics or {}, "evidence": evidence or [], "reasons": reasons or []}


def _audit_lvs(project):
    path = signoff_scripts() / "extract" / "lvs_audit.py"
    spec = importlib.util.spec_from_file_location("edacode_lvs_audit", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.audit(project, project / "lvs")


def _drc_native(e, report):
    """Audit KLayout's native database, completion log and bound full deck."""
    receipt_path = "drc/drc_result.json"
    receipt = e.document(receipt_path)
    names = {"raw": receipt.get("raw_report_path", "drc/6_drc.lyrdb"),
             "log": receipt.get("log_path", "drc/6_drc.log"),
             "deck": receipt.get("audit_rule_path", "drc/audit_rule.lydrc")}
    for key, value in names.items():
        if isinstance(value, str) and Path(value).is_absolute():
            try:
                names[key] = str(Path(value).relative_to(e.project))
            except ValueError:
                return "UNKNOWN", {}, ["drc_evidence_outside_project"], []
        elif not isinstance(value, str):
            return "UNKNOWN", {}, ["drc_evidence_path_invalid"], []
    reasons, observed, verdict = [], {}, "UNKNOWN"
    refs = [receipt_path, *names.values(), "drc/6_drc_count.rpt"]
    try:
        data = e.read(names["raw"])
        if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
            raise ValueError("drc_xml_entities_unsupported")
        root = ET.fromstring(data)
        categories, cells, items = root.find("categories"), root.find("cells"), root.find("items")
        if (root.tag != "report-database" or categories is None or cells is None or items is None
                or not root.findtext("description") or not root.findtext("generator") or not root.findtext("top-cell")):
            raise ValueError("drc_native_schema_incomplete")
        category_names = [node.findtext("name", "").strip().strip("\"'") for node in categories.iter("category")]
        cell_names = {node.findtext("name", "").strip().strip("\"'") for node in cells.findall("cell")}
        if (not category_names or "" in category_names or len(category_names) != len(set(category_names))
                or root.findtext("top-cell") not in cell_names):
            raise ValueError("drc_native_categories_or_cells_invalid")
        counts = {name: 0 for name in category_names}
        for node in items:
            category = node.findtext("category", "").strip().strip("\"'")
            cell = node.findtext("cell", "").strip().strip("\"'")
            if node.tag != "item" or category not in counts or cell not in cell_names or node.find("values") is None:
                raise ValueError("drc_native_item_invalid")
            counts[category] += 1
        actual = len(items)
        antenna = [n for key, n in counts.items() if "antenna" in key.lower()]
        observed = {"native_items": actual, "native_categories": len(counts),
                    "antenna_violations": sum(antenna) if antenna else None,
                    "count_scope": "native KLayout report items; antenna only explicitly named categories"}
        count_text = e.read("drc/6_drc_count.rpt").decode().strip()
        native_count = int(count_text) if re.fullmatch(r"\d+", count_text) else None
        numbers = (native_count, report.get("total_violations"), report.get("raw_marker_count"), receipt.get("violations"))
        if any(_integer(n) and n != actual for n in numbers):
            return "FAIL", observed, ["drc_native_summary_count_conflict"], e.references(*refs)
        if actual > 0:
            return "FAIL", observed, ["drc_native_violations"], e.references(*refs)
        if any(not _integer(n) for n in numbers):
            reasons.append("drc_exact_counts_missing_or_invalid")
        log = e.read(names["log"]).decode("utf-8")
        if ("Writing report database:" not in log or not re.search(r"Total elapsed:\s*\d", log)
                or re.search(r"(?im)^\s*(ERROR\b|FATAL\b|.*Segmentation fault)", log)):
            reasons.append("drc_native_completion_not_confirmed")
        deck = e.read(names["deck"]).decode("utf-8")
        active = "\n".join(line for line in deck.splitlines() if not line.lstrip().startswith("#"))
        expected_categories = set(re.findall(r'''\.output\(\s*["']([^"']+)["']''', active))
        if not expected_categories or not expected_categories.issubset(counts):
            reasons.append("drc_full_deck_categories_not_confirmed")
        if (receipt.get("status") != "clean" or receipt.get("drc_mode") != "full" or receipt.get("excluded_rules") != []
                or type(receipt.get("exit_code")) is not int or receipt["exit_code"] != 0):
            reasons.append("drc_full_execution_scope_not_confirmed")
        for key, field in (("raw", "raw_report_sha256"), ("log", "log_sha256"), ("deck", "deck_sha256")):
            expected = receipt.get(field)
            if not expected:
                reasons.append("drc_" + field + "_missing")
            elif expected != e.files[names[key]]["sha256"]:
                return "STALE", observed, ["drc_" + field + "_mismatch"], e.references(*refs)
        for key in ("gds_sha256", "deck_sha256", "run_tag"):
            if not receipt.get(key) or receipt[key] != report.get(key):
                reasons.append("drc_receipt_summary_" + key + "_conflict")
        if not receipt.get("deck_sha256_post") or not receipt.get("audit_rule_sha256"):
            reasons.append("drc_rule_pre_post_binding_missing")
        elif not receipt["deck_sha256_post"] == receipt["audit_rule_sha256"] == receipt.get("deck_sha256"):
            return "STALE", observed, ["drc_rule_changed_during_execution"], e.references(*refs)
        verdict = "PASS" if not reasons else "UNKNOWN"
    except (OSError, ValueError, ET.ParseError, UnicodeError) as exc:
        reasons.append(str(exc) if isinstance(exc, ValueError) else "drc_native_evidence_unavailable")
    return verdict, observed, reasons, e.references(*refs)


def _drc(e):
    name = "reports/drc.json"
    report = e.document(name)
    count = report.get("total_violations")
    raw_count = report.get("raw_marker_count")
    reasons = []
    if _integer(count) and count > 0:
        verdict, reasons = "FAIL", ["drc_violations_reported"]
    elif _integer(raw_count) and raw_count > 0 and count == 0:
        verdict, reasons = "FAIL", ["drc_summary_marker_count_conflict"]
    elif report.get("status") in ("fail", "failed", "violations"):
        verdict, reasons = "FAIL", ["drc_reported_failure"]
    elif report.get("status") != "clean" or not _integer(count):
        verdict, reasons = "UNKNOWN", ["drc_clean_status_and_exact_count_required"]
    elif type(report.get("exit_code")) is not int or report["exit_code"] != 0 or report.get("drc_mode") != "full":
        verdict, reasons = "UNKNOWN", ["full_drc_execution_not_confirmed"]
    else:
        verdict = "PASS"
    # Bind the report to the project copy of the actual checked layout.
    run = report.get("run_tag") or _object(report.get("provenance")).get("run_tag")
    expected = report.get("gds_sha256") or _object(report.get("provenance")).get("gds_sha256")
    binding = "UNKNOWN"
    layout_path = None
    if isinstance(run, str) and re.fullmatch(r"RUN_[A-Za-z0-9_.-]+", run) and isinstance(expected, str):
        for folder in ("results", "final"):
            layout_path = f"backend/{run}/{folder}/6_final.gds"
            try:
                actual = hashlib.sha256(e.read(layout_path, 512 * 1024 * 1024)).hexdigest()
                binding = "PASS" if actual == expected else "STALE"
                break
            except (OSError, ValueError):
                pass
    if binding != "PASS":
        reasons.append("drc_layout_digest_" + ("mismatch" if binding == "STALE" else "unavailable"))
    categories = _object(report.get("categories"))
    antenna = [v.get("count") for key, v in categories.items()
               if "antenna" in key.lower() and isinstance(v, dict)]
    native, observed, native_reasons, native_refs = _drc_native(e, report)
    return _check("klayout_native_full_deck_drc", _combine([verdict, binding, native]),
                  metrics={"violations": count, "raw_markers": raw_count,
                           "antenna_violations": sum(antenna) if antenna and all(_integer(n) for n in antenna) else None,
                           **observed},
                  evidence=e.references(name, layout_path) + native_refs, reasons=reasons + native_reasons,
                  level="full_deck_drc" if report.get("drc_mode") == "full" else "unverified")


def _rcx(e):
    report_name = "reports/rcx.json"
    report = e.document(report_name)
    receipt_name = "rcx/rcx_result.json"
    receipt = e.document(receipt_name)
    declared = report.get("execution") or {}
    reasons, verdict, actual_count = [], "UNKNOWN", None
    relative = "rcx/6_final.spef"
    supplied = report.get("spef_file")
    if isinstance(supplied, str):
        try:
            relative = str(Path(supplied).relative_to(e.project)) if Path(supplied).is_absolute() else supplied
        except ValueError:
            reasons.append("spef_path_outside_project")
    try:
        text = e.read(relative, 128 * 1024 * 1024).decode("utf-8")
        names = re.findall(r"^\*(?:D_NET|R_NET)\s+(\S+)\s+\S+\s*$", text, re.M)
        actual_count = len(names)
        if (not text.startswith('*SPEF ') or not names or len(set(names)) != len(names)
                or len(re.findall(r"^\*END\s*$", text, re.M)) != len(names)
                or len(re.findall(r"^\*(?:D_NET|R_NET)\b", text, re.M)) != len(names)):
            reasons.append("spef_structure_unconfirmed")
        elif _integer(report.get("net_count")) and report["net_count"] != actual_count:
            verdict, reasons = "FAIL", ["spef_summary_net_count_conflict"]
        elif receipt and declared != receipt:
            verdict, reasons = "FAIL", ["rcx_execution_receipt_conflict"]
        elif (not receipt or receipt.get("status") != "complete" or type(receipt.get("exit_code")) is not int
              or receipt["exit_code"] != 0):
            reasons.append("rcx_successful_execution_receipt_missing")
        elif receipt.get("spef_sha256") != e.files[relative]["sha256"]:
            verdict, reasons = "STALE", ["rcx_spef_digest_mismatch"]
        elif report.get("status") == "complete" and _integer(report.get("net_count")):
            verdict = "PASS"
        else:
            reasons.append("rcx_summary_completion_unconfirmed")
    except (OSError, ValueError, UnicodeError):
        reasons.append("spef_unavailable_or_unsupported")
    return _check("openrcx_spef", verdict, metrics={"reported_nets": report.get("net_count"),
                  "spef_nets": actual_count, "log_nets_extracted": _object(report.get("log_info")).get("nets_extracted"),
                  "count_scope": "SPEF net sections; extraction log population may differ"},
                  evidence=e.references(report_name, receipt_name, relative), reasons=reasons, level="parasitic_extraction")


def _sta(e):
    name = "reports/timing_check.json"
    report = e.document(name)
    metrics = {k: report.get(k) for k in ("wns", "tns", "hold_wns", "hold_tns", "clock_period_ns")}
    values = [metrics[k] for k in ("wns", "tns", "hold_wns", "hold_tns")]
    finite = all(type(v) in (int, float) and math.isfinite(v) for v in values)
    verdict = "FAIL" if finite and any(v < 0 for v in values) else (
        "PASS" if finite and report.get("tier") == "clean" else "UNKNOWN")
    ppa_name = "reports/ppa.json"
    ppa = e.document(ppa_name)
    expected = _object(report.get("provenance")).get("ppa_sha256")
    binding = "UNKNOWN" if not expected or ppa_name not in e.files else (
        "PASS" if e.files[ppa_name]["sha256"] == expected else "STALE")
    reasons = []
    run = report.get("run_tag") or _object(report.get("provenance")).get("run_tag")
    native_name = f"backend/{run}/logs/6_report.json" if isinstance(run, str) and re.fullmatch(r"RUN_[A-Za-z0-9_.-]+", run) else None
    native = e.document(native_name) if native_name else {}
    native_verdict = "PASS"
    keys = (("wns", "setup_wns", "finish__timing__setup__ws"),
            ("tns", "setup_tns", "finish__timing__setup__tns"),
            ("hold_wns", "hold_wns", "finish__timing__hold__ws"),
            ("hold_tns", "hold_tns", "finish__timing__hold__tns"))
    ppa_timing = _object(_object(ppa.get("summary")).get("timing"))
    for key, ppa_key, raw_key in keys:
        actual = native.get(raw_key)
        if type(actual) not in (int, float) or not math.isfinite(actual):
            native_verdict = _combine([native_verdict, "UNKNOWN"])
            reasons.append("native_sta_slack_missing:" + key)
            continue
        for summary in (metrics.get(key), ppa_timing.get(ppa_key)):
            if type(summary) not in (int, float) or not math.isfinite(summary):
                native_verdict = _combine([native_verdict, "UNKNOWN"])
                reasons.append("sta_summary_slack_missing:" + key)
            elif not math.isclose(actual, summary, rel_tol=1e-9, abs_tol=1e-12):
                native_verdict = "FAIL"
                reasons.append("sta_native_summary_conflict:" + key)
        metrics[key] = actual
    verdict = _combine([verdict, binding, native_verdict])
    # This observed STA result is never an observation of functional simulation.
    return _check("static_timing_analysis", verdict, metrics=metrics, evidence=e.references(name, ppa_name, native_name),
                  reasons=reasons + ([] if verdict == "PASS" else ["timing_violation_or_conflict" if verdict == "FAIL" else "timing_evidence_missing"]),
                  level="static_timing")


def _simulation(e):
    name = "reports/functional_check.json"
    report = e.document(name)
    result = _object(report.get("functional_result"))
    stages = report.get("checks") if isinstance(report.get("checks"), list) else []
    failed = report.get("ok") is False or any(type(s) is dict and s.get("exit_code") not in (None, 0) for s in stages)
    # Legacy repair receipts retain a log tail but not the original simulator
    # log under this project. Preserve that scope instead of asserting a new audit.
    return _check("functional_simulation", "FAIL" if failed else "UNKNOWN",
                  metrics={"reported_ok": report.get("ok"), "reported_checks": result.get("checks"),
                           "oracle_id": result.get("oracle")}, evidence=e.references(name),
                  reasons=["simulation_failure_reported" if failed else "raw_simulator_evidence_not_bound"],
                  level="simulation_report_only" if report else "unverified")


def _dataset(e):
    image_name = "reports/image_dataset.json"
    image = e.document(image_name)
    is_image = image_name in e.files
    name = "images/verify.json" if is_image else "reports/verify.json"
    report = e.document(name)
    results = report.get("results")
    if not isinstance(results, list) or not results:
        return _check("dataset_verifier", reasons=["nonempty_dataset_checks_required"], evidence=e.references(name))
    valid = all(isinstance(r, dict) and isinstance(r.get("check"), str) and type(r.get("ok")) is bool for r in results)
    verdict = "PASS" if valid and all(r["ok"] for r in results) else "FAIL" if valid else "UNKNOWN"
    unique = valid and len({r["check"] for r in results}) == len(results)
    if not unique:
        verdict = "UNKNOWN"
    passed = sum(r.get("ok") is True for r in results if isinstance(r, dict))
    reasons = [] if verdict == "PASS" else ["dataset_checks_failed_or_unconfirmed"]
    for summary in (report, _object(image.get("verify")) if is_image else {}):
        for key, observed in (("passed", passed), ("total", len(results))):
            if key in summary and (not _integer(summary[key]) or summary[key] != observed):
                verdict = "FAIL"
                reasons.append("dataset_summary_count_conflict")
    binding, reason = _stage_binding(e, "graph" if is_image else "verify")
    if reason:
        reasons.append(reason)
    outputs, output_reason = _output_binding(e, "image" if is_image else "graph", name)
    if output_reason:
        reasons.append(output_reason)
    return _check("image_dataset_verifier" if is_image else "graph_dataset_verifier", _combine([verdict, binding, outputs]),
                  metrics={"passed": passed, "total": len(results)},
                  evidence=e.references(name, image_name, "reports/input_bindings.json", "reports/output_bindings.json"), reasons=reasons,
                  level="image_dataset_consistency" if is_image else "graph_dataset_consistency")


def _output_binding(e, kind, report_name):
    receipt = _object(e.document("reports/output_bindings.json").get(kind))
    scope = "unchanged_during_verifier" if kind == "graph" else "post_generation_and_internal_verify"
    if (receipt.get("schema_version") != 1 or receipt.get("kind") != kind or receipt.get("scope") != scope
            or receipt.get("stage") != ("verify" if kind == "graph" else "graph")
            or type(receipt.get("exit_code")) is not int or receipt["exit_code"] != 0
            or not isinstance(receipt.get("execution_id"), str) or not receipt["execution_id"]):
        return "UNKNOWN", "dataset_output_execution_unconfirmed"
    inputs = _object(e.document("reports/input_bindings.json").get(receipt["stage"]))
    if inputs.get("execution_id") != receipt["execution_id"]:
        return "UNKNOWN", "dataset_input_output_execution_mismatch"
    verifier = _object(receipt.get("verifier"))
    if verifier.get("path") != report_name or not verifier.get("sha256") or report_name not in e.files:
        return "UNKNOWN", "dataset_verifier_output_binding_missing"
    if verifier["sha256"] != e.files[report_name]["sha256"]:
        return "STALE", "dataset_verifier_report_changed"
    after = _object(receipt.get("after"))
    if not after.get("sha256") or not after.get("files"):
        return "UNKNOWN", "dataset_output_snapshot_missing"
    if kind == "graph":
        before = _object(receipt.get("before"))
        if not before.get("sha256"):
            return "UNKNOWN", "dataset_pre_verifier_snapshot_missing"
        if before != after:
            return "STALE", "dataset_outputs_changed_during_verifier"
    elif receipt.get("before") is not None:
        return "UNKNOWN", "image_output_capture_scope_invalid"
    try:
        current = artifact_snapshots.capture(e.project, kind)
    except (OSError, ValueError):
        return "UNKNOWN", "current_dataset_outputs_unavailable"
    if current != after:
        return "STALE", "dataset_outputs_changed_after_verifier"
    if kind == "image":
        native = _object(e.document(report_name).get("output_binding"))
        if e.files.get(report_name, {}).get("sha256") != verifier["sha256"]:
            return "STALE", "dataset_verifier_report_changed"
        if (native.get("schema_version") != 1 or native.get("scope") != "core_files_unchanged_during_verifier"
                or not isinstance(native.get("before"), list) or not isinstance(native.get("after"), list)):
            return "UNKNOWN", "image_native_output_binding_missing"
        core = [entry for entry in current["files"] if entry["path"] in {
            "images/features.npz", "images/labels.npz", "images/image_meta.json"}]
        if native["before"] != native["after"] or native["after"] != core:
            return "STALE", "image_core_outputs_changed_during_or_after_verifier"
    e.output_snapshot = current
    return "PASS", None


def _stage_binding(e, stage):
    name = "reports/input_bindings.json"
    binding = _object(e.document(name).get(stage))
    if name in e.changed:
        return "STALE", "review_input_receipt_changed"
    after = _object(binding.get("after"))
    if not binding or not all(after.get(key) for key in ("sha256", "rtl_sha256", "constraints_sha256")):
        return "UNKNOWN", stage + "_input_binding_missing"
    try:
        current = capture(e.project)
    except (OSError, ValueError):
        return "UNKNOWN", "current_inputs_unavailable"
    if reference(current) != after or binding.get("before") != after:
        return "STALE", stage + "_inputs_changed"
    if binding.get("unchanged") is not True or type(binding.get("exit_code")) is not int or binding["exit_code"] != 0:
        return "UNKNOWN", stage + "_execution_unconfirmed"
    if e.input_snapshot is not None and e.input_snapshot != current:
        return "STALE", "review_stage_inputs_differ"
    # Retain the exact manifests validated against the execution receipt. A
    # later packaging-time capture cannot establish this earlier relationship.
    e.input_snapshot = current
    return "PASS", None


def compare_claim(claim: dict, observation: dict) -> dict:
    """Compare a statement to a host observation; this function grants no authority."""
    claimed, observed = claim.get("claimed"), observation.get("value")
    verdict = observation.get("verdict", "UNKNOWN")
    reasons = []
    required = claim.get("verification_level")
    level = observation.get("verification_level", "unverified")
    status_names = {"clean": "PASS", "complete": "PASS", "ok": "PASS", "met": "PASS",
                    "fail": "FAIL", "failed": "FAIL", "violations": "FAIL", "invalid": "FAIL"}
    comparable = status_names.get(claimed, claimed) if isinstance(claimed, str) and not claim.get("metric") else claimed
    if verdict not in VERDICTS or observed is None or claimed is None:
        verdict = "UNKNOWN"
    elif verdict == "STALE":
        reasons.append("evidence_stale")
    elif required and required != level:
        verdict = "UNKNOWN" if level == "unverified" else "FAIL"
        reasons.append("verification_level_does_not_support_claim")
    elif (verdict != "UNKNOWN" or type(observed) in (int, float)) and (
            type(comparable) is not type(observed) or comparable != observed):
        verdict = "FAIL"
        reasons.append("claim_disagrees_with_observation")
    elif verdict in {"PASS", "FAIL"}:
        verdict = "PASS"  # A correct statement of failure is supported; no check is upgraded.
    return {"claim_id": str(claim.get("claim_id", "claim"))[:160], "claimed": claimed, "observed": observed,
            "evidence": observation.get("evidence", []), "method": observation.get("method", "unverified"),
            "verification_level": level, "verdict": verdict, "reasons": reasons}


def _statements(e):
    statements = []
    for kind, path, field in (("drc", "reports/drc.json", "status"), ("lvs", "reports/lvs.json", "status"),
                             ("rcx", "reports/rcx.json", "status"), ("sta", "reports/timing_check.json", "tier")):
        doc = e.document(path)
        statements.append({"claim_id": kind + "." + field, "kind": kind, "claimed": doc.get(field), "source": path})
    simulation = e.document("reports/functional_check.json")
    statements.append({"claim_id": "simulation.ok", "kind": "simulation",
                       "claimed": ("PASS" if simulation["ok"] else "FAIL") if type(simulation.get("ok")) is bool else None,
                       "source": "reports/functional_check.json"})
    for kind, path, field, metric in (("drc", "reports/drc.json", "total_violations", "violations"),
                                    ("rcx", "reports/rcx.json", "net_count", "spef_nets"),
                                    ("dataset", "reports/verify.json", "passed", "passed"),
                                    ("dataset", "reports/verify.json", "total", "total")):
        if kind == "dataset" and "reports/image_dataset.json" in e.files:
            path = "images/verify.json"
        doc = e.document(path)
        if field in doc:
            statements.append({"claim_id": kind + "." + field, "kind": kind, "metric": metric,
                               "claimed": doc[field], "source": path})
    return statements


def review(project: Path, *, claims=None) -> dict:
    e = _Evidence(project)
    checks = {"drc": _drc(e), "rcx": _rcx(e), "sta": _sta(e), "simulation": _simulation(e), "dataset": _dataset(e)}
    try:
        audit = _audit_lvs(e.project)
        if not isinstance(audit, dict) or audit.get("verdict") not in VERDICTS:
            raise ValueError("invalid LVS audit")
    except Exception as exc:
        audit = {"verdict": "UNKNOWN", "layers": [], "verification_level": "unverified", "metrics": {},
                 "evidence_files": [], "reasons": ["raw_lvs_audit_unavailable", type(exc).__name__]}
    checks["lvs"] = _check("independent_raw_lvs_audit", audit["verdict"], metrics=audit.get("metrics"),
                           evidence=audit.get("evidence_files"), reasons=audit.get("reasons"),
                           level=audit.get("verification_level", "unverified"))
    checks["lvs"]["audit"] = audit
    checks["lvs"]["reasons"].extend(reason for layer in audit.get("layers", []) for reason in layer.get("reasons", []))
    native_layers = {layer.get("id"): layer.get("verdict", "UNKNOWN") for layer in audit.get("layers", [])}
    checks["lvs"]["verdict"] = _combine([checks["lvs"]["verdict"], *(native_layers.get(key, "UNKNOWN") for key in LAYER_IDS)])
    binding, reason = _stage_binding(e, "signoff")
    statements = _statements(e)
    strict = e.document("reports/signoff_manifest.json")
    for kind in REQUIRED:
        checks[kind]["verdict"] = _combine([checks[kind]["verdict"], binding])
        if reason:
            checks[kind]["reasons"].append(reason)
    if e.changed:
        for kind, check in checks.items():
            if kind in REQUIRED or any(item.get("path") in e.changed for item in check["evidence"]):
                check["verdict"] = "STALE"
                check["reasons"].append("evidence_changed_during_review")
    layers = {}
    for layer_id in LAYER_IDS:
        native = next((layer for layer in audit.get("layers", []) if layer.get("id") == layer_id),
                      {"id": layer_id, "verdict": "UNKNOWN", "reasons": ["layer_evidence_missing"]})
        components = {kind: {"verdict": checks[kind]["verdict"], "method": checks[kind]["method"],
                             "reasons": checks[kind]["reasons"]} for kind in ("drc", "rcx")}
        components["lvs"] = {**native, "method": "independent_raw_lvs_audit"}
        layers[layer_id] = {"id": layer_id, "scope": "required_eda_checks", "checks": components,
                           "verdict": _combine([binding, *(part["verdict"] for part in components.values())]),
                           "reasons": [kind + ":" + why for kind, part in components.items() for why in part.get("reasons", [])]
                                      + ([reason] if reason else [])}
    if claims is not None:
        if not isinstance(claims, list) or len(claims) > 32 or any(not isinstance(c, dict) for c in claims):
            raise ValueError("claims must be at most 32 structured statements")
        for claim in claims:
            if (not isinstance(claim.get("kind"), str) or not isinstance(claim.get("claim_id", "claim"), str)
                    or (claim.get("metric") is not None and not isinstance(claim["metric"], str))
                    or type(claim.get("claimed")) not in (str, int, float, bool, type(None))
                    or (isinstance(claim.get("claimed"), str) and len(claim["claimed"]) > 512)):
                raise ValueError("claim identifiers and metrics must be strings; claimed must be a bounded scalar")
            statements.append({k: claim[k] for k in ("claim_id", "kind", "metric", "claimed", "verification_level") if k in claim})
    comparisons = []
    for claim in statements:
        check = checks.get(claim.get("kind"), {})
        if type(claim.get("claimed")) not in (str, int, float, bool, type(None)):
            claim = {**claim, "claimed": None}
        value = check.get("verdict")
        if claim.get("metric"):
            value = check.get("metrics", {})
            for key in str(claim["metric"]).split("."):
                value = value.get(key) if isinstance(value, dict) else None
        row = compare_claim(claim, {**check, "value": value,
                            "evidence": check.get("evidence", []) + e.references(claim.get("source"))})
        if claim.get("metric"):
            row["metric"] = claim["metric"]
            row["count_scope"] = check.get("metrics", {}).get("count_scope") or (
                _object(check.get("metrics", {}).get(str(claim["metric"]).split(".")[0])).get("count_scope"))
        comparisons.append(row)
    return {"schema_version": 1, "policy_sha256": digest({"policy": POLICY,
                "implementation": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "artifact_implementation": hashlib.sha256(Path(artifact_snapshots.__file__).read_bytes()).hexdigest(),
                "lvs_policy": audit.get("policy_sha256")}),
            "required_checks": list(REQUIRED), "verdict": _combine(checks[k]["verdict"] for k in REQUIRED),
            "three_layer": layers, "verification_level": checks["lvs"]["verification_level"],
            "checks": checks, "claims": comparisons,
            "input_snapshot": e.input_snapshot if binding == "PASS" else None,
            "output_snapshot": e.output_snapshot if checks["dataset"]["verdict"] == "PASS" else None,
            "evidence_files": list(e.files.values()) + audit.get("evidence_files", []),
            "changed_evidence": sorted(e.changed),
            "prior_policy": {"strict_clean": strict.get("strict_clean"), "strict_missing": strict.get("strict_missing"),
                             "required_by_this_review": False},
            "unverified": [kind for kind, check in checks.items() if check["verdict"] in {"UNKNOWN", "STALE"}]}


def validation_summary(detail: dict) -> dict:
    """Bounded projection only; legacy database status never synthesizes a review."""
    review = detail.get("validation")
    if not isinstance(review, dict) or review.get("schema_version") != 1 or review.get("verdict") not in VERDICTS:
        return {"status": "legacy_unreviewed", "verdict": "UNKNOWN", "report": "verification_review.json"}
    return {"schema_version": 1, "status": "reviewed", "verdict": review["verdict"],
            "policy_sha256": review.get("policy_sha256"), "verification_level": review.get("verification_level", "unverified"),
            "report": "verification_review.json", "required_checks": list(REQUIRED),
            "three_layer": {key: {"verdict": value.get("verdict", "UNKNOWN"), "scope": "required_eda_checks",
                                   "reasons": value.get("reasons", [])[:8],
                                   "checks": {kind: {"verdict": part.get("verdict", "UNKNOWN"), "method": part.get("method"),
                                                     "reasons": part.get("reasons", [])[:4]}
                                              for kind, part in value.get("checks", {}).items() if kind in REQUIRED}}
                            for key, value in review.get("three_layer", {}).items() if key in LAYER_IDS},
            "checks": {key: {"verdict": value.get("verdict", "UNKNOWN"), "method": value.get("method"),
                              "verification_level": value.get("verification_level"), "metrics": _small_metrics(value.get("metrics", {}))}
                       for key, value in review.get("checks", {}).items() if key in {*REQUIRED, "sta", "simulation", "dataset"}},
            "unverified": review.get("unverified", [])[:8]}


def _small_metrics(value, depth=0):
    if isinstance(value, dict) and depth < 3:
        return {str(k)[:64]: _small_metrics(v, depth + 1) for k, v in list(value.items())[:16]}
    if type(value) in (int, float, bool) or value is None:
        return value
    return str(value)[:160]
