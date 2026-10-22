"""Every arm reads the SAME success definition from the shared history package: the shared
label (ann-v2 admission), never the export's enriched rollback/outcome fields."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'memory' / 'scripts'))
import t2a_adapters as A  # noqa: E402


def unit(uid, admitted, enriched_positive=True, edits=None, row_delta='{}'):
    return {'unit_id': uid, 'source_eligible': True,
            'outcome': {'verified': True, 'canonical': 'PASS' if enriched_positive else 'FAIL'},
            'rollback': {'rolled_back': 'UNKNOWN', 'recovery_applied': {'status_for_main': 'false'}},
            'action': {'config_delta': row_delta, 'from_stage': 'floorplan'},
            'raw_row': {'situation': {'check': 'orfs_stage', 'violation_class': 'place', 'error_code': 'FLW-0024'}},
            'identity': {'ts': '2026-10-17T00:00:00'},
            'shared_label': {'version': 'shared-history-v2-labels', 'admitted': admitted, 'edits': edits or {}}}


def test_the_shared_label_is_the_only_success_signal():
    assert A.positive(unit('a', admitted=False, enriched_positive=True)) is False
    assert A.positive(unit('b', admitted=True, enriched_positive=False)) is True


def test_legacy_units_keep_the_legacy_definition():
    u = unit('c', admitted=True)
    del u['shared_label']
    assert A.positive(u) is True                     # unchanged behaviour without a package


def test_recipes_from_the_package_use_the_rows_effective_edit():
    # an engine-loop recorder row stores {before, after}; the recipe must carry the after-value
    u = unit('d', admitted=True, edits={'CORE_UTILIZATION': '53'},
             row_delta='{"CORE_UTILIZATION": {"before": "89", "after": "53"}}')
    recipes = A.build_recipes([u, unit('e', admitted=False, edits={'CORE_UTILIZATION': '40'})])
    assert [r['config_edits'] for r in recipes] == [{'CORE_UTILIZATION': '53'}]


def test_load_shared_checks_the_manifest_hash(tmp_path):
    units = tmp_path / 'shared_units.jsonl'
    units.write_text(json.dumps(unit('f', True)) + '\n')
    import hashlib
    man = tmp_path / 'manifest.json'
    man.write_text(json.dumps({'units_sha256': hashlib.sha256(units.read_bytes()).hexdigest()}))
    assert [u['unit_id'] for u in A.load_shared(tmp_path)] == ['f']
    man.write_text(json.dumps({'units_sha256': '0' * 64}))
    try:
        A.load_shared(tmp_path)
        raise AssertionError('hash mismatch must fail')
    except ValueError:
        pass
