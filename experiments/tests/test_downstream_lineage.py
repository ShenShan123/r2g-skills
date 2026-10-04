import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import audit_downstream_lineage as oracle


def setup(monkeypatch, original_buffer=False, conflicting=False):
    endpoints = [(('source', 'Y'), '1'), (('sink', 'A'), '2' if conflicting else '1')]
    truth = {'aliases': {'signal': '1'}, 'net': {'1', '2'} if conflicting else {'1'},
             'edges': {('pin', 'connects_to', 'net'): endpoints,
                       ('io_pin', 'connects_to', 'net'): []}}
    monkeypatch.setattr(oracle, 'logical_oracle', lambda module: truth)
    module = {'cells': {'source': {}, 'sink': {}}}
    if original_buffer:
        module['cells']['newbuf'] = {}
    text = '''COMPONENTS 3 ;
- source NAND2 ;
- sink NAND2 ;
- newbuf BUF ;
END COMPONENTS
NETS 2 ;
- renamed ( source Y ) ( newbuf A ) ;
- split ( newbuf Y ) ( sink A ) ;
END NETS
PINS 0 ;
END PINS
'''
    return oracle.reconstruct(module, text, {'BUF': ('A', 'Y')}, {'renamed': 2., 'split': 3.})


def test_added_buffer_bridges_renamed_net(monkeypatch):
    aliases, values, bridges = setup(monkeypatch)
    assert aliases['signal'] == '1'
    assert values['1']['valid'] and values['1']['value'] == 5.
    assert values['1']['nets'] == ['renamed', 'split']
    assert len(bridges) == 1


def test_existing_buffer_is_not_a_backend_bridge(monkeypatch):
    _, _, bridges = setup(monkeypatch, original_buffer=True)
    assert not bridges


def test_component_with_two_logical_owners_is_invalid(monkeypatch):
    _, values, _ = setup(monkeypatch, conflicting=True)
    assert all(not v['valid'] and v['conflict'] for v in values.values())


def test_path_normalization():
    assert oracle.canonical(r'\a/b[0]') == 'a.b[0]'
