"""Conditional source-group precision planning, never final-data admission.

Hoeffding (1963), Theorem 2, applied to independent bounded group effects.
For K simultaneous contrasts use a union bound; dependence between contrasts
is allowed, but dependence between sampled source groups is not. These are
sufficient worst-case precision budgets, not necessary N or power estimates.
No observed outcomes enter the planning calculation.
"""
from __future__ import annotations

import json
import math


def positive_real(value, name):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError(name)


def positive_int(value, name):
    if type(value) is not int or value <= 0:
        raise ValueError(name)


def parameters(span, alpha, contrasts):
    positive_real(span, 'range span')
    positive_real(alpha, 'family error probability')
    positive_int(contrasts, 'contrast count')
    if alpha >= 1:
        raise ValueError('alpha must be below one')


def sufficient_groups(half_width, *, span=2.0, alpha=0.05, contrasts=3):
    parameters(span, alpha, contrasts)
    positive_real(half_width, 'half width')
    if half_width >= span:
        raise ValueError('choose a nontrivial half width below range span')
    amount = span * span * math.log(2 * contrasts / alpha) / (2 * half_width * half_width)
    if not math.isfinite(amount) or amount > 10**9:
        raise ValueError('unsupported planning scale')
    return math.ceil(amount)


def radius(groups, *, span=2.0, alpha=0.05, contrasts=3):
    parameters(span, alpha, contrasts)
    positive_int(groups, 'independent source groups')
    return span * math.sqrt(math.log(2 * contrasts / alpha) / (2 * groups))


def empirical_interval(*, mean, groups, independent_sampling_verified,
                       target_population_sampling_verified, prospectively_fixed,
                       lower=-1.0, upper=1.0, alpha=0.05, contrasts=3):
    """Fail closed: source labels/clone counts alone cannot enable inference.

    Boolean attestations are caller evidence obligations, not evidence created
    by this function. Current R5 data does not satisfy them.
    """
    if any(x is not True for x in (independent_sampling_verified,
            target_population_sampling_verified, prospectively_fixed)):
        raise ValueError('empirical inference assumptions unverified')
    if (type(mean) not in (int, float) or not math.isfinite(mean)
            or type(lower) not in (int, float) or type(upper) not in (int, float)
            or not math.isfinite(lower) or not math.isfinite(upper)
            or not lower <= mean <= upper or lower >= upper):
        raise ValueError('invalid effect or range')
    error = radius(groups, span=upper-lower, alpha=alpha, contrasts=contrasts)
    return [max(lower, mean-error), min(upper, mean+error)]


def planning_scenarios():
    widths = (0.20, 0.15, 0.10, 0.05)
    return {'schema': 'r5-source-group-precision-scenarios-v1',
        'role': 'CONDITIONAL_PLANNING_NOT_EMPIRICAL_INFERENCE',
        'estimand': 'equal_weight_mean_of_source_group_paired_repair_rate_differences',
        'group_effect_range': [-1, 1], 'family_alpha': 0.05,
        'contrasts': ['m-plus_vs_m-minus', 'm-plus_vs_mremove', 'm-plus_vs_transform-only'],
        'formula': 'ceil(span^2 * ln(2*K/alpha) / (2*half_width^2))',
        'rows': [{'half_width': w,
                  'sufficient_independent_groups_one_contrast': sufficient_groups(w, contrasts=1),
                  'sufficient_independent_groups_three_contrasts': sufficient_groups(w)} for w in widths],
        'eight_independent_groups_hypothetical_radius': radius(8),
        'assumptions': ['independent source-group draws',
            'sampling supports the declared target population',
            'equal group weights and fixed within-group task rules',
            'fixed sample size and comparisons before observing final outcomes'],
        'not_counted_as_independent_groups': ['testcases', 'seeds', 'parameters', 'memory views',
            'cold replays', 'copied or forked mechanism leaves'],
        'applies_to_current_purposive_corpus': False,
        'current_empirical_interval': None, 'power_claim': None,
        'minimum_necessary_sample_size_claim': None,
        'selected_target_precision': None, 'new_tasks_authorized': 0,
        'model_calls_authorized': 0, 'final_test_ready': False,
        'reference': 'https://www.cs.rpi.edu/academics/courses/spring06/random/hoefding.pdf'}


if __name__ == '__main__':
    print(json.dumps(planning_scenarios(), indent=2, sort_keys=True))
