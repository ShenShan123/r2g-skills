"""Repeat bootstrap must resolve deployed pins deterministically (RMD4-P1-01).

Three-platform fixed pilot 2026-07-27: a plain `bootstrap.sh` with no explicit
$R2G_ENV_FILE selected `/opt/OpenROAD-flow-scripts` and an ambient PDK even though
BOTH deployed consumer skills already pinned a different ORFS and a conda PDK —
the ones every production flow used. It then attempted strict platform-rule
installation in that wrong, read-only checkout and failed.

Root cause: eda-install has no `references/env.local.sh` of its own, so its copy of
_env.sh finds no pin at resolution step 3 and falls through to the hardcoded
candidate list (where /opt sorts before /proj); `write_env_local.sh` recalls the
consumer pins only at the PIN step, far too late to make plan or install idempotent.

The acceptance conditions from the remediation plan, in order:
  * two runs with unchanged valid pins select the same ORFS/PDK;
  * an unrelated valid checkout cannot displace the agreed pin;
  * conflicting consumer pins fail closed BEFORE installation;
  * an explicit operator selection overrides pins only when clearly reported.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

EDA_ROOT = Path(__file__).resolve().parents[1]
RESOLVE = EDA_ROOT / "scripts" / "setup" / "resolve_pins.sh"

CONSUMERS = ("signoff-loop", "def-graph", "rtl-acquire")


def _fake_orfs(root: Path, name: str) -> Path:
    """A checkout valid enough for the resolver: it has flow/Makefile."""
    d = root / name
    (d / "flow").mkdir(parents=True)
    (d / "flow" / "Makefile").write_text(f"# {name}\n")
    return d


def _skills_tree(tmp_path: Path, pins: dict[str, tuple[Path | None, Path | None]]) -> Path:
    """A minimal r2g-skills tree: eda-install/scripts/setup/resolve_pins.sh plus
    each consumer's references/env.local.sh."""
    skills = tmp_path / "r2g-skills"
    setup = skills / "eda-install" / "scripts" / "setup"
    setup.mkdir(parents=True)
    (setup / "resolve_pins.sh").write_text(RESOLVE.read_text())
    for consumer in CONSUMERS:
        refs = skills / consumer / "references"
        refs.mkdir(parents=True)
        if consumer not in pins:
            continue
        orfs, pdk = pins[consumer]
        lines = []
        if orfs is not None:
            lines.append(f'export ORFS_ROOT="{orfs}"')
        if pdk is not None:
            lines.append(f'export PDK_ROOT="{pdk}"')
        (refs / "env.local.sh").write_text("\n".join(lines) + "\n")
    return skills


def _resolve(skills: Path, env: dict | None = None) -> tuple[int, dict]:
    res = subprocess.run(
        ["bash", str(skills / "eda-install" / "scripts" / "setup" / "resolve_pins.sh")],
        capture_output=True, text=True, check=False, env=env)
    out = {}
    for line in res.stdout.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            out[k] = v
    return res.returncode, out


@pytest.fixture()
def staged(tmp_path):
    """Two valid ORFS checkouts; only `pinned` is named by the consumer skills —
    `other` is the /opt-style decoy that used to win."""
    pinned = _fake_orfs(tmp_path, "pinned_ORFS")
    other = _fake_orfs(tmp_path, "other_ORFS")
    pdk = tmp_path / "pdk"
    (pdk / "sky130A").mkdir(parents=True)
    return pinned, other, pdk


def test_agreeing_pins_are_selected(staged, tmp_path):
    pinned, _other, pdk = staged
    skills = _skills_tree(tmp_path, {c: (pinned, pdk) for c in CONSUMERS})
    rc, sel = _resolve(skills)
    assert rc == 0
    assert sel["SELECTION_SOURCE"] == "deployed_pins"
    assert sel["SELECTED_ORFS_ROOT"] == str(pinned)
    assert sel["SELECTED_PDK_ROOT"] == str(pdk)
    assert sel["PIN_CONFLICT"] == "0"
    assert sel["SELECTED_ENV_SHA256"]


def test_selection_is_idempotent_across_runs(staged, tmp_path):
    pinned, _other, pdk = staged
    skills = _skills_tree(tmp_path, {c: (pinned, pdk) for c in CONSUMERS})
    first = _resolve(skills)[1]
    second = _resolve(skills)[1]
    assert first == second, "repeat bootstrap must select the same toolchain"


def test_an_unrelated_valid_checkout_cannot_displace_the_pin(staged, tmp_path):
    """The /opt decoy: another perfectly valid ORFS exists, but it is not pinned."""
    pinned, other, pdk = staged
    skills = _skills_tree(tmp_path, {c: (pinned, pdk) for c in CONSUMERS})
    rc, sel = _resolve(skills)
    assert rc == 0
    assert sel["SELECTED_ORFS_ROOT"] == str(pinned)
    assert str(other) not in sel["SELECTED_ORFS_ROOT"]


def test_conflicting_consumer_pins_fail_closed(staged, tmp_path):
    pinned, other, pdk = staged
    skills = _skills_tree(tmp_path, {
        "signoff-loop": (pinned, pdk),
        "def-graph": (other, pdk),          # a DIFFERENT live installation
        "rtl-acquire": (pinned, pdk),
    })
    rc, sel = _resolve(skills)
    assert rc == 4, "a disagreement between live pins must not be resolved silently"
    assert sel["PIN_CONFLICT"] == "1"
    assert "ORFS_ROOT" in sel["PIN_CONFLICT_DETAIL"]
    assert sel["SELECTED_ORFS_ROOT"] == ""


def test_a_stale_pin_is_not_a_conflict_and_never_selected(staged, tmp_path):
    """A pin to a DELETED tree must not win, and must not manufacture a conflict —
    it is simply not a candidate."""
    pinned, _other, pdk = staged
    skills = _skills_tree(tmp_path, {
        "signoff-loop": (pinned, pdk),
        "def-graph": (tmp_path / "deleted_ORFS", pdk),
        "rtl-acquire": (pinned, pdk),
    })
    rc, sel = _resolve(skills)
    assert rc == 0
    assert sel["PIN_CONFLICT"] == "0"
    assert sel["SELECTED_ORFS_ROOT"] == str(pinned)


def test_explicit_env_file_overrides_and_is_reported(staged, tmp_path):
    pinned, other, pdk = staged
    skills = _skills_tree(tmp_path, {c: (pinned, pdk) for c in CONSUMERS})
    override = tmp_path / "override_env.sh"
    override.write_text(f'export ORFS_ROOT="{other}"\nexport PDK_ROOT="{pdk}"\n')
    import os
    env = dict(os.environ, R2G_ENV_FILE=str(override))
    env.pop("ORFS_ROOT", None)
    env.pop("PDK_ROOT", None)
    rc, sel = _resolve(skills, env=env)
    assert rc == 0
    assert sel["SELECTION_SOURCE"].startswith("explicit:")
    assert sel["SELECTED_ORFS_ROOT"] == str(other)
    assert sel["OVERRIDES_PINS"] == "1", "an override of an agreeing pin must be reported"


def test_no_pins_falls_back_to_autodetect(tmp_path):
    skills = _skills_tree(tmp_path, {})
    rc, sel = _resolve(skills)
    assert rc == 3
    assert sel["SELECTION_SOURCE"] == "autodetect"


def test_bootstrap_dry_run_fails_closed_on_conflicting_pins(staged, tmp_path):
    """The plan itself must not be computed against an ambiguous toolchain — a
    misleading plan is exactly what this defect produced."""
    pinned, other, pdk = staged
    skills = _skills_tree(tmp_path, {
        "signoff-loop": (pinned, pdk),
        "def-graph": (other, pdk),
        "rtl-acquire": (pinned, pdk),
    })
    # Stage the real bootstrap beside the staged setup dir so its SETUP_DIR
    # resolves to the conflicting fixture.
    boot = skills / "eda-install" / "bootstrap.sh"
    boot.write_text((EDA_ROOT / "bootstrap.sh").read_text())
    (skills / "eda-install" / "scripts" / "flow").mkdir(parents=True)
    res = subprocess.run(["bash", str(boot), "--dry-run"],
                         capture_output=True, text=True, check=False)
    assert res.returncode == 4, res.stdout + res.stderr
    assert "disagree" in (res.stdout + res.stderr)


def test_install_manifest_records_the_selection(staged, tmp_path):
    """`install_manifest.json` is the provenance a later run compares against."""
    import os
    pinned, _other, pdk = staged
    skills = _skills_tree(tmp_path, {c: (pinned, pdk) for c in CONSUMERS})
    boot = skills / "eda-install" / "bootstrap.sh"
    boot.write_text((EDA_ROOT / "bootstrap.sh").read_text())
    flow = skills / "eda-install" / "scripts" / "flow"
    flow.mkdir(parents=True)
    (flow / "check_env.sh").write_text("#!/usr/bin/env bash\nexit 0\n")
    # No installers staged -> every tier reports MISS and is skipped as optional;
    # the manifest step still runs, which is what we are asserting.
    res = subprocess.run(["bash", str(boot), "--yes", "--tiers", "graph"],
                         capture_output=True, text=True, check=False,
                         env=dict(os.environ, R2G_ENV_FILE="", ORFS_ROOT="", PDK_ROOT=""))
    manifest = skills / "signoff-loop" / "references" / "install_manifest.json"
    assert manifest.exists(), res.stdout[-3000:] + res.stderr[-2000:]
    rec = json.loads(manifest.read_text())
    assert rec["selection_source"] == "deployed_pins"
    assert rec["orfs_root"] == str(pinned)
    assert rec["orfs_flow_makefile_sha256"]
    assert rec["env_file_sha256"]
