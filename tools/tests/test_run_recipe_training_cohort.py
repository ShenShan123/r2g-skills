import argparse
import importlib.util
import sqlite3
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO / "tools/run_recipe_training_cohort.py"
SPEC = importlib.util.spec_from_file_location("recipe_training_cohort", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_source_identity_recovers_expander_repository_and_commit(tmp_path):
    commit = "a" * 40
    rtl = tmp_path / "repositories/github/acme/core" / commit / "source/rtl/top.sv"
    rtl.parent.mkdir(parents=True)
    rtl.write_text("module top(input clk); always @(posedge clk); endmodule\n")

    source, url, resolved_commit = MODULE.source_identity(rtl)

    assert source == rtl.parents[1]
    assert url == "https://github.com/acme/core.git"
    assert resolved_commit == commit


def test_row_inputs_rejects_files_from_two_commits(tmp_path):
    files = []
    for commit in ("a" * 40, "b" * 40):
        rtl = tmp_path / "repositories/github/acme/core" / commit / "source/rtl/top.sv"
        rtl.parent.mkdir(parents=True)
        rtl.write_text("module top; endmodule\n")
        files.append(rtl)

    try:
        MODULE.row_inputs({"rtl_files": ";".join(map(str, files))})
    except ValueError as exc:
        assert "unbound" in str(exc)
    else:
        raise AssertionError("cross-commit compilation closure was accepted")


def test_row_inputs_adds_transitive_headers_and_readmem_payload(tmp_path):
    commit = "a" * 40
    source = tmp_path / "repositories/github/acme/core" / commit / "source"
    rtl = source / "rtl/top.sv"
    header = source / "rtl/include/params.svh"
    payload = source / "rtl/data/weights.hex"
    rtl.parent.mkdir(parents=True)
    header.parent.mkdir(parents=True)
    payload.parent.mkdir(parents=True)
    rtl.write_text('`include "include/params.svh"\nmodule top; endmodule\n')
    header.write_text('$readmemh("../data/weights.hex", memory);\n')
    payload.write_text("00\n")

    _, compilation_units, dependencies = MODULE.row_inputs(
        {"rtl_files": str(rtl), "include_dirs": str(source / "rtl")}
    )

    assert compilation_units == [rtl.resolve()]
    assert {path.resolve() for path in dependencies} == {
        header.resolve(),
        payload.resolve(),
    }


def test_row_inputs_moves_explicit_headers_out_of_compilation_units(tmp_path):
    commit = "a" * 40
    source = tmp_path / "repositories/github/acme/core" / commit / "source"
    rtl = source / "rtl/top.sv"
    header = source / "rtl/params.svh"
    rtl.parent.mkdir(parents=True)
    rtl.write_text('`include "params.svh"\nmodule top; endmodule\n')
    header.write_text("`define WIDTH 8\n")

    _, compilation_units, dependencies = MODULE.row_inputs(
        {
            "rtl_files": f"{rtl};{header}",
            "include_dirs": str(rtl.parent),
        }
    )

    assert compilation_units == [rtl.resolve()]
    assert dependencies == [header.resolve()]


def test_row_inputs_rejects_dynamic_readmem_payload(tmp_path):
    commit = "a" * 40
    rtl = tmp_path / "repositories/github/acme/core" / commit / "source/rtl/top.sv"
    rtl.parent.mkdir(parents=True)
    rtl.write_text("module top; initial $readmemh(memory_path, memory); endmodule\n")

    try:
        MODULE.row_inputs({"rtl_files": str(rtl), "include_dirs": str(rtl.parent)})
    except ValueError as exc:
        assert "unresolved compilation collateral" in str(exc)
    else:
        raise AssertionError("dynamic readmem payload was accepted")


def test_row_inputs_rejects_missing_include(tmp_path):
    commit = "a" * 40
    rtl = tmp_path / "repositories/github/acme/core" / commit / "source/rtl/top.sv"
    rtl.parent.mkdir(parents=True)
    rtl.write_text('`include "missing.svh"\nmodule top; endmodule\n')

    try:
        MODULE.row_inputs({"rtl_files": str(rtl), "include_dirs": str(rtl.parent)})
    except ValueError as exc:
        assert "unresolved include closure" in str(exc)
    else:
        raise AssertionError("missing include was accepted")


def test_family_id_is_read_from_expander_notes():
    assert MODULE.family_id({"design": "d1", "notes": "design_id=d1; family_id=f123; release=x"}) == "f123"


def test_write_json_is_atomic_and_read_json_round_trips(tmp_path):
    path = tmp_path / "state/result.json"
    MODULE.write_json(path, {"strict_clean": False, "signature": ["DRC:mcon.5"]})

    assert MODULE.read_json(path) == {
        "strict_clean": False,
        "signature": ["DRC:mcon.5"],
    }


def test_force_rerun_args_overrides_existing_rerun_field():
    original = argparse.Namespace(rerun=False, workers=1, max_mapped_cells=100000)

    forced = MODULE.force_rerun_args(original)

    assert forced.rerun is True
    assert forced.workers == 1
    assert original.rerun is False


def test_constraint_coverage_requires_a_current_explicit_complete_record():
    assert MODULE.has_complete_constraint_coverage({}) is False
    assert MODULE.has_complete_constraint_coverage(
        {"constraint_coverage": {"status": "unknown"}}
    ) is False
    assert MODULE.has_complete_constraint_coverage(
        {"constraint_coverage": {"status": "incomplete"}}
    ) is False
    assert MODULE.has_complete_constraint_coverage(
        {"constraint_coverage": {"status": "complete"}}
    ) is True


def test_stable_replay_projects_requires_all_eligible_results(tmp_path):
    campaign = tmp_path / "campaign"
    for name in ("a", "b"):
        MODULE.write_json(campaign / f"state/failure_replay/{name}/attempt_1.json", {})
    MODULE.write_json(
        campaign / "state/failure_replay_summary.json",
        {"results": [{"project": "/p/a", "status": "stable_repair_challenge"}]},
    )

    complete, stable = MODULE.stable_replay_projects(campaign)

    assert complete is False
    assert stable == [Path("/p/a")]


def test_stable_replay_projects_filters_unstable_results(tmp_path):
    campaign = tmp_path / "campaign"
    for name in ("a", "b"):
        MODULE.write_json(campaign / f"state/failure_replay/{name}/attempt_1.json", {})
    MODULE.write_json(
        campaign / "state/failure_replay_summary.json",
        {
            "results": [
                {"project": "/p/a", "status": "stable_repair_challenge"},
                {"project": "/p/b", "status": "unstable_or_ineligible"},
            ]
        },
    )

    complete, stable = MODULE.stable_replay_projects(campaign)

    assert complete is True
    assert stable == [Path("/p/a")]


def test_stable_replay_projects_accepts_explicit_zero_task_completion(tmp_path):
    campaign = tmp_path / "campaign"
    MODULE.write_json(
        campaign / "state/failure_replay_summary.json",
        {
            "eligible_count": 0,
            "completed_count": 0,
            "complete": True,
            "results": [],
        },
    )

    complete, stable = MODULE.stable_replay_projects(campaign)

    assert complete is True
    assert stable == []


def test_stable_replay_flow_evidence_uses_second_attempt(tmp_path):
    campaign = tmp_path / "campaign"
    project = campaign / "projects/demo"
    evidence = campaign / "state/failure_replay/demo/attempt_2.json"
    MODULE.write_json(
        evidence,
        {"commands": [{"returncode": 124, "command": ["run_orfs.sh"]}]},
    )

    returncode, path = MODULE.stable_replay_flow_evidence(campaign, project)

    assert returncode == 124
    assert path == evidence


def test_stable_replay_flow_evidence_ignores_nonzero_signoff_gate(tmp_path):
    """A physical timing miss is not an ORFS crash when the flow itself completed."""
    campaign = tmp_path / "campaign"
    project = campaign / "projects/demo"
    evidence = campaign / "state/failure_replay/demo/attempt_2.json"
    MODULE.write_json(
        evidence,
        {
            "commands": [
                {"returncode": 0, "command": ["bash", "/r2g/run_orfs.sh"]},
                {"returncode": 0, "command": ["bash", "/r2g/fix_signoff.sh"]},
                {"returncode": 3, "command": ["python3", "/r2g/signoff_gate.py"]},
            ]
        },
    )

    returncode, path = MODULE.stable_replay_flow_evidence(campaign, project)

    assert returncode == 0
    assert path == evidence


def test_replay_skips_interrupted_unclassified_and_ineligible_capacity_failures(tmp_path):
    campaign = tmp_path / "campaign"
    projects = []
    for name, flags in (
        ("interrupted", {"execution_interrupted": True}),
        ("unclassified", {"unclassified_execution_failure": True}),
        ("capacity", {"capacity_infeasible": True}),
        ("scale", {"scale_ineligible": True}),
    ):
        project = campaign / "projects" / name
        project.mkdir(parents=True)
        MODULE.write_json(
            project / "repair_family_probe_result.json",
            {"strict_clean": False, "environment_failure": False, **flags},
        )
        projects.append({"status": "ready", "project": str(project)})
    MODULE.write_json(campaign / "state/cohort_manifest.json", {"records": projects})
    args = argparse.Namespace(
        campaign_root=campaign,
        workers=1,
        cores=1,
        timeout_seconds=1,
    )
    MODULE.replay_failures(args)

    assert not (campaign / "state/failure_replay").exists()
    summary = MODULE.read_json(campaign / "state/failure_replay_summary.json")
    assert summary["eligible_count"] == 0
    assert summary["completed_count"] == 0
    assert summary["complete"] is True
    assert summary["results"] == []


def test_summary_counts_scale_ineligible_without_treating_it_as_repair_evidence(tmp_path):
    campaign = tmp_path / "campaign"
    project = campaign / "projects/too_small"
    project.mkdir(parents=True)
    MODULE.write_json(
        project / "repair_family_probe_result.json",
        {"scale_ineligible": True, "strict_clean": False},
    )
    MODULE.write_json(
        campaign / "state/cohort_manifest.json",
        {"records": [{"design": "too_small", "status": "ready", "project": str(project)}]},
    )

    MODULE.summarize(argparse.Namespace(campaign_root=campaign))

    summary = MODULE.read_json(campaign / "state/cohort_summary.json")
    assert summary["scale_ineligible"] == 1
    assert summary["repair_challenge"] == 0


def test_summary_counts_constraint_ineligible_without_repair_evidence(tmp_path):
    campaign = tmp_path / "campaign"
    project = campaign / "projects/incomplete_sdc"
    project.mkdir(parents=True)
    MODULE.write_json(
        project / "repair_family_probe_result.json",
        {"constraint_coverage_incomplete": True, "strict_clean": False},
    )
    MODULE.write_json(
        campaign / "state/cohort_manifest.json",
        {"records": [{"design": "incomplete_sdc", "status": "ready", "project": str(project)}]},
    )

    MODULE.summarize(argparse.Namespace(campaign_root=campaign))

    summary = MODULE.read_json(campaign / "state/cohort_summary.json")
    assert summary["constraint_ineligible"] == 1
    assert summary["repair_challenge"] == 0


def test_summary_counts_unevaluable_final_timing_as_constraint_ineligible(tmp_path):
    campaign = tmp_path / "campaign"
    project = campaign / "projects/pathless_top"
    project.mkdir(parents=True)
    MODULE.write_json(
        project / "repair_family_probe_result.json",
        {"timing_evaluation_incomplete": True, "strict_clean": False},
    )
    MODULE.write_json(
        campaign / "state/cohort_manifest.json",
        {"records": [{"design": "pathless_top", "status": "ready", "project": str(project)}]},
    )

    MODULE.summarize(argparse.Namespace(campaign_root=campaign))

    summary = MODULE.read_json(campaign / "state/cohort_summary.json")
    assert summary["constraint_ineligible"] == 1
    assert summary["repair_challenge"] == 0


def test_quarantine_preexisting_candidates_is_platform_scoped(tmp_path):
    db = tmp_path / "knowledge.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE recipe_status ("
        "symptom_id TEXT, design_class TEXT, platform TEXT, strategy TEXT, "
        "status TEXT, provenance TEXT, generation INTEGER, updated_at TEXT, "
        "status_version INTEGER)"
    )
    conn.executemany(
        "INSERT INTO recipe_status VALUES (?,?,?,?,?,?,?,?,?)",
        [
            ("s1", "logic/small", "sky130hd", "density_relief", "candidate", "old", 1, "t0", 2),
            ("s2", "logic/small", "sky130hd", "setup_slack_margin", "promoted", "ab", 1, "t0", 3),
            ("s3", "logic/small", "nangate45", "period_relax", "candidate", "old", 1, "t0", 4),
        ],
    )
    conn.commit()
    conn.close()

    records = MODULE.quarantine_preexisting_candidates(db, "sky130hd")

    assert [(row["symptom_id"], row["strategy"]) for row in records] == [
        ("s1", "density_relief")
    ]
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT symptom_id, status, provenance, status_version FROM recipe_status "
        "ORDER BY symptom_id"
    ).fetchall()
    conn.close()
    assert rows == [
        ("s1", "parked", "campaign_quarantine_preexisting_candidate", 3),
        ("s2", "promoted", "ab", 3),
        ("s3", "candidate", "old", 4),
    ]


def test_fixed_clock_target_excludes_period_relax_without_losing_other_exclusions():
    original = {"R2G_FIX_EXCLUDE": "unsafe_recipe", "KEEP": "yes"}

    protected = MODULE.protect_fixed_clock_target(original)

    assert protected["R2G_FIX_EXCLUDE"] == "unsafe_recipe,period_relax"
    assert protected["KEEP"] == "yes"
    assert original["R2G_FIX_EXCLUDE"] == "unsafe_recipe"
    assert MODULE.protect_fixed_clock_target(protected)["R2G_FIX_EXCLUDE"] == (
        "unsafe_recipe,period_relax"
    )


def test_fixed_footprint_excludes_all_area_changing_strategies_idempotently():
    original = {"R2G_FIX_EXCLUDE": "unsafe_recipe", "KEEP": "yes"}

    protected = MODULE.protect_fixed_footprint(original)
    excluded = protected["R2G_FIX_EXCLUDE"].split(",")

    assert excluded[0] == "unsafe_recipe"
    assert set(MODULE._FOOTPRINT_CHANGING_STRATEGIES).issubset(excluded)
    assert len(excluded) == len(set(excluded))
    assert protected["KEEP"] == "yes"
    assert original["R2G_FIX_EXCLUDE"] == "unsafe_recipe"
    assert MODULE.protect_fixed_footprint(protected) == protected


def test_frozen_existing_recipe_run_skips_learning_and_ab_drain(tmp_path, monkeypatch):
    campaign = tmp_path / "campaign"
    project = campaign / "projects" / "demo"
    project.mkdir(parents=True)
    runtime_db = tmp_path / "knowledge.sqlite"
    runtime_db.touch()
    heuristics = tmp_path / "heuristics.json"
    heuristics.write_text("{}\n")
    journal = tmp_path / "journal.sqlite"
    journal.touch()
    replay = campaign / "state" / "failure_replay" / "demo" / "attempt_2.json"
    replay.parent.mkdir(parents=True)
    replay.write_text("{}\n")

    monkeypatch.setattr(MODULE, "stable_replay_projects", lambda campaign: (True, [project]))
    monkeypatch.setattr(
        MODULE,
        "stable_replay_flow_evidence",
        lambda campaign, project: (2, replay),
    )
    monkeypatch.setattr(MODULE, "quarantine_preexisting_candidates", lambda *args: [])
    commands = []

    class Completed:
        returncode = 0

    def run(command, **kwargs):
        commands.append(command)
        return Completed()

    monkeypatch.setattr(MODULE.subprocess, "run", run)
    args = MODULE.argparse.Namespace(
        campaign_root=campaign,
        runtime_db=runtime_db,
        heuristics=heuristics,
        journal_db=journal,
        platform="sky130hd",
        workers=1,
        cores=4,
        poll_seconds=1,
        max_wait_seconds=1,
        fixed_clock_target=True,
        fixed_footprint=True,
        frozen_knowledge=True,
        quarantine_preexisting_candidates=True,
    )

    MODULE.run_existing_recipes(args)

    run_commands = [command for command in commands if "run" in command]
    assert len(run_commands) == 1
    assert "--no-learn" in run_commands[0]
    assert not any("ab-drain" in command for command in commands)


def test_existing_recipe_run_records_empty_stable_cohort_without_engineer_loop(
    tmp_path, monkeypatch
):
    campaign = tmp_path / "campaign"
    runtime_db = tmp_path / "knowledge.sqlite"
    runtime_db.touch()
    heuristics = tmp_path / "heuristics.json"
    heuristics.write_text("{}\n")
    journal = tmp_path / "journal.sqlite"
    journal.touch()
    monkeypatch.setattr(MODULE, "stable_replay_projects", lambda campaign: (True, []))
    commands = []
    monkeypatch.setattr(
        MODULE.subprocess,
        "run",
        lambda command, **kwargs: commands.append(command),
    )
    args = MODULE.argparse.Namespace(
        campaign_root=campaign,
        runtime_db=runtime_db,
        heuristics=heuristics,
        journal_db=journal,
        platform="sky130hd",
        workers=1,
        cores=4,
        poll_seconds=1,
        max_wait_seconds=1,
        fixed_clock_target=True,
        fixed_footprint=True,
        frozen_knowledge=True,
        quarantine_preexisting_candidates=True,
    )

    MODULE.run_existing_recipes(args)

    result = MODULE.read_json(campaign / "state/existing_recipe_execution.json")
    assert result["status"] == "no_stable_repair_challenges"
    assert result["stable_projects"] == []
    assert result["records"] == []
    assert commands == []
