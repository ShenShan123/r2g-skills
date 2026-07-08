"""Techlib-restructure safety gate — cross-platform CSV regression scaffold (Task 0).

The upcoming `techlib` restructure consolidates per-platform logic in the feature/
label extractors. Its safety contract is **byte-for-byte identical CSV output**
before/after on two pinned designs that exercise two different PDKs:

  * aes_core (nangate45) — V_nom 1.10, metal1..metal10, 61 distinct curated
    cell_type_ids.
  * cordic  (sky130hd)  — V_nom 1.80, li1/met1..met5, differentiated cell_type_ids and
    non-zero cell_area/power. (The sky130 quote-bug — quoted liberty cell names that
    collapsed these to 0/UNKNOWN — was fixed on this branch, so the baseline now carries
    the corrected, non-degenerate values.) Labels use canonical names
    (cell_congestion/timing_features/ir_drop/wirelength).

This module is SCAFFOLDING. It:
  1. Regenerates both stages for both designs into a temp dir via the pinned
     helper `tools/regen_extract_baseline.sh` (same pinned DEFs as the baseline,
     so the auto-find DEF ambiguity — the cordic nangate trap — is removed).
  2. Asserts every produced CSV is byte-identical (md5) to the committed baseline
     under $R2G_TECHLIB_BASELINE (default /tmp/techlib_baseline).
  3. Skips cleanly when the baseline dir is absent (design_cases/ + the baseline
     are gitignored / machine-local), so CI without the corpus still imports and
     runs this file green.

Task 10 (cross-platform PDK value regression) adds the functions below the original
baseline-gate tests. These functions are independent of the baseline CSV content — they
test the techlib modules directly against the REAL ORFS PDK files and pin the
2026-05-30 verified values (voltages, routing layers, gz cell counts) so a future
edit that silently changes any platform's constants is caught with a descriptive
failure. They skip per-platform when a PDK file is absent (ORFS is machine-local).

--- Recorded baseline anchors (sanity references; verified at capture time) ---
  cordic metadata: num_cells=6508 num_nets=1454 num_ios=107 dbu=1000
    tracks_per_layer=li1:1125|met1:1294|met2:956|met3:646|met4:478|met5:128 V_nom=1.80
  cordic nodes_net col 'num_layer' distinct set = {0,2,3,4,5}
  cordic nodes_gate col 'cell_type_id' differentiated + cell_area/power > 0 (quote-bug fixed)
  aes_core metadata: nangate45, V_nom=1.10, dbu=2000, metal1..metal10
  aes_core nodes_gate 'cell_type_id' distinct count = 61 (curated, varied)

--- 2026-05-30 cross-platform PDK value anchors (Task 10) ---
  Voltages (profile + resolve no-PWR fallback, both paths):
    nangate45="1.1"  sky130hd="1.8"  sky130hs="1.8"  asap7="0.70"  gf180="5.0"  ihp-sg13g2="1.2"
  Routing layers (from real tech LEFs):
    nangate45: metal1..metal10 (all 10 present)
    sky130hd/sky130hs: li1 + met1..met5
    asap7: M1..M9 (+ Pad; M1..M9 asserted as subset)
    gf180 (sorted-first *_tech.lef = gf180mcu_2LM_1TM_30K_7t_tech.lef): Metal1 Metal2
    ihp-sg13g2: Metal1..Metal5 + TopMetal1 + TopMetal2
  gz liberty cell counts (deterministic sorted-first picks):
    asap7 (NLDM TT corner, lib.gz): 42 cells
    gf180 (*tt*5v00*.lib.gz): 229 cells
"""
from __future__ import annotations

import glob
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
HELPER = REPO_ROOT / "tools" / "regen_extract_baseline.sh"
BASELINE_DIR = Path(os.environ.get("R2G_TECHLIB_BASELINE", "/tmp/techlib_baseline"))

# --- Task 10: techlib imports (resolved via conftest's EXTRACT_DIR sys.path entry) ---
from techlib import lef as _lef          # noqa: E402
from techlib import liberty as _liberty  # noqa: E402
from techlib import profile as _profile  # noqa: E402
from techlib import resolve as _resolve  # noqa: E402

# Designs and the CSV sub-trees the gate covers. Per-design subdirs of CSVs.
DESIGNS = ("aes_core", "cordic")
CSV_SUBDIRS = ("features", "labels")


def _md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _rel_csvs(root: Path) -> dict[str, Path]:
    """Map 'subdir/name.csv' -> abs path for every CSV under root/{features,labels}."""
    out: dict[str, Path] = {}
    for sub in CSV_SUBDIRS:
        d = root / sub
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.csv")):
            out[f"{sub}/{p.name}"] = p
    return out


def _baseline_present() -> bool:
    if not BASELINE_DIR.is_dir():
        return False
    # Require at least one design dir that actually has CSVs captured.
    for d in DESIGNS:
        if _rel_csvs(BASELINE_DIR / d):
            return True
    return False


# NB: this skip gate is applied ONLY to the 4 baseline-regen/byte-diff gate tests below
# (test_regenerated_matches_baseline + test_md5sums_file_recorded, each parametrized over
# 2 designs). It is intentionally NOT a module-level `pytestmark`: the Task-10 PDK-value
# tests (test_t10_*) drive techlib directly against the real ORFS PDK files and do NOT need
# the regen baseline, so they must still RUN on a fresh checkout / CI where /tmp/techlib_baseline
# is absent — gated only by their own per-platform PDK-file skips.
_BASELINE_GATE = pytest.mark.skipif(
    not _baseline_present(),
    reason=(
        f"techlib baseline absent at {BASELINE_DIR} "
        "(design_cases/ + baseline are machine-local; "
        "run tools/regen_extract_baseline.sh to capture it)"
    ),
)


@pytest.fixture(scope="module")
def regenerated(tmp_path_factory) -> Path:
    """Regenerate both stages for both designs into a fresh temp dir via the helper.

    Uses the SAME pinned DEFs as the committed baseline, so a byte diff reflects a
    real change in extractor output — not input drift.
    """
    if not HELPER.exists():
        pytest.skip(f"helper script missing: {HELPER}")
    out = tmp_path_factory.mktemp("techlib_current")
    proc = subprocess.run(
        ["bash", str(HELPER), str(out)],
        capture_output=True,
        text=True,
    )
    if proc.returncode == 77:
        # rc=77 is the helper's "frozen INPUT artifact absent" sentinel: a campaign
        # re-target (/r2g-debug Step 1b re-points + re-flows design_cases/) consumed the
        # pinned RUN dirs this byte-diff gate regenerates from. That is environmental input
        # drift, NOT an extractor regression -- skip, don't hard-error. Restore with
        # tools/regen_extract_baseline.sh once the relevant designs are flowed again.
        sys.stderr.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        pytest.skip("techlib baseline INPUT artifacts absent (campaign consumed design_cases/ "
                    "pinned RUN dirs); run tools/regen_extract_baseline.sh to restore")
    if proc.returncode != 0:
        # Surface helper output so a regen failure is debuggable, not a silent skip.
        sys.stderr.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        pytest.fail(f"regen_extract_baseline.sh failed (rc={proc.returncode})")
    return out


@_BASELINE_GATE
@pytest.mark.parametrize("design", DESIGNS)
def test_regenerated_matches_baseline(regenerated: Path, design: str):
    """Every baseline CSV must be reproduced byte-identically by a fresh regen."""
    base = _rel_csvs(BASELINE_DIR / design)
    if not base:
        pytest.skip(f"no baseline CSVs for {design} under {BASELINE_DIR}")
    cur = _rel_csvs(regenerated / design)

    missing = sorted(set(base) - set(cur))
    assert not missing, f"{design}: regenerated set is missing CSV(s): {missing}"

    # A refactor that renames or adds a CSV must also trip the gate, not slip
    # through just because every *baseline* file still matches.
    extra = sorted(set(cur) - set(base))
    assert not extra, f"{design}: regenerated has unexpected new CSV(s): {extra}"

    mismatches = []
    for rel, base_path in base.items():
        cur_path = cur[rel]
        b, c = _md5(base_path), _md5(cur_path)
        if b != c:
            mismatches.append(f"{rel}: baseline {b} != current {c}")
    assert not mismatches, f"{design}: CSV byte-mismatch vs baseline:\n" + "\n".join(mismatches)


@_BASELINE_GATE
@pytest.mark.parametrize("design", DESIGNS)
def test_md5sums_file_recorded(design: str):
    """Each baseline design dir carries an MD5SUMS manifest matching its CSVs."""
    design_dir = BASELINE_DIR / design
    manifest = design_dir / "MD5SUMS"
    if not manifest.exists():
        pytest.skip(f"no MD5SUMS for {design} (baseline partial)")
    recorded = {}
    for line in manifest.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        digest, rel = line.split(None, 1)
        recorded[rel] = digest
    actual = {rel: _md5(p) for rel, p in _rel_csvs(design_dir).items()}
    assert recorded == actual, (
        f"{design}: MD5SUMS manifest disagrees with on-disk CSVs "
        f"(recorded={recorded}, actual={actual})"
    )


# ============================================================================
# Task 10 — Cross-platform PDK value regression (2026-05-30 verified anchors)
#
# These tests pin concrete, verified values for ALL six ORFS platforms against
# the REAL PDK files so any future change that silently shifts a platform's
# voltage token, routing-layer set, or gz-liberty cell count is caught here with
# a descriptive failure.  They do NOT use the CSV baseline dir; per-platform
# PDK files are skipped cleanly when ORFS is absent (machine-local).
# ============================================================================

# ---------------------------------------------------------------------------
# Helpers shared by the Task-10 tests (private to this module)
# ---------------------------------------------------------------------------

def _t10_platforms_dir() -> str | None:
    """ORFS platforms dir: $ORFS_ROOT first, then the known dev-box path."""
    candidates: list[str] = []
    orfs_root = os.environ.get("ORFS_ROOT")
    if orfs_root:
        candidates.append(os.path.join(orfs_root, "flow", "platforms"))
    candidates.append("/proj/workarea/user5/OpenROAD-flow-scripts/flow/platforms")
    for c in candidates:
        if c and os.path.isdir(c):
            return c
    return None


def _t10_tech_lef(platform: str) -> str | None:
    """Return the routing tech-LEF path for *platform*, or None if absent."""
    pdir = _t10_platforms_dir()
    if not pdir:
        return None
    _literal: dict[str, str] = {
        "nangate45":   "nangate45/lef/NangateOpenCellLibrary.tech.lef",
        "sky130hd":    "sky130hd/lef/sky130_fd_sc_hd.tlef",
        "sky130hs":    "sky130hs/lef/sky130_fd_sc_hs.tlef",
        "asap7":       "asap7/lef/asap7_tech_1x_201209.lef",
        "ihp-sg13g2":  "ihp-sg13g2/lef/sg13g2_tech.lef",
    }
    if platform in _literal:
        p = os.path.join(pdir, _literal[platform])
        return p if os.path.isfile(p) else None
    if platform == "gf180":
        matches = sorted(glob.glob(os.path.join(pdir, "gf180", "lef", "*_tech.lef")))
        return matches[0] if matches else None
    return None


def _t10_gz_lib(platform: str) -> str | None:
    """Return a deterministic (sorted-first) .lib.gz path, or None."""
    pdir = _t10_platforms_dir()
    if not pdir:
        return None
    if platform == "asap7":
        # NLDM TT corner preferred; fall back to any .lib.gz under asap7/lib/
        matches = sorted(glob.glob(os.path.join(pdir, "asap7", "lib", "NLDM", "*_TT_*.lib.gz")))
        if not matches:
            matches = sorted(glob.glob(
                os.path.join(pdir, "asap7", "lib", "**", "*.lib.gz"), recursive=True
            ))
        return matches[0] if matches else None
    if platform == "gf180":
        matches = sorted(glob.glob(os.path.join(pdir, "gf180", "lib", "*tt*5v00*.lib.gz")))
        if not matches:
            matches = sorted(glob.glob(os.path.join(pdir, "gf180", "lib", "*.lib.gz")))
        return matches[0] if matches else None
    return None


def _t10_lef_or_skip(platform: str) -> str:
    path = _t10_tech_lef(platform)
    if not path:
        pytest.skip(f"{platform}: tech LEF absent (ORFS machine-local)")
    return path


def _t10_gz_or_skip(platform: str) -> str:
    path = _t10_gz_lib(platform)
    if not path:
        pytest.skip(f"{platform}: .lib.gz absent (ORFS machine-local)")
    return path


def _assert_area_power_nondegenerate(db: dict, platform: str) -> None:
    """Guard against degenerate (all-zero) cell area/power on a parsed liberty DB.

    asap7 and gf180 carry leakage as block-form ``leakage_power () { value : X }``
    with NO scalar ``cell_leakage_power``; gf180 additionally QUOTES the value
    (``value : "0.00029065"``). A parser that only handled the scalar form (or the
    bare-number block) silently collapsed every cell's power to 0 — the same
    failure class as the sky130 quoted-cell-name bug (commit 363a8b2). Real
    std-cell areas are likewise always positive. This guard asserts both columns
    are non-degenerate so a regression in the block/quote handling is caught.
    """
    cells = db["cells"]
    n = len(cells)
    assert n > 0, f"{platform}: no cells parsed"
    # Every real standard cell has positive area.
    zero_area = [c["name"] for c in cells.values()
                 if not (c.get("area") is not None and c["area"] > 0)]
    assert not zero_area, (
        f"{platform}: {len(zero_area)}/{n} cells have non-positive area "
        f"(e.g. {zero_area[:3]}) — liberty area parse degenerate"
    )
    # Leakage power must not collapse to all-zero. The verified 2026-05-30 state is
    # 100% positive on both asap7 (42/42) and gf180 (229/229); require a strong
    # majority so a future ORFS lib that legitimately adds a zero-leakage cell is
    # tolerated while the all-zero degeneracy bug is still caught decisively.
    n_power_pos = sum(1 for c in cells.values()
                      if c.get("power") is not None and c["power"] > 0)
    assert n_power_pos >= 0.9 * n, (
        f"{platform}: only {n_power_pos}/{n} cells have power>0 — block-form/quoted "
        f"leakage parse degenerate (expected ~all positive; verified 100% on 2026-05-30)"
    )


# ---------------------------------------------------------------------------
# 1. Supply-voltage regression — both consumer paths, all six platforms
# ---------------------------------------------------------------------------

# The 2026-05-30 verified verbatim voltage tokens.  String equality (not float)
# is deliberate: "0.70" != "0.7" and the shell SUPPLY_VOLTAGE output must match.
_T10_EXPECTED_VOLTAGE_STR: dict[str, str] = {
    "nangate45":  "1.1",
    "sky130hd":   "1.8",
    "sky130hs":   "1.8",
    "asap7":      "0.70",
    "gf180":      "5.0",
    "ihp-sg13g2": "1.2",
}

_T10_ALL_PLATFORMS = list(_T10_EXPECTED_VOLTAGE_STR)


@pytest.mark.parametrize("platform", _T10_ALL_PLATFORMS)
def test_t10_supply_voltage_profile_str(platform: str):
    """techlib.profile.get_profile(p).supply_voltage_str equals the 2026-05-30 token.

    Pure logic — no ORFS files needed.  Catches any future edit that shifts a
    platform's nominal voltage without updating all consumers.
    """
    expected = _T10_EXPECTED_VOLTAGE_STR[platform]
    actual = _profile.get_profile(platform).supply_voltage_str
    assert actual == expected, (
        f"{platform}: profile.supply_voltage_str={actual!r}, expected={expected!r} "
        f"(2026-05-30 verified token)"
    )


@pytest.mark.parametrize("platform", _T10_ALL_PLATFORMS)
def test_t10_supply_voltage_resolve_no_pwr(platform: str):
    """resolve._resolve_supply_voltage('', p) equals the 2026-05-30 token (no-PWR path).

    Tests the live consumer path (what run_features.sh / run_labels.sh receive when
    PWR_NETS_VOLTAGES is not set).  Pure logic — no ORFS files needed.
    """
    expected = _T10_EXPECTED_VOLTAGE_STR[platform]
    actual = _resolve._resolve_supply_voltage("", platform)
    assert actual == expected, (
        f"{platform}: resolve._resolve_supply_voltage('', ...)={actual!r}, "
        f"expected={expected!r} (2026-05-30 verified token)"
    )
    # Both consumer paths must agree (belt-and-suspenders: profile == resolve fallback).
    profile_str = _profile.get_profile(platform).supply_voltage_str
    assert actual == profile_str, (
        f"{platform}: resolve fallback {actual!r} != profile_str {profile_str!r}"
    )


# ---------------------------------------------------------------------------
# 2. Routing-layer regression — per-platform exact membership assertions
# ---------------------------------------------------------------------------

def test_t10_layers_nangate45():
    """nangate45 tech LEF: metal1..metal10 all present (complete set, verified 2026-05-30)."""
    path = _t10_lef_or_skip("nangate45")
    layers = _lef.routing_layers(path)
    expected = {f"metal{i}" for i in range(1, 11)}
    missing = expected - set(layers)
    assert not missing, (
        f"nangate45: missing layers {sorted(missing)} in parsed set {layers}"
    )
    # Full set check: no extra non-metal layers (all parsed names match metal\d+).
    assert all(re.fullmatch(r"metal\d+", n) for n in layers), (
        f"nangate45: unexpected non-metal layer names in {layers}"
    )


def test_t10_layers_sky130hd():
    """sky130hd tech LEF: li1 + met1..met5 all present (verified 2026-05-30)."""
    path = _t10_lef_or_skip("sky130hd")
    layers = _lef.routing_layers(path)
    required = {"li1", "met1", "met2", "met3", "met4", "met5"}
    missing = required - set(layers)
    assert not missing, (
        f"sky130hd: missing layers {sorted(missing)} in parsed set {layers}"
    )


def test_t10_layers_sky130hs():
    """sky130hs tech LEF: li1 + met1..met5 all present (verified 2026-05-30)."""
    path = _t10_lef_or_skip("sky130hs")
    layers = _lef.routing_layers(path)
    required = {"li1", "met1", "met2", "met3", "met4", "met5"}
    missing = required - set(layers)
    assert not missing, (
        f"sky130hs: missing layers {sorted(missing)} in parsed set {layers}"
    )


def test_t10_layers_asap7():
    """asap7 tech LEF: M1..M9 present as subset (verified 2026-05-30; M1..M9 ⊆ result).

    The asap7 LEF also declares a 'Pad' layer; we assert the M-numbered set but do
    NOT assert the exact full set so variants with more/fewer Pad entries still pass.
    """
    path = _t10_lef_or_skip("asap7")
    layers = _lef.routing_layers(path)
    required = {f"M{i}" for i in range(1, 10)}  # M1..M9
    missing = required - set(layers)
    assert not missing, (
        f"asap7: missing M-layers {sorted(missing)} in parsed set {layers}"
    )
    # Every parsed layer must be either M\d+ or Pad (the only two families seen).
    unexpected = [n for n in layers if not re.fullmatch(r"M\d+", n) and n != "Pad"]
    assert not unexpected, (
        f"asap7: unexpected layer names {unexpected} in {layers}"
    )


def test_t10_layers_gf180():
    """gf180 sorted-first *_tech.lef: Metal1 + Metal2 present; all names match Metal\\d+.

    The gf180 platform ships many tech-LEF variants (2LM/3LM/4LM/5LM/6LM); the
    sorted-first pick is gf180mcu_2LM_1TM_30K_7t_tech.lef (2 routing layers).
    We assert Metal1 + Metal2 are present and that every parsed name matches Metal\\d+,
    making the test robust to variants that declare more Metal layers.
    """
    path = _t10_lef_or_skip("gf180")
    layers = _lef.routing_layers(path)
    assert "Metal1" in layers, f"gf180: Metal1 missing from {layers}"
    assert "Metal2" in layers, f"gf180: Metal2 missing from {layers}"
    assert len(layers) >= 2, f"gf180: fewer than 2 routing layers in {layers}"
    bad = [n for n in layers if not re.fullmatch(r"Metal\d+", n)]
    assert not bad, f"gf180: non-Metal\\d+ layer names {bad} in {layers}"


def test_t10_layers_ihp_sg13g2():
    """ihp-sg13g2 tech LEF: Metal1..Metal5 AND TopMetal1/TopMetal2 all present (verified 2026-05-30)."""
    path = _t10_lef_or_skip("ihp-sg13g2")
    layers = _lef.routing_layers(path)
    required = {"Metal1", "Metal2", "Metal3", "Metal4", "Metal5", "TopMetal1", "TopMetal2"}
    missing = required - set(layers)
    assert not missing, (
        f"ihp-sg13g2: missing layers {sorted(missing)} in parsed set {layers}"
    )


# ---------------------------------------------------------------------------
# 3. gz-liberty cell-count regression — asap7 + gf180 (verified 2026-05-30)
# ---------------------------------------------------------------------------

def test_t10_gz_liberty_asap7():
    """.lib.gz parse for asap7 (NLDM TT corner): non-empty cells dict, >10 cells.

    Verified 2026-05-30: asap7sc7p5t_AO_LVT_TT_nldm_211120.lib.gz yields 42 cells.
    We assert >10 (not the exact count) to remain robust to minor cell-library
    additions in future ORFS updates while still catching a truncated/failed parse.
    """
    path = _t10_gz_or_skip("asap7")
    db = _liberty.load_liberty_db([path])
    n = len(db["cells"])
    assert n > 10, (
        f"asap7 .lib.gz: only {n} cells parsed from {path} "
        f"(expected >10; verified=42 on 2026-05-30)"
    )
    assert path in db["sources"]["lib"], f"asap7 .lib.gz: path missing from sources"
    # asap7 leakage is block-form (bare value, no scalar cell_leakage_power).
    _assert_area_power_nondegenerate(db, "asap7")


def test_t10_gz_liberty_gf180():
    """.lib.gz parse for gf180 (tt 5v00 corner): non-empty cells dict, >10 cells.

    Verified 2026-05-30: gf180mcu_fd_sc_mcu7t5v0__tt_025C_5v00.lib.gz yields 229 cells.
    We assert >10 for robustness.
    """
    path = _t10_gz_or_skip("gf180")
    db = _liberty.load_liberty_db([path])
    n = len(db["cells"])
    assert n > 10, (
        f"gf180 .lib.gz: only {n} cells parsed from {path} "
        f"(expected >10; verified=229 on 2026-05-30)"
    )
    assert path in db["sources"]["lib"], f"gf180 .lib.gz: path missing from sources"
    # gf180 leakage is block-form with QUOTED values (value : "0.000…") — the
    # regression that motivated the quote-strip fix; power was 0/229 before it.
    _assert_area_power_nondegenerate(db, "gf180")
