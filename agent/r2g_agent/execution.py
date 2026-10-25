"""Host-owned tool execution shared by chat, HTTP, CLI, and MCP.

The existing turn/action and tool-output stores remain authoritative. Direct
commands have no model budget and never opt into background model reviews.
Transport arguments carry an idempotency key, never execution authority.
"""
from __future__ import annotations

import inspect
import json
import uuid
from dataclasses import dataclass, replace
from pathlib import Path

from . import tools
from .input_snapshots import manifest, read_tree
from .submissions import source_snapshot
from .tool_outputs import ToolContext, ToolOutputError, redact
from .turns import OWNER, TERMINAL, TurnConflict, digest

HOST_FIELDS = frozenset({"session_id", "turn_id", "action_id", "grant_id", "read_only",
                         "allow_session_tools", "execution_context", "review_enabled"})
JOB_CREATORS = frozenset({"start_job", "start_batch", "execute_rtl_repair"})


@dataclass(frozen=True)
class ExecutionContext:
    transport: str
    request_id: str
    session_id: str | None = None
    turn_id: str | None = None
    action_id: str | None = None
    job_id: str | None = None
    read_only: bool = False
    grant_id: str | None = None
    allow_session_tools: bool = False


def response(receipt: dict) -> dict:
    """Preserve established result fields while exposing the durable receipt."""
    result = receipt["result"]
    return {**(result if isinstance(result, dict) else {"result": result}),
            "output_ref": receipt.get("output_ref"),
            "execution": {k: receipt.get(k) for k in (
                "session_id", "turn_id", "action_id", "request_id", "status", "replayed")}}


class HostExecutor:
    def __init__(self, sessions, jobs):
        self.sessions, self.jobs = sessions, jobs

    def host_context(self, transport: str, request_id: str | None = None, session_id: str | None = None,
                     *, turn_id: str | None = None, action_id: str | None = None,
                     allow_session_tools: bool = False, read_only: bool = False,
                     grant_id: str | None = None, job_id: str | None = None) -> ExecutionContext:
        if transport not in {"web", "cli", "mcp", "chat"}:
            raise ValueError("unsupported execution transport")
        for value in (session_id, turn_id, action_id, grant_id, job_id):
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError("host identifiers must be nonempty strings")
        if request_id is not None and (not isinstance(request_id, str) or not 1 <= len(request_id) <= 200):
            raise ValueError("request_id must contain 1–200 characters")
        if allow_session_tools and not session_id:
            raise ValueError("session tools require an explicitly approved session")
        if session_id:
            with self.sessions.turns.connection() as c:
                if not c.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone():
                    raise ValueError("unknown session")
        context = ExecutionContext(transport, request_id or uuid.uuid4().hex, session_id, turn_id,
                                   action_id, job_id, read_only, grant_id, allow_session_tools)
        if turn_id:
            context = self._bound_context(context)
        elif grant_id:
            from .repairs import validate_chat_grant
            validate_chat_grant(self.sessions.path, grant_id, session_id, job_id)
        return context

    def _bound_context(self, context: ExecutionContext) -> ExecutionContext:
        turn = self.sessions.turns.get(context.turn_id)
        if not turn or turn["session_id"] != context.session_id:
            raise TurnConflict("turn does not belong to the execution session")
        grant = turn["payload"].get("grant_id")
        if context.grant_id and context.grant_id != grant:
            raise TurnConflict("grant does not belong to this turn")
        direct = turn["kind"] == "execution"
        return replace(context, read_only=context.read_only or turn["kind"] == "job_review" or
                       (direct and bool(turn["payload"].get("read_only"))),
                       grant_id=grant, job_id=turn["job_id"],
                       allow_session_tools=context.allow_session_tools and
                       (not direct or bool(turn["payload"].get("allow_session_tools"))))

    @staticmethod
    def _arguments(name: str, arguments: dict) -> dict:
        if name not in tools.REGISTRY:
            raise ValueError("unknown tool %s" % name)
        if not isinstance(arguments, dict) or any(not isinstance(k, str) or k.startswith("_") or k in HOST_FIELDS
                                                  for k in arguments):
            raise ValueError("tool arguments cannot supply host context or permissions")
        signature = inspect.signature(tools.REGISTRY[name]["fn"])
        signature = signature.replace(parameters=[p for p in signature.parameters.values() if not p.name.startswith("_")])
        try:
            bound = signature.bind(**arguments)
        except TypeError as exc:
            raise ValueError("bad tool arguments: %s" % exc) from exc
        bound.apply_defaults()
        return dict(bound.arguments)

    @staticmethod
    def _denial(name: str, context: ExecutionContext) -> dict | None:
        if tools.REGISTRY[name].get("session_scoped") and not context.allow_session_tools:
            return {"error": "session-scoped tools require explicit host approval", "error_code": "session_approval_required"}
        if context.read_only and name not in tools.REVIEW_TOOLS:
            return {"error": "read-only execution cannot invoke this tool", "error_code": "read_only_review"}
        if context.grant_id and name not in tools.REVIEW_TOOLS | {"execute_rtl_repair"}:
            return {"error": "repair turn cannot bypass the registered action policy", "error_code": "repair_action_scope"}
        return None

    @staticmethod
    def _snapshot(name: str, arguments: dict) -> str | None:
        if name in {"start_job", "start_batch"}:
            sources, _ = tools.resolve_submission(name, arguments)
        elif name in {"inspect_design", "check_design"}:
            # Match the checker's complete input binding, including quoted
            # includes with arbitrary suffixes and data files. Descriptor-based
            # capture also preserves its rejection of symlinked inputs.
            path = Path(arguments["path"]).expanduser().absolute()
            return manifest(read_tree(path))["sha256"]
        else:
            return None
        return source_snapshot(sources)

    @staticmethod
    def _receipt(context: ExecutionContext, result, *, status: str, output_ref=None, replayed=False) -> dict:
        return {"result": redact(result), "output_ref": output_ref, "session_id": context.session_id,
                "request_id": context.request_id,
                "turn_id": context.turn_id, "action_id": context.action_id, "status": status, "replayed": replayed}

    def _previous(self, turn: dict, context: ExecutionContext) -> dict:
        turn = self.sessions.turns.get(turn["id"])
        actions = self.sessions.turns.actions(turn["id"])
        action = actions[0] if actions else None
        context = replace(context, session_id=turn["session_id"], turn_id=turn["id"],
                          action_id=action["id"] if action else None)
        if action and action["status"] == "complete":
            return self._receipt(context, action["result"], status=turn["status"],
                                 output_ref=action["output_ref"], replayed=True)
        uncertain = turn["status"] == "recovery_required"
        return self._receipt(context, {"error": "execution requires reconciliation" if uncertain else "execution is already in progress",
                                      "error_code": "execution_unconfirmed" if uncertain else "execution_in_progress",
                                      "execution_may_have_completed": uncertain}, status=turn["status"], replayed=True)

    def execute(self, name: str, arguments: dict, context: ExecutionContext) -> dict:
        """Run one direct command, or return its existing durable observation."""
        if context.turn_id or context.action_id:
            raise ValueError("use execute_prepared for an existing chat action")
        try:
            arguments = self._arguments(name, arguments)
            if name == "cancel_job" and context.job_id is None:
                context = replace(context, job_id=arguments["job_id"])
            denial = self._denial(name, context)
            if denial:
                return self._receipt(context, denial, status="rejected")
            snapshot = self._snapshot(name, arguments)
            key = "execution:" + digest(context.request_id)
            self.sessions.turns.recover()
            payload = {"message": "直接执行 " + name, "operation": name, "arguments": arguments,
                       "request_id": context.request_id, "source_snapshot": snapshot, "review_enabled": False,
                       "read_only": context.read_only, "grant_id": context.grant_id,
                       "allow_session_tools": context.allow_session_tools}
            turn, created = self.sessions.turns.create(context.session_id, key, payload, kind="execution", job_id=context.job_id,
                                                       max_model_calls=0, max_actions=1, claim_immediately=True)
            sid = turn["session_id"]
            context = replace(context, session_id=sid, turn_id=turn["id"])
            if not created and not self.sessions.turns.claim(turn["id"]):
                return self._previous(turn, context)
            call = {"id": "host-command", "name": name, "arguments": arguments}
            messages = self.sessions.load(sid)
            messages.extend([
                {"role": "user", "content": payload["message"], "turn_id": turn["id"], "turn_kind": "execution",
                 "transport": context.transport},
                {"role": "assistant", "content": "", "tool_calls": [call], "turn_id": turn["id"]}])
            self.sessions.save(sid, messages)
            action = self.sessions.turns.begin_action(turn["id"], "host:" + turn["id"], call)
            context = replace(context, action_id=action["id"])
            if not self.sessions.turns.start_action(action["id"]):
                raise TurnConflict("action was already started")
            self.sessions.turns.publish(turn["id"], {"type": "tool_call", **call, "action_id": action["id"]})
            receipt = self.execute_prepared(name, arguments, context, messages=messages, title=payload["message"],
                                            expected_snapshot=snapshot)
            self.sessions.turns.publish(turn["id"], {"type": "tool_result", "id": call["id"], "name": name,
                "action_id": action["id"], "result": receipt["result"], "output_ref": receipt["output_ref"]})
            status = receipt["status"]
            self.sessions.turns.finish(turn["id"], status, {"type": "done" if status == "complete" else "error",
                **({} if status == "complete" else {"message": receipt["result"].get("error", "execution stopped")})})
            return receipt
        except (ValueError, OSError) as exc:
            if context.turn_id:
                return self._interrupted(context, type(exc).__name__)
            return self._receipt(context, {"error": str(exc)[:1024], "error_code": "execution_conflict" if isinstance(exc, TurnConflict)
                                          else "invalid_execution"}, status="conflict" if isinstance(exc, TurnConflict) else "rejected")
        except Exception as exc:
            if context.turn_id:
                return self._interrupted(context, type(exc).__name__)
            raise

    def _interrupted(self, context: ExecutionContext, reason: str) -> dict:
        turn = self.sessions.turns.get(context.turn_id)
        if turn and turn["status"] not in TERMINAL:
            self.sessions.turns.finish(turn["id"], "recovery_required", {"type": "error",
                "message": "direct execution interrupted (%s); no automatic replay" % reason})
        return self._previous(self.sessions.turns.get(context.turn_id), context)

    def execute_prepared(self, name: str, arguments: dict, context: ExecutionContext, *,
                         messages: list[dict], title: str = "", expected_snapshot: str | None = None) -> dict:
        """Execute an already durable action; append/store its one observation.

        Chat owns its model loop and terminal turn event. This method never
        requests a model or retries an uncertain tool. The caller stops on a
        recovery_required receipt instead of executing later proposed tools.
        """
        context = self._bound_context(context)
        turn = self.sessions.turns.get(context.turn_id)
        actions = self.sessions.turns.actions(context.turn_id)
        action = next((a for a in actions if a["id"] == context.action_id), None)
        if not action or action["tool_name"] != name or action["request_hash"] != digest({"name": name, "arguments": arguments}):
            raise TurnConflict("tool request does not match the durable action intent")
        if action["status"] == "complete":
            return self._receipt(context, action["result"], status="complete", output_ref=action["output_ref"], replayed=True)
        if action["status"] != "executing" or turn["status"] != "running" or turn["owner"] != OWNER:
            raise TurnConflict("worker does not own an executing action")
        try:
            result = self._denial(name, context) if name in tools.REGISTRY else None
            normalized = self._arguments(name, arguments) if result is None else {}
        except ValueError as exc:
            normalized, result = {}, {"error": str(exc), "error_code": "invalid_tool_arguments"}
        snapshot = expected_snapshot
        if result is None:
            try:
                snapshot = snapshot if snapshot is not None else self._snapshot(name, normalized)
            except (ValueError, OSError) as exc:
                result = {"error": str(exc), "error_code": "invalid_tool_inputs"}
        if result is None:
            # A generic read-only entry has no fixed job. The executor performs
            # the allowlist check; job-focused reviews retain the inner scope gate.
            tool_context = ToolContext(context.session_id, self.sessions.outputs, self.sessions.read_history,
                context.turn_id, context.action_id, context.job_id,
                context.read_only and context.job_id is not None, context.grant_id)
            result = tools.call(name, normalized, context=tool_context)
            if snapshot is not None:
                try:
                    changed = self._snapshot(name, normalized) != snapshot
                except (ValueError, OSError):
                    changed = True
                if changed:
                    result = {**(result if isinstance(result, dict) else {}),
                              "error": "source changed during execution; result needs reconciliation",
                              "error_code": "source_changed_during_execution"}
        try:
            view, ref = self.sessions.outputs.record(context.session_id, action["call_id"], name, result)
        except ToolOutputError as exc:
            failure = {"error": str(exc), "error_code": "tool_output_not_recorded", "execution_may_have_completed": True}
            messages.append({"role": "tool", "tool_call_id": action["call_id"], "name": name,
                             "content": json.dumps(failure), "turn_id": context.turn_id, "action_id": context.action_id})
            self.sessions.save(context.session_id, messages, title)
            return self._receipt(context, failure, status="recovery_required")
        messages.append({"role": "tool", "tool_call_id": action["call_id"], "name": name,
                         "content": json.dumps(view, ensure_ascii=False), "output_ref": ref,
                         "turn_id": context.turn_id, "action_id": context.action_id})
        job_id = result.get("job_id") if name in JOB_CREATORS and isinstance(result, dict) and not result.get("error") else None
        self.sessions.turns.complete_action(context.action_id, view, ref, messages, title, job_id=job_id)
        return self._receipt(context, view, status="error" if isinstance(result, dict) and result.get("error") else "complete",
                             output_ref=ref)
