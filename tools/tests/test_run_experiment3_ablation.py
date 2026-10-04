import importlib.util
import json
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO / "tools/run_experiment3_ablation.py"
SPEC = importlib.util.spec_from_file_location("run_experiment3_ablation", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def _result(*, clean=False, signature=None, wns=-1.0, drc=0, **flags):
    return {
        "task_id": "task-a",
        "protected_task_digest": "family-a",
        "strict_clean": clean,
        "normalized_failure_signature": signature or [],
        "metrics": {"setup_wns_ns": wns, "drc_violations": drc, "route_violations": 0, "lvs_mismatches": 0},
        **flags,
    }


def _candidate():
    return {
        "candidate_id": "timing-map",
        "candidate_version": 1,
        "candidate_hash": "abc",
        "failure_domain": "setup_timing",
        "applicability": {
            "platform": "sky130hd",
            "required_failure_signatures": ["SETUP_TIMING"],
            "requires_negative_setup_wns": True,
        },
        "config_edits": {"ABC_AREA": "0"},
    }


def test_candidate_applicability_uses_dynamic_baseline_predicates():
    assert MODULE.candidate_applies(_candidate(), _result(signature=["SETUP_TIMING"], wns=-1.0))
    assert not MODULE.candidate_applies(_candidate(), _result(signature=["SETUP_TIMING"], wns=0.1))


def test_candidate_applies_to_nangate45_antenna_wildcard():
    candidate = {
        "applicability": {
            "platform": "nangate45",
            "required_failure_signatures": ["DRC:*_ANTENNA"],
        }
    }
    baseline = _result(signature=["DRC:METAL5_ANTENNA"], drc=4)
    assert MODULE.candidate_applies(candidate, baseline)


def test_bind_frozen_baseline_constraints_restores_sdc_and_digest(tmp_path):
    baseline = tmp_path / "baseline"
    project = tmp_path / "project"
    for root in (baseline, project):
        (root / "constraints").mkdir(parents=True)
    (baseline / "constraints/constraint.sdc").write_text("baseline-sdc\n")
    (project / "constraints/constraint.sdc").write_text("generated-sdc\n")
    (project / "constraints/config.mk").write_text("export TEST = 1\n")
    protected = {
        "source_digest": "source",
        "compilation_units": ["rtl/helper.v", "rtl/top.v"],
        "dependency_inputs": ["rtl/include/defs.vh", "rtl/include/types.vh"],
        "source_repo_url": "https://example.invalid/repo",
        "source_commit": "abc",
        "top_module": "top",
        "platform": "nangate45",
        "clock_port": "clk",
        "target_frequency_mhz": 100.0,
        "sdc_sha256": MODULE.sha256_file(baseline / "constraints/constraint.sdc"),
        "signoff_mode": "strict",
        "check_set": ["orfs", "drc"],
        "top_parameters": {},
    }
    digest = MODULE.hashlib.sha256(
        MODULE.json.dumps(protected, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    MODULE.write_json(
        baseline / "repair_family_probe_input.json",
        {"protected_task": protected, "protected_task_digest": digest},
    )
    generated = dict(protected)
    generated.pop("top_parameters")
    generated["compilation_units"] = list(reversed(protected["compilation_units"]))
    generated["dependency_inputs"] = list(reversed(protected["dependency_inputs"]))
    generated["sdc_sha256"] = MODULE.sha256_file(project / "constraints/constraint.sdc")
    MODULE.write_json(
        project / "repair_family_probe_input.json",
        {
            "protected_task": generated,
            "protected_task_digest": "generated",
            "config_sha256": "old",
        },
    )
    MODULE.bind_frozen_baseline_constraints(baseline, project)
    rebound = MODULE.read_json(project / "repair_family_probe_input.json")
    assert rebound["protected_task"] == protected
    assert rebound["protected_task_digest"] == digest
    assert (project / "constraints/constraint.sdc").read_text() == "baseline-sdc\n"


def test_formal_win_requires_strict_clean(tmp_path):
    project = tmp_path / "trial"
    project.mkdir()
    (project / "repair_family_probe_result.json").write_text("{}", encoding="utf-8")
    baseline = _result(signature=["SETUP_TIMING"], wns=-2.0)
    improved = _result(signature=["SETUP_TIMING"], wns=-0.1)
    clean = _result(clean=True, wns=0.3)
    improved_evidence = MODULE.trial_evidence(
        baseline, improved, _candidate(), split="a_propose", evidence_role="a_propose_frozen_replay", project=project
    )
    clean_evidence = MODULE.trial_evidence(
        baseline, clean, _candidate(), split="a_propose", evidence_role="a_propose_frozen_replay", project=project
    )
    assert improved_evidence["wns_delta_ns"] > 0
    assert improved_evidence["verdict"] == "loss"
    assert clean_evidence["verdict"] == "win"


def test_trial_evidence_records_isolated_arm(tmp_path):
    project = tmp_path / "trial"
    project.mkdir()
    (project / "repair_family_probe_result.json").write_text("{}", encoding="utf-8")
    evidence = MODULE.trial_evidence(
        _result(signature=["SETUP_TIMING"]),
        _result(clean=True, wns=0.2),
        _candidate(),
        split="b_heldout",
        evidence_role="pure_llm",
        project=project,
        arm_id="pure_llm_gpt",
    )
    assert evidence["arm_id"] == "pure_llm_gpt"


def test_infrastructure_failure_is_inconclusive(tmp_path):
    project = tmp_path / "trial"
    project.mkdir()
    (project / "repair_family_probe_result.json").write_text("{}", encoding="utf-8")
    evidence = MODULE.trial_evidence(
        _result(signature=["DRC:m3.2"], drc=4),
        _result(signature=["DRC:m3.2"], drc=2, execution_interrupted=True),
        _candidate() | {"failure_domain": "drc_edge_pin"},
        split="a_validation",
        evidence_role="a_validation",
        project=project,
    )
    assert evidence["verdict"] == "inconclusive"
    assert evidence["provenance_complete"] is False


def test_materialize_preserves_baseline_edits_and_overrides_candidate(tmp_path):
    baseline = tmp_path / "baseline"
    baseline.mkdir()
    source = tmp_path / "source"
    (source / "rtl").mkdir(parents=True)
    (source / "rtl/top.v").write_text("module top; endmodule\n", encoding="utf-8")
    (source / "rtl/defs.vh").write_text("// defs\n", encoding="utf-8")
    manifest = {
        "source_snapshot": str(source),
        "source_repo_url": "https://example.test/repo",
        "source_commit": "a" * 40,
        "family_id": "family-a",
        "task_id": "task-a",
        "compilation_units": ["rtl/rtl/top.v"],
        "dependency_inputs": ["rtl/rtl/defs.vh"],
        "config_edits": {"SYNTH_MEMORY_MAX_BITS": "131072", "ABC_AREA": "1"},
        "config_unsets": [],
        "protected_task": {
            "platform": "sky130hd",
            "top_module": "top",
            "clock_port": "clk",
            "target_frequency_mhz": 100,
        },
    }
    (baseline / "repair_family_probe_input.json").write_text(json.dumps(manifest), encoding="utf-8")
    command = MODULE.materialize_command(baseline, tmp_path / "trial", _candidate(), "variant")
    joined = " ".join(command)
    assert "SYNTH_MEMORY_MAX_BITS=131072" in joined
    assert "ABC_AREA=0" in joined
    assert "ABC_AREA=1" not in joined


def test_materialize_falls_back_to_portable_baseline_rtl(tmp_path):
    baseline = tmp_path / "baseline"
    (baseline / "rtl/rtl").mkdir(parents=True)
    (baseline / "rtl/rtl/top.v").write_text("module top; endmodule\n", encoding="utf-8")
    manifest = {
        "source_snapshot": str(tmp_path / "missing-original"),
        "source_repo_url": "https://example.test/repo",
        "source_commit": "a" * 40,
        "family_id": "family-a",
        "task_id": "task-a",
        "compilation_units": ["rtl/rtl/top.v"],
        "dependency_inputs": [],
        "config_edits": {},
        "config_unsets": [],
        "protected_task": {
            "platform": "sky130hd",
            "top_module": "top",
            "clock_port": "clk",
            "target_frequency_mhz": 100,
        },
    }
    (baseline / "repair_family_probe_input.json").write_text(json.dumps(manifest), encoding="utf-8")

    command = MODULE.materialize_command(baseline, tmp_path / "trial", None, "variant")

    assert command[command.index("--source") + 1] == str((baseline / "rtl").resolve())


def test_m0_trial_slug_is_stable():
    assert MODULE._trial_slug(None) == "m0_baseline"


def test_a_explore_executes_frozen_round_plan_before_admission():
    candidate = _candidate()
    plan = {
        "schema_version": "experiment3-proposal-round-plan-1.0",
        "selected": [{"candidate": candidate, "proposal_sources": ["gpt"]}],
    }
    records = MODULE._candidate_records(plan, "a_explore")
    assert [row["candidate_hash"] for row in records] == ["abc"]
    assert records[0]["candidate"] == candidate


def test_a_explore_rejects_non_round_plan():
    try:
        MODULE._candidate_records({"candidate_records": []}, "a_explore")
    except ValueError as exc:
        assert "frozen proposal round plan" in str(exc)
    else:
        raise AssertionError("a_explore accepted a mutable/non-round plan")


def test_clean_sentinel_replays_every_candidate_despite_empty_signature():
    candidates = [
        {"candidate": _candidate()},
        {"candidate": _candidate() | {"candidate_id": "second", "candidate_hash": "def"}},
    ]
    applicable = MODULE._applicable_candidates(
        "clean-task",
        {"clean-task"},
        candidates,
        _result(clean=True, signature=[], wns=0.5),
        "a_formal",
    )
    assert [row["candidate_hash"] for row in applicable] == ["abc", "def"]


def test_repair_challenge_still_filters_by_failure_domain():
    candidate = _candidate()
    applicable = MODULE._applicable_candidates(
        "repair-task",
        set(),
        [{"candidate": candidate}],
        _result(signature=["DRC:m3.2"], drc=4),
        "a_formal",
    )
    assert applicable == []


def test_task_restriction_is_split_bounded():
    assert MODULE._restrict_task_ids(["a", "b"], ["b"], "b_heldout") == ["b"]
    try:
        MODULE._restrict_task_ids(["a", "b"], ["validation-task"], "b_heldout")
    except ValueError as exc:
        assert "not in split b_heldout" in str(exc)
    else:
        raise AssertionError("cross-split task restriction was accepted")


def test_matrix_signature_binds_tasks_candidates_and_resources(tmp_path):
    work = [("task-a", tmp_path / "baseline", [_candidate()])]
    first = MODULE._matrix_run_signature(
        mode="m1",
        split="b_heldout",
        arm_id="m1",
        work=work,
        cores=4,
        timeout_seconds=7200,
    )
    same = MODULE._matrix_run_signature(
        mode="m1",
        split="b_heldout",
        arm_id="m1",
        work=work,
        cores=4,
        timeout_seconds=7200,
    )
    changed = MODULE._matrix_run_signature(
        mode="m1",
        split="b_heldout",
        arm_id="m1",
        work=work,
        cores=4,
        timeout_seconds=3600,
    )
    assert first == same
    assert first != changed


def test_resume_skips_only_terminal_provenance_complete_results():
    complete = {
        "status": "completed",
        "attempts": [{"provenance_complete": True}],
    }
    infrastructure_failure = {
        "status": "completed",
        "attempts": [{"provenance_complete": False}],
    }
    assert MODULE._terminal_checkpoint_result(complete)
    assert MODULE._terminal_checkpoint_result({"status": "no_applicable_candidate"})
    assert not MODULE._terminal_checkpoint_result(infrastructure_failure)
    assert not MODULE._terminal_checkpoint_result(
        {"status": "infrastructure_incomplete", "attempts": [{"provenance_complete": False}]}
    )
    assert not MODULE._terminal_checkpoint_result({"status": "runner_error"})


def test_manifest_file_records_are_portable(tmp_path):
    campaign = tmp_path / "campaign"
    artifact = campaign / "data/artifact.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("{}\n", encoding="utf-8")
    record = MODULE.portable_file_record(artifact, campaign)
    assert record["scope"] == "campaign"
    assert record["relative_path"] == "data/artifact.json"
    assert "path_at_freeze" in record
