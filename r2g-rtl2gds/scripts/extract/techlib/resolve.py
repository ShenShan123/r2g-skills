"""techlib.resolve — per-platform liberty/LEF/voltage path resolution (ORFS-only).

A faithful Python port of ``scripts/flow/resolve_platform_paths.sh`` (which is now a
thin shim that ``source``s ``_env.sh`` and ``exec``s this module). The contract is
**byte-for-byte identical** ``KEY=VALUE`` stdout, so ``run_features.sh`` /
``run_labels.sh`` / ``regen_extract_baseline.sh`` are unaffected. Task 6 of the techlib
restructure; see references/label-extraction.md + references/feature-extraction.md.

usage: resolve.py <config.mk> <platform>

Emits exactly six lines on stdout, in this order, each ``KEY=<value>\\n``:

    LIB_FILES TECH_LEF SC_LEF ADDITIONAL_LIBS ADDITIONAL_LEFS SUPPLY_VOLTAGE

Values may carry trailing whitespace straight from the ORFS Make expansion; that
whitespace is PRESERVED verbatim (the original shell ``${line#KEY=}`` did the same).

How the primary dump works
--------------------------
Like the shell, this STILL shells out to ORFS ``make`` to expand the platform vars
(``make`` resolves corner-built vars like asap7/gf180's ``LIB_FILES`` and
``PWR_NETS_VOLTAGES`` that a static config-file parse cannot). The IDENTICAL make
command is used: same ``--eval`` recipe string, ``DESIGN_CONFIG``/``PLATFORM`` args,
``unset SCRIPTS_DIR`` (popped from the subprocess env), ``cwd=FLOW_DIR``, stderr
discarded.

FLOW_DIR contract
-----------------
This module does NOT re-implement ``_env.sh`` autodetect. It reads ``$FLOW_DIR`` from
the environment (the shim ``source``s ``_env.sh`` first and exports it). If ``FLOW_DIR``
is absent it falls back to ``$ORFS_ROOT/flow``. If neither is set the primary make dump
is skipped (no Makefile to run) and only the glob fallback + voltage map apply — exactly
what the shell does when ``$FLOW_DIR/Makefile`` is missing.

Fallbacks (verbatim from the shell)
-----------------------------------
* If no resolved LIB_FILES path exists on disk → glob ``$PLATFORM_DIR/lib`` in pattern
  order ``*typical*.lib *__tt*.lib *_tt_*.lib *tt*.lib *.lib``, each ``ls -1 | grep -v
  fakeram | head -1`` (lexicographic sort, first match wins).
* If TECH_LEF is empty or missing → glob ``$PLATFORM_DIR/lef`` in pattern order
  ``*tech*.lef *.tlef *.tech.lef``, each ``ls -1 | head -1``.
* SUPPLY_VOLTAGE: parse ``PWR_NETS_VOLTAGES`` exactly like the shell
  (``tr -d '"'`` then ``awk '{print $2}'``); if that token is empty OR contains any char
  outside ``[0-9.]`` → per-platform fallback ``techlib.profile.get_profile(platform)
  .supply_voltage_str`` (the VERBATIM token, e.g. asap7 "0.70" not "0.7"). When PWR
  yields a valid token, emit it verbatim.

stdlib only (os, sys, subprocess, glob, re). Imports ``techlib.profile``.
"""
from __future__ import annotations

import glob
import os
import re
import subprocess
import sys

# When run as a plain script (python3 .../techlib/resolve.py), `techlib` is not yet on
# sys.path. Insert scripts/extract/ (the package's parent) so `from techlib import
# profile` resolves identically to the `python3 -m techlib.resolve` invocation.
if __package__ in (None, ""):
    _EXTRACT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _EXTRACT_DIR not in sys.path:
        sys.path.insert(0, _EXTRACT_DIR)

from techlib import profile  # noqa: E402

# The six KEY=VALUE lines emitted, in contract order. The first five come from the make
# dump (or fallbacks); SUPPLY_VOLTAGE is derived separately.
_OUTPUT_KEYS = (
    "LIB_FILES",
    "TECH_LEF",
    "SC_LEF",
    "ADDITIONAL_LIBS",
    "ADDITIONAL_LEFS",
    "SUPPLY_VOLTAGE",
)

# The make-eval recipe string — byte-identical to resolve_platform_paths.sh so the dump
# (and thus the parsed values) match exactly.
_MAKE_EVAL = (
    '__r2g_dump: ; @printf "%s\\n" '
    '"LIB_FILES=$(LIB_FILES)" '
    '"TECH_LEF=$(TECH_LEF)" '
    '"SC_LEF=$(SC_LEF)" '
    '"ADDITIONAL_LIBS=$(ADDITIONAL_LIBS)" '
    '"ADDITIONAL_LEFS=$(ADDITIONAL_LEFS)" '
    '"PWR_NETS_VOLTAGES=$(PWR_NETS_VOLTAGES)"'
)

# Glob patterns + order, verbatim from the shell's fallback loops.
_LIB_GLOB_PATTERNS = ("*typical*.lib", "*__tt*.lib", "*_tt_*.lib", "*tt*.lib", "*.lib")
_TECH_LEF_GLOB_PATTERNS = ("*tech*.lef", "*.tlef", "*.tech.lef")

# Mirrors the shell case glob `''|*[!0-9.]*` — a token is INVALID (triggers the
# per-platform fallback) when it is empty OR contains any char outside [0-9.].
_VALID_VOLTAGE_RE = re.compile(r"^[0-9.]+$")


def _flow_dir() -> str:
    """Resolve $FLOW_DIR (ORFS flow dir) from the environment.

    The shim ``source``s ``_env.sh`` and exports FLOW_DIR before exec'ing this module;
    we read it here rather than re-detecting. Falls back to ``$ORFS_ROOT/flow`` (matching
    ``_env.sh``'s own ``FLOW_DIR=$ORFS_ROOT/flow`` derivation). Returns "" if neither is
    set — callers then skip the primary make dump (no Makefile), as the shell does.
    """
    fd = os.environ.get("FLOW_DIR", "")
    if fd:
        return fd
    orfs = os.environ.get("ORFS_ROOT", "")
    if orfs:
        return os.path.join(orfs, "flow")
    return ""


def _abs_config(config_mk: str) -> str:
    """Absolutize CONFIG_MK iff it is a non-empty existing file (shim lines 20-25).

    The shell only absolutizes when ``-n "$CONFIG_MK" && -f "$CONFIG_MK"``; otherwise it
    leaves the value untouched (and the primary make dump is then skipped because
    ``-f "$CONFIG_MK"`` fails). We mirror that exactly.
    """
    if config_mk and os.path.isfile(config_mk):
        return os.path.join(
            os.path.abspath(os.path.dirname(config_mk)), os.path.basename(config_mk)
        )
    return config_mk


def _run_make_dump(config_mk: str, platform: str, flow_dir: str) -> dict[str, str]:
    """Run the ORFS make-eval dump and parse the 6 vars (PRIMARY source).

    Mirrors shell lines 31-48: gated on a non-empty existing CONFIG_MK and an existing
    ``$FLOW_DIR/Makefile``. ``SCRIPTS_DIR`` is removed from the subprocess env (the shell
    ``unset SCRIPTS_DIR`` — an inherited value breaks the ORFS Makefile). stderr is
    discarded; a make failure degrades to empty values (shell's ``|| true``).

    Returns a dict with keys LIB_FILES/TECH_LEF/SC_LEF/ADDITIONAL_LIBS/ADDITIONAL_LEFS
    and PWR (the PWR_NETS_VOLTAGES raw value), each defaulting to "".
    """
    parsed = {
        "LIB_FILES": "",
        "TECH_LEF": "",
        "SC_LEF": "",
        "ADDITIONAL_LIBS": "",
        "ADDITIONAL_LEFS": "",
        "PWR": "",
    }
    makefile = os.path.join(flow_dir, "Makefile") if flow_dir else ""
    if not (config_mk and os.path.isfile(config_mk) and makefile and os.path.isfile(makefile)):
        return parsed

    env = dict(os.environ)
    env.pop("SCRIPTS_DIR", None)  # shell: `unset SCRIPTS_DIR || true`

    try:
        proc = subprocess.run(
            [
                "make",
                "-f",
                "Makefile",
                "DESIGN_CONFIG=" + config_mk,
                "PLATFORM=" + platform,
                "--eval=" + _MAKE_EVAL,
                "__r2g_dump",
            ],
            cwd=flow_dir,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,  # shell: `2>/dev/null`
            text=True,
            # A pure variable-expansion dump is near-instant; a multi-second-plus stall
            # means a hung make (e.g. a stale ORFS lock). Cap it so resolve never blocks
            # indefinitely — on timeout we degrade to empty values + the glob/voltage
            # fallback, exactly as for any other make failure (the shell's `|| true`).
            timeout=120,
        )
        dump = proc.stdout
    except (OSError, subprocess.TimeoutExpired):
        # `make` not found / not executable, OR a hung make hit the timeout → degrade like
        # the shell's `|| true` (empty values; the fallbacks then apply).
        return parsed

    # Parse the same way the shell does: split into lines (each terminated by the make
    # printf's "\n"), match the KEY= prefix, take everything after it (PRESERVING any
    # trailing whitespace). `splitlines()` drops the trailing newline only, exactly like
    # `while IFS= read -r line`.
    for line in dump.splitlines():
        if line.startswith("LIB_FILES="):
            parsed["LIB_FILES"] = line[len("LIB_FILES="):]
        elif line.startswith("TECH_LEF="):
            parsed["TECH_LEF"] = line[len("TECH_LEF="):]
        elif line.startswith("SC_LEF="):
            parsed["SC_LEF"] = line[len("SC_LEF="):]
        elif line.startswith("ADDITIONAL_LIBS="):
            parsed["ADDITIONAL_LIBS"] = line[len("ADDITIONAL_LIBS="):]
        elif line.startswith("ADDITIONAL_LEFS="):
            parsed["ADDITIONAL_LEFS"] = line[len("ADDITIONAL_LEFS="):]
        elif line.startswith("PWR_NETS_VOLTAGES="):
            parsed["PWR"] = line[len("PWR_NETS_VOLTAGES="):]
    return parsed


def _first_existing_lib(lib_files: str) -> str:
    """First whitespace-split token in LIB_FILES that exists on disk ("" if none).

    Mirrors the shell ``for l in $LIB_FILES; do [[ -f "$l" ]] && ...``: unquoted ``$LIB_FILES``
    word-splits on whitespace, so we ``.split()`` (which also collapses runs of whitespace
    and ignores leading/trailing — matching shell word-splitting).
    """
    for token in lib_files.split():
        if os.path.isfile(token):
            return token
    return ""


def _ls1_first(directory: str, pattern: str, exclude_substr: str | None = None) -> str:
    """Emulate ``ls -1 <directory>/<pattern> | [grep -v <excl>] | head -1``.

    ``ls -1`` lists matching paths in lexicographic order; ``glob.glob`` is unordered so we
    ``sorted()`` to reproduce the ``ls`` sort. ``grep -v <excl>`` drops paths whose FULL
    printed path contains the substring (``ls`` prints ``directory/<name>``, so the grep
    sees the dir prefix too — matching that here). Returns "" when nothing matches.
    """
    matches = sorted(glob.glob(os.path.join(directory, pattern)))
    if exclude_substr is not None:
        matches = [m for m in matches if exclude_substr not in m]
    return matches[0] if matches else ""


def _resolve_lib_files(lib_files: str, platform_dir: str) -> str:
    """LIB_FILES with the glob fallback applied (shell lines 50-60).

    If the primary LIB_FILES has no existing path, glob ``$PLATFORM_DIR/lib`` in pattern
    order (each ``ls -1 ... | grep -v fakeram | head -1``); first pattern that yields a hit
    wins. If none hit, LIB_FILES is left as the (unmodified) primary value — same as the
    shell, where the ``for pat`` loop simply doesn't reassign.
    """
    if _first_existing_lib(lib_files):
        return lib_files
    lib_dir = os.path.join(platform_dir, "lib")
    for pat in _LIB_GLOB_PATTERNS:
        found = _ls1_first(lib_dir, pat, exclude_substr="fakeram")
        if found:
            return found
    return lib_files


def _resolve_tech_lef(tech_lef: str, platform_dir: str) -> str:
    """TECH_LEF with the glob fallback applied (shell lines 61-66).

    If TECH_LEF is empty or not an existing file, glob ``$PLATFORM_DIR/lef`` in pattern
    order (each ``ls -1 ... | head -1``); first hit wins. Otherwise unchanged.
    """
    if tech_lef and os.path.isfile(tech_lef):
        return tech_lef
    lef_dir = os.path.join(platform_dir, "lef")
    for pat in _TECH_LEF_GLOB_PATTERNS:
        found = _ls1_first(lef_dir, pat)
        if found:
            return found
    return tech_lef


def _parse_pwr_token(pwr: str) -> str:
    """The candidate voltage token from PWR_NETS_VOLTAGES (shell line 72).

    Reproduces ``echo "$PWR" | tr -d '"' | awk '{print $2}'``: strip ALL double-quotes,
    whitespace-split, take the 2nd field (index 1) if present else "". ``awk`` field
    splitting ignores leading whitespace and collapses runs, matching ``str.split()``.
    Only computed when PWR is non-empty (shell's ``if [[ -n "$PWR" ]]``); for empty PWR the
    token is "" (and the validity test then falls through to the per-platform default).
    """
    if not pwr:
        return ""
    fields = pwr.replace('"', "").split()
    return fields[1] if len(fields) >= 2 else ""


def _resolve_supply_voltage(pwr: str, platform: str) -> str:
    """SUPPLY_VOLTAGE: PWR-derived token if valid, else per-platform fallback.

    Shell lines 70-85: the token from ``_parse_pwr_token`` is valid iff non-empty AND every
    char is in ``[0-9.]`` (the ``''|*[!0-9.]*`` case glob means INVALID). A valid token is
    emitted verbatim; an invalid/empty token falls back to the per-platform map, which now
    lives in ``techlib.profile`` as ``supply_voltage_str`` (the VERBATIM shell token, e.g.
    asap7 "0.70" / gf180 "5.0"; unknown → "1.0").
    """
    token = _parse_pwr_token(pwr)
    if token and _VALID_VOLTAGE_RE.match(token):
        return token
    return profile.get_profile(platform).supply_voltage_str


def resolve(config_mk: str, platform: str = "nangate45") -> dict[str, str]:
    """Resolve the six platform vars, returning an ordered-by-contract dict.

    Keys: LIB_FILES, TECH_LEF, SC_LEF, ADDITIONAL_LIBS, ADDITIONAL_LEFS, SUPPLY_VOLTAGE.
    Values preserve any trailing whitespace from the make expansion (the contract). This
    is the Python API; ``main()`` prints these as ``KEY=VALUE`` lines.
    """
    platform = platform or "nangate45"
    config_mk = _abs_config(config_mk)
    flow_dir = _flow_dir()
    # platform_dir anchors the glob fallbacks under $FLOW_DIR/platforms/<plat>. With no
    # FLOW_DIR (ORFS absent), this degrades to a RELATIVE "platforms/<plat>" that won't
    # exist on disk — so glob.glob returns nothing and the lib/tech-LEF fallbacks correctly
    # yield "" (the make dump was already skipped). Net effect mirrors the shell when
    # $FLOW_DIR/Makefile is missing: empty paths + the per-platform voltage default.
    platform_dir = os.path.join(flow_dir, "platforms", platform) if flow_dir else os.path.join(
        "platforms", platform
    )

    dump = _run_make_dump(config_mk, platform, flow_dir)

    lib_files = _resolve_lib_files(dump["LIB_FILES"], platform_dir)
    tech_lef = _resolve_tech_lef(dump["TECH_LEF"], platform_dir)
    supply_voltage = _resolve_supply_voltage(dump["PWR"], platform)

    return {
        "LIB_FILES": lib_files,
        "TECH_LEF": tech_lef,
        "SC_LEF": dump["SC_LEF"],
        "ADDITIONAL_LIBS": dump["ADDITIONAL_LIBS"],
        "ADDITIONAL_LEFS": dump["ADDITIONAL_LEFS"],
        "SUPPLY_VOLTAGE": supply_voltage,
    }


def main(argv: list[str] | None = None) -> int:
    """CLI: print the six KEY=VALUE lines exactly like the original shell.

    Each line is ``KEY=<value>\\n`` (the shell's ``printf "KEY=%s\\n"``), in contract order.
    No diagnostics on stdout; any chatter belongs on stderr (the shim keeps _env.sh's
    output on stderr). Mirrors ``PLATFORM="${2:-nangate45}"`` for a missing platform arg.
    """
    args = sys.argv[1:] if argv is None else argv
    config_mk = args[0] if len(args) >= 1 else ""
    platform = args[1] if len(args) >= 2 else "nangate45"

    resolved = resolve(config_mk, platform)
    out = []
    for key in _OUTPUT_KEYS:
        out.append("%s=%s\n" % (key, resolved[key]))
    sys.stdout.write("".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
