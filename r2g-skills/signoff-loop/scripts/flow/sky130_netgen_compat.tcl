# R2G Sky130 compatibility layer evaluated after the canonical open_pdks setup.
# The base setup path is passed explicitly and included in the LVS receipt.
if {![info exists env(R2G_NETGEN_BASE_SETUP)]} {
    error "R2G_NETGEN_BASE_SETUP is required"
}
source $env(R2G_NETGEN_BASE_SETUP)

# Magic and the SkyWater standard-cell SPICE describe the fixed antenna-diode
# perimeter using different junction-boundary conventions.  Keep topology,
# model class, polarity, and area strict; discard only this non-comparable
# property on both sides.
foreach side {circuit1 circuit2} {
    set cell_list [cells list -all -$side]
    if {[lsearch $cell_list sky130_fd_pr__diode_pw2nd_05v5] >= 0} {
        property "-$side sky130_fd_pr__diode_pw2nd_05v5" delete pj perim
    }
}
