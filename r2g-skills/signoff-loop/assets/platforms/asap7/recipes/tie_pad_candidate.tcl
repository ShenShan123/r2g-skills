# tie_pad.tcl -- ASAP7 GATE.S.3 candidate (2026-11-20, from repair-arm evidence: residual GATE.S.3 poly columns pass
# through TIELOx1 edges 7.6x more often than other columns). Pads ONLY the tie cells by 2 sites each side; the global
# CELL_PAD_IN_SITES_* padding is unchanged. Sourced as PRE_DETAIL_PLACE_TCL, PRE_CTS_TCL and PRE_GLOBAL_ROUTE_TCL so every
# legalisation (detail place, CTS, GRT) honours it.
set_placement_padding -masters {TIELOx1_ASAP7_75t_R TIEHIx1_ASAP7_75t_R} -left 2 -right 2
