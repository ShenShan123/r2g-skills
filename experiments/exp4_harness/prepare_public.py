#!/usr/bin/env python3
"""Produce the same implementation-free graph contract for all four arms."""
import argparse
import json
from pathlib import Path
import shutil
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "exp4_conversion/spec_v2"))
from redact_docs import redact

TASK = """Build one reusable physical-design-to-graph dataset converter.

Read /data/spec/CONTRACT.md, SEMANTICS.md and the three accompanying format documents.
SEMANTICS.md gives the exact categorical encodings, formulas and edge schemas;
it takes precedence if a narrative document describes a different convention.
The 18 development designs are /data/dev/*, and their technology libraries are
/data/platform/{nangate45,sky130hd,gf180}. The design's config.mk gives PLATFORM.
Python with torch, torch_geometric, numpy and pandas, and OpenROAD are installed.
Produce /work/converter/run.sh with this exact interface:
    bash /converter/run.sh DESIGN_DIR PLATFORM_DIR OUTPUT_DIR
It must create OUTPUT_DIR/dataset/{b,c,d,e,f}_graph.pt and *_graph_homo.pt.
During development invoke bash /work/converter/run.sh with the same arguments.
All reusable code must be inside /work/converter. The converter is mounted
read-only for evaluation; write temporary files only under OUTPUT_DIR or /tmp.
No model, network, previous development output or agent home will be available
during production. Use installed Python/EDA libraries; do not depend on downloading.
Test your converter across the development designs and all three technologies.
Use python3 /data/spec/check_format.py OUTPUT_DIR for format feedback.
You have up to 200 model requests and 4 hours. After development the converter is
frozen and run on 60 unseen designs, with no per-design model repair. Optimize
physical correctness and full graph coverage, then total production resources.
"""

CONTRACT = """# Physical graph conversion contract

The task is final-layout B–F graph extraction only. Required files are five
HeteroData objects named b_graph.pt through f_graph.pt and five Data objects named
b_graph_homo.pt through f_graph_homo.pt, inside OUTPUT_DIR/dataset/.
The format, feature definitions, units, labels, filtering and graph reduction
rules are in SEMANTICS.md, graph-dataset.md, feature-extraction.md and
label-extraction.md. SEMANTICS.md is normative if the narrative documents differ.
Both forms must represent the same physical graph. Preserve node identities,
parallel edges, reverse edges, raw/log1p twins and documented NaN masks.

Only nonpower quantities are evaluated: connectivity and nonpower features,
wirelength, congestion, timing, ground capacitance, coupling capacitance and
resistance. Power features and IR drop values are excluded; preserve their
documented tensor slots. Do not create images, netlist graphs, or four-stage
graphs for this experiment.

Inputs are already complete: 6_final.def, 6_final.spef, 6_final.sdc, 6_final.v,
constraint.sdc, config.mk. Use final DEF and final SDC for geometry and timing;
the timing definition uses Liberty + final SDC without reading SPEF into STA.
SPEF supplies the RC quantities. Do not rerun synthesis or place-and-route.
Technology libraries are supplied separately and also installed with OpenROAD.

Numerical tolerances (absolute + relative * |truth|), in documented raw units:
wirelength 2e-4 + 1e-6; congestion 1e-6 + 1e-5; timing 1e-4 + 1e-6;
ground/coupling capacitance 1e-5 + 1e-5; resistance 1e-4 + 1e-5.
Full design acceptance also requires all graph features, topology and masks.
For label accuracy, missing finite reference values count as incorrect. The
six physical label families are averaged equally within each design; designs
are averaged equally. Copies across graph views/forms do not add weight.

The public checker checks representation, not physical correctness. It contains
no reference answers. Use it alongside your own physical checks on development
inputs. No hidden reference results will be supplied during development.
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skill", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    log = {}
    for name in ("graph-dataset.md", "feature-extraction.md", "label-extraction.md"):
        counts = {"code_blocks": 0, "code_spans": 0, "bare_refs": 0}
        (args.out / name).write_text(redact((args.skill / "references" / name).read_text(), counts))
        log[name] = counts
    shutil.copyfile(HERE / "check_format.py", args.out / "check_format.py")
    shutil.copyfile(HERE / "SEMANTICS.md", args.out / "SEMANTICS.md")
    (args.out / "CONTRACT.md").write_text(CONTRACT)
    (args.out / "task_native.txt").write_text(TASK + "\nImplement the converter yourself using the format documents and standard libraries.\n")
    (args.out / "task_r2g.txt").write_text(TASK + "\nUse the installed R2G def-graph skill at /r2g/def-graph and its graph-only entrypoint /r2g/convert_graph.sh.\n")
    (args.out / "REDACTION_LOG.json").write_text(json.dumps(log, indent=2))


if __name__ == "__main__":
    main()
