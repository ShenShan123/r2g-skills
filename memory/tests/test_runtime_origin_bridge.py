"""Origin/current distinction and negative-control conformance."""
import copy
import pytest

from scripts.audit_r3_capability_gap_runtime_bridge import verify_epoch_maps, MODIFIED, ADDED, _git
from scripts.audit_r3_capability_gap_shadow_authority import audit_shadow_authority
from scripts.build_r3_orfs_interference_source_bound_inputs import _digest


def _binding(files, root):
    binding = {"version": "p13-interference-runtime-generation-binding-v1",
        "files": [{"path": root + "/" + name, "sha256": value} for name, value in sorted(files.items())]}
    binding["binding_digest"] = _digest(binding)
    return binding


def _inputs():
    old = {name: "sha256:origin" for name in MODIFIED}
    old["tehm/rtl/rtl_oracle.py"] = "sha256:unchanged-physical-oracle"
    old["tehm/assets/guard_binding.py"] = "sha256:unchanged-source-locator"
    new = {name: "sha256:selector-update" if name in MODIFIED else value for name, value in old.items()}
    new.update({name: "sha256:added" for name in ADDED})
    return old, new


def _verify(old, new, git_files=None):
    return verify_epoch_maps(_binding(old, "/recorded/memory"), _binding(new, "/relocated/memory"),
        old if git_files is None else git_files, origin_root="/recorded/memory", current_root="/relocated/memory")


def test_exact_selector_bridge_preserves_old_epoch_and_handles_relocated_root():
    old, new = _inputs()
    saved = copy.deepcopy(old)
    result = _verify(old, new)
    assert result["modified_files"] == sorted(MODIFIED)
    assert result["added_files"] == sorted(ADDED)
    assert result["unchanged_file_count"] == 2
    assert old == saved


@pytest.mark.parametrize("tamper", ["oracle", "locator", "missing", "unexpected", "not_added", "not_modified"])
def test_unrelated_or_incomplete_runtime_changes_rejected(tamper):
    old, new = _inputs()
    if tamper == "oracle":
        new["tehm/rtl/rtl_oracle.py"] = "changed-physical-outcome-oracle"
    elif tamper == "locator":
        new["tehm/assets/guard_binding.py"] = "changed-after-baseline-locator"
    elif tamper == "missing":
        new.pop("tehm/rtl/rtl_oracle.py")
    elif tamper == "unexpected":
        new["tehm/new_unpinned.py"] = "extra"
    elif tamper == "not_added":
        new.pop(next(iter(ADDED)))
    else:
        key = next(iter(MODIFIED))
        new[key] = old[key]
    with pytest.raises(ValueError, match="exact selector-only"):
        _verify(old, new)


@pytest.mark.parametrize("tamper", ["hash", "missing", "extra"])
def test_git_origin_requires_full_exact_coverage(tamper):
    old, new = _inputs()
    git_files = copy.deepcopy(old)
    if tamper == "hash":
        git_files["tehm/rtl/rtl_oracle.py"] = "forged"
    elif tamper == "missing":
        git_files.pop("tehm/rtl/rtl_oracle.py")
    else:
        git_files["tehm/extra.py"] = "unbound-origin-file"
    with pytest.raises(ValueError, match="Git origin"):
        _verify(old, new, git_files)


@pytest.mark.parametrize("path", ["relative.py", "/recorded/memory/../escape.py", "/other/runtime.py"])
def test_origin_paths_cannot_escape_recorded_root(path):
    old, new = _inputs()
    origin = _binding(old, "/recorded/memory")
    origin["files"][0]["path"] = path
    origin["binding_digest"] = _digest({k: v for k, v in origin.items() if k != "binding_digest"})
    with pytest.raises(ValueError):
        verify_epoch_maps(origin, _binding(new, "/relocated/memory"), old,
            origin_root="/recorded/memory", current_root="/relocated/memory")


def test_duplicate_origin_files_rejected_even_with_recomputed_self_digest():
    old, new = _inputs()
    origin = _binding(old, "/recorded/memory")
    origin["files"].append(copy.deepcopy(origin["files"][0]))
    origin["binding_digest"] = _digest({k: v for k, v in origin.items() if k != "binding_digest"})
    with pytest.raises(ValueError, match="duplicate"):
        verify_epoch_maps(origin, _binding(new, "/relocated/memory"), old,
            origin_root="/recorded/memory", current_root="/relocated/memory")


@pytest.mark.parametrize("commit", ["HEAD", "33acb02", "--help", "a" * 39, "g" * 40])
def test_git_origin_requires_full_immutable_commit(commit, tmp_path):
    with pytest.raises(ValueError, match="full commit"):
        _git(tmp_path, commit, "rev-parse", commit)


@pytest.mark.parametrize("tamper", ["bridge_status", "bridge_version", "controlled_version", "cold_status",
    "cold_version", "cold_source", "cold_count", "cold_check"])
def test_strict_shadow_review_rejects_missing_or_relabelled_origin_evidence(tmp_path, monkeypatch, tamper):
    import scripts.audit_r3_capability_gap_shadow_authority as audit
    bridge = {"version": "r3-capability-gap-runtime-origin-bridge-v1", "status": "PASS"}
    controlled = {"version": "r3-source-bound-controlled-gap-shadow-add-v2"}
    controlled["report_digest"] = _digest(controlled)
    cold = {"version": "r3-source-bound-controlled-gap-cold-replay-v1", "status": "PASS",
        "source_report_digest": controlled["report_digest"], "checks": {str(i): True for i in range(19)}}
    if tamper.startswith("bridge_"):
        bridge[tamper.removeprefix("bridge_")] = "tampered"
    elif tamper == "controlled_version":
        controlled["version"] = "uncontrolled-l1"
    elif tamper in {"cold_status", "cold_version"}:
        cold[tamper.removeprefix("cold_")] = "tampered"
    elif tamper == "cold_source":
        cold["source_report_digest"] = "wrong-source"
    elif tamper == "cold_count":
        cold["checks"].pop("0")
    else:
        cold["checks"]["0"] = False
    for report in (bridge, controlled, cold):
        report["report_digest"] = _digest({k: v for k, v in report.items() if k != "report_digest"})
    reports = {"bridge": bridge, "controlled": controlled, "cold": cold}
    monkeypatch.setattr(audit, "_load_json", lambda path, name: reports[path.name])
    output = tmp_path / "must-not-exist.json"
    with pytest.raises(ValueError, match="exact historical"):
        audit_shadow_authority(tmp_path / "bridge", tmp_path / "controlled", tmp_path / "cold", output=output)
    assert not output.exists()


def test_strict_shadow_review_never_overwrites_consumed_output(tmp_path):
    output = tmp_path / "consumed.json"
    output.write_text("retained evidence")
    with pytest.raises(ValueError, match="must be new"):
        audit_shadow_authority(tmp_path / "absent", tmp_path / "absent2", tmp_path / "absent3", output=output)
    assert output.read_text() == "retained evidence"
