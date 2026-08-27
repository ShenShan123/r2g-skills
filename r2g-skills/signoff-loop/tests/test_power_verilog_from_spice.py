from __future__ import annotations

import power_verilog_from_spice as p


SPICE = """
.subckt sky130_fd_sc_hd__nand2_1 A B Y VGND VNB VPB VPWR
.ends
.subckt sky130_fd_sc_hd__tapvpwrvgnd_1 VGND VPWR
.ends
"""


def test_adds_only_library_declared_power_pins_and_supply_wires():
    source = """module top (a, y);
 input a;
 output y;
 sky130_fd_sc_hd__nand2_1 u0 (.A(a),
    .B(a),
    .Y(y));
endmodule
"""
    output, stats = p.add_power_pins(source, p.cell_power_pins(SPICE))
    assert "wire VDD;" in output and "wire VSS;" in output
    for fragment in (".VGND(VSS)", ".VNB(VSS)", ".VPB(VDD)", ".VPWR(VDD)"):
        assert fragment in output
    assert stats["instances_powered"] == 1


def test_preserves_existing_power_connections_and_is_idempotent():
    source = """module top ();
 wire VDD;
 wire VSS;
 sky130_fd_sc_hd__tapvpwrvgnd_1 t0 (.VGND(VSS), .VPWR(VDD));
endmodule
"""
    once, stats = p.add_power_pins(source, p.cell_power_pins(SPICE))
    twice, stats2 = p.add_power_pins(once, p.cell_power_pins(SPICE))
    assert once == twice == source
    assert stats["instances_powered"] == stats2["instances_powered"] == 0
