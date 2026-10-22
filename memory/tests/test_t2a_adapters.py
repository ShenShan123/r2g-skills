import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import t2a_adapters as A
import pytest
import t2a_runtime as T


def unit(ident, outcome='PASS', rolled_back=False):
    return {'unit_id':ident,'source_eligible':True,'outcome':{'canonical':outcome,'verified':True},
            'rollback':{'rolled_back':rolled_back},'raw_row':{'situation':{'platform':'sky130hd',
                 'check':'orfs_stage','violation_class':'route','die_mode':'auto','util_band':'gt60','timeout':True}},
            'action':{'config_delta':{'CORE_UTILIZATION':'45'},'from_stage':'floorplan'},
            'identity':{'ts':'2026-10-01'}}


def test_recipe_never_accepts_rollback_unknown_failure_or_duplicate_evidence(tmp_path):
    rows=[unit('a'),unit('b',rolled_back=True),unit('c',outcome='FAIL'),unit('d',rolled_back='UNKNOWN')]
    recipes=A.build_recipes(rows+[rows[0]])
    assert len(recipes)==1 and recipes[0]['unit_ids']==['a']
    p=tmp_path/'recipes.json';p.write_text(json.dumps({'recipes':recipes}))
    sit=A.situation(rows[0]);cfg={'CORE_UTILIZATION':'80'}
    assert len(A.recipe_strategies(p,sit,cfg))==1
    assert A.recipe_strategies(p,{**sit,'timeout':False},cfg)==[]
    assert A.recipe_strategies(p,sit,{'CORE_UTILIZATION':'45'})==[]
    assert A.recipe_strategies(p,sit,cfg,[recipes[0]['id']])==[]


def test_amem_evolution_updates_only_returned_neighbors_and_stable_links():
    note={'id':'new','context':'new','tags':[],'links':[]}
    neighbors=[{'id':'a','context':'old a','tags':[]},{'id':'b','context':'old b','tags':[]}]
    changed=A.evolve_note(note,neighbors,{'should_evolve':True,'actions':['strengthen','update_neighbor'],
          'suggested_connections':['b','missing'], 'tags_to_update':['route'],
          'new_context_neighborhood':['updated a'],'new_tags_neighborhood':[['one']]})
    assert changed and note['links']==['b'] and note['tags']==['route']
    assert neighbors[0]['context']=='updated a' and neighbors[1]['context']=='old b'


def test_expel_compares_every_success_failure_and_uses_first_success_chunks():
    rows=[{'task':'t','id':i,'success':s,'time':i} for i,s in [('a',True),('b',True),('c',False)]]
    schedule=A.expel_schedule(rows)
    assert [r['kind'] for r in schedule]==['compare','compare','all_success']
    assert schedule[-1]['success']==[rows[0]]


def test_original_expel_updates_keep_evidence_counts():
    parse,update=A.expel_operations()
    rules=update([],parse('ADD: Lower congestion before rerouting.'))
    assert rules==[('Lower congestion before rerouting.',2)]
    rules=update(rules,parse('AGREE 1: Lower congestion before rerouting.'))
    assert rules[0][1]==3
    assert update(rules,parse('REMOVE 1: Lower congestion before rerouting.'),list_full=True)==[]


def test_o1_full_expel_insights_fit_without_dropping_rules_or_examples():
    memory=A.PromptMemory.__new__(A.PromptMemory)
    memory.arm='EXPEL';memory.data={'rules':[['verified insight. '*3300,2]]}
    memory.rows=[{'id':'source','text':'verified trajectory'}]
    class Index:
        def search(self,*args):return [0]
    memory.index=Index()
    with pytest.raises(ValueError,match='Full insight/workflow'):
        memory.context('query',{},max_bytes=5500)
    context,ids=memory.context('query',{},max_bytes=T.MEMORY_CONTEXT_BYTES)
    assert memory.data['rules'][0][0] in context and ids==['source']
    assert 'verified trajectory' in context and len(context.encode())<80000


def test_o1_local_probe_measures_complete_json_body_not_just_memory_text():
    import t2a_verify_controls as V
    from types import SimpleNamespace
    H=SimpleNamespace(LLM_SYSTEM='Repair {platform}',LLM_API_MODEL='deepseek-flash',
                      LLM_MAX_TOKENS=800,LLM_MAX_INPUT_BYTES=96000)
    assert 64000<V.serialized_probe_bytes(H,'query','x'*64000)<96000
    with pytest.raises(ValueError,match='Serialized local probe'):
        V.serialized_probe_bytes(H,'query','x'*96000)
