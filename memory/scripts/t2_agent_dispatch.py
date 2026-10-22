"""Opt-in agent-selected dispatch primitives; no EDA/provider launch on import.

Adapters choose operations; the dispatcher never inserts a catalogue phase.
The existing ledger, not an in-memory counter, owns the three-request limit.
This module is not yet a nine-arm experiment runner.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import time
from typing import Any, Callable

VERSION = 'agent-selected-total-online-three-v2'
ARMS = ('NB', 'RET', 'RECIPE', 'EXPEL', 'AWM', 'AMEM', 'TM0', 'REME', 'REASONINGBANK')
PURPOSES = {'planning', 'repair', 'memory_adaptation', 'query', 'rerank', 'judge'}


class OnlineAllowanceSpent(RuntimeError):
    """Registered opportunity limit, distinct from technical termination."""


class IntegrityStop(RuntimeError):
    """UNKNOWN usage, missing accounting, or broken integration: stop admission."""


def episode_budget():
    return {'budget_id': VERSION, 'caps': {'llm_requests': 3, 'repair_attempts': 3},
            'reserve': {}}


def _emit(harness, project, kind, data):
    rc, _ = harness._ledger(project, 'event', '--type', kind,
                            '--json', json.dumps(data, sort_keys=True, allow_nan=False))
    if rc:
        raise IntegrityStop('Cannot record ' + kind)


class OnlineGateway:
    """Single metered provider boundary for both native helpers and host agent.

    Native adapters must inject this gateway instead of instantiating an SDK.
    The phase ledger is separate and must already be configured by the runner.
    The existing provider client's atomic holds enforce simultaneous admission.
    """

    def __init__(self, harness, project: Path, key: str):
        self.harness, self.project, self.key = harness, Path(project), key
        path = os.environ.get('R2G_BUDGET_FILE')
        if not path or not Path(path).is_file():
            raise IntegrityStop('No episode budget binding')
        self.budget_path = Path(path)
        self.budget_bytes = self.budget_path.read_bytes()
        budget = json.loads(self.budget_bytes)
        if budget.get('caps') != episode_budget()['caps'] or budget.get('reserve', {}):
            raise IntegrityStop('Expected three total online requests and no extra episode caps')
        if budget.get('combined_tokens_cap') is not None:
            raise IntegrityStop('Unregistered episode token cap')
        if os.environ.get('R2G_REQUIRE_LLM_BUDGET') != '1':
            raise IntegrityStop('Provider metering is not required')
        self.stopped = False

    def remaining(self):
        if self.stopped:
            raise IntegrityStop('This gateway previously stopped on unknown/invalid usage')
        if self.budget_path.read_bytes() != self.budget_bytes:
            raise IntegrityStop('Episode budget changed after binding')
        rc, raw = self.harness._ledger(self.project, 'remaining')
        if rc:
            raise IntegrityStop('Cannot read episode allowance')
        data = json.loads(raw)
        n = data.get('llm_requests')
        if (isinstance(n, bool) or not isinstance(n, (int, float))
                or not math.isfinite(n) or n != int(n) or not 0 <= n <= 3):
            raise IntegrityStop('Unknown or invalid online allowance')
        # A phase UNKNOWN halt is enforced atomically by the existing client.
        return int(n)

    def call(self, messages, prefix, *, purpose, candidate_generating=False,
             sampling_override=None):
        if purpose not in PURPOSES:
            raise ValueError('Unregistered LLM purpose')
        if purpose in {'planning', 'repair', 'memory_adaptation'} and not candidate_generating:
            raise ValueError('Action-producing requests require candidate authority')
        if purpose in {'query', 'rerank', 'judge'} and candidate_generating:
            raise ValueError('Auxiliary requests cannot create executable candidates')
        if self.remaining() == 0:
            raise OnlineAllowanceSpent('Three total online requests have been spent')
        prefix = Path(prefix)
        if not prefix.resolve().is_relative_to(self.project.resolve()):
            raise IntegrityStop('Request receipts must belong to this episode')
        # A restart must not overwrite raw provider receipts or implicitly retry.
        if list(prefix.parent.glob(prefix.name + '.try*.request.json')):
            raise IntegrityStop('Request prefix already used')
        _emit(self.harness, self.project, 'agent_request_intent', {
            'version': VERSION, 'purpose': purpose,
            'candidate_generating': candidate_generating, 'prefix': str(prefix)})
        content, usage = self.harness.llm_call(
            self.key, messages, prefix, backoff=(), ledger=self.project,
            role='repair' if candidate_generating else 'auxiliary',
            sampling_override=sampling_override)
        if usage.get('budget_exhausted'):
            # A concurrent winner may have consumed the last episode request.
            # Phase faults never become a normal three-call completion.
            if usage.get('scope') != 'phase' and self.remaining() == 0:
                raise OnlineAllowanceSpent('Three total online requests have been spent')
            self.stopped = True
            raise IntegrityStop('Admission stopped outside the online opportunity limit')
        valid = all(isinstance(usage.get(k), (int, float)) and not isinstance(usage[k], bool)
                    and math.isfinite(usage[k]) and usage[k] >= 0
                    for k in ('prompt_tokens', 'completion_tokens'))
        if content is None or not valid or usage.get('accounting_error') or usage.get('stopped'):
            self.stopped = True
            raise IntegrityStop('Provider response or usage is unknown/invalid')
        return content, usage


@dataclass(frozen=True)
class Tool:
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Stop:
    reason: str


def dispatch(policy, tools: dict[str, Callable], initial: dict, gateway: OnlineGateway,
             final_check: Callable, event: Callable):
    """Run a policy on verified observations, without inserting repair actions.

    policy.next_action(observation, gateway) may use native local memory and/or
    metered calls, then return one Tool or Stop. Tools own permission checking,
    verification, rollback and their underlying resource events. A callback
    cannot declare acceptance without the common final_check confirming it.
    """
    observation = initial
    reason = None
    while not observation.get('accepted'):
        try:
            decision = policy.next_action(observation, gateway)
        except OnlineAllowanceSpent:
            reason = 'online_allowance_spent'
            break
        if isinstance(decision, Stop):
            reason = decision.reason
            break
        if not isinstance(decision, Tool) or decision.name not in tools:
            raise IntegrityStop('Policy selected an unknown tool or malformed decision')
        event('agent_tool_selected', {'name': decision.name, 'arguments': decision.arguments})
        start, cpu = time.monotonic(), time.process_time()
        try:
            observation = tools[decision.name](**decision.arguments)
            if not isinstance(observation, dict):
                raise IntegrityStop('Tool returned an invalid observation')
        except Exception as exc:
            event('agent_tool_outcome', {'name': decision.name, 'status': 'TECHNICAL_FAILURE',
                  'wall_s': time.monotonic()-start, 'cpu_s': time.process_time()-cpu,
                  'error_type': type(exc).__name__})
            raise
        event('agent_tool_outcome', {'name': decision.name, 'status': 'RETURNED',
              'wall_s': time.monotonic()-start, 'cpu_s': time.process_time()-cpu})
    verdict = final_check()
    # The caller applies the established terminal_status classifier; an UNKNOWN
    # final verdict is never a measured failure just because three calls ran out.
    return {'stop_reason': reason or 'acceptance_observed', 'final': verdict}
