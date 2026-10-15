"""Grid (image-representation) export — the transpose guard comes FIRST.

The project's worst shipped defect was a demand grid keyed (y,x) instead of
(x,y): ~79.7% of congestion labels were wrong on every platform while the row
count, the column count and the value distribution all looked reasonable. An
image representation is the same hazard with a bigger blast radius, because a
transposed array still loads, still trains and still reports a loss.

So these tests are written against an ASYMMETRIC synthetic layout, where the
correct answer and its transpose differ. A symmetric fixture would pass either
way and prove nothing.

The convention under test is CircuitNet's, so a grid we emit drops straight into
that ecosystem: arrays are indexed [x][y] with shape (num_gcells_x,
num_gcells_y) -- `np.zeros(gcell_size)` with `gcell_size = [x_count, y_count]`
and `density[j, k] += 1` where j is x (CircuitNet feature_extraction/src/read.py).
Our own densify_util already builds dense[gx][gy], so the two agree and no
transpose is needed at the boundary; that agreement is what these tests pin.
"""
import csv
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "scripts", "extract", "labels"))
import extract_congestion as ec


# --------------------------------------------------------------------------- #
# A deliberately asymmetric field: a vertical wire fills ONE COLUMN.           #
# --------------------------------------------------------------------------- #
def _vertical_wire_grid():
    demand_h, demand_v = {}, {}
    # x=100 (gcell 0), y spans 0..4000 -> column x_gcell=0, rows y_gcell 0..3
    ec.add_route_segment(demand_h, demand_v, 100, 0, 100, 4000, 1000, 1000, 1000.0)
    return ec.build_grid_utilization(demand_h, demand_v, cap_h=10.0, cap_v=10.0)


def test_emitted_rows_carry_x_and_y_explicitly(tmp_path):
    """Triples, not a dense matrix. A dense dump is where a transpose hides: the
    file looks identical either way and only the reader's assumption decides. A
    row that names its own x and y cannot be silently transposed."""
    import grid_export as gx
    out = tmp_path / "congestion_grid.csv"
    util = _vertical_wire_grid()
    dense = ec.densify_util(util, 6, 4)
    gauss = ec.gaussian_filter_2d(dense, 6, 4, sigma=1.0)
    gx.write_grid_csv(str(out), dense, gauss, 6, 4)
    rows = list(csv.DictReader(open(out)))
    assert rows, "grid export wrote no rows"
    assert set(rows[0]) >= {"x_gcell", "y_gcell", "util", "gaussian_util"}


def test_column_stays_a_column_through_the_file(tmp_path):
    """The wire fills x_gcell=0 across several y. After a round trip the demand
    must still be in that COLUMN -- if it comes back as a ROW, the export
    transposed it."""
    import grid_export as gx
    out = tmp_path / "congestion_grid.csv"
    util = _vertical_wire_grid()
    dense = ec.densify_util(util, 6, 4)
    gauss = ec.gaussian_filter_2d(dense, 6, 4, sigma=1.0)
    gx.write_grid_csv(str(out), dense, gauss, 6, 4)
    hot = {(int(r["x_gcell"]), int(r["y_gcell"]))
           for r in csv.DictReader(open(out)) if float(r["util"]) > 0}
    assert hot == {(0, 0), (0, 1), (0, 2), (0, 3)}, f"got {sorted(hot)}"
    # the diagonal mirror must be empty, which is exactly what the 2026-07-05
    # transposition got wrong
    assert not {(x, y) for (x, y) in hot if x > 0}


def test_densify_roundtrip_matches_circuitnet_indexing(tmp_path):
    """Rebuild the dense array the way a consumer would and check it against the
    source array element by element, with shape (x_count, y_count)."""
    import grid_export as gx
    out = tmp_path / "congestion_grid.csv"
    util = _vertical_wire_grid()
    dense = ec.densify_util(util, 6, 4)
    gauss = ec.gaussian_filter_2d(dense, 6, 4, sigma=1.0)
    gx.write_grid_csv(str(out), dense, gauss, 6, 4)
    back = gx.read_grid_csv(str(out), 6, 4)
    assert len(back["util"]) == 6 and len(back["util"][0]) == 4
    for x in range(6):
        for y in range(4):
            assert abs(back["util"][x][y] - dense[x][y]) < 1e-9
            assert abs(back["gaussian_util"][x][y] - gauss[x][y]) < 1e-9


def test_a_transposed_reader_fails_loudly(tmp_path):
    """The negative control: a consumer that allocates (y_count, x_count) must
    not quietly succeed. Every new check here has to be proven to FAIL on a
    deliberate corruption, or it is decoration."""
    import grid_export as gx
    out = tmp_path / "congestion_grid.csv"
    util = _vertical_wire_grid()
    dense = ec.densify_util(util, 6, 4)
    gauss = ec.gaussian_filter_2d(dense, 6, 4, sigma=1.0)
    gx.write_grid_csv(str(out), dense, gauss, 6, 4)
    try:
        gx.read_grid_csv(str(out), 4, 6)        # swapped on purpose
    except ValueError as e:
        assert "out of range" in str(e) or "grid" in str(e).lower()
    else:
        raise AssertionError("a transposed read must not succeed silently")


def test_sidecar_records_the_convention_and_dims(tmp_path):
    """The dims and the index convention travel WITH the data. A consumer that
    has to guess the axis order is one assumption away from the 2026-07-05 bug."""
    import grid_export as gx
    side = tmp_path / "congestion_grid.json"
    gx.write_grid_sidecar(str(side), design="t", gridxcnt=6, gridycnt=4,
                          grid_step_x=1000, grid_step_y=1000, db_units=1000.0,
                          die=(0, 0, 6000, 4000), cap_h=10.0, cap_v=10.0,
                          nonzero=4)
    meta = json.load(open(side))
    assert meta["gcell_size"] == [6, 4]
    assert meta["index_order"] == "xy"
    assert "circuitnet" in meta["convention"].lower()
