"""SKILL.md's Platform Support Matrix must match platform_capability.py's probe.

failure-patterns.md #32 closed with the lesson "a support-matrix 'Yes' must be
backed by an executable deck resolution on that platform" — but that lesson was
only ever applied to the sky130hs row that prompted it. On 2026-08-01 a gf180
round found the gf180 row still promising `KLayout DRC=Yes, KLayout LVS=Yes,
RCX=Yes` while this ORFS checkout ships gf180 with no `drc/`, no `lvs/` and no
`RCX_RULES` (probe: tier=installed, drc_deck/lvs/antenna/rcx all MISS). The
ihp-sg13g2 row overclaimed KLayout LVS the same way.

Prose cannot be trusted to stay true, so the table is now machine-checked: this
test parses the matrix out of SKILL.md and asserts every probed column against
`probe_platform`. Editing the table to disagree with the toolchain fails here.

Only the three columns the probe actually measures are asserted (KLayout DRC,
KLayout LVS, RCX) plus the tier. "Magic DRC" / "Netgen LVS" describe sky130
tool availability rather than a per-platform deck, so they stay prose.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import platform_capability as pc

SKILL_MD = Path(__file__).resolve().parents[1] / "SKILL.md"

# Matrix column header -> (probe capability key, human label).
PROBED_COLUMNS = {
    "KLayout DRC": "drc_deck",
    "KLayout LVS": "lvs",
    "RCX": "rcx",
}


def _parse_matrix(text: str) -> list[dict]:
    """Rows of the '#### Platform Support Matrix' table as {column: cell} dicts."""
    section = text.split("#### Platform Support Matrix", 1)
    assert len(section) == 2, "SKILL.md lost its '#### Platform Support Matrix' heading"
    lines = section[1].splitlines()

    header, rows, seen_header = None, [], False
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("|"):
            if seen_header:
                break  # table ended (footnotes / next section)
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if header is None:
            header, seen_header = cells, True
            continue
        if set("".join(cells)) <= set("-: "):
            continue  # separator row
        rows.append(dict(zip(header, cells)))
    assert header is not None, "no table found under the Platform Support Matrix heading"
    return rows


def _cell_says_yes(cell: str) -> bool:
    """'Yes', 'Yes¹', '**Yes**' -> True; 'No', 'No³', '—' -> False."""
    body = re.sub(r"[^A-Za-z]", "", cell).lower()
    assert body in ("yes", "no", ""), f"undecidable matrix cell {cell!r}"
    return body == "yes"


def _strip_footnote(cell: str) -> str:
    """'installed³' -> 'installed'."""
    return re.sub(r"[^a-z_]", "", cell.lower())


MATRIX = _parse_matrix(SKILL_MD.read_text())

# Resolve the SAME environment main() probes under (RMD3-P1-02): _env.sh, not
# ambient. Probing ambient made every row skip for want of a flow dir, and a
# suite of skips reads exactly like a suite of passes.
_ENV = pc.resolve_signoff_env()
_PROBE_ENV = _ENV if _ENV is not None else None
FLOW_DIR = pc.find_flow_dir(env=_PROBE_ENV)


def _installed(platform: str) -> bool:
    return bool(FLOW_DIR) and (Path(FLOW_DIR) / "platforms" / platform).is_dir()


def test_matrix_is_not_vacuous():
    """A parser that silently matches nothing would make every check below pass."""
    assert len(MATRIX) >= 5, f"parsed only {len(MATRIX)} matrix rows — parser drifted"
    assert "Tier" in MATRIX[0], "matrix lost its Tier column"
    for col in PROBED_COLUMNS:
        assert col in MATRIX[0], f"matrix lost its {col!r} column"


def test_probe_actually_ran_on_this_checkout():
    """Guard the skip path: an all-skipped run must not read as a green matrix.

    Skipping is honest only when ORFS is genuinely absent. Whenever a flow dir
    resolves, most of the matrix must really have been probed.
    """
    if not FLOW_DIR:
        pytest.skip("no ORFS flow dir on this machine — matrix cannot be checked")
    checked = [r["Platform"] for r in MATRIX if _installed(r["Platform"])]
    assert len(checked) >= 5, (
        f"only {len(checked)} of {len(MATRIX)} matrix platforms are installed under "
        f"{FLOW_DIR} ({checked}) — the row checks below would be near-vacuous"
    )


@pytest.mark.parametrize("row", MATRIX, ids=[r["Platform"] for r in MATRIX])
def test_matrix_row_matches_probe(row):
    if not FLOW_DIR:
        pytest.skip("no ORFS flow dir — cannot probe platform capability")
    platform = row["Platform"]
    if not _installed(platform):
        pytest.skip(f"{platform} not installed in this ORFS checkout")

    caps = pc.probe_platform(FLOW_DIR, platform, env=_PROBE_ENV)

    assert _strip_footnote(row["Tier"]) == caps.get("tier"), (
        f"SKILL.md says {platform} tier={row['Tier']!r} but the probe reports "
        f"{caps.get('tier')!r} (missing={caps.get('missing')})"
    )
    if "drc_deck" not in caps:
        # An `unsupported` platform short-circuits before capability probing, so
        # there is nothing to compare against — the matrix must then claim nothing.
        assert caps.get("tier") == "unsupported", (
            f"{platform} returned no capability keys but is not unsupported: {caps}")
        for column in PROBED_COLUMNS:
            assert not _cell_says_yes(row[column]), (
                f"SKILL.md claims {platform} {column}={row[column]!r}, but {platform} "
                f"is unsupported in this version and is never probed "
                f"({caps.get('unsupported_reason')})")
        return

    for column, cap_key in PROBED_COLUMNS.items():
        documented = _cell_says_yes(row[column])
        probed = bool(caps[cap_key]["ok"])
        assert documented == probed, (
            f"SKILL.md says {platform} {column}={row[column]!r} but the probe "
            f"reports {cap_key}={'ok' if probed else 'MISS'} ({caps[cap_key]}). "
            "A support-matrix 'Yes' must be backed by an executable deck "
            "resolution on that platform (failure-patterns.md #32)."
        )
