import sys
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from probe_nangate45_drc_mode import flat_deck, contact_local_deck, contact_difference_deck, report, compare


def write_report(path, values=(), category='METAL1', cell='top', extra_category=True):
    root = ET.Element('report-database')
    ET.SubElement(root, 'top-cell').text = 'top'
    cats = ET.SubElement(root, 'categories')
    for name in ['METAL1'] + (['CONTACT3'] if extra_category else []):
        c = ET.SubElement(cats, 'category')
        ET.SubElement(c, 'name').text = name
        ET.SubElement(c, 'description').text = name + ' check'
    cells = ET.SubElement(root, 'cells')
    c = ET.SubElement(cells, 'cell')
    ET.SubElement(c, 'name').text = cell
    ET.SubElement(c, 'references')
    items = ET.SubElement(root, 'items')
    for value in values:
        it = ET.SubElement(items, 'item')
        ET.SubElement(it, 'category').text = category
        ET.SubElement(it, 'cell').text = cell
        ET.SubElement(it, 'multiplicity').text = '1'
        v = ET.SubElement(it, 'values')
        ET.SubElement(v, 'value').text = value
    path.write_bytes(ET.tostring(root))
    return path


def test_only_execution_mode_changed():
    ruby = 'tiles(1000.um)\ndeep\nthreads(4)\nFEOL = true\na.not(b).output("test")\n'
    root = ET.Element('klayout-macro')
    ET.SubElement(root, 'text').text = ruby
    trial = flat_deck(ET.tostring(root))
    assert ET.fromstring(trial).findtext('text') == ruby.replace('\ndeep\n', '\nflat\n')
    with pytest.raises(ValueError):
        flat_deck(trial)


def test_contact_local_keeps_deep_and_original_antenna_layers():
    expression = 'cont.not(active.or(poly.or(metal1)))'
    ruby = 'deep\n' + expression + '.output("CONTACT.3")\nconnect(gate, poly)\nantenna_check(gate, metal1, 300, diode)\n'
    root = ET.Element('klayout-macro')
    ET.SubElement(root, 'text').text = ruby
    transformed = ET.fromstring(contact_local_deck(ET.tostring(root))).findtext('text')
    assert transformed == ruby.replace(expression, 'cont.dup.flatten.not(active.dup.flatten.or(poly.dup.flatten.or(metal1.dup.flatten)))')
    with pytest.raises(ValueError):
        contact_local_deck(ET.tostring(ET.Element('klayout-macro')))


def test_difference_keeps_modes_and_all_other_code():
    ruby = 'deep\ncont.not(active.or(poly.or(metal1))).output("CONTACT.3")\nantenna_check(gate, metal1, 300, diode)\n'
    root = ET.Element('klayout-macro')
    ET.SubElement(root, 'text').text = ruby
    text = ET.fromstring(contact_difference_deck(ET.tostring(root))).findtext('text')
    assert text == ruby.replace('cont.not(active.or(poly.or(metal1)))', 'cont.not(active).not(poly).not(metal1)')


def test_exact_markers_match_independent_of_order(tmp_path):
    a = write_report(tmp_path/'a', ['polygon: (0,0;1,0;0,1)', 'float: 0.25'])
    b = write_report(tmp_path/'b', ['float: 0.25', 'polygon: (0,0;1,0;0,1)'])
    assert compare(a, b)['equivalent_on_this_case']
    assert report(a)['items'] == 2


def test_equal_counts_different_coordinates_rejected(tmp_path):
    a = write_report(tmp_path/'a', ['polygon: (0,0;1,0;0,1)'])
    b = write_report(tmp_path/'b', ['polygon: (2,2;3,2;2,3)'])
    result = compare(a, b)
    assert not result['equivalent_on_this_case']
    assert result['missing_markers'] == result['extra_markers'] == 1


def test_dropped_empty_rule_and_changed_numeric_value_rejected(tmp_path):
    a = write_report(tmp_path/'a')
    b = write_report(tmp_path/'b', extra_category=False)
    assert not compare(a, b)['equivalent_on_this_case']
    a = write_report(a, ['float: 0.25'])
    b = write_report(b, ['float: 0.26'])
    assert not compare(a, b)['equivalent_on_this_case']


def test_duplicate_marker_multiplicity_not_ignored(tmp_path):
    a = write_report(tmp_path/'a', ['polygon: (0,0;1,0;0,1)'])
    b = write_report(tmp_path/'b', ['polygon: (0,0;1,0;0,1)']*2)
    assert compare(a, b)['extra_markers'] == 1


def test_hierarchy_requires_explicit_transform(tmp_path):
    a = write_report(tmp_path/'a', ['polygon: (0,0;1,0;0,1)'], cell='subcell')
    with pytest.raises(ValueError, match='Hierarchical'):
        report(a)
