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


# ── DRC_SKIP_CONTACT deeper-fallback transform (comments out CONTACT.* checks) ──
# The sed expression exactly as it appears in run_drc.sh.
_SED_SKIP_CONTACT = r's/^([[:space:]]*[^#].*\.output\("CONTACT\.)/# r2g-skip-contact: \1/'


def _apply_skip_contact(src: str) -> str:
    result = subprocess.run(
        ["sed", "-E", _SED_SKIP_CONTACT],
        input=src, capture_output=True, text=True, check=True,
    )
    return result.stdout


# A realistic slice of the FreePDK45 deck around the CONTACT group.
_CONTACT_SLICE = (
    'cont    = polygons(10, 0)\n'
    'cont.width(65.nm).output("CONTACT.1", "CONTACT.1 : width")\n'
    'cont.space(75.nm, euclidian).output("CONTACT.2", "CONTACT.2 : spacing")\n'
    'cont.not(active.or(poly.or(metal1))).output("CONTACT.3", "CONTACT.3 : inside")\n'
    'active.enclosing(cont, 5.nm, euclidian).output("CONTACT.4", "CONTACT.4 : enc")\n'
    'metal1.width(65.nm, euclidian).output("METAL1.1", "METAL1.1 : width")\n'
    'cont.interacting(error_corners.polygons(1.dbu)).output("METAL1.3", "METAL1.3 : enc")\n'
)


def test_skip_contact_comments_all_contact_checks():
    """Every CONTACT.* check line (incl. ones referencing FEOL layers) is commented."""
    out = _apply_skip_contact(_CONTACT_SLICE)
    for n in (1, 2, 3, 4):
        assert not re.search(rf'^\s*[^#].*\.output\("CONTACT\.{n}', out, re.MULTILINE), (
            f"CONTACT.{n} check should be commented out, got:\n{out}"
        )
    # And the guard regex run_drc.sh uses must find no uncommented CONTACT lines.
    assert not re.search(r'^\s*[^#].*\.output\("CONTACT\.', out, re.MULTILINE)


def test_skip_contact_leaves_layer_def_and_metal_checks():
    """The `cont` layer definition and METAL.* checks must NOT be commented."""
    out = _apply_skip_contact(_CONTACT_SLICE)
    assert re.search(r'^cont\s*=\s*polygons', out, re.MULTILINE), (
        "the `cont` layer definition must remain (later metal rules reference it)"
    )
    assert re.search(r'^\s*metal1\.width.*METAL1\.1', out, re.MULTILINE), (
        "METAL1.1 (real P&R routing check) must remain active"
    )
    assert re.search(r'^\s*cont\.interacting.*METAL1\.3', out, re.MULTILINE), (
        "METAL1.3 is a METAL-labelled check; only CONTACT.* labels are stripped"
    )
