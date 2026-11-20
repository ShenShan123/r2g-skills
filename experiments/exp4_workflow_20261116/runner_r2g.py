#!/usr/bin/env python3
"""R2G model loop with an explicit, installed generation-and-validation tool."""
from __future__ import annotations

import argparse
from contextvars import ContextVar
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
from types import SimpleNamespace

sys.path[:0] = ["/agent", "/agent/deps", "/r2g"]


def execute(command, log_path, timeout=3600):
    with Path(log_path).open("w+", encoding="utf-8", errors="replace") as log:
        process = subprocess.Popen(command, cwd="/work", stdout=log,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        try:
            process.wait(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            timed_out = True
        log.seek(0)
        output = log.read()
    return {"exit_code": process.returncode, "timed_out": timed_out,
            "output": output[-11000:], "output_chars": len(output), "log": str(log_path)}


class DatasetTools:
    CURRENT_SESSION = ContextVar("exp4_session", default=None)
    task_kind = "graph"

    @classmethod
    def specs(cls):
        return [
            {"name": "generate_and_validate",
             "description": "Use the installed R2G scripts to generate the dataset and validate it against the physical inputs. Runs selected development designs sequentially; omitted designs means all 18. Returns validation outcomes and saved reports. This capability needs no converter implementation.",
             "parameters": {"type": "object", "properties": {
                 "designs": {"type": "array", "items": {"type": "string"},
                             "description": "Development design directory names under /data/dev."},
                 "output_dir": {"type": "string", "description": "Output root under /work; default /work/validation."}},
                 "required": []}},
            {"name": "shell", "description": "Inspect inputs and validation reports, or write the reusable launch wrapper for the installed R2G workflow.",
             "parameters": {"type": "object", "properties": {
                 "command": {"type": "string"},
                 "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 3600}},
                 "required": ["command"]}}]

    @classmethod
    def call(cls, name, args):
        if name == "shell":
            return execute(["bash", "-lc", args["command"]], "/work/tool-output.log",
                           min(max(int(args.get("timeout_seconds", 300)), 1), 3600))
        if name != "generate_and_validate":
            return {"error": "Unknown tool: " + name}
        from generate_and_validate import summary
        root = Path("/data/dev")
        selected = args.get("designs", sorted(p.name for p in root.iterdir() if p.is_dir()))
        output = Path(args.get("output_dir", "/work/validation")).resolve()
        if not output.is_relative_to("/work"):
            return {"error": "output_dir must be inside /work"}
        output.mkdir(parents=True, exist_ok=True)
        results = []
        for design in selected:
            source = (root / design).resolve()
            if not source.is_relative_to(root) or not (source / "config.mk").is_file():
                return {"error": "Unknown development design: " + design, "results": results}
            match = re.search(r"^\s*(?:export\s+)?PLATFORM\s*[:?+]?=\s*(\S+)",
                              (source / "config.mk").read_text(), re.M)
            platform = Path("/data/platform") / match[1]
            destination = output / source.name
            destination.mkdir(parents=True, exist_ok=True)
            execution = execute(["bash", f"/r2g/generate_{cls.task_kind}.sh",
                                 str(source), str(platform), str(destination)],
                                destination / "tool.log", 1800)
            report = destination / "validation.json"
            result = summary(json.loads(report.read_text())) if report.is_file() else {
                "design": source.name, "status": "failed", "report": None}
            # A killed conversion must never inherit a previous passing report.
            if execution["exit_code"]:
                result.update(status="failed", execution=execution)
            results.append(result)
        return {"task_kind": cls.task_kind, "passed": sum(r["status"] == "passed" for r in results),
                "total": len(results), "results": results}


def dataset_system_prompt(config):
    kind = config.get("task_kind", "graph")
    if kind not in ("graph", "image"):
        raise ValueError("task_kind must be graph or image")
    return ("You are the R2G dataset-production agent. Use generate_and_validate to apply "
            "the installed generation and verification workflow to the supplied physical designs. "
            f"The reusable production entrypoint is /r2g/generate_{kind}.sh "
            "DESIGN_DIR PLATFORM_DIR OUTPUT_DIR. Follow /experiment/task.txt. "
            "Deliver a thin /work/converter/run.sh that invokes this entrypoint; the installed "
            "runtime already implements the dataset conversion and physical checks. Inspect its "
            "reports as needed. Runtime sources are read-only. Do not reimplement conversion "
            "or validation, or rerun synthesis/place-and-route. Report unresolved failures honestly.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    DatasetTools.task_kind = config.get("task_kind", "graph")
    os.environ["R2G_HOME"] = "/work/.r2g"
    sys.modules["r2g_agent.tools"] = DatasetTools
    from r2g_agent import agent_loop
    from r2g_agent.llm.anthropic import AnthropicAdapter
    from r2g_agent.llm.openai_responses import OpenAIResponsesAdapter
    protocol = os.environ.get("EXP4_PROTOCOL", config["protocol"])
    base = os.environ["EXP4_API_BASE_URL"].removesuffix("/v1")
    model = SimpleNamespace(provider="exp4", model=config["model"], protocol=protocol,
                            base_url=base, max_tokens=32768, temperature=1.0, thinking=True)
    adapter = (AnthropicAdapter(model, "exp4-local") if protocol == "anthropic" else
               OpenAIResponsesAdapter(model, "exp4-local", reasoning_effort="medium"))
    agent_loop.tools = DatasetTools
    agent_loop.MAX_STEPS = int(config.get("caps", {}).get("model_calls", 200))
    agent_loop.system_prompt = lambda: dataset_system_prompt(config)
    store = agent_loop.SessionStore(Path("/work/sessions.sqlite"))
    session = store.create()
    for event in agent_loop.run_turn(adapter, store, session, Path("/experiment/task.txt").read_text()):
        print(json.dumps(event, ensure_ascii=False), flush=True)
    Path("/work/r2g_usage.json").write_text(json.dumps(store.usage(session), indent=2))
    return 0 if Path("/work/converter/run.sh").is_file() else 2


if __name__ == "__main__":
    raise SystemExit(main())
