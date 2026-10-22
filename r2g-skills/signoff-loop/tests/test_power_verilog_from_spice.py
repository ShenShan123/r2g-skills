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


def test_preserves_escaped_child_identifier_separators():
    # Installed Netgen includes the terminating space in an escaped token,
    # then scans until a second delimiter. Do not collapse OpenROAD's spaces.
    source = r'''module top (a, y);
 input a; output y;
 \ALU[1:0]  \instance[1:0]  (.a(a), .y(y));
endmodule
module \ALU[1:0]  (a, y);
 input a; output y;
 sky130_fd_sc_hd__nand2_1 n0 (.A(a), .B(a), .Y(y));
endmodule
'''
    output, stats = p.propagate_hierarchical_power_ports(source, set(p.cell_power_pins(SPICE)))
    assert r'\ALU[1:0]  \instance[1:0]  (' in output
    assert stats['child_instances_connected'] == 1
    assert '.VDD(VDD)' in output and '.VSS(VSS)' in output
    again, _ = p.propagate_hierarchical_power_ports(output, set(p.cell_power_pins(SPICE)))
    assert output == again


def test_existing_internal_child_supply_wires_become_real_ports():
    source = '''module top (a, y);
 input a; output y;
 wire VDD;
 wire VSS;
 child u0 (.a(a), .y(y));
endmodule
module child (a, y);
 input a; output y;
 wire VDD;
 wire VSS;
 sky130_fd_sc_hd__nand2_1 n0 (.A(a), .B(a), .Y(y), .VGND(VSS), .VPWR(VDD));
endmodule
'''
    output, stats = p.propagate_hierarchical_power_ports(source)
    top, child = output.split('module child', 1)
    assert ' wire VDD;' in top and ' wire VSS;' in top
    assert ' inout VDD;' in child and ' inout VSS;' in child
    assert ' wire VDD;' not in child and ' wire VSS;' not in child
    assert stats['modules_power_declarations_promoted'] == ['child']
    again, stats2 = p.propagate_hierarchical_power_ports(output)
    assert again == output and stats2['changed'] is False


def test_previously_added_child_header_does_not_leave_power_as_internal_wire():
    source = '''module top (VDD, VSS);
 inout VDD; inout VSS;
 child u0 (.VDD(VDD), .VSS(VSS));
endmodule
module child (VDD, VSS);
 wire VDD;
 wire VSS;
 sky130_fd_sc_hd__tapvpwrvgnd_1 n0 (.VGND(VSS), .VPWR(VDD));
endmodule
'''
    output, stats = p.propagate_hierarchical_power_ports(source)
    assert ' wire VDD;' not in output and ' wire VSS;' not in output
    assert stats['changed'] and stats['modules_power_declarations_promoted'] == ['child']


def test_escaped_child_vector_port_gets_one_consistent_opaque_identifier():
    source = r'''module top (y);
 output [2:0] y;
 child u0 (.\Y[3:1] (y));
endmodule
module child (\Y[3:1] );
 output [2:0] \Y[3:1] ;
 sky130_fd_sc_hd__buf_1 n0 (.X(\Y[3:1] [0]));
endmodule
'''
    output, mapping = p.canonicalize_child_port_identifiers(source,'top')
    assert list(mapping)==['Y[3:1]']
    alias=mapping['Y[3:1]']
    assert output.count(alias)==4 and r'\Y[3:1]' not in output
    assert 'module top (y);' in output and '.X('+alias+'[0])' in output
    assert p.canonicalize_child_port_identifiers(output,'top')==(output,{})


def test_port_canonicalization_refuses_to_rename_a_public_interface():
    import pytest
    source = r'''module top (\Y[3:1] );
 output [2:0] \Y[3:1] ;
 child u0 (.\Y[3:1] (\Y[3:1] ));
endmodule
module child (\Y[3:1] );
 output [2:0] \Y[3:1] ;
endmodule
'''
    with pytest.raises(ValueError,match='public'):
        p.canonicalize_child_port_identifiers(source,'top')
