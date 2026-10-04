"""Supplemental E4 cold development with incremental edits and semantic feedback."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

from experiment4_progressive_support import source_response, semantic_feedback, rank

BASE = Path('/home/yangao')
ROOT = BASE / 'r2g_exp4_progressive_20260910'
RUNTIME = BASE / 'r2g_exp4_confirmatory_v3_20260909/runtime'
TOOLS = Path(__file__).parent
PY = BASE / '.conda/envs/gnn_env/bin/python'
spec = importlib.util.spec_from_file_location('transport', ROOT / 'transport.py')
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)

SYSTEM = '''Develop a real physical-design-to-graph converter. Read the files in the supplied config and derive entities, connections and physical values from them. Do not emit empty or fixed placeholder graphs. Do not specialize by design name. Use installed Yosys/OpenROAD (including subprocess) or Python parsers; network and R2G source are inaccessible. Write only output_dir. Native tools are optional, not a supplied solution.
Interface: python converter.py --config CONFIG. Build all four stage products from the public contract. Preserve stage feature cutoff; final route/SPEF/STA are label sources, not early-stage features. NaN/mask denotes genuinely absent data, not a replacement for extracting available values.
Work in order: (1) actual identities and core incidence, (2) physical coordinates and labels, (3) stage alignment and complete packaging. You receive development-only semantic coverage and diagnostics; no test feedback. Missing identity blocks dependent checks and is not an independent numerical error.
Return one JSON object: {"summary":"...","python_source":"complete initial program"} OR {"summary":"...","edits":[{"old":"exact unique source substring","new":"replacement"}]}. Markdown fences are accepted. Edits apply in order, atomically. Prefer short incremental edits after the initial implementation. All three models have the same limits. Do not claim success without actual input-derived data.'''


def measure(method, converter):
    common = [str(PY), str(TOOLS / 'run_experiment4_progressive_referee.py'), '--cohort', str(ROOT / 'cohort.json'),
              '--campaign-root', str(ROOT), '--runtime-root', str(RUNTIME), '--python', str(PY)]
    for phase in ('run', 'evaluate'):
        cmd = common + [phase, '--split', 'development', '--method', method, '--workers', '1']
        if phase == 'run':
            cmd += ['--converter', str(converter)]
        completed = subprocess.run(cmd)
        if phase == 'run':
            for log in (ROOT / 'methods' / method).glob('*/logs/converter.log'):
                if log.read_text(errors='replace').startswith('bwrap:'):
                    raise RuntimeError('Sandbox launch failed; infrastructure error, not a model score')
        if completed.returncode and phase != 'run':
            raise RuntimeError('Native referee failed; resume without another model request')
    output = ROOT / 'reports' / ('semantic-' + method)
    toolchain = BASE / 'r2g_toolchain/OpenROAD-flow-scripts/tools/install'
    subprocess.run([str(PY), str(RUNTIME / 'experiments/experiment4_semantic_audit.py'), '--campaign', str(ROOT),
        '--output', str(output), '--split', 'development', '--methods', method,
        '--yosys', str(toolchain / 'yosys/bin/yosys'), '--openroad', str(toolchain / 'OpenROAD/bin/openroad'),
        '--study-role', 'supplemental development feedback only'], check=True)
    summary = t.read_json(output / 'summary.json')
    if any(r.get('status') == 'ORACLE_ERROR' for r in summary['rows']):
        raise RuntimeError('Oracle failed; do not charge this as model failure')
    cohort = t.read_json(ROOT / 'cohort.json')['splits']['development']
    cases = [t.read_json(output / 'cases' / method / (row['task_id'] + '.json')) for row in cohort]
    feedback = semantic_feedback(cases)
    native = t.feedback_for(ROOT, method, t.read_json(ROOT / 'cohort.json'))
    feedback['native'] = native
    a = native['aggregate']
    fraction = a['static_contract_checks_passed'] / max(1, a['static_contract_checks_total'])
    return feedback, rank(feedback, fraction)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--model', choices=['gpt', 'claude', 'qwen'], required=True)
    p.add_argument('--canary', action='store_true', help='No API: audit a historical program on development cases')
    args = p.parse_args()
    if args.canary:
        f, r = measure('canary-progressive-gpt', BASE / 'r2g_exp4_compact_20260909/converter_development/gpt/frozen_converter.py')
        t.write_json(ROOT / 'canary_feedback.json', {'feedback': f, 'rank': r})
        print('Canary complete:', r)
        return
    ready = ROOT / 'preflight_passed.json'
    if not ready.exists():
        raise SystemExit('Preflight not passed: no API call made')
    t.load_env_file(BASE / '.config/r2g/experiment1_api.env')
    route = t.route_by_key(ROOT / 'protocol/experiment4_model_routes.json', args.model)
    limits = t.read_json(ROOT / 'protocol/limits.json')
    contract = t.read_json(ROOT / 'protocol/compact_contract.json')
    work = ROOT / 'converter_development' / args.model
    work.mkdir(parents=True, exist_ok=True)
    previous, feedback, ranked, consumed = '', None, [], 0
    for number in range(limits['calls_per_model']):
        rd = work / f'round_{number}'
        rd.mkdir(exist_ok=True)
        receipt = rd / 'received_response.json'
        payload = {'round': number, 'contract': contract, 'previous_source': previous,
                   'development_feedback': feedback}
        user = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        pending = rd / 'request.json'
        if pending.exists():
            saved = t.read_json(pending)
            if saved['user'] != user or saved['system'] != SYSTEM:
                raise RuntimeError('Resume prompt mismatch; refuse silent request change')
            max_tokens = saved['max_tokens']
        else:
            estimate = (len(user.encode()) + len(SYSTEM.encode())) // 3 + 1024
            max_tokens = min(limits['max_output_tokens'], limits['effective_tokens_per_model'] - consumed - estimate)
            if max_tokens < limits['minimum_output_tokens']:
                break
            t.write_json(pending, {'user': user, 'system': SYSTEM, 'max_tokens': max_tokens})
        raw, usage = t.call_model(route, SYSTEM, user, max_tokens, retries=5, checkpoint=receipt)
        charged = t.token_count(usage, (len(user.encode()) + len(SYSTEM.encode())) // 3 + 1024 + max_tokens)
        consumed += charged
        t.write_json(work / 'token_ledger.json', {'consumed': consumed, 'completed_responses': number + 1,
                     'effective_token_limit': limits['effective_tokens_per_model'], 'last_usage': usage})
        result_path = rd / 'result.json'
        if result_path.exists():
            result = t.read_json(result_path)
            feedback = result['feedback']
            if result.get('source_sha256'):
                converter = rd / 'converter.py'
                if t.sha256_file(converter) != result['source_sha256']:
                    raise RuntimeError('Saved source digest changed')
                previous = converter.read_text()
                ranked.append(result)
        else:
            try:
                new_source = source_response(raw, previous)
            except (ValueError, SyntaxError, TypeError) as exc:
                feedback = {'edit_error': str(exc), 'previous_source_preserved': True}
                t.write_json(result_path, {'feedback': feedback})
                continue
            converter = rd / 'converter.py'
            converter.write_text(new_source)
            method = f'llm-{args.model}-progressive-{number}'
            feedback, score = measure(method, converter)
            result = {'round': number, 'converter': str(converter), 'source_sha256': t.sha256_file(converter),
                      'feedback': feedback, 'rank': score}
            t.write_json(result_path, result)
            previous = new_source
            ranked.append(result)
        if feedback.get('semantic_passes') == feedback.get('total_cases') and feedback.get('total_cases', 0) > 0:
            if feedback['native']['aggregate']['all_strict_pass']:
                break
        if consumed >= limits['effective_tokens_per_model']:
            break
    if ranked:
        best = max(ranked, key=lambda r: tuple(r['rank']))
        shutil.copy2(best['converter'], work / 'frozen_converter.py')
        t.write_json(work / 'development_complete.json', {'selected': best, 'consumed': consumed,
                     'test_started': False, 'note': 'Await independent-test readiness check'})
    else:
        t.write_json(work / 'development_complete.json', {'no_valid_converter': True, 'consumed': consumed})


if __name__ == '__main__':
    main()
