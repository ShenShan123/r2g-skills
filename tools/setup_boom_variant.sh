#!/usr/bin/env bash
# Bootstrap a BOOM Chipyard variant under design_cases/<name>/ from
# batch2rtl/BOOM CPU/<variant>/. The setup mirrors the structure used by the
# already-validated boom_smallseboom case:
#   * copy the top, plusarg_reader, EICG_wrapper, IOCell, freepdk45 wrapper
#   * regenerate openram_stubs.v from the wrapper file
#   * copy chipyard_stubs.v from boom_smallseboom (same ClockDividerN stub)
#   * write a config.mk + constraint.sdc using the SmallSEBoom template,
#     with DESIGN_NAME and the top file path swapped per variant
#
# Usage:
#   tools/setup_boom_variant.sh <project-name> <variant-dir>
# Example:
#   tools/setup_boom_variant.sh boom_smallboomnol2 \
#       "batch2rtl/BOOM CPU/SmallBoomNoL2_OpenRAM_FreePDK45"

set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 <project-name> <variant-dir>" >&2
  exit 1
fi

NAME="$1"
SRC="$2"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/design_cases/$NAME"

# Locate variant components
TOP=$(ls "$SRC/rtl/"*.top.v 2>/dev/null | head -1)
WRAPPER="$SRC/rtl/freepdk45_autogen_openram_sram.v"
EICG="$SRC/rtl/EICG_wrapper.v"
IOCELL="$SRC/rtl/IOCell.v"
PLUSARG="$SRC/rtl/plusarg_reader.v"
TEMPLATE="$ROOT/design_cases/boom_smallseboom"

for f in "$TOP" "$WRAPPER" "$EICG" "$IOCELL" "$PLUSARG"; do
  [[ -f "$f" ]] || { echo "missing: $f" >&2; exit 1; }
done
[[ -d "$TEMPLATE/rtl" ]] || { echo "template missing: $TEMPLATE" >&2; exit 1; }

# Initialize layout (mirrors scripts/project/init_project.py output)
python3 "$ROOT/r2g-rtl2gds/scripts/project/init_project.py" "$NAME" >/dev/null

# Copy variant RTL
cp "$TOP" "$DEST/rtl/$(basename "$TOP")"
cp "$WRAPPER" "$DEST/rtl/$(basename "$WRAPPER")"
cp "$EICG" "$DEST/rtl/$(basename "$EICG")"
cp "$IOCELL" "$DEST/rtl/$(basename "$IOCELL")"
cp "$PLUSARG" "$DEST/rtl/$(basename "$PLUSARG")"

# Reuse SmallSEBoom's helper stubs (same Chipyard scaffolding across all variants)
cp "$TEMPLATE/rtl/chipyard_stubs.v" "$DEST/rtl/chipyard_stubs.v"

# Generate behavioral OpenRAM stubs from THIS variant's wrapper
python3 "$ROOT/tools/gen_openram_behavioral_stubs.py" \
    "$DEST/rtl/$(basename "$WRAPPER")" \
    "$DEST/rtl/openram_stubs.v"

# Build the VERILOG_FILES list for config.mk via Python (avoids heredoc
# backslash-doubling pitfalls).
python3 - <<PYEOF >"$DEST/constraints/config.mk"
import os
files = [
    "EICG_wrapper.v",
    "IOCell.v",
    "plusarg_reader.v",
    "chipyard_stubs.v",
    "openram_stubs.v",
    os.path.basename("$WRAPPER"),
    os.path.basename("$TOP"),
]
abs_files = [os.path.join("$DEST/rtl", f) for f in files]
print("export DESIGN_NAME = ChipTop")
print("export PLATFORM    = nangate45")
print()
print("export VERILOG_FILES = \\\\")
for f in abs_files[:-1]:
    print(f"    {f} \\\\")
print(f"    {abs_files[-1]}")
print()
print("export SDC_FILE      = $DEST/constraints/constraint.sdc")
PYEOF
cat >> "$DEST/constraints/config.mk" <<EOF

# Behavioral SRAM stubs allow up to 64K-bit memories; largest BOOM cut is
# 1w1r_512x64 = 32K bits.
export SYNTH_MEMORY_MAX_BITS = 65536

# Hierarchical synthesis keeps ABC bounded per kept module — proved out by
# the boom_smallseboom retry (43 min synth vs 2h28m flat-mode timeout).
export SYNTH_HIERARCHICAL = 1
export ABC_AREA           = 1

# BOOM is large; safety flags avoid OpenROAD CTS SIGSEGV.
export SKIP_CTS_REPAIR_TIMING = 1
export SKIP_LAST_GASP = 1

# First-pass area target (will adjust if floorplan/place fails).
export CORE_UTILIZATION = 25
export PLACE_DENSITY_LB_ADDON = 0.25
EOF

# Write constraint.sdc (single core_clock, false_path on JTAG/reset)
cat > "$DEST/constraints/constraint.sdc" <<'EOF'
current_design ChipTop
set clk_name      core_clock
set clk_port_name clock_clock
set clk_period    20.0
set clk_io_pct    0.2

set clk_port [get_ports $clk_port_name]
create_clock -name $clk_name -period $clk_period $clk_port

set non_clock_inputs [all_inputs -no_clocks]
set_input_delay  [expr $clk_period * $clk_io_pct] -clock $clk_name $non_clock_inputs
set_output_delay [expr $clk_period * $clk_io_pct] -clock $clk_name [all_outputs]

set_false_path -from [get_ports {jtag_TCK jtag_TMS jtag_TDI reset}]
set_false_path -to   [get_ports {jtag_TDO}]
EOF

# Write rtl-notes
cat > "$DEST/reports/rtl-notes.md" <<EOF
# $NAME — RTL setup notes

Source: \`$SRC/rtl/\`
Top: ChipTop (Chipyard TestHarness top)
Generated: $(date -Is)

Files:
- $(basename "$TOP") — design top
- $(basename "$WRAPPER") — \`*_ext\` SRAM wrappers (instantiates undefined freepdk45_sram_*)
- openram_stubs.v — behavioral 1rw0r/1w1r flop-array implementations of every
  freepdk45_sram_<rows>x<cols>[_<gran>] referenced by the wrapper
- chipyard_stubs.v — \`ClockDividerN\` parameterized stub (taken from boom_smallseboom)
- EICG_wrapper.v / IOCell.v / plusarg_reader.v — Chipyard scaffolding

Strategy: SYNTH_HIERARCHICAL=1 + ABC_AREA=1 — same approach that unblocked
boom_smallseboom's flat-mode ABC stall.
EOF

echo "Set up: $DEST"
echo "  Top:        $(basename "$TOP")"
echo "  RTL files:  $(ls "$DEST/rtl/"*.v | wc -l)"
echo "  Mem stubs:  $(grep -c '^module ' "$DEST/rtl/openram_stubs.v")"
echo "Run with:    ORFS_TIMEOUT=21600 bash r2g-rtl2gds/scripts/flow/run_orfs.sh $DEST"
