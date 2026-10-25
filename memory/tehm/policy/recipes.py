"""Context-bound recipe retrieval for the shared signoff executor."""
import json
import sys
from pathlib import Path

GROUP_KEYS = ('platform', 'check', 'violation_class')
RECIPE_KEYS = (*GROUP_KEYS, 'die_mode', 'util_band', 'timeout')


def recipe_strategies(path, sit, cfg, exclude=(), limit=2):
    # Executor contract (failure-patterns "Memory candidate broke the executor contract"):
    # `recheck` comes from the recipe's own recorded check; an unmappable check stays None
    # so the shared gate reports an invalid candidate instead of executing a guess.
    knowledge = Path(__file__).resolve().parents[3] / 'r2g-skills/signoff-loop/knowledge'
    if str(knowledge) not in sys.path:
        sys.path.insert(0, str(knowledge))
    from candidate_contract import recheck_from_evidence
    data = json.loads(Path(path).read_text())
    out = []
    for recipe in data['recipes']:
        if recipe['id'] in exclude or any(recipe['situation'].get(k) != sit.get(k) for k in RECIPE_KEYS):
            continue
        if all(str(cfg.get(k)) == str(v) for k, v in recipe['config_edits'].items()):
            continue
        out.append({**recipe, 'source': 'recipe', 'rule_id': recipe['id'], 'auto_apply': True,
                    'recheck': recheck_from_evidence(recipe['situation'].get('check')),
                    'candidate_attempt_authorized': True, 'memory_trial': True,
                    'transformation_family': 'SOURCE_CONCRETE_RECIPE',
                    'description': 'Verified SOURCE recipe; successful evidence count %d' % len(recipe['unit_ids'])})
        if len(out) >= limit:
            break
    return out
