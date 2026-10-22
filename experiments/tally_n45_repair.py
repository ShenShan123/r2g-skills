#!/usr/bin/env python3
"""nangate45 dev: baseline vs r2g, counted from the verdict files only.

Two numbers, kept apart because they answer different questions:

  baseline        the frozen re-run's dual-clean, PLUS designs rescued by
                  APPARATUS fixes (driver file-list hygiene, pin floor) -- those
                  fixes apply to both arms, so a rescued design enters the
                  baseline and earns r2g nothing.
  r2g             baseline PLUS designs the REPAIR arm made dual-clean that the
                  (updated) baseline did not.

A design counts once, by its latest verdict in the arm it belongs to. No
verdict is not a pass: a missing or timed-out judgement counts as not clean.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

D = Path("/proj/workarea/yangao")
DEV = [l.strip() for l in (D / "split_dev.txt").read_text().splitlines() if l.strip()]


def short(name: str) -> str:
    return "__".join(name.split("__")[:3])


def dual(r: dict) -> bool:
    return r.get("drc_status") == "clean" and r.get("lvs_status") == "clean"


def load_signoff(p: Path) -> dict:
    if not p.is_file():
        return {}
    rows = json.loads(p.read_text())
    rows = rows if isinstance(rows, list) else rows.get("designs", [])
    import re
    return {short(re.sub(r"^\d+__", "", r.get("design", ""))): r for r in rows}


def main() -> int:
    base = load_signoff(D / "rerun_nangate45_20261018/signoff.json")
    timeout = load_signoff(D / "timeout_rerun.json")       # apparatus: 6h bound
    # Apparatus rescues that enter BOTH arms. Only fixes that run under the
    # frozen formula spec count here (the pin floor batch has spec entries).
    # Designs rescued from driver defects ran at the platform default period
    # because the pilot never sized them; they add equally to both arms, so
    # they are reported as their own stratum and kept OUT of the comparison
    # rather than re-run under a formula spec at no benefit to the gap.
    app = load_signoff(D / "ppl_fix_n45" / "signoff.json")
    app.update(load_signoff(D / "ppl_fix2_n45" / "signoff.json"))   # per-design PPL correction
    rescued_other = {}
    for b in ("apparatus_fix_n45", "datafile_fix_n45"):
        rescued_other.update(load_signoff(D / b / "signoff.json"))

    rep = {}                                                # repair arm, by recipe
    for f in (D / "r2g_repair_n45/verdicts").glob("*.json"):
        v = json.loads(f.read_text())
        if v.get("cleared_at"):
            rep[short(v["design"])] = "antenna@" + v["cleared_at"]
    # Designs the baseline cannot synthesise count ONLY from their formula-spec
    # re-runs (hdr_drop_spec, sv_frontend_spec); the earlier platform-default
    # runs of the same recipes are superseded and deliberately not read.
    for recipe in ("skip_incr", "hdr_drop_spec", "aldff", "m1wide", "strip_attr", "dontuse_oai221x4", "case_rename", "no_rcx", "dontuse_ant"):
        for so in (D / "knob_ab" / recipe).glob("*/signoff.json"):
            for k, r in load_signoff(so).items():
                if dual(r):
                    rep.setdefault(k, recipe)
    for root, label in (("sv_frontend_spec", "sv_frontend"),
                        ("sv_frontend_spec_ant", "sv_frontend+antenna")):
        for so in (D / root).glob("*/signoff.json"):
            for k, r in load_signoff(so).items():
                if dual(r):
                    rep.setdefault(k, label)

    devk = {short(n) for n in DEV}
    b_frozen = {k for k in devk if k in base and dual(base[k])}
    b_app = {k for k in devk - b_frozen
             if (k in timeout and dual(timeout[k])) or (k in app and dual(app[k]))}
    baseline = b_frozen | b_app
    stratum = {k for k, r in rescued_other.items() if k in devk}
    # a repair-arm win counts only if it ran under the formula spec: designs
    # without a spec entry are listed as pending until re-run with one
    spec = json.loads((D / "spec/spec_nangate45.json").read_text())
    r2g_extra = {k: v for k, v in rep.items()
                 if k in devk and k not in baseline and k not in stratum}
    pending = {k: v for k, v in r2g_extra.items() if k not in spec}
    r2g_extra = {k: v for k, v in r2g_extra.items() if k in spec}
    # Repairs of designs that fail AT SYNTHESIS are set aside for now (user,
    # 2026-10-21): the headline gap counts back-end flow and sign-off repairs
    # only. The failure stage decides, not where the fix is applied -- the
    # OAI221_X4 dont-use acts in synthesis but repairs a sign-off DRC violation.
    SYNTH_STAGE = {"aldff", "strip_attr", "hdr_drop_spec", "sv_frontend", "sv_frontend+antenna"}
    synth_side = {k: v for k, v in r2g_extra.items() if v in SYNTH_STAGE}
    r2g_extra = {k: v for k, v in r2g_extra.items() if v not in SYNTH_STAGE}

    print("dev %d 份" % len(devk))
    print("  冻结基线双清            %4d" % len(b_frozen))
    print("  + 器具修复救回（两臂同用） %4d   -> 基线 %d" % (len(b_app), len(baseline)))
    print("  + r2g 修复臂额外双清      %4d   -> r2g %d" % (len(r2g_extra), len(baseline) + len(r2g_extra)))
    from collections import Counter
    for k, v in Counter(r2g_extra.values()).most_common():
        print("        %-16s %3d" % (k, v))
    print("  差距（r2g - 基线）        %+4d" % len(r2g_extra))
    print("  暂不计：综合阶段失败的修复成功 %d 份  %s" % (len(synth_side), dict(Counter(synth_side.values()))))
    print("  待按公式规格重跑的 r2g 成功 %3d   %s" % (len(pending), dict(Counter(pending.values()))))
    print("  单独一层：驱动缺陷救回（不进比较） %d 份，其中双清 %d"
          % (len(stratum), sum(1 for k in stratum if dual(rescued_other[k]))))
    if "-v" in sys.argv:
        for k in sorted(r2g_extra):
            print("    %s  %s" % (r2g_extra[k], k))
    return 0


if __name__ == "__main__":
    sys.exit(main())
