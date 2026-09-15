"""Archived bytes are distinct from current tool validity and authority."""
import copy
import hashlib
import json

import pytest

from tehm.adapters import orfs_archived_replay as consumer
from tehm.adapters.orfs_terminal_failure import (
    FLOW_CONTRACT, _digest, replay_flow_feasibility_pair, terminal_run_file_bindings,
)
from tehm.origin_bundle import OriginBundle, OriginBundleError, OriginReadPlan, READ_PLAN_SCHEMA, SCHEMA
from .test_orfs_terminal_failure import replay_inputs


def _sha(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _plan_from_files(root, files):
    root.mkdir()
    bindings, blobs = [], {}
    for path in files:
        data = path.read_bytes()
        value = _sha(data)
        hexed = value.removeprefix("sha256:")
        relative = f"sha256/{hexed[:2]}/{hexed}"
        blob = root / relative
        blob.parent.mkdir(parents=True, exist_ok=True)
        blob.write_bytes(data)
        bindings.append({"original_path": str(path), "sha256": value})
        blobs[value] = {"relative_path": relative, "size": len(data)}
    manifest = {"schema": SCHEMA, "aliases": bindings, "blobs": blobs}
    manifest_data = json.dumps(manifest, sort_keys=True).encode()
    (root / "manifest.json").write_bytes(manifest_data)
    payload = {"schema": READ_PLAN_SCHEMA, "bindings": sorted(
        bindings, key=lambda row: (row["original_path"], row["sha256"]),
    )}
    pin = _sha(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode())
    return OriginReadPlan(
        OriginBundle(root, expected_manifest_sha256=_sha(manifest_data)), bindings,
        expected_plan_digest=pin,
    )


def _archive_pair(tmp_path, change=None):
    historical_tools = {
        "status": "bound_internal", "manifest_validation": {"valid": True},
        "generation": "historical-original-root",
    }
    projects = [tmp_path / side for side in ("before", "after")]
    pins = []
    for project, density, success in zip(projects, (95, 40), (False, True)):
        project.mkdir()
        pin, _, _ = replay_inputs(
            project, version=FLOW_CONTRACT["version"], success=success,
            run_name="RUN_" + project.name, toolchain=historical_tools,
            config_lines=("export DESIGN_NAME = design\nexport PLATFORM = sky130hs\n"
                          f"export CORE_UTILIZATION = {density}\n"),
        )
        pins.append(pin)
    if change is not None:
        change(projects)
    root = tmp_path / "bundle"
    root.mkdir()
    bindings, blobs = [], {}
    for project in projects:
        for path in sorted(project.rglob("*")):
            if not path.is_file():
                continue
            data = path.read_bytes()
            value = _sha(data)
            hexed = value.removeprefix("sha256:")
            relative = f"sha256/{hexed[:2]}/{hexed}"
            blob = root / relative
            blob.parent.mkdir(parents=True, exist_ok=True)
            blob.write_bytes(data)
            bindings.append({"original_path": str(path), "sha256": value})
            blobs[value] = {"relative_path": relative, "size": len(data)}
    manifest = {"schema": SCHEMA, "aliases": bindings, "blobs": blobs}
    data = json.dumps(manifest, sort_keys=True).encode()
    (root / "manifest.json").write_bytes(data)
    plan_payload = {"schema": READ_PLAN_SCHEMA, "bindings": sorted(
        bindings, key=lambda row: (row["original_path"], row["sha256"]),
    )}
    plan_pin = _sha(json.dumps(
        plan_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode())
    plan = OriginReadPlan(
        OriginBundle(root, expected_manifest_sha256=_sha(data)), bindings,
        expected_plan_digest=plan_pin,
    )
    try:
        expected = replay_flow_feasibility_pair(
            *projects, before_pin=pins[0], after_pin=pins[1],
            current_toolchain=historical_tools, config_edits={"CORE_UTILIZATION": "40"},
        )
    except ValueError:
        expected = None  # Deliberately malformed fixture, not positive evidence.
    # The logical paths in JSON and canonical identity no longer exist live.
    for project in projects:
        project.rename(project.with_name(project.name + "-unavailable"))
    kwargs = {
        "before_pin": pins[0], "after_pin": pins[1],
        "config_edits": {"CORE_UTILIZATION": "40"},
        "historical_toolchain_digest": _digest(historical_tools),
        "current_toolchain_manifest": tmp_path / "current-lock.json",
        "expected_current_manifest_digest": "current-lock-digest",
    }
    return plan, list(map(str, projects)), kwargs, expected


def _mock_current(monkeypatch, reports=None):
    lock = {"manifest_digest": "current-lock-digest", "orfs": {"root": "/relocated/current"}}
    current = {
        "status": "bound_internal", "generation": "current-relocated-root",
        "manifest_validation": {"valid": True, "manifest_digest": "current-lock-digest"},
    }
    calls = []
    monkeypatch.setattr(consumer, "load_toolchain_manifest", lambda path: copy.deepcopy(lock))

    def probe(reference):
        calls.append(copy.deepcopy(reference))
        return copy.deepcopy(reports[len(calls) - 1] if reports else current)

    monkeypatch.setattr(consumer, "preflight_orfs_toolchain", probe)
    return calls, current


def test_archive_pair_uses_no_original_paths_and_separates_live_tool_generation(tmp_path, monkeypatch):
    plan, projects, kwargs, expected = _archive_pair(tmp_path)
    calls, _ = _mock_current(monkeypatch)
    receipt = consumer.replay_archived_flow_feasibility_pair(plan, *projects, **kwargs)
    assert receipt["before"]["verdict"] == "FAIL"
    assert receipt["after"]["verdict"] == "PASS"
    assert receipt["controlled_measurement_valid"] is True
    assert receipt["historical_pair_receipt"] == expected
    assert receipt["historical_receipt_identity_preserved"] is True
    assert receipt["historical_run_directory_inventory_verified"] is False
    assert receipt["current_toolchain_probed"] is True and len(calls) == 2
    assert receipt["origin_read_plan"]["logical_paths_preserved"] is True
    assert receipt["origin_read_plan"]["live_filesystem_fallback"] is False
    for key in ("historical_execution_reexecuted", "toolchain_equivalence_proven",
                "canonical_replayed", "learner_admission", "promotion_attempted",
                "production_authority"):
        assert receipt[key] is False
    assert receipt == consumer.replay_archived_flow_feasibility_pair(plan, *projects, **kwargs)


@pytest.mark.parametrize("pin", ["before_pin", "after_pin", "historical_toolchain_digest",
                                  "expected_current_manifest_digest"])
def test_archive_pair_requires_independent_registration_and_generation_pins(tmp_path, monkeypatch, pin):
    plan, projects, kwargs, _ = _archive_pair(tmp_path)
    _mock_current(monkeypatch)
    kwargs[pin] = "wrong-independent-pin"
    with pytest.raises(ValueError, match="binding|pin"):
        consumer.replay_archived_flow_feasibility_pair(plan, *projects, **kwargs)


def test_saved_historical_valid_true_cannot_override_current_live_failure(tmp_path, monkeypatch):
    plan, projects, kwargs, _ = _archive_pair(tmp_path)
    _mock_current(monkeypatch, [{"status": "blocked", "manifest_validation": {"valid": True}}])
    with pytest.raises(ValueError, match="live preflight"):
        consumer.replay_archived_flow_feasibility_pair(plan, *projects, **kwargs)


def test_current_tools_must_not_change_during_archive_replay(tmp_path, monkeypatch):
    plan, projects, kwargs, _ = _archive_pair(tmp_path)
    valid = {"status": "bound_internal", "manifest_validation": {
        "valid": True, "manifest_digest": "current-lock-digest"}}
    _mock_current(monkeypatch, [valid, {**valid, "changed": True}])
    with pytest.raises(ValueError, match="changed during"):
        consumer.replay_archived_flow_feasibility_pair(plan, *projects, **kwargs)


@pytest.mark.parametrize("data", [b'{"run_tag":"RUN_after","run_tag":"RUN_after","make_status":0}',
                                  b'{"run_tag":"RUN_after","make_status":NaN}'])
def test_pinned_raw_json_still_rejects_duplicates_and_nonfinite_values(tmp_path, monkeypatch, data):
    def change(projects):
        project = projects[1]
        (project / "backend/RUN_after/run-meta.json").write_bytes(data)
        execution_path = project / "campaign-run-receipt.json"
        execution = json.loads(execution_path.read_text())
        execution["terminal_run_files"] = terminal_run_file_bindings(project)
        execution_path.write_text(json.dumps(execution))

    plan, projects, kwargs, _ = _archive_pair(tmp_path, change)
    _mock_current(monkeypatch)
    with pytest.raises(ValueError):
        consumer.replay_archived_flow_feasibility_pair(plan, *projects, **kwargs)


def test_archive_pair_does_not_promote_incomplete_completion_to_pass(tmp_path, monkeypatch):
    def change(projects):
        project = projects[1]
        (project / "backend/RUN_after/stage_log.jsonl").write_text(
            json.dumps({"stage": "synth", "status": 0}),
        )
        path = project / "campaign-run-receipt.json"
        execution = json.loads(path.read_text())
        files = terminal_run_file_bindings(project)
        execution.update(terminal_run_files=files, stage_log_sha256=files[1]["sha256"])
        path.write_text(json.dumps(execution))

    plan, projects, kwargs, _ = _archive_pair(tmp_path, change)
    _mock_current(monkeypatch)
    receipt = consumer.replay_archived_flow_feasibility_pair(plan, *projects, **kwargs)
    assert receipt["after"]["verdict"] == "UNKNOWN"
    assert receipt["controlled_measurement_valid"] is False
    assert receipt["production_authority"] is False


@pytest.mark.parametrize("role", ["treatment", "control"])
def test_archived_canonical_reconstruction_preserves_all_rows_without_authority(
        tmp_path, tmp_tehm, role):
    from dataclasses import asdict
    from .test_orfs_toolchain_manifest import _fake_orfs
    from tehm.adapters.orfs_scoped import build_flow_feasibility_record
    from tehm.canonical.capture import capture
    from tehm.causal.mechanism import load_transition_facts
    from tehm.orfs_toolchain import build_toolchain_manifest
    from tehm.orfs_toolchain_preflight import preflight_orfs_toolchain
    from tehm.verified_execution import require_verified_execution

    original_orfs, _, _ = _fake_orfs(tmp_path / "original-orfs")
    historical_lock = build_toolchain_manifest(
        preflight_orfs_toolchain({"orfs_root": str(original_orfs)}, env={}),
    )
    historical_path = tmp_path / "historical-lock.json"
    historical_path.write_text(json.dumps(historical_lock))
    historical_tools = preflight_orfs_toolchain({
        "orfs_root": str(original_orfs), "toolchain_manifest": str(historical_path),
    })
    projects, pins = [tmp_path / side for side in ("before", "after")], []
    for project, density, success in zip(projects, (95, 40), (False, True)):
        project.mkdir()
        pin, _, _ = replay_inputs(
            project, version=FLOW_CONTRACT["version"], success=success,
            run_name="RUN_" + project.name, toolchain=historical_tools,
            config_lines=("export DESIGN_NAME = design\nexport PLATFORM = sky130hs\n"
                          f"export CORE_UTILIZATION = {density}\n"),
        )
        pins.append(pin)
    acquisition = {
        "before": str(projects[0]), "after": str(projects[1]),
        "lineage_id": "fixture-archived-density", "before_pin": pins[0], "after_pin": pins[1],
        "config_edits": {"CORE_UTILIZATION": "40"}, "toolchain_manifest": str(historical_path),
        "expected_manifest_digest": historical_lock["manifest_digest"], "role": role,
    }
    original_record = build_flow_feasibility_record(**acquisition)
    conn, store, _ = tmp_tehm
    captured = capture(conn, store, original_record, dataset_learner_eligible=False)
    files = [path for project in projects for path in project.rglob("*") if path.is_file()]
    plan = _plan_from_files(tmp_path / "canonical-bundle", files + [historical_path])
    for project in projects:
        project.rename(project.with_name(project.name + "-unavailable"))
    historical_path.rename(historical_path.with_suffix(".unavailable"))
    original_orfs.rename(original_orfs.with_name("original-orfs-unavailable"))

    current_orfs, _, _ = _fake_orfs(tmp_path / "current-orfs")
    current_lock = build_toolchain_manifest(
        preflight_orfs_toolchain({"orfs_root": str(current_orfs)}, env={}),
    )
    current_path = tmp_path / "current-lock.json"
    current_path.write_text(json.dumps(current_lock))
    current_kwargs = {
        "current_toolchain_manifest": current_path,
        "expected_current_manifest_digest": current_lock["manifest_digest"],
    }
    rebuilt, replay = consumer.rebuild_archived_flow_feasibility_record(
        plan, acquisition=acquisition, **current_kwargs,
    )
    assert asdict(rebuilt) == asdict(original_record)
    assert replay["canonical_record_reconstructed"] is True
    changes = conn.total_changes
    receipt = consumer.replay_persisted_archived_flow_feasibility(
        conn, captured.transition_id, plan, acquisition=acquisition, **current_kwargs,
    )
    assert conn.total_changes == changes
    assert receipt["canonical_replayed"] and receipt["persisted_binding_verified"]
    assert receipt["canonical_memory_mutation"] == "none"
    assert not receipt["learner_admission"] and not receipt["production_authority"]
    assert not receipt["toolchain_equivalence_proven"]
    with pytest.raises(ValueError, match="scoped_execution_replay_required"):
        require_verified_execution(load_transition_facts(conn, captured.transition_id))
    wrong = {**acquisition, "expected_manifest_digest": "wrong-original-lock-pin"}
    with pytest.raises(ValueError, match="historical manifest pin"):
        consumer.rebuild_archived_flow_feasibility_record(plan, acquisition=wrong, **current_kwargs)
    conn.execute("UPDATE tehm_states SET source_digest=? WHERE state_id=?",
                 ("tampered-source-digest", captured.state_ids["before"]))
    with pytest.raises(ValueError, match="evidence mismatch"):
        consumer.replay_persisted_archived_flow_feasibility(
            conn, captured.transition_id, plan, acquisition=acquisition, **current_kwargs,
        )
