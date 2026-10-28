"""Host-observation based legal next actions for EDA tasks.

The model sees this bounded view, while the executor must recompute it from the
current job immediately before controlled actions. An allowed candidate does
not replace the action controller's source, grant, version or oracle checks.
In particular, this module never makes a PASS verdict or grants permission.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
import re

from . import task_skills

MAX_DESIGNS = 8
MAX_EVENTS = 200
MAX_CANDIDATES = 32
_ID = re.compile(r"[A-Za-z0-9_.-]{1,100}\Z")
_TOP = re.compile(r"[A-Za-z_][A-Za-z0-9_$]{0,99}\Z")
_HASH = re.compile(r"[a-fA-F0-9]{64}\Z")
_INFRA = re.compile(r"permission denied|no space left|command not found|not installed|"
                    r"cannot execute|license.*(?:denied|unavailable)|timed? out|timeout|"
                    r"out of memory|disk.*(?:full|space)|missing.*(?:executable|toolchain)", re.I)
_DEPENDENCY = re.compile(r"unknown module(?: type)?|(?:cannot|can't|unable to) find module|"
                         r"module\b.{0,160}(?:not found|not part of the design|not in the design)|"
                         r"missing (?:module|dependency)|unresolved (?:module|reference)", re.I)
_SYNTAX = re.compile(r"syntax error|parse error|unexpected\s+(?:TOK_|token)", re.I)
_FUNCTION = re.compile(r"assertion.*fail|(?:state|output|functional|simulation).*mismatch|"
                       r"functional.*fail|asynchronous reset failed", re.I)
_ACTIVE = frozenset({"pending", "queued", "running", "finalizing"})
_STOPPED = frozenset({"cancelled", "cancelling", "recovery_required"})
_PHYSICAL = frozenset({"flow", "signoff", "repair", "lvs", "drc", "timing", "graph", "verify", "done"})
_INPUT = frozenset({"input", "prepare", "compile", "synth"})

# These observations remain available even if the selected stage has no legal
# mutation. Session and grant ownership are still enforced by HostExecutor.
READ_TOOLS = frozenset({"job_status", "list_jobs", "design_detail", "read_log", "read_rtl",
    "dataset_info", "review_evidence", "knowledge_lookup", "eda_capabilities", "task_skill",
    "plan_next_step", "read_tool_output", "read_session_history", "list_examples", "flow_params",
    "disk_usage", "task_status"})
CONTROLLED_TOOLS = frozenset({"inspect_design", "check_design", "check_environment", "diagnose",
    "propose_rtl_repair", "execute_rtl_repair", "execute_dependency_debug", "start_job", "start_batch"})


def _obj(value):
    return value if isinstance(value, dict) else {}


def _text(value, limit=160):
    return value[:limit] if isinstance(value, str) else ""


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _events(job, events):
    if not isinstance(events, list):
        raise ValueError("host events must be a list")
    # JobStore.events may return descending pages. Identity, rather than caller
    # order, determines the most recent evidence. Cross-job evidence is ignored.
    own = [e for e in events if isinstance(e, dict) and e.get("job_id") == job["id"]
           and type(e.get("id")) is int and e["id"] > 0]
    return sorted(own, key=lambda event: event["id"])[-MAX_EVENTS:]


def _stage(record):
    detail = _obj(record.get("detail"))
    return _text((detail.get("failed_stage") or record.get("stage"))
                 if record.get("status") == "failed" else record.get("stage"), 40)


def _failure(record, events, evidence):
    stage = _stage(record)
    current = [e for e in events if e.get("design") == record["name"] and e.get("stage") == stage
               and e.get("kind") == "fail"][-1:]
    text = "\n".join(_text(e.get("message"), 2000) + "\n" +
        _text(_obj(e.get("data")).get("log_tail"), 6000) for e in current)
    report = _obj(evidence) or _obj(_obj(record.get("detail")).get("validation"))
    if _INFRA.search(text):
        return "environment", current
    if stage in _INPUT and _DEPENDENCY.search(text):
        return "missing_dependency", current
    if stage in _INPUT and _SYNTAX.search(text):
        return "syntax", current
    if _FUNCTION.search(text) or (stage in {"simulation", "simulate", "functional_check"}
                                  and record.get("status") == "failed"):
        return "functional", current
    if stage in _PHYSICAL and report.get("verdict") in {"UNKNOWN", "STALE"}:
        return "physical_evidence_unresolved", current
    if stage in _PHYSICAL and record.get("status") == "failed":
        return "physical_failure", current
    if record.get("status") == "failed":
        return "unclassified_failure", current
    return "none", current


def _budget_refusals(budget, key="remaining_actions"):
    if not isinstance(budget, dict) or key not in budget:
        return []
    value = budget[key]
    return [] if type(value) is int and value > 0 else ["budget_exhausted_or_invalid:" + key]


def _binding_refusals(record):
    binding = _obj(_obj(_obj(record.get("detail")).get("input_bindings")).get(_stage(record)))
    before, after = _obj(binding.get("before")), _obj(binding.get("after"))
    if (binding.get("unchanged") is not True or before != after
            or any(not _HASH.fullmatch(str(before.get(k, ""))) for k in
                   ("sha256", "rtl_sha256", "constraints_sha256"))
            or type(binding.get("exit_code")) is not int or binding["exit_code"] == 0):
        return ["failure_source_binding_missing_changed_or_inconsistent"]
    return []


def _candidate(design, suffix, tool, arguments, reason, *, refusals=(), preconditions=(), checks=()):
    return {"id": design + ":" + suffix, "design": design, "tool": tool,
        "arguments": arguments, "allowed": not refusals, "reasons": [reason],
        "refusals": list(dict.fromkeys(refusals)), "preconditions": list(preconditions),
        "checks": list(checks), "requires_host_revalidation": tool in CONTROLLED_TOOLS}


def action_view(job: dict, events: list[dict], *, capabilities: dict | None = None,
                evidence: dict | None = None, grant_id: str | None = None,
                budget: dict | None = None, design: str = "", debug_authorized: bool = False) -> dict:
    """Return legal bounded candidates from current host records.

    ``evidence`` may be a report for the selected design or a name->report map.
    ``debug_authorized`` is a host-validated authorization for this exact job;
    it is never accepted from model tool arguments. ``grant_id`` only selects
    existing RTL repair actions; that controller validates the actual grant.
    """
    if not isinstance(job, dict) or not _ID.fullmatch(str(job.get("id", ""))):
        raise ValueError("a valid host job is required")
    if not isinstance(job.get("designs"), list):
        raise ValueError("host designs must be a list")
    if design and (not isinstance(design, str) or not _ID.fullmatch(design)):
        raise ValueError("invalid design name")
    own = _events(job, events)
    selected = [r for r in job["designs"] if isinstance(r, dict) and
                isinstance(r.get("name"), str) and _ID.fullmatch(r["name"]) and
                (not design or r["name"] == design)]
    caps = _obj(capabilities)
    out = {"schema_version": 1, "job_id": job["id"], "job_status": _text(job.get("status"), 40),
        "scope": "stage legality; host controllers retain source, authorization and verification checks",
        "designs": [], "candidates": [], "refusals": [],
        "omitted": {"designs": max(0, len(selected) - MAX_DESIGNS), "candidates": 0}}
    if not selected:
        out["refusals"].append("design_not_found" if design else "design_records_unavailable")
    platform = _text(_obj(job.get("spec")).get("platform"), 100)
    base = {"job_id": job["id"]}
    add = out["candidates"].append
    for record in selected[:MAX_DESIGNS]:
        name, stage = record["name"], _stage(record)
        detail = _obj(record.get("detail"))
        report = _obj(_obj(evidence).get(name)) or _obj(evidence)
        failure, current = _failure(record, own, report)
        active = job.get("status") in _ACTIVE or record.get("status") in _ACTIVE
        stopped = job.get("status") in _STOPPED or record.get("status") in _STOPPED
        control = (["execution_not_terminal"] if active else []) + (
                   ["execution_cancelled_or_requires_reconciliation"] if stopped else [])
        running = _budget_refusals(budget)
        scope = {**base, "design": name}
        out["designs"].append({"design": name, "stage": stage, "status": _text(record.get("status"), 40),
            "failure_kind": failure, "evidence_event_ids": [e["id"] for e in current],
            "input_binding_id": _text(_obj(_obj(_obj(detail.get("input_bindings")).get(stage)).get("before")).get("sha256"), 64)})
        add(_candidate(name, "status", "job_status", base, "observe_current_job"))
        add(_candidate(name, "reports", "design_detail", scope, "read_current_design_and_bindings"))
        if stage and _ID.fullmatch(stage):
            add(_candidate(name, "log", "read_log", {**scope, "stage": stage, "lines": 60},
                "read_actual_stage_evidence"))
        if active or stopped:
            continue
        add(_candidate(name, "source", "read_rtl", scope, "inspect_source_without_mutation"))
        add(_candidate(name, "capabilities", "eda_capabilities", {"platform": platform},
            "check_current_tool_and_pdk_identity"))
        if stage in _PHYSICAL or record.get("status") == "passed":
            add(_candidate(name, "review", "review_evidence", scope,
                "verify_raw_final_quantitative_and_technical_evidence",
                checks=("same_source_and_policy", "three_layers", "unresolved_is_not_pass")))
        if job.get("status") == "done":
            add(_candidate(name, "delivery", "dataset_info", base, "inspect_actual_delivery_manifest"))
        if failure == "environment":
            add(_candidate(name, "environment", "check_environment", {"platform": platform},
                "classify_environment_without_changing_host_configuration", refusals=control + running,
                preconditions=("host_platform_scope",), checks=("tool_and_asset_readiness",)))
            continue
        project, top = record.get("project"), detail.get("top")
        path = str(PurePosixPath(project) / "rtl") if isinstance(project, str) and project.startswith("/") else ""
        if stage in _INPUT and path and len(path) <= 1600 and isinstance(top, str) and _TOP.fullmatch(top):
            add(_candidate(name, "inputs", "inspect_design", {"path": path, "top": top},
                "inspect_existing_input_dependencies", refusals=control + running,
                preconditions=("job_project_input_scope",), checks=("bounded_source_inventory",)))
            for check, skill in (("compile", "compile_failure"), ("synthesis_structure", "synthesis_diagnosis")):
                contract = task_skills.validate_diagnostic(skill, check, stage=stage, capabilities=caps)
                add(_candidate(name, check, "check_design", {"path": path, "top": top, "check": check},
                    "cheap_diagnostic_before_expensive_flow", refusals=control + running + contract["refusals"],
                    preconditions=("job_project_input_scope", "current_tool_identity", "fixed_diagnostic_argv"),
                    checks=("source_hash_unchanged", "diagnostic_only_not_functional_or_signoff_pass")))
        if failure == "missing_dependency":
            refusal = control + running + _budget_refusals(budget, "remaining_recovery_attempts")
            if not isinstance(budget, dict) or any(key not in budget for key in
                    ("remaining_actions", "remaining_recovery_attempts")):
                refusal.append("explicit_dependency_debug_budget_required")
            refusal += _binding_refusals(record)
            if debug_authorized is not True:
                refusal.append("job_scoped_dependency_debug_authorization_required")
            if not current:
                refusal.append("current_missing_module_failure_event_required")
            if not path or not isinstance(top, str) or not _TOP.fullmatch(top):
                refusal.append("job_project_and_top_required")
            if (platform != _obj(caps.get("platform")).get("name") or
                    _obj(caps.get("platform")).get("agent_supported") is not True):
                refusal.append("current_supported_platform_identity_required")
            for tool in ("iverilog", "yosys"):
                item = _obj(_obj(caps.get("tools")).get(tool))
                if item.get("usable") is not True or not _HASH.fullmatch(str(item.get("sha256", ""))):
                    refusal.append("current_diagnostic_tool_identity_required:" + tool)
            add(_candidate(name, "dependency_debug", "execute_dependency_debug",
                {**scope, "evidence_event_id": current[-1]["id"] if current else 0},
                "recover_omitted_compilation_units_from_existing_copied_sources", refusals=refusal,
                preconditions=("host_scoped_debug_authorization", "immutable_source_and_constraints",
                    "copied_existing_hdl_only", "one_attempt_no_child_reauthorization"),
                checks=("missing_module_evidence", "isolated_compile_and_synthesis", "required_full_flow",
                        "three_layer_review_before_delivery")))
        if failure == "syntax":
            chosen = task_skills.select_tasks(job, own, capabilities=caps, design=name)
            repair = next((c for c in chosen["candidates"] if c["skill_id"] == "compile_failure"), None)
            refusal = control + running + ([] if repair and repair["decision"] == "eligible_for_proposal"
                else ["registered_rtl_repair_preconditions_not_met"])
            add(_candidate(name, "propose_semicolon", "propose_rtl_repair", scope,
                "registered_counter8_semicolon_candidate_only", refusals=refusal,
                preconditions=("exact_current_file_line_and_failure_event", "counter8_contract_v1"),
                checks=("read_only_proposal", "host_policy_and_source_revalidation")))
            add(_candidate(name, "execute_semicolon", "execute_rtl_repair", {},
                "execute_only_the_existing_host_approved_proposal",
                refusals=refusal + ([] if grant_id else ["host_approved_rtl_repair_grant_required"]),
                preconditions=("proposal_bound_to_grant_and_job", "remaining_grant_rounds"),
                checks=("independent_counter8_oracle", "full_eda_recheck", "three_layer_review")))
        if stage in {"flow", "signoff", "repair", "lvs", "drc", "timing"}:
            check = "lvs" if stage == "lvs" else "drc" if stage == "drc" else "timing" if stage == "timing" else "route" if stage == "flow" else "lvs"
            add(_candidate(name, "physical_diagnosis", "diagnose", {**scope, "check": check},
                "read_existing_physical_repair_recommendations_without_applying_them",
                refusals=control + running,
                checks=("no_deck_waiver", "no_blackbox_fallback", "no_check_substitution")))
    if len(out["candidates"]) > MAX_CANDIDATES:
        out["omitted"]["candidates"] = len(out["candidates"]) - MAX_CANDIDATES
        out["candidates"] = out["candidates"][:MAX_CANDIDATES]
    # Hash the actual bounded view, including the grants/budget effects, without
    # serializing raw events or unbounded design details back into model context.
    out["observation_id"] = _digest(out)
    return out


def validate_action(name: str, arguments: dict, job: dict, events: list[dict], **host) -> dict:
    """Fail closed for controlled actions, while retaining ordinary read tools.

    HostExecutor must call this with fresh observations and host-only kwargs.
    Final action controllers retain their own independent guards. Generic job
    resubmission and unregistered mutations are never legalized by this view.
    """
    view = action_view(job, events, **host)
    result = {"allowed": False, "refusals": [], "candidate_ids": [],
              "observation_id": view["observation_id"], "schema_version": 1}
    if not isinstance(arguments, dict) or any(not isinstance(k, str) or k.startswith("_") for k in arguments):
        result["refusals"] = ["invalid_action_arguments"]
        return result
    if arguments.get("job_id") and arguments["job_id"] != job["id"]:
        result["refusals"] = ["action_outside_current_job"]
        return result
    names = {r["name"] for r in job["designs"] if isinstance(r, dict) and isinstance(r.get("name"), str)}
    if arguments.get("design") and arguments["design"] not in names:
        result["refusals"] = ["action_outside_current_design"]
        return result
    if name in READ_TOOLS:
        result["allowed"] = True
        return result
    candidates = [c for c in view["candidates"] if c["tool"] == name]
    for candidate in candidates:
        expected = candidate["arguments"]
        if any(arguments.get(k) != v for k, v in expected.items()):
            continue
        extra = set(arguments) - set(expected)
        if name == "inspect_design":
            extra -= {"top"}
        elif name == "propose_rtl_repair":
            extra -= {"file", "line", "expected_line", "evidence_event_id", "rationale"}
        elif name == "execute_rtl_repair":
            extra -= {"proposal_id"}
        if extra:
            continue
        result["candidate_ids"].append(candidate["id"])
        if candidate["allowed"]:
            result["allowed"] = True
        else:
            result["refusals"].extend(candidate["refusals"])
    if result["allowed"]:
        result["refusals"] = []
    elif not result["refusals"]:
        result["refusals"] = ["action_or_arguments_not_legal_at_current_stage"]
    result["refusals"] = list(dict.fromkeys(result["refusals"]))
    return result
