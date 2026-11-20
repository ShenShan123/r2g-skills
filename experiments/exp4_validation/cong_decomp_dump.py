"""OpenROAD-embedded Python: per-layer per-gcell stored capacity/usage of 5_1_grt.odb plus the
routed demand reconstructed from the stored GR guides (an edge between adjacent gcells along the
layer's preferred direction that a guide spans = 1 track of demand, as in FastRoute's edge model).
env DESIGN_DIR OUT_DIR -> OUT_DIR/cong_decomp.json"""
import json, os
from openroad import Design, Tech
D, O = os.environ['DESIGN_DIR'], os.environ['OUT_DIR']
t = Tech(); d = Design(t); d.evalTclString('read_db %s/5_1_grt.odb' % D)
b = d.getBlock(); g = b.getGCellGrid()
gx, gy = list(g.getGridX()), list(g.getGridY()); nx, ny = len(gx), len(gy)
die = b.getDieArea()
import bisect
def ix(x): return max(0, min(nx - 1, bisect.bisect_right(gx, x) - 1))
def iy(y): return max(0, min(ny - 1, bisect.bisect_right(gy, y) - 1))
layers = {}
for lay in b.getTech().getLayers():
    if lay.getType() != 'ROUTING':
        continue
    layers[lay.getName()] = dict(dir=lay.getDirection(), level=lay.getRoutingLevel(),
                                 cap=[[g.getCapacity(lay, i, j) for i in range(nx)] for j in range(ny)],
                                 use=[[g.getUsage(lay, i, j) for i in range(nx)] for j in range(ny)],
                                 dem=[[0] * nx for _ in range(ny)])
nguides = 0
for net in b.getNets():
    if net.getSigType() in ('POWER', 'GROUND'):
        continue
    for gd in net.getGuides():
        L = layers.get(gd.getLayer().getName())
        if L is None:
            continue
        bx = gd.getBox(); nguides += 1
        # guide boxes are gcell-aligned; use box centre-of-gcell indices
        i0, i1 = ix(bx.xMin() + 1), ix(bx.xMax() - 1); j0, j1 = iy(bx.yMin() + 1), iy(bx.yMax() - 1)
        if L['dir'] == 'HORIZONTAL':
            for j in range(j0, j1 + 1):
                for i in range(i0, i1):
                    L['dem'][j][i] += 1
        else:
            for i in range(i0, i1 + 1):
                for j in range(j0, j1):
                    L['dem'][j][i] += 1
json.dump(dict(nx=nx, ny=ny, grid_x=gx, grid_y=gy, die=[die.xMin(), die.yMin(), die.xMax(), die.yMax()], guides=nguides, layers=layers),
          open(os.path.join(O, 'cong_decomp.json'), 'w'))
print('CONG_OK', nx, ny, nguides)
