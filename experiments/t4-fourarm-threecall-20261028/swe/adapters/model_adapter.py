"""Metered transport only: native SWE-agent owns parsing, tools and requery decisions."""
from __future__ import annotations

import copy

from sweagent.agent.models import AbstractModel, GenericAPIModelConfig, InstanceStats


class MeteredModel(AbstractModel):
    def __init__(self, provider, tools):
        self.provider = provider
        self.tools = tools
        self.config = GenericAPIModelConfig(name=provider.model, per_instance_cost_limit=0,
                                            total_cost_limit=0, retry={'retries': 1}, fallbacks=[])
        self.stats = InstanceStats()
        self.reasoning = {}
        self.billing_cost = None  # native instance_cost=0 disables its cost cap; NOT a billing result

    def query(self, history, action_prompt='> '):
        messages = []
        for raw in history:
            message = {k: copy.deepcopy(raw[k]) for k in ('role', 'content', 'tool_calls') if k in raw}
            content=message.get('content')
            if isinstance(content,list):
                # Native cache-control blocks are Anthropic transport metadata,
                # not memory content. Keep the exact text for DeepSeek's wire.
                if len(content)!=1 or content[0].get('type')!='text':
                    raise ValueError('this registered SWE subset supports native text-only messages')
                message['content']=content[0]['text']
            if raw['role'] == 'tool':
                ids = raw.get('tool_call_ids') or [raw.get('tool_call_id')]
                if len(ids) != 1 or not ids[0]:
                    raise ValueError('native tool observation must identify exactly one tool call')
                message['tool_call_id'] = ids[0]
            calls = tuple(t['id'] for t in raw.get('tool_calls') or [])
            if raw['role'] == 'assistant' and calls in self.reasoning:
                message['reasoning_content'] = self.reasoning[calls]
            messages.append(message)
        body = {'messages': messages, 'temperature': self.config.temperature}
        if self.tools.use_function_calling:
            body['tools'] = self.tools.tools
        # No SDK retries. Native format-error requeries call this method again and
        # therefore get separate request/usage records in the provider journal.
        data = self.provider.request(body, purpose='native_swe_agent_decision')
        message = data['choices'][0]['message']
        ids = tuple(t['id'] for t in message.get('tool_calls') or [])
        if ids and isinstance(message.get('reasoning_content'), str):
            self.reasoning[ids] = message['reasoning_content']
        usage = data['usage']
        self.stats.api_calls += 1
        self.stats.tokens_sent += usage['prompt_tokens']
        self.stats.tokens_received += usage['completion_tokens']
        result = {'message': message.get('content') or ''}
        if self.tools.use_function_calling:
            result['tool_calls'] = copy.deepcopy(message.get('tool_calls') or [])
        return result
