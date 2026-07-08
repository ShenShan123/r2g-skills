#!/usr/bin/env bash
set -euo pipefail
# Install an antenna model into the ORFS nangate45 tech/SC LEFs so OpenROAD's
# repair_antennas actually fires on this platform.
#
# Why: the stock nangate45 tech LEF ships NO per-layer antenna ratio rules, so
# OpenROAD check_antennas finds nothing to repair, while KLayout's FreePDK45.lydrc
# signoff deck flags antennas at 300:1.  The only diode (ANTENNA_X1) also has
# ANTENNADIFFAREA 0.0, which OpenROAD rejects (GRT-0246 "No diode ... found").
# This installs:
#   * ANTENNAMODEL OXIDE1 + ANTENNAAREARATIO <ratio> on every routing layer
#     (default 300 — MATCHES the signoff deck, does NOT relax it), and
#   * a non-zero ANTENNADIFFAREA on ANTENNA_X1 so repair_antennas accepts it.
#
# This is a tech-model input to the router; it does NOT touch any DRC/LVS *rule
# deck* (the KLayout 300:1 grader is unchanged).  See references/signoff-fixing.md.
#
# Idempotent.  Backs up originals once to *.r2g-pre-antenna.orig.
# Usage:
#   install_nangate45_antenna.sh [--ratio 300] [--diff-area 0.1]
#   install_nangate45_antenna.sh --uninstall      # restore the stock LEFs
#   install_nangate45_antenna.sh --status         # report install state

RATIO=300; DIFF_AREA=0.1; ACTION=install
while [[ $# -gt 0 ]]; do
  case "$1" in
    --ratio) RATIO="$2"; shift 2;;
    --diff-area) DIFF_AREA="$2"; shift 2;;
    --uninstall) ACTION=uninstall; shift;;
    --status) ACTION=status; shift;;
    -*) echo "unknown flag: $1" >&2; exit 1;;
    *) echo "unexpected arg: $1" >&2; exit 1;;
  esac
done

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
# shellcheck source=/dev/null
source "$REPO/r2g-skills/signoff-loop/scripts/flow/_env.sh" >/dev/null 2>&1 || true
: "${FLOW_DIR:?ORFS FLOW_DIR not found; set ORFS_ROOT}"

PLAT="$FLOW_DIR/platforms/nangate45"
TECH="$PLAT/lef/NangateOpenCellLibrary.tech.lef"
SC="$PLAT/lef/NangateOpenCellLibrary.macro.mod.lef"
# Reference LEF carrying the full per-pin antenna model (same cell set as SC_LEF,
# but ORFS's .mod SC_LEF has it stripped — we merge it back).
REF="$PLAT/lef/NangateOpenCellLibrary.macro.lef"
PATCHER="$REPO/r2g-skills/signoff-loop/scripts/flow/antenna_lef_patch.py"
SUFFIX=".r2g-pre-antenna.orig"

for f in "$TECH" "$SC" "$REF"; do
  [[ -f "$f" ]] || { echo "ERROR: LEF not found: $f" >&2; exit 1; }
done

_ratios()   { grep -c '^[[:space:]]*ANTENNAAREARATIO[[:space:]]' "$TECH" 2>/dev/null || true; }
_gateareas(){ grep -c '^[[:space:]]*ANTENNAGATEAREA[[:space:]]' "$SC" 2>/dev/null || true; }
_diodeval() { python3 - "$SC" <<'PY' 2>/dev/null || true
import re,sys
inc=False
for l in open(sys.argv[1]):
    if re.match(r'^\s*MACRO\s+ANTENNA_X1\b',l): inc=True
    elif inc and re.match(r'^\s*END\s+ANTENNA_X1\b',l): break
    elif inc:
        m=re.search(r'ANTENNADIFFAREA\s+([0-9.eE+-]+)',l)
        if m: print(m.group(1)); break
PY
}

if [[ "$ACTION" == "status" ]]; then
  echo "tech LEF: $TECH"
  echo "  routing layers with ANTENNAAREARATIO: $(_ratios)/10"
  echo "  backup present: $([[ -f "$TECH$SUFFIX" ]] && echo yes || echo no)"
  echo "SC LEF: $SC"
  echo "  std-cell pins with ANTENNAGATEAREA: $(_gateareas)"
  echo "  ANTENNA_X1 ANTENNADIFFAREA: $(_diodeval)"
  exit 0
fi

if [[ "$ACTION" == "uninstall" ]]; then
  restored=0
  for f in "$TECH" "$SC"; do
    if [[ -f "$f$SUFFIX" ]]; then cp "$f$SUFFIX" "$f"; rm -f "$f$SUFFIX"; restored=1; echo "restored $f"; fi
  done
  [[ "$restored" == "1" ]] && echo "Uninstalled nangate45 antenna model." \
    || echo "Nothing to uninstall (no $SUFFIX backups found)."
  exit 0
fi

# install: back up once, then patch in place (idempotent)
[[ -f "$TECH$SUFFIX" ]] || cp "$TECH" "$TECH$SUFFIX"
[[ -f "$SC$SUFFIX" ]]   || cp "$SC" "$SC$SUFFIX"

python3 "$PATCHER" tech --in "$TECH" --out "$TECH" --ratio "$RATIO"
python3 "$PATCHER" sc   --in "$SC"   --out "$SC"   --diff-area "$DIFF_AREA" --ref "$REF"

n="$(_ratios)"; g="$(_gateareas)"
if [[ "$n" -ne 10 ]]; then
  echo "ERROR: expected 10 routing layers with ANTENNAAREARATIO, found $n" >&2
  exit 1
fi
if [[ "$g" -lt 100 ]]; then
  echo "ERROR: expected std-cell ANTENNAGATEAREA pins (~388), found $g — pin merge failed" >&2
  exit 1
fi
echo "Installed nangate45 antenna model: ratio=$RATIO on $n routing layers; $g std-cell gate-area pins; ANTENNA_X1 diff-area=$DIFF_AREA"
echo "  (signoff KLayout 300:1 deck untouched; uninstall with --uninstall)"
