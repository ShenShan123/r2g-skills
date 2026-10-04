#!/usr/bin/env python3
"""Recompute frozen 600k audit metrics; emit vector figures and exact LaTeX tables."""
from pathlib import Path
import json,hashlib,math
from collections import Counter
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Patch,Rectangle
ROOT=Path(__file__).resolve().parents[1]
E=ROOT/'evidence/e4_600k'
RAW=Path('/home/yangao/r2g_exp4_progressive_20260910/fixed_test_24/reports/semantic/cases')
D=json.loads((E/'fixed_test_results.json').read_text())
A=json.loads((E/'semantic_audit_summary.json').read_text())
F=json.loads((E/'failure_analysis_evidence.json').read_text())
ids=['r2g-frozen-v3','llm-gpt-frozen','llm-claude-frozen','llm-qwen-frozen'];names=['R2G','GPT-5.6-sol','Claude Opus 5','Qwen3.7-Max']
groups=['identity','topology','numeric','label'];checks={};source_hashes={}
for m in ids:
 files=sorted((RAW/m).glob('*.json'));assert len(files)==24
 sums={k:[] for k in groups};passes=0
 for p in files:
  c=json.loads(p.read_text());assert c.get('status')!='ORACLE_ERROR';passes+=c['verified_core_status']=='PASS'
  source_hashes[str(p)]=hashlib.sha256(p.read_bytes()).hexdigest()
  for chk in c['checks']:
   g=chk['group'];ev=chk.get('evidence') or {};status=chk['status']
   if g in ['identity','topology']:
    if sum(ev.get(k,0) for k in ['tp','fp','fn']):sums[g].append(float(ev.get('f1',0)))
    elif status!='PASS':sums[g].append(0)
   if g in ['numeric','label']:
    expected=ev.get('expected',ev.get('report_expected_labels',0))
    if expected:sums[g].append(min(1,max(0,ev.get('matched',0)-ev.get('incorrect',0))/expected))
    elif status not in ['PASS','NOT_APPLICABLE']:sums[g].append(0)
 coverage={k:sum(v)/len(v) if v else 0 for k,v in sums.items()}
 assert passes==D['methods'][m]['semantic_passes']
 for g in groups:assert math.isclose(coverage[g],D['methods'][m]['coverage'][g],abs_tol=1e-12),(m,g)
 checks[m]={'passes':passes,'coverage':coverage,'included_check_counts':{k:len(v) for k,v in sums.items()}}
# Independently extract one representative, without treating it as a sampled prevalence estimate.
task='exp1_49940d240660_blinking';case={}
for m in ids:
 cs=F[m]['representatives'][task]['checks'];out={}
 for g,k in [('identity','gate'),('identity','pin'),('label','net.routed_wirelength_um'),('label','net.ground_cap_pF'),('label','pin.setup_slack_ns'),('label','pin.hold_slack_ns')]:
  ch=next(c for c in cs if c['group']==g and c['check']==k and c['stage']=='route');ev=ch['evidence']
  out[k]={'status':ch['status'],'evidence':ev}
 case[m]=out
manifest={'study':'600k fixed benchmark retest','sources':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in E.glob('*') if p.is_file()},'case_source_sha256':source_hashes,'recomputed':checks,'representative':case}
(ROOT/'evidence/e4_600k_derived.json').write_text(json.dumps(manifest,indent=2)+'\n')
from plot_style import configure, BLUE, TEAL, CORAL, GOLD, PASS, FAIL, UNKNOWN, INK
configure()
costs=json.loads((ROOT/'evidence/api_costs.json').read_text())
cost_labels=[n+'\n'+costs['converter_development'][k]['display_usd']+' API dev.'
             for n,k in zip(names,['r2g','gpt','claude','qwen'])]
blue=BLUE;orange=TEAL
fig,(a,b)=plt.subplots(1,2,figsize=(5.5,2.65))
fig.subplots_adjust(left=.24,right=.97,bottom=.19,top=.73,wspace=.30)
for ax,gs,scale in [(a,['identity','topology'],1),(b,['numeric','label'],100)]:
 for off,g,col in [(-.17,gs[0],BLUE if ax is a else CORAL),(.17,gs[1],TEAL if ax is a else GOLD)]:
  vals=[checks[m]['coverage'][g]*scale for m in ids]
  ax.barh([i+off for i in range(4)],vals,height=.29,color=col)
  for i,v in enumerate(vals):
   txt=f'{v:.3f}' if scale==1 else (f'{v:.3f}' if 0<v<.1 else f'{v:.2f}')
   inside=v>.7*scale
   ax.text(v-.025*scale if inside else v+.025*scale,i+off,txt,ha='right' if inside else 'left',va='center',color='white' if inside and ax is a else INK,fontsize=8)
 ax.set(ylim=(3.6,-.6),xlim=(0,scale),yticks=range(4),yticklabels=cost_labels if ax is a else [],xticks=[0,scale/2,scale])
 ax.tick_params(axis='y',labelsize=8.5)
 ax.tick_params(axis='y',length=0);ax.spines['left'].set_visible(False)
 ax.legend(handles=[Patch(facecolor=BLUE if ax is a else CORAL,label='Feature' if gs[0]=='numeric' else gs[0].capitalize()),Patch(facecolor=TEAL if ax is a else GOLD,label=gs[1].capitalize())],loc='lower center',bbox_to_anchor=(.5,1.01),ncol=2,frameon=False,handlelength=.8,columnspacing=.6,handletextpad=.3)
fig.text(.24,.94,'a  Logical structure',fontsize=9,fontweight='bold')
fig.text(.652,.94,'b  Physical semantics',fontsize=9,fontweight='bold')
a.set_xlabel('Mean audited F1');b.set_xlabel('Mean correct coverage (%)')
for ext in ['pdf','png']:fig.savefig(ROOT/f'figures/conversion_semantics.{ext}',dpi=260)
plt.close(fig)
gs=['alignment','causal','identity','label','mask','numeric','topology'];colors={'PASS':PASS,'FAIL':FAIL,'UNASSESSABLE':UNKNOWN}
fig,ax=plt.subplots(figsize=(5.5,2.1));fig.subplots_adjust(left=.24,right=.99,bottom=.26,top=.76)
for i,m in enumerate(ids):
 for j,g in enumerate(gs):
  states=Counter(r['groups'][g]['status'] for r in A['rows'] if r['method']==m)
  assert sum(states.values())==24 and len(states)==1
  st=next(iter(states));ax.add_patch(Rectangle((j-.5,i-.5),1,1,facecolor=colors[st],edgecolor='white',linewidth=2))
  ax.text(j,i,{'PASS':'P','FAIL':'F','UNASSESSABLE':'U'}[st]+':24',ha='center',va='center')
ax.set(xlim=(-.5,6.5),ylim=(3.5,-.5),xticks=range(7),xticklabels=['Align.','Causal','Identity','Label','Mask','Feature','Topo.'],yticks=range(4),yticklabels=names)
ax.xaxis.tick_top();ax.tick_params(length=0)
for spine in ax.spines.values():spine.set_visible(False)
fig.legend(handles=[Patch(facecolor=c,label=l) for (st,c),l in zip(colors.items(),['P: pass','F: fail','U: unassessable'])],loc='lower center',ncol=3,frameon=False)
for ext in ['pdf','png']:fig.savefig(ROOT/f'figures/conversion_core_checks.{ext}',dpi=260)
plt.close(fig)
(ROOT/'tables').mkdir(exist_ok=True)
rows=[]
for m,n in zip(ids,names):
 c=checks[m];v=c['coverage'];rows.append(f"{n} & {c['passes']}/24 & {v['identity']:.4f} & {v['topology']:.4f} & {100*v['numeric']:.2f}\\% & {100*v['label']:.3f}\\%"+r'\\')
(ROOT/'tables/e4_results.tex').write_text(r'''\begin{tabular}{lrrrrr}\toprule
Method & Core pass & Identity F1 & Topology F1 & Feature & Label\\\midrule
'''+ '\n'.join(rows)+r'''
\bottomrule\end{tabular}
''')
rows=[]
for m,n in zip(ids,names):
 vals=[]
 for k,v in case[m].items():
  ev=v['evidence']
  if v['status']=='UNASSESSABLE':vals.append('U');continue
  if k in ['gate','pin']:vals.append(f"{ev['tp']}/{ev['tp']+ev['fn']}")
  else:vals.append(f"{max(0,ev['matched']-ev['incorrect'])}/{ev['expected']}")
 rows.append(n+' & '+' & '.join(vals)+r'\\')
(ROOT/'tables/e4_case.tex').write_text(r'''\begin{tabular}{lrrrrrr}\toprule
Method & Gates & Pins & Wirelength & Ground cap. & Setup & Hold\\\midrule
'''+ '\n'.join(rows)+r'''
\bottomrule\end{tabular}
''')
budget=json.loads((E/'development_budget_comparison.json').read_text())
rows=[]
for model in ['gpt','claude','qwen']:
 fields=[]
 for group in ['identity','topology','numeric','label']:
  factor=1 if group in ['identity','topology'] else 100
  precision=3 if factor==1 or group=='label' else 2
  vals=[budget[model][b]['coverage'][group]*factor for b in ['400k','600k']]
  fields.append(f'{vals[0]:.{precision}f} $\\to$ {vals[1]:.{precision}f}')
 rows.append(names[['gpt','claude','qwen'].index(model)+1]+' & '+' & '.join(fields)+r'\\')
(ROOT/'tables/e4_development.tex').write_text(r'''\begin{tabular}{lrrrr}\toprule
Model & Identity F1 & Topology F1 & Feature (\%) & Label (\%)\\\midrule
'''+ '\n'.join(rows)+r'''
\bottomrule\end{tabular}
''')
print('Verified 96 full case audits, reproduced all 16 aggregate metrics; wrote two vector figures and three tables.')

# Highlight the proposed method consistently in the result tables.
for name in ['e4_results','e4_case']:
 p=ROOT/f'tables/{name}.tex'
 lines=p.read_text().splitlines()
 for i,line in enumerate(lines):
  if line.startswith('R2G &'):
   cells=line.removesuffix(r'\\').split(' & ')
   lines[i]=' & '.join(r'\textbf{'+cell+'}' for cell in cells)+r'\\'
 p.write_text('\n'.join(lines)+'\n')
