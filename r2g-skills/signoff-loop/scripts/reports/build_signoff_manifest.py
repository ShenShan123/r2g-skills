#!/usr/bin/env python3
"""Bind the strict signoff evidence bundle into one manifest (pilot P0-2, 2026-07-21).

The round-2 pilot found every project missing canonical reports/route.json,
reports/rcx.json, and reports/timing_check.json, LVS recorded as skipped, and the
Fmax winner still a placement proxy marked UNVERIFIED — so every CONSTRAINT and
SIGNOFF gate failed even where ORFS route, SPEF, and full DRC actually existed.
The evidence existed; nothing bound it. This script writes
reports/signoff_manifest.json:

  reports      per-report sha256 + the load-bearing fields (drc status/count, lvs
               status/mismatches, route residuals, rcx status, timing tier/wns)
  constraint   the SDC digest + parsed clk_period, the Fmax-search winner, and the
               FINAL timing confirmation — `fmax_qualification.qualified` is true
               only when the stamped SDC period matches the search winner AND the
               confirming full flow's timing_check tier is clean. `missing`
               ENUMERATES what still blocks qualification (pilot H3: the failure
               must name the absent final-timing confirmation, not just echo the
               matching proxy/SDC periods).
  confirming_run  the backend RUN the reports attribute themselves to (report_io
               provenance envelopes), consensus flag, and the DEF/GDS digests of
               that run's layout
  platform_capability  strict-capability summary for the project's platform
               (platform_capability.py), when the ORFS env is discoverable
  strict_clean the strict V1 verdict over the whole bundle, with `strict_missing`
               enumerating every unmet condition

Recorder, not a gate: exit 0 after writing (fail-soft), or 1 with --strict when
the bundle is not strict-clean. Emission points: fix_signoff.sh (end of every
fixing run) and tools/run_signoff.sh (per-design batch signoff).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time

REPORT_FILES = ("drc.json", "lvs.json", "route.json", "rcx.json",
                "timing_check.json", "ppa.json", "fmax_search.json")
# Relative tolerance for stamped-SDC-vs-winner period match: the stamp rounds to
# ~6 significant digits (1.0243910000000003 -> 1.02439), never more than 1e-3 off.
PERIOD_RTOL = 1e-3


def _sha256(path):
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _run_tag(doc):
    if not isinstance(doc, dict):
        return None
    prov = doc.get("provenance")
    tag = (prov or {}).get("run_tag") if isinstance(prov, dict) else None
    if not tag and isinstance(doc.get("backend_run"), str):
        tag = doc["backend_run"]
    return tag


def _newest_run_with_layout(backend):
    try:
        runs = sorted((d for d in os.listdir(backend) if d.startswith("RUN_")),
                      key=lambda d: os.path.getmtime(os.path.join(backend, d)),
                      reverse=True)
    except OSError:
        return None
    for d in runs:
        for sub in ("results", "final"):
            if os.path.isfile(os.path.join(backend, d, sub, "6_final.def")):
                return d
    return runs[0] if runs else None


def _layout_digests(backend, run):
    out = {}
    if not (backend and run):
        return out
    for kind, name in (("def_sha256", "6_final.def"), ("gds_sha256", "6_final.gds")):
        for sub in ("results", "final"):
            p = os.path.join(backend, run, sub, name)
            if os.path.isfile(p):
                out[kind] = _sha256(p)
                break
    return out


def build(project_dir):
    project_dir = os.path.realpath(project_dir)
    reports_dir = os.path.join(project_dir, "reports")
    docs, reports = {}, {}
    for fn in REPORT_FILES:
        p = os.path.join(reports_dir, fn)
        doc = _load(p)
        docs[fn] = doc
        entry = {"present": doc is not None,
                 "sha256": _sha256(p) if os.path.isfile(p) else None}
        if isinstance(doc, dict):
            for key in ("status", "total_violations", "mismatch_count", "tier",
                        "wns", "wns_ns", "drc_mode"):
                if key in doc:
                    entry[key] = doc[key]
            tag = _run_tag(doc)
            if tag:
                entry["run_tag"] = tag
        reports[fn] = entry

    # --- constraint provenance + Fmax qualification (P0-2 / H3) ------------
    sdc = os.path.join(project_dir, "constraints", "constraint.sdc")
    sdc_sha = _sha256(sdc)
    stamped = None
    try:
        with open(sdc, encoding="utf-8", errors="ignore") as f:
            m = re.search(r"set\s+clk_period\s+([\d.eE+-]+)", f.read())
        stamped = float(m.group(1)) if m else None
    except OSError:
        pass
    fmax = docs.get("fmax_search.json") or {}
    winner = ((fmax.get("winner") or {}).get("period")
              if isinstance(fmax.get("winner"), dict) else None)
    tc = docs.get("timing_check.json") or {}
    tier = tc.get("tier")
    missing = []
    if not isinstance(fmax, dict) or fmax.get("status") != "ok" or winner is None:
        missing.append("fmax_search winner (reports/fmax_search.json status=ok)")
    if stamped is None:
        missing.append("stamped SDC clk_period (constraints/constraint.sdc)")
    period_match = None
    if winner is not None and stamped is not None:
        period_match = abs(stamped - winner) <= PERIOD_RTOL * max(abs(winner), 1e-9)
        if not period_match:
            missing.append(f"stamped period {stamped} does not match search winner {winner}")
    if tier is None:
        missing.append("FINAL timing confirmation (reports/timing_check.json from the "
                       "confirming full flow) — the search winner is a placement proxy "
                       "until a finish-stage STA at the stamped period confirms it")
    elif tier != "clean":
        missing.append(f"final timing tier is {tier!r}, need 'clean' at the stamped period")
    constraint = {
        "sdc_sha256": sdc_sha,
        "stamped_clk_period": stamped,
        "fmax_winner_period": winner,
        "fmax_status": fmax.get("status") if isinstance(fmax, dict) else None,
        "period_match": period_match,
        "final_timing_tier": tier,
        "final_timing_wns": tc.get("wns", tc.get("wns_ns")),
        "qualified": not missing,
        "missing": missing,
    }

    # --- confirming run + layout binding ----------------------------------
    backend = os.path.join(project_dir, "backend")
    tags = {fn: e["run_tag"] for fn, e in reports.items() if e.get("run_tag")}
    uniq = sorted(set(tags.values()))
    selected = uniq[0] if len(uniq) == 1 else _newest_run_with_layout(backend)
    confirming = {
        "run_tag": selected,
        "attributed_reports": tags,
        "consensus": len(uniq) == 1 if tags else None,
        **_layout_digests(backend, selected),
    }
    if len(uniq) > 1:
        confirming["conflict"] = uniq

    # --- platform + strict capability (fail-soft) --------------------------
    platform = None
    cfg = os.path.join(project_dir, "constraints", "config.mk")
    try:
        with open(cfg, encoding="utf-8", errors="ignore") as f:
            m = re.search(r"^\s*(?:export\s+)?PLATFORM\s*\??=\s*(\S+)", f.read(), re.M)
        platform = m.group(1) if m else None
    except OSError:
        pass
    capability = None
    try:
        flow_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "flow")
        if flow_dir not in sys.path:
            sys.path.insert(0, flow_dir)
        import platform_capability
        fd = platform_capability.find_flow_dir()
        if fd and platform:
            caps = platform_capability.probe_platform(fd, platform)
            capability = {"strict_signoff_ready": caps.get("strict_signoff_ready"),
                          "missing": caps.get("missing", [])}
    except Exception:  # noqa: BLE001 — capability context is best-effort
        capability = None

    # --- strict V1 verdict over the bundle ---------------------------------
    strict_missing = []
    drc = docs.get("drc.json") or {}
    if drc.get("status") != "clean":
        strict_missing.append(f"drc: status={drc.get('status')!r}, need full-deck 'clean' "
                              "(clean_beol is not strict)")
    lvs = docs.get("lvs.json") or {}
    if lvs.get("status") != "clean":
        strict_missing.append(f"lvs: status={lvs.get('status')!r}, need executed 'clean' "
                              "('skipped' is not strict — pilot P0-2)")
    route = docs.get("route.json") or {}
    rv = route.get("total_violations")
    if not (route.get("status") == "clean" or rv == 0):
        strict_missing.append(f"route: status={route.get('status')!r} "
                              f"total_violations={rv!r}, need 0 residuals")
    rcx = docs.get("rcx.json") or {}
    if rcx.get("status") != "complete":
        strict_missing.append(f"rcx: status={rcx.get('status')!r}, need 'complete'")
    if tier != "clean":
        strict_missing.append(f"timing: tier={tier!r}, need 'clean'")
    if tags and len(uniq) != 1:
        strict_missing.append(f"report binding: reports name {len(uniq)} different runs {uniq}")

    return {
        "manifest_version": 1,
        "project": project_dir,
        "platform": platform,
        "generated_at": int(time.time()),
        "reports": reports,
        "constraint": constraint,
        "confirming_run": confirming,
        "platform_capability": capability,
        "strict_clean": not strict_missing,
        "strict_missing": strict_missing,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("project_dir")
    ap.add_argument("--out", default=None,
                    help="output path (default <project>/reports/signoff_manifest.json)")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 when the bundle is not strict-clean")
    args = ap.parse_args(argv)

    manifest = build(args.project_dir)
    out = args.out or os.path.join(args.project_dir, "reports", "signoff_manifest.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1)
    os.replace(tmp, out)

    state = "STRICT-CLEAN" if manifest["strict_clean"] else \
        f"not strict ({len(manifest['strict_missing'])} unmet)"
    print(f"signoff manifest: {state} -> {out}")
    for reason in manifest["strict_missing"]:
        print(f"  - {reason}")
    if args.strict and not manifest["strict_clean"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
