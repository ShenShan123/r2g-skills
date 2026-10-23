# Netgen setup for the nangate45 LVS fallback (r2g, 2026-10-22).
#
# Both netlists come out of the platform KLayout deck AFTER its own
# align/purge/simplify, so device merging is already done identically on both
# sides. This file only has to make Netgen judge devices the way that deck's
# comparer does: MOS W and L within 0.1% (the deck's tolerance(...) lines; the
# deck's 1 nm absolute floor is far below any FreePDK45 width), source/drain
# interchangeable, and no other device property compared (the deck compares
# none -- the extracted side carries AS/AD/PS/PD, the CDL side does not).
foreach c {1 2} {
  set cells [cells list -all -circuit$c]
  foreach dev {NMOS_VTL PMOS_VTL} {
    if {[lsearch -exact $cells $dev] >= 0} {
      property "-circuit$c $dev" delete as ad ps pd
      property "-circuit$c $dev" tolerance {w 0.001} {l 0.001}
      permute "-circuit$c $dev" drain source
    }
  }
}
