"""EDA actions of Experiment 3's model-only arm, served to a native agent harness over MCP.

The native-harness arm (DeepSeek Harness, Codex) keeps the harness's own agent loop, file
and shell tools, and gets the two EDA actions an engineer would script by hand as MCP tools:
run_flow and run_signoff. They are the SAME BareTools calls, budgets and audit journal as the
model-only arm of Experiment 3 (experiments/bare_agent), so the only difference between the
arms is the agent harness.

Transport: MCP Streamable HTTP, JSON responses (no server-sent stream), stdlib only. The
server runs OUTSIDE the agent's sandbox (ORFS writes its workspace under the read-only
toolchain), on 127.0.0.1. EDA calls are serialized: one project, one flow at a time.

Integrity: after every run_flow the final GDS digest is recorded, after every run_signoff the
DRC/LVS report digests; state lives in the attempt directory, which the agent cannot see. The
runner trusts a report only if these digests still match.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class QuietServer(ThreadingHTTPServer):
    """A client dropping a keep-alive connection is normal; do not print a traceback for it."""
    daemon_threads = True

    def handle_error(self, request, client_address):
        import sys
        if not isinstance(sys.exc_info()[1], (ConnectionResetError, BrokenPipeError)):
            super().handle_error(request, client_address)
from pathlib import Path

PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
MAX_RESULT_CHARS = 12000            # same clip as the model-only arm's tool results
KEEPALIVE_S = 20                    # SSE comment interval during a long EDA call
STAGES = ("synth", "floorplan", "place", "cts", "route", "finish")

TOOLS = [
    {"name": "run_flow",
     "description": ("Run the OpenROAD-flow-scripts flow for this project from the given stage through finish "
                     "(synth, floorplan, place, cts, route, finish). Earlier stages are reused from the last run. "
                     "Returns status, failing stage, run directory and the log tail; full logs are under the "
                     "project's backend/<run>/ directory. Takes minutes to hours."),
     "inputSchema": {"type": "object", "properties": {"from_stage": {"type": "string", "enum": list(STAGES)}}}},
    {"name": "run_signoff",
     "description": ("Run full-deck DRC and LVS (and RCX) on the latest completed layout with the platform's "
                     "signoff decks. Returns DRC status and violation counts per rule, and LVS status. Reports "
                     "are written to the project's reports/ (drc.json, lvs.json) and logs to drc/, lvs/. "
                     "Takes minutes to hours."),
     "inputSchema": {"type": "object", "properties": {}}},
]


def sha(p: Path) -> str | None:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


class EdaService:
    """Budgeted run_flow / run_signoff over one BareTools instance."""

    def __init__(self, tools, caps: dict, state_path: Path, memory_bound):
        self.tools, self.caps, self.state_path = tools, caps, state_path
        self.memory_bound = memory_bound
        self.lock = threading.Lock()
        self.calls: list[dict] = []
        self.flow_gds: dict[str, str | None] = {}     # run name -> final GDS digest at flow end
        self.last_signoff: dict | None = None         # report + GDS digests of the last signoff
        self._save()

    def _gds(self, run):
        for c in (run / "results/6_final.gds", run / "final/6_final.gds") if run else ():
            if c.is_file():
                return c
        return None

    def _save(self):
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"counts": self.tools.counts, "eda_seconds": self.tools.eda_seconds,
                                   "calls": self.calls, "flow_gds": self.flow_gds,
                                   "last_signoff": self.last_signoff}, indent=1, default=str))
        tmp.replace(self.state_path)

    def call(self, name: str, args: dict) -> tuple[dict, bool]:
        if name not in ("run_flow", "run_signoff"):
            return {"error": "unknown tool %s" % name}, True
        with self.lock:
            t0 = time.time()
            if self.memory_bound():
                result, err = {"error": "attempt stopped: an EDA step exceeded the 64 GB memory bound"}, True
            elif self.tools.counts[name] >= self.caps[name]:
                result, err = {"error": "budget exhausted for %s" % name}, True
            else:
                try:
                    if name == "run_flow":
                        stage = (args or {}).get("from_stage", "synth")
                        if stage not in STAGES:
                            raise ValueError("from_stage must be one of %s" % ", ".join(STAGES))
                        result = self.tools.run_flow(stage)
                        run = self.tools._latest_run()
                        if run:
                            self.flow_gds[run.name] = sha(self._gds(run)) if self._gds(run) else None
                    else:
                        result = self.tools.run_signoff()
                        run = self.tools._latest_run()
                        rep = self.tools.project / "reports"
                        self.last_signoff = {"run": run.name if run else None,
                                             "gds": sha(self._gds(run)) if self._gds(run) else None,
                                             "drc": sha(rep / "drc.json"), "lvs": sha(rep / "lvs.json"),
                                             "at": time.time()}
                    err = False
                except Exception as e:                       # noqa: BLE001 -- the model sees it, the attempt goes on
                    result, err = {"error": "tool failed: %s: %s" % (type(e).__name__, e)}, True
            self.calls.append({"tool": name, "arguments": args, "started": t0,
                               "seconds": round(time.time() - t0, 1), "error": err,
                               "result": result if len(json.dumps(result, default=str)) < 4000 else "(see project logs)"})
            self._save()
            return result, err


def make_server(service: EdaService, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    sessions: set[str] = set()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _send(self, code: int, body: dict | None = None, headers: dict | None = None):
            data = json.dumps(body).encode() if body is not None else b""
            self.send_response(code)
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            if body is not None:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):                     # no server-initiated stream
            self._send(405, {"error": "method not allowed"}, {"Allow": "POST, DELETE"})

        def do_DELETE(self):
            sessions.discard(self.headers.get("Mcp-Session-Id", ""))
            self._send(200, {})

        def do_POST(self):
            if self.path.rstrip("/") != "/mcp":
                return self._send(404, {"error": "not found"})
            try:
                msg = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            except (ValueError, UnicodeDecodeError):
                return self._send(400, {"jsonrpc": "2.0", "id": None,
                                        "error": {"code": -32700, "message": "parse error"}})
            # A lone tools/call from a client that accepts SSE is answered as a stream: headers at once,
            # a keep-alive comment every KEEPALIVE_S while the EDA call runs, then the result event.
            # A plain JSON response sends nothing until the flow ends, and Node's fetch (DeepSeek
            # Harness, 2026-11-12) gives up on a silent response after 300 s: every flow or signoff
            # longer than 5 min came back to the agent as "fetch failed".
            if (isinstance(msg, dict) and msg.get("method") == "tools/call" and msg.get("id") is not None
                    and "text/event-stream" in self.headers.get("Accept", "")):
                return self._stream_call(msg)
            batch = msg if isinstance(msg, list) else [msg]
            out, extra = [], {}
            for m in batch:
                mid, method, params = m.get("id"), m.get("method"), m.get("params") or {}
                if mid is None:                # notification (initialized, cancelled, ...)
                    continue
                if method == "initialize":
                    want = params.get("protocolVersion")
                    sid = uuid.uuid4().hex
                    sessions.add(sid)
                    extra["Mcp-Session-Id"] = sid
                    res = {"protocolVersion": want if want in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[1],
                           "capabilities": {"tools": {"listChanged": False}},
                           "serverInfo": {"name": "eda", "version": "1.0"},
                           "instructions": ("EDA actions for this project: run_flow and run_signoff. "
                                            "Budgets are enforced here.")}
                elif method == "ping":
                    res = {}
                elif method == "tools/list":
                    res = {"tools": TOOLS}
                elif method == "tools/call":
                    result, err = service.call(params.get("name", ""), params.get("arguments") or {})
                    text = json.dumps(result, ensure_ascii=False, default=str)
                    if len(text) > MAX_RESULT_CHARS:
                        text = text[:MAX_RESULT_CHARS] + "...(truncated)"
                    res = {"content": [{"type": "text", "text": text}], "isError": err}
                else:
                    out.append({"jsonrpc": "2.0", "id": mid,
                                "error": {"code": -32601, "message": "method not found: %s" % method}})
                    continue
                out.append({"jsonrpc": "2.0", "id": mid, "result": res})
            if not out:
                return self._send(202, None)
            self._send(200, out if isinstance(msg, list) else out[0], extra)

        def _stream_call(self, msg: dict):
            params = msg.get("params") or {}
            box: dict = {}
            done = threading.Event()

            def work():
                try:
                    box["r"] = service.call(params.get("name", ""), params.get("arguments") or {})
                finally:
                    done.set()

            threading.Thread(target=work, daemon=True).start()
            self.close_connection = True
            alive = True
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.flush()
                while not done.wait(KEEPALIVE_S):
                    self.wfile.write(b": eda call running\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                alive = False          # the client went away; the EDA call still completes and is recorded
            done.wait()
            if not alive:
                return
            result, err = box.get("r", ({"error": "tool failed"}, True))
            text = json.dumps(result, ensure_ascii=False, default=str)
            if len(text) > MAX_RESULT_CHARS:
                text = text[:MAX_RESULT_CHARS] + "...(truncated)"
            body = {"jsonrpc": "2.0", "id": msg["id"],
                    "result": {"content": [{"type": "text", "text": text}], "isError": err}}
            try:
                self.wfile.write(b"event: message\ndata: " + json.dumps(body).encode() + b"\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

    return QuietServer((host, port), Handler)
