import importlib.util
import json
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO / "tools/run_experiment4_graph_conversion.py"
SPEC = importlib.util.spec_from_file_location("run_experiment4_graph_conversion", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_method_config_only_retargets_output(tmp_path):
    case = tmp_path / "case"
    case.mkdir()
    source = {"schema": "r2g2_four_stage_sample_v1", "output_dir": "/old", "yosys_v": "/raw/a.v"}
    (case / "sample.json").write_text(json.dumps(source), encoding="utf-8")
    method = tmp_path / "method"
    result = MODULE.method_config(case, method)
    payload = json.loads(result.read_text(encoding="utf-8"))
    assert payload["yosys_v"] == "/raw/a.v"
    assert payload["output_dir"] == str((method / "generated").resolve())


def test_converter_cannot_call_r2g_or_external_processes(tmp_path):
    safe = tmp_path / "safe.py"
    safe.write_text("import argparse\n", encoding="utf-8")
    MODULE.validate_converter(safe)

    unsafe = tmp_path / "unsafe.py"
    unsafe.write_text("import subprocess\n", encoding="utf-8")
    with pytest.raises(ValueError, match="forbidden"):
        MODULE.validate_converter(unsafe)


def test_cohort_rows_all_has_no_duplication():
    cohort = {"splits": {"canary": [{"task_id": "a"}], "development": [{"task_id": "b"}], "hidden_test": [{"task_id": "c"}]}}
    assert [row["task_id"] for row in MODULE.cohort_rows(cohort, "all")] == ["a", "b", "c"]


def test_frozen_input_is_an_independent_copy(tmp_path):
    source = tmp_path / "source.dat"
    target = tmp_path / "frozen/input.dat"
    source.write_text("original", encoding="utf-8")
    MODULE.copy_frozen_input(source, target)
    source.write_text("changed", encoding="utf-8")
    assert target.read_text(encoding="utf-8") == "original"


def test_frozen_input_copy_replaces_an_existing_hardlink(tmp_path):
    source = tmp_path / "source.dat"
    target = tmp_path / "frozen/input.dat"
    target.parent.mkdir(parents=True)
    source.write_text("original", encoding="utf-8")
    target.hardlink_to(source)
    MODULE.copy_frozen_input(source, target)
    source.write_text("changed", encoding="utf-8")
    assert target.read_text(encoding="utf-8") == "original"


def test_input_attestation_detects_changed_artifact(tmp_path):
    artifact = tmp_path / "input.dat"
    config = tmp_path / "sample.json"
    openroad = tmp_path / "openroad"
    artifact.write_text("input", encoding="utf-8")
    config.write_text("{}", encoding="utf-8")
    openroad.write_text("binary", encoding="utf-8")
    state = {
        "status": "ready",
        "materializer_version": MODULE.MATERIALIZER_VERSION,
        "baseline_result_sha256": "baseline",
        "config": str(config),
        "config_sha256": MODULE.sha256_file(config),
        "artifacts": {
            "input": {
                "path": str(artifact),
                "bytes": artifact.stat().st_size,
                "sha256": MODULE.sha256_file(artifact),
            }
        },
        "toolchain": {
            "openroad_exe": str(openroad),
            "openroad_sha256": MODULE.sha256_file(openroad),
        },
    }
    row = {"baseline_result_sha256": "baseline"}
    assert MODULE.input_attestation_errors(state, row) == []
    artifact.write_text("changed", encoding="utf-8")
    assert any("artifact" in error for error in MODULE.input_attestation_errors(state, row))


def test_hidden_gate_rejects_missing_converter_manifests(tmp_path):
    protocol = tmp_path / "protocol"
    protocol.mkdir()
    (protocol / "experiment4_resource_limits.json").write_text(
        json.dumps({"converter_development": {"total_tokens_per_model": 200000, "api_calls_per_model": 5}}),
        encoding="utf-8",
    )
    errors = MODULE.frozen_converter_gate_errors(tmp_path)
    assert len(errors) == 3


def test_hidden_gate_accepts_three_attested_converters(tmp_path):
    protocol = tmp_path / "protocol"
    protocol.mkdir()
    (protocol / "experiment4_resource_limits.json").write_text(
        json.dumps({"converter_development": {"total_tokens_per_model": 200000, "api_calls_per_model": 5}}),
        encoding="utf-8",
    )
    for model_key in ("gpt", "claude", "qwen"):
        root = tmp_path / "converter_development" / model_key
        root.mkdir(parents=True)
        converter = root / "frozen_converter.py"
        converter.write_text("import argparse\n", encoding="utf-8")
        (root / "frozen_converter_manifest.json").write_text(
            json.dumps(
                {
                    "model_key": model_key,
                    "converter": str(converter),
                    "converter_sha256": MODULE.sha256_file(converter),
                    "hidden_test_exposed": False,
                }
            ),
            encoding="utf-8",
        )
        (root / "token_ledger.json").write_text(
            json.dumps({"consumed": 100, "calls": [{}, {}, {}]}), encoding="utf-8"
        )
    assert MODULE.frozen_converter_gate_errors(tmp_path) == []
