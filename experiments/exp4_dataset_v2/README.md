# Exp 4 dataset v2 -- DROPPED (2026-11-07)

`selection.json` / `select_designs.py` record a proposed 300-per-platform selection (baseline DRC+LVS clean,
300-30k cells, identical size strata 136/150/14, 215 RTL designs common to all three platforms). The stored
baseline runs lack 1_2_yosys.v and the floorplan/CTS/GRT stage databases (and nangate45 lacks 3_place.odb),
so building it needs physical-design re-runs. The user dropped the plan for that reason; nothing was launched.
Kept only as a record.
