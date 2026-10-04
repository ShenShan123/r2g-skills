import importlib.util
import json
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO / "experiments/run_experiment4_llm_converter.py"
SPEC = importlib.util.spec_from_file_location("run_experiment4_llm_converter", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_parse_response_accepts_fenced_json():
    payload = MODULE.parse_response('```json\n{"summary":"ok","python_source":"print(1)"}\n```')
    assert payload == {"summary": "ok", "python_source": "print(1)"}


def test_parse_response_accepts_delimiter_envelope_without_json_escaping():
    payload = MODULE.parse_response(
        "===SUMMARY_BEGIN===\nrevision\n===SUMMARY_END===\n"
        "===PYTHON_SOURCE_BEGIN===\nprint({'raw': r'\\w+'})\n===PYTHON_SOURCE_END==="
    )
    assert payload == {"summary": "revision", "python_source": "print({'raw': r'\\w+'})"}


def test_parse_response_accepts_outer_fenced_delimiter_envelope():
    payload = MODULE.parse_response(
        "```text\n===SUMMARY_BEGIN===\nrevision\n===SUMMARY_END===\n"
        "===PYTHON_SOURCE_BEGIN===\nprint('complete source')\n===PYTHON_SOURCE_END===\n```"
    )
    assert payload == {"summary": "revision", "python_source": "print('complete source')"}


def test_assistant_text_accepts_typed_content_blocks():
    text, source = MODULE.assistant_text(
        {"content": [{"type": "text", "text": "first"}, {"type": "text", "text": " second"}]}
    )
    assert text == "first second"
    assert source == "content_blocks"


def test_assistant_text_falls_back_to_reasoning_content():
    text, source = MODULE.assistant_text({"content": "", "reasoning_content": "answer"})
    assert text == "answer"
    assert source == "reasoning_content"


def test_anonymous_development_summary_does_not_expose_ids_or_paths():
    cohort = {
        "splits": {
            "development": [
                {"task_id": "secret-id", "run_dir": "/secret/path", "size_band": "small", "mapped_cells": 123, "platform": "sky130hd"}
            ]
        }
    }
    summary = MODULE.anonymous_development_summary(cohort)
    encoded = json.dumps(summary)
    assert "secret-id" not in encoded
    assert "/secret/path" not in encoded
    assert summary[0]["case"] == "dev-01"


def test_feedback_returns_bounded_actionable_diagnostics_without_task_id(tmp_path):
    task_id = "secret-task-id"
    case_root = tmp_path / "methods" / "method" / task_id
    (case_root / "logs").mkdir(parents=True)
    (case_root / "score.json").write_text(
        json.dumps(
            {
                "strict_pass": False,
                "validation_status": "PASS",
                "statistics_status": "MISSING",
                "contract_checks_passed": 0,
                "contract_checks_total": 0,
                "structural_issue_count": 0,
                "conversion_wall_seconds": 1.25,
                "output_bytes": 100,
            }
        ),
        encoding="utf-8",
    )
    (case_root / "run_state.json").write_text(json.dumps({"status": "completed"}), encoding="utf-8")
    (case_root / "logs" / "independent_validate.log").write_text(
        f"FileNotFoundError: [Errno 2] No such file: '/tmp/{task_id}/generated/stages/floorplan/features/metadata.csv'\n",
        encoding="utf-8",
    )
    (case_root / "logs" / "structural_summary.log").write_text(
        "AttributeError: 'HeteroData' has no attribute 'global_feature_schema'\n",
        encoding="utf-8",
    )
    cohort = {"splits": {"development": [{"task_id": task_id}]}}
    feedback = MODULE.feedback_for(tmp_path, "method", cohort, max_signatures=2, max_chars=80)
    encoded = json.dumps(feedback)
    assert task_id not in encoded
    assert feedback["aggregate"]["strict_passes"] == 0
    assert feedback["cases"][0]["error_signatures"] == [
        {
            "source": "independent_validate",
            "category": "MISSING_OUTPUT",
            "detail": "stages/floorplan/features/metadata.csv",
        },
        {
            "source": "structural_summary",
            "category": "MISSING_ATTRIBUTE",
            "detail": "global_feature_schema",
        },
    ]


def test_feedback_rank_prefers_strict_then_static_progress_then_earlier_round():
    weak = {"aggregate": {"strict_passes": 1, "static_contract_checks_passed": 20}}
    strong = {"aggregate": {"strict_passes": 2, "contract_checks_passed": 0}}
    assert MODULE.feedback_rank(strong, 2) > MODULE.feedback_rank(weak, 0)
    partial = {"aggregate": {"strict_passes": 0, "static_contract_checks_passed": 4}}
    empty = {"aggregate": {"strict_passes": 0, "static_contract_checks_passed": 3}}
    assert MODULE.feedback_rank(partial, 2) > MODULE.feedback_rank(empty, 0)
    tie = {"aggregate": {"strict_passes": 2, "static_contract_checks_passed": 3}}
    assert MODULE.feedback_rank(tie, 1) > MODULE.feedback_rank(tie, 2)


def test_development_budget_exhaustion_freezes_instead_of_requesting_again():
    assert MODULE.development_budget_stop_reason(3999, 4096, 5, 6) == "token_budget_exhausted"
    assert MODULE.development_budget_stop_reason(8192, 4096, 6, 6) == "api_call_budget_exhausted"
    assert MODULE.development_budget_stop_reason(8192, 4096, 5, 6) is None
