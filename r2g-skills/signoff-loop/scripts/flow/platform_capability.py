#!/usr/bin/env python3
"""Per-platform STRICT-signoff capability manifest (pilot round-2 P0-3 / GC-ENV-07).

A green check_env.sh proves the EXECUTABLES exist; it says nothing about whether
the selected ORFS platform can satisfy a strict V1 signoff. The 2026-07-21 pilot
proved the gap: ENV passed while nangate45 had no KLayout LVS rule installed and
its only diode (ANTENNA_X1) carried ANTENNADIFFAREA 0.0 — OpenROAD then emits
GRT-0246 and antenna repair is a no-op — so every strict SIGNOFF/CONSTRAINT gate
was unreachable, discovered only AFTER multi-hour flows. This probe answers, per
platform and BEFORE any flow starts:

  drc_deck   full KLayout DRC deck resolvable (KLAYOUT_DRC_FILE, drc/*.lydrc,
             or the deliberate sky130hs<-sky130hd sibling borrow, failure-patterns #32)
  lvs        a working LVS path: KLayout deck (KLAYOUT_LVS_FILE / lvs/*.lylvs)
             on non-sky130 platforms, or the Magic+Netgen+PDK triple on sky130
             (mirrors fix_signoff.sh's platform-aware engine selection)
  antenna    a usable antenna model: >=1 routing layer carrying an ANTENNA*AREARATIO
             family rule in the tech LEF AND a CLASS CORE ANTENNACELL diode whose
             ANTENNADIFFAREA is > 0 (OpenROAD rejects a 0.0-area diode;
             tools/install_nangate45_antenna.sh installs both on nangate45)
  rcx        RCX_RULES resolvable (OpenRCX extraction rules)
  timing     LIB_FILES first liberty resolvable

`strict_signoff_ready` is the AND of all five. Consumers:
  * check_env.sh — prints the per-platform capability table; set
    R2G_STRICT_PLATFORMS="nangate45 sky130hd" to make readiness REQUIRED
  * diagnose_signoff_fix.py — refuses to rank a diode-forced antenna repair
    when the antenna model is provably unusable (pilot P1-1)
  * the V1 registry GC-ENV-07 command condition (strict ENV oracle, pilot H1)

Exit codes: 0 ok / report-only; 1 with --strict when any selected platform is
not strict-ready; 2 usage/environment errors (no flow dir).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import sys

# Deliberate sibling deck borrows (NEVER generic): sky130hs uses the sky130hd
# deck — pure sky130A process-layer geometry, shared tech (failure-patterns #32).
SIBLING_DRC_DECK = {"sky130hs": ("sky130hd", "drc/sky130hd.lydrc")}

# Platforms whose KLayout .lyt MUST carry the modern lefdef reader options
# (RMD-P0-04, three-platform pilot 2026-07-22 / failure-patterns #33): this
# ORFS ships sky130hs.lyt with LEGACY option names, so KLayout's def2stream
# merge silently drops ALL DEF-derived geometry — routing, vias, pin rects,
# special (power) routing. Magic then extracts a portless top subckt and every
# Netgen LVS is invalid (all four pilot fixtures). Tool presence alone is NOT a
# sufficient readiness oracle: the probe must verify the postcondition of
# tools/patch_sky130hs_lyt.py. Scoped to the known-broken platform only (the
# same deliberate-pair philosophy as SIBLING_DRC_DECK).
MODERN_LYT_REQUIRED = {"sky130hs"}


def _lyt_modern(platform_dir: str, platform: str) -> bool | None:
    """True when <platform>.lyt carries the modern lefdef reader options
    (mirrors tools/patch_sky130hs_lyt.py --check); None when unreadable."""
    text = _read(os.path.join(platform_dir, f"{platform}.lyt"))
    if text is None:
        return None
    return ("<routing-datatype-string>" in text
            and "<produce-special-routing>" in text)


def find_flow_dir(explicit: str | None = None) -> str | None:
    """ORFS flow dir: explicit arg > $FLOW_DIR > $ORFS_ROOT/flow > common roots."""
    for cand in (explicit, os.environ.get("FLOW_DIR"),
                 os.path.join(os.environ.get("ORFS_ROOT", ""), "flow")
                 if os.environ.get("ORFS_ROOT") else None,
                 os.path.expanduser("~/OpenROAD-flow-scripts/flow"),
                 "/opt/OpenROAD-flow-scripts/flow"):
        if cand and os.path.isdir(os.path.join(cand, "platforms")):
            return os.path.realpath(cand)
    return None


def _mk_var(text: str, key: str) -> str | None:
    """First value token of a platform config.mk variable (continuations dropped:
    for LIB_FILES-style lists the FIRST file is the representative probe)."""
    m = re.search(rf"^\s*(?:export\s+)?{re.escape(key)}\s*\??=\s*(.*)$", text, re.M)
    if not m:
        return None
    raw = m.group(1).strip().rstrip("\\").strip()
    return raw.split()[0] if raw.split() else None


def _resolve(raw: str | None, platform_dir: str, platform: str) -> str | None:
    if not raw:
        return None
    out = raw.replace("$(PLATFORM_DIR)", platform_dir).replace("$(PLATFORM)", platform)
    return out if "$(" not in out else None  # unresolvable make var (e.g. $(PRIMARY_VT_TAG))


def find_antenna_diodes(sc_lef_text: str) -> list[tuple[str, float | None]]:
    """(cell, ANTENNADIFFAREA) for every MACRO declaring CLASS ... ANTENNACELL."""
    diodes: list[tuple[str, float | None]] = []
    cell, is_diode, area = None, False, None
    for line in sc_lef_text.split("\n"):
        m = re.match(r"^\s*MACRO\s+(\S+)", line)
        if m:
            cell, is_diode, area = m.group(1), False, None
            continue
        if cell is None:
            continue
        if re.search(r"^\s*CLASS\s+.*\bANTENNACELL\b", line):
            is_diode = True
        m = re.search(r"^\s*ANTENNADIFFAREA\s+([0-9.eE+-]+)", line)
        if m:
            try:
                area = float(m.group(1))
            except ValueError:
                area = None
        if re.match(rf"^\s*END\s+{re.escape(cell)}\b", line):
            if is_diode:
                diodes.append((cell, area))
            cell = None
    return diodes


def _which(env_key: str, *names: str) -> str | None:
    exe = os.environ.get(env_key)
    if exe and os.access(exe, os.X_OK):
        return exe
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def _read(path: str | None) -> str | None:
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def probe_platform(flow_dir: str, platform: str) -> dict:
    pdir = os.path.join(flow_dir, "platforms", platform)
    caps: dict = {"platform": platform, "platform_dir": pdir}
    cfg = _read(os.path.join(pdir, "config.mk"))
    if cfg is None:
        caps.update(status="missing_platform", strict_signoff_ready=False,
                    missing=["platform_dir"])
        return caps

    # --- full DRC deck ---------------------------------------------------
    deck = _resolve(_mk_var(cfg, "KLAYOUT_DRC_FILE"), pdir, platform)
    source = "config.mk"
    if not (deck and os.path.isfile(deck)):
        found = sorted(glob.glob(os.path.join(pdir, "drc", "*.lydrc")))
        deck, source = (found[0], "platform drc/") if found else (None, None)
    if not deck and platform in SIBLING_DRC_DECK:
        sib, rel = SIBLING_DRC_DECK[platform]
        cand = os.path.join(flow_dir, "platforms", sib, rel)
        if os.path.isfile(cand):
            deck, source = cand, f"sibling:{sib} (failure-patterns #32)"
    caps["drc_deck"] = {"ok": bool(deck), "path": deck, "source": source}

    # --- LVS path (mirrors fix_signoff.sh's engine selection) ------------
    if platform.startswith("sky130"):
        magic = _which("MAGIC_EXE", "magic")
        netgen = _which("NETGEN_EXE", "netgen", "netgen-lvs")
        pdk = os.environ.get("PDK_ROOT") or ""
        tech = os.path.join(pdk, "sky130A", "libs.tech", "magic", "sky130A.tech")
        setup = os.path.join(pdk, "sky130A", "libs.tech", "netgen", "sky130A_setup.tcl")
        ok = bool(magic and netgen and os.path.isfile(tech) and os.path.isfile(setup))
        caps["lvs"] = {"ok": ok, "engine": "netgen",
                       "magic": magic, "netgen": netgen,
                       "pdk_tech": tech if os.path.isfile(tech) else None,
                       "pdk_setup": setup if os.path.isfile(setup) else None}
        # GDS-import postcondition (RMD-P0-04): a legacy .lyt makes every
        # Netgen LVS verdict invalid (def2stream drops all DEF geometry ->
        # portless Magic extraction), so LVS is NOT capable regardless of the
        # tool triple. Verified here, before any flow spends hours.
        if platform in MODERN_LYT_REQUIRED:
            lyt_ok = _lyt_modern(pdir, platform)
            caps["lvs"]["lyt_modern"] = lyt_ok
            if lyt_ok is not True:
                caps["lvs"]["ok"] = False
                caps["lvs"]["hint"] = ("run tools/patch_sky130hs_lyt.py, then regenerate "
                                       "GDS from finish (failure-patterns #33 / RMD-P0-04)")
    else:
        rule = _resolve(_mk_var(cfg, "KLAYOUT_LVS_FILE"), pdir, platform)
        if not (rule and os.path.isfile(rule)):
            found = (sorted(glob.glob(os.path.join(pdir, "lvs", "*.lylvs")))
                     or sorted(glob.glob(os.path.join(pdir, "*.lylvs"))))
            rule = found[0] if found else None
        hint = None
        if not rule and platform == "nangate45":
            hint = "run tools/install_nangate45_lvs.sh (bundled FreePDK45.lylvs)"
        caps["lvs"] = {"ok": bool(rule), "engine": "klayout", "path": rule,
                       **({"hint": hint} if hint else {})}

    # --- antenna model + usable diode ------------------------------------
    tech_lef = _resolve(_mk_var(cfg, "TECH_LEF"), pdir, platform)
    sc_lef = _resolve(_mk_var(cfg, "SC_LEF"), pdir, platform)
    tech_text, sc_text = _read(tech_lef), _read(sc_lef)
    # Any per-layer antenna-ratio family rule counts as "model present":
    # nangate45 (patched) uses ANTENNAAREARATIO; sky130 ships
    # ANTENNADIFFAREARATIO / ANTENNADIFFSIDEAREARATIO instead.
    ratio_layers = len(re.findall(
        r"^\s*ANTENNA(?:DIFF)?(?:SIDE)?AREARATIO\s", tech_text or "", re.M))
    diodes = find_antenna_diodes(sc_text or "")
    usable = [c for c, a in diodes if a is not None and a > 0]
    hint = None
    if platform == "nangate45" and (ratio_layers == 0 or not usable):
        hint = "run tools/install_nangate45_antenna.sh (model + ANTENNADIFFAREA patch)"
    caps["antenna"] = {
        "ok": bool(ratio_layers > 0 and usable),
        "tech_lef": tech_lef, "sc_lef": sc_lef,
        "ratio_layers": ratio_layers,
        "diodes": [{"cell": c, "diff_area": a} for c, a in diodes],
        "usable_diodes": usable,
        **({"hint": hint} if hint else {}),
    }
    if tech_text is None or sc_text is None:
        # Cannot read the LEFs (unresolvable make var / missing file): honest
        # unknown, not a fabricated fail — but never strict-ready either.
        caps["antenna"]["ok"] = False
        caps["antenna"]["detail"] = "tech/SC LEF unreadable or unresolvable"

    # --- RCX rules + timing libs -----------------------------------------
    rcx = _resolve(_mk_var(cfg, "RCX_RULES"), pdir, platform)
    caps["rcx"] = {"ok": bool(rcx and os.path.isfile(rcx)), "path": rcx}
    lib = _resolve(_mk_var(cfg, "LIB_FILES"), pdir, platform)
    if not (lib and os.path.isfile(lib)):
        found = sorted(glob.glob(os.path.join(pdir, "lib", "*.lib*")))
        lib = found[0] if found else None
    caps["timing"] = {"ok": bool(lib), "lib": lib}

    missing = [k for k in ("drc_deck", "lvs", "antenna", "rcx", "timing")
               if not caps[k]["ok"]]
    caps["missing"] = missing
    caps["strict_signoff_ready"] = not missing
    # Explicit capability tiers (RMD-P0-03): `installed` (the platform dir
    # exists), `research_ready` (flows + timing + a DRC deck — enough for
    # research-tier datasets), `strict_signoff_ready` (all five capabilities —
    # the ONLY tier that may enter a strict V1 campaign). Conflating these is
    # how ENV credit was awarded on platforms that could never satisfy the
    # required signoff policy.
    if not missing:
        caps["tier"] = "strict_signoff_ready"
    elif caps["drc_deck"]["ok"] and caps["timing"]["ok"]:
        caps["tier"] = "research_ready"
    else:
        caps["tier"] = "installed"
    return caps


def antenna_repair_usable(platform: str, flow_dir: str | None = None):
    """Precondition probe for diode-forced antenna repair (pilot P1-1).

    Returns (usable, reason): True when a positive-diff-area ANTENNACELL diode
    AND tech antenna ratios exist; False with a concrete reason when the model
    is PROVABLY unusable; None when the environment cannot be inspected (no
    flow dir / unreadable LEFs) — callers must FAIL OPEN on None, since blocking
    a repair on missing introspection would regress working setups."""
    fd = find_flow_dir(flow_dir)
    if not fd:
        return None, "ORFS flow dir not discoverable (FLOW_DIR/ORFS_ROOT unset)"
    caps = probe_platform(fd, platform)
    ant = caps.get("antenna") or {}
    if caps.get("status") == "missing_platform" or ant.get("detail"):
        return None, ant.get("detail") or "platform dir missing"
    if ant.get("ok"):
        return True, f"usable diode(s): {', '.join(ant['usable_diodes'])}"
    if ant.get("ratio_layers", 0) == 0:
        return False, ("tech LEF carries no ANTENNA*AREARATIO rule on any routing layer "
                       "(OpenROAD check_antennas finds nothing to repair)"
                       + (f"; {ant['hint']}" if ant.get("hint") else ""))
    return False, ("no CLASS CORE ANTENNACELL diode with ANTENNADIFFAREA > 0 "
                   "(GRT-0246: repair_antennas rejects a 0-area diode)"
                   + (f"; {ant['hint']}" if ant.get("hint") else ""))


def _summary_line(caps: dict) -> str:
    if caps.get("status") == "missing_platform":
        return f"{caps['platform']:<12} MISSING platform dir"
    flags = " ".join(
        f"{k}={'ok' if caps[k]['ok'] else 'MISS'}"
        for k in ("drc_deck", "lvs", "antenna", "rcx", "timing"))
    state = "STRICT-READY" if caps["strict_signoff_ready"] else \
        f"{caps.get('tier', 'installed')} (" + ",".join(caps["missing"]) + ")"
    return f"{caps['platform']:<12} {state:<28} {flags}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--flow-dir", help="ORFS flow dir (default: $FLOW_DIR / $ORFS_ROOT/flow)")
    ap.add_argument("--platform", action="append", default=[],
                    help="platform to probe; repeatable (default: every platforms/ dir)")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 unless every selected platform is strict_signoff_ready")
    ap.add_argument("--summary", action="store_true",
                    help="human-readable table instead of JSON")
    ap.add_argument("--out", help="also write the JSON manifest to this path")
    args = ap.parse_args(argv)

    flow_dir = find_flow_dir(args.flow_dir)
    if not flow_dir:
        print("ERROR: no ORFS flow dir (set FLOW_DIR or ORFS_ROOT, or pass --flow-dir)",
              file=sys.stderr)
        return 2
    platforms = args.platform or sorted(
        os.path.basename(p) for p in glob.glob(os.path.join(flow_dir, "platforms", "*"))
        if os.path.isfile(os.path.join(p, "config.mk")))  # skip helper dirs (common/, io libs)
    manifest = {"flow_dir": flow_dir,
                "platforms": {p: probe_platform(flow_dir, p) for p in platforms}}
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=1)
    if args.summary:
        for p in platforms:
            print(_summary_line(manifest["platforms"][p]))
    else:
        print(json.dumps(manifest, indent=1))
    if args.strict:
        bad = [p for p in platforms
               if not manifest["platforms"][p].get("strict_signoff_ready")]
        if bad:
            print(f"strict platform capability: FAIL ({', '.join(bad)})", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
