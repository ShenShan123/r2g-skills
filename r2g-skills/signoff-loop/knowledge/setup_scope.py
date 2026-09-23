"""Bounded scope for the hierarchy-plus-placement timing development action."""
import math
import symptom

STRATEGY = 'hierarchical_place_timing_repair'
EDITS = {'SYNTH_HIERARCHICAL': '1', 'ENABLE_PLACE_REPAIR_TIMING': '1'}
SIGNATURE = symptom.canonical_signature('timing', 'setup_hier_place')
SYMPTOM_ID = symptom.symptom_id(SIGNATURE)
KEY = dict(symptom_id=SYMPTOM_ID, design_class='*', platform='sky130hd', strategy=STRATEGY)


def evidence(cfg, tcheck, routing_clean):
    wns = tcheck.get('wns_ns', tcheck.get('wns'))
    if (cfg.get('PLATFORM') != 'sky130hd' or not routing_clean
            or str(cfg.get('ABC_AREA', '0')).strip() != '1'
            or isinstance(wns, bool) or not isinstance(wns, (int, float))
            or not math.isfinite(wns) or not -3.0 <= wns < 0):
        return None
    return dict(platform='sky130hd', abc_area='1', routing_clean=True, wns_ns=wns)


def matches(strategy):
    e = strategy.get('setup_scope_evidence') or {}
    return (strategy.get('id') == STRATEGY and strategy.get('config_edits') == EDITS
            and not strategy.get('sdc_edits')
            and evidence({'PLATFORM': e.get('platform'), 'ABC_AREA': e.get('abc_area')},
                         {'wns_ns': e.get('wns_ns')}, e.get('routing_clean') is True) is not None)
