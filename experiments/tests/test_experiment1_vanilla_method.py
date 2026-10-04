import json
from pathlib import Path

import pytest

import experiments.run_experiment1_vanilla_method as vanilla
from experiments.run_experiment1_rtl_acquisition import ExperimentError
from experiments.run_experiment1_vanilla_method import (
    VanillaRun,
    anthropic_messages,
    anthropic_response_message,
    anthropic_tools,
    assistant_history_message,
    endpoint_kind_for_route,
    inline_safe_paths,
    is_transient_git_failure,
    is_transient_network_error,
    normalized_checkout,
    public_repository_record,
    provider_usage,
    responses_input,
    responses_response_message,
    responses_tools,
    run_submission_stop_reason,
    submitted_stop_reason,
    tool_specs,
    validation_event_summary,
)


@pytest.fixture(autouse=True)
def fixed_toolchain_env(monkeypatch):
    monkeypatch.setattr(vanilla, "campaign_toolchain_env", lambda _root: {})


def test_endpoint_kind_distinguishes_official_api_from_gateway():
    assert endpoint_kind_for_route({"channel": "official_api"}) == "official"
    assert endpoint_kind_for_route({"channel": "third_party_gateway"}) == "gateway"


def test_tool_specs_are_self_contained_json_schemas():
    specs = tool_specs()
    rendered = json.dumps(specs)
    assert "#/$defs/" not in rendered
    assert "submit_candidates" in rendered
    assert "validate_candidate" in rendered
    assert "run_synth_only" not in rendered
    submit = next(
        item["function"]
        for item in specs
        if item["function"]["name"] == "submit_candidates"
    )
    out_of_scope = submit["parameters"]["properties"]["out_of_scope"]["items"]
    assert out_of_scope["additionalProperties"] is False
    assert set(out_of_scope["required"]) == {
        "repo_url", "commit", "language", "reason", "discovery_method"
    }
    rendered_candidate = json.dumps(
        next(
            item["function"]
            for item in specs
            if item["function"]["name"] == "validate_candidate"
        )["parameters"]["properties"]["candidate"]
    )
    assert "repository_path" in rendered_candidate
    assert "path_or_url" not in rendered_candidate
    clone = next(
        item["function"]
        for item in specs
        if item["function"]["name"] == "clone_repository"
    )
    assert "opaque handle" in clone["description"]
    assert "provenance is bound" in clone["description"]
    search = next(
        item["function"]
        for item in specs
        if item["function"]["name"] == "search_repositories"
    )
    assert "pass that exact HTTPS value unchanged" in search["description"]


def test_anthropic_adapter_preserves_tool_calls_and_results():
    system, messages = anthropic_messages(
        [
            {"role": "system", "content": "frozen task"},
            {"role": "user", "content": "find RTL"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "function": {
                            "name": "search_repositories",
                            "arguments": '{"query":"uart"}',
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "[]"},
        ]
    )
    assert system == "frozen task"
    assert messages[1]["content"][0]["type"] == "tool_use"
    assert messages[2]["content"][0]["type"] == "tool_result"
    assert all("input_schema" in item for item in anthropic_tools())

    restored = anthropic_response_message(
        {
            "content": [
                {"type": "text", "text": "checking"},
                {
                    "type": "tool_use",
                    "id": "call_2",
                    "name": "submit_candidates",
                    "input": {"candidates": [], "out_of_scope": []},
                },
            ]
        }
    )
    assert restored["content"] == "checking"
    assert restored["tool_calls"][0]["function"]["name"] == "submit_candidates"


def test_responses_adapter_preserves_tool_calls_and_results():
    converted = responses_input(
        [
            {"role": "system", "content": "frozen task"},
            {"role": "user", "content": "find RTL"},
            {
                "role": "assistant",
                "content": "checking",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "function": {
                            "name": "search_repositories",
                            "arguments": '{"query":"uart"}',
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "[]"},
        ]
    )
    assert converted[2]["role"] == "assistant"
    assert converted[3]["type"] == "function_call"
    assert converted[4] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "[]",
    }
    assert all("name" in item and "function" not in item for item in responses_tools())

    restored = responses_response_message(
        {
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "checking"}],
                },
                {
                    "type": "function_call",
                    "id": "fc_1",
                    "call_id": "call_2",
                    "name": "submit_candidates",
                    "arguments": '{"candidates":[],"out_of_scope":[]}',
                },
            ]
        }
    )
    assert restored["content"] == "checking"
    assert restored["tool_calls"][0]["id"] == "call_2"
    assert restored["tool_calls"][0]["function"]["name"] == "submit_candidates"


def test_provider_usage_normalizes_openai_compatible_fields():
    assert provider_usage(
        {
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 25,
                "total_tokens": 125,
                "completion_tokens_details": {"reasoning_tokens": 7},
            }
        }
    ) == {"input": 100, "output": 18, "reasoning": 7, "total": 125}


def test_provider_usage_normalizes_responses_reasoning_fields():
    assert provider_usage(
        {
            "usage": {
                "input_tokens": 40,
                "output_tokens": 10,
                "total_tokens": 50,
                "output_tokens_details": {"reasoning_tokens": 3},
            }
        }
    ) == {"input": 40, "output": 7, "reasoning": 3, "total": 50}


def test_provider_usage_normalizes_separate_reasoning_fields():
    assert provider_usage(
        {
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 18,
                "total_tokens": 125,
                "completion_tokens_details": {"reasoning_tokens": 7},
            }
        }
    ) == {"input": 100, "output": 18, "reasoning": 7, "total": 125}


def test_provider_usage_reconciles_inconsistent_gateway_total():
    assert provider_usage(
        {
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 25,
                "total_tokens": 122,
                "completion_tokens_details": {"reasoning_tokens": 7},
            }
        }
    ) == {"input": 100, "output": 15, "reasoning": 7, "total": 122}


def test_empty_tool_calls_are_not_replayed_to_provider():
    value = assistant_history_message({"content": "continue", "tool_calls": []})
    assert value == {"role": "assistant", "content": "continue"}


def test_safe_path_references_are_inlined():
    value = inline_safe_paths(
        {"items": {"$ref": "#/$defs/safeRelativePath"}}
    )
    assert value["items"]["type"] == "string"
    assert "$ref" not in value["items"]


def test_optional_commit_spellings_resolve_to_head():
    for value in (None, "", "null", "None", "HEAD"):
        assert normalized_checkout(value) == "HEAD"
    assert normalized_checkout("a" * 40) == "a" * 40


def test_search_result_exposes_clone_repository_url_field():
    result = public_repository_record(
        {
            "full_name": "example/rtl",
            "html_url": "https://github.com/example/rtl",
            "license": {"spdx_id": "MIT"},
        }
    )
    assert result["repo_url"] == "https://github.com/example/rtl"
    assert result["html_url"] == result["repo_url"]
    assert result["license_spdx"] == "MIT"


def test_github_api_slot_persists_shared_pacing_timestamp(tmp_path, monkeypatch):
    lock = tmp_path / "locks" / "github_api.lock"
    monkeypatch.setenv("R2G_EXPERIMENT1_GITHUB_API_LOCK", str(lock))
    monkeypatch.setenv("R2G_EXPERIMENT1_GITHUB_API_MIN_INTERVAL_SECONDS", "2.2")
    with vanilla.github_api_slot():
        assert lock.exists()
    assert float(lock.read_text(encoding="utf-8")) > 0


def test_context_compaction_keeps_only_four_recent_tool_turns():
    runner = VanillaRun.__new__(VanillaRun)
    runner.queries = []
    runner.repo_meta = {}
    runner.validation_history = []
    runner.qualified_candidates = {}
    runner.search_budget = 120
    runner.token_budget = 2_000_000
    runner.wall_time_budget = 21_600
    runner.turn_count = 40
    runner.max_turns = 100
    runner.usage = {"input": 1_490_000, "output": 10_000, "reasoning": 0, "total": 1_500_000}
    runner.started_monotonic = __import__("time").monotonic() - 60
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "task"},
    ]
    for turn in range(8):
        messages.extend(
            [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{"id": str(turn)}],
                },
                {
                    "role": "tool",
                    "tool_call_id": str(turn),
                    "content": "x" * 1000,
                },
            ]
        )
    compacted = runner.compact_messages(messages)
    assert len(compacted) == 11
    assert compacted[3]["tool_calls"][0]["id"] == "4"
    state = json.loads(compacted[2]["content"].split(": ", 1)[1])
    assert state["provider_token_budget"] == 2_000_000
    assert state["provider_tokens_remaining"] == 500_000
    assert state["llm_turns_used"] == 40
    assert state["llm_turns_remaining"] == 60
    assert state["budget_phase"] == "low"
    assert state["qualified_candidate_count"] == 0
    assert "validate_candidate" in state["instruction"]


def test_readonly_terminal_allows_local_cat_but_blocks_escape(tmp_path):
    (tmp_path / "rtl.v").write_text("module rtl; endmodule\n", encoding="utf-8")
    runner = VanillaRun.__new__(VanillaRun)
    runner.repo_paths = {"repo": tmp_path}
    result = runner.readonly_command(
        {"repo_id": "repo", "argv": ["cat", "rtl.v"]}
    )
    assert result["returncode"] == 0
    assert "module rtl" in result["stdout"]
    with pytest.raises(ExperimentError):
        runner.readonly_command(
            {"repo_id": "repo", "argv": ["cat", "/etc/passwd"]}
        )


def test_run_kind_selects_distinct_workspace(tmp_path):
    runner = VanillaRun(
        route={
            "method_id": "qwen-vanilla",
            "model_id": "qwen-test",
            "provider": "test",
        },
        campaign_root=tmp_path,
        batch_id=1,
        target=25,
        max_turns=100,
        max_output_tokens=4096,
        run_kind="formal",
    )
    assert runner.root.name == "qwen-vanilla.batch1.formal"
    assert runner.last_search_monotonic is None
    assert runner.qualified_candidates == {}
    assert runner.token_budget == 20_000_000
    assert runner.wall_time_budget == 172_800
    assert runner.search_budget == 960
    assert runner.cpu_cores == 4
    assert runner.env["NUM_CORES"] == "4"
    assert runner.env["ORFS_MAX_CPUS"] == "4"


def test_qualification_checkpoint_is_written_once(tmp_path):
    runner = VanillaRun(
        route={
            "method_id": "qwen-vanilla",
            "model_id": "qwen-test",
            "provider": "test",
        },
        campaign_root=tmp_path,
        batch_id=1,
        target=1,
        max_turns=1,
        max_output_tokens=128,
        run_kind="smoke",
    )
    runner.checkpoint_targets = (1,)
    candidate = candidate_fixture()
    key = (candidate["repo_url"], candidate["commit"], candidate["top_module"])
    runner.qualified_candidates[key] = candidate
    runner.write_qualification_checkpoints()
    checkpoint = runner.root / "checkpoints" / "qualified_001.json"
    first = checkpoint.read_text(encoding="utf-8")
    runner.write_qualification_checkpoints()
    assert checkpoint.read_text(encoding="utf-8") == first
    assert json.loads(first)["candidates"] == [candidate]


def test_natural_turn_exhaustion_is_scoreable_turn_limit(tmp_path, monkeypatch):
    runner = VanillaRun(
        route={
            "method_id": "kimi-vanilla",
            "model_id": "kimi-test",
            "provider": "test",
        },
        campaign_root=tmp_path,
        batch_id=1,
        target=1,
        max_turns=1,
        max_output_tokens=128,
        run_kind="smoke",
    )
    observed: dict = {}

    def fake_turn(messages):
        observed["messages"] = messages
        return {"role": "assistant", "content": "searching", "tool_calls": []}

    monkeypatch.setattr(runner, "api_turn", fake_turn)
    submission_path = runner.run()
    payload = json.loads(submission_path.read_text())
    result = json.loads((runner.root / "method_result.json").read_text())
    assert payload["stop_reason"] == "turn_limit"
    assert payload["resource_usage"]["llm_turns"] == 1
    prompt = "\n".join(str(message.get("content") or "") for message in observed["messages"])
    assert "20000000 cumulative provider tokens" in prompt
    assert "1 LLM response turns" in prompt
    assert "960 repository searches" in prompt
    assert "172800 seconds of method wall time" in prompt
    assert result["score_eligible"] is True
    assert result["submitted"] == 0


def test_submit_uses_only_durably_qualified_candidate(tmp_path):
    runner = VanillaRun(
        route={
            "method_id": "qwen-vanilla",
            "model_id": "qwen-test",
            "provider": "test",
        },
        campaign_root=tmp_path,
        batch_id=1,
        target=25,
        max_turns=100,
        max_output_tokens=4096,
        run_kind="formal",
    )
    candidate = {
        "candidate_id": "qualified",
        "repo_url": "https://github.com/example/rtl",
        "commit": "a" * 40,
        "top_module": "top",
    }
    key = (candidate["repo_url"], candidate["commit"], candidate["top_module"])
    runner.qualified_candidates[key] = candidate
    invented = {
        "candidate_id": "invented",
        "repo_url": "https://github.com/example/other",
        "commit": "b" * 40,
        "top_module": "other",
    }
    result = runner.submit({"candidates": [candidate, invented]})
    assert result["submitted"] == 1
    assert result["accepted"] is False
    assert runner.final is None


def test_submit_resolves_a_validated_candidate_reference_to_canonical_provenance(
    tmp_path,
):
    runner = VanillaRun(
        route={
            "method_id": "qwen-vanilla",
            "model_id": "qwen-test",
            "provider": "test",
        },
        campaign_root=tmp_path,
        batch_id=1,
        target=1,
        max_turns=100,
        max_output_tokens=4096,
        run_kind="formal",
    )
    qualified = candidate_fixture()
    runner.qualified_candidates[
        (qualified["repo_url"], qualified["commit"], qualified["top_module"])
    ] = qualified
    replay = dict(qualified)
    replay["repo_url"] = "https://github.com/example/transcribed-wrong"
    replay["commit"] = "b" * 40
    result = runner.submit({"candidates": [replay], "out_of_scope": []})
    assert result["accepted"] is True
    assert runner.final is not None
    assert runner.final["candidates"][0]["repo_url"] == qualified["repo_url"]


def candidate_fixture() -> dict:
    return {
        "candidate_id": "local_top",
        "repo_url": "https://github.com/example/rtl",
        "commit": "a" * 40,
        "top_module": "top",
        "rtl_files": ["top.v"],
        "header_files": [],
        "include_dirs": ["."],
        "defines": [],
        "top_parameters": {},
        "readmem_files": [],
        "language": "verilog",
        "license_evidence": {
            "spdx_id": "ISC",
            "repository_path": "COPYING",
            "note": "Pinned repository license",
        },
        "category": "controller",
        "discovery_method": "unit-test",
        "selection_reason": "Exercise the public precheck",
        "confidence": 1.0,
    }


def validation_runner(tmp_path: Path) -> VanillaRun:
    runner = VanillaRun(
        route={
            "method_id": "qwen-vanilla",
            "model_id": "qwen-test",
            "provider": "test",
        },
        campaign_root=tmp_path / "campaign",
        batch_id=1,
        target=1,
        max_turns=3,
        max_output_tokens=1024,
        run_kind="smoke",
    )
    source = tmp_path / "source"
    source.mkdir()
    (source / "top.v").write_text(
        "module top(input a, output y); assign y = a; endmodule\n",
        encoding="utf-8",
    )
    (source / "COPYING").write_text("ISC License\n", encoding="utf-8")
    runner.repo_paths["repo"] = source
    runner.repo_meta["repo"] = {
        "repo_url": "https://github.com/example/rtl",
        "commit": "a" * 40,
    }
    return runner


def passing_synth_result() -> dict:
    return {
        "returncode": 0,
        "elapsed_seconds": 1.0,
        "timed_out": False,
        "config_path": "/tmp/config.mk",
        "flow_variant": "base",
        "source_mapped_netlist": "/tmp/1_synth.v",
        "mapped_netlist": "/tmp/mapped.v",
        "synth_log": "/tmp/synth.log",
        "mapped_cells": 120,
        "has_functional_io": True,
        "unresolved_module_evidence": [],
        "synth_qualified": True,
    }


def test_validate_candidate_rejects_active_closure_gap_before_synth(
    tmp_path, monkeypatch
):
    runner = validation_runner(tmp_path)
    candidate = candidate_fixture()
    (runner.repo_paths["repo"] / "top.v").write_text(
        '`include "missing.vh"\nmodule top(input a, output y); assign y=a; endmodule\n',
        encoding="utf-8",
    )
    called = False

    def unexpected_synth(*args, **kwargs):
        nonlocal called
        called = True
        return passing_synth_result()

    monkeypatch.setattr(vanilla, "run_formal_synth", unexpected_synth)
    result = runner.validate_candidate({"repo_id": "repo", "candidate": candidate})
    assert result["failure_class"] == "compilation_closure_incomplete"
    assert result["synthesis_run"] is False
    assert called is False
    assert runner.qualified_candidates == {}


def test_validate_candidate_rejects_license_mismatch_before_synth(
    tmp_path, monkeypatch
):
    runner = validation_runner(tmp_path)
    candidate = candidate_fixture()
    candidate["license_evidence"]["spdx_id"] = "MIT"
    monkeypatch.setattr(
        vanilla,
        "run_formal_synth",
        lambda *args, **kwargs: pytest.fail("synthesis must not run"),
    )
    result = runner.validate_candidate({"repo_id": "repo", "candidate": candidate})
    assert result["failure_class"] == "license_evidence_incomplete"
    assert result["synthesis_run"] is False
    assert runner.qualified_candidates == {}


def test_validate_candidate_checkpoints_only_after_every_gate_passes(
    tmp_path, monkeypatch
):
    runner = validation_runner(tmp_path)
    candidate = candidate_fixture()
    monkeypatch.setattr(
        vanilla, "run_formal_synth", lambda *args, **kwargs: passing_synth_result()
    )
    result = runner.validate_candidate({"repo_id": "repo", "candidate": candidate})
    assert result["precheck_qualified"] is True
    assert result["failure_class"] is None
    assert len(runner.qualified_candidates) == 1


def test_validate_candidate_normalizes_equivalent_git_repository_urls(
    tmp_path, monkeypatch
):
    runner = validation_runner(tmp_path)
    candidate = candidate_fixture()
    candidate["repo_url"] = "https://github.com/EXAMPLE/RTL.git"
    monkeypatch.setattr(
        vanilla, "run_formal_synth", lambda *args, **kwargs: passing_synth_result()
    )
    result = runner.validate_candidate({"repo_id": "repo", "candidate": candidate})
    assert result["precheck_qualified"] is True


def test_validate_candidate_binds_provenance_to_the_repository_handle(
    tmp_path, monkeypatch
):
    runner = validation_runner(tmp_path)
    candidate = candidate_fixture()
    candidate["repo_url"] = "https://github.com/example/other"
    candidate["commit"] = "b" * 40
    monkeypatch.setattr(
        vanilla, "run_formal_synth", lambda *args, **kwargs: passing_synth_result()
    )
    result = runner.validate_candidate({"repo_id": "repo", "candidate": candidate})
    assert result["accepted"] is True
    assert result["precheck_qualified"] is True
    assert result["source_identity_bound_by_runner"] is True
    qualified = next(iter(runner.qualified_candidates.values()))
    assert qualified["repo_url"] == "https://github.com/example/rtl"
    assert qualified["commit"] == "a" * 40


def test_validate_candidate_does_not_checkpoint_synthesis_failure(
    tmp_path, monkeypatch
):
    runner = validation_runner(tmp_path)
    candidate = candidate_fixture()
    failed = passing_synth_result()
    failed.update(
        {
            "returncode": 1,
            "mapped_netlist": None,
            "mapped_cells": 0,
            "synth_qualified": False,
        }
    )
    monkeypatch.setattr(vanilla, "run_formal_synth", lambda *args, **kwargs: failed)
    result = runner.validate_candidate({"repo_id": "repo", "candidate": candidate})
    assert result["failure_class"] == "synth_failed"
    assert result["synthesis_run"] is True
    assert runner.qualified_candidates == {}


def test_submit_rejects_malformed_out_of_scope_without_finalizing(tmp_path):
    runner = VanillaRun(
        route={
            "method_id": "qwen-vanilla",
            "model_id": "qwen-test",
            "provider": "test",
        },
        campaign_root=tmp_path,
        batch_id=1,
        target=25,
        max_turns=100,
        max_output_tokens=4096,
        run_kind="formal",
    )
    result = runner.submit(
        {
            "candidates": [],
            "out_of_scope": [
                {
                    "repo_url": "https://github.com/example/vhdl",
                    "commit": "a" * 40,
                    "reason": "VHDL source",
                }
            ],
        }
    )
    assert result["accepted"] is False
    assert result["schema_errors"]
    assert runner.final is None


def test_early_submission_has_distinct_scoreable_stop_reason():
    assert submitted_stop_reason(25, 25) == "target_reached"
    assert submitted_stop_reason(1, 25) == "submitted_early"
    assert submitted_stop_reason(0, 25) == "submitted_early"


def test_empty_submission_is_rejected_without_finalizing(tmp_path: Path):
    runner = VanillaRun(
        route={"method_id": "qwen-vanilla"},
        campaign_root=tmp_path,
        batch_id=1,
        target=1,
        max_turns=4,
        max_output_tokens=4096,
        run_kind="smoke",
    )
    result = runner.submit({"candidates": [], "out_of_scope": []})
    assert result["accepted"] is False
    assert result["failure_class"] == "empty_submission"
    assert runner.final is None


def test_smoke_completion_is_early_relative_to_formal_protocol():
    assert run_submission_stop_reason(1, 1, "smoke") == "submitted_early"
    assert run_submission_stop_reason(200, 200, "formal") == "target_reached"


def test_transient_network_classification_is_bounded_to_transport_errors():
    import http.client
    import urllib.error

    assert is_transient_network_error(urllib.error.URLError("temporary TLS failure"))
    assert is_transient_network_error(http.client.IncompleteRead(b"partial"))
    assert not is_transient_network_error(ValueError("bad response schema"))
    assert is_transient_git_failure("gnutls_handshake() failed: TLS connection terminated")
    assert not is_transient_git_failure("repository not found")


def test_validation_event_summary_exposes_gate_result_without_candidate_payload():
    result = {
        "accepted": True,
        "precheck_qualified": False,
        "failure_class": "compilation_closure_incomplete",
        "synthesis_run": False,
        "input_evidence": {"files": ["large", "private", "payload"]},
        "schema_errors": ["first", "second"],
    }
    assert validation_event_summary(result) == {
        "accepted": True,
        "precheck_qualified": False,
        "failure_class": "compilation_closure_incomplete",
        "synthesis_run": False,
        "schema_errors": ["first", "second"],
    }
