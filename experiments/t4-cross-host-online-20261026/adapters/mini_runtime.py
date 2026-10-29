"""Native mini-SWE-agent transport and unverified-experience adapter.

An arbitrary successful shell command is never interpreted as a solved issue.
Official SWE-bench evaluation is a separate required boundary before launch.
"""
from __future__ import annotations

import dataclasses
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from bridge import ROOT, State
from native_evidence import receipt

sys.path.insert(0, str(ROOT / "sources/mini-swe-agent/src"))


class MiniProvider:
    def __init__(self, provider):
        self.provider = provider

    def query(self, messages):
        from minisweagent.models.utils.actions_toolcall import BASH_TOOL, parse_toolcall_actions
        prepared = [{k: v for k, v in m.items() if k != "extra"} for m in messages]
        data = self.provider.request({"messages": prepared, "tools": [BASH_TOOL]}, purpose="native_agent")
        message = data["choices"][0]["message"]
        calls = [SimpleNamespace(id=t["id"], function=SimpleNamespace(**t["function"]))
                 for t in message.get("tool_calls") or []]
        actions = parse_toolcall_actions(calls, format_error_template="{{error}}",
            template_kwargs={"finish_reason": data["choices"][0].get("finish_reason")})
        return {**message, "extra": {"actions": actions, "usage": data["usage"],
                                     "provider_id": data["id"], "api_cost": None}}

    def format_message(self, **kwargs):
        return kwargs

    def format_observation_messages(self, message, outputs, template_vars=None):
        from minisweagent.models.utils.actions_toolcall import format_toolcall_observation_messages
        return format_toolcall_observation_messages(actions=message["extra"]["actions"],
            outputs=outputs, observation_template=("{% if output.exception_info %}"
            "<exception>{{output.exception_info}}</exception>{% endif %}"
            "<returncode>{{output.returncode}}</returncode><output>{{output.output}}</output>"),
            template_vars=template_vars)

    def get_template_vars(self):
        return {"model_name": self.provider.model}

    def serialize(self):
        return {"info": {"table4_provider": "official-deepseek-metered",
            "cost_note": "native internal counter is not billing; actual cost in separate provider ledger"}}


class MiniEvidence:
    def __init__(self, memory, workspace, task_id, lineage_id):
        self.memory, self.workspace = memory, Path(workspace)
        self.task_id, self.lineage_id = task_id, lineage_id
        self.previous = self.state()

    def state(self):
        diff = subprocess.check_output(["git", "-C", str(self.workspace), "diff", "--no-ext-diff"], text=True)
        return State(self.task_id, self.lineage_id, "software", "mini-swe-agent:swebench",
            "issue_tests", {"official_evaluation": {"status": "UNKNOWN"}}, {"git_diff": diff},
            {"observable_symptom": {"check": "issue_tests", "class": "UNKNOWN"}})

    def verify(self, before, action, after, result):
        # Canonical capture is allowed, but shell exit 0 is not a test oracle.
        # These transitions cannot create positive/executable authority.
        return receipt(self.memory, before, action, after, verdict="UNKNOWN", oracle="UNKNOWN",
            scope="mini-swe-agent:native_shell_observation", complete=False,
            before_measured=False, removed=None, raw={"native_outputs": result})


def sandbox_environment(workspace):
    from minisweagent.environments.extra.bubblewrap import BubblewrapEnvironment
    workspace = Path(workspace).resolve()
    if not (workspace.is_relative_to(ROOT / "hosts") or workspace.is_relative_to(ROOT / "reports")):
        raise ValueError("software environment must be inside the isolated experiment")
    args = ["--unshare-all", "--die-with-parent", "--new-session", "--clearenv",
            "--ro-bind", "/usr", "/usr", "--ro-bind", "/lib", "/lib",
            "--ro-bind", "/lib64", "/lib64", "--symlink", "usr/bin", "/bin",
            "--tmpfs", "/tmp", "--proc", "/proc", "--dev", "/dev",
            "--symlink", str(workspace), "/testbed",
            "--setenv", "HOME", "/tmp", "--setenv", "PATH", "/usr/bin:/bin"]
    return BubblewrapEnvironment(cwd=str(workspace), wrapper_args=args, timeout=30)


def official_evaluator_ready():
    # Do not silently replace the official evaluation with shell success or
    # local tests, or claim a Bubblewrap run is the official Docker environment.
    import shutil
    docker = shutil.which("docker")
    if not docker:
        return {"ready": False, "reason": "official SWE-bench Docker runtime unavailable"}
    probe = subprocess.run([docker, "info", "--format", "{{.ServerVersion}}"],
                           capture_output=True, text=True, timeout=15)
    return {"ready": False, "docker_available": probe.returncode == 0,
            "reason": "instance environments not qualified; Docker availability alone is insufficient"}
