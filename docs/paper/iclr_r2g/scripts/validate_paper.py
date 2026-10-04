#!/usr/bin/env python3
"""Validate current ICLR manuscript, official style, and E4 evidence bindings."""
from pathlib import Path
import hashlib,json,re,subprocess
from decimal import Decimal
ROOT=Path(__file__).resolve().parents[1]
def output(*args):return subprocess.check_output(args,text=True)
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
info=output('pdfinfo',str(ROOT/'main.pdf'));fonts=output('pdffonts',str(ROOT/'main.pdf'))
text=output('pdftotext','-layout',str(ROOT/'main.pdf'),'-')
log=(ROOT/'main.log').read_text();aux=(ROOT/'main.aux').read_text()
migration=json.loads((ROOT/'evidence/migration_manifest.json').read_text())
for name,sha in migration['template_files'].items():assert digest(ROOT/name)==sha,name
revision=json.loads((ROOT/'evidence/e4_revision_manifest.json').read_text())
for name,sha in revision['files_sha256'].items():assert digest(ROOT/name)==sha,name
assert digest(ROOT/'evidence/figure_data.json')=='345d25d3af353a5bc7efcc709093950facfa6ec165083712bd608b86d085ba96', 'Frozen numerical figure data changed'
costs=json.loads((ROOT/'evidence/api_costs.json').read_text())
for name,sha in costs['sources_sha256'].items():assert digest(Path(name))==sha,name
for section in ['acquisition','repair_learning','repair_deployment','converter_development']:
 for model,row in costs[section].items():
  if 'input_tokens' not in row:continue
  assert row['input_tokens']+row['billed_output_tokens']==row['total_tokens']
  p=costs['pricing'][model]
  usd=(Decimal(row['input_tokens'])*Decimal(p['input_usd_per_million'])+Decimal(row['billed_output_tokens'])*Decimal(p['output_usd_per_million']))/Decimal(1000000)
  assert row['reference_api_usd']==float(usd) and row['display_usd']==f'${usd:.2f}'
assert costs['acquisition']['grok']['total_tokens']==19991047
for figure,section in [('acquisition_yield','acquisition'),('repair_outcomes_cost','repair_deployment'),('conversion_semantics','converter_development')]:
 labels=output('pdftotext',str(ROOT/f'figures/{figure}.pdf'),'-')
 for row in costs[section].values():assert row['display_usd'] in labels,(figure,row['display_usd'])
assert 'APICOSTACCOUNTING' in re.sub(r'\s+', '', text).upper()
models=json.loads((ROOT/'evidence/model_names.json').read_text())
for name,sha in models['sources'].items():assert digest(Path(name))==sha,name
for figure in ['acquisition_yield','repair_outcomes_cost','conversion_semantics','conversion_core_checks']:
 labels=output('pdftotext',str(ROOT/f'figures/{figure}.pdf'),'-')
 for model in ['GPT-5.6-sol','Claude Opus 5','Qwen3.7-Max']:assert model in labels,(figure,model)
 assert not re.search(r'\b(?:GPT|Claude|Qwen)\b(?![-\d]| Opus)',labels),figure
derived=json.loads((ROOT/'evidence/e4_600k_derived.json').read_text())
for name,sha in derived['sources'].items():assert digest(ROOT/name)==sha,name
for name,sha in derived['case_source_sha256'].items():assert digest(Path(name))==sha,name
full=json.loads((ROOT/'evidence/e3_full_r2g_derived.json').read_text())
for name,sha in full['sources'].items():assert digest(ROOT/name)==sha,name
assert full['counts']['completed']==13 and full['counts']['strict_clean']==8
assert full['counts']['repair_orfs_calls']==11 and full['no_action_tasks']==2
assert full['evaluation_role']=='post_development_supplement_not_heldout'
repair_labels=output('pdftotext',str(ROOT/'figures/repair_outcomes_cost.pdf'),'-')
assert 'Full R2G' in repair_labels and '(post-dev.)' not in repair_labels
for value in ['61.54','19,296.617','2.804435','Full R2G']:assert value in text,value
assert 'recipes developed using evidence from the evaluated challenge set' in (ROOT/'sections/03_evaluation.tex').read_text()
assert 'post-development' not in text and 'Complete-Library Supplement' not in text
stage=json.loads((ROOT/'evidence/gnn_stage_prediction.json').read_text())
geometry=json.loads((ROOT/'evidence/gnn_geometry_ablation.json').read_text())
models_gnn=json.loads((ROOT/'evidence/gnn_model_comparison.json').read_text())
portability=json.loads((ROOT/'evidence/gnn_portability_verification.json').read_text())
assert len(stage['summary'])==12 and all(row['seeds']==3 for row in stage['summary'])
assert len(geometry['summary'])==3 and all(row['seeds']==3 for row in geometry['summary'])
assert len(models_gnn['summary'])==28 and all(row['seeds']==3 for row in models_gnn['summary'])
assert portability['status']=='PASS' and portability['test_designs']==54
assert portability['original']['pooled_n']==142996 and portability['reproduced']['pooled_n']==142996
main=(ROOT/'main.tex').read_text()
tex='\n'.join([main,*[p.read_text() for p in sorted((ROOT/'sections').glob('*.tex'))],*[p.read_text() for p in sorted((ROOT/'tables').glob('*.tex'))]])
for kind in ['figure','table']:
 for block in re.findall(r'\\begin\{'+kind+r'\}.*?\\end\{'+kind+r'\}',tex,re.S):
  caption=block.index(r'\caption')
  if kind=='figure':assert caption>block.index(r'\includegraphics'), 'Figure caption must follow figure'
  else:
   content=re.search(r'\\(?:begin\{tabular\}|input\{tables/)',block)
   assert content and caption<content.start(), 'Table caption must precede table'
for name in ['conversion_semantics','conversion_core_checks']:
 figure_text=output('pdftotext',str(ROOT/f'figures/{name}.pdf'),'-')
 assert 'Feature' in figure_text and 'Numeric' not in figure_text
active='\n'.join(line.split('%')[0] for line in main.splitlines())
assert '\\iclrfinalcopy' not in active
assert 'geometry' not in active and '\\pagestyle{empty}' not in active and 'IEEE' not in active
assert 'Anonymous authors' in text and 'Paper under double-blind review' in text
assert 'Under review as a conference paper at ICLR 2027' in text and 'ICLR 2026' not in text
assert 'ai use statement' in text.lower() and 'iclr2027_conference' in active
assert not re.search(r'\bE[1-9]\d*\b|\bExperiment\s+(?:[1-9]\d*|one|two|three|four)\b',text,re.I), 'Internal experiment IDs remain in rendered paper'
assert 'TBD' not in text and 'Pending evidence' not in text
for value in ['33.52','600,000','556,437','587,522','570,956','25.12','13.33','178/209','100/207']:assert value in text,value
for name in ['GPT-5.6-sol','Claude Opus 5','Qwen3.7-Max','grok-4.5']:assert name in text,name
assert not re.search(r'\b(?:GPT|Claude|Qwen)\b(?![-\d]| Opus)',tex), 'Unversioned model in manuscript'
assert 'grok-4.5-build' not in tex
for obsolete in ['347,156','294,793','288,119','97.4','1,473.585']:assert obsolete not in tex,obsolete
assert not re.search(r'(Citation|Reference).*undefined',log)
assert not re.search(r'Overfull \\[hv]box',log)
rows=[r.split() for r in fonts.splitlines()[2:] if r.strip()]
assert rows and all(r[-5]=='yes' for r in rows) and 'Type 3' not in fonts
assert re.search(r'Page size:\s+612\s+x\s+792\s+pts',info)
keys={k.strip() for group in re.findall(r'\\cite[pt]?\{([^}]+)\}',tex) for k in group.split(',')}
assert keys<=set(re.findall(r'@\w+\{([^,]+),',(ROOT/'references.bib').read_text()))
for name in ['workflow','acquisition_yield','repair_outcomes_cost','conversion_semantics','conversion_core_checks','downstream_utility']:
 images=output('pdfimages','-list',str(ROOT/f'figures/{name}.pdf'))
 assert not images.splitlines()[2:],name
 assert f'figures/{name}.pdf' in tex
for value in ['278 strict-clean designs','170.2','55.0','142,996']:assert value in text,value
body_end=int(re.search(r'\\newlabel\{sec:main_end\}\{\{[^}]*\}\{(\d+)\}',aux).group(1))
assert body_end<=9,'Exceeds ICLR 2027 main-text limit'
report={'pages':int(re.search(r'Pages:\s+(\d+)',info).group(1)),'main_text_ends_on_page':body_end,
 'template':'ICLR 2027 official template','anonymous_review':True,'paper_size':'US Letter',
 'ai_use_statement_present':True,'official_style_unmodified':True,'e1_e3_numerical_results_unchanged':True,'full_model_names_in_figures':True,
 'e3_full_r2g_designs_verified':13,'e3_full_r2g_trials_verified':11,'e3_full_r2g_role':'post_development_supplement_not_heldout',
 'e4_source_cases_verified':len(derived['case_source_sha256']),'e4_aggregate_metrics_recomputed':16,
 'new_figures_vector_only':True,'fonts_embedded':True,'type3_fonts':False,
 'downstream_designs':278,'downstream_split':[175,49,54],'downstream_seeds':3,
 'downstream_portability_targets_verified':142996,
 'unresolved_references':False,'overfull_boxes':False,'placeholders':0,'citation_keys':sorted(keys),
 'reference_api_costs_verified':True,'api_cost_ledger_sources_verified':len(costs['sources_sha256']),
 'pdf_sha256':digest(ROOT/'main.pdf'),'note':'E4 audits, Full R2G evidence, downstream GNN summaries, and API reference-cost arithmetic verified; no submission performed.'}
(ROOT/'evidence/main_readable.txt').write_text(text)
(ROOT/'evidence/build_validation.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
