import os
from pathlib import Path

from scripts.run_orfs_diversity_campaign import (
    _classify_attempt, _has_run, _resume_stage, _reusable_success, _run_bounded,
    _stage_checkpoint, _workspace_key,
)
from scripts.orfs_storage import default_work_root, enforce_work_root


def _run(root: Path, name: str, status: int, with_def: bool = True) -> Path:
    run = root / "backend" / name
    (run / "final").mkdir(parents=True)
    (run / "run-meta.json").write_text('{"make_status": %d}\n' % status)
    (run / "stage_log.jsonl").write_text('{"stage":"route","status":%d}\n' % status)
    if with_def:
        (run / "final" / "6_final.def").write_text("VERSION 5.8 ;\n")
    return run


def test_timeout_run_is_not_resumable_success(tmp_path):
    _run(tmp_path, "RUN_timeout", 124)
    assert _has_run(tmp_path) is False


def test_legacy_timeout_receipt_cannot_be_cached_as_success(tmp_path):
    _run(tmp_path, "RUN_legacy", 0)
    digest = "abc"
    assert _reusable_success({"config_sha256": digest, "completed": True,
                              "flow_rc": 124}, digest, tmp_path) is False
    assert _reusable_success({"config_sha256": digest, "completed": True,
                              "flow_rc": 0}, digest, tmp_path) is True


def test_success_requires_frozen_final_def(tmp_path):
    _run(tmp_path, "RUN_no_def", 0, with_def=False)
    assert _has_run(tmp_path) is False
    _run(tmp_path, "RUN_clean", 0)
    for name in ("run-meta.json", "stage_log.jsonl"):
        os.utime(tmp_path / "backend" / "RUN_no_def" / name, (1, 1))
    for name in ("run-meta.json", "stage_log.jsonl"):
        os.utime(tmp_path / "backend" / "RUN_clean" / name, (2, 2))
    os.utime(tmp_path / "backend" / "RUN_clean" / "final" / "6_final.def", (2, 2))
    assert _has_run(tmp_path) is True


def test_newer_failed_run_invalidates_older_success(tmp_path):
    _run(tmp_path, "RUN_clean", 0)
    _run(tmp_path, "RUN_timeout", 124)
    for name in ("run-meta.json", "stage_log.jsonl"):
        os.utime(tmp_path / "backend" / "RUN_clean" / name, (1, 1))
        os.utime(tmp_path / "backend" / "RUN_timeout" / name, (2, 2))
    os.utime(tmp_path / "backend" / "RUN_clean" / "final" / "6_final.def", (1, 1))
    assert _has_run(tmp_path) is False


def test_stage_checkpoint_uses_newest_parseable_record(tmp_path):
    run = tmp_path / "backend" / "RUN_latest"
    run.mkdir(parents=True)
    (run / "stage_log.jsonl").write_text(
        '{"stage":"synth","status":"ok","elapsed_s":3}\n'
        'not-json\n'
        '{"stage":"route","status":"timeout","elapsed_s":120}\n')
    checkpoint = _stage_checkpoint(tmp_path)
    assert checkpoint["stage"] == "route"
    assert checkpoint["status"] == "timeout"
    assert checkpoint["path"].endswith("RUN_latest/stage_log.jsonl")
    assert _resume_stage(checkpoint) == "route"
    assert _resume_stage({"stage": "route", "status": 0}) is None
    assert _resume_stage({"stage": "unknown", "status": 2}) is None
    config = tmp_path / "constraints" / "config.mk"
    config.parent.mkdir(exist_ok=True)
    config.write_text("export PLATFORM = sky130hd\nexport DESIGN_NAME = gcd\n")
    assert _resume_stage(checkpoint, project=tmp_path) is None


def test_attempt_classifier_keeps_infrastructure_separate(tmp_path):
    log = tmp_path / "flow.log"
    log.write_text("ERROR: R2G_INPUTS_MISSING\n")
    assert _classify_attempt(2, False, False, None, log) == (
        "INFRASTRUCTURE_FAILURE", "infrastructure")
    log.write_text("ERROR: Stage 'route' failed (exit code 2)\n")
    assert _classify_attempt(2, False, False, None, log) == (
        "FLOW_FAILURE", "design_or_tool")
    log.write_text("ERROR: resume lineage verification failed\n")
    assert _classify_attempt(4, False, False, None, log) == (
        "INFRASTRUCTURE_FAILURE", "infrastructure")
    assert _classify_attempt(124, False, False, None, log) == (
        "TIMEOUT", "infrastructure")


def test_workspace_key_serializes_logical_design_variants(tmp_path):
    project = tmp_path / "variant"
    config = project / "constraints" / "config.mk"
    config.parent.mkdir(parents=True)
    config.write_text(
        "export DESIGN_NICKNAME = fifo_variant\n"
        "export DESIGN_NAME = selector_fifo16\n")
    # The scheduler deliberately omits FLOW_VARIANT: before/after variants of
    # one logical design must not run concurrently against shared ORFS inputs.
    assert _workspace_key(project, "sky130hs") == (
        "sky130hs", "fifo_variant")


def test_workspace_key_falls_back_for_incomplete_project(tmp_path):
    project = tmp_path / "unmaterialized_variant"
    assert _workspace_key(project, "sky130hs") == (
        "sky130hs", "unmaterialized_variant")


def test_outer_supervisor_reaps_timed_out_process_group(tmp_path):
    log = tmp_path / "supervised.log"
    rc, timed_out = _run_bounded(
        ["bash", "-c", "sleep 5"], log, env={}, timeout=1, grace=1)
    assert (rc, timed_out) == (124, True)


def test_orfs_storage_defaults_to_tmp_and_rejects_data1_without_override():
    assert str(default_work_root("prospective")).startswith("/tmp/")
    assert enforce_work_root(Path("/tmp/tehm-orfs/prospective")) == Path(
        "/tmp/tehm-orfs/prospective")
