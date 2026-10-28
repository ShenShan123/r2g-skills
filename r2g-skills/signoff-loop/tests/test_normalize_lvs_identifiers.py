import hashlib
import pytest
import normalize_lvs_identifiers as n

S='.subckt top cfg[-1] y\nX1 cfg[-1] y buf\n.ends\n'
V='module top (\\cfg[-1] , y);\ninput \\cfg[-1] ;\noutput y;\nbuf b(y, \\cfg[-1] );\nendmodule\n'


def test_bijective_encoding_preserves_connected_port_and_devices():
    s,v,m=n.normalize(S,V,'top');new=m['cfg[-1]']
    assert s.replace(new,'cfg[-1]')==S
    assert v.replace(new,'\\cfg[-1]')==V
    assert n.normalize(s,v,'top')==(s,v,{})


def test_missing_layout_port_is_not_invented_or_matched():
    bad=S.replace('cfg[-1]','cfg[-2]')
    assert n.normalize(bad,V,'top')==(bad,V,{})


def test_collision_is_rejected():
    new='r2g_lvs_ident_'+hashlib.sha256(b'cfg[-1]').hexdigest()[:20]
    with pytest.raises(ValueError,match='collision'):n.normalize(S+'* '+new+'\n',V,'top')


def test_internal_names_other_modules_and_positive_indices_are_unchanged():
    assert n.normalize(S,V.replace('input ','wire '),'top')==(S,V.replace('input ','wire '),{})
    assert n.normalize(S,V+'module child(); endmodule\n','top')==(S,V+'module child(); endmodule\n',{})
    s=S.replace('[-1]','[1]');v=V.replace('[-1]','[1]')
    assert n.normalize(s,v,'top')==(s,v,{})


def test_other_subcircuits_and_whitespace_stay_exact():
    child='.subckt child cfg[-1] z\nX0 cfg[-1] z buf\n.ends\n'
    s,v,m=n.normalize(child+S,V,'top')
    assert s.startswith(child)
    assert s.replace(m['cfg[-1]'],'cfg[-1]')==child+S


CS='.subckt top CLK clk y\nX1 CLK y clk buf\n.ends\n'
CV='module top (CLK, clk, y);\ninput CLK;\noutput clk;\noutput y;\nbuf b (.CLK(CLK), .Y(clk));\nendmodule\n'


def ext_nodes(tmp_path, short=False, missing=False):
    from restore_ext_ports import parse_ext
    p=tmp_path/'top.ext'
    p.write_text('port "CLK" 0\nport "y" 2\n'+('' if missing else 'port "clk" 1\n')+
                 ('merge "CLK" "clk"\n' if short else ''))
    return parse_ext(str(p))


def test_case_alias_requires_independent_magic_ports(tmp_path):
    assert n.normalize(CS,CV,'top')==(CS,CV,{})
    with pytest.raises(ValueError,match='merged port'):
        n.normalize(CS,CV,'top',ext_nodes(tmp_path,short=True))
    with pytest.raises(ValueError,match='lack Magic'):
        n.normalize(CS,CV,'top',ext_nodes(tmp_path,missing=True))


def test_case_encoding_preserves_library_formal_pins_and_exact_inverse(tmp_path):
    child='.subckt child CLK clk\nX0 CLK clk buf\n.ends\n'
    s,v,m=n.normalize(child+CS,CV,'top',ext_nodes(tmp_path))
    assert s.startswith(child)
    assert '.CLK('+m['CLK']+')' in v
    for old,new in m.items():s=s.replace(new,old);v=v.replace(new,old)
    assert s==child+CS and v==CV


def test_case_encoding_preserves_comments_and_escaped_internal_identifiers(tmp_path):
    v=CV.replace('buf b', '// CLK is a signal\nwire \\CLK.extra ;\nbuf b')
    s,updated,m=n.normalize(CS,v,'top',ext_nodes(tmp_path))
    assert '// CLK is a signal' in updated and '\\CLK.extra ' in updated
    assert n.normalize(s,updated,'top',ext_nodes(tmp_path))==(s,updated,{})


def test_case_encoding_refuses_unsupported_signal_usage_and_spice_model_alias(tmp_path):
    with pytest.raises(ValueError,match='Verilog net position'):
        n.normalize(CS,CV.replace('endmodule','assign y = CLK;\nendmodule'),'top',ext_nodes(tmp_path))
    with pytest.raises(ValueError,match='SPICE net position'):
        n.normalize(CS.replace('clk buf','clk CLK'),CV,'top',ext_nodes(tmp_path))
