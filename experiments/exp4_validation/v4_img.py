#!/usr/bin/env python3
"""V4 images: write clean/corrupted npz COPIES for one design. Usage: v4_img.py SRC.npz OUT_DIR"""
import shutil, sys
from pathlib import Path
import numpy as np

src, out = Path(sys.argv[1]), Path(sys.argv[2])
z = dict(np.load(src, allow_pickle=False))


def save(name, **chg):
    d = out / name; d.mkdir(parents=True, exist_ok=True)
    q = dict(z); q.update(chg)
    np.savez_compressed(d / src.name, **q)


(out / 'clean').mkdir(parents=True, exist_ok=True); shutil.copyfile(src, out / 'clean' / src.name)
c = z['cong']; X = z['X']
square = c.shape[1] == c.shape[2]
save('DI1_transposed_cong', cong=np.ascontiguousarray(np.swapaxes(c, 1, 2) if square else c[:, ::-1, :]))
save('DI2_cong_h_v_swapped', cong=np.ascontiguousarray(c[::-1]))
X3 = X.copy(); X3[[2, 3]] = X[[3, 2]]; save('DI3_channel_order_swapped', X=X3)
X4 = X.copy(); X4[2] = c[0]; save('DI4_route_label_leaked_into_input', X=X4)
X5 = X.copy(); X5[5] = X[5] * 1000; save('DI5_power_unit_x1000', X=X5)
X6 = X.copy(); X6[0] = X[0] / 4; X6[1] = X[1] / 4; save('DI6_wrong_die_scale_density', X=X6)
print('IMG_INJECT', src.name)
