"""Tests for the BEOL-only DRC deck transform used by run_drc.sh.

The sed command in run_drc.sh flips the top-level FEOL flag to false while
leaving BEOL untouched.  This test replicates that transform and verifies
correctness without needing a live ORFS environment.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

# The sed expression as it appears in run_drc.sh.
_SED_EXPR = r's/^([[:space:]]*FEOL[[:space:]]*=[[:space:]]*)true/\1false/'


def _apply_transform(src: str) -> str:
    """Apply the BEOL deck sed transform to *src* and return the result."""
    result = subprocess.run(
        ["sed", "-E", _SED_EXPR],
        input=src,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def test_feol_flipped_to_false(tmp_path):
    """FEOL = true  →  FEOL = false after the transform."""
    fake_deck = "FEOL    = true # Front-end-of-line checks\nBEOL    = true\n"
    out = _apply_transform(fake_deck)
    # FEOL must now be false
    assert re.search(r'^\s*FEOL\s*=\s*false', out, re.MULTILINE), (
        f"Expected 'FEOL = false' in output, got:\n{out}"
    )


def test_beol_unchanged(tmp_path):
    """BEOL = true must remain true after the transform."""
    fake_deck = "FEOL    = true # Front-end-of-line checks\nBEOL    = true\n"
    out = _apply_transform(fake_deck)
    assert re.search(r'^\s*BEOL\s*=\s*true', out, re.MULTILINE), (
        f"Expected 'BEOL = true' in output, got:\n{out}"
    )


def test_only_feol_line_changed():
    """Only the FEOL line changes; every other line is byte-for-byte identical."""
    fake_deck = (
        "# KLayout DRC deck for FreePDK45\n"
        "FEOL    = true # Front-end-of-line checks\n"
        "BEOL    = true\n"
        "ANTENNA = true\n"
        "# end of deck\n"
    )
    out = _apply_transform(fake_deck)
    in_lines = fake_deck.splitlines()
    out_lines = out.splitlines()
    assert len(in_lines) == len(out_lines), "Line count must not change"
    for i, (a, b) in enumerate(zip(in_lines, out_lines)):
        if re.match(r'\s*FEOL\s*=', a):
            assert b != a, f"Line {i} (FEOL) should have changed"
            assert "false" in b, f"Line {i} (FEOL) should contain 'false'"
        else:
            assert a == b, f"Line {i} should be unchanged: {a!r} != {b!r}"


def test_feol_already_false_is_idempotent():
    """If FEOL is already false the transform is a no-op (idempotent)."""
    fake_deck = "FEOL    = false\nBEOL    = true\n"
    out = _apply_transform(fake_deck)
    assert out == fake_deck, "Already-false deck should be unchanged"


def test_no_feol_line_leaves_deck_untouched():
    """A deck without a FEOL line is passed through unchanged."""
    fake_deck = "# deck without FEOL flag\nBEOL    = true\n"
    out = _apply_transform(fake_deck)
    assert out == fake_deck, "Deck without FEOL line should be unchanged"
