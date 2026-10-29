"""Instance-local host hooks; original host methods remain the executor.

The observer supplies domain states and independent verification. These hooks
do not infer task success from an arbitrary tool's return code.
"""
from __future__ import annotations

import dataclasses
import functools
from pathlib import Path
import time

from bridge import FullMemory, State, digest


class HostObserver:
    def __init__(self, memory: FullMemory, state_builder, verifier):
        self.memory, self.state_builder, self.verifier = memory, state_builder, verifier
        self.last_choice = None
        self.direct_driver = None
        self.defer_writes = False
        self.deferred = []
        self.suppress_verification = False

    def before_decision(self):
        state = self.state_builder()
        self.last_choice = self.memory.consult(state)
        return self.last_choice

    def after_action(self, before, action, result, *, rolled_back=False):
        if self.suppress_verification:
            self.memory.journal.append("recovery_tool", action_digest=digest(action),
                                       excluded_from_positive_evidence=True)
            return None
        after = self.state_builder()
        verification = self.verifier(before, action, after, result)
        self.memory.journal.append("native_tool", action_digest=digest(action),
                                   before_digest=digest(before.canonical()),
                                   after_digest=digest(after.canonical()),
                                   verification_available=verification is not None)
        if verification is None or self.memory.arm == "native":
            return None
        if isinstance(verification, tuple):
            before, action, after, verification = verification
        if self.defer_writes:
            self.deferred.append((before, action, after, verification))
            return None
        return self.memory.write(before, action, after, verification, rolled_back=rolled_back)

    def consume(self, messages):
        choice = self.before_decision()
        self.memory.journal.append("memory_consumption", mode=choice["mode"],
                                   selected_ids=choice.get("selected_ids", []),
                                   context_bytes=len(choice["context"].encode()))
        if choice["mode"] == "direct_reuse":
            self.memory.journal.append("local_reuse_rejected", reason="no qualified step driver at this call site",
                                       fallback="experience_assisted" if choice["context"] else "no_experience")
        return choice["context"]


def attach_mini(agent, observer: HostObserver):
    """Use DefaultAgent's documented query hook and original execute_actions."""
    original_query, original_execute = agent.query, agent.execute_actions

    @functools.wraps(original_query)
    def query():
        context = observer.consume(agent.messages)
        if context:
            agent.add_messages(agent.model.format_message(role="user", content=context))
        return original_query()

    @functools.wraps(original_execute)
    def execute(message):
        before = observer.state_builder()
        started, cpu = time.monotonic(), time.process_time()
        result = original_execute(message)
        observer.memory.journal.append("host_action_batch", host="mini-swe-agent",
            count=len(message.get("extra", {}).get("actions", [])),
            wall_s=time.monotonic()-started, cpu_s=time.process_time()-cpu)
        observer.after_action(before, {"domain": "software.ACTION_BATCH",
            "transformation_family": "native_action_batch",
            "payload": {"actions": message.get("extra", {}).get("actions", [])}}, result)
        return result

    agent.query, agent.execute_actions = query, execute
    return agent


def attach_rtlscout(agent, observer: HostObserver):
    """Keep the native tool dispatcher, evaluator and model request format."""
    original_chat, original_tool = agent.client.chat_completion, agent._execute_tool

    @functools.wraps(original_chat)
    def chat(*args, **kwargs):
        if args:
            raise ValueError("pinned RTLScout calls chat_completion with keyword arguments")
        kwargs = dict(kwargs)
        context = ""
        if kwargs.get("tools"):
            choice = observer.before_decision()
            observer.memory.journal.append("memory_consumption", mode=choice["mode"],
                selected_ids=choice.get("selected_ids", []), context_bytes=len(choice["context"].encode()))
            if choice["mode"] == "direct_reuse" and observer.direct_driver:
                local = observer.direct_driver(choice)
                if local and local["accepted"]:
                    from rtlscout.llm_client import ChatResponse
                    return ChatResponse(content=local["summary"], provider="TEHM-local", finish_reason="local_action")
                context = local["summary"] if local else "No executable applicable memory at this state. Use native tools and online reasoning."
            else:
                context = choice["context"]
        if context:
            kwargs["messages"] = [*kwargs["messages"], {"role": "user", "content": context}]
        return original_chat(**kwargs)

    @functools.wraps(original_tool)
    def tool(name, arguments):
        before = observer.state_builder()
        started, cpu = time.monotonic(), time.process_time()
        result = original_tool(name, arguments)
        observer.memory.journal.append("native_tool_cost", host="rtlscout", tool=name,
                                       wall_s=time.monotonic()-started,
                                       cpu_s=time.process_time()-cpu)
        observer.after_action(before, {"domain": "rtl.NATIVE_TOOL",
            "transformation_family": name, "payload": {"arguments": arguments}}, result)
        return result

    agent.client.chat_completion, agent._execute_tool = chat, tool
    return agent


def attach_rtlfixer(code_agent, executor, observer: HostObserver):
    """Retain RTLFixer's own RAG tools and native executable evaluation."""
    original_generate, original_evaluate = code_agent.generate_chat, executor.evaluate

    @functools.wraps(original_generate)
    def generate(messages, *args, **kwargs):
        context = observer.consume(messages)
        if context:
            from generators.model import Message
            messages = [*messages, Message(role="user", content=context)]
        return original_generate(messages, *args, **kwargs)

    @functools.wraps(original_evaluate)
    def evaluate(*args, **kwargs):
        before = observer.state_builder()
        started, cpu = time.monotonic(), time.process_time()
        result = original_evaluate(*args, **kwargs)
        observer.memory.journal.append("native_tool_cost", host="rtlfixer", tool="evaluate",
                                       wall_s=time.monotonic()-started,
                                       cpu_s=time.process_time()-cpu)
        observer.after_action(before, {"domain": "rtl.NATIVE_EVALUATION",
            "transformation_family": "evaluate_candidate",
            "payload": {"candidate": args[1] if len(args) > 1 else kwargs.get("func"),
                        "compile_only": kwargs.get("compile_only")}}, result)
        return result

    code_agent.generate_chat, executor.evaluate = generate, evaluate
    return code_agent
