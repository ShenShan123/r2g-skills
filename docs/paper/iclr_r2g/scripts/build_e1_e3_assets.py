#!/usr/bin/env python3
"""Publication-sized vector figures from frozen E1/E3 results with explicit model versions.

Each plotted value is saved with its input digest in figure_data.json.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Patch, Rectangle
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--materials', type=Path, default=Path('/home/yangao/r2g_paper_experiment_materials_20260909'))
args = parser.parse_args()
relative = {
    'e1': 'experiment1/results/experiment1_final_scores_6llm_plus_r2g.json',
    'e3': 'experiment3/results/experiment3_final_results.json',
}
sources = {k: args.materials/v for k,v in relative.items()}
data = {k: json.loads(p.read_text()) for k,p in sources.items()}
costs = json.loads((ROOT/'evidence/api_costs.json').read_text())
manifest = {'sources': {k: {'path': str(p), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                        for k,p in sources.items()}}
from plot_style import configure, save as save_figure, BLUE, CORAL, PALE, INK, TEAL, MUTED
ORANGE=CORAL
configure()
def save(fig,name): save_figure(fig,ROOT,name)

# E1: every row fills the same 200-slot denominator. Preserve submitted failures.
lookup = {m['method_id']: m for m in data['e1']['methods']}
ids = ['r2g-expander-cold','openai-vanilla','claude-vanilla','qwen-vanilla',
       'kimi-vanilla','deepseek-vanilla','grok-vanilla','gemini-vanilla']
labels = ['R2G Cold','GPT-5.6-sol','Claude Opus 5','Qwen3.7-Max','Kimi-K2.7-Code','DeepSeek-V4-Pro','grok-4.5','Gemini-3.1-Pro-Preview']
qualified = np.array([lookup[k]['infrastructure_adjusted_publishable_qualified'] for k in ids])
submitted = np.array([lookup[k]['submitted'] for k in ids])
assert data['e1']['fixed_denominator'] == 200
assert np.all(qualified <= submitted) and np.all(submitted <= 200)
fig, ax = plt.subplots(figsize=(5.5, 2.65))
fig.subplots_adjust(left=.32, right=.72, bottom=.18, top=.78)
y = np.arange(len(ids))
ax.barh(y, 200, height=.63, color=PALE, zorder=1)
ax.barh(y, qualified, height=.63, color=BLUE, zorder=3)
ax.barh(y, submitted-qualified, left=qualified, height=.63, color=ORANGE,
        edgecolor='white', linewidth=.35, zorder=3)
ax.set(yticks=y, yticklabels=labels, xlim=(0,200), ylim=(7.6,-.65),
       xticks=[0,50,100,150,200], xlabel='Requested RTL slots (N = 200)')
ax.spines['left'].set_visible(False); ax.tick_params(axis='y', length=0)
ax.grid(axis='x', color='white', lw=.7, zorder=2)
cost_ids = ['r2g','gpt','claude','qwen','kimi','deepseek','grok','gemini']
for i,(q,s,k) in enumerate(zip(qualified,submitted,cost_ids)):
    ax.text(1.06, i, f'{q}/{s}', transform=ax.get_yaxis_transform(), va='center', fontsize=8.5)
    ax.text(1.65, i, costs['acquisition'][k]['display_usd'], transform=ax.get_yaxis_transform(),
            va='center', ha='right', fontsize=8.5)
ax.text(1.06,-1.08,'Q / S',transform=ax.get_yaxis_transform(),va='center',fontsize=8.5)
ax.text(1.65,-1.08,'API USD',transform=ax.get_yaxis_transform(),va='center',ha='right',fontsize=8.5)
fig.legend(handles=[Patch(facecolor=BLUE,label='Qualified'),Patch(facecolor=ORANGE,label='Rejected'),
                    Patch(facecolor=PALE,label='Unfilled')],loc='upper center',bbox_to_anchor=(.5,.985),
           ncol=3,frameon=False,handlelength=.95,handletextpad=.35,columnspacing=.8)
manifest['acquisition_yield'] = {'method_ids':ids,'qualified':qualified.tolist(),'submitted':submitted.tolist(),
                               'unfilled':(200-submitted).tolist(),'denominator':200,'right_column':'qualified/submitted'}
save(fig,'acquisition_yield')

# E3: paired row alignment; actual trial counts have a separate linear scale.
ids = ['m0','m1','m2','m3','gpt','claude','qwen']
labels = ['M0','M1','M2','M3','GPT-5.6-sol','Claude Opus 5','Qwen3.7-Max']
rows = [data['e3']['b_results'][k] for k in ids[:4]] + [data['e3']['pure_llm_results'][k] for k in ids[4:]]
drc = np.array([r['drc_successes'] for r in rows]); setup=np.array([r['setup_successes'] for r in rows])
trials = [r.get('challenge_orfs_attempts',r.get('orfs_attempts')) for r in rows]
assert np.array_equal(drc+setup,[r['challenge_successes'] for r in rows])
# Preserve the original held-out series separately from the post-development arm.
manifest['repair_outcomes_cost']={'method_ids':ids,'drc':drc.tolist(),'setup':setup.tolist(),
                                'trials':trials,'denominator':13,'m3_uncovered':6}
full=json.loads((ROOT/'evidence/e3_full_r2g_derived.json').read_text())
assert full['evaluation_role']=='post_development_supplement_not_heldout'
labels+=['Full R2G']
drc=np.append(drc,full['counts']['drc_clean']);setup=np.append(setup,full['counts']['setup_clean'])
trials=trials+[full['counts']['repair_orfs_calls']]
fig,(a,b)=plt.subplots(1,2,figsize=(5.5,3.05),gridspec_kw={'width_ratios':[1.5,1]})
fig.subplots_adjust(left=.23,right=.82,bottom=.18,top=.76,wspace=.32)
y=np.arange(len(labels))
a.barh(y,13,height=.62,color=PALE)
a.barh(y,drc,height=.62,color=BLUE)
a.barh(y,setup,left=drc,height=.62,color=ORANGE)
a.barh(3,6,left=7,height=.62,color='white',edgecolor='#768591',hatch='////',linewidth=.6)
a.barh(7,full['no_action_tasks'],left=13-full['no_action_tasks'],height=.62,color='white',edgecolor=MUTED,hatch='////',linewidth=.6)
a.set(yticks=y,yticklabels=labels,xlim=(0,13),xticks=[0,7,13],ylim=(7.8,-.65),xlabel='Clean designs / 13')
a.spines['left'].set_visible(False); a.tick_params(axis='y',length=0)
for i,total in zip(y,drc+setup):
    a.text(total+.22 if total<7 else total-.24,i,str(total),va='center',
           ha='left' if total<7 else 'right',color=INK if total<7 else 'white',fontsize=8.5)
b.barh(y,trials,height=.62,color=[TEAL]*4+[MUTED]*3+[TEAL])
b.set(xlim=(0,40),xticks=[0,20,40],ylim=(7.8,-.65),yticks=[],xlabel='ORFS trials')
b.spines['left'].set_visible(False)
for i,t in zip(y,trials): b.text(t+.8,i,str(t),va='center',fontsize=8.5)
for i,k in enumerate(ids+['full_r2g']):
    b.text(1.66,i,costs['repair_deployment'][k]['display_usd'],
           transform=b.get_yaxis_transform(),ha='right',va='center',fontsize=8.5)
b.text(1.66,-1.12,'API USD',transform=b.get_yaxis_transform(),ha='right',va='center',fontsize=8.5)
fig.legend(handles=[Patch(facecolor=BLUE,label='DRC'),Patch(facecolor=ORANGE,label='Setup'),
                    Patch(facecolor='white',edgecolor='#768591',hatch='////',label='No eligible action')],
           loc='upper center',bbox_to_anchor=(.5,.985),ncol=3,frameon=False,
           handlelength=.9,handletextpad=.35,columnspacing=.8)
fig.text(.61,.86,'b  Deployment cost',fontsize=9,fontweight='bold')
fig.text(.23,.86,'a  Repair success',fontsize=9,fontweight='bold')

save(fig,'repair_outcomes_cost')

# Preserve E4 evidence and refuse any numerical change to the frozen E1/E3 series.
existing=json.loads((ROOT/'evidence/figure_data.json').read_text())
for key in ['acquisition_yield','repair_outcomes_cost']:
    assert manifest[key]==existing[key], key
for key in ['e1','e3']:
    assert manifest['sources'][key]==existing['sources'][key], key
print('Rebuilt E1/E3 figures with full model versions; frozen values and sources unchanged.')
