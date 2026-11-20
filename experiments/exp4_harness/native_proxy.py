"""Fixed-model Unix-socket gateway for the native CLIs, with all-request accounting.

Only this host process can contact the credentialed loopback relay. The isolated
container sees a Unix socket and cannot reach host TCP listeners. No prompts,
responses, authorization headers or provider keys are written to the ledger.
"""
from __future__ import annotations

import http.client
import json
import socketserver
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

HOP = {"host", "connection", "content-length", "transfer-encoding", "accept-encoding", "authorization", "x-api-key"}


def usage_record(events: list[dict], protocol: str) -> dict:
    """Normalize cumulative provider counters, including interrupted responses."""
    u, complete, response_model = {}, False, None
    for ev in events:
        kind = ev.get("type")
        if protocol == "anthropic":
            message = ev.get("message") or ev
            incoming = message.get("usage") or {}
            response_model = message.get("model") or response_model
            for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens"):
                if incoming.get(k) is not None:
                    u[k] = max(u.get(k, 0), int(incoming[k]))
            thinking = (incoming.get("output_tokens_details") or {}).get("thinking_tokens")
            if thinking is not None:
                u["reasoning_tokens"] = max(u.get("reasoning_tokens", 0), int(thinking))
            complete |= kind == "message_stop" or (kind == "message" and bool(ev.get("stop_reason")))
        else:
            response = ev.get("response") or ev
            incoming = response.get("usage") or {}
            response_model = response.get("model") or response_model
            if incoming:
                u = {"input_tokens": int(incoming.get("input_tokens") or 0),
                     "cache_read_input_tokens": int((incoming.get("input_tokens_details") or {}).get("cached_tokens") or 0),
                     "cache_creation_input_tokens": int((incoming.get("input_tokens_details") or {}).get("cache_write_tokens") or 0),
                     "output_tokens": int(incoming.get("output_tokens") or 0),
                     "reasoning_tokens": int((incoming.get("output_tokens_details") or {}).get("reasoning_tokens") or 0)}
            complete |= kind in ("response.completed", "response.incomplete") or response.get("status") in ("completed", "incomplete")
    reported = bool(u)
    cached = u.get("cache_read_input_tokens", 0)
    if protocol == "anthropic":
        # Anthropic separates cache reads/writes from ordinary input tokens.
        uncached = u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
        u["input_tokens"] = uncached + cached
    else:
        uncached = max(u.get("input_tokens", 0) - cached, 0)
    u["uncached_input_tokens"] = uncached
    u["total_tokens"] = u.get("input_tokens", 0) + u.get("output_tokens", 0)
    return {**u, "usage_reported": reported, "stream_complete": complete, "response_model": response_model}


class Ledger:
    def __init__(self, path: Path, model: str, max_calls: int):
        self.path, self.model, self.max_calls = path, model, max_calls
        self.calls: list[dict] = []
        self.lock = threading.Lock()
        self.stopped = False
        self.refused_requests = 0

    def reserve(self, metadata: dict) -> dict | None:
        # Reserve before forwarding: child agents cannot overshoot the call cap.
        with self.lock:
            if self.stopped or len(self.calls) >= self.max_calls:
                self.refused_requests += 1
                return None
            row = {"call_index": len(self.calls) + 1, "started": time.time(),
                   "finished": False, **metadata}
            self.calls.append(row)
            return row

    def finish(self, row: dict, values: dict):
        with self.lock:
            row.update(values, finished=True, seconds=round(time.time() - row["started"], 3))
            self.path.write_text(json.dumps(self.calls, indent=2))

    def snapshot(self):
        with self.lock:
            return [dict(c) for c in self.calls]


class UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        # A native CLI can close a stream after its budget is exhausted.
        pass


def make_proxy(socket_path: Path | tuple[str, int], upstream_url: str, protocol: str, ledger: Ledger,
               policy: dict | None = None):
    up = urlsplit(upstream_url)
    if up.scheme != "http" or up.hostname not in ("127.0.0.1", "localhost", "::1") or up.username or up.password or up.query or up.fragment:
        raise ValueError("upstream_url must be a credential-free local HTTP relay")
    route = "/messages" if protocol == "anthropic" else "/responses"
    endpoint = up.path.rstrip("/")
    if not endpoint.endswith(route):
        endpoint = endpoint + ("" if endpoint.endswith("/v1") else "/v1") + route

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def fail(self, status, message):
            raw = json.dumps({"error": {"message": message, "type": "exp4_gateway"}}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            if self.path.rstrip("/") not in (route, "/v1" + route):
                return self.fail(404, "unsupported API route")
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 32 * 1024 * 1024:
                return self.fail(413, "invalid request size")
            raw = self.rfile.read(size)
            try:
                body = json.loads(raw)
            except ValueError:
                return self.fail(400, "invalid JSON")
            if not isinstance(body, dict) or body.get("model") != ledger.model:
                return self.fail(400, "model must match the frozen experiment model")
            # One declared model policy across native and R2G arms; auxiliary
            # native requests pass through the same normalization and ledger.
            if policy:
                if protocol == "anthropic":
                    if policy.get("thinking") is not None:
                        body["thinking"] = {"type": "enabled" if policy["thinking"] else "disabled"}
                    if policy.get("reasoning_effort"):
                        body["output_config"] = {"effort": policy["reasoning_effort"]}
                    if policy.get("max_tokens"):
                        body["max_tokens"] = min(int(body.get("max_tokens") or policy["max_tokens"]), int(policy["max_tokens"]))
                    if policy.get("drop_temperature", True):
                        body.pop("temperature", None)
                else:
                    # Codex client attribution/cache-routing hints are optional
                    # transport metadata unsupported by the frozen relay. Both
                    # GPT arms use the provider's normal prefix caching instead.
                    body.pop("client_metadata", None)
                    body.pop("prompt_cache_key", None)
                    if policy.get("reasoning_effort"):
                        body["reasoning"] = {**(body.get("reasoning") or {}), "effort": policy["reasoning_effort"]}
                    if policy.get("max_tokens"):
                        body["max_output_tokens"] = min(int(body.get("max_output_tokens") or policy["max_tokens"]), int(policy["max_tokens"]))
                raw = json.dumps(body).encode()
            row = ledger.reserve({"model": body["model"], "protocol": protocol,
                                  "request_fields": sorted(body),
                                  "reasoning": body.get("reasoning"), "thinking": body.get("thinking"),
                                  "output_config": body.get("output_config"),
                                  "max_output_tokens": body.get("max_output_tokens", body.get("max_tokens"))})
            if row is None:
                return self.fail(429, "experiment model-call budget exhausted")
            conn = http.client.HTTPConnection(up.hostname, up.port or 80, timeout=600)
            events, pending, nonstream, status, connected, delivered = [], b"", bytearray(), None, False, True
            try:
                headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
                headers["Content-Type"] = "application/json"
                conn.request("POST", endpoint, body=raw, headers=headers)
                response = conn.getresponse()
                status = response.status
                self.send_response(status)
                for key, value in response.getheaders():
                    if key.lower() not in HOP:
                        self.send_header(key, value)
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                connected = True
                sse = "event-stream" in (response.getheader("Content-Type") or "")
                while True:
                    chunk = response.read1(65536)
                    if not chunk:
                        break
                    if delivered:
                        try:
                            self.wfile.write(chunk)
                            self.wfile.flush()
                        except (BrokenPipeError, ConnectionResetError):
                            delivered = False
                    if sse:
                        pending += chunk
                        while b"\n" in pending:
                            line, pending = pending.split(b"\n", 1)
                            if line.startswith(b"data:"):
                                try:
                                    event = json.loads(line[5:].strip())
                                    if isinstance(event, dict):
                                        events.append(event)
                                except ValueError:
                                    pass
                    else:
                        nonstream.extend(chunk)
                if nonstream:
                    try:
                        event = json.loads(nonstream)
                        if isinstance(event, dict):
                            events.append(event)
                    except ValueError:
                        pass
                ledger.finish(row, {"status": status, **usage_record(events, protocol), "delivered": delivered})
            except Exception as error:
                ledger.finish(row, {"status": status, **usage_record(events, protocol),
                                    "error": type(error).__name__, "delivered": False})
                if not connected:
                    self.fail(502, "model relay unavailable")
            finally:
                conn.close()

    if isinstance(socket_path, tuple):
        if socket_path[0] != "127.0.0.1":
            raise ValueError("TCP accounting gateways must bind loopback")
        return ThreadingHTTPServer(socket_path, Handler)
    return UnixServer(str(socket_path), Handler)
