#!/usr/bin/env python3
"""Index ASAP7 pilot / synth-only run directories into one small JSON per run (sizes + pin count), no layouts.

  index_runs.py ROOT [ROOT ...] > index.jsonl

Used to bring 203's pilot sizes to 216's spec builder as a few MB of metadata rather than its layouts. Each line:
  {"tag", "root", "result": result.json, "synth_stat": text of the top synth_stat.txt,
   "pins_def": PINS count from 6_final.def's header (null without a DEF),
   "pins_netlist": signal port bits of the top module in 1_synth.v (null without it),
   "ppl0024": the PPL-0024 refusal line if the run hit one}
"""
import glob
import json
import os
import re
import sys

PORT_RE = re.compile(r"^\s*(input|output|inout)\s+(?:wire\s+|reg\s+)?(\[(\-?\d+):(\-?\d+)\]\s*)?([^;]+);", re.M)


def netlist_pins(path: str, top: str | None) -> int | None:
    try:
        t = open(path, errors="replace").read()
    except OSError:
        return None
    m = re.search(r"^module\s+\\?%s\s*\(" % re.escape(top), t, re.M) if top else None
    if not m:
        return None
    end = t.find("endmodule", m.start())
    body = t[m.start():end]
    n = 0
    for d in PORT_RE.finditer(body):
        width = abs(int(d.group(3)) - int(d.group(4))) + 1 if d.group(2) else 1
        names = [x for x in d.group(5).split(",") if x.strip()]
        n += width * len(names)
    return n


def main() -> None:
    for root in sys.argv[1:]:
        for rj in sorted(glob.glob(os.path.join(root, "runs", "*", "result.json"))):
            rd = os.path.dirname(rj)
            try:
                res = json.load(open(rj))
            except ValueError:
                continue
            rec = {"tag": os.path.basename(rd), "root": root, "result": res}
            st = sorted(glob.glob(os.path.join(rd, "reports", "*", "*", "*", "synth_stat.txt")))
            rec["synth_stat"] = open(st[0], errors="replace").read() if st else None
            rec["synth_stat_relpath"] = os.path.relpath(st[0], rd) if st else None
            pins = None
            d = os.path.join(rd, "6_final.def")
            if os.path.isfile(d):
                head = open(d, errors="replace").read(200000)
                m = re.search(r"^PINS (\d+)", head, re.M)
                pins = int(m.group(1)) if m else None
            rec["pins_def"] = pins
            top = None
            cfg = os.path.join(rd, "config.mk")
            if os.path.isfile(cfg):
                m = re.search(r"^export DESIGN_NAME\s*=\s*(\S+)", open(cfg).read(), re.M)
                top = m.group(1) if m else None
            rec["pins_netlist"] = netlist_pins(os.path.join(rd, "1_synth.v"), top)
            rec["ppl0024"] = None
            for lg in glob.glob(os.path.join(rd, "logs", "*", "*", "*", "3_2_place_iop.log")):
                m = re.search(r"\[ERROR PPL-0024\][^\n]*", open(lg, errors="replace").read())
                if m:
                    rec["ppl0024"] = m.group(0)
                    rec["ppl_log_relpath"] = os.path.relpath(lg, rd)
            print(json.dumps(rec))


if __name__ == "__main__":
    main()
