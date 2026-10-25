"""Image dataset (build_images.py + verify_images.py) on a hand-checkable design.

The die is NON-square (40 x 20 um, 8 x 4 GCells of 5 um) so a transposed array
cannot pass a shape check by accident, and one cell is rotated (E) so a
footprint that ignores orientation lands in the wrong GCells. Values are checked
against hand arithmetic first; then every channel is corrupted in turn and the
independent verifier must name the corruption.
"""
from __future__ import annotations

import csv
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

SKILL = Path(__file__).resolve().parents[1]
IMG = SKILL / "scripts" / "extract" / "images"
sys.path.insert(0, str(IMG))

import build_images  # noqa: E402
import extract_congestion  # noqa: E402

LEF = """VERSION 5.8 ;
MACRO INVX
  CLASS CORE ;
  SIZE 1 BY 2 ;
  PIN A
    PORT
      LAYER metal1 ;
        RECT 0.1 0.1 0.3 0.3 ;
    END
  END A
  PIN Z
    PORT
      LAYER metal1 ;
        RECT 0.6 1.0 0.8 1.2 ;
    END
  END Z
END INVX
MACRO BIGM
  CLASS BLOCK ;
  SIZE 10 BY 6 ;
  PIN D
    PORT
      LAYER metal1 ;
        RECT 0 0 1 1 ;
    END
  END D
END BIGM
END LIBRARY
"""

DEF_TMPL = """VERSION 5.8 ;
DESIGN toy ;
UNITS DISTANCE MICRONS 1000 ;
DIEAREA ( 0 0 ) ( 40000 20000 ) ;
COMPONENTS 4 ;
- u1 INVX + PLACED ( 1000 1000 ) N ;
- u2 INVX + PLACED ( 12000 3000 ) E ;
- u3 INVX + PLACED ( 33000 15000 ) FS ;
- m1 BIGM + PLACED ( 20000 8000 ) N ;
END COMPONENTS
PINS 1 ;
- in1 + NET n1 + DIRECTION INPUT + USE SIGNAL
  + LAYER metal3 ( 0 0 ) ( 100 100 )
  + PLACED ( 0 5000 ) N ;
END PINS
NETS 3 ;
- n1 ( PIN in1 ) ( u1 A ) + USE SIGNAL ;
- n2 ( u1 Z ) ( u2 A ) ( m1 D ) + USE SIGNAL {ROUTE};
- VDD ( u1 VDD ) ( u2 VDD ) + USE POWER ;
END NETS
END DESIGN
"""

CONG = {(7, 0): (0.5, 0.40), (0, 3): (0.2, 0.25), (2, 1): (0.9, 0.7)}   # (x, y): (util, gaussian)


@pytest.fixture
def toy(tmp_path):
    (tmp_path / "cells.lef").write_text(LEF)
    (tmp_path / "place.def").write_text(DEF_TMPL.replace("{ROUTE}", ""))
    labels = tmp_path / "labels"
    labels.mkdir()
    json.dump({"design": "toy", "gcell_size": [8, 4], "index_order": "xy", "grid_step_dbu": [5000, 5000],
               "db_units": 1000.0, "die_dbu": [0, 0, 40000, 20000]}, open(labels / "congestion_grid.json", "w"))
    with open(labels / "congestion_grid.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["x_gcell", "y_gcell", "util", "gaussian_util"])
        for (x, y), (u, gv) in CONG.items():
            w.writerow([x, y, u, gv])
    with open(labels / "ir_drop.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Design", "Cell", "X", "Y", "Voltage_V", "IR_Drop_mV", "P95_mV", "label", "has_irdrop"])
        w.writerow(["toy", "u1", 1.0, 1.0, 1.095, 5.0, 12, 0.1, "true"])
        w.writerow(["toy", "u3", 33.0, 15.0, 1.088, 12.0, 12, 0.2, "true"])
    # the graph dataset's per-cell label, computed by the REAL label code
    util = [[0.0] * 4 for _ in range(8)]
    gauss = [[0.0] * 4 for _ in range(8)]
    for (x, y), (u, gv) in CONG.items():
        util[x][y], gauss[x][y] = u, gv
    sizes = {"INVX": (1.0, 2.0), "BIGM": (10.0, 6.0)}
    with open(labels / "cell_congestion.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Design", "Cell", "cell_type", "cell_congestion", "label", "label_raw"])
        for name, m, x, y, o in (("u1", "INVX", 1000, 1000, "N"), ("u2", "INVX", 12000, 3000, "E"),
                                 ("u3", "INVX", 33000, 15000, "FS"), ("m1", "BIGM", 20000, 8000, "N")):
            bb = extract_congestion.cell_bbox_dbu(x, y, m, o, sizes, 1000)
            c, lab, raw = extract_congestion.cell_congestion_over_bbox(bb, x, y, util, gauss, 5000, 5000, 8, 4)
            w.writerow(["toy", name, m, c, lab, raw])
    return tmp_path


def _build(toy, out="img"):
    build_images.main(["--feature-def", str(toy / "place.def"), "--labels-dir", str(toy / "labels"),
                       "--out-dir", str(toy / out), "--lef", str(toy / "cells.lef")])
    return toy / out


def _verify(toy, img):
    r = subprocess.run([sys.executable, str(IMG / "verify_images.py"), str(img), "--labels-dir", str(toy / "labels"),
                        "--final-def", str(toy / "place.def"), "--lef", str(toy / "cells.lef"),
                        "--json", str(toy / "v.json")], capture_output=True, text=True)
    res = json.load(open(toy / "v.json"))
    return r.returncode, {x["check"]: x["ok"] for x in res["results"]}


def test_values_by_hand(toy):
    img = _build(toy)
    F, L = np.load(img / "features.npz"), np.load(img / "labels.npz")
    assert F["cell_density"].shape == (8, 4)                       # [x][y], not (4, 8)
    cd = F["cell_density"]
    # u1: 1x2 um at (1,1) -> GCell (0,0), area 2 of 25
    assert cd[0, 0] == pytest.approx(2 / 25)
    # u2 rotated E: footprint 2x1 at (12,3) -> GCell (2,0) only
    assert cd[2, 0] == pytest.approx(2 / 25)
    # u3 FS keeps 1x2 at (33,15) -> GCell (6,3)
    assert cd[6, 3] == pytest.approx(2 / 25)
    # macro 10x6 at (20,8) is NOT standard-cell density, it is macro_region:
    mr = F["macro_region"]
    assert mr[4, 1] == pytest.approx(5 * 2 / 25) and mr[4, 2] == pytest.approx(5 * 4 / 25)
    assert mr[5, 2] == pytest.approx(5 * 4 / 25) and mr[4, 0] == 0.0
    assert cd[4, 2] == 0.0
    # pins: n1 (in1 + u1.A) and n2 (u1.Z, u2.A, m1.D); VDD is skipped
    assert F["pin_density"].sum() == 5
    # labels are exactly the grid CSV, at their [x][y]
    assert L["congestion"][7, 0] == pytest.approx(0.40) and L["congestion_raw"][2, 1] == pytest.approx(0.9)
    assert L["ir_drop"][0, 0] == pytest.approx(5.0) and L["ir_drop"][6, 3] == pytest.approx(12.0)
    meta = json.load(open(img / "image_meta.json"))
    assert meta["shape"] == [8, 4] and meta["feature_stats"]["macros"] == 1


def test_verifier_passes_clean_build(toy):
    rc, res = _verify(toy, _build(toy))
    assert rc == 0, [k for k, v in res.items() if not v]
    binding = json.loads((toy / "v.json").read_text())["output_binding"]
    assert binding["before"] == binding["after"]
    assert {entry["path"] for entry in binding["after"]} == {
        "images/features.npz", "images/labels.npz", "images/image_meta.json"}


def test_verifier_rejects_core_file_mutation_after_loading(toy, monkeypatch):
    import verify_images
    img = _build(toy)
    original = verify_images.read_lef

    def mutate_after_loading(*args):
        # The loaded tensors still satisfy every numerical check. Only the
        # verifier's byte binding can detect the changed artifact on disk.
        with (img / "features.npz").open("ab") as stream:
            stream.write(b"changed after load")
        return original(*args)

    monkeypatch.setattr(verify_images, "read_lef", mutate_after_loading)
    report = toy / "mutation.json"
    rc = verify_images.main([str(img), "--labels-dir", str(toy / "labels"), "--final-def", str(toy / "place.def"),
                             "--lef", str(toy / "cells.lef"), "--json", str(report)])
    observed = json.loads(report.read_text())
    assert rc == 1
    assert [r["check"] for r in observed["results"] if not r["ok"]] == ["provenance.output_bytes_unchanged"]
    assert observed["output_binding"]["before"] != observed["output_binding"]["after"]


@pytest.mark.parametrize("channel,corrupt,expect", [
    ("features", lambda a: {**a, "cell_density": a["cell_density"][::-1].copy()}, "cell_density.array"),
    ("features", lambda a: {**a, "rudy": a["rudy"] * 1.1}, "rudy.integral"),
    ("features", lambda a: {**a, "pin_rudy": a["pin_rudy"] * 0.9}, "pin_rudy.total"),
    ("features", lambda a: {**a, "pin_density": np.where(a["pin_density"] > 0, a["pin_density"] - 1, 0)},
     "pin_density.total"),
    ("features", lambda a: {**a, "macro_region": np.zeros_like(a["macro_region"])}, "macro_region.array"),
    ("labels", lambda a: {**a, "congestion": np.roll(a["congestion"], 1, axis=0)}, "congestion.equals_grid_csv"),
    ("labels", lambda a: {**a, "ir_drop": a["ir_drop"] + (np.arange(32).reshape(8, 4) == 9) * 3.0},
     "ir_drop.zero_where_no_cell"),
    ("labels", lambda a: {**a, "congestion": a["congestion"].T.copy()}, "structure.shape_finite_nonneg"),
])
def test_verifier_catches_corruption(toy, channel, corrupt, expect):
    img = _build(toy)
    f = img / ("%s.npz" % channel)
    arrs = dict(np.load(f))
    np.savez(f, **corrupt(arrs))
    rc, res = _verify(toy, img)
    assert rc != 0 and res.get(expect) is False, res


def test_refuses_routed_feature_def(toy):
    (toy / "place.def").write_text(DEF_TMPL.replace("{ROUTE}", "+ ROUTED metal1 ( 1000 1000 ) ( 5000 * )"))
    with pytest.raises(SystemExit):
        _build(toy, "img2")
