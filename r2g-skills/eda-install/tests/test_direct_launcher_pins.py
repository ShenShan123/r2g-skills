"""Configuration precedence controls, not EDA/oracle capability evidence."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest


EDA = Path(__file__).resolve().parents[1]
SKILLS = EDA.parent
CONSUMERS = ("eda-install", "signoff-loop", "def-graph", "rtl-acquire")


def executable(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    return path


def isolated_skill(tmp_path, consumer):
    skill = tmp_path / "skills" / consumer
    flow = skill / "scripts" / "flow"
    flow.mkdir(parents=True)
    shutil.copy2(SKILLS / consumer / "scripts/flow/_env.sh", flow / "_env.sh")
    (skill / "references").mkdir()
    return skill, flow / "_env.sh"


def environment(tmp_path):
    # Do not change HOME; isolate discovery with explicit inputs and no caller
    # executable pins, Python/conda setup, or shell-startup injection.
    env = {key: os.environ[key] for key in ("HOME", "USER") if key in os.environ}
    env.update(PATH="/usr/bin:/bin", R2G_HERMETIC="1",
               R2G_PREFIX=str(tmp_path / "bundle"),
               R2G_TOOLCHAIN_ROOT=str(tmp_path / "bundle"))
    orfs = tmp_path / "orfs"
    (orfs / "flow").mkdir(parents=True)
    (orfs / "flow/Makefile").write_text("all:\n")
    env["ORFS_ROOT"] = str(orfs)
    return env


def resolve(resolver, env, *variables):
    script = 'source "$1" >/dev/null; shift; for key; do printf "%s\\n" "${!key:-}"; done'
    return subprocess.run(["bash", "-c", script, "pin-control", str(resolver), *variables],
                          env=env, capture_output=True, text=True, check=True,
                          timeout=15).stdout.splitlines()


@pytest.mark.parametrize("consumer", CONSUMERS)
def test_autodetect_prefers_sdk_launchers_over_payloads(tmp_path, consumer):
    _, resolver = isolated_skill(tmp_path, consumer)
    env = environment(tmp_path)
    root = Path(env["R2G_TOOLCHAIN_ROOT"])
    launchers = [executable(root / "openroad-matched/launch_openroad.sh"),
                 executable(root / "yosys/launch_yosys.sh")]
    executable(root / "openroad-matched/bin/openroad")
    executable(root / "yosys/bin/yosys")
    assert resolve(resolver, env, "OPENROAD_EXE", "YOSYS_EXE") == list(map(str, launchers))


@pytest.mark.parametrize("consumer", CONSUMERS)
def test_explicit_env_file_magic_outranks_local_and_bundle(tmp_path, consumer):
    skill, resolver = isolated_skill(tmp_path, consumer)
    env = environment(tmp_path)
    requested = executable(tmp_path / "declared-sdk/magic")
    local = executable(tmp_path / "local-sdk/magic")
    executable(Path(env["R2G_TOOLCHAIN_ROOT"]) / "magic/bin/magic")
    pin = tmp_path / "explicit.env.sh"
    pin.write_text(f'export MAGIC_EXE="{requested}"\n')
    (skill / "references/env.local.sh").write_text(f'export MAGIC_EXE="{local}"\n')
    env["R2G_ENV_FILE"] = str(pin)
    assert resolve(resolver, env, "MAGIC_EXE") == [str(requested)]
    # True caller overrides must retain the documented first precedence.
    env["MAGIC_EXE"] = str(local)
    assert resolve(resolver, env, "MAGIC_EXE") == [str(local)]


def test_hermetic_writer_pins_launchers_not_bare_payloads(tmp_path):
    skill, _ = isolated_skill(tmp_path, "eda-install")
    setup = skill / "scripts/setup"
    setup.mkdir()
    writer = setup / "write_env_local.sh"
    shutil.copy2(EDA / "scripts/setup/write_env_local.sh", writer)
    env = environment(tmp_path)
    root = Path(env["R2G_TOOLCHAIN_ROOT"])
    launchers = [executable(root / "openroad-matched/launch_openroad.sh"),
                 executable(root / "yosys/launch_yosys.sh")]
    payloads = [executable(root / "openroad-matched/bin/openroad"),
                executable(root / "yosys/bin/yosys")]
    refs = tmp_path / "consumer-references"
    refs.mkdir()
    (refs / "env.local.sh").write_text(
        f'export OPENROAD_EXE="{payloads[0]}"\nexport YOSYS_EXE="{payloads[1]}"\n')
    subprocess.run(["bash", str(writer), "--hermetic", "--target", str(refs)],
                   env=env, capture_output=True, text=True, check=True, timeout=15)
    body = (refs / "env.local.sh").read_text()
    for variable, launcher in zip(("OPENROAD_EXE", "YOSYS_EXE"), launchers):
        assert f'export {variable}="{launcher}"' in body


@pytest.mark.parametrize("consumer", CONSUMERS)
def test_explicit_pin_survives_orfs_environment_defaults(tmp_path, consumer):
    skill, resolver = isolated_skill(tmp_path, consumer)
    env = environment(tmp_path)
    requested = executable(tmp_path / "declared-sdk/openroad")
    fallback = executable(tmp_path / "local-sdk/openroad")
    pin = tmp_path / "explicit.env.sh"
    pin.write_text(f'export OPENROAD_EXE="{requested}"\n')
    env["R2G_ENV_FILE"] = str(pin)
    (Path(env["ORFS_ROOT"]) / "env.sh").write_text(f'export OPENROAD_EXE="{fallback}"\n')
    (skill / "references/env.local.sh").write_text(f'export OPENROAD_EXE="{fallback}"\n')
    assert resolve(resolver, env, "OPENROAD_EXE") == [str(requested)]


@pytest.mark.parametrize("consumer", CONSUMERS)
def test_yosys_pipe_width_is_pinned_independently_of_terminal(tmp_path, consumer):
    _, resolver = isolated_skill(tmp_path, consumer)
    env = environment(tmp_path)
    executable(Path(env["R2G_TOOLCHAIN_ROOT"]) / "yosys/launch_yosys.sh")
    env["COLUMNS"] = "40"
    assert resolve(resolver, env, "COLUMNS") == ["8192"]


@pytest.mark.parametrize("direct", ["0", "1"])
def test_direct_detection_does_not_probe_sudo_or_conda(tmp_path, direct):
    skill, _ = isolated_skill(tmp_path, "eda-install")
    setup = skill / "scripts/setup"
    setup.mkdir()
    detect = setup / "detect_env.sh"
    shutil.copy2(EDA / "scripts/setup/detect_env.sh", detect)
    env = environment(tmp_path)
    env["R2G_DIRECT"] = direct
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    log = tmp_path / "sudo-called"
    sudo = executable(fake_bin / "sudo")
    sudo.write_text(f'#!/bin/sh\nprintf "called\\n" > "{log}"\nexit 0\n')
    conda = executable(fake_bin / "conda")
    python = executable(fake_bin / "python3")
    python.write_text("#!/bin/sh\nexit 1\n")
    env["PATH"] = str(fake_bin) + ":/usr/bin:/bin"
    result = subprocess.run(["bash",str(detect)],env=env,capture_output=True,text=True,
                            check=True,timeout=15)
    facts = dict(line.split("=",1) for line in result.stdout.splitlines())
    if direct == "1":
        assert not log.exists()
        assert facts["HAVE_SUDO"] == "0" and facts["HAVE_CONDA"] == ""
    else:
        assert log.exists()
        assert facts["HAVE_SUDO"] == "1" and facts["HAVE_CONDA"] == str(conda)
