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
