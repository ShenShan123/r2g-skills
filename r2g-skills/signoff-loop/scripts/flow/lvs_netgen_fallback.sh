#!/usr/bin/env bash
# nangate45 LVS fallback: KLayout extracts, Netgen compares.
#
#   lvs_netgen_fallback.sh <gds> <concat_cdl> <top> <out_dir>
#
# For a design whose KLayout LVS gave NO verdict. KLayout 0.30.7's netlist
# COMPARER crashes (SIGSEGV in sort_circuit / derive_node_identities) on some
# nangate45 designs even after run_lvs.sh's crash retries; upgrading KLayout,
# dropping the cross-reference and raising the search budget all failed
# (failure-patterns.md "LVS KLayout sort_circuit/gen_log_entry SIGSEGV").
#
# Only the comparison algorithm changes. Extraction and netlist normalisation
# are the platform deck's own lines 1-384 (assets/platforms/nangate45/
# lvs_fallback/FreePDK45_extract.lylvs) on the same GDS and the same
# concatenated CDL the KLayout LVS read; both netlists are written out and
# Netgen compares them with tolerances matching the deck's own
# (nangate45_netgen_setup.tcl). Validated on the nangate45 repair arm
# (2026-10-22): 11/11 KLayout-clean designs also match, and every negative
# control -- a pin swap, a deleted cell, two swapped flip-flop D inputs, a real
# case-folded-name mismatch -- reads mismatch. It never replaces a verdict
# KLayout gave; extract_lvs.py adopts it only for crash/incomplete/unknown.
#
# Writes <out_dir>/netgen_fallback.json:
#   {"verdict": "match"|"mismatch"|"no_verdict", "final": <Netgen's line>, ...}
# "match" is ONLY Netgen's unqualified "Final result: Circuits match uniquely.";
# any other final result (port/property errors, "Top level cell failed pin
# matching") is a mismatch; no final line means Netgen never finished.
set -uo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ASSETS="$HERE/../../assets/platforms/nangate45/lvs_fallback"
GDS="$1"; CDL="$2"; TOP="$3"; OUT="$4"
TIMEOUT="${LVS_FALLBACK_TIMEOUT:-${LVS_TIMEOUT:-21600}}"
KL="${KLAYOUT_CMD:-klayout}"; NG="${NETGEN_EXE:-netgen}"
mkdir -p "$OUT"
RES="$OUT/netgen_fallback.json"
# Isolate output names per invocation. A failed extractor/comparer must never
# consume files left by an earlier attempt in the same project.
rm -f "$RES"
OUT=$(mktemp -d "$OUT/attempt.XXXXXXXX") || exit 3
EXTRACT_RC=""; COMPARE_RC=""
INPUT_SHA=$(python3 - "$GDS" "$CDL" "$ASSETS/FreePDK45_extract.lylvs" "$ASSETS/nangate45_netgen_setup.tcl" <<'PY'
import hashlib, json, sys
def sha(path):
    try:
        h = hashlib.sha256()
        with open(path, 'rb') as stream:
            for chunk in iter(lambda: stream.read(1048576), b''): h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None
print(json.dumps({path: sha(path) for path in sys.argv[1:]}))
PY
) || exit 3

emit() {  # verdict final detail
  python3 - "$RES" "$1" "$2" "$3" "$KL" "$NG" "$OUT" "$INPUT_SHA" "$EXTRACT_RC" "$COMPARE_RC" <<'PY'
import hashlib, json, os, subprocess, sys, time
from pathlib import Path
out, verdict, final, detail, kl, ng, work, original, extract_rc, compare_rc = sys.argv[1:]
def sha(path):
    try:
        h = hashlib.sha256()
        with open(path, 'rb') as stream:
            for chunk in iter(lambda: stream.read(1048576), b''): h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None
inputs = json.loads(original)
if verdict == 'match' and (any(not digest or sha(path) != digest for path, digest in inputs.items())
                           or extract_rc != '0' or compare_rc != '0'):
    verdict, detail = 'no_verdict', 'inputs_changed_or_execution_incomplete'
def ver(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return (r.stdout or r.stderr).strip().splitlines()[0]
    except Exception:
        return None
doc = {"engine": "klayout_extract+netgen_compare", "verdict": verdict,
           "final": final or None, "detail": detail or None,
           "klayout": ver([kl, "-b", "-v"]), "netgen": ver([ng, "-batch", "quit"]),
           "extraction_exit_code": int(extract_rc) if extract_rc else None,
           "comparison_exit_code": int(compare_rc) if compare_rc else None,
           "input_sha256": inputs, "attempt_directory": Path(work).name,
           "output_sha256": {name: sha(Path(work) / name) for name in
                             ('layout.cir', 'schematic.cir', 'layout_bu.cir', 'schematic_bu.cir', 'netgen.rpt')},
           "written_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
temporary = out + '.tmp'
with open(temporary, 'w') as stream:
    json.dump(doc, stream, indent=2)
os.replace(temporary, out)
PY
  echo "netgen fallback: verdict=$1 ${2:-$3}"
}

command -v "$NG" >/dev/null 2>&1 || [[ -x "$NG" ]] || { emit no_verdict "" "netgen_not_installed"; exit 3; }
[[ -s "$GDS" ]] || { emit no_verdict "" "missing_gds"; exit 3; }
[[ -s "$CDL" ]] || { emit no_verdict "" "missing_cdl"; exit 3; }

timeout --kill-after=60 "$TIMEOUT" "$KL" -b -r "$ASSETS/FreePDK45_extract.lylvs" \
  -rd in_gds="$GDS" -rd cdl_file="$CDL" -rd report_file="$OUT/extract.lvsdb" \
  -rd target_netlist="$OUT/target.cir" -rd extracted_out="$OUT/layout.cir" \
  -rd schematic_out="$OUT/schematic.cir" > "$OUT/extract.log" 2>&1
rc=$?
EXTRACT_RC="$rc"
if [[ $rc -ne 0 || ! -s "$OUT/layout.cir" || ! -s "$OUT/schematic.cir" ]]; then
  emit no_verdict "" "extract_failed(rc=$rc)"; exit 4
fi
tl=$(python3 -I "$HERE/../extract/reorder_spice.py" "$OUT/layout.cir" "$OUT/layout_bu.cir" --top "$TOP") \
  || { emit no_verdict "" "reorder_layout_failed"; exit 4; }
ts=$(python3 -I "$HERE/../extract/reorder_spice.py" "$OUT/schematic.cir" "$OUT/schematic_bu.cir" --top "$TOP") \
  || { emit no_verdict "" "reorder_schematic_failed"; exit 4; }
( cd "$OUT" && timeout --kill-after=60 "$TIMEOUT" "$NG" -batch lvs "layout_bu.cir $tl" \
    "schematic_bu.cir $ts" "$ASSETS/nangate45_netgen_setup.tcl" netgen.rpt -json > netgen.log 2>&1 )
rc=$?
COMPARE_RC="$rc"
if [[ $rc -ne 0 ]]; then
  emit no_verdict "" "netgen_failed(rc=$rc)"; exit 4
fi
final=$(grep -h "^Final result" "$OUT/netgen.rpt" 2>/dev/null | tail -1)
case "$final" in
  "Final result: Circuits match uniquely.") emit match "$final" "" ;;
  "Final result:"*) emit mismatch "$final" "" ;;
  *) emit no_verdict "" "netgen_unfinished(rc=$rc)"; exit 4 ;;
esac
