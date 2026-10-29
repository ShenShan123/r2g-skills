"""Offline replay of real pre-states; no evaluator/provider/EDA is invoked."""
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parent
sys.path[:0]=[str(ROOT),str(ROOT/'code/memory/scripts'),str(ROOT/'code/memory')]
import experiment as E
import t2_tehm_execution_policy as P
from t2_tehm_effective_state import load_registry, resolve


def main():
    old=ROOT.parent/'jobs/C00775/TEHM_FIXED'
    units=[json.loads(line) for line in (old/'shared/shared_units.jsonl').read_text().splitlines()]
    index=E.load(old/'evidence-index.json')
    registry=load_registry();rows=[]
    for unit in units:
        if not unit.get('shared_label',{}).get('admitted'):continue
        raw=unit['raw_row'];action=raw['config_delta']
        proof=registry['historical_units'][unit['unit_id']]['pre_execution']
        receipt=E.load(proof['path']);execution={**proof,'values':receipt['effective']}
        historical=resolve(raw['config_before'],action,registry,historical_unit=unit)
        current=resolve(raw['config_before'],action,registry,execution=execution)
        assert all(v['status']=='KNOWN' for v in historical.values())
        assert all(v['status']=='KNOWN' for v in current.values())
        # This artificial candidate tests the consumer contract, not the real
        # producer's ranking. The real evidence and its UNKNOWN signature remain
        # untouched; use the specific rule in the public situation as a current
        # query, never as recovered historical annotation.
        signature='drc:'+raw['situation']['violation_class']
        candidate={'id':'offline_raw_action','auto_apply':True,'config_edits':action,
                   'recheck':'drc','rerun_from':raw['from_stage']}
        plan={'strategies':[candidate], 'tehm_v2':{'state':{'signature':signature,'failures':[signature]},
            'decisions':[{'id':candidate['id'],'decision':'kept'}]}}
        decision=P.choose(plan=plan,situation=raw['situation'],cfg=raw['config_before'],units=units,
            index=index,exclude_families=set(),used=[],effective_execution=execution)
        assert decision['candidate'] is None
        assert decision['reason']!='unknown_action_precondition'
        rows.append({'unit_id':unit['unit_id'],'current_preconditions':current,
            'historical_preconditions':historical,'history_signature':unit['shared_label']['signature'],
            'historical_rule_unknown_not_promoted':True,'consumer_decision':decision,
            'candidate_plan_is_offline_fixture':True})
    assert len(rows)==3
    # All 50 per-target sources still match the original frozen hashes. No new
    # execution, admission, annotation or writeback authority is generated.
    parent=E.load(ROOT.parent/'registration.json')
    labels={}
    for job in E.load(ROOT.parent/'plan.json')['jobs']:
        if job['variant']!='TEHM_FIXED':continue
        for rel in ('shared/shared_units.jsonl','ann/ann_v2_dev.jsonl','evidence-index.json','exclusions.json'):
            path=(Path(job['root'])/rel).resolve()
            expected=parent['files'].get(str(path))
            if expected is None:raise ValueError('Source lacks original freeze: '+str(path))
            assert E.sha(path)==expected
            labels[str(path)]=expected
    E.record(ROOT/'reports/replay.json',{'boundary':'offline consumer-contract replay; not real-flow validation',
        'rows':rows,'all_original_sources_unchanged':True,'source_hashes':labels,
        'source_label_edits':0,'new_model_requests':0,'new_eda_flows':0,
        'preconditions_resolved':3,'new_executable_positives_created':0})
    print(json.dumps({'replayed_real_pre_states':len(rows),'preconditions':'KNOWN',
        'original_unknown_signatures':'unchanged; still not executable','model_requests':0,'EDA_flows':0}))


if __name__=='__main__':main()
