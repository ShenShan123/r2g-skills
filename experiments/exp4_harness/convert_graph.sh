#!/usr/bin/env bash
# R2G-only, graph-only physical-input adapter. Never mounted in native arms.
set -euo pipefail
D=$(realpath "$1"); P=$(realpath "$2"); O=$(realpath -m "$3")
S=/r2g/def-graph
mkdir -p "$O/project/constraints"
J="$O/project"
python3 - "$D/config.mk" "$J/constraints/config.mk" <<'PY'
import re, sys
text = open(sys.argv[1]).read()
with open(sys.argv[2], 'w') as f:
    for name in ('DESIGN_NAME', 'PLATFORM'):
        value = re.search(r'^\s*(?:export\s+)?' + name + r'\s*[:?+]?=\s*(\S+)', text, re.M).group(1)
        f.write(f'export {name} = {value}\n')
PY
cp "$D/constraint.sdc" "$J/constraints/constraint.sdc"
PL=$(sed -n 's/^export PLATFORM = //p' "$J/constraints/config.mk")
export R2G_IGNORE_ENV_LOCAL=1
export R2G_DEF="$D/6_final.def" R2G_SPEF="$D/6_final.spef"
export R2G_GRAPH_KIND=both R2G_GRAPH_VARIANTS=bcdef R2G_GRAPH_PYTHON=/opt/py/bin/python3
export R2G_SIGNOFF_GATE=warn
export ORFS_ROOT=/r2g_toolchain/OpenROAD-flow-scripts
# DEF timing fallback intentionally omits ODB-only IR drop: this study scores B--F nonpower labels.
bash "$S/scripts/flow/run_graphs.sh" "$J" "$PL"
mkdir -p "$O/dataset"
for v in b c d e f; do
  cp "$J/dataset/${v}_graph.pt" "$J/dataset/${v}_graph_homo.pt" "$O/dataset/"
done
cp "$J/dataset/graph_manifest.json" "$O/dataset/"
