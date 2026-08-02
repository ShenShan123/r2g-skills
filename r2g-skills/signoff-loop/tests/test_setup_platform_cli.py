"""setup_rtl_designs CLI must accept the documented SPACE form ``--platform asap7``.

Regression for the 2026-06-30 root cause: the hand-rolled arg parser understood only the
``--platform=asap7`` (equals) form, while SKILL Step 1b, build_pending_ledger.py's header,
and the /r2g-debug command all invoke ``--platform asap7`` (space). The space form fell
through to the positional-design branch -> platform_override=None -> the whole-corpus PDK
re-target became a SILENT no-op and the script exited 0, so an "asap7 round" would have
rebuilt the OLD platform (or built nothing). See references/failure-patterns.md
"Platform re-target CLI mismatch (silent no-op)".
"""
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[3] / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import setup_rtl_designs as srd


# ---- the normalizer (new code carrying the fix) ---------------------------------------

def test_normalize_space_form_to_equals():
    assert srd._normalize_value_flags(["--platform", "asap7", "--force"]) == \
        ["--platform=asap7", "--force"]


def test_normalize_equals_form_unchanged():
    assert srd._normalize_value_flags(["--platform=asap7", "--force"]) == \
        ["--platform=asap7", "--force"]


def test_normalize_positional_design_unchanged():
    # a bare design name must NOT be swallowed as a flag value
    assert srd._normalize_value_flags(["mydesign", "--force"]) == ["mydesign", "--force"]


def test_normalize_trailing_flag_without_value():
    # degenerate misuse: a value flag with nothing after it is left as-is
    assert srd._normalize_value_flags(["--platform"]) == ["--platform"]


# ---- the parse outcome (proves platform_override is actually set) ---------------------

def test_platform_space_form_sets_override():
    # THE bug: the space form must set platform_override (was None before the fix).
    _, _, platform_override = srd.parse_setup_args(["--platform", "asap7", "--force"])
    assert platform_override == "asap7"


def test_platform_equals_form_still_works():
    _, _, platform_override = srd.parse_setup_args(["--platform=nangate45"])
    assert platform_override == "nangate45"


def test_space_form_platform_does_not_become_a_design():
    # before the fix, "asap7" was parsed as the single selected design (selected==["asap7"])
    _, selected, platform_override = srd.parse_setup_args(["--platform", "asap7"])
    assert platform_override == "asap7"
    assert selected != ["asap7"]
    assert selected is None


def test_designs_space_form():
    _, selected, _ = srd.parse_setup_args(["--designs", "a,b,c"])
    assert selected == ["a", "b", "c"]


# ---- unknown-flag / --help rejection (2026-08-01) --------------------------------------
# Sibling of the silent-no-op above: unknown flags were DROPPED in silence, so a typo'd
# `--platfrom gf180 --force` re-pointed all ~708 config.mk to the DEFAULT platform and
# exited 0, and `--help` fell through the same gap and RAN the whole-corpus setup.

def test_unknown_flag_is_rejected():
    import pytest
    with pytest.raises(SystemExit) as e:
        srd.parse_setup_args(["--platfrom=gf180", "--force"])
    assert "--platfrom" in str(e.value)


def test_help_does_not_fall_through_to_setup():
    import pytest
    with pytest.raises(SystemExit) as e:
        srd.parse_setup_args(["--help"])
    assert e.value.code == 0


def test_known_flags_still_parse():
    force, selected, platform = srd.parse_setup_args(
        ["--platform", "gf180", "--designs", "a,b", "--force"])
    assert (force, selected, platform) == (True, ["a", "b"], "gf180")


# ---- the exit-code contract, proven through a subprocess -------------------------------
# An in-process SystemExit assert does not prove the CLI's exit code or that main() was
# never reached; only a real process does (see memory: test CLIs through subprocesses).

def test_cli_help_exits_zero_without_running_setup():
    import subprocess
    r = subprocess.run([sys.executable, str(TOOLS / "setup_rtl_designs.py"), "--help"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    assert "Setting up" not in r.stdout, "--help RAN the corpus setup"


def test_cli_unknown_flag_exits_nonzero_without_running_setup():
    import subprocess
    r = subprocess.run([sys.executable, str(TOOLS / "setup_rtl_designs.py"),
                        "--platfrom", "gf180", "--force"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode != 0, "a typo'd platform flag must not silently re-point the corpus"
    assert "Setting up" not in r.stdout, "unknown flag still ran the corpus setup"
