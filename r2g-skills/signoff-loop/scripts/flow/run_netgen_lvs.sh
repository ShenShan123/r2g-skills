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

DESIGN_NAME=$(grep -E '^[[:space:]]*export[[:space:]]+DESIGN_NAME[[:space:]]*=' "$CONFIG_MK" | head -1 | sed 's/.*=\s*//' | tr -d ' ')
DESIGN_NICKNAME=$(grep -E '^[[:space:]]*export[[:space:]]+DESIGN_NICKNAME[[:space:]]*=' "$CONFIG_MK" | head -1 | sed 's/.*=\s*//' | tr -d ' ' || true)
DESIGN_NICKNAME="${DESIGN_NICKNAME:-$DESIGN_NAME}"

# Map platform to PDK files
MAGIC_TECH=""
NETGEN_SETUP=""
case "$PLATFORM" in
  sky130hd|sky130hs)
    MAGIC_TECH="$PDK_ROOT/sky130A/libs.tech/magic/sky130A.tech"
    NETGEN_SETUP="$PDK_ROOT/sky130A/libs.tech/netgen/sky130A_setup.tcl"
    ;;
  *)
    echo "WARNING: Netgen LVS not supported for platform $PLATFORM" >&2
    echo "Supported platforms: sky130hd, sky130hs" >&2
    LVS_DIR="$PROJECT_DIR/lvs"
    mkdir -p "$LVS_DIR"
    echo '{"tool": "netgen", "status": "skipped", "reason": "Netgen LVS not supported for platform '"$PLATFORM"'"}' > "$LVS_DIR/netgen_lvs_result.json"
    echo "Netgen LVS skipped: no setup file for $PLATFORM"
    exit 0
    ;;
esac
NETGEN_EFFECTIVE_SETUP="$NETGEN_SETUP"
NETGEN_COMPAT_SETUP=""

if [[ ! -f "$MAGIC_TECH" ]]; then
  echo "ERROR: Magic tech file not found at $MAGIC_TECH" >&2
  exit 1
fi

if [[ ! -f "$NETGEN_SETUP" ]]; then
  echo "ERROR: Netgen setup file not found at $NETGEN_SETUP" >&2
  exit 1
fi

# The open_pdks tech file may declare the oldest Magic revision whose extraction
# semantics it supports.  Treat that as an executable compatibility contract,
# not an informational warning: an older binary has produced crashes and
# unusable LVS evidence on this host.
MAGIC_REQUIRED="$(sed -nE 's/.*requires[[:space:]]+magic-([0-9.]+).*/\1/p' "$MAGIC_TECH" | head -1)"
# Magic's standalone ``-dnull`` build intentionally has no command-line
# version switch.  A direct bundle may therefore provide the audited version
# as R2G_MAGIC_VERSION; only probe ``--version`` when no explicit pin exists.
MAGIC_VERSION="${R2G_MAGIC_VERSION:-}"
if [[ -z "$MAGIC_VERSION" && ( -n "$MAGIC_REQUIRED" || "${R2G_STRICT_SIGNOFF:-0}" == "1" ) ]]; then
  MAGIC_VERSION="$(timeout 10 "$MAGIC_EXE" --version 2>/dev/null | head -1 | tr -d '[:space:]' || true)"
fi
if [[ -n "$MAGIC_REQUIRED" ]]; then
  _magic_first="$(printf '%s\n%s\n' "$MAGIC_REQUIRED" "$MAGIC_VERSION" | sort -V | head -1)"
  if [[ "$_magic_first" != "$MAGIC_REQUIRED" ]]; then
    echo "ERROR: $MAGIC_TECH requires Magic >= $MAGIC_REQUIRED; found $MAGIC_VERSION at $MAGIC_EXE" >&2
    exit 1
  fi
fi
NETGEN_VERSION="${R2G_NETGEN_VERSION:-}"
if [[ "${R2G_STRICT_SIGNOFF:-0}" == "1" ]]; then
  NETGEN_VERSION="$(timeout 10 "$NETGEN_CMD" -batch 2>/dev/null | head -1 | tr -d '\r' || true)"
  NETGEN_REVISION="$(printf '%s' "$NETGEN_VERSION" | sed -nE 's/.*Netgen[[:space:]]+1\.5\.([0-9]+).*/\1/p')"
  if [[ -z "$NETGEN_REVISION" || "$NETGEN_REVISION" -lt 312 ]]; then
    echo "ERROR: strict sky130 LVS requires Netgen >= 1.5.312; found '${NETGEN_VERSION:-unknown}'" >&2
    echo "  1.5.312 fixes loss of definitions when a library and Verilog are read into one circuit." >&2
    exit 1
  fi
fi

# Verify GDS from the exact preserved backend run.
# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/_restage_for_signoff.sh"
RESULTS_DIR="$ORFS_RESULTS_DIR"

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
  "$FLOW_DIR/results/$PLATFORM/$DESIGN_NICKNAME/base/6_final.v" \
  "$PROJECT_DIR/synth/synth_output.v"; do
  if [[ -f "$candidate" ]]; then
    VERILOG_NETLIST="$candidate"
    break
  fi
done

if [[ -z "$VERILOG_NETLIST" ]]; then
  echo "ERROR: No Verilog netlist found for LVS comparison" >&2
  echo "Searched: $RESULTS_DIR/6_final.v, synth_output.v" >&2
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
LVS_DIR="$PROJECT_DIR/lvs"
mkdir -p "$LVS_DIR"
# Derived schematic normalization receipts must never be reused across LVS runs.
rm -f "$LVS_DIR/powered_hierarchical.v" "$LVS_DIR/power_connectivity.json"
ODB_FILE=$(find "$RESULTS_DIR" -name "6_final.odb" 2>/dev/null | head -1)
if [[ -n "$ODB_FILE" && -n "${OPENROAD_EXE:-}" ]]; then
  POWERED_NETLIST="$LVS_DIR/powered.v"
  cat > "$LVS_DIR/write_powered_verilog.tcl" << ORTCL
read_db "$ODB_FILE"
write_verilog -include_pwr_gnd "$POWERED_NETLIST"
exit
ORTCL
  # Bounded (2026-07-04 M3: a large ODB hangs write_verilog indefinitely, and
  # inside an `if` set -e never fires). r2g_bounded_run (RMD2-P0-01) also reaps
  # any session survivor before returning.
  # A healthy write_verilog finishes in seconds.  Some OpenROAD builds abort
  # and leave a pipe/session survivor; waiting the historical fixed 900s before
  # the deterministic SPICE-signature fallback made a recoverable crash dominate
  # strict-signoff runtime.  Keep this probe independently bounded.
  POWERED_VERILOG_TIMEOUT="${POWERED_VERILOG_TIMEOUT:-120}"
  if r2g_bounded_run "$POWERED_VERILOG_TIMEOUT" 10 "$LVS_DIR/write_powered_verilog.log" \
       "$OPENROAD_EXE" -no_init -exit "$LVS_DIR/write_powered_verilog.tcl" \
     && [[ -s "$POWERED_NETLIST" ]] && grep -q 'VPWR' "$POWERED_NETLIST"; then
    echo "Using power-aware netlist from ODB: $POWERED_NETLIST"
    VERILOG_NETLIST="$POWERED_NETLIST"
  else
    echo "WARNING: powered-netlist generation failed; falling back to $VERILOG_NETLIST (LVS may show implicit-power-pin mismatch)" >&2
  fi
fi

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
  sky130hd) SC_LIB_NAME="sky130_fd_sc_hd" ;;
  sky130hs) SC_LIB_NAME="sky130_fd_sc_hs" ;;
esac
SC_SPICE="$PDK_ROOT/sky130A/libs.ref/$SC_LIB_NAME/spice/$SC_LIB_NAME.spice"
if [[ ! -f "$SC_SPICE" ]]; then
  _R2G_SC_SPICE="$HOME/.local/share/r2g-pdk/$SC_LIB_NAME/$SC_LIB_NAME.spice"
  if [[ -f "$_R2G_SC_SPICE" ]]; then
    SC_SPICE="$_R2G_SC_SPICE"
    echo "Using user-local SkyWater transistor SPICE: $SC_SPICE"
  else
  # Some ORFS installations deliberately ship the production CDL beside the
  # platform instead of duplicating the very large transistor library under
  # open_pdks.  CDL is valid SPICE input for Netgen and is the exact library
  # used by the selected ORFS platform.
  _ORFS_SC_CDL="$FLOW_DIR/platforms/$PLATFORM/cdl/$PLATFORM.cdl"
  if [[ -f "$_ORFS_SC_CDL" ]]; then
    SC_SPICE="$_ORFS_SC_CDL"
    echo "Using ORFS platform standard-cell CDL: $SC_SPICE"
  else
    echo "WARNING: std-cell SPICE/CDL not found — schematic cells will be hollow" >&2
    SC_SPICE=""
  fi
  fi
fi
SC_SPICE_SOURCE="$SC_SPICE"

# Some OpenROAD builds abort in write_verilog on otherwise valid ODBs.  Fall
# back to the logical final netlist plus the exact power-pin signature of the
# transistor SPICE library. Named functional pins are preserved; only
# library-declared supply pins and their explicit hierarchical propagation are
# added to the derived schematic.
if [[ -n "$SC_SPICE_SOURCE" ]] && ! grep -q '\.VPWR[[:space:]]*(' "$VERILOG_NETLIST"; then
  POWERED_FALLBACK="$LVS_DIR/powered_from_spice.v"
  if python3 "$(dirname "${BASH_SOURCE[0]}")/power_verilog_from_spice.py" \
       "$VERILOG_NETLIST" "$SC_SPICE_SOURCE" "$POWERED_FALLBACK" \
       --receipt "$LVS_DIR/powered_from_spice.json"; then
    echo "Using SPICE-signature powered netlist: $POWERED_FALLBACK"
    VERILOG_NETLIST="$POWERED_FALLBACK"
  elif [[ "${R2G_STRICT_SIGNOFF:-0}" == "1" ]]; then
    echo "ERROR: unable to construct an explicit powered schematic for strict LVS" >&2
    exit 1
  else
    echo "WARNING: powered schematic fallback failed; implicit supply nets may mismatch" >&2
  fi
fi

# ORFS can retain non-flattened RTL-generated child modules.  Explicit power
# pins inside those children otherwise become private implicit nets, producing
# a real Netgen topology mismatch (for riscv32i: 6128 vs 6132 nets).  Propagate
# VDD/VSS through powered child-module ports in a derived schematic only; the
# source Verilog, ODB, and layout are untouched.  The receipt binds this
# transformation to the exact input bytes.
POWER_HIER_NETLIST="$LVS_DIR/powered_hierarchical.v"
POWER_CONNECTIVITY_RECEIPT="$LVS_DIR/power_connectivity.json"
if python3 "$(dirname "${BASH_SOURCE[0]}")/normalize_power_connectivity.py" \
     "$VERILOG_NETLIST" "$DESIGN_NAME" "$POWER_HIER_NETLIST" \
     --receipt "$POWER_CONNECTIVITY_RECEIPT"; then
  VERILOG_NETLIST="$POWER_HIER_NETLIST"
elif [[ "${R2G_STRICT_SIGNOFF:-0}" == "1" ]]; then
  echo "ERROR: unable to close derived schematic over hierarchical power nets" >&2
  exit 1
else
  echo "WARNING: hierarchical power normalization failed; retaining $VERILOG_NETLIST" >&2
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
# A failed extractor must never be graded using a prior successful netlist.
rm -f "$EXTRACTED_SPICE" "$LVS_DIR/netgen_lvs.log" \
  "$LVS_DIR/netgen_lvs.rpt" "$LVS_DIR/netgen_lvs_result.json" \
  "$LVS_DIR/extracted.raw.spice" "$LVS_DIR/layout_normalization.json" \
  "$LVS_DIR/library_normalization.json" "$LVS_DIR/standard_cells.normalized.spice"

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
cat > "$EXTRACT_TCL" << MAGIC_EOF
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

if [[ $MAGIC_STATUS -ne 0 ]]; then
  echo "ERROR: Magic SPICE extraction exited $MAGIC_STATUS" >&2
  echo '{"tool": "netgen", "status": "error", "reason": "Magic SPICE extraction nonzero"}' > "$LVS_DIR/netgen_lvs_result.json"
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
RAW_EXTRACTED_SPICE="$LVS_DIR/extracted.raw.spice"
cp "$EXTRACTED_SPICE" "$RAW_EXTRACTED_SPICE"

# Normalize antenna-diode primitives in the extracted netlist (X subcircuit
# instance -> D device, perim= -> pj=) so the diode class matches the PDK cell
# library instead of flattening sky130_fd_sc_hd__diode_2 and failing top-level
# pin matching. See normalize_diode_spice.py and references/failure-patterns.md
# "sky130 LVS" cause 5 (fixed 2026-06-11).
python3 "$(dirname "${BASH_SOURCE[0]}")/normalize_diode_spice.py" "$EXTRACTED_SPICE"

# Reconcile extractor-vs-library representation differences without touching
# the source GDS, powered Verilog, original PDK library, or canonical open_pdks
# setup.  Each normalized artifact has an exact transform-count/digest receipt.
# The compatibility setup keeps diode topology/model/polarity/area strict and
# drops only its non-comparable Magic-vs-library perimeter convention.
SKY130_NORMALIZER="$(dirname "${BASH_SOURCE[0]}")/normalize_sky130_lvs_spice.py"
if [[ -n "$SC_SPICE" ]]; then
  python3 "$SKY130_NORMALIZER" layout "$EXTRACTED_SPICE" \
    --receipt "$LVS_DIR/layout_normalization.json"
  NORMALIZED_SC_SPICE="$LVS_DIR/standard_cells.normalized.spice"
  python3 "$SKY130_NORMALIZER" library "$SC_SPICE" "$NORMALIZED_SC_SPICE" \
    --receipt "$LVS_DIR/library_normalization.json"
  SC_SPICE="$NORMALIZED_SC_SPICE"
  NETGEN_COMPAT_SETUP="$(dirname "${BASH_SOURCE[0]}")/sky130_netgen_compat.tcl"
  NETGEN_EFFECTIVE_SETUP="$NETGEN_COMPAT_SETUP"
fi

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
lvs "\$circuit1 $DESIGN_NAME" "\$circuit2 $DESIGN_NAME" "$NETGEN_EFFECTIVE_SETUP" "$NETGEN_REPORT"
NETGEN_EOF
else
  cat > "$NETGEN_TCL" << NETGEN_EOF
set circuit1 [readnet spice "$EXTRACTED_SPICE"]
set circuit2 [readnet verilog "$VERILOG_NETLIST"]
lvs "\$circuit1 $DESIGN_NAME" "\$circuit2 $DESIGN_NAME" "$NETGEN_EFFECTIVE_SETUP" "$NETGEN_REPORT"
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
  env MAGIC_EXT_USE_GDS=1 R2G_NETGEN_BASE_SETUP="$NETGEN_SETUP" \
  "$NETGEN_CMD" -batch source "$NETGEN_TCL"
LVS_STATUS=$?
set -e
tail -n 25 "$NETGEN_LOG" 2>/dev/null || true

# Parse results
LVS_RESULT="unknown"
MATCH_STATUS="unknown"
if [[ -f "$NETGEN_LOG" ]]; then
  # Parser diagnostics invalidate the comparison itself.  Older Netgen builds
  # can continue after malformed escaped identifiers and emit a plausible
  # topology mismatch; that is an infrastructure error, never design evidence.
  if grep -qiE "Expected to find (instance pin block|end of instance)|Error:  No match in call for pin|has no pins" "$NETGEN_LOG" 2>/dev/null; then
    LVS_RESULT="error"
    MATCH_STATUS="unknown"
    [[ "$LVS_STATUS" -ne 0 ]] || LVS_STATUS=2
  elif grep -qi "property errors" "$NETGEN_LOG" 2>/dev/null; then
    LVS_RESULT="mismatch"
    MATCH_STATUS="mismatch"
  elif grep -qi "Circuits match uniquely\|Result: PASS\|netlists match" "$NETGEN_LOG" 2>/dev/null; then
    LVS_RESULT="clean"
    MATCH_STATUS="match"
  elif grep -qi "mismatch\|NOT match\|Result: FAIL\|netlists do not match" "$NETGEN_LOG" 2>/dev/null; then
    LVS_RESULT="mismatch"
    MATCH_STATUS="mismatch"
  fi
fi

# Also check the report file
if [[ -f "$NETGEN_REPORT" ]] && [[ "$MATCH_STATUS" == "unknown" ]]; then
  if grep -qi "property errors" "$NETGEN_REPORT" 2>/dev/null; then
    LVS_RESULT="mismatch"
    MATCH_STATUS="mismatch"
  elif grep -qi "Circuits match\|PASS" "$NETGEN_REPORT" 2>/dev/null; then
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
  if grep -qi 'property errors' "$NETGEN_REPORT"; then
    MISMATCH_CLASS="netgen_property"
  elif grep -qi 'Top level cell failed pin matching' "$NETGEN_REPORT" && \
       ! grep -qiE 'Number of (devices|nets):.*\*\*Mismatch\*\*' "$NETGEN_REPORT"; then
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
  elif grep -qiE 'Number of (devices|nets):.*\*\*MISMATCH\*\*|do not match|Top level cell failed pin matching' "$NETGEN_REPORT"; then
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
  "raw_extracted_spice": "$RAW_EXTRACTED_SPICE",
  "raw_extracted_sha256": "$(sha256sum "$RAW_EXTRACTED_SPICE" | cut -d' ' -f1)",
  "reference_netlist": "$VERILOG_NETLIST",
  "reference_sha256": "$(sha256sum "$VERILOG_NETLIST" | cut -d' ' -f1)",
  "extracted_sha256": "$(sha256sum "$EXTRACTED_SPICE" | cut -d' ' -f1)",
  "standard_cell_library_source": "$SC_SPICE_SOURCE",
  "standard_cell_library_source_sha256": "$([[ -n "$SC_SPICE_SOURCE" ]] && sha256sum "$SC_SPICE_SOURCE" | cut -d' ' -f1 || true)",
  "standard_cell_library": "$SC_SPICE",
  "standard_cell_library_sha256": "$([[ -n "$SC_SPICE" ]] && sha256sum "$SC_SPICE" | cut -d' ' -f1 || true)",
  "netgen_setup_sha256": "$(sha256sum "$NETGEN_SETUP" | cut -d' ' -f1)",
  "netgen_compat_setup": "$NETGEN_COMPAT_SETUP",
  "netgen_compat_setup_sha256": "$([[ -n "$NETGEN_COMPAT_SETUP" ]] && sha256sum "$NETGEN_COMPAT_SETUP" | cut -d' ' -f1 || true)",
  "power_connectivity_receipt": "$POWER_CONNECTIVITY_RECEIPT",
  "power_connectivity_receipt_sha256": "$(sha256sum "$POWER_CONNECTIVITY_RECEIPT" 2>/dev/null | cut -d' ' -f1 || true)",
  "magic_executable": "$MAGIC_EXE",
  "magic_version": "$MAGIC_VERSION",
  "magic_required": "$MAGIC_REQUIRED",
  "netgen_executable": "$NETGEN_CMD",
  "netgen_version": "$NETGEN_VERSION",
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
  cp "$LVS_DIR"/*normalization.json "$TARGET_RUN/lvs/" 2>/dev/null || true
  cp "$LVS_DIR"/power_connectivity.json "$TARGET_RUN/lvs/" 2>/dev/null || true
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
