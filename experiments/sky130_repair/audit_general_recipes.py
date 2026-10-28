"""Read-only audit of current cross-project recipe evidence and lifecycle status."""
import hashlib,json,sqlite3,sys,time
from pathlib import Path
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from strict_signoff import inspect_run
from area_trial_evidence import classify
REPO=HERE.parents[1]

def independent_replay(root,strategy,check):
 root=Path(root)
 if not (root/'plan.json').exists():return {'state':'not_prepared'}
 plan=json.loads((root/'plan.json').read_text())
 if (plan.get('mode')!='normal_unforced_independent_replay' or plan.get('forced_selection') is not False
     or plan['key']['strategy']!=strategy or len(plan['arms'])!=1):raise ValueError('invalid independent replay plan')
 selected=json.loads((root/'selection.json').read_text())
 stage='place' if strategy=='lvs_port_feedthrough_buffer' else 'floorplan'
 if selected['returncode']!=0 or selected['stdout'].strip().split('\t')!=[strategy,stage,check]:raise ValueError('normal selection evidence differs')
 applied=json.loads((root/'application.json').read_text())
 if applied['returncode']!=0 or json.loads(applied['stdout'])['status']!='applied':raise ValueError('normal application evidence differs')
 result={'state':'prepared','key':plan['key'],'normal_selection_verified':True,'physical_success':False}
 manifests=list(root.glob('execution_manifest*.json'))
 for path in manifests:
  if json.loads(path.read_text())['plan_sha256']!=hashlib.sha256((root/'plan.json').read_bytes()).hexdigest():raise ValueError('running replay plan changed')
 arm=plan['arms'][0];project=Path(arm['project']);receipt=project/'execution.json'
 if receipt.exists():
  execution=json.loads(receipt.read_text());result['state']=execution['status']
  if execution['status']=='completed':
   if strategy!='fixed_area_route_relief':
    if execution['flow_returncode']!=0:
     raw=Path(execution['run'])/'result.json';reported=json.loads(raw.read_text())
     if reported.get('status')=='pass':raise ValueError('failed flow has contradictory passing result')
     result.update(flow_returncode=execution['flow_returncode'],classification='flow_failed',
                   result_sha256=hashlib.sha256(raw.read_bytes()).hexdigest())
     return result
    actual=inspect_run(execution['run'])
    if actual!=execution.get('strict_signoff'):raise ValueError('independent signoff changed')
    result.update(acceptance=actual,physical_success=actual['dual_clean'])
   else:
    actual=classify(execution['run'],execution['flow_returncode'])
    if actual!=execution.get('acceptance'):raise ValueError('independent area acceptance changed')
    result.update(acceptance=actual,physical_success=actual['is_success'])
 return result

def audit():
 store=REPO/'r2g-skills/signoff-loop/knowledge/knowledge.sqlite'
 with sqlite3.connect('file:'+str(store)+'?mode=ro',uri=True) as c:
  c.row_factory=sqlite3.Row
  statuses=[dict(r) for r in c.execute('select * from recipe_status where platform=? and strategy in (?,?,?)',('sky130hd','lvs_port_feedthrough_buffer','pin_pdn_clearance','fixed_area_route_relief'))]
 result={'at':time.strftime('%FT%T%z'),'lifecycle_rows':statuses,'recipes':{}}
 port=REPO/'design_cases/sky130_recipe_validation_20261026'
 manifest=json.loads((port/'durable_evidence.json').read_text());rows=[]
 for arm in manifest['arms']:
  actual=inspect_run(Path(arm['project'])/'physical_run')
  if actual!=arm['strict_signoff']:raise ValueError('Published recipe evidence changed')
  rows.append({'family':arm['source_repository'],'role':arm['role'],'repeat':arm['repeat'],'dual_clean':actual['dual_clean']})
 result['recipes']['lvs_port_feedthrough_buffer']={'key':manifest['key'],'physical_arms':rows,'independent_families':len({r['family'] for r in rows}),'scope':'Only the promoted symptom/platform/design-class key; not a generic LVS mismatch fix'}
 root=Path('/tmp/sky130_aux208/pdn_recipe/trials');plan=json.loads((root/'plan.json').read_text());rows=[]
 for arm in plan['arms']:
  receipt=Path(arm['project'])/'execution.json';entry={k:arm[k] for k in ('case','role','repeat','source_repository')}
  entry['state']='not_started'
  if receipt.exists():
   r=json.loads(receipt.read_text());entry['state']=r['status']
   if r['status']=='completed':
    actual=inspect_run(Path(r['run']))
    if actual!=r.get('strict_signoff'):raise ValueError('Completed PDN evidence changed or incomplete')
    entry.update(dual_clean=actual['dual_clean'],drc=actual['drc'],lvs=actual['lvs'])
  rows.append(entry)
 result['recipes']['pin_pdn_clearance']={'key':plan['key'],'physical_arms':rows,'independent_families':len({r['source_repository'] for r in rows}),'publication_evidence':'design_cases/sky130_pdn_recipe_validation_20261026/published.json','remaining_acceptance':'Repeated physical A/B and ordinary publication completed; independent normal replay acceptance is reported separately below.'}
 root=Path('/tmp/sky130_aux208/area_recipe/trials');plan=json.loads((root/'plan.json').read_text());rows=[]
 for arm in plan['arms']:
  receipt=Path(arm['project'])/'execution.json';entry={k:arm[k] for k in ('case','role','repeat','source_repository')}
  entry['state']='not_started'
  if receipt.exists():
   r=json.loads(receipt.read_text());entry['state']=r['status']
   if r['status']=='completed':
    actual=classify(Path(r['run']),r['flow_returncode'])
    if actual!=r.get('acceptance'):raise ValueError('Completed area evidence changed')
    entry.update(acceptance=actual)
  rows.append(entry)
 result['recipes']['fixed_area_route_relief']={'key':plan['key'],'physical_arms':rows,'independent_families':len({r['source_repository'] for r in rows}),'publication_evidence':'design_cases/sky130_area_recipe_validation_20261027/published.json','acceptance_scope':'Repeated physical A/B and ordinary publication completed; independent normal replay is reported separately below. Historical replays never add unique rescues.'}
 result['recipes']['lvs_port_feedthrough_buffer']['normal_replay']=independent_replay('/tmp/sky130_aux208/port_independent_replay/C02994/normal_replay','lvs_port_feedthrough_buffer','lvs')
 result['recipes']['pin_pdn_clearance']['normal_replay']=independent_replay('/tmp/sky130_aux208/pdn_independent_replay/normal_replay','pin_pdn_clearance','drc')
 result['recipes']['fixed_area_route_relief']['normal_replay']=independent_replay('/tmp/sky130_aux208/area_independent_replay/normal_replay','fixed_area_route_relief','route')
 result['observation_scope']='Receipt states are not liveness proof; completed physical artifacts independently rechecked. Observe worker handles separately before restart or slot reuse.'
 result['user_priority']='Cross-project reusable recipes first; hard low-transferability cases may be recorded and deferred. C03878 bespoke CTS investigation stopped; C03259 candidate retained without new priority rerun.'
 return result
if __name__=='__main__':print(json.dumps(audit(),indent=2))
