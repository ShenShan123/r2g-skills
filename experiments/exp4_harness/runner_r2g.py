#!/usr/bin/env python3
"""Dataset-only entrypoint for the real R2G model/tool loop, inside the sandbox.

Physical implementation is already complete in this experiment. Replace the
RTL/job UI tools with one shell tool for building and checking the converter;
retain agent_loop, SessionStore and the model adapters. No job replay is used.
"""
from __future__ import annotations

import argparse
from contextvars import ContextVar
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from types import SimpleNamespace

sys.path[:0] = ["/agent", "/agent/deps"]


class DatasetTools:
    CURRENT_SESSION = ContextVar("exp4_session", default=None)

    @staticmethod
    def specs():
        return [{"name": "shell", "description": "Run a shell command in the isolated development workspace. Read the skill and format documents, create a reusable converter, and test it on development designs.",
                 "parameters": {"type": "object", "properties": {
                     "command": {"type": "string"},
                     "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 3600}},
                     "required": ["command"]}}]

    @staticmethod
    def call(name, args):
        if name != "shell":
            return {"error": "Unknown tool: " + name}
        with open("/work/tool-output.log", "w+", encoding="utf-8", errors="replace") as log:
            p = subprocess.Popen(["bash", "-lc", args["command"]], cwd="/work", stdout=log,
                                 stderr=subprocess.STDOUT, start_new_session=True)
            timeout = min(max(int(args.get("timeout_seconds", 300)), 1), 3600)
            try:
                p.wait(timeout=timeout)
                timed_out = False
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)
                p.wait()
                timed_out = True
            log.seek(0)
            output = log.read()
        return {"exit_code": p.returncode, "timed_out": timed_out,
                "output": output[-11000:], "output_chars": len(output)}


def dataset_system_prompt(config):
    kind = config.get("task_kind", "graph")
    if kind == "image":
        product = (
            "For image conversion, use the image-dataset section of the installed "
            "def-graph skill at /r2g/def-graph/SKILL.md and its image extraction scripts. "
            "The image-only entrypoint is /r2g/convert_image.sh DESIGN_DIR PLATFORM_DIR OUTPUT_DIR. "
            "Produce the placement feature maps and routed congestion label maps "
            "required by the image contract.")
    elif kind == "graph":
        product = (
            "For graph conversion, use the installed def-graph skill at "
            "/r2g/def-graph/SKILL.md and its scripts. The graph-only entrypoint is "
            "/r2g/convert_graph.sh DESIGN_DIR PLATFORM_DIR OUTPUT_DIR.")
    else:
        raise ValueError("task_kind must be graph or image")
    return (
        "You are the R2G dataset-production agent. The request in /experiment/task.txt "
        "defines the work and required checks, including when it requests only an "
        "infrastructure smoke check. " + product + " "
        "The physical inputs are complete; do not rerun synthesis or place-and-route. "
        "Use the shell tool to inspect, develop, and validate. For a converter-development "
        "request, deliver a reusable /work/converter/run.sh and test development inputs "
        "as requested. It may call the installed /r2g runtime. The hidden evaluation "
        "inputs and scorer are unavailable.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=Path, required=True)
    args = ap.parse_args()
    cfg = json.loads(args.config.read_text())
    os.environ["R2G_HOME"] = "/work/.r2g"
    # Import the loop with the dataset tool surface; no RTL pipeline is needed.
    sys.modules["r2g_agent.tools"] = DatasetTools
    from r2g_agent import agent_loop
    from r2g_agent.llm.anthropic import AnthropicAdapter
    from r2g_agent.llm.openai_responses import OpenAIResponsesAdapter
    protocol = os.environ.get("EXP4_PROTOCOL", cfg["protocol"])
    base = os.environ["EXP4_API_BASE_URL"].removesuffix("/v1")
    mc = SimpleNamespace(provider="exp4", model=cfg["model"], protocol=protocol,
                         base_url=base, max_tokens=32768, temperature=1.0, thinking=True)
    adapter = (AnthropicAdapter(mc, "exp4-local") if protocol == "anthropic" else
               OpenAIResponsesAdapter(mc, "exp4-local", reasoning_effort="medium"))
    agent_loop.tools = DatasetTools
    agent_loop.MAX_STEPS = int(cfg.get("caps", {}).get("model_calls", 200))
    agent_loop.system_prompt = lambda: dataset_system_prompt(cfg)
    store = agent_loop.SessionStore(Path("/work/sessions.sqlite"))
    sid = store.create()
    for event in agent_loop.run_turn(adapter, store, sid, Path("/experiment/task.txt").read_text()):
        print(json.dumps(event, ensure_ascii=False), flush=True)
    Path("/work/r2g_usage.json").write_text(json.dumps(store.usage(sid), indent=2))
    return 0 if Path("/work/converter/run.sh").is_file() else 2


if __name__ == "__main__":
    raise SystemExit(main())
