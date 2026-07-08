"""Tests for the BEOL-only DRC deck transform used by run_drc.sh.

The sed command in run_drc.sh flips BOTH the top-level FEOL and ANTENNA
toggles to false (ANTENNA depends on the FEOL-derived `gate` layer, so it
cannot run with FEOL off) while leaving BEOL and OFFGRID untouched.  This test
replicates that transform and verifies correctness without needing a live ORFS
environment.
"""
from __future__ import annotations

import re
import subprocess

# The sed expressions exactly as they appear in run_drc.sh.
_SED_FEOL = r's/^([[:space:]]*FEOL[[:space:]]*=[[:space:]]*)true/\1false/'
_SED_ANTENNA = r's/^([[:space:]]*ANTENNA[[:space:]]*=[[:space:]]*)true/\1false/'


def _apply_transform(src: str) -> str:
    """Apply the BEOL deck sed transform (FEOL + ANTENNA) to *src*."""
    result = subprocess.run(
        ["sed", "-E", "-e", _SED_FEOL, "-e", _SED_ANTENNA],
        input=src,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def test_feol_flipped_to_false():
    """FEOL = true  →  FEOL = false after the transform."""
    fake_deck = "FEOL    = true # Front-end-of-line checks\nBEOL    = true\n"
    out = _apply_transform(fake_deck)
    assert re.search(r'^\s*FEOL\s*=\s*false', out, re.MULTILINE), (
        f"Expected 'FEOL = false' in output, got:\n{out}"
    )


def test_antenna_flipped_to_false():
    """ANTENNA = true  →  ANTENNA = false (depends on FEOL-derived gate layer)."""
    fake_deck = (
        "FEOL    = true # Front-end-of-line checks\n"
        "BEOL    = true\n"
        "ANTENNA = true\n"
    )
    out = _apply_transform(fake_deck)
    assert re.search(r'^\s*ANTENNA\s*=\s*false', out, re.MULTILINE), (
        f"Expected 'ANTENNA = false' in output, got:\n{out}"
    )


def test_beol_and_offgrid_unchanged():
    """BEOL and OFFGRID must remain true after the transform."""
    fake_deck = (
        "FEOL    = true # Front-end-of-line checks\n"
        "BEOL    = true\n"
        "ANTENNA = true\n"
        "OFFGRID = true\n"
    )
    out = _apply_transform(fake_deck)
    assert re.search(r'^\s*BEOL\s*=\s*true', out, re.MULTILINE), (
        f"Expected 'BEOL = true' in output, got:\n{out}"
    )
    assert re.search(r'^\s*OFFGRID\s*=\s*true', out, re.MULTILINE), (
        f"Expected 'OFFGRID = true' in output, got:\n{out}"
    )


def test_full_deck_only_feol_and_antenna_change():
    """On a 4-toggle deck: FEOL+ANTENNA→false, BEOL+OFFGRID stay true; others identical."""
    fake_deck = (
        "# KLayout DRC deck for FreePDK45\n"
        "FEOL    = true # Front-end-of-line checks\n"
        "BEOL    = true\n"
        "ANTENNA = true\n"
        "OFFGRID = true\n"
        "# end of deck\n"
    )
    out = _apply_transform(fake_deck)
    in_lines = fake_deck.splitlines()
    out_lines = out.splitlines()
    assert len(in_lines) == len(out_lines), "Line count must not change"
    for i, (a, b) in enumerate(zip(in_lines, out_lines)):
        if re.match(r'\s*FEOL\s*=', a) or re.match(r'\s*ANTENNA\s*=', a):
            assert b != a, f"Line {i} should have changed: {a!r}"
            assert "false" in b, f"Line {i} should contain 'false': {b!r}"
        else:
            assert a == b, f"Line {i} should be unchanged: {a!r} != {b!r}"


def test_already_false_is_idempotent():
    """If FEOL and ANTENNA are already false the transform is a no-op."""
    fake_deck = "FEOL    = false\nBEOL    = true\nANTENNA = false\n"
    out = _apply_transform(fake_deck)
    assert out == fake_deck, "Already-false deck should be unchanged"


def test_no_feol_or_antenna_line_leaves_deck_untouched():
    """A deck without FEOL/ANTENNA lines is passed through unchanged."""
    fake_deck = "# deck without FEOL/ANTENNA flags\nBEOL    = true\nOFFGRID = true\n"
    out = _apply_transform(fake_deck)
    assert out == fake_deck, "Deck without FEOL/ANTENNA lines should be unchanged"


# ── DRC_BEOL_STRICT deeper fallback: comment EVERY `.output(` inside if FEOL…end#FEOL ──
# The awk program exactly as it appears in run_drc.sh.
_AWK_BEOL_STRICT = r'''
/^[[:space:]]*if[[:space:]]+FEOL([^[:alnum:]_]|$)/ { infeol=1 }
infeol && /^[[:space:]]*end[[:space:]]*#[[:space:]]*FEOL/ { infeol=0 }
{ if (infeol && $0 ~ /\.output\(/ && $0 !~ /^[[:space:]]*#/) print "# r2g-beol-strict: " $0; else print }
'''


def _apply_beol_strict(src: str) -> str:
    result = subprocess.run(
        ["awk", _AWK_BEOL_STRICT],
        input=src, capture_output=True, text=True, check=True,
    )
    return result.stdout


# A realistic slice spanning the FEOL block (IMPLANT + CONTACT) and the BEOL block.
_FEOL_BLOCK = (
    'if FEOL\n'
    'info("FEOL checks")\n'
    'gate = poly & active\n'
    'active.width(90.nm).output("ACTIVE.1", "ACTIVE.1 : width")\n'
    'implant = nplus.or(pplus)\n'
    'implant.width(45.nm, euclidian).output("IMPLANT.3", "IMPLANT.3 : width")\n'
    'nplus.and(pplus).output("IMPLANT.5", "IMPLANT.5 : overlap")\n'
    'cont    = polygons(10, 0)\n'
    'cont.width(65.nm).output("CONTACT.1", "CONTACT.1 : width")\n'
    'cont.space(75.nm, euclidian).output("CONTACT.2", "CONTACT.2 : spacing")\n'
    'end # FEOL\n'
    'if BEOL\n'
    'metal1.width(65.nm, euclidian).output("METAL1.1", "METAL1.1 : width")\n'
    'end # BEOL\n'
)


def test_beol_strict_comments_every_feol_block_check():
    """ALL checks inside if FEOL…end#FEOL (ACTIVE/IMPLANT/CONTACT) are commented."""
    out = _apply_beol_strict(_FEOL_BLOCK)
    for label in ("ACTIVE.1", "IMPLANT.3", "IMPLANT.5", "CONTACT.1", "CONTACT.2"):
        assert not re.search(rf'^\s*[^#].*\.output\("{re.escape(label)}', out, re.MULTILINE), (
            f"{label} (inside FEOL block) should be commented, got:\n{out}"
        )


def test_beol_strict_leaves_layer_defs_and_beol_checks():
    """Layer-derivation lines and the BEOL-block METAL check must remain active."""
    out = _apply_beol_strict(_FEOL_BLOCK)
    # Layer derivations (no .output) stay — later metal rules reference them.
    assert re.search(r'^gate\s*=\s*poly', out, re.MULTILINE)
    assert re.search(r'^implant\s*=\s*nplus', out, re.MULTILINE)
    assert re.search(r'^cont\s*=\s*polygons', out, re.MULTILINE)
    # The METAL1.1 check is OUTSIDE the FEOL block (in `if BEOL`) → must stay active.
    assert re.search(r'^\s*metal1\.width.*METAL1\.1', out, re.MULTILINE), (
        "METAL1.1 (real P&R routing check, in BEOL block) must remain active"
    )


def test_beol_strict_idempotent_on_already_commented():
    """Re-running the strip on already-stripped content is a no-op (no double prefix)."""
    once = _apply_beol_strict(_FEOL_BLOCK)
    twice = _apply_beol_strict(once)
    assert once == twice, "strict strip must be idempotent"
