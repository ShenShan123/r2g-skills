import importlib.util
import json
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO / "experiments/experiment4_protocol.py"
SPEC = importlib.util.spec_from_file_location("experiment4_protocol", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def make_pool(tmp_path: Path) -> tuple[Path, Path]:
    projects = tmp_path / "projects"
    tasks = []
    ranges = ((100, 499), (500, 1999), (2000, 10000))
    for band_index, (minimum, maximum) in enumerate(ranges):
        for index in range(14):
            task_id = f"task_{band_index}_{index:02d}"
            project = projects / task_id / "baseline"
            run_dir = project / "backend" / "RUN_test"
            for name in MODULE.REQUIRED_RESULTS:
                target = run_dir / "results" / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(name, encoding="utf-8")
            cells = minimum + ((maximum - minimum) * index // 13)
            write_json(
                project / "repair_family_probe_result.json",
                {"task_id": task_id, "strict_clean": True, "mapped_cells": cells, "run_dir": str(run_dir)},
            )
            tasks.append(
                {
                    "task_id": task_id,
                    "repo_url": f"https://example.test/repo-{band_index}-{index}",
                    "commit": f"commit-{index}",
                    "top_module": f"top_{index}",
                }
            )
    plan = tmp_path / "plan.json"
    write_json(plan, {"tasks": tasks})
    return projects, plan


def test_build_cohort_is_deterministic_balanced_and_group_disjoint(tmp_path):
    projects, plan = make_pool(tmp_path)
    first = MODULE.build_cohort(projects, plan, "fixed-seed")
    second = MODULE.build_cohort(projects, plan, "fixed-seed")

    first["created_at"] = "ignored"
    second["created_at"] = "ignored"
    assert first == second
    assert len(first["splits"]["canary"]) == 1
    assert len(first["splits"]["development"]) == 6
    assert len(first["splits"]["hidden_test"]) == 24
    groups = [row["source_group"] for rows in first["splits"].values() for row in rows]
    assert len(groups) == len(set(groups))
    MODULE.validate_cohort(first, check_files=True)


def test_validate_rejects_changed_baseline_evidence(tmp_path):
    projects, plan = make_pool(tmp_path)
    cohort = MODULE.build_cohort(projects, plan, "fixed-seed")
    selected = cohort["splits"]["canary"][0]
    Path(selected["baseline_result"]).write_text("{}", encoding="utf-8")
    try:
        MODULE.validate_cohort(cohort, check_files=True)
    except ValueError as exc:
        assert "baseline evidence changed" in str(exc)
    else:
        raise AssertionError("changed baseline evidence was accepted")
