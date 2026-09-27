"""Process-shared offline dispatch accounting for the R5 S2 controller.

No network/provider/tokenizer is implemented or authorized here. Token counts
come from a trusted future adapter, never model text. For now initialization
requires OFFLINE_ONLY. Local flock + fsync serialize callers; uncertain pending
calls retain all reservations and cannot be dispatched again after reopening.
The runner-owned directory must not be replaced/unlinked by concurrent writers.
Hash chains detect corruption, not malicious edits by the trusted ledger owner.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import uuid

from . import research_r5_s2_controller as c

SCHEMA = "tehm-r5-s2-campaign-budget-v1"
LIMITS = ("calls_per_arm", "tokens_per_arm", "evaluations_per_arm", "calls_total",
          "tokens_total", "max_input_tokens", "max_output_tokens")


def validate_plan(plan: dict) -> None:
    if type(plan) is not dict or set(plan) != {"schema", "mode", "tasks", "counter_id", *LIMITS}:
        raise c.ControllerError("campaign plan fields")
    if plan["schema"] != SCHEMA or plan["mode"] != "OFFLINE_ONLY":
        raise c.ControllerError("live provider use is not enabled or authorized")
    if (type(plan["tasks"]) is not list or not plan["tasks"] or len(plan["tasks"]) > 100
            or any(type(t) is not str or not re.fullmatch(r"[a-z0-9_-]{1,64}", t) for t in plan["tasks"])
            or len(set(plan["tasks"])) != len(plan["tasks"])):
        raise c.ControllerError("campaign task registry")
    if type(plan["counter_id"]) is not str or not re.fullmatch(r"[a-z0-9_-]{1,80}", plan["counter_id"]):
        raise c.ControllerError("counter identity missing")
    if any(not c.positive(plan[key]) for key in LIMITS):
        raise c.ControllerError("invalid campaign budget")


def _hash(value):
    return c.sha(c.canonical(value))


def _digest(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


class CampaignLedger:
    def __init__(self, path: Path, expected_plan: dict):
        validate_plan(expected_plan)
        self.path = Path(path).absolute()
        if self.path.parent.resolve(strict=True) != self.path.parent or self.path.is_symlink():
            raise c.ControllerError("linked campaign ledger path")
        self.plan = json.loads(c.canonical(expected_plan))
        with self._locked() as (_, state):
            if state["plan"] != self.plan:
                raise c.ControllerError("campaign plan changed")

    @classmethod
    def create(cls, path: Path, plan: dict):
        validate_plan(plan)
        path = Path(path).absolute()
        if path.parent.resolve(strict=True) != path.parent:
            raise c.ControllerError("linked campaign parent")
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        try:
            event = {"sequence": 0, "previous_digest": None, "event": "init", "plan": plan}
            event["digest"] = _hash(event)
            cls._write(fd, event)
        finally:
            os.close(fd)
        # Persist directory entry as well as data before permitting reservations.
        parent = os.open(path.parent, os.O_DIRECTORY)
        try: os.fsync(parent)
        finally: os.close(parent)
        return cls(path, plan)

    @staticmethod
    def _write(fd, event):
        data = c.canonical(event) + b"\n"
        os.lseek(fd, 0, os.SEEK_END)
        while data:
            count = os.write(fd, data)
            if count <= 0: raise OSError("incomplete campaign event write")
            data = data[count:]
        os.fsync(fd)

    @contextmanager
    def _locked(self):
        fd = os.open(self.path, os.O_RDWR | os.O_NOFOLLOW)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            size = os.fstat(fd).st_size
            if not 0 < size <= 4 * 1024 * 1024:
                raise c.ControllerError("empty or excessive ledger")
            with os.fdopen(os.dup(fd), "rb") as stream: data = stream.read()
            if not data.endswith(b"\n"):
                raise c.ControllerError("partial event: retain reservations, do not resume")
            try: events = [json.loads(line) for line in data.splitlines()]
            except (ValueError, UnicodeError) as exc:
                raise c.ControllerError("corrupt campaign ledger") from exc
            previous = None
            for seq, event in enumerate(events):
                if type(event) is not dict: raise c.ControllerError("invalid campaign event")
                payload = dict(event); digest = payload.pop("digest", None)
                if digest != _hash(payload) or payload.get("sequence") != seq or payload.get("previous_digest") != previous:
                    raise c.ControllerError("broken campaign hash chain")
                previous = digest
            if events[0].get("event") != "init" or events[0].get("plan") != self.plan:
                raise c.ControllerError("wrong campaign plan")
            state = {"plan": events[0]["plan"], "calls": {}, "stopped_arms": set(),
                     "halted": False, "events": events}
            for event in events[1:]:
                kind, ident = event["event"], event["call_id"]
                if kind == "reserve": state["calls"][ident] = dict(event, state="RESERVED")
                else:
                    call = state["calls"][ident]
                    call.update(event)
                    call["state"] = {"claim": "DISPATCHED", "finish": event.get("terminal"),
                        "candidate": "CANDIDATE", "reject": "INVALID", "evaluate": "EVALUATING",
                        "feedback": "EVALUATED"}[kind]
                    if kind == "finish" and (event["terminal"] != "RECEIVED" or event["usage_status"] != "VALID"):
                        state["stopped_arms"].add((call["task"], call["policy"]))
                    if kind == "finish" and event["usage_status"] in {"OVERRUN", "MALFORMED", "MISSING"}:
                        state["halted"] = True
                    if kind == "feedback" and event["feedback"]["stop"]:
                        state["stopped_arms"].add((call["task"], call["policy"]))
            yield fd, state
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _append(self, fd, state, kind, **fields):
        event = {"sequence": len(state["events"]), "previous_digest": state["events"][-1]["digest"],
                 "event": kind, **fields}
        event["digest"] = _hash(event)
        self._write(fd, event)

    def reserve(self, task: str, policy: str, prompt: dict, *, input_tokens: int, counter_id: str) -> str:
        if task not in self.plan["tasks"] or policy not in c.POLICIES:
            raise c.ControllerError("unregistered task/policy")
        if counter_id != self.plan["counter_id"] or not c.positive(input_tokens) or input_tokens > self.plan["max_input_tokens"]:
            raise c.ControllerError("input count or counter identity invalid")
        prompt_digest = _hash(prompt)
        if len(c.canonical(prompt)) > c.MAX_RESPONSE_BYTES:
            raise c.ControllerError("excessive prompt")
        with self._locked() as (fd, state):
            calls = list(state["calls"].values())
            arm = [x for x in calls if (x["task"], x["policy"]) == (task, policy)]
            amount = input_tokens + self.plan["max_output_tokens"]
            if (state["halted"] or (task, policy) in state["stopped_arms"]
                    or any(x["state"] not in {"INVALID", "EVALUATED"} for x in arm)
                    or len(calls) >= self.plan["calls_total"] or len(arm) >= self.plan["calls_per_arm"]
                    or sum(x["reserved_tokens"] for x in calls) + amount > self.plan["tokens_total"]
                    or sum(x["reserved_tokens"] for x in arm) + amount > self.plan["tokens_per_arm"]):
                raise c.ControllerError("pending, stopped or exhausted campaign/arm")
            ident = uuid.uuid4().hex
            self._append(fd, state, "reserve", call_id=ident, task=task, policy=policy,
                prompt_digest=prompt_digest, input_tokens=input_tokens, counter_id=counter_id,
                output_limit=self.plan["max_output_tokens"], reserved_tokens=amount)
            return ident

    def _transition(self, ident, required, kind, **fields):
        with self._locked() as (fd, state):
            call = state["calls"].get(ident)
            if call is None or call["state"] not in required:
                raise c.ControllerError("duplicate or out-of-order call transition")
            if kind in {"claim", "candidate", "evaluate"} and state["halted"]:
                raise c.ControllerError("campaign halted")
            if kind == "claim" and fields["prompt_digest"] != call["prompt_digest"]:
                raise c.ControllerError("dispatch prompt drift")
            if kind == "evaluate":
                arm = [x for x in state["calls"].values() if (x["task"], x["policy"]) == (call["task"], call["policy"])]
                if fields["candidate_digest"] != call["candidate_digest"] or sum(x.get("evaluation_reserved", False) for x in arm) >= self.plan["evaluations_per_arm"]:
                    raise c.ControllerError("candidate identity/evaluation budget")
                fields["evaluation_reserved"] = True
            self._append(fd, state, kind, call_id=ident, **fields)

    def claim(self, ident: str, prompt: dict):
        self._transition(ident, {"RESERVED"}, "claim", prompt_digest=_hash(prompt))

    def finish(self, ident: str, terminal: str, *, response: str | None, usage: dict | None):
        if terminal not in {"RECEIVED", "ERROR", "TIMEOUT"}:
            raise c.ControllerError("invalid transport terminal")
        if (terminal == "RECEIVED" and type(response) is not str) or (terminal != "RECEIVED" and response is not None):
            raise c.ControllerError("response/terminal mismatch")
        if response is not None and len(response.encode()) > c.MAX_RESPONSE_BYTES:
            raise c.ControllerError("response too large; terminate separately as error")
        with self._locked() as (fd, state):
            call = state["calls"].get(ident)
            if call is None or call["state"] != "DISPATCHED":
                raise c.ControllerError("call is not dispatched")
            valid = (type(usage) is dict and set(usage) == {"input_tokens", "output_tokens"}
                     and all(type(v) is int and v >= 0 for v in usage.values()))
            status = "MISSING" if usage is None else "MALFORMED" if not valid else "VALID"
            if valid and (usage["input_tokens"] > call["input_tokens"] or usage["output_tokens"] > call["output_limit"]):
                status = "OVERRUN"
            # Save raw reported usage, including malformed/overrun values, without
            # accepting it as measured tokens. Reject non-JSON payloads outright.
            if len(c.canonical(usage)) > 4096: raise c.ControllerError("usage size limit")
            self._append(fd, state, "finish", call_id=ident, terminal=terminal,
                response_digest=c.sha(response.encode()) if response is not None else None,
                usage=usage, usage_status=status, refund_tokens=0)

    def candidate(self, ident, candidate_digest):
        if not _digest(candidate_digest): raise c.ControllerError("invalid candidate identity")
        self._transition(ident, {"RECEIVED"}, "candidate", candidate_digest=candidate_digest)

    def reject(self, ident):
        self._transition(ident, {"RECEIVED"}, "reject")

    def evaluate(self, ident, candidate_digest):
        self._transition(ident, {"CANDIDATE"}, "evaluate", candidate_digest=candidate_digest)

    def feedback(self, ident, private_result):
        result = c.public_feedback(private_result)
        self._transition(ident, {"EVALUATING"}, "feedback", feedback=result)
        return result

    def snapshot(self):
        with self._locked() as (_, state):
            calls = list(state["calls"].values())
            return {"schema": SCHEMA, "mode": "OFFLINE_ONLY", "plan": self.plan,
                "reserved_calls": len(calls), "reserved_tokens": sum(x["reserved_tokens"] for x in calls),
                "claimed_dispatches": sum(any(e["event"] == "claim" and e["call_id"] == x["call_id"] for e in state["events"][1:]) for x in calls),
                "calls": calls, "halted": state["halted"],
                "stopped_arms": sorted(state["stopped_arms"]), "tail_digest": state["events"][-1]["digest"],
                "provider_calls": 0, "reported_usage_is_live_measurement": False}
