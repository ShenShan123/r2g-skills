"""Replay the observed wire failure without sending any new model request."""
import copy
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "adapters"), str(ROOT / "sources/rtlscout")]


class ScriptedProvider:
    model = "offline-replay-only"
    def __init__(self):
        self.requests = []
    def request(self, payload, **kwargs):
        self.requests.append(copy.deepcopy(payload))
        response = next((ROOT / "hosts/rtlfixer/native/episodes/2012_q2b__c1/provider").glob("*.response.json"))
        return json.loads(response.read_text())


def test_scout_retains_exact_provider_reasoning_by_tool_call_id():
    if sys.version_info[:2] == (3, 10):
        pytest.skip("RTLScout uses its separate modern OpenAI environment")
    from provider_clients import ScoutClient
    provider = ScriptedProvider()
    client = ScoutClient(provider)
    first = client.chat_completion([{"role": "user", "content": "fixture"}], tools=[{}])
    message = {"role": "assistant", "content": first.content or "",
        "tool_calls": [{"id": t.id, "type": "function", "function": {"name": t.name, "arguments": t.arguments}}
                       for t in first.tool_calls]}
    client.chat_completion([{"role": "user", "content": "fixture"}, message,
        {"role": "tool", "tool_call_id": first.tool_calls[0].id, "content": "fixture feedback"}], tools=[{}])
    assert provider.requests[1]["messages"][1]["reasoning_content"]
    assert "reasoning_content" not in message  # native host message is not mutated


def test_legacy_langchain_round_trip_preserves_reasoning_without_tool_argument_pollution():
    if sys.version_info[:2] != (3, 10):
        pytest.skip("requires RTLFixer's real LangChain environment")
    from provider_clients import LegacyFunctionsClient
    from langchain.chat_models.openai import convert_dict_to_message, convert_message_to_dict
    provider = ScriptedProvider()
    client = LegacyFunctionsClient(provider)
    first = client.create(messages=[{"role": "user", "content": "fixture"}], functions=[{}])
    raw = first["choices"][0]["message"]
    reasoning = raw["function_call"]["_table4_reasoning_content"]
    assert reasoning
    # Exercise the actual native message conversion, not an approximation.
    back = convert_message_to_dict(convert_dict_to_message(raw))
    from langchain.agents.output_parsers.openai_functions import OpenAIFunctionsAgentOutputParser
    from langchain.schema import ChatGeneration
    parsed = OpenAIFunctionsAgentOutputParser().parse_result([ChatGeneration(message=convert_dict_to_message(raw))])
    assert set(parsed.tool_input) == {"code_completion"}
    client.create(messages=[{"role": "user", "content": "fixture"}, back,
        {"role": "function", "name": back["function_call"]["name"], "content": "fixture feedback"}], functions=[{}])
    sent = provider.requests[1]["messages"][1]
    assert sent["reasoning_content"] == reasoning
    assert set(sent["tool_calls"][0]["function"]) == {"name", "arguments"}
