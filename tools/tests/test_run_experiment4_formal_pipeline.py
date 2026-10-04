import importlib.util
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO / "tools/run_experiment4_formal_pipeline.py"
SPEC = importlib.util.spec_from_file_location("run_experiment4_formal_pipeline", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_method_specs_are_fixed_and_use_only_frozen_converters(tmp_path):
    assert MODULE.method_specs(tmp_path) == [
        ("r2g-frozen-v3", None),
        ("llm-gpt-frozen", tmp_path / "converter_development/gpt/frozen_converter.py"),
        ("llm-claude-frozen", tmp_path / "converter_development/claude/frozen_converter.py"),
        ("llm-qwen-frozen", tmp_path / "converter_development/qwen/frozen_converter.py"),
    ]


def test_converter_manifest_gate_requires_all_three_models(tmp_path):
    assert MODULE.converter_manifests(tmp_path) == [
        tmp_path / "converter_development/gpt/frozen_converter_manifest.json",
        tmp_path / "converter_development/claude/frozen_converter_manifest.json",
        tmp_path / "converter_development/qwen/frozen_converter_manifest.json",
    ]


def test_pre_hidden_audit_status_and_cohort_are_both_required(tmp_path):
    cohort = tmp_path / 'cohort.json'
    cohort.write_text('{}')
    digest = MODULE.sha256_file(cohort)
    assert digest != ''
    ready = {'status': 'ready_for_hidden_evaluation', 'phase': 'hidden_evaluation',
             'cohort_sha256': digest, 'failed_checks': []}
    assert MODULE.pre_hidden_audit_errors(ready, cohort) == []
    ready['cohort_sha256'] = 'wrong'
    assert 'cohort hash' in MODULE.pre_hidden_audit_errors(ready, cohort)[0]


def test_semantic_usable_requires_native_and_verified_core_pass():
    native = {
        "method": {
            "expected_tasks": 3,
            "scores": [
                {"task_id": "a", "strict_pass": True},
                {"task_id": "b", "strict_pass": True},
                {"task_id": "c", "strict_pass": False},
            ],
        }
    }
    semantic = {
        "rows": [
            {"method": "method", "task": "a", "verified_core_status": "PASS"},
            {"method": "method", "task": "b", "verified_core_status": "FAIL"},
            {"method": "method", "task": "c", "verified_core_status": "PASS"},
        ]
    }
    result = MODULE.semantic_usable_summary(native, semantic)["method"]
    assert result["native_strict_passes"] == 2
    assert result["verified_core_semantic_passes"] == 2
    assert result["semantic_usable_passes"] == 1
    assert result["semantic_usable_rate"] == 1 / 3
