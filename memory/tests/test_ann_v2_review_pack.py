"""The review companion quotes original files; it never signs the human audit."""
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import ann_v2_review_pack as R


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False) + '\n')
    return path


def fixture(tmp_path, raw_rollback=None):
    row = {'strategy': 'density_relief', 'check': 'drc', 'iter': 1,
           'fix_session_id': 'session', 'before': '88', 'after': '0'}
    if raw_rollback is not None:
        row['rolled_back'] = raw_rollback
    log = write(tmp_path / 'project/reports/fix_log.jsonl', row)
    ref = {'path': str(log), 'locator': 'line 1: rolled_back',
           'sha256': hashlib.sha256(log.read_bytes()).hexdigest()}
    field = {'status': 'KNOWN', 'value': False, 'source': [ref]}
    binding = {'status': 'UNKNOWN', 'run': None, 'unknown_reason': 'no_explicit_link', 'candidates': []}
    record = {'unit_id': 'u1', 'status': 'ATTEMPT', 'row_kind': 'drc/li.3',
              'binding': {'pre': binding, 'post': binding}, 'identity': {},
              'targets': [], 'step_effect': {'drc': 'CLEARED'},
              'new_failures': {'lvs': 'NOT_MEASURED_BEFORE'}, 'rolled_back': field,
              'positive': True, 'positive_blockers': []}
    unit = {'unit_id': 'u1', 'raw_row': {**row, 'rolled_back': False},
            'identity': {'design': 'example'},
            'provenance': {'sources': [{'path': str(log), 'line': 1}]},
            'rollback': {'recovery_applied': {'kind': 'restoration_review', 'scope': 'step only'}}}
    ann = write(tmp_path / 'ann.jsonl', record)
    source = write(tmp_path / 'source.jsonl', unit)
    sample = write(tmp_path / 'sample.json', {'seed': 20261017,
                    'by_stratum': {'drc|CLEARED|positive': ['u1']},
                    'stratum_sizes': {'drc|CLEARED|positive': 1}})
    return ann, source, sample, log


def test_missing_original_rollback_is_not_filled_from_enriched_export(tmp_path):
    ann, source, sample, log = fixture(tmp_path)
    result = R.build_package(ann, source, sample, tmp_path / 'review')
    card = next((tmp_path / 'review/cards').glob('*.md')).read_text()
    assert '原始日志未记录 rolled_back' in card
    assert '导出记录中的值' in card and 'restoration_review' in card
    assert result['units_with_findings'] == 1
    assert 'HUMAN_VERDICT: 未填写' in card
    assert 'HUMAN_VERDICT: PASS' not in card
    assert json.loads(log.read_text()).get('rolled_back') is None


def test_actual_false_is_shown_as_literal_original_evidence(tmp_path):
    ann, source, sample, _ = fixture(tmp_path, False)
    result = R.build_package(ann, source, sample, tmp_path / 'review')
    card = next((tmp_path / 'review/cards').glob('*.md')).read_text()
    assert '原始日志未记录 rolled_back' not in card
    assert '"rolled_back": false' in card
    assert result['units_with_findings'] == 0
    assert '并不表示审阅通过' in (tmp_path / 'review/README.md').read_text()


def test_existing_review_is_never_overwritten(tmp_path):
    ann, source, sample, _ = fixture(tmp_path)
    out = tmp_path / 'review'
    R.build_package(ann, source, sample, out)
    index = out / 'README.md'
    index.write_text('human notes stay here')
    with pytest.raises(FileExistsError):
        R.build_package(ann, source, sample, out)
    assert index.read_text() == 'human notes stay here'


def test_inputs_unchanged_and_output_deterministic(tmp_path):
    ann, source, sample, log = fixture(tmp_path)
    old = {p: p.read_bytes() for p in (ann, source, sample, log)}
    R.build_package(ann, source, sample, tmp_path / 'one')
    R.build_package(ann, source, sample, tmp_path / 'two')
    for path, value in old.items():
        assert path.read_bytes() == value
    for p in (tmp_path / 'one').rglob('*'):
        if p.is_file():
            assert p.read_bytes() == (tmp_path / 'two' / p.relative_to(tmp_path / 'one')).read_bytes()


def test_hash_drift_is_reported_separately_from_field_value(tmp_path):
    path = write(tmp_path / 'metric.json', {'violations': 0})
    result = R.extract_reference({'path': str(path), 'locator': 'violations', 'sha256': '0' * 64}, R.Sources())
    assert result['value'] == 0 and result['hash_status'] == 'MISMATCH'


def test_missing_or_unreadable_source_not_presented_as_false_or_zero(tmp_path):
    path = write(tmp_path / 'row.jsonl', {'strategy': 'x'})
    result = R.extract_reference({'path': str(path), 'locator': 'line 1: rolled_back'}, R.Sources())
    assert result['state'] == 'MISSING_FIELD' and result['value'] is None
    missing = R.extract_reference({'path': str(tmp_path / 'absent'), 'locator': 'violations'}, R.Sources())
    assert missing['state'] == 'UNREADABLE'


def test_sample_membership_must_match_and_cannot_silently_drop_a_unit(tmp_path):
    ann, source, sample, _ = fixture(tmp_path)
    d = json.loads(sample.read_text()); d['by_stratum']['drc|CLEARED|positive'].append('missing')
    write(sample, d)
    with pytest.raises(ValueError, match='missing'):
        R.build_package(ann, source, sample, tmp_path / 'review')
    assert not (tmp_path / 'review').exists()


def test_derived_rule_class_timeout_and_metric_are_not_false_mismatches(tmp_path):
    log = write(tmp_path / 'fix_log.jsonl', {'before_categories': '{"m3.2":{"count":88}}'})
    stages = write(tmp_path / 'stage_log.jsonl', {'stage': 'route', 'status': 2})
    metrics = write(tmp_path / '6_report.json', {'finish__timing__setup__ws': -0.1})
    def f(value, path, locator):
        return {'status': 'KNOWN', 'value': value,
                'source': [{'path': str(path), 'locator': locator}]}
    record = {'rolled_back': {'status': 'UNKNOWN', 'value': None},
              'targets': [{'type': 'drc', 'fields': {'rule_class': f('m3.2', log, 'line 1: before_categories')}},
                          {'type': 'stage_abort', 'fields': {'timeout': f(False, stages, 'line 1: status')}},
                          {'type': 'setup', 'fields': {'metric': f('setup_ws', metrics, 'finish__timing__setup__ws')}}]}
    evidence, findings = R.field_evidence(record, R.Sources())
    assert findings == []
    assert sum(bool(ex.get('derivation')) for _, _, refs in evidence for ex in refs) == 3
