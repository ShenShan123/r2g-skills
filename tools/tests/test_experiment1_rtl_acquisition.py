from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import tools.run_experiment1_rtl_acquisition as experiment
from tools.build_experiment1_r2g_submission import query_records
from tools.run_experiment1_rtl_acquisition import (
    EXPERIMENT_DIR,
    METHOD_IDS,
    SUBMISSION_SCHEMA,
    batch_summary,
    clone_candidate,
    infer_spdx_identifier,
    inspect_mapped_netlist,
    semantic_submission_errors,
    synthesis_qualification_failure,
    submission_score_eligibility,
    source_cache_key,
    verify_candidate_inputs,
    validate_json,
    validate_model_preflight,
    write_synth_project,
)
from tools.run_experiment1_r2g_method import (
    classify_stop_reason,
    frontier_counts,
    query_records as expander_query_records,
)


EXAMPLE = EXPERIMENT_DIR / "experiment1_submission.example.json"


def submission() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def test_example_submission_passes_schema_and_semantics():
    value = submission()
    assert validate_json(value, SUBMISSION_SCHEMA) == []
    assert semantic_submission_errors(value) == []


def test_formal_method_set_contains_six_vanilla_and_one_cold_r2g_condition():
    assert len(METHOD_IDS) == 7
    assert {item for item in METHOD_IDS if item.endswith("-vanilla")} == {
        "openai-vanilla",
        "anthropic-vanilla",
        "deepseek-vanilla",
        "qwen-vanilla",
        "glm-vanilla",
        "kimi-vanilla",
    }
    assert "r2g-expander-cold" in METHOD_IDS
    assert "r2g-expander-warm" not in METHOD_IDS


def test_preflight_is_digest_bound(tmp_path: Path):
    routes = tmp_path / "routes.json"
    routes.write_text("{}\n", encoding="utf-8")
    preflight = tmp_path / "preflight.json"
    preflight.write_text(
        json.dumps(
            {
                "routes_sha256": experiment.sha256_file(routes),
                "results": [
                    {"method_id": method, "status": "ready"}
                    for method in sorted(item for item in METHOD_IDS if item.endswith("-vanilla"))
                ],
            }
        ),
        encoding="utf-8",
    )
    validate_model_preflight(preflight, routes)

def test_evaluator_rejects_feedback_before_all_four_batches_lock(
    tmp_path: Path, monkeypatch
):
    campaign = tmp_path / "campaign"
    campaign.mkdir()
    manifest = {
        "batches": [
            {
                "method_id": "openai-vanilla",
                "batch_id": batch_id,
                "status": "submitted" if batch_id < 4 else "pending",
            }
            for batch_id in range(1, 5)
        ]
    }
    (campaign / "execution_manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(experiment, "verify_bound_campaign", lambda _manifest: None)
    with pytest.raises(experiment.ExperimentError, match="all four"):
        experiment.evaluate_batch(
            SimpleNamespace(
                campaign_root=campaign,
                method_id="openai-vanilla",
                batch_id=1,
                cores=4,
            )
        )


def test_campaign_init_binds_formal_protocol_and_preflight(
    tmp_path: Path, monkeypatch
):
    routes = EXPERIMENT_DIR / "experiment1_model_routes.json"
    preflight = tmp_path / "preflight.json"
    preflight.write_text(
        json.dumps(
            {
                "routes_sha256": experiment.sha256_file(routes),
                "results": [
                    {"method_id": method, "status": "ready"}
                    for method in sorted(item for item in METHOD_IDS if item.endswith("-vanilla"))
                ],
            }
        ),
        encoding="utf-8",
    )
    def fake_git(*args, **_kwargs):
        if args[:2] == ("rev-parse", "HEAD"):
            return "a" * 40
        return ""

    monkeypatch.setattr(experiment, "git_text", fake_git)
    monkeypatch.setattr(experiment, "resolved_agent_env", lambda: {})
    monkeypatch.setattr(
        experiment,
        "toolchain_record",
        lambda _env: {
            "platform": "sky130hd",
            "orfs_root": "/orfs",
            "orfs_commit": "b" * 40,
            "yosys_version": "Yosys test",
            "pdk_root": "/pdk",
        },
    )
    campaign = tmp_path / "campaign"
    experiment.init_campaign(
        SimpleNamespace(
            campaign_root=campaign,
            campaign_id="canary",
            model_routes=routes,
            model_preflight=preflight,
            allow_dirty_canary=True,
        )
    )
    manifest = json.loads((campaign / "execution_manifest.json").read_text())
    assert len(manifest["batches"]) == 28
    assert len(manifest["method_reports"]) == 7
    assert manifest["protocol"]["path"].endswith(
        "docs/experiments/formal_experiment_1_2_key_design_zh.md"
    )
    assert manifest["model_route_preflight"]["sha256"] == experiment.sha256_file(preflight)
    assert "r2g_warm_memory" not in manifest
    assert experiment.validate_json(manifest, experiment.MANIFEST_SCHEMA) == []


def test_target_reached_requires_25_candidates():
    value = submission()
    value["stop_reason"] = "target_reached"
    errors = semantic_submission_errors(value)
    assert any("exactly 25" in item for item in errors)


def test_smoke_target_reached_uses_explicit_target():
    value = submission()
    value["stop_reason"] = "target_reached"
    assert semantic_submission_errors(value, target_candidates=1) == []


def test_infrastructure_termination_is_valid_audit_but_not_score_eligible():
    value = submission()
    value["stop_reason"] = "provider_failure"
    assert validate_json(value, SUBMISSION_SCHEMA) == []
    assert semantic_submission_errors(value) == []
    assert submission_score_eligibility(value) == (
        False,
        "non-scoreable infrastructure termination: provider_failure",
    )


def test_submitted_early_is_score_eligible_and_keeps_missing_slots_as_failures():
    value = submission()
    value["stop_reason"] = "submitted_early"
    assert validate_json(value, SUBMISSION_SCHEMA) == []
    assert semantic_submission_errors(value) == []
    assert submission_score_eligibility(value) == (True, None)


def test_duplicate_candidate_key_is_rejected():
    value = submission()
    duplicate = deepcopy(value["candidates"][0])
    duplicate["candidate_id"] = "same_source_new_id"
    value["candidates"].append(duplicate)
    errors = semantic_submission_errors(value)
    assert any("unique within the batch" in item for item in errors)


def test_repository_submission_cap_is_enforced():
    value = submission()
    first = value["candidates"][0]
    value["candidates"] = []
    for index in range(5):
        candidate = deepcopy(first)
        candidate["candidate_id"] = f"same_repo_{index}"
        candidate["top_module"] = f"top_{index}"
        value["candidates"].append(candidate)
    errors = semantic_submission_errors(value)
    assert any("at most four" in item for item in errors)


def test_prior_batch_candidate_key_is_rejected():
    value = submission()
    key = (
        value["candidates"][0]["repo_url"],
        value["candidates"][0]["commit"],
        value["candidates"][0]["top_module"],
    )
    errors = semantic_submission_errors(value, prior_keys={key})
    assert any("earlier batch" in item for item in errors)


def test_token_accounting_mismatch_is_rejected():
    value = submission()
    value["resource_usage"]["total_tokens"] += 1
    errors = semantic_submission_errors(value)
    assert any("total_tokens" in item for item in errors)


def test_query_count_must_match_resource_record():
    value = submission()
    value["resource_usage"]["search_requests"] = 1
    errors = semantic_submission_errors(value)
    assert any("query log length" in item for item in errors)


def test_r2g_submission_query_records_reads_jsonl(tmp_path: Path):
    (tmp_path / "search_queries.jsonl").write_text(
        json.dumps(
            {
                "query": "processor language:Verilog",
                "backend": "github",
                "page": 1,
                "timestamp": "2026-07-30T00:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    assert query_records(tmp_path) == [
        {
            "query": "processor language:Verilog",
            "backend": "github",
            "page": 1,
            "timestamp": "2026-07-30T00:00:00Z",
        }
    ]


def test_schema_rejects_path_escape_and_search_overrun():
    value = submission()
    value["candidates"][0]["rtl_files"] = ["../private.v"]
    value["resource_usage"]["search_requests"] = 121
    errors = validate_json(value, SUBMISSION_SCHEMA)
    assert any("private.v" in item for item in errors)
    assert any("greater than the maximum of 120" in item for item in errors)


def test_schema_requires_repository_relative_license_evidence():
    value = submission()
    value["candidates"][0]["license_evidence"]["repository_path"] = (
        "https://github.com/example/rtl/blob/" + "a" * 40 + "/COPYING"
    )
    errors = validate_json(value, SUBMISSION_SCHEMA)
    assert any("repository_path" in item for item in errors)

    legacy = submission()
    evidence = legacy["candidates"][0]["license_evidence"]
    evidence["path_or_url"] = evidence.pop("repository_path")
    errors = validate_json(legacy, SUBMISSION_SCHEMA)
    assert any("repository_path" in item for item in errors)


def test_mapped_netlist_inspection_counts_cells_and_functional_io(tmp_path: Path):
    netlist = tmp_path / "mapped.v"
    instances = "\n".join(
        f"  sky130_fd_sc_hd__inv_1 _{index}_ (\n    .A(in),\n    .Y(out)\n  );"
        for index in range(100)
    )
    netlist.write_text(
        "module top(in, out);\n"
        "  input in;\n"
        "  output out;\n"
        f"{instances}\n"
        "endmodule\n",
        encoding="utf-8",
    )
    result = inspect_mapped_netlist(netlist, "")
    assert result["mapped_cells"] == 100
    assert result["has_functional_io"] is True
    assert result["unresolved_module_evidence"] == []


def test_synthesis_gate_rejects_trivial_and_oversize_designs():
    base = {
        "returncode": 0,
        "timed_out": False,
        "mapped_netlist": "/tmp/mapped.v",
        "unresolved_module_evidence": [],
        "has_functional_io": True,
    }
    assert synthesis_qualification_failure({**base, "mapped_cells": 99}) == "trivial_design"
    assert synthesis_qualification_failure({**base, "mapped_cells": 100}) is None
    assert synthesis_qualification_failure({**base, "mapped_cells": 99999}) is None
    assert synthesis_qualification_failure({**base, "mapped_cells": 100000}) == "oversize_design"


def test_synth_project_stages_whitespace_paths(tmp_path: Path):
    source = tmp_path / "source tree"
    rtl = source / "rtl file .v"
    rtl.parent.mkdir(parents=True)
    rtl.write_text("module top(input clk, input a, output y); assign y=a; endmodule\n")
    candidate = deepcopy(submission()["candidates"][0])
    candidate.update({
        "repo_url": "https://github.com/example/space-path",
        "commit": "b" * 40,
        "top_module": "top",
        "rtl_files": ["rtl file .v"],
        "include_dirs": ["."],
        "defines": [],
        "top_parameters": {},
    })
    config, _variant = write_synth_project(candidate, source, tmp_path / "project")
    text = config.read_text()
    verilog_line = next(line for line in text.splitlines() if line.startswith("export VERILOG_FILES"))
    assert "source tree" not in verilog_line
    staged = Path(verilog_line.split("=", 1)[1].strip())
    assert staged.is_symlink()
    assert staged.resolve() == rtl.resolve()


def test_compile_closure_detects_undeclared_transitive_header(tmp_path: Path):
    (tmp_path / "top.v").write_text(
        '`include "defs.vh"\nmodule top(input a, output y); assign y = a; endmodule\n',
        encoding="utf-8",
    )
    (tmp_path / "defs.vh").write_text("`define WIDTH 1\n", encoding="utf-8")
    (tmp_path / "COPYING").write_text("ISC\n", encoding="utf-8")
    candidate = deepcopy(submission()["candidates"][0])
    candidate.update(
        {
            "rtl_files": ["top.v"],
            "header_files": [],
            "include_dirs": ["."],
            "readmem_files": [],
        }
    )
    result = verify_candidate_inputs(candidate, tmp_path)
    assert result["undeclared_headers"] == ["defs.vh"]


def test_compile_closure_accepts_declared_transitive_header(tmp_path: Path):
    (tmp_path / "top.v").write_text(
        '`include "defs.vh"\nmodule top(input a, output y); assign y = a; endmodule\n',
        encoding="utf-8",
    )
    (tmp_path / "defs.vh").write_text("`define WIDTH 1\n", encoding="utf-8")
    (tmp_path / "COPYING").write_text("ISC\n", encoding="utf-8")
    candidate = deepcopy(submission()["candidates"][0])
    candidate.update(
        {
            "rtl_files": ["top.v"],
            "header_files": ["defs.vh"],
            "include_dirs": ["."],
            "readmem_files": [],
        }
    )
    result = verify_candidate_inputs(candidate, tmp_path)
    assert result["undeclared_headers"] == []
    assert result["missing_include_references"] == []


def test_compile_closure_ignores_include_in_inactive_branch(tmp_path: Path):
    (tmp_path / "top.v").write_text(
        "`ifdef FORMAL_ONLY\n"
        '`include "missing_formal_harness.vh"\n'
        "`endif\n"
        "module top(input a, output y); assign y = a; endmodule\n",
        encoding="utf-8",
    )
    (tmp_path / "COPYING").write_text("ISC\n", encoding="utf-8")
    candidate = deepcopy(submission()["candidates"][0])
    candidate.update(
        {
            "rtl_files": ["top.v"],
            "header_files": [],
            "include_dirs": ["."],
            "defines": [],
            "readmem_files": [],
        }
    )
    result = verify_candidate_inputs(candidate, tmp_path)
    assert result["missing_include_references"] == []


def test_compile_closure_checks_include_when_candidate_define_activates_it(
    tmp_path: Path,
):
    (tmp_path / "top.v").write_text(
        "`ifdef FORMAL_ONLY\n"
        '`include "missing_formal_harness.vh"\n'
        "`endif\n"
        "module top(input a, output y); assign y = a; endmodule\n",
        encoding="utf-8",
    )
    (tmp_path / "COPYING").write_text("ISC\n", encoding="utf-8")
    candidate = deepcopy(submission()["candidates"][0])
    candidate.update(
        {
            "rtl_files": ["top.v"],
            "header_files": [],
            "include_dirs": ["."],
            "defines": ["FORMAL_ONLY=1"],
            "readmem_files": [],
        }
    )
    result = verify_candidate_inputs(candidate, tmp_path)
    assert result["missing_include_references"] == [
        "top.v:missing_formal_harness.vh"
    ]


def test_compile_closure_applies_macros_from_active_includes(tmp_path: Path):
    (tmp_path / "top.v").write_text(
        '`include "config.vh"\n'
        "`ifndef FEATURE_READY\n"
        '`include "missing_fallback.vh"\n'
        "`endif\n"
        "module top(input a, output y); assign y = a; endmodule\n",
        encoding="utf-8",
    )
    (tmp_path / "config.vh").write_text(
        "`define FEATURE_READY\n", encoding="utf-8"
    )
    (tmp_path / "COPYING").write_text("ISC\n", encoding="utf-8")
    candidate = deepcopy(submission()["candidates"][0])
    candidate.update(
        {
            "rtl_files": ["top.v"],
            "header_files": ["config.vh"],
            "include_dirs": ["."],
            "defines": [],
            "readmem_files": [],
        }
    )
    result = verify_candidate_inputs(candidate, tmp_path)
    assert result["missing_include_references"] == []


def test_license_is_read_from_pinned_repository_file(tmp_path: Path):
    (tmp_path / "top.v").write_text(
        "module top(input a, output y); assign y = a; endmodule\n",
        encoding="utf-8",
    )
    (tmp_path / "COPYING").write_text("ISC License\n", encoding="utf-8")
    candidate = deepcopy(submission()["candidates"][0])
    candidate["rtl_files"] = ["top.v"]
    candidate["license_evidence"] = {
        "spdx_id": "ISC",
        "repository_path": "COPYING",
        "note": "Pinned repository license",
    }
    result = verify_candidate_inputs(candidate, tmp_path)
    assert result["license_location_ok"] is True
    assert result["license_spdx_observed"] == "ISC"
    assert result["license_declared"] is True


def test_noncanonical_spdx_does_not_match_observed_license(tmp_path: Path):
    (tmp_path / "top.v").write_text(
        "module top(input a, output y); assign y = a; endmodule\n",
        encoding="utf-8",
    )
    (tmp_path / "COPYING").write_text(
        "GNU Lesser General Public License\nVersion 2.1\n",
        encoding="utf-8",
    )
    candidate = deepcopy(submission()["candidates"][0])
    candidate["rtl_files"] = ["top.v"]
    candidate["license_evidence"] = {
        "spdx_id": "LGPL-2.1",
        "repository_path": "COPYING",
        "note": "Ambiguous legacy identifier",
    }
    result = verify_candidate_inputs(candidate, tmp_path)
    assert result["license_spdx_observed"] == "LGPL-2.1-only"
    assert result["license_declared"] is False


def test_r2g_method_stop_reason_distinguishes_completion_from_crash():
    assert classify_stop_reason(0, 25, 25) == "target_reached"
    assert classify_stop_reason(0, 20, 25) == "search_exhausted"
    assert classify_stop_reason(124, 20, 25) == "wall_time_limit"
    assert classify_stop_reason(1, 20, 25) == "provider_failure"
    assert classify_stop_reason(1, 25, 25) == "provider_failure"


def test_expander_search_budget_counts_requests_not_query_definitions(tmp_path: Path):
    import sqlite3

    state = tmp_path / "state"
    state.mkdir()
    connection = sqlite3.connect(state / "frontier.sqlite")
    connection.executescript(
        """
        CREATE TABLE queries (
          query_id TEXT PRIMARY KEY, provider TEXT, query_text TEXT,
          created_at TEXT, updated_at TEXT, attempts INTEGER
        );
        CREATE TABLE repository_revisions (id TEXT);
        CREATE TABLE acquisition_attempts (id TEXT);
        INSERT INTO queries VALUES
          ('q1','github','uart rtl','2026-01-01T00:00:00+00:00',
           '2026-01-01T00:10:00+00:00',3);
        """
    )
    connection.commit()
    connection.close()
    counts = frontier_counts(tmp_path)
    records = expander_query_records(tmp_path)
    assert counts["query_definitions"] == 1
    assert counts["search_requests"] == 3
    assert len(records) == 3
    assert [row["page"] for row in records] == [1, 2, 3]
    incremental = expander_query_records(tmp_path, {"q1": 2})
    assert len(incremental) == 1
    assert incremental[0]["page"] == 3


def test_primary_rates_use_fixed_target_denominator():
    results = [{"qualified": True} for _ in range(20)]
    summary = batch_summary(results, target_candidates=25)
    assert summary["submission_completion_rate"] == 0.8
    assert summary["independent_qualification_rate"] == 0.8

    mixed = [{"qualified": True} for _ in range(15)] + [
        {"qualified": False} for _ in range(10)
    ]
    summary = batch_summary(mixed, target_candidates=25)
    assert summary["submission_completion_rate"] == 1.0
    assert summary["independent_qualification_rate"] == 0.6


def test_diverse_qualified_yield_uses_only_qualified_repositories():
    results = [
        {"qualified": True, "key": ["https://github.com/a/repo", "a" * 40, "top0"]},
        {"qualified": True, "key": ["https://github.com/a/repo", "a" * 40, "top1"]},
        {"qualified": True, "key": ["https://github.com/b/repo", "b" * 40, "top"]},
        {"qualified": False, "key": ["https://github.com/c/repo", "c" * 40, "top"]},
    ]
    summary = batch_summary(results, target_candidates=25)
    assert summary["qualified_unique_repositories"] == 2
    assert summary["qualified_effective_repository_count"] == pytest.approx(1.8)
    assert summary["diverse_qualified_yield"] == pytest.approx(1.8 / 25)


def test_source_cache_key_is_per_repository_commit_not_top():
    first = submission()["candidates"][0]
    second = deepcopy(first)
    second["top_module"] = "another_top"
    assert source_cache_key(first) == source_cache_key(second)


def test_clone_candidate_does_not_fetch_unlisted_submodules(
    monkeypatch, tmp_path: Path
):
    candidate = deepcopy(submission()["candidates"][0])
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        commands.append(command)
        if command[:2] == ["git", "clone"]:
            destination = Path(command[-1])
            destination.mkdir(parents=True)
            (destination / ".gitmodules").write_text(
                '[submodule "unused"]\n\tpath = unused\n\turl = https://example.invalid/unused\n',
                encoding="utf-8",
            )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(experiment, "run", fake_run)
    monkeypatch.setattr(
        experiment,
        "git_text",
        lambda *args, **kwargs: candidate["commit"],
    )
    destination = tmp_path / "source"
    clone_candidate(candidate, destination, tmp_path / "git.log")
    assert destination.is_dir()
    assert not any(command[:2] == ["git", "submodule"] for command in commands)


def test_license_text_is_normalized_to_spdx():
    assert (
        infer_spdx_identifier(
            "Apache License\nVersion 2.0, January 2004"
        )
        == "Apache-2.0"
    )
    assert (
        infer_spdx_identifier(
            "GNU Lesser General Public License, version 3 or any later version"
        )
        == "LGPL-3.0-or-later"
    )
    assert (
        infer_spdx_identifier(
            "SPDX-License-Identifier: CERN-OHL-W-2.0"
        )
        == "CERN-OHL-W-2.0"
    )
    assert (
        infer_spdx_identifier(
            "GNU GENERAL PUBLIC LICENSE\nVersion 3, 29 June 2007\n"
            + ("terms " * 500)
            + "GNU Lesser General Public License, version 3 or later"
        )
        == "GPL-3.0-only"
    )
    assert (
        infer_spdx_identifier(
            "Redistribution and use in source and binary forms, with or without "
            "modification, are permitted provided that the following conditions "
            "are met. THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND "
            "CONTRIBUTORS \"AS IS\"."
        )
        == "BSD-2-Clause"
    )
    assert (
        infer_spdx_identifier(
            "Redistribution and use in source and binary forms are permitted. "
            "Neither the name of the copyright holder nor the names of its "
            "contributors may be used to endorse or promote products. "
            "THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "
            "\"AS IS\"."
        )
        == "BSD-3-Clause"
    )


def test_synth_project_writes_orfs_top_parameter_dictionary(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "top.v").write_text(
        "module top #(parameter WIDTH=8) (input clk, output q); assign q=clk; endmodule\n",
        encoding="utf-8",
    )
    candidate = deepcopy(submission()["candidates"][0])
    candidate.update(
        {
            "top_module": "top",
            "rtl_files": ["top.v"],
            "include_dirs": [],
            "top_parameters": {"WIDTH": 8, "ENABLED": True},
        }
    )
    config, _ = write_synth_project(candidate, source, tmp_path / "project")
    text = config.read_text(encoding="utf-8")
    assert "export VERILOG_TOP_PARAMS = WIDTH 8 ENABLED 1\n" in text
    assert "VERILOG_TOP_PARAMS = {" not in text


def test_synth_project_rejects_ambiguous_top_parameter(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "top.v").write_text(
        "module top(input clk, output q); assign q=clk; endmodule\n",
        encoding="utf-8",
    )
    candidate = deepcopy(submission()["candidates"][0])
    candidate.update(
        {
            "top_module": "top",
            "rtl_files": ["top.v"],
            "include_dirs": [],
            "top_parameters": {"MASK": "32'h 0000_0000"},
        }
    )
    with pytest.raises(experiment.ExperimentError):
        write_synth_project(candidate, source, tmp_path / "project")
