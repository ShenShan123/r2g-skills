"""Prepare an isolated supplemental development campaign without API calls."""
import importlib.util
import json
from pathlib import Path
import shutil

from experiment4_progressive_support import normalize_contract

BASE = Path('/home/yangao')
ROOT = BASE / 'r2g_exp4_progressive_20260910'
OLD = BASE / 'r2g_exp4_compact_20260909'


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def main():
    if (ROOT / 'converter_development').exists():
        raise SystemExit('Development already exists: do not overwrite preparation')
    protocol = ROOT / 'protocol'
    protocol.mkdir(parents=True, exist_ok=True)
    old_cohort = json.loads((OLD / 'cohort.json').read_text())
    cohort = {'schema_version': old_cohort.get('schema_version'),
              'role': 'supplemental development; independent test not yet selected',
              'splits': {'development': old_cohort['splits']['development'],
                         'canary': old_cohort['splits'].get('canary', []), 'hidden_test': []}}
    save(ROOT / 'cohort.json', cohort)
    inputs = ROOT / 'inputs'
    inputs.mkdir(exist_ok=True)
    for row in cohort['splits']['development'] + cohort['splits']['canary']:
        path = inputs / row['task_id']
        if not path.exists():
            path.symlink_to((OLD / 'inputs' / row['task_id']).resolve(), target_is_directory=True)
    contract = normalize_contract(json.loads((OLD / 'protocol/public_contract_v2.json').read_text()))
    save(protocol / 'public_contract_v2.json', contract)
    # Publish the full contract on request but send a deduplicated compact view by default.
    prompt_contract = {k: v for k, v in contract.items() if k not in ('json_shapes', 'created_at', 'provenance')}
    graph_specs = prompt_contract.pop('graph_schemas')
    prompt_contract['route_graph_schema'] = graph_specs['stages/route/heterograph.pt']
    prompt_contract['base_graph_schema'] = graph_specs['base_graph/base_graph.pt']
    prompt_contract['stage_global_attributes'] = {s: graph_specs[f'stages/{s}/heterograph.pt']['global_attributes'] for s in contract['stages']}
    prompt_contract['stage_edge_types'] = {s: graph_specs[f'stages/{s}/heterograph.pt']['edge_types'] for s in contract['stages']}
    prompt_contract['json_shapes'] = {name: {'type': shape.get('type'),
        'required_top_level_keys': sorted(shape.get('properties', {}))}
        for name, shape in contract['json_shapes'].items()}
    # Exact CSV schemas are available; avoid repeating identical stage headers.
    csv_specs = prompt_contract.pop('csv_schemas')
    prompt_contract['csv_schemas'] = {k: v for k, v in csv_specs.items()
                                      if not k.startswith('stages/') or k.startswith('stages/route/')}
    prompt_contract['csv_stage_rule'] = 'Use the same headers for all four stages; values obey the stage cutoff.'
    save(protocol / 'compact_contract.json', prompt_contract)
    shutil.copy2(OLD / 'protocol/experiment4_model_routes.json', protocol / 'experiment4_model_routes.json')
    # Preserve existing transport and recovery helpers as a versioned local dependency.
    shutil.copy2(OLD / 'develop.py', ROOT / 'transport.py')
    save(protocol / 'limits.json', {'effective_tokens_per_model': 400000, 'calls_per_model': 12,
         'max_output_tokens': 16384, 'minimum_output_tokens': 2048,
         'case_timeout_seconds': 3600, 'model_parallelism': 1,
         'source_bytes': 200000, 'seed_from_prior_converters': False,
         'selection': 'semantic usable, identity F1, topology F1, label coverage, numeric coverage, static coverage',
         'test_gate': 'No test execution until independent source-disjoint cohort is validated',
         'budget_note': 'Keep 400k cap; allow more smaller incremental calls. This is a new protocol, not a replacement score.'})
    save(ROOT / 'preparation.json', {'status': 'development_scaffold_ready_test_not_selected',
        'original_results_unchanged': True, 'development_cases': len(cohort['splits']['development']),
        'old_contract_bytes': (OLD / 'protocol/public_contract_v2.json').stat().st_size,
        'compact_contract_bytes': len(json.dumps(prompt_contract, separators=(',', ':')).encode()),
        'remaining_gates': ['sandboxed converter smoke', 'development semantic referee canary',
                            'independent test cohort selection and clone audit']})
    print(ROOT)


if __name__ == '__main__':
    main()
