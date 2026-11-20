#!/usr/bin/env python3
"""Select remaining recipe gaps, excluding demonstrated oversized designs."""
import collections
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    candidates = json.loads((HERE / 'candidates.json').read_text())
    h203 = json.loads((HERE / 'inventory_203.json').read_text())
    h216 = json.loads((HERE / 'inventory_216.json').read_text())
    selected, excluded = [], []
    for row in candidates['rows']:
        cid = row['id']
        a, b = h203['rows'][cid], h216['rows'][cid]
        projects = a['r2g_projects'] + b['r2g_projects']
        observed = []
        value = (b.get('baseline') or {}).get('cells')
        if isinstance(value, int):
            observed.append({'kind': 'baseline_synth_cells', 'value': value})
        for p in projects:
            for kind, value in [('r2g_sizing_cells', (p.get('detail') or {}).get('sizing', {}).get('cells')),
                                ('r2g_final_stdcell_count', (p['reports'].get('ppa.json') or {}).get('geometry', {}).get('stdcell_count'))]:
                if isinstance(value, int):
                    observed.append({'kind': kind, 'value': value})
        cells = max((x['value'] for x in observed), default=None)
        current = h203['live_recipe_reruns'].get(cid, {})
        if row['learned_recipe_success'] or current.get('dual_clean'):
            reason = 'existing_learned_recipe_verified_success'
        elif cid.endswith('C03956'):
            reason = 'known_64GiB_memory_bound'
        elif cells is not None and cells > 30000:
            reason = 'more_than_30000_cells'
        elif not a.get('input_exists'):
            reason = 'original_source_unavailable'
        elif not projects:
            reason = 'no_recorded_stop_project'
        else:
            reason = None
        evidence = {**row, 'cell_count_evidence': observed, 'max_observed_cells': cells,
                    'live_recipe_rerun': current}
        if reason:
            excluded.append({**evidence, 'exclusion': reason})
            continue
        project = max(projects, key=lambda p: p.get('updated', 0))
        symptoms = [r.get('symptom') for r in row['recipe_evidence'] if r.get('symptom')]
        symptom = symptoms[0] if symptoms else {}
        priority = ('1_known_manual_fix' if 'fixed' in (row['r2g_gpt_plus_fallback'], row['r2g_plus_fallback']) else
                    '3_recover_authentic_memory_assets' if symptom.get('class') == 'missing_mem_init_file' else
                    '2_diagnose_remaining_failure')
        selected.append({**evidence, 'candidate': a['candidate'], 'stop_host': project['host'],
                         'stop_project': project['path'], 'stop_detail': project.get('detail'),
                         'job_receipt': project.get('job_receipt'), 'baseline_dir': b.get('baseline_dir'),
                         'baseline_result': b.get('baseline'), 'priority': priority, 'symptom': symptom,
                         'size_status': 'measured_within_limit' if cells is not None else 'synthesis_failed_size_unknown'})
    result = {'scope': 676, 'original_recipe_failures': 107,
              'selection': 'No verified learned-recipe success in either model store or completed reruns; maximum recorded synth/final cell count <=30000; known 64GiB-bound cases excluded. Synthesis-failed designs with unknown cell count are retained and flagged, not assumed small.',
              'counts': dict(collections.Counter(r['platform'] for r in selected)),
              'excluded_counts': dict(collections.Counter(r['exclusion'] for r in excluded)),
              'selected': selected, 'excluded': excluded}
    (HERE / 'selection.json').write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(json.dumps({k: result[k] for k in ['counts', 'excluded_counts']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
