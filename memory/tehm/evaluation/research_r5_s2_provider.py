"""Bounded, non-retrying DeepSeek DEV transport; no model-owned verdicts.

The historical C6 ledger remains offline. This explicit live subclass reuses
its durable transitions with a separately validated authorization contract.
Request UTF-8 size plus 512 is a conservative admission heuristic, not an exact
provider tokenizer. Reserve the full 8000 input tokens; API usage is authoritative.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import socket
import urllib.error
import urllib.request

from . import research_r5_s2_campaign as campaign
from . import research_r5_s2_controller as c

MODEL = "deepseek-flash"
HOST = "api.deepseek.com"
COUNTER = "utf8-admission-full-8000-reservation-v1"


def live_plan(auth):
    return {"schema": campaign.SCHEMA, "mode": "LIVE_AUTHORIZED",
        "tasks": ["dev_task_001"], "counter_id": COUNTER,
        "calls_per_arm": 2, "tokens_per_arm": 20000, "evaluations_per_arm": 2,
        "calls_total": 6, "tokens_total": 60000, "max_input_tokens": 8000,
        "max_output_tokens": 2000, "authorization_digest": campaign._hash(auth),
        "model": MODEL, "provider_host": HOST}


def validate_live(plan, auth):
    expected = {"schema": "r5-s2-user-authorization-approved-v1",
        "role": "DEV_ONLY_NOT_FINAL", "retry_limit": 0, "authorized_tasks": 1,
        "authorized_policies": list(c.POLICIES), "maximum_calls_per_policy": 2,
        "maximum_total_calls": 6, "maximum_input_tokens_per_call": 8000,
        "maximum_output_tokens_per_call": 2000, "maximum_total_tokens": 60000,
        "model_version": "DeepSeek-V4.1-Flash", "official_api_model_id": MODEL,
        "provider_host": HOST}
    if type(auth) is not dict or any(auth.get(k) != v for k, v in expected.items()):
        raise c.ControllerError("missing or mismatched explicit DEV authorization")
    if plan != live_plan(auth):
        raise c.ControllerError("live plan drift")


class LiveLedger(campaign.CampaignLedger):
    def __init__(self, path, plan, *, authorization):
        validate_live(plan, authorization)
        self.path = Path(path).absolute()
        if self.path.parent.resolve(strict=True) != self.path.parent or self.path.is_symlink():
            raise c.ControllerError("linked live ledger path")
        self.plan = json.loads(c.canonical(plan))
        with self._locked():
            pass

    @classmethod
    def create(cls, path, plan, *, authorization):
        validate_live(plan, authorization)
        path = Path(path).absolute()
        if path.parent.resolve(strict=True) != path.parent:
            raise c.ControllerError("linked live parent")
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        try:
            event = {"sequence": 0, "previous_digest": None, "event": "init", "plan": plan}
            event["digest"] = campaign._hash(event)
            cls._write(fd, event)
        finally:
            os.close(fd)
        parent = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
        return cls(path, plan, authorization=authorization)

    def snapshot(self):
        result = super().snapshot()
        result.update(mode="LIVE_AUTHORIZED", provider_calls=None)
        return result


def flash_key(path: Path) -> str:
    """Parse literal assignments only; never execute env file or select Pro."""
    blocks = []
    for line in path.read_text().splitlines():
        match = re.fullmatch(r"\s*(?:export\s+)?(DEEPSEEK_[A-Z_]+)\s*=\s*(.*?)\s*", line)
        if not match:
            continue
        name, raw = match.groups()
        value = shlex.split(raw, comments=True)
        if len(value) != 1:
            raise c.ControllerError("non-literal provider configuration")
        if name == "DEEPSEEK_API_KEY":
            blocks.append({})
        if blocks:
            blocks[-1][name] = value[0]
    selected = [b for b in blocks if b.get("DEEPSEEK_MODEL") == "deepseek-v4.1-flash"
                and b.get("DEEPSEEK_BASE_URL", "").rstrip("/") == "https://" + HOST]
    if len(selected) != 1 or not selected[0].get("DEEPSEEK_API_KEY"):
        raise c.ControllerError("unique official Flash block required")
    key = selected[0]["DEEPSEEK_API_KEY"]
    if any(x in key for x in ("$", "`", "\r", "\n")):
        raise c.ControllerError("non-literal credential")
    return key


def request_body(prompt):
    body = {"model": MODEL, "messages": [
        {"role": "system", "content": prompt["system"]},
        {"role": "user", "content": c.canonical(prompt["user"]).decode()}],
        "thinking": {"type": "disabled"}, "max_tokens": 2000,
        "stream": False, "temperature": 0, "response_format": {"type": "json_object"}}
    if len(c.canonical(body)) + 512 > 8000:
        raise c.ControllerError("request exceeds conservative byte admission limit")
    return body


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def dispatch(body, key):
    """Exactly one HTTP request; never log headers, credentials or exception text."""
    request = urllib.request.Request("https://" + HOST + "/chat/completions",
        data=c.canonical(body), headers={"Authorization": "Bearer " + key,
        "Content-Type": "application/json"}, method="POST")
    opener = urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(request, timeout=60) as response:
            raw = response.read(c.MAX_RESPONSE_BYTES + 1)
            if len(raw) > c.MAX_RESPONSE_BYTES:
                return {"terminal": "ERROR", "http_status": response.status,
                        "error_kind": "RESPONSE_SIZE_LIMIT"}, b""
            return {"terminal": "RECEIVED", "http_status": response.status}, raw
    except urllib.error.HTTPError as exc:
        return {"terminal": "ERROR", "http_status": exc.code,
                "error_kind": "HTTP_ERROR"}, b""
    except (TimeoutError, socket.timeout):
        return {"terminal": "TIMEOUT", "http_status": None,
                "error_kind": "TRANSPORT_TIMEOUT"}, b""
    except (urllib.error.URLError, OSError):
        return {"terminal": "ERROR", "http_status": None,
                "error_kind": "TRANSPORT_ERROR"}, b""


def decode(raw):
    """Usage is validated independently from proposal parsing; never inferred."""
    data = json.loads(raw)
    usage = data.get("usage", {})
    keys = ("prompt_tokens", "completion_tokens", "total_tokens")
    if (any(type(usage.get(k)) is not int or usage[k] < 0 for k in keys)
            or usage["total_tokens"] != usage["prompt_tokens"] + usage["completion_tokens"]):
        raise c.ControllerError("missing or inconsistent provider usage")
    if "prompt_cache_hit_tokens" in usage or "prompt_cache_miss_tokens" in usage:
        hits, misses = usage.get("prompt_cache_hit_tokens"), usage.get("prompt_cache_miss_tokens")
        if (type(hits) is not int or type(misses) is not int or min(hits, misses) < 0
                or hits + misses != usage["prompt_tokens"]):
            raise c.ControllerError("inconsistent cache usage")
    choices = data.get("choices")
    if type(choices) is not list or len(choices) != 1:
        raise c.ControllerError("ambiguous response choices")
    choice = choices[0]
    content = choice.get("message", {}).get("content")
    if type(content) is not str:
        raise c.ControllerError("missing response content")
    return content, {"input_tokens": usage["prompt_tokens"], "output_tokens": usage["completion_tokens"]}, {
        "id": data.get("id"), "model": data.get("model"),
        "system_fingerprint": data.get("system_fingerprint"),
        "finish_reason": choice.get("finish_reason"), "usage": usage}
