#!/usr/bin/env python3
"""Freeze reference API costs from recorded usage; never rewrite experiment results.

All figures use the same uncached, base-context rate convention. This is a
reference-price comparison, not a reconstruction of gateway invoices.
"""
from decimal import Decimal
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RATES = {
    'gpt': ('GPT-5.6-sol', '4', '20', 'https://developers.openai.com/api/docs/models/gpt-5.6-sol', 'Published promotional base rate, available at least through 2026-11-21.'),
    'claude': ('Claude Opus 5', '5', '25', 'https://platform.claude.com/docs/en/models/opus-5/whats-new-opus-5', 'Standard inference.'),
    'qwen': ('Qwen3.7-Max', '2.5', '7.5', 'https://www.alibabacloud.com/help/en/model-studio/qwen3-7-max', 'Singapore international list rate, used consistently across campaigns.'),
    'kimi': ('Kimi-K2.7-Code', '.95', '4', 'https://platform.kimi.ai/', 'Standard inference.'),
    'deepseek': ('DeepSeek-V4-Pro', '1.32', '3.96', 'https://api-docs.deepseek.com/quick_start/pricing/?push_animated=1&show_loading=0&theme=light&webview_progress_bar=1', 'Peak list rate; off-peak discounts not applied.'),
    'grok': ('grok-4.5', '2', '6', 'https://docs.x.ai/developers/pricing', 'Global base-context rate.'),
    'gemini': ('Gemini-3.1-Pro-Preview', '2', '12', 'https://ai.google.dev/gemini-api/docs/gemini-3', 'Standard base-context rate.'),
}

# Author-supplied breakdowns; acquisition output excludes the separate reasoning
# field, whereas repair/conversion completion counts already include reasoning.
ACQUISITION = {
    'gpt': (19813605, 171489, 0),
    'claude': (19716357, 256013, 0),
    'qwen': (19506155, 238341, 222612),
    'deepseek': (19092150, 357730, 527152),
    'gemini': (19827310, 109802, 46328),
    'kimi': (15407180, 479933, 230568),
    'grok': (19466839, 136555, 387653),
}
LEARNING = {'gpt': (28118, 3831), 'claude': (31312, 2351), 'qwen': (32045, 11579)}
REPAIR = {'gpt': (37251, 19741), 'claude': (34815, 15619), 'qwen': (39781, 60235)}
CONVERSION = {'gpt': (489086, 67351), 'claude': (504183, 83339), 'qwen': (345284, 225672)}
sources = {}


def read(path):
    sources[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return json.loads(path.read_text())


def verify_calls(paths, expected):
    usages = []
    for path in paths:
        data = read(path)
        if 'calls' in data:
            usages.extend(call['usage'] for call in data['calls'].values())
        else:
            usages.append(data['usage'])
    assert usages, paths
    inputs = [u.get('prompt_tokens') or u.get('input_tokens', 0) for u in usages]
    outputs = [u.get('completion_tokens') or u.get('output_tokens', 0) for u in usages]
    assert (sum(inputs), sum(outputs)) == expected, (paths, sum(inputs), sum(outputs))
    assert all(i + o == u['total_tokens'] for i, o, u in zip(inputs, outputs, usages))
    return {'calls': len(usages), 'max_input_tokens_per_call': max(inputs)}


def cost(model, inp, out, reasoning=0):
    rate = RATES[model]
    usd = (Decimal(inp) * Decimal(rate[1]) + Decimal(out + reasoning) * Decimal(rate[2])) / Decimal(1000000)
    return {'input_tokens': inp, 'output_tokens': out, 'separate_reasoning_tokens': reasoning,
            'total_tokens': inp + out + reasoning, 'billed_output_tokens': out + reasoning,
            'reference_api_usd': float(usd), 'display_usd': f'${usd:.2f}'}


result = {'rate_date': '2026-09-17', 'currency': 'USD',
          'convention': 'Uncached base-context reference prices; no context, regional or service-tier premiums; no cache, batch or off-peak discounts. Not actual gateway expenditure. Excludes EDA compute, engineering and unrecorded calls.',
          'pricing': {k: {'model': v[0], 'input_usd_per_million': v[1], 'output_usd_per_million': v[2], 'source_url': v[3], 'scope': v[4]} for k, v in RATES.items()},
          'acquisition': {}, 'repair_learning': {}, 'repair_deployment': {}, 'converter_development': {}}
archive = Path('/home/yangao/r2g_experiment1_final_archive_20260903/execution/methods')
for model, counts in ACQUISITION.items():
    # The author-confirmed acquisition breakdown is authoritative for grok-4.5.
    if model != 'grok':
        method = 'openai' if model == 'gpt' else model
        recorded = read(archive/f'{method}-vanilla/runner_state.json')['usage']
        assert tuple(recorded[k] for k in ['input', 'output', 'reasoning']) == counts
    result['acquisition'][model] = cost(model, *counts)
assert result['acquisition']['grok']['total_tokens'] == 19991047
result['acquisition']['r2g'] = {'reference_api_usd': 0, 'display_usd': '$0.00', 'total_tokens': 0}
state = Path('/home/yangao/r2g_exp3_formal_20260906/state')
dev = Path('/home/yangao/r2g_exp4_progressive_20260910/converter_development')
for model in LEARNING:
    for section, counts, paths in [
        ('repair_learning', LEARNING[model], [state/'a_learning/token_ledgers'/f'{model}.json']),
        ('repair_deployment', REPAIR[model], sorted((state/'pure_llm'/model).glob('*/token_ledger.json'))),
        ('converter_development', CONVERSION[model], sorted((dev/model).glob('round_*/received_response.json'))),
    ]:
        result[section][model] = {**cost(model, *counts), **verify_calls(paths, counts)}
    ledger = read(dev/model/'token_ledger.json')
    assert ledger['consumed'] == sum(CONVERSION[model])
for model in ['m0', 'm1', 'm2', 'm3', 'full_r2g']:
    result['repair_deployment'][model] = {'reference_api_usd': 0, 'display_usd': '$0.00', 'total_tokens': 0}
result['converter_development']['r2g'] = {'reference_api_usd': None, 'display_usd': 'n/a', 'scope': 'Historical tool development not metered in this campaign.'}
result['repair_learning_total_usd'] = sum(v['reference_api_usd'] for v in result['repair_learning'].values())
assert sum(v['total_tokens'] for v in result['repair_learning'].values()) == 109236
assert sum(v['total_tokens'] for v in result['repair_deployment'].values()) == 207442
result['sources_sha256'] = sources
(ROOT/'evidence/api_costs.json').write_text(json.dumps(result, indent=2)+'\n')
for section in ['acquisition', 'repair_learning', 'repair_deployment', 'converter_development']:
    print(section, {k: v['display_usd'] for k, v in result[section].items()})
print('Verified usage sources:', len(sources))
