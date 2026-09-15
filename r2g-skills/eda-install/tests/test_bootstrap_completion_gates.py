"""Real bootstrap shell with isolated phase stubs; never installs EDA tools."""
import json
import hashlib
import os
from pathlib import Path
import subprocess

import pytest

EDA = Path(__file__).resolve().parents[1]


def fixture_tree(tmp_path):
    collection = tmp_path / "skills"
    skill = collection / "eda-install"
    setup, flow = skill / "scripts/setup", skill / "scripts/flow"
    setup.mkdir(parents=True)
    flow.mkdir()
    (skill / "bootstrap.sh").write_text((EDA / "bootstrap.sh").read_text())
    (setup / "resolve_pins.sh").write_text("printf 'SELECTION_SOURCE=autodetect\\n'\n")
    (setup / "detect_env.sh").write_text(
        "printf '%s\\n' 'HAVE_SUDO=0' 'PKG_MGR=none' 'OS_FAMILY=fixture' "
        f"'BIG_VOLUME={tmp_path}' 'BIG_VOLUME_FREE_GB=100' "
        f"'ORFS_ROOT={tmp_path}/orfs' 'OPENROAD_EXE={tmp_path}/tools/openroad' "
        f"'YOSYS_EXE={tmp_path}/tools/yosys'\n")
    (setup / "write_env_local.sh").write_text(
        'printf "pin\\n" >> "$PHASE_LOG"\nexit "$PIN_TEST_RC"\n')
    (flow / "check_env.sh").write_text(
        'printf "verify\\n" >> "$PHASE_LOG"\nexit "$VERIFY_TEST_RC"\n')
    (collection / "install.sh").write_text(
        'printf "deploy\\n" >> "$PHASE_LOG"\nexit "$DEPLOY_TEST_RC"\n')
    return skill


def run_bootstrap(skill, *, pin_rc=0, verify_rc=0, deploy_rc=0, extra=()):
    env = dict(os.environ, PIN_TEST_RC=str(pin_rc), VERIFY_TEST_RC=str(verify_rc),
               DEPLOY_TEST_RC=str(deploy_rc), PHASE_LOG=str(skill / "phases.log"),
               R2G_ENV_FILE="", ORFS_ROOT="", PDK_ROOT="", R2G_STRICT_PLATFORMS="",
               R2G_TOOLCHAIN_ROOT=str(skill / "empty-tools"))
    return subprocess.run(["bash", str(skill / "bootstrap.sh"), "--direct", "--yes",
                           "--tiers", "core", *extra], env=env, capture_output=True, text=True)


@pytest.mark.parametrize("pin_rc,verify_rc", [(0, 0), (7, 0), (0, 9), (7, 9)])
def test_required_pin_and_verify_codes_determine_terminal_result(tmp_path, pin_rc, verify_rc):
    skill = fixture_tree(tmp_path)
    result = run_bootstrap(skill, pin_rc=pin_rc, verify_rc=verify_rc)
    failed = bool(pin_rc or verify_rc)
    assert result.returncode == int(failed), result.stdout + result.stderr
    manifest = json.loads((skill / "references/install_manifest.json").read_text())
    assert manifest["install_rc"] == 0
    assert manifest["pin_rc"] == pin_rc
    assert manifest["verify_rc"] == verify_rc
    assert manifest["deploy_rc"] == 0
    assert manifest["bootstrap_rc"] == int(failed)
    assert (skill / "phases.log").read_text().splitlines() == ["pin", "verify"]


@pytest.mark.parametrize("missing", ["pin", "verify"])
def test_missing_required_phase_script_cannot_report_success(tmp_path, missing):
    skill = fixture_tree(tmp_path)
    path = (skill / "scripts/setup/write_env_local.sh" if missing == "pin"
            else skill / "scripts/flow/check_env.sh")
    path.unlink()
    result = run_bootstrap(skill)
    assert result.returncode != 0
    manifest = json.loads((skill / "references/install_manifest.json").read_text())
    assert manifest[missing + "_rc"] != 0 and manifest["bootstrap_rc"] != 0


@pytest.mark.parametrize("deploy_rc", [0, 11])
def test_explicit_deploy_is_an_observed_requested_phase(tmp_path, deploy_rc):
    skill = fixture_tree(tmp_path)
    result = run_bootstrap(skill, deploy_rc=deploy_rc, extra=("--deploy",))
    assert result.returncode == int(bool(deploy_rc))
    manifest = json.loads((skill / "references/install_manifest.json").read_text())
    assert manifest["deploy_rc"] == deploy_rc
    assert manifest["bootstrap_rc"] == int(bool(deploy_rc))
    assert (skill / "phases.log").read_text().splitlines() == ["pin", "verify", "deploy"]


def test_required_manifest_write_failure_is_not_a_success(tmp_path):
    skill = fixture_tree(tmp_path)
    (skill / "references/install_manifest.json").mkdir(parents=True)
    result = run_bootstrap(skill)
    assert result.returncode != 0
    assert "could not write required install_manifest.json" in result.stderr


def test_dry_run_skips_install_pin_and_verify(tmp_path):
    skill = fixture_tree(tmp_path)
    result = run_bootstrap(skill, pin_rc=7, verify_rc=9, extra=("--dry-run",))
    assert result.returncode == 0
    assert not (skill / "phases.log").exists()
    assert not (skill / "references/install_manifest.json").exists()


def test_installer_failure_is_not_cleared_by_successful_later_phases(tmp_path):
    skill = fixture_tree(tmp_path)
    (skill / "scripts/setup/install_frontend.sh").write_text(
        'test "$R2G_DIRECT" = 1 || exit 3\n'
        'printf "install\\n" >> "$PHASE_LOG"\nexit 13\n')
    result = run_bootstrap(skill, extra=("--tiers", "frontend"))
    assert result.returncode == 1
    manifest = json.loads((skill / "references/install_manifest.json").read_text())
    assert manifest["install_rc"] == 1 and manifest["bootstrap_rc"] == 1
    assert manifest["pin_rc"] == manifest["verify_rc"] == 0
    assert (skill / "phases.log").read_text().splitlines() == ["install", "pin", "verify"]


def test_broken_metadata_python_environment_cannot_report_success(tmp_path):
    skill = fixture_tree(tmp_path)
    # A portable launcher may repair PYTHONHOME before starting its payload.
    # Force this negative control to the system interpreter, rather than assume
    # that the first python3 on PATH fails merely because an env var is invalid.
    broken_bin = skill / "broken-python-bin"
    broken_bin.mkdir()
    python = broken_bin / "python3"
    python.write_text('#!/bin/sh\nexec /usr/bin/python3 "$@"\n')
    python.chmod(0o755)
    env = dict(os.environ, PIN_TEST_RC="0", VERIFY_TEST_RC="0", DEPLOY_TEST_RC="0",
               PHASE_LOG=str(skill / "phases.log"), R2G_ENV_FILE="", ORFS_ROOT="",
               PDK_ROOT="", R2G_STRICT_PLATFORMS="", PYTHONHOME=str(skill / "missing-python-home"),
               PATH=str(broken_bin) + os.pathsep + os.environ["PATH"])
    result = subprocess.run(["bash", str(skill / "bootstrap.sh"), "--direct", "--yes",
                             "--tiers", "core"], env=env, capture_output=True, text=True)
    assert result.returncode == 1
    assert "could not write required install_manifest.json" in result.stderr
    assert not (skill / "references/install_manifest.json").exists()


def test_manifest_records_current_pin_digest_not_pre_pin_digest(tmp_path):
    skill = fixture_tree(tmp_path)
    (skill / "scripts/setup/resolve_pins.sh").write_text(
        (EDA / "scripts/setup/resolve_pins.sh").read_text())
    orfs = skill.parent / "verified-orfs"
    (orfs / "flow").mkdir(parents=True)
    (orfs / "flow/Makefile").write_text("# fixture checkout\n")
    pins = []
    for consumer in ("signoff-loop", "def-graph"):
        pin = skill.parent / consumer / "references/env.local.sh"
        pin.parent.mkdir(parents=True)
        pin.write_text(f'export ORFS_ROOT="{orfs}"\n# pre-pin\n')
        pins.append(pin)
    before = hashlib.sha256(pins[0].read_bytes()).hexdigest()
    (skill / "scripts/setup/write_env_local.sh").write_text(
        'printf "pin\\n" >> "$PHASE_LOG"\n' +
        ''.join(f"printf '%s\\n' 'export ORFS_ROOT=\"{orfs}\"' '# post-pin' > '{pin}'\n"
                for pin in pins))
    result = run_bootstrap(skill)
    assert result.returncode == 0, result.stdout + result.stderr
    manifest = json.loads((pins[0].parent / "install_manifest.json").read_text())
    after = hashlib.sha256(pins[0].read_bytes()).hexdigest()
    assert after != before
    assert manifest["selection_source"] == "deployed_pins"
    assert manifest["env_file_sha256"] == after
    assert manifest["plan_env_file_sha256"] == before
    assert manifest["orfs_root"] == str(orfs)
    assert manifest["final_pin_resolution_rc"] == 0


@pytest.mark.parametrize("resolver_rc", [None, 8])
def test_unavailable_initial_resolver_fails_before_any_write_phase(tmp_path, resolver_rc):
    skill = fixture_tree(tmp_path)
    resolver = skill / "scripts/setup/resolve_pins.sh"
    if resolver_rc is None:
        resolver.unlink()
    else:
        resolver.write_text(f"exit {resolver_rc}\n")
    result = run_bootstrap(skill)
    assert result.returncode != 0
    assert not (skill / "phases.log").exists()
    assert not (skill / "references/install_manifest.json").exists()


def test_failed_final_pin_resolution_cannot_report_success(tmp_path):
    skill = fixture_tree(tmp_path)
    counter = skill / "resolution-count"
    (skill / "scripts/setup/resolve_pins.sh").write_text(
        f"if [ -f '{counter}' ]; then printf 'SELECTION_SOURCE=conflict\\n'; exit 4; fi\n"
        f"touch '{counter}'\nprintf 'SELECTION_SOURCE=autodetect\\n'; exit 3\n")
    result = run_bootstrap(skill)
    assert result.returncode != 0
    manifest = json.loads((skill / "references/install_manifest.json").read_text())
    assert manifest["final_pin_resolution_rc"] == 4
    assert manifest["bootstrap_rc"] != 0


def test_requested_deploy_does_not_write_after_failed_provisioning(tmp_path):
    skill = fixture_tree(tmp_path)
    result = run_bootstrap(skill, pin_rc=7, extra=("--deploy",))
    assert result.returncode != 0
    assert (skill / "phases.log").read_text().splitlines() == ["pin", "verify"]
    manifest = json.loads((skill / "references/install_manifest.json").read_text())
    assert manifest["deploy_rc"] != 0 and manifest["bootstrap_rc"] != 0


def test_pin_drift_after_resolution_rejects_terminal_manifest(tmp_path):
    skill = fixture_tree(tmp_path)
    pin = skill / "selected.env.local.sh"
    pin.write_text("# original pin\n")
    (skill / "scripts/setup/resolve_pins.sh").write_text(
        f"printf '%s\\n' 'SELECTION_SOURCE=autodetect' 'SELECTED_ENV_FILE={pin}'\n"
        f"printf 'SELECTED_ENV_SHA256=%s\\n' \"$(sha256sum '{pin}' | cut -d' ' -f1)\"\n")
    (skill / "scripts/flow/check_env.sh").write_text(
        f"printf '# drift after resolution\\n' > '{pin}'\nexit 0\n")
    result = run_bootstrap(skill)
    assert result.returncode != 0
    assert not (skill / "install_manifest.json").exists()
    assert "could not write required install_manifest.json" in result.stderr
