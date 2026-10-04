from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import sqlite3
import stat
import signal
import subprocess

import pytest


REPO = Path(__file__).resolve().parents[2]

from experiments.experiment2_signoff import (
    DEFAULT_COHORT,
    Experiment2Error,
    evaluate_checkpoint,
    fixture_by_id,
    lint_cohort,
    list_bounded_project_files,
    load_cohort,
    materialize_project,
    method_runtime_state,
    protected_input_failures,
    public_fixture_id,
    read_bounded_project_file,
    read_json,
    require_fixed_target_frequency,
    resource_intervention,
    set_target_frequency,
    set_bounded_core_utilization,
    set_bounded_explicit_area,
    set_bounded_string_knob,
    sha256_file,
    signoff_environment,
    verify_campaign_bindings,
    write_json_atomic,
)
from experiments.run_experiment2_signoff import (
    copy_writable_seed,
    full_r2g_environment,
    install_full_r2g_policy_guards,
    result_context,
    runtime_skills,
)
from experiments import run_experiment2_batch
from experiments.run_experiment2_vanilla_method import (
    VanillaSignoffRun,
    chat_message_from_responses,
    responses_input,
    responses_payload,
)
from experiments.run_experiment2_retrospective_repair_screen import (
    baseline_rejection,
    classify as classify_retrospective_screen,
)
def test_frozen_pilot_cohort_is_balanced_and_unique():
    cohort = load_cohort(DEFAULT_COHORT)
    assert lint_cohort(cohort) == []
    assert [item["size_bin"] for item in cohort["fixtures"]].count("small") == 2
    assert [item["size_bin"] for item in cohort["fixtures"]].count("medium") == 3
    assert [item["size_bin"] for item in cohort["fixtures"]].count("large") == 3
    assert len({item["candidate"]["repo_url"] for item in cohort["fixtures"]}) == 8


def _cohort_evidence_is_local(path: Path) -> bool:
    """Do the admission-evidence files this cohort binds by absolute path still exist?

    A cohort binds each fixture's baseline runs, family evidence and clean
    witness by ABSOLUTE path plus sha256, and three of these cohorts point into
    /home/yangao/r2g_exp2_fixed100_expanded_screen_2026_08_09_v22b and
    .../r2g_exp2_retrospective_repair_screen_2026_08_09_v18c, which were
    reclaimed in the 2026-10-04 disk cleanup. lint_cohort() is therefore right
    to report them missing: the binding is unverifiable on this host rather
    than broken. The assertions that depend on it skip, the rest still run --
    the same convention as the def-graph suite skipping without torch and
    rtl-acquire skipping without its corpus.
    """
    cohort = json.loads(path.read_text())
    base = path.resolve().parent
    for fixture in cohort.get("fixtures") or []:
        evidence = fixture.get("admission_evidence") or {}
        records = list(evidence.get("baseline_runs") or [])
        for key in ("family_evidence", "witness"):
            item = evidence.get(key)
            if isinstance(item, dict):
                records.append(item)
        for record in records:
            raw = str(record.get("path") or "")
            if raw and not (base / raw).resolve().is_file():
                return False
    return True


def _read_cohort(path: Path) -> dict:
    """load_cohort(), tolerating evidence that this host no longer stores."""
    if _cohort_evidence_is_local(path):
        return load_cohort(path)
    return json.loads(path.read_text())


def _assert_evidence_bound(cohort: dict, path: Path) -> None:
    """Assert the cohort's evidence binding, or skip where the evidence is gone."""
    if not _cohort_evidence_is_local(path):
        pytest.skip(f"admission evidence for {path.name} is not on this host")
    assert lint_cohort(cohort, base_dir=path.parent) == []


def test_v2_development_cohort_has_blinded_ids_and_tracks():
    path = REPO / "docs" / "experiments" / "signoff" / "experiment2_v2_development_cohort_2026_08_11.json"
    cohort = _read_cohort(path)
    assert len({public_fixture_id(item) for item in cohort["fixtures"]}) == 8
    assert all(public_fixture_id(item).startswith("task_") for item in cohort["fixtures"])
    assert {item["objective_track"] for item in cohort["fixtures"]} == {
        "fixed_goal",
        "resource_tradeoff",
        "clean_sentinel",
    }
    _assert_evidence_bound(cohort, path)


def test_v2_materialized_manifest_exposes_only_public_id(tmp_path: Path):
    fixture = copy.deepcopy(fixture_by_id(load_cohort(DEFAULT_COHORT), "ben_marshall_uart_tx"))
    fixture["public_id"] = "task_123"
    source = tmp_path / "source"
    rtl = source / "rtl" / "uart_tx.v"
    rtl.parent.mkdir(parents=True)
    rtl.write_text("module uart_tx(input clk, output tx); assign tx=clk; endmodule\n")
    project = tmp_path / "project"
    materialize_project(source, project, fixture)
    manifest = read_json(project / "experiment2_input_manifest.json")
    assert manifest["fixture_id"] == "task_123"
    assert fixture["id"] not in json.dumps(manifest)


def test_v2_mutation_requires_recorded_baseline(tmp_path: Path):
    runner = VanillaSignoffRun.__new__(VanillaSignoffRun)
    runner.baseline_first = True
    runner.baseline_path = tmp_path / "baseline_observation.json"
    with pytest.raises(Experiment2Error, match="baseline-first protocol"):
        runner.require_baseline_before_mutation()
    write_json_atomic(
        runner.baseline_path,
        {"status": "observed", "validation_sha256": "a" * 64, "role_consistent": True},
    )
    runner.require_baseline_before_mutation()


def test_v2_reports_resource_intervention(clean_project):
    project, fixture = clean_project
    fixture["objective_track"] = "resource_tradeoff"
    fixture["footprint_policy"] = {
        "mode": "bounded_core_utilization",
        "initial": 25,
        "minimum": 8,
        "maximum": 25,
    }
    set_bounded_core_utilization(project, fixture, 10)
    result = resource_intervention(project, fixture)
    assert result["objective_track"] == "resource_tradeoff"
    assert result["final_core_utilization_pct"] == 10
    assert result["estimated_core_area_multiplier"] == pytest.approx(2.5)


def test_v2_full_r2g_flow_entries_are_policy_guarded(tmp_path: Path):
    skills = tmp_path / "r2g-skills"
    flow = skills / "signoff-loop" / "scripts" / "flow"
    flow.mkdir(parents=True)
    for name in ("run_orfs.sh", "resume_orfs.sh"):
        path = flow / name
        path.write_text("#!/usr/bin/env bash\nexit 0\n")
        path.chmod(0o755)
    install_full_r2g_policy_guards(skills)
    for name in ("run_orfs.sh", "resume_orfs.sh"):
        wrapper = flow / name
        real = flow / f".experiment2_{name[:-3]}_real.sh"
        assert real.is_file()
        assert "policy-check" in wrapper.read_text()
        assert "--record-flow-attempt" in wrapper.read_text()
        assert ': "${R2G_EXP2_CAMPAIGN_ROOT:' in wrapper.read_text()
        assert wrapper.stat().st_mode & stat.S_IXUSR


def test_v2_full_r2g_guard_accepts_runtime_without_removed_resume_entry(tmp_path: Path):
    skills = tmp_path / "r2g-skills"
    flow = skills / "signoff-loop" / "scripts" / "flow"
    flow.mkdir(parents=True)
    entry = flow / "run_orfs.sh"
    entry.write_text("#!/usr/bin/env bash\nexit 0\n")
    entry.chmod(0o755)
    install_full_r2g_policy_guards(skills)
    assert "policy-check" in entry.read_text()
    assert (flow / ".experiment2_run_orfs_real.sh").is_file()
    env = {
        **os.environ,
        "PYTHON_BIN": "true",
        "R2G_EXP2_CAMPAIGN_ROOT": str(tmp_path),
        "R2G_EXP2_FIXTURE_ID": "fixture",
        "R2G_EXP2_CONTROLLER": str(tmp_path / "controller.py"),
    }
    assert subprocess.run(["bash", str(entry)], env=env, check=False).returncode == 0


def test_benchmark_challenge_screen_allows_one_pinned_repository():
    cohort = copy.deepcopy(load_cohort(DEFAULT_COHORT))
    cohort["cohort_kind"] = "benchmark_challenge_screen"
    for fixture in cohort["fixtures"]:
        fixture["candidate"]["repo_url"] = "https://example.test/frozen-benchmark"
    cohort["repeatability_fixture_ids"] = [item["id"] for item in cohort["fixtures"]]
    assert lint_cohort(cohort) == []


def test_standard_cohort_still_rejects_duplicate_repositories():
    cohort = copy.deepcopy(load_cohort(DEFAULT_COHORT))
    duplicate = cohort["fixtures"][0]["candidate"]["repo_url"]
    cohort["fixtures"][1]["candidate"]["repo_url"] = duplicate
    assert "Pilot fixtures must come from distinct repositories" in lint_cohort(cohort)


@pytest.fixture()
def clean_project(tmp_path: Path):
    fixture = copy.deepcopy(fixture_by_id(load_cohort(DEFAULT_COHORT), "ben_marshall_uart_tx"))
    source = tmp_path / "source"
    source_file = source / "rtl" / "uart_tx.v"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("module uart_tx(input clk, output tx); assign tx=clk; endmodule\n")
    project = tmp_path / "project"
    materialize_project(source, project, fixture)
    set_target_frequency(project, fixture, 100.0)
    run_dir = project / "backend" / "RUN_clean"
    (run_dir / "final").mkdir(parents=True)
    for name in ("6_final.def", "6_final.gds", "6_final.odb", "6_final.v", "6_final.spef"):
        (run_dir / "final" / name).write_text(name + "\n")
    reports = project / "reports"
    write_json_atomic(reports / "drc.json", {"status": "clean", "total_violations": 0})
    write_json_atomic(reports / "lvs.json", {"status": "clean", "mismatch_count": 0})
    write_json_atomic(reports / "route.json", {"status": "clean", "total_violations": 0})
    write_json_atomic(reports / "rcx.json", {"status": "complete"})
    write_json_atomic(
        reports / "timing_check.json",
        {"tier": "clean", "wns": 0.1, "tns": 0, "hold_wns": 0.05, "hold_tns": 0},
    )
    write_json_atomic(
        reports / "signoff_gate.json",
        {
            "status": "pass",
            "checks": {
                "orfs": {"status": "complete"},
                "antenna": {"status": "clean", "violations": 0},
                "binding": {"status": "bound"},
                "report_binding": {"status": "bound"},
                "artifact_digest": {"status": "bound"},
                "task_provenance": {"status": "bound"},
            },
        },
    )
    return project, fixture


def test_true_clean_checkpoint_passes(clean_project):
    project, fixture = clean_project
    result = evaluate_checkpoint(project, fixture)
    assert result["strict_clean"] is True
    assert all(item["status"] == "pass" for item in result["gates"].values())


@pytest.mark.parametrize(
    ("mutation", "expected_gate"),
    [
        ("source", "protected_inputs"),
        ("footprint", "protected_inputs"),
        ("wrong_clock", "protected_inputs"),
        ("false_path", "protected_inputs"),
        ("drc", "drc"),
        ("lvs", "lvs"),
        ("route", "route"),
        ("setup", "timing"),
        ("hold", "timing"),
        ("antenna", "antenna"),
        ("rcx", "rcx"),
        ("foreign_report", "provenance"),
        ("artifact_mismatch", "provenance"),
        ("missing_report", "drc"),
        ("nan_metric", "drc"),
    ],
)
def test_adversarial_checkpoint_fails_closed(clean_project, mutation, expected_gate):
    project, fixture = clean_project
    reports = project / "reports"
    if mutation == "source":
        (project / "rtl" / "rtl" / "uart_tx.v").write_text("mutated\n")
    elif mutation == "footprint":
        config = project / "constraints" / "config.mk"
        config.write_text(config.read_text().replace("0 0 90 90", "0 0 900 900"))
    elif mutation == "wrong_clock":
        sdc = project / "constraints" / "constraint.sdc"
        sdc.write_text(sdc.read_text().replace("set clk_port_name clk", "set clk_port_name fake_clk"))
    elif mutation == "false_path":
        with (project / "constraints" / "constraint.sdc").open("a") as stream:
            stream.write("set_false_path -from [all_inputs] -to [all_outputs]\n")
    elif mutation == "drc":
        write_json_atomic(reports / "drc.json", {"status": "violations", "total_violations": 1})
    elif mutation == "lvs":
        write_json_atomic(reports / "lvs.json", {"status": "fail", "mismatch_count": 1})
    elif mutation == "route":
        write_json_atomic(reports / "route.json", {"status": "dirty", "total_violations": 1})
    elif mutation == "setup":
        timing = read_json(reports / "timing_check.json")
        timing.update({"tier": "minor", "wns": -0.01, "tns": -0.01})
        write_json_atomic(reports / "timing_check.json", timing)
    elif mutation == "hold":
        timing = read_json(reports / "timing_check.json")
        timing.update({"hold_wns": -0.01, "hold_tns": -0.01})
        write_json_atomic(reports / "timing_check.json", timing)
    elif mutation == "antenna":
        gate = read_json(reports / "signoff_gate.json")
        gate["checks"]["antenna"] = {"status": "dirty", "violations": 1}
        write_json_atomic(reports / "signoff_gate.json", gate)
    elif mutation == "rcx":
        write_json_atomic(reports / "rcx.json", {"status": "missing"})
    elif mutation == "foreign_report":
        gate = read_json(reports / "signoff_gate.json")
        gate["checks"]["report_binding"] = {"status": "foreign"}
        gate["status"] = "dirty"
        write_json_atomic(reports / "signoff_gate.json", gate)
    elif mutation == "artifact_mismatch":
        gate = read_json(reports / "signoff_gate.json")
        gate["checks"]["artifact_digest"] = {"status": "mismatch"}
        gate["status"] = "dirty"
        write_json_atomic(reports / "signoff_gate.json", gate)
    elif mutation == "missing_report":
        (reports / "drc.json").unlink()
    elif mutation == "nan_metric":
        write_json_atomic(reports / "drc.json", {"status": "clean", "total_violations": float("nan")})
    result = evaluate_checkpoint(project, fixture)
    assert result["strict_clean"] is False
    assert result["gates"][expected_gate]["status"] == "fail"


def test_protected_input_checker_names_multiple_violations(clean_project):
    project, fixture = clean_project
    (project / "rtl" / "rtl" / "uart_tx.v").write_text("changed\n")
    sdc = project / "constraints" / "constraint.sdc"
    sdc.write_text(sdc.read_text() + "set_multicycle_path 2 -from [all_inputs]\n")
    failures = protected_input_failures(project, fixture)
    assert any("source byte mismatch" in item for item in failures)
    assert any("forbidden timing exception" in item for item in failures)


def test_bounded_core_utilization_is_editable_only_inside_frozen_bounds(tmp_path: Path):
    fixture = copy.deepcopy(fixture_by_id(load_cohort(DEFAULT_COHORT), "ben_marshall_uart_tx"))
    fixture["footprint_policy"] = {
        "mode": "bounded_core_utilization",
        "initial": 25,
        "minimum": 8,
        "maximum": 25,
    }
    fixture.pop("die_area", None)
    fixture.pop("core_area", None)
    source = tmp_path / "source"
    source_file = source / "rtl" / "uart_tx.v"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("module uart_tx(input clk, output tx); assign tx=clk; endmodule\n")
    project = tmp_path / "project"
    materialize_project(source, project, fixture)
    config = project / "constraints" / "config.mk"
    text = config.read_text()
    assert "export CORE_UTILIZATION = 25" in text
    assert "DIE_AREA" not in text
    assert protected_input_failures(project, fixture) == []

    result = set_bounded_core_utilization(project, fixture, 12)
    assert result["core_utilization"] == 12
    assert "export CORE_UTILIZATION = 12" in config.read_text()
    with pytest.raises(Experiment2Error, match="within"):
        set_bounded_core_utilization(project, fixture, 7)

    config.write_text(text.replace("CORE_UTILIZATION = 25", "CORE_UTILIZATION = 12"))
    assert protected_input_failures(project, fixture) == []

    config.write_text(text.replace("CORE_UTILIZATION = 25", "CORE_UTILIZATION = 7"))
    assert any("outside frozen bounds" in item for item in protected_input_failures(project, fixture))

    config.write_text(text + "export DIE_AREA = 0 0 500 500\n")
    assert any("explicit DIE_AREA" in item for item in protected_input_failures(project, fixture))


def test_retrospective_screen_separates_clean_synth_and_repair_needed():
    base = {
        "environment_failure": False,
        "failed_gates": [],
        "strict_clean": False,
        "flow_failure_stage": None,
        "normalized_physical_signature": ["drc"],
    }
    clean = {**base, "strict_clean": True, "normalized_physical_signature": []}
    synth = {**base, "flow_failure_stage": "synth", "normalized_physical_signature": []}
    feasible = {**clean}
    assert baseline_rejection(clean) == "rejected_baseline_failure_not_reproduced"
    assert baseline_rejection(synth) == "rejected_frontend_or_synthesis_failure"
    assert baseline_rejection(base, dict(base)) is None
    assert classify_retrospective_screen(base, dict(base), feasible) == "admitted_repair_needed"


def test_bounded_project_listing_finds_real_files_and_filters(clean_project):
    project, _ = clean_project
    result = list_bounded_project_files(
        project,
        {"prefix": "backend", "contains": "6_final", "max_results": 3},
    )
    assert result["matched_count"] == 5
    assert result["truncated"] is True
    assert len(result["files"]) == 3
    assert all(item["path"].startswith("backend/RUN_clean/final/6_final") for item in result["files"])


def test_bounded_project_listing_rejects_escape_and_skips_external_symlink(clean_project, tmp_path):
    project, _ = clean_project
    with pytest.raises(Experiment2Error, match="project-relative"):
        list_bounded_project_files(project, {"prefix": "../outside"})
    outside = tmp_path / "secret.txt"
    outside.write_text("secret\n")
    link = project / "backend" / "outside-link"
    link.symlink_to(outside)
    result = list_bounded_project_files(project, {"prefix": "backend"})
    assert not any(item["path"].endswith("outside-link") for item in result["files"])




def test_signoff_environment_is_isolated_per_method_fixture(clean_project, tmp_path):
    project, _ = clean_project
    runtime = tmp_path / "runtime-skills"
    campaign = tmp_path / "campaign"
    env = signoff_environment(runtime, campaign, project)
    expected = method_runtime_state(project)
    assert env["R2G_KNOWLEDGE_DB"] == str(expected / "knowledge.sqlite")
    assert env["R2G_JOURNAL_DB"] == str(expected / "journal.sqlite")
    assert env["R2G_HEURISTICS_PATH"] == str(expected / "heuristics.json")
    assert env["NUM_CORES"] == "4"
    assert "ORFS_MAX_CPUS" not in env
    assert campaign / "runtime" / "knowledge.sqlite" != Path(env["R2G_KNOWLEDGE_DB"])


def test_frozen_sqlite_seed_is_copied_to_a_writable_private_database(tmp_path):
    frozen = tmp_path / "frozen" / "knowledge.sqlite"
    frozen.parent.mkdir()
    with sqlite3.connect(frozen) as connection:
        connection.execute("CREATE TABLE evidence (value TEXT)")
    frozen.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)

    working = tmp_path / "method" / "runtime_state" / "knowledge.sqlite"
    copy_writable_seed(frozen, working)

    assert frozen.stat().st_mode & stat.S_IWUSR == 0
    assert working.stat().st_mode & stat.S_IWUSR
    with sqlite3.connect(working) as connection:
        connection.execute("INSERT INTO evidence VALUES ('learned')")
        connection.commit()
        assert connection.execute("SELECT value FROM evidence").fetchone() == ("learned",)


def test_experiment2_fixed_target_is_enforced():
    assert require_fixed_target_frequency(100.0) == 100.0
    for value in (99.0, 200.0, float("nan")):
        with pytest.raises(Experiment2Error, match="frozen at 100 MHz"):
            require_fixed_target_frequency(value)


def test_family_contract_smoke_cohort_is_evidence_bound():
    path = REPO / "docs/experiments/signoff/experiment2_family_contract_smoke_cohort_2026_08_11.json"
    cohort = json.loads(path.read_text())
    assert [item["role"] for item in cohort["fixtures"]].count("repair_needed") == 2
    assert [item["role"] for item in cohort["fixtures"]].count("clean_sentinel") == 2
    _assert_evidence_bound(cohort, path)


def test_family_balanced_parallel_cohort_is_evidence_bound_and_disjoint():
    path = REPO / "docs/experiments/signoff/experiment2_family_balanced_parallel_pilot_2026_08_11.json"
    cohort = json.loads(path.read_text())
    assert len({item["candidate"]["repo_url"] for item in cohort["fixtures"]}) == 8
    assert [item["role"] for item in cohort["fixtures"]].count("repair_needed") == 6
    _assert_evidence_bound(cohort, path)


def test_pin_fixture_allows_only_bounded_explicit_area(tmp_path):
    path = REPO / "docs/experiments/signoff/experiment2_family_balanced_parallel_pilot_2026_08_11.json"
    fixture = fixture_by_id(_read_cohort(path), "axi_interconnect_pin_repair")
    source = tmp_path / "source"
    for relative in fixture["candidate"]["rtl_files"]:
        rtl = source / relative
        rtl.parent.mkdir(parents=True, exist_ok=True)
        rtl.write_text("module placeholder; endmodule\n")
    project = tmp_path / "project"
    materialize_project(source, project, fixture)

    result = set_bounded_explicit_area(project, fixture, 703, 703)

    assert result["die_perimeter_um"] == 2812
    assert "CORE_UTILIZATION" not in (project / "constraints/config.mk").read_text()
    assert protected_input_failures(project, fixture) == []
    with pytest.raises(Experiment2Error, match="perimeter"):
        set_bounded_explicit_area(project, fixture, 100, 100)


def test_rule_specific_string_action_is_exactly_allowlisted(tmp_path):
    path = REPO / "docs/experiments/signoff/experiment2_family_balanced_parallel_pilot_2026_08_11.json"
    fixture = fixture_by_id(_read_cohort(path), "siliconcompiler_gcd_drc_repair")
    source = tmp_path / "source"
    rtl = source / fixture["candidate"]["rtl_files"][0]
    rtl.parent.mkdir(parents=True)
    rtl.write_text("module gcd(input clk); endmodule\n")
    project = tmp_path / "project"
    materialize_project(source, project, fixture)

    set_bounded_string_knob(project, fixture, "PLACE_PINS_ARGS", "-exclude right:*")

    assert protected_input_failures(project, fixture) == []
    with pytest.raises(Experiment2Error, match="outside"):
        set_bounded_string_knob(project, fixture, "PLACE_PINS_ARGS", "-exclude right:*; rm -rf /")


def test_grade_context_preserves_failed_fixture_role_and_resource_usage(tmp_path):
    method_dir = tmp_path / "methods" / "deepseek-vanilla" / "repair_fixture"
    method_dir.mkdir(parents=True)
    write_json_atomic(
        method_dir / "vanilla_submission.json",
        {"resource_usage": {"total_tokens": 123, "wall_time_seconds": 4.5}},
    )
    fixture = {
        "id": "repair_fixture",
        "role": "repair_needed",
        "family_id": "footprint_congestion",
        "strict_clean_scope": "fixed_target_physical_signoff",
    }

    context = result_context(fixture, method_dir)

    assert context["role"] == "repair_needed"
    assert context["family_id"] == "footprint_congestion"
    assert context["resource_usage"]["total_tokens"] == 123


def test_batch_requires_bound_parallel_limit_and_cpu_affinity():
    source = (REPO / "experiments/run_experiment2_batch.py").read_text()
    assert 'parser.add_argument("--max-workers", type=int)' in source
    assert "parallel Experiment 2 execution requires --isolated-cpu-affinity" in source
    assert 'command = ["taskset", "-c", cpu_affinity, *command]' in source


def test_family_contract_smoke_rejects_changed_evidence(tmp_path):
    path = REPO / "docs/experiments/signoff/experiment2_family_contract_smoke_cohort_2026_08_11.json"
    cohort = json.loads(path.read_text())
    bad = tmp_path / "changed.json"
    bad.write_text('{"strict_clean": false}\n')
    cohort["fixtures"][0]["admission_evidence"]["baseline_runs"][0]["path"] = str(bad)
    errors = lint_cohort(cohort, base_dir=path.parent)
    assert any("evidence is missing or changed" in item for item in errors)


def test_experiment_fixture_is_not_stamped_as_rtl_acquire_promotion(tmp_path):
    fixture = copy.deepcopy(fixture_by_id(load_cohort(DEFAULT_COHORT), "ben_marshall_uart_tx"))
    source = tmp_path / "source"
    rtl = source / "rtl" / "uart_tx.v"
    rtl.parent.mkdir(parents=True)
    rtl.write_text("module uart_tx(input clk, output tx); assign tx=clk; endmodule\n")
    project = tmp_path / "project"
    materialize_project(source, project, fixture)
    metadata = read_json(project / "metadata.json")
    assert "promoted_from" not in metadata
    assert metadata["source_kind"] == "frozen_experiment_fixture"
    assert metadata["experiment2_strict_clean_scope"] == "fixed_target_physical_signoff"


def test_fixed_target_fixture_accepts_not_applicable_acquisition_provenance(clean_project):
    project, fixture = clean_project
    fixture["strict_clean_scope"] = "fixed_target_physical_signoff"
    gate = read_json(project / "reports" / "signoff_gate.json")
    gate["checks"]["task_provenance"] = {"status": "not_applicable"}
    write_json_atomic(project / "reports" / "signoff_gate.json", gate)
    assert evaluate_checkpoint(project, fixture)["strict_clean"] is True


def test_non_experiment_fixture_rejects_not_applicable_acquisition_provenance(clean_project):
    project, fixture = clean_project
    gate = read_json(project / "reports" / "signoff_gate.json")
    gate["checks"]["task_provenance"] = {"status": "not_applicable"}
    write_json_atomic(project / "reports" / "signoff_gate.json", gate)
    assert evaluate_checkpoint(project, fixture)["strict_clean"] is False


def test_changed_target_period_fails_protected_input_gate(clean_project):
    project, fixture = clean_project
    sdc = project / "constraints" / "constraint.sdc"
    sdc.write_text(sdc.read_text().replace("set clk_period 10", "set clk_period 9"))
    result = evaluate_checkpoint(project, fixture)
    assert result["strict_clean"] is False
    assert result["gates"]["protected_inputs"]["status"] == "fail"
    assert any("target period changed" in item for item in result["failures"])


def test_batch_shutdown_terminates_all_registered_method_groups(monkeypatch):
    calls = []
    monkeypatch.setattr(run_experiment2_batch.os, "killpg", lambda pgid, sig: calls.append((pgid, sig)))
    monkeypatch.setattr(run_experiment2_batch.time, "sleep", lambda _seconds: None)
    run_experiment2_batch._ACTIVE_METHOD_GROUPS.clear()
    run_experiment2_batch.register_method_group(101)
    run_experiment2_batch.register_method_group(202)

    run_experiment2_batch.terminate_active_method_groups()

    assert set(calls) == {
        (101, signal.SIGTERM),
        (202, signal.SIGTERM),
        (101, signal.SIGKILL),
        (202, signal.SIGKILL),
    }
    run_experiment2_batch._ACTIVE_METHOD_GROUPS.clear()


def test_responses_history_preserves_function_call_round_trip():
    instructions, items = responses_input(
        [
            {"role": "system", "content": "system rules"},
            {"role": "user", "content": "inspect"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "inspect_state", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": '{"ok":true}'},
        ]
    )
    assert instructions == "system rules"
    assert items == [
        {"role": "user", "content": "inspect"},
        {"type": "function_call", "call_id": "call_1", "name": "inspect_state", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "call_1", "output": '{"ok":true}'},
    ]


def test_responses_payload_uses_flat_function_tools():
    payload = responses_payload(
        {"model_id": "gpt-test", "tool_choice": "auto"},
        [{"role": "system", "content": "rules"}, {"role": "user", "content": "work"}],
        128,
    )
    assert payload["instructions"] == "rules"
    assert payload["max_output_tokens"] == 128
    assert payload["tools"]
    assert "name" in payload["tools"][0]
    assert "function" not in payload["tools"][0]


def test_responses_function_call_is_converted_to_chat_tool_call():
    message = chat_message_from_responses(
        {
            "status": "completed",
            "output": [
                {
                    "type": "function_call",
                    "call_id": "call_2",
                    "name": "inspect_state",
                    "arguments": '{}',
                }
            ],
        }
    )
    assert message["tool_calls"][0]["id"] == "call_2"
    assert message["tool_calls"][0]["function"]["name"] == "inspect_state"


def test_incomplete_responses_result_fails_closed():
    with pytest.raises(Experiment2Error, match="did not complete"):
        chat_message_from_responses(
            {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}}
        )


def test_vanilla_can_read_frozen_rtl_by_project_relative_path(clean_project):
    project, _ = clean_project
    result = read_bounded_project_file(project, {"path": "rtl/rtl/uart_tx.v"})
    assert result["path"] == "rtl/rtl/uart_tx.v"
    assert any("module uart_tx" in line for line in result["lines"])


def test_campaign_binding_freezes_seeds_but_allows_working_knowledge_mutation(tmp_path):
    root = tmp_path / "campaign"
    root.mkdir()
    files = {}
    for name in ("cohort", "task", "routes", "knowledge", "heuristics", "runner"):
        path = root / f"{name}.json"
        path.write_text(f'{{"name":"{name}"}}\n')
        files[name] = {"path": str(path), "sha256": sha256_file(path)}
    write_json_atomic(
        root / "experiment2_campaign.json",
        {
            "cohort": files["cohort"],
            "task_spec": files["task"],
            "model_routes": files["routes"],
            "knowledge_policy": {
                "full_r2g_seed": files["knowledge"],
                "full_r2g_heuristics_seed": files["heuristics"],
            },
            "implementations": {"runner": files["runner"]},
        },
    )
    assert verify_campaign_bindings(root)["model_routes"] == files["routes"]
    working = root / "methods" / "full-r2g" / "fixture" / "runtime_state" / "knowledge.sqlite"
    working.parent.mkdir(parents=True)
    working.write_text("initial\n")
    working.write_text("learned evidence\n")
    assert verify_campaign_bindings(root)["model_routes"] == files["routes"]
    Path(files["routes"]["path"]).write_text('{"name":"changed"}\n')
    with pytest.raises(Experiment2Error, match="model routes"):
        verify_campaign_bindings(root)


def test_full_r2g_runtime_isolated_per_fixture(tmp_path):
    first = runtime_skills(tmp_path, "full-r2g", "fixture_a")
    second = runtime_skills(tmp_path, "full-r2g", "fixture_b")
    shared = runtime_skills(tmp_path, "qwen-vanilla", "fixture_a")
    assert first != second
    assert first == tmp_path / "methods" / "full-r2g" / "fixture_a" / "agent_runtime" / "r2g-skills"
    assert shared == tmp_path / "runtime" / "r2g-skills"


def test_full_r2g_environment_unifies_explicit_and_environment_knowledge_paths(tmp_path):
    project = tmp_path / "methods" / "full-r2g" / "fixture_a" / "project"
    project.mkdir(parents=True)
    env = full_r2g_environment(tmp_path, "fixture_a", project)
    knowledge = runtime_skills(tmp_path, "full-r2g", "fixture_a") / "signoff-loop" / "knowledge"
    assert env["R2G_KNOWLEDGE_DB"] == str(knowledge / "knowledge.sqlite")
    assert env["R2G_HEURISTICS_PATH"] == str(knowledge / "heuristics.json")
