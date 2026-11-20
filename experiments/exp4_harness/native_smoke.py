#!/usr/bin/env python3
"""Exercise native API transport against a local fake provider; never bills a model."""
from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from runner_native import run_native


class FakeProvider(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append({"model": body.get("model"), "path": self.path,
                                     "request_fields": sorted(body),
                                     "tools": [t.get("name", (t.get("function") or {}).get("name")) for t in body.get("tools", [])]})
        model = body["model"]
        if self.path.endswith("/messages"):
            events = [{"type": "message_start", "message": {"id": "msg_smoke", "type": "message", "role": "assistant", "model": model, "content": [], "usage": {"input_tokens": 10, "output_tokens": 0}}},
                      {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                      {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Infrastructure smoke complete."}},
                      {"type": "content_block_stop", "index": 0},
                      {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 5}},
                      {"type": "message_stop"}]
        else:
            message = {"id": "msg_smoke", "type": "message", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": "Infrastructure smoke complete.", "annotations": []}]}
            response = {"id": "resp_smoke", "object": "response", "created_at": 1, "model": model, "status": "completed", "output": [message],
                        "usage": {"input_tokens": 10, "input_tokens_details": {"cached_tokens": 2}, "output_tokens": 5, "output_tokens_details": {"reasoning_tokens": 0}, "total_tokens": 15}}
            events = [{"type": "response.created", "response": {**response, "status": "in_progress", "output": [], "usage": None}},
                      {"type": "response.output_item.added", "output_index": 0, "item": {**message, "status": "in_progress", "content": []}},
                      {"type": "response.content_part.added", "item_id": "msg_smoke", "output_index": 0, "content_index": 0, "part": {"type": "output_text", "text": "", "annotations": []}},
                      {"type": "response.output_text.delta", "item_id": "msg_smoke", "output_index": 0, "content_index": 0, "delta": "Infrastructure smoke complete."},
                      {"type": "response.output_text.done", "item_id": "msg_smoke", "output_index": 0, "content_index": 0, "text": "Infrastructure smoke complete."},
                      {"type": "response.output_item.done", "output_index": 0, "item": message},
                      {"type": "response.completed", "response": response}]
        raw = b"".join(("event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n").encode() for event in events)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeProvider)
    server.requests = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    config["upstream_url"] = "http://127.0.0.1:%d/v1" % server.server_address[1]
    config["caps"] = {**config.get("caps", {}), "model_calls": 4, "wall_seconds": 90}
    try:
        result = run_native(config)
        result["fake_provider_requests"] = server.requests
        print(json.dumps(result))
        return 0 if result["exit_code"] == 0 and result["usage_complete"] else 1
    finally:
        server.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
