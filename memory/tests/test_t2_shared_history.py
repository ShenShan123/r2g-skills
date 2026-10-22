"""Shared history package: one unit set and one label set for every arm; source isolation by
cluster; the export's enriched rollback/outcome never reaches any arm (text or TEHM import)."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'memory' / 'scripts'))
sys.path.insert(0, str(ROOT / 'memory'))
import t2_shared_history as SH  # noqa: E402

SPLIT = {'learn': ['opencores/des'], 'verify': ['secworks/chacha'], 'verify_source_groups': ['secworks']}


def rec(uid, fam, grp, admitted=False, rb=(None, None), status='ATTEMPT'):
    return {'unit_id': uid, 'status': status,
            'identity': {'ip_family': {'value': fam}, 'source_group': {'value': grp}},
            'binding': {'post': {'status': 'KNOWN'}, 'pre': {'status': 'KNOWN'}},
            'admission': {'status': 'ADMITTED' if admitted else 'NOT_ADMITTED'},
            'step_effect': {'stage_abort': 'CLEARED'}, 'new_failures': {'stage': 'KNOWN-none'},
            'rolled_back': {'status': rb[0], 'value': rb[1]},
            'targets': [{'type': 'stage_abort', 'type_status': 'KNOWN',
                         'fields': {'stage': {'value': 'place'}, 'error_code': {'value': 'FLW-0024'}}}]}


def unit(uid, design, project, enriched_rb=False, session='s1', it=1):
    return {'unit_id': uid, 'identity': {'design': design},
            'raw_row': {'fix_session_id': session, 'iter': it, 'strategy': 'core_util_relief', 'check': 'orfs_stage',
                        'violation_class': 'place', 'from_stage': 'place', 'rolled_back': enriched_rb,
                        'config_delta': json.dumps({'CORE_UTILIZATION': {'before': '89', 'after': '53'}}),
                        'situation': {'platform': 'sky130hd'}, 'ts': f'2026-10-17T0{it}:00:00'},
            'provenance': {'sources': [{'path': f'/x/{project}/reports/fix_log.jsonl', 'line': it}]},
            'text_view': 'old text ... Canonical outcome: PASS. Rolled back: false.'}


def files(tmp_path, units, recs):
    s, l = tmp_path / 'src.jsonl', tmp_path / 'lab.jsonl'
    s.write_text(''.join(json.dumps(u) + '\n' for u in units))
    l.write_text(''.join(json.dumps(r) + '\n' for r in recs))
    return [s], [l]


def test_dev_view_keeps_learning_clusters_only_and_t2_keeps_all(tmp_path):
    src, lab = files(tmp_path, [unit('a', 'des_area', 'des_area__u77'), unit('b', 'chacha_core', 'chacha_core__u72'),
                                unit('c', 'x', 'x__u1')],
                     [rec('a', 'opencores/des', 'opencores/des'), rec('b', 'secworks/chacha', 'secworks'),
                      rec('c', None, None)])
    dev, man = SH.build(src, lab, SPLIT, 'dev_verification')
    assert [u['unit_id'] for u in dev] == ['a'] and man['dropped'] == {'verify_cluster_or_unknown_identity': 2}
    assert len(SH.build(src, lab, SPLIT, 't2')[0]) == 3


def test_success_and_text_come_from_the_shared_label_only(tmp_path):
    src, lab = files(tmp_path, [unit('a', 'des_area', 'des_area__u77', enriched_rb=False)],
                     [rec('a', 'opencores/des', 'opencores/des', admitted=False, rb=(None, None))])
    u = SH.build(src, lab, SPLIT, 't2')[0][0]
    assert u['shared_label']['admitted'] is False
    assert 'Canonical outcome' not in u['text_view'] and 'success no' in u['text_view']
    assert u['text_view_v1'].startswith('old text')
    assert u['shared_label']['edits'] == {'CORE_UTILIZATION': '53'}


def test_the_tehm_import_view_carries_only_the_verified_rollback(tmp_path):
    src, lab = files(tmp_path, [unit('a', 'des_area', 'des_area__u77', enriched_rb=False),
                                unit('b', 'des_area', 'des_area__u77', enriched_rb=False, it=2)],
                     [rec('a', 'opencores/des', 'opencores/des', rb=(None, None)),
                      rec('b', 'opencores/des', 'opencores/des', admitted=True, rb=('KNOWN', False))])
    a, b = SH.tehm_view(SH.build(src, lab, SPLIT, 't2')[0])
    assert 'rolled_back' not in a['raw_row'] and b['raw_row']['rolled_back'] is False


def test_units_are_normalised_to_the_schema_every_arm_reads(tmp_path):
    src, lab = files(tmp_path, [unit('a', 'des_area', 'des_area__u77', it=1), unit('b', 'des_area', 'des_area__u77', it=2)],
                     [rec('a', 'opencores/des', 'opencores/des'), rec('b', 'opencores/des', 'opencores/des')])
    a, b = SH.build(src, lab, SPLIT, 't2')[0]
    assert a['lineage']['project'] == 'des_area__u77' and a['identity']['session'] == 's1'
    assert (a['capture_context']['step_index'], b['capture_context']['step_index']) == (0, 1)
    assert b['capture_context']['is_terminal'] is True and a['source_eligible'] is True


def test_an_override_may_remove_success_but_never_grant_it():
    import t2_shared_history as SH
    lab = {"admitted": True, "outcome_class": "admitted_positive"}
    out = SH.apply_override(lab, {"admitted": False, "outcome_class": "cleared_new_failure", "reason": "DRC 80"})
    assert out["admitted"] is False and out["outcome_class"] == "cleared_new_failure" and out["override"]["reason"]
    assert lab["admitted"] is True                                   # input untouched
