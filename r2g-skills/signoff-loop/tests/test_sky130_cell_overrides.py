"""CDL topology override for sky130 LVS (scripts/flow/apply_sky130_cell_overrides.py).

failure-patterns.md "sky130 LVS", CDL topology override: a few cells' CDL describe shared-node
m=2 stacks where the layout has split stacks; only those definitions are replaced by the
open_pdks SPICE ones, every other definition must stay byte-identical, and a listed cell that is
missing fails closed (keeps the CDL definition, reported in the receipt).
"""
import json
import subprocess
import sys
from pathlib import Path

FLOW = Path(__file__).resolve().parents[1] / "scripts" / "flow"
sys.path.insert(0, str(FLOW))
import apply_sky130_cell_overrides as ovr  # noqa: E402

CDL = """* header kept
.SUBCKT sky130_fd_sc_hd__a21oi_1 A1 A2 B1 VGND VNB VPB VPWR Y
MMNA0 Y A1 sndA1 VNB sky130_fd_pr__nfet_01v8 m=1 w=0.65 l=0.15
+ sb=0.265
.ENDS sky130_fd_sc_hd__a21oi_1
.SUBCKT sky130_fd_sc_hd__a21oi_2 A1 A2 B1 VGND VNB VPB VPWR Y
MMNA0 Y A1 sndA1 VNB sky130_fd_pr__nfet_01v8 m=2 w=0.65 l=0.15
+ sb=0.265
MMNA1 sndA1 A2 VGND VNB sky130_fd_pr__nfet_01v8 m=2 w=0.65 l=0.15
.ENDS sky130_fd_sc_hd__a21oi_2
* trailer kept
"""
PDK = """.subckt sky130_fd_sc_hd__a21oi_2 A1 A2 B1 VGND VNB VPB VPWR Y
X0 VGND A2 a_114_47# VNB sky130_fd_pr__nfet_01v8 w=650000u l=150000u
X10 a_114_47# A1 Y VNB sky130_fd_pr__nfet_01v8 w=650000u
+ l=150000u
X9 a_285_47# A2 VGND VNB sky130_fd_pr__special_nfet_01v8 w=650000u l=150000u
.ends
.subckt sky130_fd_sc_hd__a21oi_1 A1 A2 B1 VGND VNB VPB VPWR Y
X0 Y A1 n1 VNB sky130_fd_pr__nfet_01v8 w=650000u l=150000u
.ends
"""


def test_only_listed_cells_are_replaced_and_normalized():
    out, fields = ovr.apply_overrides(CDL, PDK, ["sky130_fd_sc_hd__a21oi_2"])
    assert sorted(fields["replaced"]) == ["sky130_fd_sc_hd__a21oi_2"] and fields["not_replaced"] == {}
    blocks = ovr.subckt_blocks(out)
    s, e = blocks["sky130_fd_sc_hd__a21oi_2"]
    new = out[s:e]
    assert "a_114_47#" in new and "a_285_47#" in new and "sndA1" not in new      # split stacks
    assert "\nM0 " in new and "\nX" not in new                                     # X calls -> M records
    assert "special_nfet" not in new and "\n+" not in new                          # normalized, joined
    # everything outside the replaced block is byte-identical
    cs, ce = ovr.subckt_blocks(CDL)["sky130_fd_sc_hd__a21oi_2"]
    assert out[:s] == CDL[:cs] and out[e:] == CDL[ce:]


def test_missing_cells_fail_closed():
    out, fields = ovr.apply_overrides(CDL, PDK, ["sky130_fd_sc_hd__ha_4", "sky130_fd_sc_hd__a21oi_2"])
    assert fields["not_replaced"] == {"sky130_fd_sc_hd__ha_4": "absent from the CDL"}
    pdk_without = PDK.split(".subckt sky130_fd_sc_hd__a21oi_1")[0].replace("a21oi_2", "zzz")
    out2, fields2 = ovr.apply_overrides(CDL, pdk_without, ["sky130_fd_sc_hd__a21oi_2"])
    assert out2 == CDL and fields2["not_replaced"] == {
        "sky130_fd_sc_hd__a21oi_2": "absent from the open_pdks SPICE"}


def test_cli_writes_receipt_and_rewrites_in_place(tmp_path):
    cdl, pdk, rec, lst = (tmp_path / n for n in ("lib.spice", "pdk.spice", "receipt.json", "list.json"))
    cdl.write_text(CDL)
    pdk.write_text(PDK)
    lst.write_text(json.dumps({"libraries": {"sky130_fd_sc_hd": ["sky130_fd_sc_hd__a21oi_2"]}}))
    subprocess.run([sys.executable, str(FLOW / "apply_sky130_cell_overrides.py"), str(cdl), str(pdk),
                    "sky130_fd_sc_hd", "--list", str(lst), "--receipt", str(rec)], check=True,
                   capture_output=True)
    r = json.loads(rec.read_text())
    assert list(r["replaced"]) == ["sky130_fd_sc_hd__a21oi_2"]
    assert r["cdl_before_sha256"] != r["cdl_after_sha256"] == ovr._sha(cdl.read_text())
    # a library with no listed cells is left untouched
    subprocess.run([sys.executable, str(FLOW / "apply_sky130_cell_overrides.py"), str(cdl), str(pdk),
                    "sky130_fd_sc_hs", "--list", str(lst), "--receipt", str(rec)], check=True,
                   capture_output=True)
    r2 = json.loads(rec.read_text())
    assert r2["replaced"] == {} and r2["cdl_before_sha256"] == r2["cdl_after_sha256"]


def test_shipped_override_list():
    spec = json.loads((FLOW / "sky130_cdl_topology_overrides.json").read_text())
    cells = spec["libraries"]["sky130_fd_sc_hd"]
    assert len(cells) == len(set(cells)) == 10 and "sky130_fd_sc_hd__a21oi_2" in cells
    assert spec["provenance"]["cdl_source_sha256"].startswith("ae02cdc4")
