"""Tests for techlib.resolve + the resolve_platform_paths.sh shim (Task 6).

Task 6 ported the per-platform liberty/LEF/voltage path resolution OUT of the shell
script ``scripts/flow/resolve_platform_paths.sh`` INTO ``techlib.resolve`` (Python API +
a byte-identical ``KEY=VALUE`` CLI), then turned the shell into a thin shim that
``source``s ``_env.sh`` and ``exec``s the module. The original shell logic is GONE, so
this is a durable regression test that does not depend on it.

Two layers:

  * **Pure-logic unit tests** (no ORFS): feed synthetic inputs to resolve.py's helper
    functions — the voltage parse/validity rule and the glob-fallback ORDER — so the core
    logic is covered even where ORFS / a corpus is absent.
  * **ORFS-backed integration tests** (skip cleanly if ORFS or a config is absent): run
    BOTH the shim and ``resolve.py`` directly and assert identical stdout (the shim just
    calls resolve.py), plus structural invariants (exactly 6 lines in contract order;
    LIB_FILES + TECH_LEF point at existing files where ORFS resolves them; SUPPLY_VOLTAGE
    equals the profile token for configs with no PWR override).

Modules import as plain top-level packages via the conftest EXTRACT_DIR sys.path entry.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from techlib import resolve, profile

SKILL_ROOT = Path(__file__).resolve().parents[1]
SHIM = SKILL_ROOT / "scripts" / "flow" / "resolve_platform_paths.sh"
RESOLVE_PY = SKILL_ROOT / "scripts" / "extract" / "techlib" / "resolve.py"

# The contract: exactly these six keys, in this order.
EXPECTED_KEYS = (
    "LIB_FILES",
    "TECH_LEF",
    "SC_LEF",
    "ADDITIONAL_LIBS",
    "ADDITIONAL_LEFS",
    "SUPPLY_VOLTAGE",
)


# --------------------------------------------------------------------------- #
# ORFS / config discovery (integration tests skip cleanly without these)
# --------------------------------------------------------------------------- #
def _flow_dir() -> Path | None:
    """Locate the ORFS flow dir like _env.sh would (env first, then known checkouts)."""
    fd = os.environ.get("FLOW_DIR")
    if fd and (Path(fd) / "Makefile").is_file():
        return Path(fd)
    orfs = os.environ.get("ORFS_ROOT")
    if orfs and (Path(orfs) / "flow" / "Makefile").is_file():
        return Path(orfs) / "flow"
    for cand in (
        Path.home() / "OpenROAD-flow-scripts",
        Path("/proj/workarea/user5/OpenROAD-flow-scripts"),
        Path("/opt/OpenROAD-flow-scripts"),
        Path("/opt/EDA4AI/OpenROAD-flow-scripts"),
    ):
        if (cand / "flow" / "Makefile").is_file():
            return cand / "flow"
    return None


def _platform_configs() -> dict[str, Path]:
    """Per-platform (platform -> config.mk) for whatever ORFS designs exist here.

    Prefer the pinned design_cases configs for nangate45/sky130hd (the CSV-gate designs);
    otherwise use a real ORFS design under flow/designs/<platform>/ (gcd, falling back to
    any design dir that has a config.mk). Platforms with no config are simply omitted, so
    callers ``pytest.skip`` per-platform.
    """
    fd = _flow_dir()
    out: dict[str, Path] = {}

    # Pinned design_cases configs (the byte-for-byte CSV-gate designs), if present.
    dc = SKILL_ROOT.parent / "design_cases"
    pinned = {
        "nangate45": dc / "aes_core" / "constraints" / "config.mk",
        "sky130hd": dc / "cordic" / "constraints" / "config.mk",
    }
    for plat, cfg in pinned.items():
        if cfg.is_file():
            out[plat] = cfg

    if fd is None:
        return out

    designs = fd / "designs"
    for plat in ("nangate45", "sky130hd", "sky130hs", "asap7", "gf180", "ihp-sg13g2"):
        if plat in out:
            continue
        pdir = designs / plat
        if not pdir.is_dir():
            continue
        gcd = pdir / "gcd" / "config.mk"
        if gcd.is_file():
            out[plat] = gcd
            continue
        # Fall back to any design dir under this platform that has a config.mk.
        for sub in sorted(pdir.iterdir()):
            cfg = sub / "config.mk"
            if cfg.is_file():
                out[plat] = cfg
                break
    return out


_CONFIGS = _platform_configs()
_PLATFORMS = sorted(_CONFIGS)


def _require_orfs():
    """Skip an ORFS-backed test when no ORFS checkout is discoverable.

    The class-level ``skipif(not _PLATFORMS)`` is NOT sufficient: a pinned
    ``design_cases/*/constraints/config.mk`` (design_cases is gitignored but PRESENT on
    this machine) populates ``_CONFIGS`` even when ORFS itself is absent. Without ORFS the
    make dump is skipped and LIB_FILES/TECH_LEF resolve to "" — so an assertion like
    ``test_lib_and_tech_lef_exist`` would FAIL confusingly rather than SKIP. Guard each
    ORFS-dependent test on ``_flow_dir()`` being present.
    """
    if _flow_dir() is None:
        pytest.skip("ORFS checkout not discoverable (FLOW_DIR/ORFS_ROOT unset, no known checkout)")


def _run_shim(config_mk: Path, platform: str) -> str:
    """Run the shim and return its stdout, FAILING clearly on an infrastructure error.

    Captures stderr + returncode so a shim/env setup failure (e.g. bash missing, _env.sh
    blow-up) surfaces as a descriptive failure with the captured stderr — not as a
    downstream "expected 6 lines, got 0" mismatch that hides the real cause.
    """
    proc = subprocess.run(
        ["bash", str(SHIM), str(config_mk), platform],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if proc.returncode != 0 or not proc.stdout:
        pytest.fail(
            f"shim infrastructure error (rc={proc.returncode}, "
            f"empty_stdout={not proc.stdout}) for {platform} / {config_mk}\n"
            f"--- stderr ---\n{proc.stderr}"
        )
    return proc.stdout


def _run_resolve_py(config_mk: Path, platform: str) -> str:
    """Run resolve.py directly, with FLOW_DIR exported like the shim does."""
    env = dict(os.environ)
    fd = _flow_dir()
    if fd is not None:
        env["FLOW_DIR"] = str(fd)
        env["ORFS_ROOT"] = str(fd.parent)
    proc = subprocess.run(
        [sys.executable, str(RESOLVE_PY), str(config_mk), platform],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env=env,
    )
    return proc.stdout


# --------------------------------------------------------------------------- #
# Pure-logic unit tests (no ORFS needed — these must NOT skip)
# --------------------------------------------------------------------------- #
class TestVoltageParse:
    """The PWR_NETS_VOLTAGES parse + validity rule (resolve.py voltage helpers)."""

    def test_typical_vdd_token(self):
        # `VDD 0.77` -> awk '{print $2}' -> 0.77 ; valid -> emitted verbatim.
        assert resolve._parse_pwr_token("VDD 0.77") == "0.77"
        assert resolve._resolve_supply_voltage("VDD 0.77", "asap7") == "0.77"

    def test_quotes_are_stripped(self):
        # tr -d '"' first, then awk field 2.
        assert resolve._parse_pwr_token('"VDD" "1.10"') == "1.10"
        assert resolve._resolve_supply_voltage('"VDD" "1.10"', "nangate45") == "1.10"

    def test_empty_pwr_falls_back_to_profile(self):
        assert resolve._parse_pwr_token("") == ""
        for plat in ("nangate45", "sky130hd", "asap7", "gf180", "ihp-sg13g2"):
            assert (
                resolve._resolve_supply_voltage("", plat)
                == profile.get_profile(plat).supply_voltage_str
            )

    def test_single_field_pwr_has_no_token(self):
        # Only one field -> awk '{print $2}' is empty -> fallback.
        assert resolve._parse_pwr_token("VDD") == ""
        assert resolve._resolve_supply_voltage("VDD", "gf180") == "5.0"

    def test_non_numeric_token_falls_back(self):
        # Token with a char outside [0-9.] is INVALID (shell `*[!0-9.]*`) -> fallback.
        assert resolve._resolve_supply_voltage("VDD 1v8", "sky130hd") == "1.8"
        assert resolve._resolve_supply_voltage("VDD abc", "nangate45") == "1.1"

    def test_unknown_platform_default_is_one(self):
        # Unknown platform + no valid token -> profile degrade default "1.0".
        assert resolve._resolve_supply_voltage("", "totally-made-up") == "1.0"

    def test_dotted_token_is_valid(self):
        # All chars in [0-9.] -> valid, emitted verbatim (even oddly formatted).
        assert resolve._resolve_supply_voltage("VDD 0.70", "asap7") == "0.70"
        assert resolve._resolve_supply_voltage("VDD 5.5", "gf180") == "5.5"

    def test_asap7_fallback_token_is_verbatim_not_float(self):
        # The key reason supply_voltage_str exists: str(float) would lose the trailing 0.
        assert resolve._resolve_supply_voltage("", "asap7") == "0.70"
        assert resolve._resolve_supply_voltage("", "asap7") != "0.7"


class TestGlobFallbackOrder:
    """The lib/tech-LEF glob fallback ORDER + grep -v fakeram + first-match."""

    def _make_libdir(self, base: Path, names: list[str]) -> Path:
        d = base / "platforms" / "fake" / "lib"
        d.mkdir(parents=True)
        for n in names:
            (d / n).write_text("")
        return base / "platforms" / "fake"

    def test_typical_wins_over_plain_lib(self, tmp_path):
        pdir = self._make_libdir(tmp_path, ["zzz.lib", "foo_typical_x.lib"])
        # *typical*.lib pattern comes first, so it wins regardless of lexicographic order.
        got = resolve._resolve_lib_files("", str(pdir))
        assert got.endswith("foo_typical_x.lib")

    def test_fakeram_excluded(self):
        # grep -v fakeram drops the macro lib; the std-cell *tt* lib is chosen. NB: the
        # shell's `grep -v fakeram` (faithfully ported) filters on the FULL ls path, so
        # this test deliberately uses a NEUTRAL tmp dir whose path contains no "fakeram"
        # (pytest's tmp_path for this test is named test_fakeram_excluded0 — its path
        # WOULD pollute the filter and drop every candidate, which is correct production
        # behavior but defeats the basename-only intent here).
        base = Path(tempfile.mkdtemp(prefix="r2g_lib_"))
        try:
            pdir = self._make_libdir(base, ["fr45_64x32__tt.lib", "sc__tt_x.lib"])
            # Sanity: the neutral base must not itself contain the exclusion substring.
            assert "fakeram" not in str(base)
            # The macro lib basename uses 'fakeram' so the filter drops it.
            (pdir / "lib" / "fr45_64x32__tt.lib").rename(pdir / "lib" / "fakeram45_64x32__tt.lib")
            got = resolve._resolve_lib_files("", str(pdir))
            assert "fakeram" not in got
            assert got.endswith("sc__tt_x.lib")
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_lexicographic_first_within_pattern(self, tmp_path):
        # Two *typical* libs -> ls -1 sort (lexicographic) picks the first.
        pdir = self._make_libdir(tmp_path, ["b_typical.lib", "a_typical.lib"])
        got = resolve._resolve_lib_files("", str(pdir))
        assert got.endswith("a_typical.lib")

    def test_falls_through_pattern_priority(self, tmp_path):
        # No typical/__tt/_tt_; a bare *tt* should match the 4th pattern before *.lib.
        pdir = self._make_libdir(tmp_path, ["mylibtt9.lib"])
        got = resolve._resolve_lib_files("", str(pdir))
        assert got.endswith("mylibtt9.lib")

    def test_no_match_keeps_primary(self, tmp_path):
        # No .lib files at all -> primary value returned unchanged.
        (tmp_path / "platforms" / "fake" / "lib").mkdir(parents=True)
        got = resolve._resolve_lib_files("ORIGINAL_VALUE", str(tmp_path / "platforms" / "fake"))
        assert got == "ORIGINAL_VALUE"

    def test_primary_existing_lib_short_circuits_fallback(self, tmp_path):
        # If a primary LIB_FILES path exists, the glob is NOT consulted.
        real = tmp_path / "real.lib"
        real.write_text("")
        pdir = self._make_libdir(tmp_path, ["other_typical.lib"])
        got = resolve._resolve_lib_files(str(real), str(pdir))
        assert got == str(real)

    def test_tech_lef_pattern_order(self, tmp_path):
        d = tmp_path / "platforms" / "fake" / "lef"
        d.mkdir(parents=True)
        (d / "foo.tlef").write_text("")
        (d / "bar_tech_x.lef").write_text("")
        pdir = tmp_path / "platforms" / "fake"
        # *tech*.lef pattern is first -> wins over .tlef.
        got = resolve._resolve_tech_lef("", str(pdir))
        assert got.endswith("bar_tech_x.lef")

    def test_tech_lef_existing_primary_short_circuits(self, tmp_path):
        real = tmp_path / "real.tlef"
        real.write_text("")
        d = tmp_path / "platforms" / "fake" / "lef"
        d.mkdir(parents=True)
        (d / "other_tech.lef").write_text("")
        got = resolve._resolve_tech_lef(str(real), str(tmp_path / "platforms" / "fake"))
        assert got == str(real)


def test_resolve_dict_preserves_key_order():
    """The Python API returns the six contract keys in order (pure logic, no ORFS)."""
    # A bogus config (no file) -> make dump is skipped; we still get all six keys.
    out = resolve.resolve("/nonexistent/config.mk", "nangate45")
    assert list(out.keys()) == list(EXPECTED_KEYS)
    # Voltage degrades to the nangate profile token.
    assert out["SUPPLY_VOLTAGE"] == profile.get_profile("nangate45").supply_voltage_str


# --------------------------------------------------------------------------- #
# ORFS-backed integration tests (skip per-platform if config/ORFS absent)
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not _PLATFORMS, reason="no ORFS platform configs discoverable")
@pytest.mark.parametrize("platform", _PLATFORMS)
class TestShimMatchesModule:
    def test_shim_equals_resolve_py(self, platform):
        """The shim is a thin wrapper — its stdout must equal resolve.py's directly."""
        _require_orfs()
        cfg = _CONFIGS[platform]
        shim_out = _run_shim(cfg, platform)
        py_out = _run_resolve_py(cfg, platform)
        assert shim_out == py_out, f"{platform}: shim stdout != resolve.py stdout"

    def test_six_lines_in_contract_order(self, platform):
        _require_orfs()
        out = _run_shim(_CONFIGS[platform], platform)
        lines = out.splitlines()
        assert len(lines) == 6, f"{platform}: expected 6 lines, got {len(lines)}"
        keys = [ln.split("=", 1)[0] for ln in lines]
        assert keys == list(EXPECTED_KEYS), f"{platform}: key order {keys}"

    def test_lib_and_tech_lef_exist(self, platform):
        """Where ORFS resolves them, LIB_FILES + TECH_LEF point at existing files."""
        _require_orfs()
        out = _run_shim(_CONFIGS[platform], platform)
        vals = {}
        for ln in out.splitlines():
            k, _, v = ln.partition("=")
            vals[k] = v
        # LIB_FILES may carry several whitespace-split paths (+ trailing ws); at least one
        # must exist on disk.
        lib_tokens = vals["LIB_FILES"].split()
        assert lib_tokens, f"{platform}: empty LIB_FILES"
        assert any(os.path.isfile(t) for t in lib_tokens), f"{platform}: no LIB_FILES on disk"
        tech = vals["TECH_LEF"].strip()
        assert tech and os.path.isfile(tech), f"{platform}: TECH_LEF missing: {tech!r}"

    def test_supply_voltage_for_no_pwr_override(self, platform):
        """For the gcd / aes_core / cordic configs (no PWR override) SUPPLY_VOLTAGE
        equals the profile token.

        asap7 / gf180 ship FF-corner gcd/aes configs that DO set PWR_NETS_VOLTAGES (0.77 /
        5.5), so they legitimately differ from the profile fallback (0.70 / 5.0). For those
        we only assert the emitted value is a valid numeric token; for the others we assert
        equality with the profile token.
        """
        _require_orfs()
        out = _run_shim(_CONFIGS[platform], platform)
        sv = ""
        for ln in out.splitlines():
            if ln.startswith("SUPPLY_VOLTAGE="):
                sv = ln[len("SUPPLY_VOLTAGE="):]
        token = profile.get_profile(platform).supply_voltage_str
        if platform in ("asap7", "gf180"):
            # PWR-override platforms: just require a valid numeric token.
            assert resolve._VALID_VOLTAGE_RE.match(sv), f"{platform}: bad voltage {sv!r}"
        else:
            assert sv == token, f"{platform}: SUPPLY_VOLTAGE {sv!r} != profile {token!r}"
