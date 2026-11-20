import numpy as np
from mst_prep import rmst
# 4 pins on a square: MST length (Manhattan) must be 3 edges of length 2
pts = np.array([[0, 0], [2, 0], [2, 2], [0, 2]])
e = rmst(pts); L = sum(np.abs(pts[i] - pts[j]).sum() for i, j in e)
assert len(e) == 3 and L == 6, (e, L)
# collinear pins: MST is the chain, total length = span
pts = np.array([[0, 0], [5, 0], [2, 0], [9, 0]]); e = rmst(pts)
assert sum(np.abs(pts[i] - pts[j]).sum() for i, j in e) == 9
print("rmst OK")
