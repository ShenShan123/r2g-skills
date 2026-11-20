"""Per-attempt accounting proxy between a native agent harness and the model relay.

Every model request the harness makes (main agent, subagents, title generation) passes through
here unchanged; the provider's own usage counters are read from the response and recorded per
call, so budgets are enforced on what was billed, independently of the harness's own reporting.

Protocols: Anthropic Messages (DeepSeek Harness -> DeepSeek /anthropic), and OpenAI Responses
(Codex), streamed or not. Once a cap is reached, further requests are refused with HTTP 429;
the runner stops the harness at the same time. Request and response bodies are not stored.
"""
from __future__ import annotations

import http.client
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class QuietServer(ThreadingHTTPServer):
    """A client dropping a keep-alive connection is normal; do not print a traceback for it."""
    daemon_threads = True

    def handle_error(self, request, client_address):
        import sys
        if not isinstance(sys.exc_info()[1], (ConnectionResetError, BrokenPipeError)):
            super().handle_error(request, client_address)
from urllib.parse import urlsplit

HOP = {"host", "content-length", "connection", "transfer-encoding", "keep-alive", "accept-encoding"}
# DeepSeek peak prices, CNY per 1M tokens (same as Experiment 3's model-only arm)
PEAK_CNY = {"uncached": 2.0, "cached": 0.04, "output": 8.0}


def call_cost_cny(c: dict) -> float:
    return ((c.get("uncached_input_tokens") or 0) * PEAK_CNY["uncached"]
            + (c.get("cache_read_input_tokens") or 0) * PEAK_CNY["cached"]
            + (c.get("output_tokens") or 0) * PEAK_CNY["output"]) / 1e6


class Usage:
    """Fold the usage fields of one response, whatever the protocol."""

    def __init__(self):
        self.u = {}
        self.complete = False
        self.stop_reason = None

    def anthropic(self, ev: dict):
        t = ev.get("type")
        if t == "message_start":
            self._a((ev.get("message") or {}).get("usage") or {})
        elif t == "message_delta":
            self._a(ev.get("usage") or {})
            self.stop_reason = (ev.get("delta") or {}).get("stop_reason") or self.stop_reason
        elif t == "message_stop":
            self.complete = True
        elif t == "message":                     # non-streamed body
            self._a(ev.get("usage") or {})
            self.stop_reason = ev.get("stop_reason")
            self.complete = True

    def _a(self, u: dict):
        # message_delta carries cumulative counts; keep the largest value seen per field
        for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens"):
            if u.get(k) is not None:
                self.u[k] = max(self.u.get(k) or 0, int(u[k]))

    def responses(self, ev: dict):
        t = ev.get("type")
        resp = ev.get("response") if t and t.startswith("response.") else (ev if ev.get("object") == "response" else None)
        if resp and resp.get("usage"):
            u = resp["usage"]
            cached = (u.get("input_tokens_details") or {}).get("cached_tokens") or 0
            self.u = {"input_tokens": u.get("input_tokens") or 0, "cache_read_input_tokens": cached,
                      "output_tokens": u.get("output_tokens") or 0,
                      "reasoning_tokens": (u.get("output_tokens_details") or {}).get("reasoning_tokens") or 0}
        if t in ("response.completed", "response.incomplete") or (ev.get("object") == "response" and ev.get("status")):
            self.complete = True
            self.stop_reason = (resp or {}).get("status")

    def record(self, protocol: str) -> dict:
        u = dict(self.u)
        if protocol == "anthropic":
            # Messages input_tokens excludes cache reads
            u["uncached_input_tokens"] = u.get("input_tokens") or 0
            u["input_tokens"] = (u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
        else:
            u["uncached_input_tokens"] = max((u.get("input_tokens") or 0) - (u.get("cache_read_input_tokens") or 0), 0)
        u["total_tokens"] = (u.get("input_tokens") or 0) + (u.get("output_tokens") or 0)
        return u


class Ledger:
    def __init__(self, caps: dict):
        self.caps = caps
        self.calls: list[dict] = []
        self.failed: list[dict] = []
        self.lock = threading.Lock()
        self.stopped: str | None = None

    def capped(self) -> str | None:
        with self.lock:
            if len(self.calls) >= self.caps["model_calls"]:
                return "cap_model_calls"
            if sum(call_cost_cny(c) for c in self.calls) >= self.caps["cost_cny"]:
                return "cap_cost"
            return None


def make_proxy(upstream: str, ledger: Ledger, protocol: str, host="127.0.0.1", port=0) -> ThreadingHTTPServer:
    up = urlsplit(upstream)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_GET(self):
            self._relay("GET", b"")

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self._relay("POST", body)

        def _relay(self, method, body):
            model_call = method == "POST" and self.path.split("?")[0].endswith(("/messages", "/responses"))
            cap = ledger.capped() if model_call else None
            if cap or (model_call and ledger.stopped):
                data = json.dumps({"type": "error", "error": {"type": "rate_limit_error",
                                   "message": "budget exhausted: %s" % (cap or ledger.stopped)}}).encode()
                self.send_response(429)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            hdrs = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
            t0 = time.time()
            usage = Usage()
            try:
                conn = http.client.HTTPConnection(up.hostname, up.port or 80, timeout=600)
                conn.request(method, (up.path.rstrip("/") + self.path) if up.path not in ("", "/") else self.path,
                             body=body if method == "POST" else None, headers=hdrs)
                resp = conn.getresponse()
            except Exception as e:                       # noqa: BLE001
                ledger.failed.append({"error": "upstream unreachable: %s" % type(e).__name__, "at": t0})
                self.send_response(502)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(resp.status)
            for k, v in resp.getheaders():
                if k.lower() not in HOP:
                    self.send_header(k, v)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            buf, raw_json = b"", bytearray()
            sse = "event-stream" in (resp.getheader("Content-Type") or "")
            ok = True
            try:
                while True:
                    chunk = resp.read1(65536) if hasattr(resp, "read1") else resp.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                    self.wfile.flush()
                    if not model_call or resp.status != 200:
                        continue
                    if sse:
                        buf += chunk
                        while b"\n" in buf:
                            line, buf = buf.split(b"\n", 1)
                            if line.startswith(b"data:"):
                                try:
                                    ev = json.loads(line[5:].strip())
                                except ValueError:
                                    continue
                                (usage.anthropic if protocol == "anthropic" else usage.responses)(ev)
                    else:
                        raw_json.extend(chunk)
                self.wfile.write(b"0\r\n\r\n")
            except (BrokenPipeError, ConnectionResetError):
                ok = False                               # the harness went away mid-stream
            finally:
                conn.close()
            if model_call:
                if not sse and raw_json:
                    try:
                        ev = json.loads(raw_json)
                        (usage.anthropic if protocol == "anthropic" else usage.responses)(
                            {**ev, "type": "message"} if protocol == "anthropic" else ev)
                    except ValueError:
                        pass
                rec = {**usage.record(protocol), "status": resp.status, "stream_complete": usage.complete and ok,
                       "usage_reported": bool(usage.u), "stop_reason": usage.stop_reason,
                       "started": t0, "seconds": round(time.time() - t0, 2)}
                with ledger.lock:
                    if resp.status == 200:
                        rec["call_index"] = len(ledger.calls) + 1
                        ledger.calls.append(rec)
                    else:
                        ledger.failed.append(rec)

    return QuietServer((host, port), Handler)
