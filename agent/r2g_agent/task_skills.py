"""Task contracts and read-only next-check selection from host observations.

This module runs no tools and grants no mutation permission. Read-only diagnosis
must satisfy a task contract; a registered repair candidate must still pass the
existing proposal and host-approval checks. Resources load individually.
"""
from __future__ import annotations

from copy import deepcopy
from importlib.resources import files
import json
import math
import re

from .input_snapshots import digest

MAX_DESIGNS = 8
MAX_EVENTS = 200
MAX_CANDIDATES = 16
MAX_OUTPUT_CHARS = 32000
_IDS = ("compile_failure", "simulation_mismatch", "synthesis_diagnosis",
        "timing_violations", "lvs_mismatch", "dataset_delivery")
_IDENTIFIER = re.compile(r"[A-Za-z0-9_.-]{1,100}\Z")
_HASH = re.compile(r"[a-fA-F0-9]{64}\Z")
_INFRA = re.compile(r"permission denied|no space left|command not found|not installed|"
                    r"cannot execute|license.*(?:denied|unavailable)|timed? out|timeout", re.I)
_SYNTAX = re.compile(r"syntax error|parse error|unexpected\s+(?:TOK_|token)", re.I)
_SIMULATION = re.compile(r"assertion.*fail|state mismatch|output mismatch|wrap mismatch|"
                         r"asynchronous reset failed|functional.*(?:fail|mismatch)", re.I)
_TIMING = re.compile(r"(?:setup|hold|timing)\s+(?:check\s+)?(?:violation|fail)|negative slack", re.I)
_LVS = re.compile(r"\blvs\b.*(?:mismatch|fail|not clean)|(?:mismatch|fail).*\blvs\b", re.I)


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _resource(name: str) -> dict:
    raw = files("r2g_agent").joinpath("task_skills", name + ".json").read_text(encoding="utf-8")
    if len(raw) > 16000:
        raise ValueError("task contract exceeds its resource budget")
    value = json.loads(raw)
    if value.get("schema_version") != 1:
        raise ValueError("unsupported task contract schema")
    return value


def list_skills() -> dict:
    """Small task manifest; no full contract bodies or tool execution."""
    return deepcopy(_resource("manifest"))


def load_skill(skill_id: str) -> dict:
    """Load one allowlisted package resource, never a caller-selected path."""
    if skill_id not in _IDS:
        raise ValueError("unknown task skill")
    return deepcopy(_resource(skill_id))


def validate_diagnostic(skill_id: str, check: str, *, stage: str = "input", capabilities: dict) -> dict:
    """Machine-check one host diagnostic against its task contract.

    The caller must supply the current host inventory, enforce the controlled
    argv/input/timeout policy and recheck executable identity at invocation.
    This authorizes neither mutations nor arbitrary simulator commands.
    """
    result = {"schema_version": 1, "skill_id": _text(skill_id, 80), "check": _text(check, 80),
              "stage": _text(stage, 40), "tool": "check_design", "allowed": False,
              "mutation_authorized": False, "contract_sha256": None, "refusals": [],
              "validated_tool_hashes": {}}
    if skill_id not in _IDS:
        result["refusals"].append("unknown_task_contract")
        return result
    contract = load_skill(skill_id)
    result["contract_sha256"] = digest(contract)
    if not isinstance(check, str) or check not in contract.get("allowed_diagnostics", []):
        result["refusals"].append("diagnostic_not_allowed_by_task_contract")
        return result
    stages = _obj(contract.get("diagnostic_stages")).get(check)
    required_tools = _obj(contract.get("diagnostic_required_tools")).get(check)
    if (not isinstance(stages, list) or not stages or not all(isinstance(s, str) for s in stages)
            or not isinstance(required_tools, list) or not required_tools
            or not all(isinstance(name, str) and _IDENTIFIER.fullmatch(name) for name in required_tools)):
        result["refusals"].append("diagnostic_policy_incomplete")
        return result
    if not isinstance(stage, str) or stage not in stages:
        result["refusals"].append("diagnostic_not_allowed_at_this_stage")
    tools = _obj(_obj(capabilities).get("tools"))
    for name in required_tools:
        tool = _obj(tools.get(name))
        if tool.get("usable") is not True:
            result["refusals"].append("diagnostic_tool_unverified:" + name)
        checksum = tool.get("sha256")
        if not isinstance(checksum, str) or not _HASH.fullmatch(checksum):
            result["refusals"].append("diagnostic_tool_identity_unbound:" + name)
        elif tool.get("usable") is True:
            result["validated_tool_hashes"][name] = checksum
    result["allowed"] = not result["refusals"]
    return result


def _obj(value) -> dict:
    return value if isinstance(value, dict) else {}


def _text(value, cap=160) -> str:
    return value[:cap] if isinstance(value, str) else ""


def _number(value) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _event_text(event: dict) -> str:
    data = _obj(event.get("data"))
    return _text(event.get("message"), 2000) + "\n" + _text(data.get("log_tail"), 6000)


def _next(tool: str, arguments: dict, purpose: str) -> dict:
    return {"tool": tool, "arguments": arguments, "purpose": purpose}


def _candidate(skill_id: str, job_id: str, record: dict, evidence: list[dict],
               capabilities: dict, reasons: list[str]) -> dict:
    contract = load_skill(skill_id)
    detail = _obj(record.get("detail"))
    stage = _text((detail.get("failed_stage") or record.get("stage"))
                  if record.get("status") == "failed" else record.get("stage"), 40)
    checks = [_next("design_detail", {"job_id": job_id, "design": record["name"]},
                    "读取本设计报告与版本绑定，不能用旧作业结论替代")]
    if stage:
        checks.append(_next("read_log", {"job_id": job_id, "design": record["name"], "stage": stage, "lines": 60},
                            "核对实际执行日志及失败位置"))
    diagnostic = {"compile_failure": "compile", "synthesis_diagnosis": "synthesis_structure"}.get(skill_id)
    project = record.get("project")
    top = detail.get("top")
    if diagnostic and isinstance(project, str) and project.startswith("/") and len(project) < 1600 and isinstance(top, str) and _IDENTIFIER.fullmatch(top):
        checks.append(_next("check_design", {"path": project.rstrip("/") + "/rtl", "top": top, "check": diagnostic},
                            "仅建议由host在任务合同、当前工具身份与预算允许时执行受控诊断；本选择器不自动执行"))
    tools = _obj(capabilities.get("tools"))
    missing = [name for name in contract["required_tools"] if _obj(tools.get(name)).get("usable") is not True]
    return {"skill_id": skill_id, "skill_version": contract["version"], "design": record["name"],
            "decision": "advisory", "execution_mode": contract["execution_mode"],
            "execution_authorized": False, "reasons": reasons,
            "refusals": ["recheck_tool_unverified:" + name for name in missing],
            "evidence_event_ids": [event["id"] for event in evidence[-6:]],
            "evidence_source": "host_job_design_and_events",
            "observed": {"design_status": _text(record.get("status"), 40), "stage": stage},
            "recommended_next_checks": checks,
            "contract_read_with": {"tool": "task_skill", "arguments": {"skill_id": skill_id}}}


def _compile(candidate: dict, job: dict, record: dict, evidence: list[dict], caps: dict) -> None:
    detail = _obj(record.get("detail"))
    binding = _obj(_obj(detail.get("input_bindings")).get("synth"))
    before, after = _obj(binding.get("before")), _obj(binding.get("after"))
    refusals = candidate["refusals"]
    if job.get("status") != "failed" or record.get("status") != "failed":
        refusals.append("repair_requires_terminal_failed_job_and_design")
    if detail.get("failed_stage") != "synth" or not any(e.get("stage") == "synth" for e in evidence):
        refusals.append("repair_requires_synthesis_syntax_event")
    if not any(_SYNTAX.search(_event_text(e)) for e in evidence):
        refusals.append("synthesis_syntax_error_not_observed")
    if detail.get("top") != "counter8":
        refusals.append("no_registered_repair_for_this_top")
    if _obj(job.get("spec")).get("output") != "graph":
        refusals.append("registered_repair_requires_graph_output")
    if (binding.get("unchanged") is not True or any(not _HASH.fullmatch(str(before.get(key, "")))
            for key in ("sha256", "rtl_sha256", "constraints_sha256"))
            or before != after or type(binding.get("exit_code")) is not int or binding["exit_code"] == 0):
        refusals.append("missing_or_inconsistent_failure_input_binding")
    else:
        candidate["observed"]["failure_snapshot_id"] = before["sha256"]
    if any(_INFRA.search(_event_text(e)) for e in evidence):
        refusals.append("infrastructure_failure_is_not_an_rtl_repair")
    if _obj(caps.get("platform")).get("agent_supported") is not True:
        refusals.append("platform_support_unverified")
    if _obj(caps.get("platform")).get("name") != _obj(job.get("spec")).get("platform"):
        refusals.append("capability_platform_mismatch")
    for name in ("iverilog", "vvp"):
        if _obj(_obj(caps.get("tools")).get(name)).get("usable") is not True:
            refusals.append("registered_oracle_tool_unverified:" + name)
    candidate["recommended_next_checks"].append(_next(
        "read_rtl", {"job_id": job["id"], "design": record["name"]},
        "定位精确文件与行；选择器不读取源码、不证明分号位置正确"))
    candidate["pending_host_checks"] = [
        "current_source_matches_failure_snapshot", "one_rtl_file_at_most_64KiB",
        "exact_line_and_safe_source_constructs", "registered_oracle_and_policy_unchanged",
        "durable_turn_action_and_session_job_scope", "explicit_approval_and_remaining_budget"]
    if not refusals:
        candidate["decision"] = "eligible_for_proposal"
        candidate["proposed_action"] = "rtl_insert_semicolon"
        candidate["reasons"].append("registered_narrow_candidate_requires_host_revalidation")


def _delivery(candidate: dict, job: dict, record: dict, evidence: list[dict]) -> None:
    detail = _obj(record.get("detail"))
    verified, signoff = _obj(detail.get("verify")), _obj(detail.get("signoff"))
    passed, total = verified.get("passed"), verified.get("total")
    refusals = candidate["refusals"]
    if type(passed) is int and type(total) is int and 0 <= passed <= total:
        candidate["observed"]["reported_dataset_checks"] = {"passed": passed, "total": total,
                                                              "source": "job.design.detail.verify"}
    if type(total) is not int or type(passed) is not int or total <= 0 or passed != total:
        refusals.append("dataset_checks_missing_failed_or_empty")
    verified_events = [e for e in evidence if e.get("stage") == "verify" and e.get("kind") == "ok"]
    last_verdict = next((e for e in reversed(evidence) if e.get("stage") == "verify"
                         and e.get("kind") in ("ok", "fail")), None)
    if not verified_events:
        refusals.append("successful_verify_event_not_in_observed_window")
    elif (type(_obj(verified_events[-1].get("data")).get("checks")) is not int
          or _obj(verified_events[-1].get("data")).get("checks") != total):
        refusals.append("dataset_count_conflict_or_event_count_unknown")
    if last_verdict and last_verdict.get("kind") == "fail":
        refusals.append("latest_verify_event_reports_failure")
    if record.get("status") != "passed" or job.get("status") != "done":
        refusals.append("job_and_design_not_successfully_terminal")
    if signoff.get("drc") != "clean" or signoff.get("lvs") != "clean" or signoff.get("dual_clean") is not True:
        refusals.append("signoff_not_confirmed_dual_clean")
    if any(type(signoff.get(key)) is not int or signoff[key] != 0 for key in ("drc_violations", "lvs_mismatches")):
        refusals.append("signoff_violation_counts_nonzero_or_unknown")
    if not isinstance(_obj(job.get("result")).get("package"), str) or not job["result"]["package"]:
        refusals.append("delivery_archive_not_recorded")
    candidate["recommended_next_checks"] = [
        _next("review_evidence", {"job_id": job["id"], "design": record["name"]}, "逐项核对三层验证和声称/实证，不从历史passed状态推断通过"),
        _next("dataset_info", {"job_id": job["id"]}, "核对实际交付产物；存在记录不等于已核对归档内容"),
        _next("design_detail", {"job_id": job["id"], "design": record["name"]}, "核对原始verify.json、输入绑定和签核报告"),
        _next("read_log", {"job_id": job["id"], "design": record["name"], "stage": "verify", "lines": 60},
              "从本次校验日志核对实际检查总数，不采用对话转述")]
    candidate["pending_host_checks"] = ["raw_report_count_and_content", "archive_members_and_hashes",
                                         "source_constraints_policy_binding", "manifest_unverified_items"]
    if not refusals:
        candidate["decision"] = "verify_delivery"
        candidate["reasons"].append("reported_results_support_delivery_inspection_not_a_new_pass_verdict")


def select_tasks(job: dict, events: list[dict], *, capabilities: dict | None = None,
                 design: str = "") -> dict:
    """Select bounded next checks from fresh host state; never execute or assert PASS.

    ``capabilities`` is capabilities.inventory's snapshot: platform/name/
    agent_supported and tools/<id>/usable. Missing or stale-looking evidence is
    reported, not upgraded from tool presence or old model prose.
    """
    if (not isinstance(job, dict) or not isinstance(job.get("id"), str)
            or not _IDENTIFIER.fullmatch(job["id"])):
        raise ValueError("a host job record with a valid id is required")
    if not isinstance(events, list) or not isinstance(job.get("designs"), list):
        raise ValueError("host events and job designs must be lists")
    if design and not _IDENTIFIER.fullmatch(design):
        raise ValueError("invalid design name")
    caps = _obj(capabilities)
    job_id = job["id"]
    supplied = job["designs"]
    selected = [r for r in supplied if isinstance(r, dict) and (not design or r.get("name") == design)]
    observations = [e for e in events[-MAX_EVENTS:] if isinstance(e, dict)
                    and e.get("job_id") == job_id and type(e.get("id")) is int and e["id"] > 0]
    observations.sort(key=lambda e: e["id"])
    out = {"schema_version": 1, "job_id": job_id, "read_only": True, "execution_authorized": False,
           "job_status": _text(job.get("status"), 40), "inventory_id": _text(caps.get("inventory_id"), 100),
           "candidates": [], "refusals": [], "omitted": {"designs": max(0, len(selected) - MAX_DESIGNS),
           "events": max(0, len(events) - MAX_EVENTS), "candidates": 0},
           "scope": "next_check_recommendations; not execution permission or a PASS verdict"}
    if job.get("status") in ("pending", "queued", "running", "finalizing"):
        out["refusals"].append("job_not_terminal; observe_progress_before_new_action")
    elif job.get("status") in ("cancelled", "cancelling", "recovery_required"):
        out["refusals"].append("job_cancelled_or_execution_requires_reconciliation")
    if not selected:
        out["refusals"].append("design_not_found" if design else "design_records_unavailable")
    for record in selected[:MAX_DESIGNS]:
        name = record.get("name")
        if not isinstance(name, str) or not _IDENTIFIER.fullmatch(name):
            out["refusals"].append("invalid_design_identity")
            continue
        detail = _obj(record.get("detail"))
        status = record.get("status")
        stage = detail.get("failed_stage") if status == "failed" else record.get("stage")
        own = [e for e in observations if e.get("design") == name]
        # Events from an older stage or another design cannot diagnose this one.
        current = [e for e in own if e.get("stage") == stage and e.get("kind") == "fail"][-1:]
        pairs = []
        if status == "failed" and stage in ("synth", "compile"):
            syntax = [e for e in current if _SYNTAX.search(_event_text(e))]
            if syntax or stage == "compile":
                pairs.append(("compile_failure", syntax or current, ["observed_current_compile_failure"]))
            pairs.append(("synthesis_diagnosis", current, ["observed_synthesis_failure"]))
        if stage in ("simulate", "simulation", "functional_check") and status == "failed":
            mismatch = [e for e in current if _SIMULATION.search(_event_text(e))]
            pairs.append(("simulation_mismatch", mismatch, ["simulation_failure_requires_oracle_evidence"]))
        timing = _obj(detail.get("timing") or detail.get("timing_check"))
        timing_events = [e for e in current if _TIMING.search(_event_text(e))]
        if stage in ("flow", "signoff", "timing") and (timing_events or any(
                _number(timing.get(k)) and timing[k] < 0 for k in ("wns", "tns", "setup_slack", "hold_slack"))):
            pairs.append(("timing_violations", timing_events, ["reported_timing_violation_requires_path_and_corner_review"]))
        signoff = _obj(detail.get("signoff"))
        lvs_events = [e for e in current if _LVS.search(_event_text(e))]
        mismatches = signoff.get("lvs_mismatches")
        if stage in ("signoff", "lvs") and (lvs_events or signoff.get("lvs") in ("failed", "mismatch", "dirty")
                                            or (_number(mismatches) and mismatches > 0)):
            pairs.append(("lvs_mismatch", lvs_events, ["reported_lvs_mismatch_requires_same_input_comparison"]))
        if stage in ("graph", "verify", "done") or status == "passed":
            pairs.append(("dataset_delivery", [e for e in own if e.get("stage") in ("graph", "verify", "done")],
                          ["dataset_or_terminal_state_requires_delivery_review"]))
        if not pairs:
            out["refusals"].append("no_supported_failure_or_delivery_evidence:" + name)
        for skill_id, evidence, reasons in pairs:
            candidate = _candidate(skill_id, job_id, record, evidence, caps, reasons)
            if not evidence and skill_id != "dataset_delivery":
                candidate["refusals"].append("matching_failure_event_missing")
            if skill_id == "compile_failure":
                _compile(candidate, job, record, evidence, caps)
            elif skill_id == "dataset_delivery":
                _delivery(candidate, job, record, evidence)
            control = (job.get("status"), status)
            if any(s in ("recovery_required", "cancelled", "cancelling") for s in control):
                candidate["decision"] = "abstain"
                candidate["refusals"].append("execution_cancelled_or_requires_reconciliation")
            elif any(s in ("pending", "queued", "running", "finalizing") for s in control):
                candidate["decision"] = "wait"
                candidate["refusals"].append("execution_not_terminal")
            candidate["refusals"] = list(dict.fromkeys(candidate["refusals"]))
            out["candidates"].append(candidate)
    out["omitted"]["candidates"] = max(0, len(out["candidates"]) - MAX_CANDIDATES)
    out["candidates"] = out["candidates"][:MAX_CANDIDATES]
    while len(_json(out)) > MAX_OUTPUT_CHARS and out["candidates"]:
        out["candidates"].pop()
        out["omitted"]["candidates"] += 1
    return out
