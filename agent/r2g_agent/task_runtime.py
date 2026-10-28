"""Connect task evidence and stage policy to the existing durable model loop.

This view never grants permissions and never infers completion from chat text.
The host executor checks an action again against the current job before use.
"""
from __future__ import annotations

import time
import json

from .task_state import TaskStateStore


def model_view(observation: dict, max_chars: int = 5000) -> dict:
    """Project model data without duplicating the full evidence ledger.

    Goals and constraints are protected once by ContextState. Omitted details
    remain readable through task_status/plan_next_step and their saved outputs.
    This is information selection, never an authorization or verdict change.
    """
    if not observation:
        return {}
    if type(max_chars) is not int or max_chars < 2000:
        raise ValueError("task model view requires at least 2000 characters")
    task = observation.get("task") or {}
    actions = observation.get("stage_actions") or {}
    view = {"task": {k: task.get(k) for k in
                     ("id", "root_turn_id", "job_id", "profile", "phase", "status")},
            "budget": observation.get("budget"),
            "stage_actions": {"job_id": actions.get("job_id"), "observation_id": actions.get("observation_id"),
                              "candidates": []}}
    current = view["task"]
    current.update(goal_ref="controller_state.goal", constraints_ref="controller_state.constraints",
                   conditions=[], pending_evidence=[], read_with={"tool": "task_status", "arguments": {}},
                   completion_authority="UNKNOWN is not PASS; only host evidence establishes completion")
    if observation.get("historical_turn_job_id"):
        view["historical_turn_job_id"] = observation["historical_turn_job_id"]
    if observation.get("unavailable"):
        view["unavailable"] = str(observation["unavailable"])[:300]
    upstream = actions.get("omitted") or {}
    omitted = {"conditions": task.get("omitted_conditions", 0),
               "pending_evidence": task.get("omitted_pending_evidence", 0),
               "candidates": upstream.get("candidates", 0), "designs": upstream.get("designs", 0)}
    view["omitted"] = omitted

    def append(target, value, key):
        target.append(value)
        if len(json.dumps(view, ensure_ascii=False)) > max_chars - 200:
            target.pop()
            omitted[key] += 1

    for condition in task.get("conditions", []):
        append(current["conditions"], {k: condition[k] for k in ("id", "verdict")}, "conditions")
    for evidence in task.get("pending_evidence", []):
        append(current["pending_evidence"], {"id": evidence["id"], "source": evidence.get("source"),
               "reasons": [str(reason)[:160] for reason in evidence.get("reasons", [])[:2]]}, "pending_evidence")
    # Prefer an executable next step to repeated generic observation tools.
    priority = {"execute_dependency_debug": 0, "execute_rtl_repair": 0,
                "propose_rtl_repair": 1, "check_design": 2, "review_evidence": 3, "inspect_design": 3}
    candidates = sorted(actions.get("candidates", []),
                        key=lambda item: (not item["allowed"], priority.get(item["tool"], 5)))
    for candidate in candidates:
        value = {k: candidate[k] for k in ("id", "tool", "allowed")}
        if candidate["allowed"]:
            value["arguments"] = candidate["arguments"]
        elif candidate.get("refusals"):
            value["refusals"] = candidate["refusals"][:3]
        append(view["stage_actions"]["candidates"], value, "candidates")
    view["stage_actions"]["requires_host_revalidation"] = True
    return view


def diagnostic_capabilities(job: dict) -> dict:
    """Probe only tools needed for cheap input checks, without loading PDKs.

    Platform support here means the registered agent flow, never PDK readiness
    or physical signoff. Full readiness remains the eda_capabilities tool.
    """
    from . import capabilities, platforms
    name = job.get("spec", {}).get("platform", "")
    records = job.get("designs", [])
    needed = any((d.get("detail") or {}).get("failed_stage", d.get("stage")) in
                 {"input", "prepare", "compile", "synth"} and d.get("status") == "failed" for d in records)
    observed = capabilities.tool_inventory(("iverilog", "yosys", "vvp")) if needed else {"tools": {}}
    return {**observed, "platform": {"name": name, "agent_supported": name in platforms.PLATFORMS},
            "scope": "cheap_diagnostics_only; PDK_assets_not_observed"}


def task_for_turn(sessions, turn: dict) -> dict:
    store = TaskStateStore(sessions.path)
    task = store.get_for_turn(turn["id"])
    if (task and turn["kind"] == "user" and turn.get("job_id") and task.get("job_id") != turn["job_id"]
            and store.get_for_job(turn["session_id"], turn["job_id"]) is None):
        # Reading an old job is not a new task. A completed ordinary submission
        # is durable evidence that this user turn selected new work. Reconcile
        # it here too, closing the action-commit / task-link crash window.
        with sessions.turns.connection() as c:
            action = c.execute("SELECT a.id FROM actions a JOIN job_links l ON a.id=l.action_id "
                "WHERE a.turn_id=? AND a.status='complete' AND a.tool_name IN ('start_job','start_batch') "
                "AND l.turn_id=a.turn_id AND l.job_id=? ORDER BY a.created DESC LIMIT 1",
                (turn["id"], turn["job_id"])).fetchone()
        if action:
            task = store.fork_for_submission(turn["id"], turn["job_id"], action["id"])
    if task is None and turn.get("job_id"):
        task = store.get_for_job(turn["session_id"], turn["job_id"])
    if task is None:
        # Background turns inherit the original goal, rather than treating the
        # generated review instruction as a new user objective.
        parent = None
        if turn["kind"] != "user" and turn.get("job_id"):
            with sessions.turns.connection() as c:
                row = c.execute("SELECT t.id FROM turns t JOIN job_links l ON t.id=l.turn_id "
                                "WHERE t.session_id=? AND l.job_id=? AND t.kind='user' "
                                "ORDER BY t.created LIMIT 1", (turn["session_id"], turn["job_id"])).fetchone()
            parent = sessions.turns.get(row[0]) if row else None
        source = parent or turn
        task = store.ensure(turn["session_id"], source["id"], source["payload"]["message"],
                            job_id=turn.get("job_id"), constraints=(
                                "Preserve source RTL and constraints outside a host-approved repair.",
                                "UNKNOWN or STALE evidence cannot establish PASS; a finished turn is not task completion.",))
    store.bind_turn(task["id"], turn["id"])
    if turn.get("job_id") and task.get("job_id") != turn["job_id"]:
        known = store.get_for_job(turn["session_id"], turn["job_id"])
        if known is None:
            store.bind_job(task["id"], turn["job_id"])
    return store.view(task["id"])


def snapshot(sessions, jobs, turn: dict) -> dict:
    """Read a bounded current task and its legal next actions for one decision."""
    from . import stage_actions
    task = task_for_turn(sessions, turn)
    if not turn.get("job_id") or jobs is None:
        return {}  # Keep ordinary chat and small context budgets lightweight.
    job = jobs.job(task.get("job_id") or turn["job_id"])
    if not job:
        return {"task": task, "unavailable": "linked job unavailable"}
    store = TaskStateStore(sessions.path)
    store.observe(task["id"], job)
    task = store.view(task["id"])
    if task["job_id"] != turn["job_id"]:
        return {"task": task, "historical_turn_job_id": turn["job_id"],
                "note": "A newer child owns current completion evidence; this old turn cannot replace it."}
    with sessions.turns.connection() as c:
        calls = c.execute("SELECT COUNT(*) FROM model_calls WHERE turn_id=?", (turn["id"],)).fetchone()[0]
    budget = {"remaining_model_calls": max(0, turn["max_model_calls"] - calls),
              "remaining_actions": max(0, turn["max_actions"] - len(sessions.turns.actions(turn["id"]))),
              "remaining_recovery_attempts": 0}
    authorized = False
    if turn["kind"] == "debug_review":
        from .debug_actions import DependencyDebugController
        authority = DependencyDebugController(sessions.path, jobs, None).authority(turn["session_id"], job["id"])
        authorized = bool(authority and authority.get("status") == "approved" and authority["deadline"] > time.time())
        if authorized:
            budget["remaining_recovery_attempts"] = 1 if authority.get("status") == "approved" else 0
    observed = diagnostic_capabilities(job) if turn["kind"] in {"user", "debug_review", "repair_review"} else None
    actions = stage_actions.action_view(job, jobs.events(job["id"], tail=200), capabilities=observed,
                                       grant_id=turn["payload"].get("grant_id"), budget=budget,
                                       debug_authorized=authorized)
    from . import tools
    available = (tools.REVIEW_TOOLS if turn["kind"] == "job_review" else tools.DEBUG_TOOLS
                 if turn["kind"] == "debug_review" else tools.REVIEW_TOOLS | {"execute_rtl_repair"}
                 if turn["payload"].get("grant_id") else set(tools.REGISTRY))
    for candidate in actions["candidates"]:
        if candidate["tool"] not in available:
            candidate["allowed"] = False
            candidate["refusals"].append("tool_unavailable_in_this_turn")
    return {"task": task, "stage_actions": actions, "budget": budget}
