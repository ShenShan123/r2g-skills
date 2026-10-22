"""ann-v2 binding of chain-log-v1 rows (T2 design draft §8d, 2026-10-17).

A new row names its pre run and its post run, and the post run names the action
(backend/RUN_*/r2g_action.json, written by run_orfs at creation). The binder uses those
explicit links only: both directions must agree and be unique; a strategy-named run, a
nearer run or an E2-consistent run never substitutes. The verifier must agree.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'memory'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_ann_v2_fields as FX  # noqa: E402
import test_ann_v2_verify as TV  # noqa: E402

mkrun, T, why = FX.mkrun, FX.T, FX.why
REUSED = {s: 'RUN_A' for s in ('synth', 'floorplan', 'place', 'cts')}


def chain_case(p, *, action='s1#1', post_names=('RUN_B',), row_post='RUN_B', row_pre='RUN_A', pre_drc=12,
               post_drc=0, rolled_back=False, reason=True, extra_row=None):
    mkrun(p, 'RUN_A', drc=pre_drc, timing=T())
    for tag in post_names:
        d = mkrun(p, tag, [('route', 0), ('finish', 0)], drc=post_drc, timing=T(),
                  reason=why('antenna_diode_iters', 'route') if reason else None, from_stage='route',
                  reused=REUSED if reason else None)
        (d / 'r2g_action.json').write_text(json.dumps({'action_id': action, 'run_tag': tag}))
    u = FX.unit(p, before_categories={'li.3': {'count': pre_drc}}, rolled_back=rolled_back)
    log = p / 'reports' / 'fix_log.jsonl'
    rows = [json.loads(l) for l in log.read_text().splitlines()]
    rows[-1].update(chain_version=1, action_id=action, parent_action=None, pre_run_tag=row_pre,
                    post_run_tag=row_post, rollback_reason=None)
    if extra_row:
        rows.append(extra_row)
    log.write_text(''.join(json.dumps(r) + '\n' for r in rows))
    u['raw_row'] = rows[0]
    return u


def test_a_chain_row_binds_both_runs_from_its_explicit_links(tmp_path):
    p = tmp_path / 'p'
    r = FX.annotate(chain_case(p))
    assert r['binding']['post']['run'] == 'RUN_B' and r['binding']['pre']['run'] == 'RUN_A'
    assert r['binding']['post']['evidence']['e1']['chain_action_id'] == 's1#1'
    assert r['positive'] is True


def test_a_chain_row_binds_even_without_a_strategy_reason_record(tmp_path):
    """The engineer-loop retry is a full reflow with no resume_meta: the explicit link binds."""
    p = tmp_path / 'p'
    r = FX.annotate(chain_case(p, reason=False))
    assert r['binding']['post']['run'] == 'RUN_B'


@pytest.mark.parametrize('kw,reason', [
    (dict(row_post=None), 'no_explicit_link'),
    (dict(row_post='RUN_GONE'), 'run_missing_on_disk'),
    (dict(post_names=('RUN_B', 'RUN_C')), 'ambiguous_binding')])
def test_a_chain_row_without_a_unique_two_way_link_is_unknown(tmp_path, kw, reason):
    p = tmp_path / 'p'
    r = FX.annotate(chain_case(p, **kw))
    assert r['binding']['post']['status'] == 'UNKNOWN' and r['binding']['post']['unknown_reason'] == reason
    assert r['positive'] is False


def test_a_named_post_run_that_does_not_name_the_action_is_contradicted(tmp_path):
    p = tmp_path / 'p'
    mkrun(p, 'RUN_B', [('route', 0), ('finish', 0)], drc=0, timing=T())      # no r2g_action.json
    r = FX.annotate(chain_case(p, post_names=()))
    assert r['binding']['post']['unknown_reason'] == 'contradicted_binding' and r['positive'] is False


def test_a_second_row_with_the_same_action_id_is_ambiguous(tmp_path):
    p = tmp_path / 'p'
    dup = {'fix_session_id': 's9', 'iter': 1, 'strategy': 'x', 'check': 'drc', 'action_id': 's1#1'}
    r = FX.annotate(chain_case(p, extra_row=dup))
    assert r['binding']['post']['unknown_reason'] == 'ambiguous_binding'


def test_a_named_pre_run_without_the_failure_is_contradicted(tmp_path):
    p = tmp_path / 'p'
    r = FX.annotate(chain_case(p, pre_drc=0))
    assert r['binding']['pre']['status'] == 'UNKNOWN'
    assert r['binding']['pre']['unknown_reason'] == 'contradicted_binding'


def test_a_rolled_back_chain_step_is_bound_and_never_positive(tmp_path):
    p = tmp_path / 'p'
    r = FX.annotate(chain_case(p, rolled_back=True))
    assert r['binding']['post']['run'] == 'RUN_B' and r['rolled_back']['value'] is True
    assert r['positive'] is False and 'rolled_back' in r['positive_blockers']


def test_the_verifier_agrees_on_chain_rows_and_fails_a_tampered_link(tmp_path):
    p = tmp_path / 'p'
    u = chain_case(p)
    src, recs = TV.annotate_all(tmp_path, [u])
    rep = TV.run_verify(tmp_path, src, recs)
    assert rep['gate'] == 'PASS' and rep['rederived_positive'] == 1, rep['findings']
    (p / 'backend' / 'RUN_B' / 'r2g_action.json').write_text(json.dumps({'action_id': 'other#1'}))
    rep = TV.run_verify(tmp_path, src, recs)
    assert rep['gate'] == 'FAIL' and 'wrong_binding' in TV.kinds(rep)


def test_the_verifier_flags_duplicate_action_ids_and_dangling_parents(tmp_path):
    p = tmp_path / 'p'
    dangling = {'fix_session_id': 's1', 'iter': 2, 'strategy': 'none', 'check': 'drc', 'verdict': 'stop_clean',
                'action_id': 's1#2', 'parent_action': 's1#7'}
    u = chain_case(p, extra_row=dangling)
    src, recs = TV.annotate_all(tmp_path, [u])
    rep = TV.run_verify(tmp_path, src, recs)
    assert 'input_integrity' in TV.kinds(rep)


def test_dev_export_takes_extra_families_for_designs_outside_source_v1(tmp_path):
    """Learning-curve corpus designs get their lineage group as family (and so a known source
    group); a design SOURCE-v1 already maps keeps its family, and a conflict raises."""
    import json as _j
    from ann_v2 import dev_export as DX
    src = tmp_path / 'v1'
    src.mkdir()
    (src / 'source_export.jsonl').write_text('')
    (src / 'source_manifest.json').write_text(_j.dumps({'split': {'family_of_design': {'aes_core': 'secworks/aes'}}}))
    proj = tmp_path / 'camp' / 'C00053__x__y__TM0__L01' / 'reports'
    proj.mkdir(parents=True)
    (proj / 'fix_log.jsonl').write_text(_j.dumps({'fix_session_id': 's', 'iter': 1, 'strategy': 'a', 'check': 'drc',
                                                  'ts': '2026-10-18T00:00:00', 'situation': {'platform': 'sky130hd'}}) + '\n')
    man = DX.build(tmp_path, ['camp'], src, '', set(), set(), tmp_path / 'out', extra_families={'C00053': 'lc:lineage_x'})
    assert man['split']['family_of_design'] == {'C00053': 'lc:lineage_x'} and man['designs_without_family'] == []
    import pytest as _p
    with _p.raises(ValueError):
        DX.build(tmp_path, ['camp'], src, '', set(), set(), tmp_path / 'out2', extra_families={'aes_core': 'other'})
