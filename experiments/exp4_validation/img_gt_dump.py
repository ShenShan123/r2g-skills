"""OpenROAD-embedded Python (Exp 4 V1, image dataset): independent ground-truth dump.

Written for this experiment; shares no code with cnn_pilot500/scripts. Uses the OpenDB API only
for what has no other source: the STORED global-route GCellGrid capacity/usage of 5_1_grt.odb,
and OpenSTA instance power at the placement stage. All geometry inputs are re-derived in Python
from the DEF/LEF text by img_check.py.
env: DESIGN_DIR OUT_DIR TECH_DIR
Writes OUT_DIR/gr_truth.json {grid_x_dbu, grid_y_dbu, die, dbu, layers{name: dir, cap[ny][nx], use[ny][nx]}}
and OUT_DIR/power_truth.tsv  name<TAB>total_power_W
"""
import json
import os

from openroad import Design, Tech

D, O, T = os.environ["DESIGN_DIR"], os.environ["OUT_DIR"], os.environ["TECH_DIR"]
tech = Tech()
des = Design(tech)
des.evalTclString("read_db %s/5_1_grt.odb" % D)
blk = des.getBlock()
grid = blk.getGCellGrid()
gx, gy = list(grid.getGridX()), list(grid.getGridY())
die = blk.getDieArea()
layers = {}
for lay in blk.getTech().getLayers():
    if lay.getType() != "ROUTING":
        continue
    cap = [[grid.getCapacity(lay, i, j) for i in range(len(gx))] for j in range(len(gy))]
    use = [[grid.getUsage(lay, i, j) for i in range(len(gx))] for j in range(len(gy))]
    layers[lay.getName()] = dict(dir=lay.getDirection(), cap=cap, use=use)
json.dump(dict(grid_x_dbu=gx, grid_y_dbu=gy, die=[die.xMin(), die.yMin(), die.xMax(), die.yMax()],
               dbu=blk.getDbUnitsPerMicron(), layers=layers), open(os.path.join(O, "gr_truth.json"), "w"))

tech2 = Tech()
p = Design(tech2)
p.evalTclString("""
read_lef %(T)s/lef/NangateOpenCellLibrary.tech.lef
read_lef %(T)s/lef/NangateOpenCellLibrary.macro.mod.lef
read_liberty %(T)s/lib/NangateOpenCellLibrary_typical.lib
read_def %(D)s/3_place.def
read_sdc %(D)s/constraint.sdc
source %(T)s/setRC.tcl
estimate_parasitics -placement
set fh [open %(O)s/power_truth.tsv w]
foreach c [get_cells *] {
  set pw [sta::instance_power $c [sta::cmd_scene]]
  puts $fh "[get_full_name $c]\\t[lindex $pw 3]"
}
close $fh
""" % dict(T=T, D=D, O=O))
print("IMG_GT_OK", len(gx), len(gy))
