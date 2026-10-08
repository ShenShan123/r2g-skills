#!/usr/bin/env bash
set -euo pipefail

# usage: run_netgen_lvs.sh <project-dir> [platform]
# Runs Netgen LVS on a completed ORFS backend run.
# Alternative to KLayout-based run_lvs.sh — uses Netgen for layout-vs-schematic.
# Workflow: Magic extracts SPICE from GDS, then Netgen compares against Verilog netlist.
# Supported platforms: sky130hd, sky130hs (requires sky130A PDK at /opt/pdks/sky130A)
# Results are collected into <project-dir>/lvs/

PROJECT_DIR="${1:-}"
PLATFORM="${2:-sky130hd}"
# Derive FLOW_VARIANT from project directory basename (matching run_orfs.sh logic)
if [[ -n "${3:-}" ]]; then
  FLOW_VARIANT="$3"
elif [[ -n "$PROJECT_DIR" && -d "$PROJECT_DIR" ]]; then
  FLOW_VARIANT="$(basename "$(cd "$PROJECT_DIR" && pwd)")"
else
  FLOW_VARIANT="base"
fi
# Auto-detect ORFS + tools (honors ORFS_ROOT / PDK_ROOT / *_EXE env overrides)
# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/_env.sh"
# Bounded process-group checker supervisor (RMD2-P0-01)
# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/_bounded_run.sh"
# Cancellation must never orphan a tool (openroad/magic/netgen): reap the whole
# checker session on any exit path (same contract as run_drc.sh / run_lvs.sh).
trap 'r2g_bounded_cleanup' EXIT
trap 'r2g_bounded_cleanup; exit 130' INT
trap 'r2g_bounded_cleanup; exit 143' TERM

if [[ -z "${ORFS_ROOT:-}" || ! -d "$FLOW_DIR" ]]; then
  echo "ERROR: ORFS not found. Set ORFS_ROOT to your OpenROAD-flow-scripts checkout." >&2
  exit 1
fi

if [[ -z "$PROJECT_DIR" ]]; then
  echo "usage: run_netgen_lvs.sh <project-dir> [platform]" >&2
  exit 1
fi

PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"
CONFIG_MK="$PROJECT_DIR/constraints/config.mk"

if [[ ! -f "$CONFIG_MK" ]]; then
  echo "ERROR: config.mk not found at $CONFIG_MK" >&2
  exit 1
fi

# Verify tools are installed (honor MAGIC_EXE / NETGEN_EXE overrides)
if [[ -z "${MAGIC_EXE:-}" ]] && ! command -v magic &>/dev/null; then
  echo "ERROR: magic not found. Set MAGIC_EXE or install magic." >&2
  exit 1
fi
: "${MAGIC_EXE:=$(command -v magic)}"

if [[ -z "${NETGEN_EXE:-}" ]]; then
  if command -v netgen &>/dev/null; then
    NETGEN_EXE="$(command -v netgen)"
  elif command -v netgen-lvs &>/dev/null; then
    NETGEN_EXE="$(command -v netgen-lvs)"
  else
    echo "ERROR: netgen/netgen-lvs not found. Set NETGEN_EXE or install netgen." >&2
    exit 1
  fi
fi
NETGEN_CMD="$NETGEN_EXE"

DESIGN_NAME=$(grep 'DESIGN_NAME' "$CONFIG_MK" | head -1 | sed 's/.*=\s*//' | tr -d ' ')

# Map platform to PDK files
MAGIC_TECH=""
NETGEN_SETUP=""
case "$PLATFORM" in
  sky130hd|sky130hs)
    MAGIC_TECH="$PDK_ROOT/sky130A/libs.tech/magic/sky130A.tech"
    NETGEN_SETUP="$PDK_ROOT/sky130A/libs.tech/netgen/sky130A_setup.tcl"
    ;;
  gf180)
    # gf180mcu ships the same tree shape as sky130A (open_pdks.gf180mcuc), so
    # the whole Magic-extract + Netgen-compare path is reused unchanged; only
    # these paths and the cell-library names below differ. The C variant is what
    # open_pdks packages; a future A/B variant would need its own branch rather
    # than a glob, so a missing PDK stays a clear error instead of a wrong tech.
    MAGIC_TECH="$PDK_ROOT/gf180mcuC/libs.tech/magic/gf180mcuC.tech"
    NETGEN_SETUP="$PDK_ROOT/gf180mcuC/libs.tech/netgen/gf180mcuC_setup.tcl"
    # ORFS's gf180 LEF declares only VDD and VSS, while the PDK's cells carry
    # VNW and VPW well pins as well (write_cdl says so outright: "Terminal VNW
    # ... not found in LEF"). Built from ORFS's LEF the netlist fills those with
    # _unconnected_N, and the comparison then sees 332 schematic nets against
    # 120 extracted. Reading the PDK's own std-cell LEF restores the pins; the
    # ties themselves are stated below, because ORFS's PDN never routed wells
    # it did not know existed.
    _pdk_sc_lef="$PDK_ROOT/gf180mcuC/libs.ref/gf180mcu_fd_sc_mcu${TRACK_OPTION:-9t}${POWER_OPTION:-5v0}/lef/gf180mcu_fd_sc_mcu${TRACK_OPTION:-9t}${POWER_OPTION:-5v0}.lef"
    ;;
  *)
    echo "WARNING: Netgen LVS not supported for platform $PLATFORM" >&2
    echo "Supported platforms: sky130hd, sky130hs, gf180" >&2
    LVS_DIR="$PROJECT_DIR/lvs"
    mkdir -p "$LVS_DIR"
    echo '{"tool": "netgen", "status": "skipped", "reason": "Netgen LVS not supported for platform '"$PLATFORM"'"}' > "$LVS_DIR/netgen_lvs_result.json"
    echo "Netgen LVS skipped: no setup file for $PLATFORM"
    exit 0
    ;;
esac

if [[ ! -f "$MAGIC_TECH" ]]; then
  echo "ERROR: Magic tech file not found at $MAGIC_TECH" >&2
  exit 1
fi

if [[ ! -f "$NETGEN_SETUP" ]]; then
  echo "ERROR: Netgen setup file not found at $NETGEN_SETUP" >&2
  exit 1
fi

# Verify GDS exists from a prior ORFS run
RESULTS_DIR="$FLOW_DIR/results/$PLATFORM/$DESIGN_NAME/$FLOW_VARIANT"
if [[ ! -d "$RESULTS_DIR" ]]; then
  RESULTS_DIR="$FLOW_DIR/results/$PLATFORM/$DESIGN_NAME"
fi

GDS_FILE=$(find "$RESULTS_DIR" -name "6_final.gds" 2>/dev/null | head -1)
if [[ -z "$GDS_FILE" ]]; then
  echo "ERROR: No 6_final.gds found in $RESULTS_DIR" >&2
  echo "Run the ORFS backend first: run_orfs.sh <project-dir>" >&2
  exit 1
fi

# Strong provenance (RMD-P0-02): resolve the backend run this verdict belongs
# to with the SAME shared resolver every checker uses, refresh the project-side
# signoff record, and digest the exact GDS bytes Magic will extract. If the
# workspace GDS is stale relative to the picked run, the digest mismatch is
# visible downstream (the def-graph gate compares it against the published
# layout) instead of silently certifying foreign bytes.
# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/_backend_run.sh"
R2G_BACKEND_RUN="$(r2g_pick_backend_run "$PROJECT_DIR" || true)"
LVS_RUN_TAG=""
if [[ -n "$R2G_BACKEND_RUN" ]]; then
  LVS_RUN_TAG="$(basename "$R2G_BACKEND_RUN")"
  r2g_write_signoff_record "$PROJECT_DIR" "$R2G_BACKEND_RUN" "$PLATFORM" "$FLOW_VARIANT"
fi
LVS_GDS_SHA="$(sha256sum "$GDS_FILE" 2>/dev/null | cut -d' ' -f1 || true)"

# Find the Verilog netlist (gate-level from synthesis or ORFS)
VERILOG_NETLIST=""
# Try ORFS result first
for candidate in \
  "$RESULTS_DIR/6_final.v" \
  "$RESULTS_DIR/6_1_fill.v" \
  "$RESULTS_DIR/5_route.v" \
  "$FLOW_DIR/results/$PLATFORM/$DESIGN_NAME/base/6_final.v" \
  "$PROJECT_DIR/synth/synth_output.v"; do
  if [[ -f "$candidate" ]]; then
    VERILOG_NETLIST="$candidate"
    break
  fi
done

# This gate predates the powered-netlist step below, which is what the
# comparison actually uses. A harvested run can hold 6_final.def and no .v at
# all; the powered netlist is then rebuilt from LEF + DEF, so an absent Verilog
# is not a reason to stop here -- only an absent DEF is.
if [[ -z "$VERILOG_NETLIST" ]] \
   && { [[ "${R2G_LVS_DEF_NETLIST:-auto}" == "0" ]] \
        || [[ -z "$(find "$RESULTS_DIR" -name "6_final.def" 2>/dev/null | head -1)" ]]; }; then
  echo "ERROR: No Verilog netlist found for LVS comparison" >&2
  echo "Searched: $RESULTS_DIR/6_final.v, synth_output.v" >&2
  echo "       and no 6_final.def to rebuild a powered netlist from" >&2
  exit 1
fi

# Prefer a POWER-AWARE netlist for LVS. ORFS's 6_final.v is logical-only (no
# VPWR/VGND connections), which makes Netgen invent per-instance implicit power
# pins that never merge into the global supplies -> spurious net-count mismatch
# (729 layout vs 3219 netlist) even though devices match. The 6_final.odb carries
# the PDN/power connectivity, so regenerate a powered netlist via OpenROAD
# `write_verilog -include_pwr_gnd` and compare against that. Validated 2026-06-11
# (RV32I memory_controller: 729 vs 729 nets, "Circuits match uniquely"). See
# references/failure-patterns.md "sky130 LVS".
#
# The Liberty must be loaded first. Without it, openroad v2.0-17598's write_verilog
# corrupts the heap on some designs ("free(): unaligned chunk detected in tcache 2",
# then Signal 6/11), with or without -include_pwr_gnd; with it the same ODB writes
# cleanly (E9H iwls05_spi, 2026-09-23). ORFS itself writes 6_final.v with Liberty
# loaded, which is why the flow never hit this. Use the flow's own processed libs
# (objects/lib), else the platform's typical-corner library.
#
# NEVER fall back to the unpowered netlist: comparing against it manufactures an
# implicit-power mismatch (132 vs 626 nets, VPWR fanout 248 vs 1) that would be
# recorded as a DESIGN failure. No powered netlist = LVS not executed (status
# "error", reason powered_netlist_unavailable), never "mismatch".
LVS_DIR="$PROJECT_DIR/lvs"
mkdir -p "$LVS_DIR"
case "$PLATFORM" in
  sky130hd) SC_LIB_NAME="sky130_fd_sc_hd"; SC_LIB_TT="__tt_025C_1v80.lib"
            PWR_PIN="VPWR" ;;
  sky130hs) SC_LIB_NAME="sky130_fd_sc_hs"; SC_LIB_TT="__tt_025C_1v80.lib"
            PWR_PIN="VPWR" ;;
  gf180)
    # The cell library depends on the track and power options the flow was
    # configured with, and gf180's typical corner is 5 V at 25 C and gzipped --
    # hardcoding sky130's 1v80 name would silently find nothing and report
    # "no Liberty found" on a platform that has it.
    _trk="${TRACK_OPTION:-9t}"; _pwr="${POWER_OPTION:-5v0}"
    SC_LIB_NAME="gf180mcu_fd_sc_mcu${_trk}${_pwr}"
    SC_LIB_TT="__tt_025C_5v00.lib.gz"
    # gf180's cells name their rails VDD/VSS, not sky130's VPWR/VGND. The
    # post-write_verilog sanity check greps for this pin to prove the netlist
    # really came out power-aware; with sky130's name hardcoded it rejected a
    # perfectly good 46 KB netlist carrying 515 .VDD connections.
    PWR_PIN="VDD"
    ;;
esac
_powered_netlist_unavailable() {  # $1 = detail
  echo "ERROR: powered-netlist generation failed; not comparing against the unpowered $VERILOG_NETLIST" >&2
  echo "       (that yields a spurious implicit-power mismatch). LVS NOT EXECUTED: $1" >&2
  python3 - "$LVS_DIR/netgen_lvs_result.json" "$1" "$DESIGN_NAME" "$PLATFORM" \
    "${LVS_RUN_TAG:-}" "$GDS_FILE" "${LVS_GDS_SHA:-}" "$LVS_DIR/write_powered_verilog.log" <<'PYEOF'
import json, sys
out, detail, design, platform, run_tag, gds, sha, log = sys.argv[1:9]
json.dump({"tool": "netgen", "design": design, "platform": platform,
           "status": "error", "reason": "powered_netlist_unavailable",
           "detail": detail, "log_file": log, "run_tag": run_tag,
           "gds_path": gds, "gds_sha256": sha}, open(out, "w"), indent=2)
PYEOF
  # Keep the backend run's copy in step, so no stale verdict survives there.
  if [[ -n "${R2G_BACKEND_RUN:-}" && -d "$R2G_BACKEND_RUN" ]]; then
    mkdir -p "$R2G_BACKEND_RUN/lvs"
    cp "$LVS_DIR/netgen_lvs_result.json" "$R2G_BACKEND_RUN/lvs/" 2>/dev/null || true
  fi
  exit 1
}
ODB_FILE=$(find "$RESULTS_DIR" -name "6_final.odb" 2>/dev/null | head -1)
# A harvested corpus run may keep only 6_final.def -- keeping every stage costs
# ~1.1 GB per design. read_lef + read_def rebuilds a database with the same
# power connectivity (DEF's SPECIALNETS `( * VPWR )` wildcard expands to
# per-instance connections), so the ODB's absence is not the absence of power
# information that this script refuses to work around. R2G_LVS_DEF_NETLIST=0
# disables the fallback.
DEF_FILE=""
# gf180 must come from the DEF even when an ODB exists: ORFS built that ODB
# with its own LEF, which declares no VNW/VPW, so the well pins are absent from
# the database itself and no global connection can reach them. Rebuilding from
# LEF + DEF with the PDK's std-cell LEF is what puts them back. Every other
# platform keeps preferring the ODB -- two independent serializations are one
# degree better than deriving layout and netlist from the same DEF.
if [[ -n "${_pdk_sc_lef:-}" && -f "${_pdk_sc_lef:-}" && -n "$ODB_FILE" ]]; then
  echo "gf180: ignoring 6_final.odb (built without well pins); rebuilding from LEF + DEF"
  ODB_FILE=""
fi
if [[ -z "$ODB_FILE" && "${R2G_LVS_DEF_NETLIST:-auto}" != "0" ]]; then
  DEF_FILE=$(find "$RESULTS_DIR" -name "6_final.def" 2>/dev/null | head -1)
  [[ -n "$DEF_FILE" ]] && echo "No 6_final.odb; rebuilding the database from LEF + $DEF_FILE"
fi
[[ -n "$ODB_FILE" || -n "$DEF_FILE" ]] \
  || _powered_netlist_unavailable "no 6_final.odb and no 6_final.def in $RESULTS_DIR"
[[ -n "${OPENROAD_EXE:-}" ]] || _powered_netlist_unavailable "OPENROAD_EXE not resolved"
LVS_LIBS=()
for _lib_dir in "$FLOW_DIR/objects/$PLATFORM/$DESIGN_NAME/$FLOW_VARIANT/lib" \
                "${R2G_BACKEND_RUN:-/nonexistent}/objects/lib"; do
  for _lib in "$_lib_dir"/*.lib; do [[ -f "$_lib" ]] && LVS_LIBS+=("$_lib"); done
  (( ${#LVS_LIBS[@]} )) && break
done
if (( ${#LVS_LIBS[@]} == 0 )); then
  for _lib in "$FLOW_DIR/platforms/$PLATFORM/lib/${SC_LIB_NAME}${SC_LIB_TT:-__tt_025C_1v80.lib}"; do
    [[ -f "$_lib" ]] && LVS_LIBS+=("$_lib")
  done
fi
(( ${#LVS_LIBS[@]} )) || _powered_netlist_unavailable "no Liberty found for $PLATFORM (write_verilog needs it)"
POWERED_NETLIST="$LVS_DIR/powered.v"
rm -f "$POWERED_NETLIST"
# Liberty first either way: without it openroad's write_verilog corrupts the
# heap on some designs, with or without -include_pwr_gnd (2026-09-23).
{
  for _lib in "${LVS_LIBS[@]}"; do printf 'read_liberty "%s"\n' "$_lib"; done
  if [[ -n "$ODB_FILE" ]]; then
    printf 'read_db "%s"\n' "$ODB_FILE"
  else
    # tech LEF first, then the standard-cell and any macro LEFs, resolved from
    # the platform config the same way the decks are
    # run_lvs.sh defines PLATFORM_DIR; this script never did, so the lookup
    # below silently found nothing and the tcl came out with no read_lef at all.
    # plain variable, not `local`: this runs inside a { ... } group command,
    # where bash rejects `local` at runtime (and `bash -n` does not catch it).
    _pdir="${PLATFORM_DIR:-$FLOW_DIR/platforms/$PLATFORM}"
    # ORFS platform configs assign with `?=` as often as `=`
    # (export TECH_LEF ?= $(PLATFORM_DIR)/lef/...), so match either -- the
    # first version of this matched only `=` and produced a tcl with no
    # read_lef at all, which openroad answered with ORD-0005 "No technology
    # has been read."
    # gf180's paths carry more than $(PLATFORM_DIR): its TECH_LEF is
    # lef/gf180mcu_$(METAL_OPTION)_$(KVALUE)K_$(TRACK_OPTION)_tech.lef, so
    # expanding only PLATFORM_DIR left a filename that does not exist, the
    # read_lef was silently skipped, and the DEF then failed with ODB-0421
    # (units mismatch) plus "unknown site" -- both symptoms of a missing tech
    # LEF rather than of anything wrong with the DEF. Substitute each option
    # the platform config defines for itself.
    _r2g_lef() {
      local _raw
      _raw=$(grep -E "^[[:space:]]*(override[[:space:]]+)?export[[:space:]]+$1[[:space:]]*[?:]?=" \
        "$_pdir/config.mk" 2>/dev/null | head -1 | sed 's/^[^=]*=[[:space:]]*//')
      [[ -n "$_raw" ]] || return 0
      local _v _val
      for _v in METAL_OPTION KVALUE TRACK_OPTION POWER_OPTION CORNER PLATFORM; do
        _val="${!_v:-}"
        if [[ -z "$_val" ]]; then
          _val=$(grep -E "^[[:space:]]*export[[:space:]]+$_v[[:space:]]*[?:]?=" \
            "$_pdir/config.mk" 2>/dev/null | head -1 | sed 's/^[^=]*=[[:space:]]*//' | tr -d ' ')
        fi
        [[ -n "$_val" ]] && _raw="${_raw//\$($_v)/$_val}"
      done
      printf '%s\n' "${_raw//\$(PLATFORM_DIR)/$_pdir}"
    }
    # The tech LEF always comes from the platform; only the std-cell LEF is
    # substituted for gf180, and the substitution is decided per source rather
    # than by comparing expanded strings (both expand through $(PLATFORM_DIR),
    # and an earlier version matched the tech LEF too, leaving openroad with no
    # technology at all).
    _sc_src="$(_r2g_lef SC_LEF)"
    if [[ -n "${_pdk_sc_lef:-}" && -f "${_pdk_sc_lef:-}" ]]; then
      _sc_src="$_pdk_sc_lef"
    fi
    for _lef in $(_r2g_lef TECH_LEF) "$_sc_src" $(_r2g_lef ADDITIONAL_LEFS); do
      [[ -n "$_lef" && -f "$_lef" ]] && printf 'read_lef "%s"\n' "$_lef"
    done
    printf 'read_def "%s"\n' "$DEF_FILE"
  fi
  if [[ -n "${_pdk_sc_lef:-}" && -f "${_pdk_sc_lef:-}" ]]; then
    # In gf180's digital cells the n-well goes to power and the p-well to
    # ground. That is the tie the silicon has; ORFS's PDN simply never emitted
    # it because its LEF declared no well pins. Verified on corescore_emitter_uart:
    # `_unconnected_108 _unconnected_109` becomes `VDD VSS`.
    printf 'add_global_connection -net VDD -pin_pattern {^VNW$} -power\n'
    printf 'add_global_connection -net VSS -pin_pattern {^VPW$} -ground\n'
    printf 'global_connect\n'
  fi
  printf 'write_verilog -include_pwr_gnd "%s"\n' "$POWERED_NETLIST"
  printf 'exit\n'
} > "$LVS_DIR/write_powered_verilog.tcl"
# Bounded (2026-07-04 M3: a large ODB hangs write_verilog indefinitely, and
# inside an `if` set -e never fires). r2g_bounded_run (RMD2-P0-01) also reaps
# any session survivor before returning.
_PWR_RC=0
r2g_bounded_run "${R2G_POWERED_NETLIST_TIMEOUT:-900}" 30 "$LVS_DIR/write_powered_verilog.log" \
  "$OPENROAD_EXE" -no_init -exit "$LVS_DIR/write_powered_verilog.tcl" || _PWR_RC=$?
if [[ $_PWR_RC -ne 0 || ! -s "$POWERED_NETLIST" ]] \
   || ! grep -q "${PWR_PIN:-VPWR}" "$POWERED_NETLIST" \
   || grep -qE '^Signal [0-9]+ received|^(free|malloc|realloc)\(\): |corrupted (size|double-linked)|double free or corruption' \
        "$LVS_DIR/write_powered_verilog.log"; then
  _powered_netlist_unavailable "openroad write_verilog exit=$_PWR_RC (see $LVS_DIR/write_powered_verilog.log)"
fi
echo "Using power-aware netlist from ${ODB_FILE:+ODB}${ODB_FILE:-LEF+DEF}: $POWERED_NETLIST"
VERILOG_NETLIST="$POWERED_NETLIST"

echo "Running Netgen LVS for design: $DESIGN_NAME"
echo "Platform: $PLATFORM"
echo "GDS: $GDS_FILE"
echo "Netlist: $VERILOG_NETLIST"
echo "Tech: $MAGIC_TECH"
echo "Netgen setup: $NETGEN_SETUP"

LVS_DIR="$PROJECT_DIR/lvs"
mkdir -p "$LVS_DIR"

# Resolve the standard-cell SPICE library for the schematic side of LVS.
# Without this, Netgen reads 6_final.v with the std cells as hollow black boxes
# ("Circuit sky130_fd_sc_hd__<cell> contains no devices") and nets explode, giving a
# spurious mismatch even when device counts match. See references/failure-patterns.md
# "sky130 LVS" (2026-06-11). Production fix: load the cell library into the schematic
# circuit so both sides expand to transistors.
case "$PLATFORM" in
  gf180) _pdk_dir="gf180mcuC" ;;
  *)     _pdk_dir="sky130A" ;;
esac
SC_SPICE="$PDK_ROOT/$_pdk_dir/libs.ref/$SC_LIB_NAME/spice/$SC_LIB_NAME.spice"
if [[ ! -f "$SC_SPICE" ]]; then
  echo "WARNING: std-cell SPICE not found at $SC_SPICE — schematic cells will be hollow" >&2
  SC_SPICE=""
fi

# Step 1: Extract SPICE netlist from GDS using Magic (hierarchical — no flatten, so
# each std cell stays a subckt that matches the cell-library definition on the
# schematic side). Run Magic inside a scratch dir so its per-cell *.ext files land
# there instead of polluting the caller's CWD (repo root) — ~50 stray files/design.
EXTRACTED_SPICE="$LVS_DIR/extracted.spice"
EXTRACT_TCL="$LVS_DIR/run_magic_extract.tcl"
EXTRACT_LOG="$LVS_DIR/magic_extract.log"
EXT_SCRATCH="$LVS_DIR/magic_ext"
rm -rf "$EXT_SCRATCH"; mkdir -p "$EXT_SCRATCH"

# Connectivity-only extraction. LVS compares topology (devices + nets), never
# parasitics, so capacitance/coupling/resistance extraction is pure waste here --
# and internodal *coupling* capacitance is O(n^2) over nearby geometry, which on a
# routing-dense top cell (e.g. apb_spi_master / sha1_core: ~75k via+cell instances)
# makes `extract all` run 8+ min and hang past NETGEN_TIMEOUT, getting SIGTERM'd
# ("Created database crash recovery file") -> no SPICE -> a bogus lvs_none. Turning
# the parasitic passes off yields the IDENTICAL LVS netlist in ~40-90s (validated
# 2026-06-13: apb_spi_master 8min-hang -> 87s complete, "Circuits match uniquely").
# Option names are exact: capacitance/coupling/resistance/adjust/length ("adjustment"
# is a syntax error). `extract all` extracts all cells using these do/no settings.
# See references/failure-patterns.md "sky130 Netgen LVS Magic top-cell extraction hang".
# gf180's cells carry their well names as GDS text -- VNW on 21/10 and VPW on
# 204/10 -- but ORFS's per-cell GDS omits both layers, so Magic has no name for
# the well nodes and emits SUB and w_<n>#. Netgen then "alters pin lists to
# match" and, in doing so, stops comparing those pins' connections: a negative
# control that tied VNW to VSS (an n-well held at ground) still read "Circuits
# match uniquely". Reading the PDK's own library GDS first defines every cell
# WITH its well labels; `gds noduplicates true` then keeps the design GDS from
# redefining them, so only the top level comes from it. sky130 needs none of
# this -- its cells expose the wells as ordinary VPB/VNB pins.
_PDK_CELL_GDS=""
if [[ "$PLATFORM" == "gf180" ]]; then
  _PDK_CELL_GDS="$PDK_ROOT/gf180mcuC/libs.ref/gf180mcu_fd_sc_mcu${TRACK_OPTION:-9t}${POWER_OPTION:-5v0}/gds/gf180mcu_fd_sc_mcu${TRACK_OPTION:-9t}${POWER_OPTION:-5v0}.gds"
  [[ -f "$_PDK_CELL_GDS" ]] || _PDK_CELL_GDS=""
fi
cat > "$EXTRACT_TCL" << MAGIC_EOF
${_PDK_CELL_GDS:+gds read "$_PDK_CELL_GDS"}
${_PDK_CELL_GDS:+gds noduplicates true}
gds read "$GDS_FILE"
load "$DESIGN_NAME"
select top cell
extract no capacitance
extract no coupling
extract no resistance
extract no adjust
extract no length
extract all
ext2spice lvs
ext2spice -o "$EXTRACTED_SPICE"
quit -noprompt
MAGIC_EOF

NETGEN_TIMEOUT="${NETGEN_TIMEOUT:-3600}"
echo "Timeout: ${NETGEN_TIMEOUT}s per step"
echo "Step 1: Extracting SPICE netlist from GDS with Magic..."
# RMD2-P0-01 (2026-07-24): the old `( cd … && setsid timeout … magic ) | tee`
# had BOTH known liveness defects — `setsid` made timeout a group leader and
# silently disabled its tree-kill (#40), and `tee` held the output pipe open so
# a TERM-ignoring descendant could hang this script forever. r2g_bounded_run
# runs Magic in its own session, logs directly to EXTRACT_LOG, TERM→grace→KILLs
# the whole group on expiry, and reaps any session survivor before returning.
# The cd into the scratch dir happens inside the session (bash -c + exec keeps
# Magic as the session leader) so per-cell *.ext files still land there.
# set +e around the call (2026-07-04 audit M2): under `set -euo pipefail` a
# Magic TIMEOUT aborted the script AT THIS LINE, skipping the intended
# status:error JSON below, so the timeout reason was lost.
MAGIC_STATUS=0
set +e
r2g_bounded_run "$NETGEN_TIMEOUT" "${NETGEN_KILL_GRACE:-30}" "$EXTRACT_LOG" \
  bash -c 'cd "$1" && exec "$2" -dnull -noconsole -T "$3" "$4"' _ \
  "$EXT_SCRATCH" "$MAGIC_EXE" "$MAGIC_TECH" "$EXTRACT_TCL"
MAGIC_STATUS=$?
set -e
tail -n 25 "$EXTRACT_LOG" 2>/dev/null || true
if [[ $MAGIC_STATUS -eq 124 || $MAGIC_STATUS -eq 137 ]]; then
  echo "ERROR: Magic SPICE extraction timed out after ${NETGEN_TIMEOUT}s (exit $MAGIC_STATUS)" >&2
  echo '{"tool": "netgen", "status": "error", "reason": "Magic SPICE extraction timeout"}' > "$LVS_DIR/netgen_lvs_result.json"
  exit 1
fi

if [[ ! -f "$EXTRACTED_SPICE" ]]; then
  echo "ERROR: Magic SPICE extraction failed — $EXTRACTED_SPICE not created" >&2
  echo '{"tool": "netgen", "status": "error", "reason": "Magic SPICE extraction failed"}' > "$LVS_DIR/netgen_lvs_result.json"
  exit 1
fi
echo "Extracted: $EXTRACTED_SPICE ($(wc -l < "$EXTRACTED_SPICE") lines)"

# Guard (failure-patterns.md #33): a PORTLESS top-level subckt means the GDS lost
# its DEF-derived geometry (pin labels attached to nothing) — comparing it is
# meaningless and Netgen would report a plausible-looking "top pin mismatch" on a
# perfectly good layout, teaching the loop a lie. Root cause seen 2026-07-09:
# ORFS sky130hs.lyt shipped legacy lefdef reader options, so def2stream silently
# dropped every wire/via/pin rect (remedy: tools/patch_sky130hs_lyt.py, then
# re-run the ORFS merge). Classify as an infra ERROR, never a mismatch.
_TOP_PORTS=$(bash "$(dirname "${BASH_SOURCE[0]}")/_spice_top_ports.sh" \
  "$EXTRACTED_SPICE" "$DESIGN_NAME")
if [[ "${_TOP_PORTS:-0}" -eq 0 ]]; then
  echo "ERROR: extracted top-level subckt '$DESIGN_NAME' has ZERO ports — the GDS" >&2
  echo "  lost its DEF geometry (labels attach to nothing). NOT a design mismatch." >&2
  echo "  Remedy: python3 tools/patch_sky130hs_lyt.py --check (failure-patterns #33)," >&2
  echo "  then re-run the ORFS merge (6_1_merged.gds) and re-run LVS." >&2
  echo '{"tool": "netgen", "status": "error", "reason": "portless top-level extraction — GDS lost DEF geometry (failure-patterns #33)"}' > "$LVS_DIR/netgen_lvs_result.json"
  exit 1
fi
echo "Top-level ports extracted: $_TOP_PORTS"

# Normalize antenna-diode primitives in the extracted netlist (X subcircuit
# instance -> D device, perim= -> pj=) so the diode class matches the PDK cell
# library instead of flattening sky130_fd_sc_hd__diode_2 and failing top-level
# pin matching. See normalize_diode_spice.py and references/failure-patterns.md
# "sky130 LVS" cause 5 (fixed 2026-06-11).
python3 "$(dirname "${BASH_SOURCE[0]}")/normalize_diode_spice.py" "$EXTRACTED_SPICE"

# Restore top-level ports ext2spice dropped to an internal alias (Magic picks a
# non-port canonical name when an anonymous route fragment precedes the port
# label in the merge order) — otherwise Netgen reports a FALSE design
# `top_pin_mismatch` on a healthy layout. Ground truth is the .ext `port`
# declarations; a merge class holding 2+ ports (a genuine short) is never
# restored. Fail-open helper: on any problem the honest mismatch remains.
# See references/failure-patterns.md #58 "Magic ext2spice port-name loss".
python3 "$(dirname "${BASH_SOURCE[0]}")/restore_ext_ports.py" \
  "$EXTRACTED_SPICE" "$DESIGN_NAME" "$EXT_SCRATCH/$DESIGN_NAME.ext" || true

# Step 2: Run Netgen LVS comparison
NETGEN_LOG="$LVS_DIR/netgen_lvs.log"
NETGEN_REPORT="$LVS_DIR/netgen_lvs.rpt"

echo "Step 2: Running Netgen LVS comparison..."
# Drive Netgen from a TCL script (not -batch lvs) so we can load the std-cell SPICE
# library into the *schematic* circuit (circuit2 = the Verilog netlist). This is the
# OpenLane-style sky130 LVS pattern: readnet the cell library into circuit2 so its
# black-box cells expand to transistors, matching the layout-extracted circuit1.
NETGEN_TCL="$LVS_DIR/run_netgen_lvs.tcl"
if [[ -n "$SC_SPICE" ]]; then
  # Load the std-cell SPICE library FIRST so circuit2 already holds the transistor-level
  # cell definitions; then read the Verilog netlist INTO THE SAME circuit handle so its
  # cell instances bind to those definitions. (Reading the Verilog first makes netgen
  # create empty placeholder cells that shadow a later library read — the cause of the
  # "Circuit sky130_fd_sc_hd__<cell> contains no devices" mismatch.)
  cat > "$NETGEN_TCL" << NETGEN_EOF
set circuit1 [readnet spice "$EXTRACTED_SPICE"]
set circuit2 [readnet spice "$SC_SPICE"]
readnet verilog "$VERILOG_NETLIST" \$circuit2
lvs "\$circuit1 $DESIGN_NAME" "\$circuit2 $DESIGN_NAME" "$NETGEN_SETUP" "$NETGEN_REPORT"
NETGEN_EOF
else
  cat > "$NETGEN_TCL" << NETGEN_EOF
set circuit1 [readnet spice "$EXTRACTED_SPICE"]
set circuit2 [readnet verilog "$VERILOG_NETLIST"]
lvs "\$circuit1 $DESIGN_NAME" "\$circuit2 $DESIGN_NAME" "$NETGEN_SETUP" "$NETGEN_REPORT"
NETGEN_EOF
fi

LVS_STATUS=0
set +e
# MAGIC_EXT_USE_GDS=1 tells the PDK's sky130A_setup.tcl that circuit1 came from a
# GDS extraction, activating its `ignore class` rules for layout-only cells
# (tapvpwrvgnd, fakediode) so they are ignored instead of flattened into the top.
# Bounded session supervisor (RMD2-P0-01) — the old `timeout … | tee` let a
# TERM-ignoring netgen descendant hold the pipe open past the timeout.
r2g_bounded_run "$NETGEN_TIMEOUT" "${NETGEN_KILL_GRACE:-60}" "$NETGEN_LOG" \
  env MAGIC_EXT_USE_GDS=1 "$NETGEN_CMD" -batch source "$NETGEN_TCL"
LVS_STATUS=$?
set -e
tail -n 25 "$NETGEN_LOG" 2>/dev/null || true

# Parse results
LVS_RESULT="unknown"
MATCH_STATUS="unknown"
if [[ -f "$NETGEN_LOG" ]]; then
  if grep -qi "Circuits match uniquely\|Result: PASS\|netlists match" "$NETGEN_LOG" 2>/dev/null; then
    LVS_RESULT="clean"
    MATCH_STATUS="match"
  elif grep -qi "mismatch\|NOT match\|Result: FAIL\|netlists do not match" "$NETGEN_LOG" 2>/dev/null; then
    LVS_RESULT="mismatch"
    MATCH_STATUS="mismatch"
  fi
fi

# A "match" that Netgen reached by flattening a subcell it could not pair is
# not a match. It prints the fact and then matches anyway:
#
#   Flattening unmatched subcell gf180mcu_..._inv_4 in circuit <top> (1)(1 instance)
#
# Found by a negative control: swapping ONE instance from inv_2 to inv_4 leaves
# the two sides genuinely disagreeing (powered.v names inv_4, the extraction
# names inv_2), but the layout has no inv_4 subcircuit, so Netgen flattens the
# orphan and the remaining topology agrees -- inv_2 and inv_4 differ only in
# width, and width is compared only between devices that paired. The verdict
# came back "Circuits match uniquely".
#
# Zero false positives on the evidence available: 5 clean gf180 designs (93 to
# 3,025 cells), the negative control that WAS caught, and a clean sky130hd run
# all report 0 such lines; only the missed control reports 1.
_LVS_FLATTENED=0
for _f in "$NETGEN_LOG" "$NETGEN_REPORT"; do
  [[ -f "$_f" ]] || continue
  _n=$(grep -c "Flattening unmatched subcell" "$_f" 2>/dev/null || echo 0)
  (( _n > _LVS_FLATTENED )) && _LVS_FLATTENED=$_n
done
if [[ "$MATCH_STATUS" == "match" && "$_LVS_FLATTENED" -gt 0 ]]; then
  echo "WARNING: Netgen matched only after flattening $_LVS_FLATTENED unmatched subcell(s);" >&2
  echo "         a cell present on one side and absent on the other is a real difference." >&2
  grep -h "Flattening unmatched subcell" "$NETGEN_LOG" "$NETGEN_REPORT" 2>/dev/null | sed 's/^/         /' >&2
  LVS_RESULT="mismatch"
  MATCH_STATUS="flattened_unmatched_subcell"
fi

# Also check the report file
if [[ -f "$NETGEN_REPORT" ]] && [[ "$MATCH_STATUS" == "unknown" ]]; then
  if grep -qi "Circuits match\|PASS" "$NETGEN_REPORT" 2>/dev/null; then
    LVS_RESULT="clean"
    MATCH_STATUS="match"
  elif grep -qi "mismatch\|FAIL" "$NETGEN_REPORT" 2>/dev/null; then
    LVS_RESULT="mismatch"
    MATCH_STATUS="mismatch"
  fi
fi

# Classify the mismatch so the knowledge store's symptom index can key repair
# experience on it (ingest_run.py reads "mismatch_class" from reports/lvs.json).
# Classes: top_pin_mismatch — devices/nets match but top-level pin lists don't
# (LVS-setup/representation residual: antenna-diode flattening or port-to-port
# feedthrough aliasing; see failure-patterns.md "sky130 LVS" cause 5);
# netgen_topology — real device/net count differences; generic — anything else.
MISMATCH_CLASS=""
if [[ "$LVS_RESULT" == "mismatch" && -f "$NETGEN_REPORT" ]]; then
  if grep -qi 'Top level cell failed pin matching' "$NETGEN_REPORT"; then
    MISMATCH_CLASS="top_pin_mismatch"
    # Sharpen the opaque pin-mismatch when the DEF PROVES a real pin-vs-PDN
    # short (failure-patterns.md #58, ROM_16: met3 IO pins placed on met3 VSS
    # straps — geometry-only DRC decks cannot see different-net overlaps, so
    # this arrives as an LVS pin mismatch). A distinct, geometrically-proven
    # class gives the learner a precise symptom key instead of a grab-bag.
    _DEF_FOR_SHORT="${GDS_FILE%.gds}.def"
    if [[ -f "$_DEF_FOR_SHORT" ]]; then
      _short_rc=0
      python3 "$(dirname "${BASH_SOURCE[0]}")/../extract/check_pin_pdn_overlap.py" \
        "$_DEF_FOR_SHORT" --json > "$LVS_DIR/pin_pdn_shorts.json" 2>/dev/null || _short_rc=$?
      if [[ "$_short_rc" -eq 4 ]]; then
        MISMATCH_CLASS="pin_pdn_short"
        echo "LVS mismatch geometrically attributed: IO pin(s) overlap PDN stripes" \
             "(see $LVS_DIR/pin_pdn_shorts.json; failure-patterns #58)"
      else
        rm -f "$LVS_DIR/pin_pdn_shorts.json" 2>/dev/null || true
      fi
    fi
  elif grep -qiE 'Number of devices:.*\*\*MISMATCH\*\*|do not match' "$NETGEN_REPORT"; then
    MISMATCH_CLASS="netgen_topology"
  else
    MISMATCH_CLASS="generic"
  fi
fi

# Write JSON result
cat > "$LVS_DIR/netgen_lvs_result.json" << JSON_EOF
{
  "tool": "netgen",
  "design": "$DESIGN_NAME",
  "platform": "$PLATFORM",
  "status": "$LVS_RESULT",
  "match": "$MATCH_STATUS",
  "mismatch_class": "$MISMATCH_CLASS",
  "extracted_spice": "$EXTRACTED_SPICE",
  "reference_netlist": "$VERILOG_NETLIST",
  "report_file": "$NETGEN_REPORT",
  "log_file": "$NETGEN_LOG",
  "run_tag": "${LVS_RUN_TAG:-}",
  "gds_path": "$GDS_FILE",
  "gds_sha256": "${LVS_GDS_SHA:-}"
}
JSON_EOF

# Clean up Magic temp files
rm -f "$LVS_DIR"/*.ext 2>/dev/null || true

# Copy to the SELECTED backend run (RMD-P0-02: the resolver's pick, never
# `ls | tail -1` — a newer empty RUN dir must not adopt this verdict).
TARGET_RUN="${R2G_BACKEND_RUN:-}"
if [[ -z "$TARGET_RUN" || ! -d "$TARGET_RUN" ]]; then
  TARGET_RUN=$(ls -d "$PROJECT_DIR/backend"/RUN_* 2>/dev/null | sort | tail -1 || true)
fi
if [[ -n "$TARGET_RUN" && -d "$TARGET_RUN" ]]; then
  mkdir -p "$TARGET_RUN/lvs"
  cp "$LVS_DIR"/netgen_lvs* "$TARGET_RUN/lvs/" 2>/dev/null || true
fi

echo ""
if [[ "$LVS_RESULT" == "clean" ]]; then
  echo "Netgen LVS CLEAN — circuits match"
elif [[ "$LVS_RESULT" == "mismatch" ]]; then
  echo "Netgen LVS FAILED — netlist mismatch detected"
  echo "Review $NETGEN_REPORT for details"
else
  echo "Netgen LVS completed — check $NETGEN_LOG for results"
fi
echo "Results: $LVS_DIR"
exit $LVS_STATUS
