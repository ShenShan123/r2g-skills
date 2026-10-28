import json
import os
import shutil
import xml.etree.ElementTree as ET

from r2g_agent import stage_views as v


DEF = '''VERSION 5.8 ;
UNITS DISTANCE MICRONS 1000 ;
DIEAREA ( 0 0 ) ( 10000 10000 ) ;
COMPONENTS 2 ;
- c1 INV_X1 + PLACED ( 1000 2000 ) N ;
- c2 INV_X1 + PLACED ( 5000 2000 ) N ;
END COMPONENTS
NETS 1 ;
- n1 ( c1 Z ) ( c2 A ) + ROUTED metal1 ( 1000 2000 ) ( 5000 * )
 NEW metal2 ( 5000 2000 ) ( * 4000 ) ;
END NETS
END DESIGN
'''


def project(tmp_path):
    p = tmp_path/'project'
    run = p/'backend/RUN_001/results'; run.mkdir(parents=True)
    (run/'6_final.def').write_text(DEF)
    stat = run.parent/'reports_orfs/synth_stat.txt'; stat.parent.mkdir()
    stat.write_text('    2  4.0  2  4.0   INV_X1\n    1  2.0  1  2.0   BUF_X1\n')
    return p, run


def test_def_geometry_and_synthesis_are_real_evidence():
    data, metrics = v.render_def(DEF)
    ET.fromstring(data)
    assert metrics == {'placed_cells': 2, 'route_segments': 2, 'sampled': False}
    assert b'M224.00,382.00L400.00,382.00' in data
    assert b'M400.00,382.00L400.00,294.00' in data
    data, metrics = v.render_synth('    2  4.0  2  4.0   INV_X1\n')
    assert b'INV_X1' in data and metrics['cell_types'] == 1
    ET.fromstring(data)


def test_pre_repair_snapshot_survives_rerun_and_slimming(tmp_path):
    p, run = project(tmp_path)
    v.capture(p, 'flow', {'signoff': {'drc': 'violations'}})
    before = v.read_json(p/'reports/stage_previews/index.json')['route']
    (run/'6_final.def').write_text(DEF.replace('5000', '7000'))
    v.capture(p, 'repair', {'signoff': {'drc': 'clean'}})
    v.capture(p, 'graph', {})
    after = v.read_json(p/'reports/stage_previews/index.json')['repair']
    assert before['source_sha256'] != after['source_sha256']
    shutil.rmtree(run)
    rows = v.views(p, {'stage': 'done', 'status': 'passed', 'detail': {'repair': [{}]}})
    signoff = next(r for r in rows if r['stage']=='route')
    assert signoff['signoff']['drc']=='violations'
    assert v.image(p, signoff)[0] != v.image(p, next(r for r in rows if r['stage']=='repair'))[0]
    assert not any(r['stage'] in ('signoff','graph','verify') for r in rows)


def test_legacy_final_image_is_not_fabricated_pre_repair_snapshot(tmp_path):
    p, run = project(tmp_path)
    (p/'reports').mkdir(); (p/'reports/layout.png').write_bytes(b'final')
    rows = v.views(p, {'stage':'done', 'status':'passed', 'detail':{'repair':[{}]}})
    assert not next(r for r in rows if r['stage']=='floorplan')['available']
    assert next(r for r in rows if r['stage']=='route')['available']
    assert not any(r['stage'] == 'repair' for r in rows)


def test_pending_and_skipped_stages_are_explicit(tmp_path):
    p, run = project(tmp_path)
    rows = v.views(p, {'stage':'synth','status':'running','detail':{}})
    assert all(not r['available'] for r in rows[1:])
    v.capture(p,'flow',{})
    rows = v.views(p, {'stage':'graph','status':'running','detail':{}})
    assert len(rows) == 6
    assert not any(r['stage']=='repair' for r in rows)


def test_each_physical_stage_uses_exact_odb_and_survives_cleanup(tmp_path, monkeypatch):
    p, run = project(tmp_path)
    for i, stem in enumerate(('2_floorplan','3_place','4_cts')):
        (run/(stem+'.odb')).write_bytes(str(i).encode())
    calls=[]
    def exported(path):
        calls.append(Path(path).stem)
        return DEF.replace('5000', str(6000+int(Path(path).read_text())*1000))
    from pathlib import Path
    monkeypatch.setattr(v, 'odb_text', exported)
    v.capture(p,'flow',{})
    assert set(calls)=={'2_floorplan','3_place','4_cts'}
    shutil.rmtree(run)
    rows=v.views(p,{'stage':'done','status':'passed','detail':{}})
    assert all(row['available'] for row in rows)
    assert len({v.image(p,r)[0] for r in rows})==5


def test_missing_intermediate_is_not_replaced_by_final_layout(tmp_path):
    p,run=project(tmp_path)
    rows=v.views(p,{'stage':'done','status':'passed','detail':{}})
    assert [r['stage'] for r in rows if r['available']]==['synth','route','final']
    assert all(not r['available'] for r in rows[1:4])


def test_stage_completion_hook_publishes_live_and_keeps_pre_repair_geometry(tmp_path, monkeypatch):
    p,run=project(tmp_path)
    flow=tmp_path/'flow'
    results=flow/'results/nangate45/test'/p.name;results.mkdir(parents=True)
    monkeypatch.setattr('r2g_agent.housekeeping.orfs_flow_dir',lambda:flow)
    (results/'2_floorplan.def').write_text(DEF)
    v.capture_completed_stage(p,run.parent,results,'floorplan','flow')
    rows=v.views(p,{'stage':'flow','status':'running','detail':{}})
    assert next(r for r in rows if r['stage']=='floorplan')['available']
    (results/'6_final.def').write_text(DEF)
    v.capture_completed_stage(p,run.parent,results,'finish','flow')
    before=v.read_json(p/'reports/stage_previews/index.json')['route']['source_sha256']
    stat=(results/'6_final.def').stat()
    (results/'6_final.def').write_text(DEF.replace('5000','8000'))
    os.utime(results/'6_final.def', ns=(stat.st_atime_ns, stat.st_mtime_ns))
    v.capture_completed_stage(p,run.parent,results,'finish','repair')
    saved=v.read_json(p/'reports/stage_previews/index.json')
    assert saved['route']['source_sha256']==before
    assert saved['repair']['source_sha256']!=before


def test_final_repair_is_separate_even_when_geometry_does_not_change(tmp_path):
    p, run = project(tmp_path)
    v.capture(p, 'flow', {})
    v.capture(p, 'repair', {'signoff': {'dual_clean': True}})
    shutil.rmtree(run)
    rows = v.views(p, {'stage': 'done', 'status': 'passed',
                       'detail': {'repair': [], 'signoff': {'dual_clean': True}}})
    assert not any(row['stage'] == 'repair' for row in rows)
    assert rows[-1]['stage'] == 'final' and rows[-1]['available']
    assert rows[-1]['label'] == '修复后的最终版图'
    assert rows[-1]['signoff_status'] == 'clean'
    assert v.image(p, rows[-1])[0] == v.image(p, next(r for r in rows if r['stage'] == 'route'))[0]


def test_final_prefers_repair_over_preserved_route_and_marks_failed_signoff(tmp_path):
    p, run = project(tmp_path)
    v.capture(p, 'flow', {})
    (run/'6_final.def').write_text(DEF.replace('5000', '8000'))
    v.capture(p, 'repair', {'signoff': {'dual_clean': False}})
    rows = v.views(p, {'stage': 'repair', 'status': 'failed',
                       'detail': {'repair': [{}], 'signoff': {'dual_clean': False}}})
    final = rows[-1]
    assert final['available'] and final['repair_attempted']
    assert final['signoff_status'] == 'failed' and '签核未通过' in final['caption']
    assert v.image(p, final)[0] != v.image(p, next(r for r in rows if r['stage'] == 'route'))[0]


def test_running_repair_does_not_relabel_initial_route_or_png(tmp_path):
    p, run = project(tmp_path)
    v.capture(p, 'flow', {})
    (p/'reports/layout.png').write_bytes(b'old final')
    rows = v.views(p, {'stage': 'repair', 'status': 'running',
                       'detail': {'signoff': {'dual_clean': False}}})
    assert not rows[-1]['available'] and rows[-1]['signoff_status'] == 'pending'
    # A new failed attempt must not silently show an earlier successful repair.
    v.capture(p, 'repair', {'signoff': {'dual_clean': True}})
    (p/'backend/RUN_002/results').mkdir(parents=True)
    rows = v.views(p, {'stage': 'repair', 'status': 'cancelled',
                       'detail': {'repair': [{}], 'signoff': {'dual_clean': True}}})
    assert not rows[-1]['available'] and rows[-1]['signoff_status'] == 'interrupted'


def test_legacy_png_only_final_is_available_after_success(tmp_path):
    p, run = project(tmp_path)
    shutil.rmtree(p/'backend')
    (p/'reports').mkdir()
    (p/'reports/layout.png').write_bytes(b'legacy final')
    final = v.views(p, {'stage': 'done', 'status': 'passed',
                        'detail': {'repair': [{}], 'signoff': {'dual_clean': True}}})[-1]
    assert final['available'] and final['source'] == 'reports/layout.png'
    assert v.image(p, final) == (b'legacy final', 'image/png')


def test_final_does_not_replace_missing_routed_result_with_placement(tmp_path):
    p, run = project(tmp_path)
    (run/'6_final.def').rename(run/'3_place.def')
    final = v.views(p, {'stage': 'flow', 'status': 'failed', 'detail': {}})[-1]
    assert final['stage'] == 'final' and not final['available']


def test_final_pending_or_cancelled_never_carries_previous_signoff(tmp_path):
    p, run = project(tmp_path)
    old = {'dual_clean': True, 'drc': 'clean', 'lvs': 'clean'}
    v.capture(p, 'repair', {'signoff': old})
    for status, expected in (('running', 'pending'), ('cancelled', 'interrupted'), ('failed', 'interrupted')):
        final = v.views(p, {'stage': 'repair', 'status': status,
                            'detail': {'repair': [{}], 'signoff': old}})[-1]
        assert final['available'] and final['signoff_status'] == expected
        assert 'signoff' not in final
        assert '双清' not in final['caption']


def test_restarted_flow_and_signoff_do_not_reuse_old_clean_verdict(tmp_path):
    p, run = project(tmp_path)
    old = {'dual_clean': True, 'drc': 'clean', 'lvs': 'clean'}
    v.capture(p, 'flow', {'signoff': old})
    for stage in ('flow', 'signoff'):
        final = v.views(p, {'stage': stage, 'status': 'running',
                            'detail': {'restarts': 1, 'signoff': old}})[-1]
        assert final['available'] and final['signoff_status'] == 'pending'
        assert 'signoff' not in final and '双清' not in final['caption']


def test_api_images_are_scoped_and_do_not_run_eda(monkeypatch, r2g_home, tmp_path):
    from fastapi.testclient import TestClient
    from r2g_agent import server, config
    server.STATE.clear();server.init_runtime(config.Config(ingest=False))
    p, run = project(tmp_path)
    store=server.tools.RT.store
    jid=store.create_job('views',{},[{'name':'test'}])
    store.set_design(jid,'test',project=str(p),status='passed',stage='done')
    c=TestClient(server.app)
    url=f'/api/jobs/{jid}/designs/test/stage-views'
    assert c.get(url).json()['stages'][0]['available']
    response=c.get(url+'/synth')
    assert response.status_code==200 and response.headers['content-type'].startswith('image/svg+xml')
    assert c.get(url+'/unknown').status_code==404
    assert c.get(url+'/repair').status_code==404
    assert c.get(url+'?version=final').json()['stages'][-1]['stage']=='final'
    assert c.get(url+'/synth?version=final').status_code==200
    assert c.get(url+'?version=unknown').status_code==400
    assert c.get(url+'/synth?version=unknown').status_code==400
    response=c.get(url+'/final')
    assert response.status_code==200 and response.headers['content-type'].startswith('image/svg+xml')
    (run/'6_final.def').unlink()
    assert c.get(url+'/final').status_code==404
    outside=tmp_path/'outside';outside.write_text('private')
    (p/'reports/stage_previews').mkdir(parents=True)
    (p/'reports/stage_previews/index.json').write_text(json.dumps({'synth':{'image':'../outside'}}))
    assert v.safe_file(p,'../outside') is None
    server.tools.RT.pipeline.pool.shutdown()


def test_final_version_does_not_mix_initial_run_stages(tmp_path):
    p, old = project(tmp_path)
    for stem in ('2_floorplan', '3_place', '4_cts'):
        (old/(stem+'.def')).write_text(DEF)
    v.capture(p, 'flow', {})
    new = p/'backend/RUN_002/results'; new.mkdir(parents=True)
    (new/'6_final.def').write_text(DEF.replace('5000', '8000'))
    (p/'images').mkdir(); (p/'images/3_place.def').write_text(DEF)
    design = {'stage':'done', 'status':'passed', 'detail':{'repair':[{}], 'signoff':{'dual_clean':True}}}
    rows = v.views(p, design, version='final')
    assert [row['stage'] for row in rows] == ['synth','floorplan','placement','cts','route','final']
    assert all(row['run']=='RUN_002' for row in rows)
    assert all(not row['available'] for row in rows[:4])
    assert all(row['available'] for row in rows[4:])
    assert next(row for row in v.views(p, design) if row['stage']=='floorplan')['available']


def test_repair_hook_preserves_each_run_stage_after_cleanup(tmp_path, monkeypatch):
    p, old = project(tmp_path)
    (old/'2_floorplan.def').write_text(DEF)
    v.capture(p, 'flow', {})
    initial = v.read_json(p/'reports/stage_previews/index.json')['floorplan']['source_sha256']
    new = p/'backend/RUN_002'; new.mkdir()
    flow = tmp_path/'flow'
    results = flow/'results/nangate45/test'/p.name; results.mkdir(parents=True)
    reports = flow/'reports/nangate45/test'/p.name; reports.mkdir(parents=True)
    (reports/'synth_stat.txt').write_text('    3  6.0  3  6.0   INV_X1\n')
    monkeypatch.setattr('r2g_agent.housekeeping.orfs_flow_dir', lambda:flow)
    v.capture_completed_stage(p, new, results, 'synth', 'repair')
    for index, (flow_stage, stem) in enumerate((('floorplan','2_floorplan'), ('place','3_place'), ('cts','4_cts'), ('route','5_route'), ('finish','6_final'))):
        (results/(stem+'.def')).write_text(DEF.replace('5000', str(6000+index*500)))
        v.capture_completed_stage(p, new, results, flow_stage, 'repair')
    saved = v.read_json(p/'reports/stage_previews/index.json')
    assert saved['floorplan']['source_sha256']==initial
    manifest = v.read_json(p/'reports/stage_previews/runs/RUN_002/index.json')
    assert set(manifest)=={'synth','floorplan','placement','cts','route','final'}
    assert manifest['route']['source'].endswith('5_route.def')
    assert manifest['final']['source'].endswith('6_final.def')
    shutil.rmtree(results); shutil.rmtree(reports); shutil.rmtree(old)
    rows = v.views(p, {'stage':'done','status':'passed','detail':{'repair':[{}]}}, version='final')
    assert all(row['available'] and row['run']=='RUN_002' for row in rows)
    assert len({v.image(p, row)[0] for row in rows})==6


def test_final_version_uses_only_verified_resume_lineage_and_labels_synthesis(tmp_path):
    p, old = project(tmp_path)
    (old/'3_place.def').write_text(DEF)
    v.capture(p, 'flow', {})
    new = p/'backend/RUN_002/results'; new.mkdir(parents=True)
    (new/'6_final.def').write_text(DEF.replace('5000', '8000'))
    digest = 'a'*64
    (old.parent/'stage_artifact_manifest.jsonl').write_text('\n'.join(json.dumps({'stage':s,'status':0,'sha256':digest}) for s in ('synth','place')))
    resume = {'reused_stages':['synth','place'], 'parent_lineage':{
        stage:{'source':'recorded','verified':True,'parent_run':'RUN_001','sha256':digest,'parent_sha256':digest}
        for stage in ('synth','place')}}
    (new.parent/'resume_meta.json').write_text(json.dumps(resume))
    design = {'stage':'done','status':'passed','detail':{'repair':[{}]}}
    rows = v.views(p, design, version='final')
    for index in (0,2):
        assert rows[index]['available'] and rows[index]['reused_from']=='RUN_001'
        assert '血缘已核验' in rows[index]['caption'] and rows[index]['run']=='RUN_002'
    resume['parent_lineage']['synth']['verified']=False
    resume['parent_lineage']['place']['parent_sha256']='b'*64
    (new.parent/'resume_meta.json').write_text(json.dumps(resume))
    rows = v.views(p, design, version='final')
    assert not rows[0]['available'] and not rows[2]['available']
    assert rows[0]['state']=='unverified'
    assert '血缘未核验' in rows[0]['caption']


def test_repaired_legacy_png_cannot_prove_prior_run_stages(tmp_path):
    p, old = project(tmp_path)
    (old/'3_place.def').write_text(DEF)
    v.capture(p, 'flow', {})
    shutil.rmtree(p/'backend')
    (p/'reports/layout.png').write_bytes(b'final')
    rows = v.views(p, {'stage':'done','status':'passed','detail':{'repair':[{}]}}, version='final')
    assert rows[-1]['available'] and rows[-1]['source']=='reports/layout.png'
    assert all(not row['available'] for row in rows[:-1])


def test_final_version_legacy_single_flow_keeps_retained_placement(tmp_path):
    p, old = project(tmp_path)
    (p/'images').mkdir(); (p/'images/3_place.def').write_text(DEF)
    (p/'reports').mkdir(); (p/'reports/layout.png').write_bytes(b'final')
    shutil.rmtree(p/'backend')
    rows = v.views(p, {'stage':'done','status':'passed','detail':{}}, version='final')
    assert rows[2]['available'] and rows[2]['source']=='images/3_place.def'
    assert not rows[1]['available'] and not rows[3]['available']
    assert rows[-1]['available']


def test_final_version_waiting_repair_does_not_show_original_timeline(tmp_path):
    p, old = project(tmp_path)
    (old/'3_place.def').write_text(DEF)
    v.capture(p, 'flow', {})
    rows = v.views(p, {'stage':'repair','status':'running','detail':{}}, version='final')
    assert all(not row['available'] for row in rows)
    assert all(row['run'] is None for row in rows)
