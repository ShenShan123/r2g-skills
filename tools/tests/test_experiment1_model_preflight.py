import importlib.util
import os
import pathlib


SCRIPT = (
    pathlib.Path(__file__).resolve().parents[1]
    / "preflight_experiment1_model_routes.py"
)
SPEC = importlib.util.spec_from_file_location("model_preflight", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_placeholder_secrets_are_rejected():
    assert not MODULE.is_usable_secret("")
    assert not MODULE.is_usable_secret("sk-xxx")
    assert not MODULE.is_usable_secret("placeholder")
    assert MODULE.is_usable_secret("sk-valid-looking-test-value")


def test_openai_tool_call_and_usage_are_parsed():
    data = {
        "model": "example-model",
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "submit_probe",
                                "arguments": '{"value":"ok"}',
                            }
                        }
                    ]
                }
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 4, "detail": {}},
    }
    passed, model, usage = MODULE.parse_openai_response(data)
    assert passed
    assert model == "example-model"
    assert usage == {"prompt_tokens": 10, "completion_tokens": 4}


def test_anthropic_tool_call_and_usage_are_parsed():
    data = {
        "model": "example-claude",
        "content": [
            {"type": "tool_use", "name": "submit_probe", "input": {"value": "ok"}}
        ],
        "usage": {"input_tokens": 12, "output_tokens": 7},
    }
    passed, model, usage = MODULE.parse_anthropic_response(data)
    assert passed
    assert model == "example-claude"
    assert usage == {"input_tokens": 12, "output_tokens": 7}


def test_responses_tool_call_and_usage_are_parsed():
    data = {
        "model": "gpt-example",
        "status": "completed",
        "output": [
            {
                "type": "function_call",
                "name": "submit_probe",
                "call_id": "call_123",
                "arguments": '{"value":"ok"}',
            }
        ],
        "usage": {"input_tokens": 20, "output_tokens": 5, "total_tokens": 25},
    }
    passed, model, usage = MODULE.parse_responses_response(data)
    assert passed
    assert model == "gpt-example"
    assert usage == {"input_tokens": 20, "output_tokens": 5, "total_tokens": 25}


def test_responses_endpoint_override_handles_v1_base(monkeypatch):
    monkeypatch.setenv("TEST_RESPONSES_BASE", "https://gateway.example/v1")
    assert MODULE.endpoint_for(
        {"api_style": "openai_responses", "endpoint_env": "TEST_RESPONSES_BASE"}
    ) == "https://gateway.example/v1/responses"


def test_wrong_tool_arguments_fail_canary():
    data = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "submit_probe",
                                "arguments": '{"value":"wrong"}',
                            }
                        }
                    ]
                }
            }
        ],
        "usage": {"total_tokens": 2},
    }
    passed, _, _ = MODULE.parse_openai_response(data)
    assert not passed


def test_probe_route_rejects_silent_model_substitution(monkeypatch):
    route = {
        "method_id": "test-vanilla",
        "provider": "gateway",
        "channel": "third_party_gateway",
        "model_id": "requested-model",
        "api_style": "openai_chat",
        "api_key_env": "TEST_MODEL_KEY",
        "endpoint": "https://example.invalid/v1/chat/completions",
        "endpoint_env": "TEST_MODEL_BASE_URL",
        "tool_choice": "required",
        "max_output_field": "max_tokens",
        "max_output_tokens": 32,
    }
    monkeypatch.setenv("TEST_MODEL_KEY", "sk-valid-looking-test-value")

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return (
                b'{"model":"different-model","choices":[{"message":'
                b'{"tool_calls":[{"function":{"name":"submit_probe",'
                b'"arguments":"{\\"value\\":\\"ok\\"}"}}]}}],'
                b'"usage":{"total_tokens":2}}'
            )

    monkeypatch.setattr(MODULE.urllib.request, "urlopen", lambda *_a, **_k: Response())
    result = MODULE.probe_route(route, 1)
    assert result["tool_canary_passed"] is True
    assert result["actual_model_matches_requested"] is False
    assert result["status"] == "model_identity_mismatch"


def test_error_body_redacts_secret():
    secret = "sk-sensitive-value"
    raw = (
        '{"error":{"type":"auth","message":"bad key ' + secret + '"}}'
    ).encode()
    parsed = MODULE.sanitized_error_body(raw, secret)
    assert secret not in str(parsed)
    assert "[REDACTED]" in parsed["message"]


def test_env_file_requires_private_permissions(tmp_path, monkeypatch):
    env_file = tmp_path / "api.env"
    env_file.write_text("EXPERIMENT_TEST_KEY=secret\n")
    env_file.chmod(0o644)
    monkeypatch.delenv("EXPERIMENT_TEST_KEY", raising=False)
    try:
        MODULE.load_env_file(env_file)
    except ValueError as exc:
        assert "group/world accessible" in str(exc)
    else:
        raise AssertionError("insecure credential file was accepted")


def test_env_file_loads_without_overwriting_process_environment(tmp_path, monkeypatch):
    env_file = tmp_path / "api.env"
    env_file.write_text(
        "EXPERIMENT_TEST_KEY='from-file'\n"
        "export EXPERIMENT_SECOND_KEY=second\n"
    )
    env_file.chmod(0o600)
    monkeypatch.setenv("EXPERIMENT_TEST_KEY", "from-process")
    monkeypatch.delenv("EXPERIMENT_SECOND_KEY", raising=False)
    MODULE.load_env_file(env_file)
    assert os.environ["EXPERIMENT_TEST_KEY"] == "from-process"
    assert os.environ["EXPERIMENT_SECOND_KEY"] == "second"


def test_env_file_replaces_placeholder_process_value(tmp_path, monkeypatch):
    env_file = tmp_path / "api.env"
    env_file.write_text("EXPERIMENT_TEST_KEY=from-file\n")
    env_file.chmod(0o600)
    monkeypatch.setenv("EXPERIMENT_TEST_KEY", "placeholder")
    MODULE.load_env_file(env_file)
    assert os.environ["EXPERIMENT_TEST_KEY"] == "from-file"
