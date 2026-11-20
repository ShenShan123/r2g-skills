#!/usr/bin/env python3
"""End-to-end check of eda_mcp.py under the real DeepSeek Harness: a run_flow that takes longer
than Node fetch's 300 s silence limit must still return its result to the agent.

  test_long_call.py --dsh DSH --node-dir NODE --work DIR [--seconds 420]
Prints PASS/FAIL. Uses the relay via DEEPSEEK_BASE_URL (default http://127.0.0.1:18776).
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import eda_mcp  # noqa: E402


class FakeTools:
    def __init__(self, project: Path, seconds: int):
        self.project, self.seconds = project, seconds
        self.counts = {"run_flow": 0, "run_signoff": 0}
        self.eda_seconds = 0.0

    def run_flow(self, stage):
        self.counts["run_flow"] += 1
        time.sleep(self.seconds)
        self.eda_seconds += self.seconds
        return {"status": "ok", "marker": "LONG-CALL-RESULT-7f3a", "slept": self.seconds}

    def run_signoff(self):
        self.counts["run_signoff"] += 1
        return {"drc": {"status": "clean"}}

    def _latest_run(self):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsh", required=True)
    ap.add_argument("--node-dir", required=True)
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--seconds", type=int, default=420)
    ap.add_argument("--relay", default="http://127.0.0.1:18776")
    a = ap.parse_args()
    ws, home = a.work / "ws", a.work / "home"
    ws.mkdir(parents=True, exist_ok=True)
    (home / "profiles/headless").mkdir(parents=True, exist_ok=True)
    svc = eda_mcp.EdaService(FakeTools(ws, a.seconds), {"run_flow": 5, "run_signoff": 5},
                             a.work / "mcp_state.json", lambda: False)
    srv = eda_mcp.make_server(svc)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/mcp" % srv.server_address[1]
    (home / "profiles/headless/cordis.patch.yml").write_text(
        "- insert:\n    - id: mcp-eda\n      name: '@deepseek-ai/dsh-mcp-client'\n      config:\n"
        "        serverName: eda\n        transport: streamable-http\n        url: %s\n"
        "        toolCallTimeoutMs: 3600000\n        failOnStartupError: true\n" % url)
    env = {"PATH": "%s/bin:/usr/bin:/bin" % a.node_dir, "HOME": str(home), "DSH_HOME": str(home),
           "DEEPSEEK_API_KEY": "relay-dummy", "DEEPSEEK_BASE_URL": a.relay, "LANG": "C.UTF-8"}
    task = ("Call the MCP tool run_flow of server eda exactly once with from_stage synth. It takes several "
            "minutes; wait for it. Then reply with the exact value of the field 'marker' from its result. "
            "Do not call any other tool.")
    p = subprocess.run([a.dsh, "headless", "--permission-mode", "workspace-write", "--approval-policy", "never",
                        "--json", "-"], input=task.encode(), capture_output=True, cwd=ws, env=env, timeout=3600)
    ev = [json.loads(l) for l in p.stdout.decode().splitlines() if l.startswith("{")]
    res = [e for e in ev if e["type"] == "tool_result"]
    final = next((e for e in ev if e["type"] == "final"), {})
    got = any("LONG-CALL-RESULT-7f3a" in json.dumps(r) for r in res)
    print(json.dumps({"rc": p.returncode, "tool_results": [{"status": r.get("status"), "result": str(r.get("result"))[:160]}
                                                           for r in res], "final": str(final.get("text"))[:200]}, indent=1))
    print("PASS" if got and all(r.get("status") == "completed" for r in res) else "FAIL")


if __name__ == "__main__":
    main()
