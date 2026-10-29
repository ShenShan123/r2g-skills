"""Domain verification adapters. A host's observed check is not full signoff."""
from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path
import uuid

from bridge import State, Verification, digest, stable


def receipt(memory, before, action, after, *, verdict, oracle, scope, complete,
            before_measured, removed, regressions=(), raw=None):
    path = memory.arm_root / "episodes" / ("verification-" + uuid.uuid4().hex + ".json")
    data = {"producer": "native_evaluator", "verdict": verdict, "oracle_type": oracle,
            "scope": scope, "complete": complete,
            "before_measured": before_measured, "original_failure_removed": removed,
            "regressions": list(regressions),
            "before_digest": digest(before.canonical()), "after_digest": digest(after.canonical()),
            "action_digest": digest(action), "raw": raw}
    path.write_text(stable(data) + "\n")
    return Verification(verdict, oracle, scope, complete, str(path),
                        hashlib.sha256(path.read_bytes()).hexdigest(), before_measured, removed,
                        tuple(regressions))


class ScoutEvidence:
    def __init__(self, memory, agent, task_id):
        self.memory, self.agent, self.task_id = memory, agent, task_id
        self.seen_eval = 0
        self.previous_evaluated_state = None
        self.pending_tools = []

    def state(self):
        # Only agent-authored sources, never the golden reference or testbench.
        files = {p.name: p.read_text() for p in sorted(self.agent.workspace.glob("*"))
                 if p.is_file() and p.suffix in {".v", ".sv"} and p.name != "tb.sv"}
        last = self.agent.all_evals[-1] if self.agent.all_evals else None
        summary = ({"status": "pass" if last.get("passed") else "fail",
                    "metric": last.get("cost_metric_name"), "cost": last.get("cost_value")}
                   if last else {"status": "UNKNOWN"})
        if (self.previous_evaluated_state is not None and len(self.agent.all_evals) == self.seen_eval
                and files != self.previous_evaluated_state.config):
            summary = {**summary, "last_measured_status": summary["status"], "status": "UNKNOWN"}
        return State(self.task_id, self.task_id, "rtl", "rtlscout:verilog",
                     "functional_and_cost", {"evaluation": summary}, files,
                     {"observable_symptom": {"check": "functional_and_cost",
                                             "class": summary["status"]}})

    def verify(self, before, action, after, result):
        self.pending_tools.append(action)
        if len(self.agent.all_evals) == self.seen_eval:
            return None  # file edits have not been evaluated yet
        self.seen_eval = len(self.agent.all_evals)
        raw = self.agent.all_evals[-1]
        # The entire evaluation has its own native scope. A missing simulation
        # fraction must not be fabricated as a fully measured functional pass.
        measured = isinstance(raw.get("pass_rate"), (int, float))
        passed = raw.get("passed")
        verdict = "PASS" if passed is True and measured else "FAIL" if passed is False and measured else "UNKNOWN"
        # A report describes the source evaluated then, not source edited since.
        # Bind to the previous measured state and all intervening native tools.
        bound_before = self.previous_evaluated_state or before
        edited = {name: after.config.get(name) for name in set(bound_before.config) | set(after.config)
                  if bound_before.config.get(name) != after.config.get(name)}
        bound_action = {"domain": "rtl.AST_REWRITE", "transformation_family": "native_source_iteration",
                        "payload": {"native_tools": self.pending_tools,
                                    "config_edits": edited,
                                    "knob_edits": {name: {"before": bound_before.config.get(name),
                                                          "after": after.config.get(name)} for name in edited},
                                    "source_before": bound_before.config,
                                    "source_after": after.config,
                                    "compatibility_profile": after.profile,
                                    "recheck": after.check}}
        self.previous_evaluated_state = after
        self.pending_tools = []
        previous = (bound_before.reports.get("evaluation") or {}).get("status")
        verified = receipt(self.memory, bound_before, bound_action, after, verdict=verdict,
            oracle="REGRESSION" if measured else "UNKNOWN", scope="rtlscout:benchmark_simulation",
            complete=measured, before_measured=previous in {"pass", "fail"},
            removed=True if previous == "fail" and verdict == "PASS" else
                    False if previous == "fail" and verdict == "FAIL" else None,
            regressions=("functional",) if previous == "pass" and verdict == "FAIL" else (),
            raw=raw)
        return bound_before, bound_action, after, verified
