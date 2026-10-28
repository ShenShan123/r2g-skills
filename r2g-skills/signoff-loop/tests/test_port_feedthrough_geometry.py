import pytest
from diagnose_port_feedthroughs import diagnose


def subject(tmp_path):
    definition=tmp_path/'6_final.def';netlist=tmp_path/'6_final.v'
    definition.write_text('''PINS 3 ;
- a + NET shared + DIRECTION INPUT + USE SIGNAL ;
- b + NET shared + DIRECTION OUTPUT + USE SIGNAL ;
- c + NET shared + DIRECTION OUTPUT + USE SIGNAL ;
END PINS
''')
    netlist.write_text('module example(input a, output b, output c); assign b=a; assign c=a; endmodule\n')
    return definition,netlist


def test_input_to_multiple_outputs_witnessed(tmp_path):
    definition,netlist=subject(tmp_path);e=diagnose(definition,netlist)
    assert e['shared_top_port_nets']==1 and e['aliased_output_count']==2
    assert e['all_aliases_have_single_input'] is True
    assert e['examples'][0]=={'net':'shared','input':'a','outputs':['b','c']}
    assert len(e['def_sha256'])==len(e['mapped_netlist_sha256'])==64


@pytest.mark.parametrize('old,new',[
    ('PINS 3','PINS 4'),
    ('DIRECTION INPUT','DIRECTION INOUT'),
    ('- a + NET shared + DIRECTION INPUT','- a + NET shared + DIRECTION OUTPUT'),
    ('- b + NET shared + DIRECTION OUTPUT','- b + NET shared + DIRECTION INPUT'),
    ('- c + NET shared','- b + NET shared'),
    ('END PINS','TRUNCATED'),
])
def test_unsupported_or_incomplete_topology_rejected(tmp_path,old,new):
    definition,netlist=subject(tmp_path);definition.write_text(definition.read_text().replace(old,new))
    assert diagnose(definition,netlist) is None


def test_mapped_bidirectional_port_rejected_even_if_def_disagrees(tmp_path):
    definition,netlist=subject(tmp_path)
    netlist.write_text(netlist.read_text().replace('input a','inout a'))
    assert diagnose(definition,netlist) is None


def test_inout_comment_is_not_a_port_declaration(tmp_path):
    definition,netlist=subject(tmp_path)
    netlist.write_text('// inout is unsupported\n/* inout commentary */\n'+netlist.read_text())
    assert diagnose(definition,netlist) is not None


def test_special_supply_inouts_are_separate_from_signal_ports(tmp_path):
    definition,netlist=subject(tmp_path)
    text=definition.read_text().replace('PINS 3','PINS 5').replace('END PINS',
        '- VDD + NET VDD + SPECIAL + DIRECTION INOUT + USE POWER ;\n'
        '- VSS + NET VSS + SPECIAL + DIRECTION INOUT + USE GROUND ;\nEND PINS')
    definition.write_text(text)
    evidence=diagnose(definition,netlist)
    assert evidence['ignored_special_supply_ports']==2 and evidence['shared_top_port_nets']==1
    definition.write_text(text.replace('+ SPECIAL',''))
    assert diagnose(definition,netlist) is None
