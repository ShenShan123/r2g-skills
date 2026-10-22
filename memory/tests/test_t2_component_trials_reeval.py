"""Re-evaluation of the component trials under failure-patterns #61: an aborted run's carried-over
finish report never yields an area PASS; a finished run's report does."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'memory' / 'scripts'))
import t2_component_trials_reeval as RV  # noqa: E402
import t2a_envelope as E  # noqa: E402

MAN = {'area': {k: {'ref': 100.0, 'max_rel_increase': c} for k, c in (('stdcell', 0.55), ('core', 1.0), ('die', 1.0))}}
ORIG = {'status': 'PASS', 'config': {'ok': True, 'violations': []}}


def run(tmp_path, last, areas):
    d = tmp_path / last[0]
    (d / 'logs').mkdir(parents=True)
    stages = ['floorplan', 'place', 'cts', 'route', 'finish']
    rows = [(s, 0) for s in stages[:stages.index(last[0])]] + [last]
    (d / 'stage_log.jsonl').write_text(''.join(json.dumps({'stage': s, 'status': st}) + '\n' for s, st in rows))
    (d / 'logs' / '6_report.json').write_text(json.dumps({E.AREA_KEYS[k]: v for k, v in areas.items()}))
    return d


def test_a_carried_over_report_turns_a_pass_into_unverified(tmp_path):
    d = run(tmp_path, ('route', 2), {'stdcell': 110, 'core': 150, 'die': 150})
    assert RV.corrected(ORIG, MAN, d)['status'] == 'UNVERIFIED'


def test_a_finished_run_keeps_its_verdict_and_caps_still_bind(tmp_path):
    ok = run(tmp_path / 'a', ('finish', 0), {'stdcell': 110, 'core': 150, 'die': 150})
    assert RV.corrected(ORIG, MAN, ok)['status'] == 'PASS'
    big = run(tmp_path / 'b', ('finish', 0), {'stdcell': 160, 'core': 150, 'die': 150})
    assert RV.corrected(ORIG, MAN, big)['status'] == 'FAIL'


def test_an_unresolved_config_stays_unverified(tmp_path):
    d = run(tmp_path, ('finish', 0), {'stdcell': 110, 'core': 150, 'die': 150})
    out = RV.corrected({'status': 'UNVERIFIED', 'reason': 'execution_config_binding_missing_or_stale'}, MAN, d)
    assert out['status'] == 'UNVERIFIED'


def test_a_router_knob_ignored_by_the_platform_script_is_a_tool_no_op(tmp_path):
    plat = tmp_path / 'fastroute.tcl'
    plat.write_text('set_global_routing_layer_adjustment $::env(MIN_ROUTING_LAYER)-$::env(MAX_ROUTING_LAYER) 0.2\n')
    d = tmp_path / 'RUN_t'
    (d / 'logs').mkdir(parents=True)
    (d / 'logs' / '2_1_floorplan.log').write_text(f'read_lef x\nsource {plat}\n')
    assert RV.tool_consumed({'ROUTING_LAYER_ADJUSTMENT': '0.1'}, d)['ROUTING_LAYER_ADJUSTMENT']['status'] == 'TOOL_NO_OP'
    plat.write_text('set_global_routing_layer_adjustment m1-m5 $::env(ROUTING_LAYER_ADJUSTMENT)\n')
    assert RV.tool_consumed({'ROUTING_LAYER_ADJUSTMENT': '0.1'}, d)['ROUTING_LAYER_ADJUSTMENT']['status'] == 'CONSUMED'
    (d / 'logs' / '2_1_floorplan.log').write_text('read_lef x\n')
    assert RV.tool_consumed({'ROUTING_LAYER_ADJUSTMENT': '0.1'}, d)['ROUTING_LAYER_ADJUSTMENT']['status'] == 'CONSUMED'
    assert RV.tool_consumed({'CORE_UTILIZATION': '52'}, d) == {}


def test_trial_overrides_class_tool_no_ops_and_strip_success_without_acceptance(tmp_path):
    src = tmp_path / 'source_export.jsonl'
    rows = [('a', 'P-util'), ('b', 'P-rla'), ('c', 'P-ok'), ('d', 'P-area')]
    src.write_text(''.join(json.dumps({'unit_id': u, 'raw_row': {'fix_session_id': 'ctrial-' + s}}) + '\n' for u, s in rows))
    T = lambda step, **kw: {'project': 'P', 'step': step, 'applied_at_tool': kw.get('tool', 'APPLIED'),
                            'congestion': kw.get('cong', 'CLEARED'), 'acceptance': kw.get('acc', 'ACCEPTED'),
                            'corrected': {'envelope': kw.get('env', 'PASS')}, 'tool_consumed': {}}
    rv = {'trials': [{'project': 'P', 'step': 'baseline'}, T('util', acc='NOT_ACCEPTED'),
                     T('rla', tool='TOOL_NO_OP', cong='NO_CHANGE', acc='UNKNOWN'), T('ok'), T('area', env='FAIL')]}
    ov = RV.trial_overrides(rv, src)
    assert ov['a']['admitted'] is False and ov['a']['outcome_class'] == 'cleared_new_failure'
    assert ov['b']['outcome_class'] == 'tool_no_op' and ov['b']['admitted'] is False and 'exclude' not in ov['b']
    assert 'c' not in ov and ov['d']['admitted'] is False
