"""One official-provider request, no SDK/internal retries, independent accounting."""
from __future__ import annotations

import json
from pathlib import Path
import time
import urllib.error
import urllib.request
import uuid

from bridge import Journal, digest, stable
from stream_transport import read_response


class UnknownUsage(RuntimeError):
    pass


class IncompleteResponse(BaseException):
    """Known billed usage, incomplete generation: never execute or retry."""


class MeteredProvider:
    def __init__(self, episode: Path, key: str, *, model="deepseek-flash"):
        self.episode, self.key, self.model = episode, key, model
        episode.mkdir(parents=True, exist_ok=False)
        self.journal = Journal(episode / "requests.jsonl")
        self.blocked = False

    def request(self, payload: dict, *, transport=None, purpose="decision"):
        if self.blocked:
            self.journal.append("request_refused", reason="previous UNKNOWN usage unresolved",
                                sent=False, model=self.model)
            raise UnknownUsage("previous request usage unresolved; no additional request sent")
        body = {**payload, "model": self.model, "stream": True,
                "stream_options": {"include_usage": True}}
        ident = uuid.uuid4().hex
        (self.episode / (ident + ".request.json")).write_text(stable(body) + "\n")
        self.journal.append("request_started", request_local_id=ident,
                            body_sha256=digest(body), body_bytes=len(stable(body).encode()),
                            model=self.model, purpose=purpose)
        started, cpu = time.monotonic(), time.process_time()
        request = urllib.request.Request("https://api.deepseek.com/chat/completions",
            data=stable(body).encode(), method="POST",
            headers={"Authorization": "Bearer " + self.key, "Content-Type": "application/json",
                     "Accept": "text/event-stream", "Connection": "close"})
        send = transport or urllib.request.urlopen
        try:
            with send(request, timeout=300) as response:
                headers = dict(response.headers)
                code = response.status
                data, complete = read_response(response, self.episode / (ident + ".response.raw"))
        except Exception as exc:
            self.blocked = True
            detail = str(getattr(exc, "reason", exc)).replace(self.key, "[REDACTED]")[:2000]
            status = getattr(exc, "code", None)
            if isinstance(exc, urllib.error.HTTPError):
                error_body = exc.read().replace(self.key.encode(), b"[REDACTED]")
                (self.episode / (ident + ".error-response.txt")).write_bytes(error_body)
            self.journal.append("request_ended", request_local_id=ident, usage="UNKNOWN",
                exception=type(exc).__name__, status=status, reason=detail, wall_s=time.monotonic()-started,
                cpu_s=time.process_time()-cpu)
            raise UnknownUsage("request failed; raw accounting retained, no retry") from exc
        (self.episode / (ident + ".response.json")).write_text(stable(data) + '\n')
        # Do not retain arbitrary proxy/cookie headers or authorization material.
        safe_headers = {k: v for k, v in headers.items()
                        if k.lower() in {"x-request-id", "request-id", "date", "content-type"}}
        try:
            usage = data.get("usage")
            if not isinstance(usage, dict) or any(type(usage.get(k)) is not int or usage[k] < 0
                    for k in ("prompt_tokens", "completion_tokens", "total_tokens")):
                raise ValueError("missing or invalid usage")
            if usage["prompt_tokens"] + usage["completion_tokens"] != usage["total_tokens"]:
                raise ValueError("usage arithmetic mismatch")
            if not data.get("model") or not data.get("id"):
                raise ValueError("missing provider model or response identity")
        except (ValueError, AttributeError) as exc:
            self.blocked = True
            self.journal.append("request_ended", request_local_id=ident, status=code,
                usage="UNKNOWN", response_headers=safe_headers,
                wall_s=time.monotonic()-started, cpu_s=time.process_time()-cpu)
            raise UnknownUsage("provider response lacks auditable usage") from exc
        self.journal.append("request_ended", request_local_id=ident, status=code,
            usage=usage, provider_model=data["model"], provider_id=data["id"],
            response_headers=safe_headers, wall_s=time.monotonic()-started,
            cpu_s=time.process_time()-cpu, api_cost=None,
            response_complete=complete,
            api_cost_note="derive later from pinned applicable pricing; never zero by default")
        if not complete:
            raise IncompleteResponse('stream ended before complete response; known usage retained, no retry')
        return data
