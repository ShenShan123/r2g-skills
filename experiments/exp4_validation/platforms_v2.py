"""Per-platform inputs of the Exp-4 v2 ground truth (PROTOCOL.md §v2, frozen 2026-11-07).

File lists are what ORFS resolves for these runs (`make print-LIB_FILES / TECH_LEF / SC_LEF` inside
localhost/r2g-orfs:26Q3 with the run's own config.mk; ADDITIONAL_LEFS/LIBS are empty). The same
files sit byte-identical in memlab203's ORFS checkout (sha256 compared 2026-11-07).
`tap` = master-name substrings (upper-case) of well-tap/endcap cells for nearest_tap_distance_um;
`gate_exclude` = the FILL|TAP gate filter (identical on every platform).
"""
PLATFORMS = {
    'nangate45': dict(tech_lef='NangateOpenCellLibrary.tech.lef', sc_lef=['NangateOpenCellLibrary.macro.mod.lef'],
                      libs=['NangateOpenCellLibrary_typical.lib'], tap=('TAP',), route_def='5_route.def',
                      sdc='constraint.sdc'),
    'sky130hd': dict(tech_lef='sky130_fd_sc_hd.tlef', sc_lef=['sky130_fd_sc_hd_merged.lef'],
                     libs=['sky130_fd_sc_hd__tt_025C_1v80.lib'], tap=('TAP',), route_def='6_final.def',
                     sdc='6_final.sdc'),
    'gf180': dict(tech_lef='gf180mcu_5LM_1TM_9K_9t_tech.lef', sc_lef=['gf180mcu_5LM_1TM_9K_9t_sc.lef'],
                  libs=['gf180mcu_fd_sc_mcu9t5v0__ff_n40C_5v50.lib.gz'], tap=('TAP', 'FILLTIE', 'ENDCAP'),
                  route_def='6_final.def', sdc='6_final.sdc'),
}
GATE_EXCLUDE = r'FILL|TAP'


def files(platform, pdir):
    from pathlib import Path
    c = PLATFORMS[platform]; p = Path(pdir)
    return dict(lefs=[p / c['tech_lef']] + [p / f for f in c['sc_lef']], tech_lef=p / c['tech_lef'],
                libs=[p / f for f in c['libs']], tap=c['tap'])
