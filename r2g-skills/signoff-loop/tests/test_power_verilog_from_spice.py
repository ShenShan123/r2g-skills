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


def test_propagates_power_ports_through_child_modules():
    source = """module top (a, y);
 input a;
 output y;
 child u0 (.a(a), .y(y));
endmodule
module child (a, y);
 input a;
 output y;
 sky130_fd_sc_hd__nand2_1 n0 (.A(a), .B(a), .Y(y));
endmodule
"""
    powered, _ = p.add_power_pins(source, p.cell_power_pins(SPICE))
    output, stats = p.propagate_hierarchical_power_ports(powered, set(p.cell_power_pins(SPICE)))
    assert "module top (a, y);" in output
    assert " wire VDD;\n wire VSS;" in output
    assert "module child (a, y,\n    VDD,\n    VSS);" in output
    assert "child u0 (.a(a), .y(y),\n     .VDD(VDD),\n     .VSS(VSS));" in output
    assert " inout VDD;\n inout VSS;" in output
    assert stats["modules_powered"] == ["child", "top"]
    assert stats["child_instances_connected"] == 1
    assert stats["positional_child_instances_skipped"] == 0


def test_hierarchical_power_propagation_is_idempotent():
    source = """module top (VDD, VSS); inout VDD; inout VSS;
 child u0 (.VDD(VDD), .VSS(VSS));
endmodule
module child (VDD, VSS); inout VDD; inout VSS;
endmodule
"""
    output, stats = p.propagate_hierarchical_power_ports(source, set())
    assert output == source
    assert stats["changed"] is False
