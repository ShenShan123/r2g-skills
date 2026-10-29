"""RTLFixer-native compiler/RAG integration; Icarus port, not paper RTLRepair data.

No host source is edited. The pinned native compiler tool hard-codes Quartus;
the domain boundary maps compile-only evaluations to installed Icarus equally
for both arms. Functional evaluation remains the original native simulator.
"""
from __future__ import annotations

import dataclasses
import copy
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import sys
import tempfile
import time

from bridge import ROOT, FullMemory, State, digest, stable
from native_evidence import receipt
from provider_clients import LegacyFunctionsClient

sys.path[:0] = [str(ROOT / "sources/rtlfixer/src"), str(ROOT / "sources/verilog-eval")]


def first_two():
    selected, seen = [], set()
    for index, line in enumerate((ROOT / "sources/rtlfixer/src/benchmarks/verilogeval-syntax.jsonl").open()):
        item = json.loads(line)
        if item["task_id"] in seen:
            continue
        seen.add(item["task_id"])
        selected.append({"row": index, "task_id": item["task_id"], "item": item})
        if len(selected) == 2:
            return selected
    raise ValueError("two distinct native tasks required")


class FixerEvidence:
    def __init__(self, memory, item):
        self.memory, self.item = memory, item
        self.candidate = item["solution"]
        self.latest = {}
        self.previous = {}
        self.counter = 0
        self.defer_writes = False
        self.pending = []
        # Linux AF_UNIX names are limited to 108 bytes. Only IPC sockets live
        # here; source, evaluator evidence and compiler scratch stay private.
        self.ipc_directory = tempfile.mkdtemp(prefix="t4-ipc-", dir="/tmp")

    def state(self):
        check = self.latest.get("scope", "native_compile")
        return State(self.item["task_id"], self.item["task_id"], "rtl", "rtlfixer:iverilog",
                     check, {"evaluation": dict(self.latest)}, {"candidate.sv": self.candidate},
                     {"observable_symptom": {"check": check,
                                              "class": self.latest.get("status", "UNKNOWN")}})

    def bind(self, executor):
        native = executor.evaluate

        def evaluate(problem, completion, test, timeout=5, compile_only=False):
            from executors.executor_utils import simple_syntax_fixer
            completion = simple_syntax_fixer(completion, problem)
            scope = "native_compile" if compile_only else "native_functional"
            mapped = "iverilog" if compile_only else False
            started, cpu = time.monotonic(), time.process_time()
            process_config = multiprocessing.current_process()._config
            old_ipc = process_config.get("tempdir")
            process_config["tempdir"] = self.ipc_directory
            try:
                result = native(problem, completion, test, timeout=timeout, compile_only=mapped)
            finally:
                if old_ipc is None:
                    process_config.pop("tempdir", None)
                else:
                    process_config["tempdir"] = old_ipc
            raw = result.get("feedback") or {}
            timed_out = "timed out" in str(raw.get("compiler_log", "")).lower()
            verdict = "UNKNOWN" if timed_out else "PASS" if result.get("passed") is True else "FAIL"
            self.candidate = completion
            self.latest = {"status": verdict.lower(), "scope": scope,
                           "compiler_log": raw.get("compiler_log", ""),
                           "test_output": raw.get("test_output", "")}
            after = dataclasses.replace(self.state(), check=scope)
            before = self.previous.get(scope) or dataclasses.replace(
                after, reports={"evaluation": {"status": "UNKNOWN"}})
            self.previous[scope] = after
            action = {"domain": "rtl.AST_REWRITE", "transformation_family": "native_source_iteration",
                      "payload": {"config_edits": {"candidate.sv": completion},
                                  "source_before": before.config, "source_after": after.config,
                                  "compatibility_profile": after.profile, "recheck": scope}}
            self.counter += 1
            self.memory.journal.append("native_tool_cost", host="rtlfixer", tool="evaluate",
                tool_index=self.counter, requested_compiler=str(compile_only), mapped_compiler=str(mapped),
                wall_s=time.monotonic()-started, cpu_process_s=time.process_time()-cpu,
                verdict=verdict, scope=scope, action_digest=digest(action))
            if self.memory.arm == "tehm":
                previous = (before.reports.get("evaluation") or {}).get("status")
                verification = receipt(self.memory, before, action, after, verdict=verdict,
                    oracle=("COMPILE" if compile_only else "REGRESSION") if not timed_out else "UNKNOWN",
                    scope="rtlfixer:" + scope, complete=not timed_out,
                    before_measured=previous in {"pass", "fail"},
                    removed=True if previous == "fail" and verdict == "PASS" else
                            False if previous == "fail" and verdict == "FAIL" else None,
                    regressions=(scope,) if previous == "pass" and verdict == "FAIL" else (),
                    raw={"task_id": problem["task_id"], "passed": result.get("passed"),
                         "compiler_log": raw.get("compiler_log", ""),
                         "test_output": raw.get("test_output", ""),
                         "test_content_sha256": hashlib.sha256(test.encode()).hexdigest()})
                if self.defer_writes:
                    self.pending.append((before, action, after, verification))
                else:
                    self.memory.write(before, action, after, verification, rolled_back=False)
            return result

        executor.evaluate = evaluate
        return executor

    def try_local(self, executor, choice):
        from local_reuse import source_edits
        entry = self.state()
        try:
            edits = source_edits(choice, entry, allowed_names={"candidate.sv"})
        except ValueError as exc:
            self.memory.journal.append("local_reuse_rejected", reason=str(exc))
            return None
        saved = copy.deepcopy((self.candidate, self.latest, self.previous))
        self.pending = []
        self.defer_writes = True
        success = False
        try:
            candidate = edits["candidate.sv"]
            compiled = executor.evaluate(self.item, candidate, self.item["test"], timeout=20, compile_only="iverilog")
            simulated = executor.evaluate(self.item, candidate, self.item["test"], timeout=20, compile_only=False)
            success = compiled.get("passed") is True and simulated.get("passed") is True
            for args in self.pending:
                self.memory.write(*args, rolled_back=not success)
        finally:
            self.defer_writes = False
            if not success:
                # Native evaluation uses disposable child scratch directories;
                # the repair entry is the candidate plus its measured state.
                self.candidate, self.latest, self.previous = saved
                if self.state().canonical() != entry.canonical():
                    raise RuntimeError("RTLFixer entry restoration failed")
        self.memory.journal.append("local_reuse_completed", accepted=success,
            rolled_back=not success, restoration="native candidate/evidence pointer, disposable evaluator scratch")
        return {"candidate": candidate, "native_compile": compiled,
                "functional_verification": simulated, "native_errors": [],
                "native_history": [], "local_reuse": True} if success else None


class FixerTransport(LegacyFunctionsClient):
    def __init__(self, provider, memory, evidence):
        super().__init__(provider)
        self.memory, self.evidence = memory, evidence

    def create(self, **kwargs):
        choice = self.memory.consult(self.evidence.state())
        if choice["mode"] == "direct_reuse":
            self.memory.journal.append("local_reuse_rejected", reason="nested native SDK call retains host tool protocol",
                                       fallback="experience_assisted", direct_entry_available=True)
        if choice["context"]:
            kwargs = {**kwargs, "messages": [*kwargs["messages"],
                {"role": "user", "content": choice["context"]}]}
        self.memory.journal.append("memory_consumption", mode=choice["mode"],
                                   selected_ids=choice.get("selected_ids", []),
                                   context_bytes=len(choice["context"].encode()))
        return super().create(**kwargs)


def make_agent(executor, transport, item):
    # The actual key exists only in the metered provider; native SDK may not
    # acquire other credentials or bypass the accounting transport.
    os.environ["OPENAI_API_KEY"] = "table4-transport-placeholder"
    from generators.agents.rtlfixer import CodeAgent
    agent = CodeAgent("deepseek-flash", executor, max_iters=10,
                      toolset=["compiler", "rag"], method="rag", compiler="iverilog")
    agent.toolkit.initialize(item)
    agent.reset_logs()
    for model in (agent.llm, agent.toolkit.llm, agent.agent_executor.agent.llm):
        model.client = transport
        model.max_retries = 0
    return agent


def execute_native(agent, executor, item, *, evidence=None):
    """The pinned react_fix_compile single-sample body, without its broken np summary."""
    from executors.executor_utils import simple_syntax_fixer
    from generators import generator_factory
    from generators.model import GPTChat, Message
    candidate = simple_syntax_fixer(item["solution"], item)
    initial = executor.evaluate(item, candidate, item["test"], timeout=20, compile_only="iverilog")
    if not initial["passed"]:
        if evidence is not None and evidence.memory.arm == "tehm":
            choice = evidence.memory.consult(evidence.state())
            if choice["mode"] == "direct_reuse":
                local = evidence.try_local(executor, choice)
                if local is not None:
                    return local
        messages = generator_factory("verilog").prepare_prompt(
            item, GPTChat("deepseek-flash"), "react_code", candidate, [], [])
        messages += [Message("assistant", candidate),
                     Message("user", "Run the compiler and fix the error if encountered.")]
        candidate = simple_syntax_fixer(agent.generate_chat(messages), item)
    final_compile = executor.evaluate(item, candidate, item["test"], timeout=20, compile_only="iverilog")
    final_sim = executor.evaluate(item, candidate, item["test"], timeout=20, compile_only=False)
    return {"candidate": candidate, "native_compile": final_compile,
            "functional_verification": final_sim,
            "native_errors": agent.error_logs, "native_history": agent.serialize(agent.agent_history)}
