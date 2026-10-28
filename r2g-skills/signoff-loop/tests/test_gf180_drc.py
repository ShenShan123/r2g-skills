"""Incomplete GF180 reports and changed rule sources must never pass signoff."""
import json
from pathlib import Path

import pytest
import gf180_drc as drc


def report(path, count=0):
    path.write_text('<report-database><categories/><cells/><items>' +
                    '<item/>' * count + '</items></report-database>')


def test_partial_official_reports_are_rejected(tmp_path):
    rules = tmp_path / 'rules'; rules.mkdir()
    out = tmp_path / 'out'; out.mkdir()
    for name in ('main', 'layers_def', 'tail', 'density', 'antenna', 'metal'):
        (rules / (name + '.drc')).touch()
    report(out / 'top_metal.lyrdb')
    with pytest.raises(ValueError, match='incomplete'):
        drc.merge_official(rules, out, 'top', tmp_path / 'merged.lyrdb')
    report(out / 'top_antenna.lyrdb', 2)
    merged = tmp_path / 'merged.lyrdb'
    assert drc.merge_official(rules, out, 'top', merged) == 2
    assert drc.report_items(merged) == 2
    report(out / 'unexpected.lyrdb')
    with pytest.raises(ValueError, match='unexpected'):
        drc.merge_official(rules, out, 'top', merged)


@pytest.mark.parametrize('xml', ['<report-database/>', '<other><items/></other>'])
def test_missing_items_is_not_clean(tmp_path, xml):
    p = tmp_path / 'report.lyrdb'; p.write_text(xml)
    with pytest.raises(ValueError):
        drc.report_items(p)


def test_altered_rule_set_is_rejected(tmp_path):
    manifest = json.loads((Path(drc.__file__).parent / 'platforms/gf180/drc_tables.json').read_text())
    for name in [*manifest['tables'], 'main.drc', 'tail.drc', 'layers_def.drc']:
        (tmp_path / name).write_text('Executing rule\n' * 700)
    with pytest.raises(ValueError, match='differs from the reviewed baseline'):
        drc.reviewed_rules(tmp_path)
