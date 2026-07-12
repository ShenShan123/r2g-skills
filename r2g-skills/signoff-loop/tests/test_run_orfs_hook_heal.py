"""Regression tests for run_orfs.sh's stale stage-hook self-heal.

failure-patterns #39 (2026-07-12): the skill tree moved r2g-rtl2gds/ ->
r2g-skills/signoff-loop/ (2026-07-07 split), orphaning the ABSOLUTE
`export POST_GLOBAL_PLACE_TCL = .../r2g-rtl2gds/.../buffer_port_feedthroughs.tcl`
path baked into config.mk. Primary designs were regenerated with the new path, but
84 pre-split A/B-arm config.mk copies were not — so when ab-drain re-ran those arms,
ORFS's `source` of the hook aborted global place ("couldn't read file ... no such
file or directory") and the loop mislabeled the abort 'unseen_crash'. An arm that
dies on a dead hook never diverges, silently starving the pdn_die A/B evidence.

run_orfs.sh now self-heals: any `*_TCL` hook whose file is MISSING is repointed to
the same-basename file under the script's canonical orfs_hooks/ dir; a VALID path is
left untouched. These tests drive the heal in isolation via the
`R2G_SELFTEST_HEAL_HOOKS`/`R2G_ORFS_HOOKS_DIR` hooks (heal one config.mk, then exit).
"""
import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
RUN_ORFS = REPO / "r2g-skills" / "signoff-loop" / "scripts" / "flow" / "run_orfs.sh"


def _heal(config_mk: Path, hooks_dir: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update(
        R2G_SELFTEST_HEAL_HOOKS=str(config_mk),
        R2G_ORFS_HOOKS_DIR=str(hooks_dir),
    )
    return subprocess.run(
        ["bash", str(RUN_ORFS)], cwd=REPO, env=env,
        capture_output=True, text=True, timeout=60,
    )


def _hooks_dir(tmp_path: Path) -> Path:
    d = tmp_path / "orfs_hooks"
    d.mkdir()
    (d / "buffer_port_feedthroughs.tcl").write_text("# canonical hook\n")
    return d


def test_dead_hook_path_repointed_to_canonical(tmp_path):
    """The exact #39 scenario: a dead r2g-rtl2gds path repoints to the live same-basename hook."""
    hooks = _hooks_dir(tmp_path)
    cfg = tmp_path / "config.mk"
    stale = "/proj/workarea/user5/agent-r2g/r2g-rtl2gds/scripts/flow/orfs_hooks/buffer_port_feedthroughs.tcl"
    cfg.write_text(
        "export PLATFORM = sky130hs\n"
        f"export POST_GLOBAL_PLACE_TCL = {stale}\n"
        "export VERILOG_FILES = /abs/design.v\n"
    )
    p = _heal(cfg, hooks)
    assert p.returncode == 0, p.stderr
    healed = cfg.read_text()
    assert f"POST_GLOBAL_PLACE_TCL = {hooks / 'buffer_port_feedthroughs.tcl'}" in healed, healed
    assert "r2g-rtl2gds" not in healed, healed
    assert "healed stale hook path" in p.stderr, p.stderr


def test_valid_hook_path_left_untouched(tmp_path):
    """A hook path that EXISTS is conservative-preserved even if a same-basename lives in orfs_hooks/."""
    hooks = _hooks_dir(tmp_path)
    live = tmp_path / "custom" / "buffer_port_feedthroughs.tcl"
    live.parent.mkdir()
    live.write_text("# a real, existing hook elsewhere\n")
    cfg = tmp_path / "config.mk"
    cfg.write_text(f"export POST_GLOBAL_PLACE_TCL = {live}\n")
    p = _heal(cfg, hooks)
    assert p.returncode == 0, p.stderr
    assert f"POST_GLOBAL_PLACE_TCL = {live}" in cfg.read_text()
    assert "healed" not in p.stderr, p.stderr


def test_dead_path_with_no_canonical_match_warns_not_repoints(tmp_path):
    """A dead path whose basename is absent from orfs_hooks/ must NOT be silently blanked."""
    hooks = _hooks_dir(tmp_path)  # only has buffer_port_feedthroughs.tcl
    cfg = tmp_path / "config.mk"
    missing = "/gone/orfs_hooks/some_other_hook.tcl"
    cfg.write_text(f"export POST_ROUTE_TCL = {missing}\n")
    p = _heal(cfg, hooks)
    assert p.returncode == 0, p.stderr
    assert missing in cfg.read_text()               # left as-is (loudly, not repointed to nothing)
    assert "WARNING stale hook path" in p.stderr, p.stderr


def test_non_hook_lines_untouched(tmp_path):
    """Only *_TCL lines are considered; other config vars pass through verbatim."""
    hooks = _hooks_dir(tmp_path)
    cfg = tmp_path / "config.mk"
    body = (
        "export PLATFORM = sky130hs\n"
        "export DIE_AREA = 0 0 200 200\n"
        "export VERILOG_FILES = /abs/r2g-rtl2gds-named/design.v\n"  # 'r2g-rtl2gds' substring but NOT a _TCL
    )
    cfg.write_text(body)
    p = _heal(cfg, hooks)
    assert p.returncode == 0, p.stderr
    assert cfg.read_text() == body, cfg.read_text()


def test_default_hooks_dir_resolves_real_canonical(tmp_path):
    """Without R2G_ORFS_HOOKS_DIR, HOOKS_DIR must resolve from the script's own location
    to the REAL orfs_hooks/ dir — the production path (driver-spawned clean bash). Proves
    the fix works even though an interactive shell's `cd`-that-lists hook is absent there."""
    real_hook = (REPO / "r2g-skills" / "signoff-loop" / "scripts" / "flow"
                 / "orfs_hooks" / "buffer_port_feedthroughs.tcl")
    assert real_hook.is_file(), "canonical hook missing from the repo"
    cfg = tmp_path / "config.mk"
    cfg.write_text(
        "export POST_GLOBAL_PLACE_TCL = /gone/r2g-rtl2gds/scripts/flow/orfs_hooks/buffer_port_feedthroughs.tcl\n"
    )
    env = dict(os.environ)
    env.pop("R2G_ORFS_HOOKS_DIR", None)          # force default BASH_SOURCE-based resolution
    env["R2G_SELFTEST_HEAL_HOOKS"] = str(cfg)
    p = subprocess.run(
        ["bash", str(RUN_ORFS)], cwd=REPO, env=env,
        capture_output=True, text=True, timeout=60,
    )
    assert p.returncode == 0, p.stderr
    assert f"POST_GLOBAL_PLACE_TCL = {real_hook}" in cfg.read_text(), cfg.read_text()
    assert "r2g-rtl2gds" not in cfg.read_text()


def test_heal_is_idempotent(tmp_path):
    """Running the heal twice is stable (a healed path stays healed, no churn)."""
    hooks = _hooks_dir(tmp_path)
    cfg = tmp_path / "config.mk"
    cfg.write_text(
        "export POST_GLOBAL_PLACE_TCL = /gone/r2g-rtl2gds/orfs_hooks/buffer_port_feedthroughs.tcl\n"
    )
    _heal(cfg, hooks)
    first = cfg.read_text()
    p2 = _heal(cfg, hooks)
    assert cfg.read_text() == first, "second heal changed an already-healed config"
    assert "healed" not in p2.stderr, p2.stderr
