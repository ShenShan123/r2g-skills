"""Replay tests for the content-addressed ORFS toolchain lock."""
from __future__ import annotations

import json
import subprocess
import sys
import pytest
from pathlib import Path

from scripts.run_orfs_diversity_campaign import preflight_orfs_toolchain
from tehm.orfs_toolchain import (
    ToolchainManifestError,
    build_toolchain_manifest,
    manifest_digest,
    validate_toolchain_manifest,
)


def _fake_orfs(root: Path) -> tuple[Path, Path, Path]:
    (root / "flow" / "scripts").mkdir(parents=True)
    (root / "flow" / "Makefile").write_text("all:\n")
    (root / "flow" / "scripts" / "synth_canonicalize.tcl").write_text("")
    openroad = root / "tools" / "install" / "OpenROAD" / "bin" / "openroad"
    yosys = root / "tools" / "install" / "yosys" / "bin" / "yosys"
    for path, output in ((openroad, "OpenROAD test"),
                         (yosys, "Yosys test")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"#!/bin/sh\necho {output}\n")
        path.chmod(0o755)
    return root, openroad, yosys


def test_manifest_round_trip_binds_every_tool_field(tmp_path):
    root, _openroad, _yosys = _fake_orfs(tmp_path / "orfs")
    report = preflight_orfs_toolchain({"orfs_root": str(root)}, env={})
    assert report["status"] == "bound_internal"
    manifest = build_toolchain_manifest(report)
    assert manifest["schema"] == "tehm-orfs-toolchain-manifest-v1"
    assert manifest["manifest_digest"] == manifest_digest(manifest)
    replay = validate_toolchain_manifest(manifest, report)
    assert replay["valid"], replay


@pytest.mark.parametrize("change", ["bytes", "missing", "symlink"])
def test_explicit_dependency_drift_rejected(tmp_path, change):
    root, _, _ = _fake_orfs(tmp_path / "orfs")
    report = preflight_orfs_toolchain({"orfs_root": str(root)}, env={})
    library = tmp_path / "cells.spice"
    library.write_text("original library")
    manifest = build_toolchain_manifest(report, dependency_files=(library,))
    assert validate_toolchain_manifest(manifest, report)["valid"]
    if change == "bytes":
        library.write_text("changed library")
    else:
        library.unlink()
        if change == "symlink":
            target = tmp_path / "other.spice"
            target.write_text("original library")
            library.symlink_to(target)
    result = validate_toolchain_manifest(manifest, report)
    assert not result["valid"]
    assert any("dependency file" in reason for reason in result["reasons"])


def test_missing_dependency_refused_at_record_time(tmp_path):
    root, _, _ = _fake_orfs(tmp_path / "orfs")
    report = preflight_orfs_toolchain({"orfs_root": str(root)}, env={})
    with pytest.raises(ToolchainManifestError, match="dependency file"):
        build_toolchain_manifest(report, dependency_files=(tmp_path / "missing",))


def test_user_prefix_binaries_are_internal_when_explicitly_pinned(tmp_path):
    root, _openroad, _yosys = _fake_orfs(tmp_path / "orfs")
    prefix = tmp_path / "tehm-toolchain"
    bindir = prefix / "miniconda3" / "envs" / "eda" / "bin"
    bindir.mkdir(parents=True)
    binaries = {}
    for name in ("openroad", "yosys"):
        path = bindir / name
        path.write_text(f"#!/bin/sh\necho {name} user\n")
        path.chmod(0o755)
        binaries[name] = path
    report = preflight_orfs_toolchain(
        {"orfs_root": str(root)},
        env={"R2G_PREFIX": str(prefix),
             "OPENROAD_EXE": str(binaries["openroad"]),
             "YOSYS_EXE": str(binaries["yosys"])})
    assert report["status"] == "bound_internal"
    assert report["tools"]["openroad"]["source"] == "r2g_prefix"
    manifest = build_toolchain_manifest(report, require_internal=True)
    assert validate_toolchain_manifest(manifest, report)["valid"]
    locked_path = tmp_path / "user-prefix-lock.json"
    locked_path.write_text(json.dumps(manifest) + "\n")
    replay = preflight_orfs_toolchain(
        {"orfs_root": str(root), "toolchain_manifest": str(locked_path)})
    assert replay["status"] == "bound_internal", replay
    assert replay["manifest_validation"]["valid"] is True


def test_toolchain_preflight_labels_mixed_internal_sources(tmp_path):
    """A prefix OpenROAD plus ORFS-packaged Yosys is visible as mixed."""
    root = tmp_path / "orfs"
    (root / "flow" / "scripts").mkdir(parents=True)
    (root / "flow" / "Makefile").write_text("all:\n")
    (root / "flow" / "scripts" / "synth_canonicalize.tcl").write_text("# probe\n")
    packaged_yosys = root / "tools" / "install" / "yosys" / "bin" / "yosys"
    packaged_yosys.parent.mkdir(parents=True)
    packaged_yosys.write_text(
        "#!/bin/sh\n"
        "case \"$*\" in\n"
        "  *-V*) echo 'Yosys 0.65' ;;\n"
        "  *) echo '-unit_delay' ;;\n"
        "esac\n"
    )
    packaged_yosys.chmod(0o755)
    prefix = tmp_path / "prefix"
    openroad = prefix / "miniconda3" / "envs" / "eda" / "bin" / "openroad"
    openroad.parent.mkdir(parents=True)
    openroad.write_text("#!/bin/sh\necho openroad\n")
    openroad.chmod(0o755)
    report = preflight_orfs_toolchain(
        {"orfs_root": str(root)},
        env={"R2G_PREFIX": str(prefix), "OPENROAD_EXE": str(openroad)},
    )
    assert report["status"] == "bound_internal", report
    assert report["compatibility"] == "mixed_internal"


def test_manifest_replay_rejects_binary_replacement(tmp_path):
    root, openroad, _yosys = _fake_orfs(tmp_path / "orfs")
    report = preflight_orfs_toolchain({"orfs_root": str(root)}, env={})
    manifest = build_toolchain_manifest(report)
    openroad.write_text("#!/bin/sh\necho OpenROAD replacement\n")
    changed = preflight_orfs_toolchain({"orfs_root": str(root)}, env={})
    result = validate_toolchain_manifest(manifest, changed)
    assert not result["valid"]
    assert any("openroad sha256" in reason for reason in result["reasons"])


def test_campaign_preflight_blocks_manifest_drift(tmp_path):
    root, openroad, _yosys = _fake_orfs(tmp_path / "orfs")
    report = preflight_orfs_toolchain({"orfs_root": str(root)}, env={})
    manifest_path = tmp_path / "toolchain.json"
    manifest_path.write_text(json.dumps(build_toolchain_manifest(report)) + "\n")
    openroad.write_text("#!/bin/sh\necho drift\n")
    changed = preflight_orfs_toolchain(
        {"orfs_root": str(root), "toolchain_manifest": str(manifest_path)},
        env={})
    assert changed["status"] == "blocked"
    assert "toolchain manifest" in changed["error"]


def test_campaign_preflight_accepts_environment_manifest_pin(tmp_path, monkeypatch):
    root, _openroad, _yosys = _fake_orfs(tmp_path / "orfs")
    report = preflight_orfs_toolchain({"orfs_root": str(root)}, env={})
    manifest_path = tmp_path / "toolchain.json"
    manifest_path.write_text(json.dumps(build_toolchain_manifest(report)) + "\n")
    monkeypatch.setenv("R2G_TOOLCHAIN_MANIFEST", str(manifest_path))
    replay = preflight_orfs_toolchain({"orfs_root": str(root)})
    assert replay["status"] == "bound_internal"
    assert replay["manifest_validation"]["valid"] is True


def test_manifest_digest_tampering_and_external_policy_fail_closed(tmp_path):
    root, _openroad, _yosys = _fake_orfs(tmp_path / "orfs")
    report = preflight_orfs_toolchain({"orfs_root": str(root)}, env={})
    manifest = build_toolchain_manifest(report, require_internal=True)
    tampered = json.loads(json.dumps(manifest))
    tampered["tools"]["yosys"]["version"] = "Yosys forged"
    result = validate_toolchain_manifest(tampered, report)
    assert not result["valid"]
    assert any("digest mismatch" in reason for reason in result["reasons"])
    external = dict(report, status="bound_external")
    try:
        build_toolchain_manifest(external, require_internal=True)
    except ToolchainManifestError as exc:
        assert "bound_internal" in str(exc)
    else:  # pragma: no cover - assertion keeps the policy explicit
        raise AssertionError("external binding must not satisfy internal policy")


def test_record_and_check_cli_use_one_manifest(tmp_path):
    root, _openroad, _yosys = _fake_orfs(tmp_path / "orfs")
    output = tmp_path / "locked.json"
    script = Path(__file__).resolve().parents[1] / "scripts" / "record_orfs_toolchain_manifest.py"
    record = subprocess.run(
        [sys.executable, str(script), "record", "--orfs-root", str(root),
         "--output", str(output)], capture_output=True, text=True, check=False)
    assert record.returncode == 0, record.stdout + record.stderr
    check = subprocess.run(
        [sys.executable, str(script), "check", "--manifest", str(output)],
        capture_output=True, text=True, check=False)
    assert check.returncode == 0, check.stdout + check.stderr
    assert json.loads(check.stdout)["valid"] is True
