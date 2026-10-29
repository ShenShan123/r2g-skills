"""SWE-agent instance hooks; no R2G executor, model-selected verdict, or core changes."""
from __future__ import annotations

import base64
import copy
import hashlib
import io
from pathlib import PurePosixPath
import time
import uuid

from sweagent.agent.hooks.abstract import AbstractAgentHook
from sweagent.types import StepOutput
from unidiff import PatchSet

from bridge import State, Verification, digest, stable
from tehm.ids import stable_dumps


def official_resolved(receipt, instance_id, patch_sha256):
    """Interpret an evaluator-owned, task/patch-bound receipt, never shell exit 0."""
    if (receipt.get('producer') != 'swebench_official_harness'
            or receipt.get('instance_id') != instance_id
            or receipt.get('patch_sha256') != patch_sha256):
        return None
    row = receipt.get('report', {}).get(instance_id, {})
    value = row.get('resolved')
    return value if type(value) is bool else None


def observation_receipt(memory, before, action, after, step):
    """A native command observation is useful history, not a verified repair."""
    path = memory.arm_root/'episodes'/('verification-'+uuid.uuid4().hex+'.json')
    data = {'producer': 'native_evaluator', 'verdict': 'UNKNOWN', 'oracle_type': 'UNKNOWN',
            'scope': 'swe-agent:native_action_observation', 'complete': False,
            'before_measured': False, 'original_failure_removed': None, 'regressions': [],
            'before_digest': digest(before.canonical()), 'after_digest': digest(after.canonical()),
            'action_digest': digest(action),
            'raw': {'action': step.action, 'observation': step.observation,
                    'execution_time': step.execution_time, 'task_success': 'UNKNOWN'}}
    path.write_text(stable(data)+'\n')
    return Verification('UNKNOWN', 'UNKNOWN', data['scope'], False, str(path),
                        hashlib.sha256(path.read_bytes()).hexdigest(), False, None)


class ActionHook(AbstractAgentHook):
    def __init__(self, memory, state_builder, verifier=None):
        self.memory, self.state_builder, self.verifier = memory, state_builder, verifier
        self.before = None
        self.suppress_write = False

    def on_init(self, *, agent):
        self.agent = agent

    def on_action_started(self, *, step):
        self.before = self.state_builder()

    def on_action_executed(self, *, step):
        after = self.state_builder()
        action = {'domain': 'software.NATIVE_ACTION', 'transformation_family': 'swe_agent_native_command',
                  'payload': {'command': step.action}}
        self.memory.journal.append('native_tool_cost', task=after.task_id, action_digest=digest(action),
                                   wall_s=step.execution_time, component='native_action')
        if self.memory.arm == 'native' or self.suppress_write:
            return
        if self.before is None:
            raise ValueError('action lacks a bound pre-state')
        verification = (self.verifier(self.before, action, after, step) if self.verifier else
                        observation_receipt(self.memory, self.before, action, after, step))
        if not isinstance(verification, Verification):
            raise ValueError('independent verifier must return a bound Verification')
        self.memory.write(self.before, action, after, verification, rolled_back=False)


def attach(agent, memory, state_builder, verifier=None, direct_driver=None):
    """Wrap only the instance's decision boundary; upstream parser/executor stays native."""
    original = agent.forward
    hook = ActionHook(memory, state_builder, verifier)
    agent.add_hook(hook)

    def forward(history):
        state = state_builder()
        choice = memory.consult(state)
        context = choice.get('context', '')
        memory.journal.append('memory_consumption', task=state.task_id, mode=choice['mode'],
            selected_ids=choice.get('selected_ids', []), context_bytes=len(context.encode()))
        if choice['mode'] == 'direct_reuse':
            if direct_driver is not None:
                step = direct_driver(choice, state, hook)
                if step is not None:
                    return step
                context = 'The selected memory action was unavailable or failed. Use native reasoning and tools.'
            else:
                memory.journal.append('local_reuse_rejected', reason='independent verifier/driver not configured')
                context = ''
        # Temporary context only. No permanent append, no wholesale history injection.
        query = copy.deepcopy(history)
        if context:
            query.append({'role': 'user', 'content': context})
        return original(query)

    agent.forward = forward
    return hook


class PatchReuse:
    """Supported direct lane: promoted bound patches to existing non-test files.

    Missing verifier, unsupported edits and repeated actions defer to native LLM.
    A failed patch is reversed and entry-state equality is required before fallback.
    """
    def __init__(self, agent, memory, state_builder, verifier):
        self.agent, self.memory, self.state_builder, self.verifier = agent, memory, state_builder, verifier
        self.attempted = set()

    @staticmethod
    def validate(choice, state, *, verifier_available):
        p = choice.get('direct_proposal') or {}
        b = p.get('binding') or {}
        if (not verifier_available or choice.get('mode') != 'direct_reuse'
                or choice.get('routing', {}).get('decision') != 'APPLY'
                or p.get('candidate_id') not in choice.get('selected_ids', [])
                or b.get('lifecycle_status') != 'promoted' or b.get('status') != 'BOUND'
                or b.get('action') != choice.get('action')):
            return None
        target = hashlib.sha256(stable_dumps(state.context().to_dict()).encode()).hexdigest()
        if b.get('proof', {}).get('target_context_digest') != target:
            return None
        obligations = p.get('obligations') or []
        supported = {'PRESERVE_FROZEN_REGRESSION', 'CLEAR_ORIGINAL_FAILURE'}
        if not obligations or any(o.get('obligation') not in supported or o.get('status') != 'SYNTHESIZABLE'
                                  for o in obligations):
            return None
        patch = (choice.get('action') or {}).get('payload', {}).get('git_patch')
        if not isinstance(patch, str) or not patch.strip():
            return None
        if any(line.startswith(('old mode ', 'new mode ', 'new file mode ', 'deleted file mode ',
                                'GIT binary patch', 'Binary files ')) for line in patch.splitlines()):
            return None
        try:
            files = PatchSet(io.StringIO(patch))
            if not files:
                return None
            for file in files:
                # New/deleted/renamed files need a richer rollback adapter.
                if file.is_added_file or file.is_removed_file or file.source_file[2:] != file.target_file[2:]:
                    return None
                for name in (file.source_file, file.target_file):
                    if not name.startswith(('a/', 'b/')):
                        return None
                    path = PurePosixPath(name[2:])
                    if (path.is_absolute() or '..' in path.parts or any(p.startswith('.') for p in path.parts)
                            or any(p in {'test', 'tests'} or p.startswith('test_') or p.endswith('_test.py')
                                   for p in path.parts)):
                        return None
        except Exception:
            return None
        return patch

    @staticmethod
    def command(patch, *, reverse=False):
        encoded = base64.b64encode(patch.encode()).decode()
        flags = '--reverse ' if reverse else ''
        # Only registered patch bytes, never a historical arbitrary shell command.
        return (f"printf %s '{encoded}' | base64 -d | git apply {flags}--check && "
                f"printf %s '{encoded}' | base64 -d | git apply {flags}-")

    def __call__(self, choice, state, hook):
        patch = self.validate(choice, state, verifier_available=self.verifier is not None)
        ident = digest(choice.get('action'))
        if patch is None or ident in self.attempted:
            self.memory.journal.append('local_reuse_rejected', reason='unsupported/unqualified/repeated patch')
            return None
        self.attempted.add(ident)
        hook.suppress_write = True
        step = StepOutput(action=self.command(patch))

        def restore():
            if self.state_builder().config == state.config:
                return
            self.agent.handle_action(StepOutput(action=self.command(patch, reverse=True),
                thought='Restore the pre-memory patch state.', output='TEHM recovery; no model request'))
            if self.state_builder().config != state.config:
                self.memory.journal.append('restoration_unverified', technical_stop=True)
                raise RuntimeError('entry state restoration not verified; stop, never fallback on dirty state')

        try:
            try:
                step = self.agent.handle_action(StepOutput(action=self.command(patch),
                    thought='Execute a qualified TEHM patch through the native handler.',
                    output='TEHM local action; no model request'))
                after = self.state_builder()
                if after.config == state.config:
                    self.memory.journal.append('local_reuse_no_effect', accepted=False)
                    return None
                verification = self.verifier(state, choice['action'], after, step)
                if not isinstance(verification, Verification):
                    raise ValueError('missing independent patch verification')
                verification.validate(self.memory.arm_root)
            except Exception as exc:
                after = self.state_builder()
                restore()
                unknown = observation_receipt(self.memory, state, choice['action'], after, step)
                self.memory.write(state, choice['action'], after, unknown, rolled_back=True)
                self.memory.journal.append('local_reuse_technical_stop', restored=True,
                                           error_type=type(exc).__name__)
                raise RuntimeError('memory verification/action failed; entry restored; technical stop') from exc
            passed = verification.verdict == 'PASS' and verification.complete
            if not passed:
                restore()
            self.memory.write(state, choice['action'], after, verification, rolled_back=not passed)
            self.memory.journal.append('local_reuse_completed', accepted=passed, rolled_back=not passed,
                                       selected_ids=choice['selected_ids'])
            return step if passed else None
        finally:
            hook.suppress_write = False
