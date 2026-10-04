"""Development-only scoring and atomic incremental source edits for E4."""
from __future__ import annotations

import ast
import copy
import json


def normalize_contract(contract):
    """Remove reference-design dimensions from an otherwise unchanged schema."""
    value = copy.deepcopy(contract)
    def visit(node):
        if isinstance(node, dict):
            for key, spec in node.items():
                if key == 'edge_index' and isinstance(spec, dict) and spec.get('kind') == 'tensor':
                    spec.pop('trailing_shape', None)
                    spec['shape'] = [2, 'E']
                if key == 'io_pin_directions' and isinstance(spec, dict):
                    spec.pop('required_value', None)
                    spec['items'] = 'design-dependent port direction'
                visit(spec)
        elif isinstance(node, list):
            for item in node:
                visit(item)
    visit(value)
    return value


def source_response(text, previous):
    """Apply a complete source or exact-match edits; never partially apply edits."""
    raw = text.strip()
    if raw.startswith('```') and raw.endswith('```'):
        raw = raw.split('\n', 1)[1].rsplit('```', 1)[0].strip()
    obj = json.loads(raw)
    if not isinstance(obj, dict) or ('python_source' in obj) == ('edits' in obj):
        raise ValueError('Supply exactly one of python_source or edits, plus optional summary')
    if set(obj) - {'python_source', 'edits', 'summary'}:
        raise ValueError('Unexpected response fields')
    if 'python_source' in obj:
        source = obj['python_source']
    else:
        if not previous or not isinstance(obj['edits'], list) or not obj['edits']:
            raise ValueError('Edits require an existing source and nonempty edits list')
        source = previous
        for edit in obj['edits']:
            if not isinstance(edit, dict) or set(edit) != {'old', 'new'}:
                raise ValueError('Each edit needs old and new strings')
            if not all(isinstance(edit[k], str) for k in ('old', 'new')):
                raise ValueError('Edit values must be strings')
            if not edit['old'] or source.count(edit['old']) != 1:
                raise ValueError('old must match exactly once; previous source is preserved')
            source = source.replace(edit['old'], edit['new'], 1)
    if not isinstance(source, str) or not source.strip() or len(source.encode()) > 200000:
        raise ValueError('Source must be nonempty and at most 200000 bytes')
    ast.parse(source)
    return source


def semantic_feedback(cases):
    """Identity/edge F1 and physical coverage precede static packaging in ranking."""
    result = {'cases': [], 'semantic_passes': 0}
    sums = {key: [] for key in ('identity', 'topology', 'numeric', 'label')}
    for case in cases:
        if case.get('status') == 'ORACLE_ERROR':
            raise RuntimeError('Oracle failure is infrastructure, not model feedback')
        checks = case.get('checks') or []
        diagnostic = []
        for check in checks:
            group = check['group']
            ev = check.get('evidence') or {}
            if group in ('identity', 'topology'):
                score = float(ev.get('f1', 0))
                # Empty expected AND actual sets are inapplicable, not positive coverage.
                if sum(ev.get(k, 0) for k in ('tp', 'fp', 'fn')):
                    sums[group].append(score)
                elif check['status'] != 'PASS':
                    sums[group].append(0)
            if group in ('numeric', 'label'):
                expected = ev.get('expected', ev.get('report_expected_labels', 0))
                if expected:
                    correct = max(0, ev.get('matched', 0) - ev.get('incorrect', 0))
                    sums[group].append(min(1, correct / expected))
                elif check['status'] not in ('PASS', 'NOT_APPLICABLE'):
                    sums[group].append(0)
            if check['status'] != 'PASS':
                diagnostic.append({k: check[k] for k in ('stage', 'group', 'check', 'status', 'evidence')})
        passed = case.get('verified_core_status') == 'PASS'
        result['semantic_passes'] += int(passed)
        # Round-robin groups avoids a wall of identity failures hiding label omissions.
        compact = []
        for group in ('identity', 'topology', 'numeric', 'label', 'mask', 'alignment', 'causal'):
            compact.extend([c for c in diagnostic if c['group'] == group][:2])
        result['cases'].append({'task': case.get('task'), 'semantic_pass': passed,
                                'diagnostics': compact})
    result['coverage'] = {k: sum(v) / len(v) if v else 0 for k, v in sums.items()}
    result['total_cases'] = len(cases)
    return result


def rank(feedback, static_fraction=0):
    coverage = feedback['coverage']
    return (feedback['semantic_passes'], coverage['identity'], coverage['topology'],
            coverage['label'], coverage['numeric'], static_fraction)
