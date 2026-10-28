"""Full population accounting must not shrink when execution is partial."""
import csv
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "repair_population", Path(__file__).resolve().parents[2] / "experiments/summarize_repair_population.py")
summary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(summary)


def population(tmp_path):
    for platform, filename in summary.MANIFESTS:
        cases = [{"id": platform + "_C00001", "platform": platform}]
        if platform == "sky130hd":
            cases[0]["local_208_ready"] = False
        (tmp_path / filename).write_text(json.dumps({"candidates": cases}))
    return tmp_path


def write_costs(root, rows):
    out = root / "nangate45" / "costs.csv"
    out.parent.mkdir(exist_ok=True)
    with out.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_missing_measurements_preserve_population_and_unknown_cost(tmp_path):
    result = summary.render(population(tmp_path))
    assert result["population"] == 2
    assert result["terminated"] == 0
    assert result["all_terminated"] is False
    assert result["statuses"] == {"not_submitted": 1, "input_blocked": 1}
    assert result["tokens_known_reported"] is None
    assert result["orfs_wrapper_calls_observed"] is None


def test_completed_api_deduplicates_but_blocked_case_is_unfinished(tmp_path):
    root = population(tmp_path)
    row = {"case_id": "nangate45_C00001", "platform": "nangate45", "runner_status": "complete",
           "session_id": "one", "tokens_known_reported": "100", "orfs_wrapper_calls": "3"}
    write_costs(root, [row, row])
    result = summary.render(root)
    assert result["population"] == 2
    assert result["terminated"] == 1
    assert result["tokens_known_reported"] == 100
    assert result["orfs_wrapper_calls_observed"] == 3
    assert result["all_terminated"] is False


def test_same_cid_on_different_platform_is_distinct(tmp_path):
    rows, _ = summary.collect(population(tmp_path))
    assert len(rows) == 2
    assert {r["platform"] for r in rows} == {"nangate45", "sky130hd"}


def test_conflicting_measurements_fail_without_publishing(tmp_path):
    root = population(tmp_path)
    row = {"case_id": "nangate45_C00001", "platform": "nangate45", "session_id": "one"}
    write_costs(root, [row, {**row, "session_id": "two"}])
    with pytest.raises(ValueError, match="Conflicting duplicate"):
        summary.render(root)
    assert not (root / "STATUS.md").exists()


def test_eda_done_does_not_end_api_accounting(tmp_path):
    root = population(tmp_path)
    row = {"case_id": "nangate45_C00001", "platform": "nangate45", "runner_status": "polling",
           "session_id": "one", "job_status": "done", "dual_clean": "True"}
    write_costs(root, [row])
    assert summary.render(root)["terminated"] == 0


def test_later_cohort_overrides_input_blocker_with_real_api_evidence(tmp_path):
    root = population(tmp_path)
    folder = root / "sky130hd_remaining"
    folder.mkdir()
    (folder / "costs.csv").write_text("case_id,platform,session_id,runner_status\nsky130hd_C00001,sky130hd,two,complete\n")
    rows, _ = summary.collect(root)
    sky = next(r for r in rows if r["platform"] == "sky130hd")
    assert sky["terminal"] == "true"
    assert sky["input_blocker"] == ""
    assert sky["measurement_source"] == "sky130hd_remaining/costs.csv"


def test_extra_cohort_can_be_repeated_without_counting_twice(tmp_path):
    root = population(tmp_path)
    folder = root / "additional"
    folder.mkdir()
    (folder / "costs.csv").write_text("case_id,platform,session_id,runner_status\nsky130hd_C00001,sky130hd,two,complete\n")
    result = summary.render(root, [Path("additional"), folder])
    assert result["population"] == 2
    assert result["terminated"] == 1


def test_platform_token_costs_never_mix_same_design_ids(tmp_path):
    root = population(tmp_path)
    row = {"case_id": "nangate45_C00001", "platform": "nangate45", "session_id": "n45",
           "runner_status": "complete", "tokens_known_reported": "100", "input_tokens_known": "80"}
    write_costs(root, [row])
    sky = root / 'sky130hd'
    sky.mkdir()
    (sky / 'costs.csv').write_text('case_id,platform,session_id,runner_status,tokens_known_reported,input_tokens_known\n'
                                 'sky130hd_C00001,sky130hd,sky,complete,700,600\n')
    report = summary.render(root)
    assert report['platform_usage']['nangate45']['tokens_known_reported'] == 100
    assert report['platform_usage']['sky130hd']['tokens_known_reported'] == 700
    assert report['platform_usage']['nangate45']['input_tokens_known'] == 80
    assert report['platform_usage']['sky130hd']['input_tokens_known'] == 600
    assert report['tokens_known_reported'] == 800
