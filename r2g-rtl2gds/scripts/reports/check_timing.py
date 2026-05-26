#!/usr/bin/env python3
"""
Post-backend timing gate with tiered response based on WNS and TNS.

Reads reports/ppa.json and classifies timing into tiers based on the WORSE
of the WNS tier and the TNS tier:
  clean        (WNS >= 0,  TNS >= 0)         — proceed to signoff
  minor        (WNS >= -2, TNS >= -10)        — agent auto-fixes
  moderate     (WNS >= -5, TNS >= -100)       — stop, present options
  severe       (WNS < -5  OR TNS < -100)      — stop, strong warning
  unconstrained (WNS > 1e+30)                 — stop, SDC config error

The combined tier = max(wns_tier, tns_tier) where severity ordering is:
  clean < minor < moderate < severe < unconstrained

Writes structured result to reports/timing_check.json.

Exit codes:
  0 — proceed (clean or minor auto-fixable)
  1 — user decision needed (moderate / severe / unconstrained)
  2 — usage error or missing data
"""
import json
import math
import re
import sys
from pathlib import Path

# Severity ordering for tier comparison
TIER_ORDER = {'clean': 0, 'minor': 1, 'moderate': 2, 'severe': 3, 'unconstrained': 4}


def worse_tier(a: str, b: str) -> str:
    """Return the more severe of two tiers."""
    return a if TIER_ORDER.get(a, 0) >= TIER_ORDER.get(b, 0) else b


def classify_wns(wns: float, moderate_thr: float, severe_thr: float) -> str:
    """Classify WNS into a tier."""
    if wns > 1e+30:
        return 'unconstrained'
    if wns < severe_thr:
        return 'severe'
    if wns < moderate_thr:
        return 'moderate'
    if wns < 0:
        return 'minor'
    return 'clean'


def classify_tns(tns: float, moderate_thr: float, severe_thr: float) -> str:
    """Classify TNS into a tier."""
    if tns < severe_thr:
        return 'severe'
    if tns < moderate_thr:
        return 'moderate'
    if tns < 0:
        return 'minor'
    return 'clean'


def read_clock_period(project: Path) -> float | None:
    """Read clock period from constraint.sdc."""
    sdc_file = project / 'constraints' / 'constraint.sdc'
    if not sdc_file.exists():
        return None
    sdc_text = sdc_file.read_text(encoding='utf-8', errors='ignore')
    m = re.search(r'set\s+clk_period\s+([\d.]+)', sdc_text)
    return float(m.group(1)) if m else None


def read_core_utilization(project: Path) -> float | None:
    """Read CORE_UTILIZATION from config.mk."""
    config_file = project / 'constraints' / 'config.mk'
    if not config_file.exists():
        return None
    for line in config_file.read_text(encoding='utf-8', errors='ignore').splitlines():
        m = re.match(r'export\s+CORE_UTILIZATION\s*=\s*([\d.]+)', line)
        if m:
            return float(m.group(1))
    return None


def build_options_moderate(wns: float, tns: float, violation_count,
                           clock_period: float | None,
                           utilization: float | None,
                           wns_tier: str, tns_tier: str) -> list[dict]:
    """Build numbered fix options for moderate timing violations."""
    options = []
    if clock_period and wns < 0:
        new_period = math.ceil((clock_period + abs(wns) * 1.5) * 2) / 2
        options.append({
            'number': len(options) + 1,
            'action': 'increase_clock_period',
            'description': f'Increase clock period from {clock_period} ns to {new_period} ns '
                           f'(+{new_period - clock_period:.1f} ns) and re-run backend',
            'new_value': new_period,
            'risk': 'low — conservative, reduces target frequency',
        })
    if utilization and utilization > 15:
        new_util = max(10, utilization - 10)
        options.append({
            'number': len(options) + 1,
            'action': 'reduce_utilization',
            'description': f'Reduce CORE_UTILIZATION from {utilization}% to {new_util}% '
                           f'and re-run backend',
            'new_value': new_util,
            'risk': 'low — gives placer more freedom, increases die area',
        })
    if clock_period and wns < 0 and utilization and utilization > 15:
        new_period = math.ceil((clock_period + abs(wns)) * 2) / 2
        new_util = max(10, utilization - 5)
        options.append({
            'number': len(options) + 1,
            'action': 'adjust_both',
            'description': f'Increase clock period to {new_period} ns AND '
                           f'reduce utilization to {new_util}%, re-run backend',
            'new_value': {'clock_period': new_period, 'utilization': new_util},
            'risk': 'low — balanced approach',
        })
    options.append({
        'number': len(options) + 1,
        'action': 'accept_and_proceed',
        'description': 'Accept timing violations and proceed to signoff anyway',
        'risk': 'high — chip will not meet target frequency',
    })
    options.append({
        'number': len(options) + 1,
        'action': 'stop_and_restructure',
        'description': 'Stop flow. Restructure RTL to shorten critical paths.',
        'risk': 'none — no further resources spent until design is fixed',
    })
    return options


def build_options_severe(wns: float, tns: float, violation_count,
                         clock_period: float | None,
                         utilization: float | None,
                         wns_tier: str, tns_tier: str) -> list[dict]:
    """Build numbered fix options for severe timing violations."""
    options = []
    if clock_period and wns < 0:
        new_period = math.ceil((clock_period + abs(wns) * 2.0) * 2) / 2
        options.append({
            'number': len(options) + 1,
            'action': 'increase_clock_period',
            'description': f'Significantly increase clock period from {clock_period} ns '
                           f'to {new_period} ns (+{new_period - clock_period:.1f} ns) '
                           f'and re-run backend',
            'new_value': new_period,
            'risk': 'medium — large frequency reduction, may not meet system requirements',
        })
    if utilization and utilization > 15:
        new_util = max(10, utilization - 15)
        options.append({
            'number': len(options) + 1,
            'action': 'reduce_utilization',
            'description': f'Reduce CORE_UTILIZATION from {utilization}% to {new_util}% '
                           f'and re-run backend',
            'new_value': new_util,
            'risk': 'low — gives placer much more freedom, significantly increases die area',
        })
    options.append({
        'number': len(options) + 1,
        'action': 'accept_and_proceed',
        'description': 'Accept timing violations and proceed to signoff anyway '
                       '(WARNING: chip will NOT work at target frequency)',
        'risk': 'very high — non-functional at target frequency',
    })
    options.append({
        'number': len(options) + 1,
        'action': 'stop_and_restructure',
        'description': 'Stop flow. Restructure RTL or change target frequency. (RECOMMENDED)',
        'risk': 'none — prevents wasting signoff time on a broken design',
    })
    return options


def build_options_unconstrained(clock_period: float | None) -> list[dict]:
    """Build fix options for unconstrained timing (SDC mismatch)."""
    return [
        {
            'number': 1,
            'action': 'fix_sdc_and_rerun',
            'description': 'Run validate_config.py to find the SDC/RTL clock port mismatch, '
                           'fix constraint.sdc, re-run synthesis and backend',
            'risk': 'none — this is always the right fix',
        },
        {
            'number': 2,
            'action': 'stop',
            'description': 'Stop flow entirely. The GDS is non-functional without timing constraints.',
            'risk': 'none',
        },
    ]


def format_timing_summary(wns, tns, violation_count, clock_period,
                          wns_tier, tns_tier) -> str:
    """Format a human-readable timing summary line."""
    parts = [f'WNS = {wns:.4f} ns [{wns_tier}]']
    if isinstance(tns, (int, float)):
        parts.append(f'TNS = {tns:.4f} ns [{tns_tier}]')
    else:
        parts.append(f'TNS = {tns}')
    if violation_count != 'N/A':
        parts.append(f'Violations = {violation_count}')
    if clock_period:
        pct = abs(wns) / clock_period * 100 if wns < 0 else 0
        parts.append(f'Clock = {clock_period} ns')
        if pct > 0:
            parts.append(f'WNS is {pct:.1f}% of period')
    return '  ' + ', '.join(parts)


def main():
    if len(sys.argv) < 2:
        print('usage: check_timing.py <project-dir> [--wns-threshold <ns>] [--tns-threshold <ns>]',
              file=sys.stderr)
        sys.exit(2)

    project = Path(sys.argv[1])
    ppa_file = project / 'reports' / 'ppa.json'
    out_file = project / 'reports' / 'timing_check.json'

    # WNS thresholds
    wns_moderate = -2.0
    wns_severe = -5.0
    # TNS thresholds
    tns_moderate = -10.0
    tns_severe = -100.0

    # Parse optional overrides
    args = sys.argv[2:]
    i = 0
    while i < len(args):
        if args[i] == '--wns-threshold' and i + 1 < len(args):
            try:
                wns_moderate = float(args[i + 1])
                wns_severe = wns_moderate * 2.5
            except ValueError:
                print(f'ERROR: invalid --wns-threshold: {args[i + 1]}', file=sys.stderr)
                sys.exit(2)
            i += 2
        elif args[i] == '--tns-threshold' and i + 1 < len(args):
            try:
                tns_moderate = float(args[i + 1])
                tns_severe = tns_moderate * 10.0
            except ValueError:
                print(f'ERROR: invalid --tns-threshold: {args[i + 1]}', file=sys.stderr)
                sys.exit(2)
            i += 2
        else:
            i += 1

    if not ppa_file.exists():
        print(f'WARNING: {ppa_file} not found. Run extract_ppa.py first.', file=sys.stderr)
        sys.exit(2)

    try:
        ppa = json.loads(ppa_file.read_text(encoding='utf-8', errors='ignore'))
    except json.JSONDecodeError as e:
        print(f'ERROR: failed to parse {ppa_file}: {e}', file=sys.stderr)
        sys.exit(2)

    timing = ppa.get('summary', {}).get('timing', {})
    wns = timing.get('setup_wns')
    tns_raw = timing.get('setup_tns')
    violation_count = timing.get('setup_violation_count', 'N/A')
    hold_wns = timing.get('hold_wns')
    hold_tns = timing.get('hold_tns')

    # Handle missing WNS
    if wns is None or not isinstance(wns, (int, float)):
        result = {
            'tier': 'unknown', 'wns': None, 'tns': None,
            'wns_tier': 'unknown', 'tns_tier': 'unknown',
            'message': 'No setup_wns found in ppa.json. Timing data may be missing.',
            'options': [
                {'number': 1, 'action': 'proceed_anyway',
                 'description': 'Proceed to signoff (the GDS may be non-functional)',
                 'risk': 'unknown'},
                {'number': 2, 'action': 'stop',
                 'description': 'Stop and investigate why timing data is missing',
                 'risk': 'none'},
            ],
        }
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(json.dumps(result, indent=2), encoding='utf-8')
        print('TIMING GATE: No timing data available.')
        print('DECISION NEEDED — choose an option:')
        for opt in result['options']:
            print(f"  [{opt['number']}] {opt['description']} (risk: {opt['risk']})")
        sys.exit(1)

    clock_period = read_clock_period(project)
    utilization = read_core_utilization(project)

    # Classify WNS and TNS independently
    wns_tier = classify_wns(wns, wns_moderate, wns_severe)
    tns = tns_raw if isinstance(tns_raw, (int, float)) else 0.0
    tns_tier = classify_tns(tns, tns_moderate, tns_severe) if isinstance(tns_raw, (int, float)) else 'clean'

    # Combined tier = worse of the two (except unconstrained is WNS-only)
    if wns_tier == 'unconstrained':
        combined_tier = 'unconstrained'
    else:
        combined_tier = worse_tier(wns_tier, tns_tier)

    tns_display = f'{tns:.4f}' if isinstance(tns_raw, (int, float)) else 'N/A'

    result = {
        'wns': wns,
        'tns': tns_raw,
        'wns_tier': wns_tier,
        'tns_tier': tns_tier,
        'tier': combined_tier,
        'violation_count': violation_count,
        'clock_period': clock_period,
        'utilization': utilization,
        'hold_wns': hold_wns,
        'hold_tns': hold_tns,
        'thresholds': {
            'wns_moderate': wns_moderate,
            'wns_severe': wns_severe,
            'tns_moderate': tns_moderate,
            'tns_severe': tns_severe,
        },
    }

    summary_line = format_timing_summary(
        wns, tns_display, violation_count, clock_period, wns_tier, tns_tier)

    # --- Tier: UNCONSTRAINED ---
    if combined_tier == 'unconstrained':
        result['auto_fixable'] = False
        result['options'] = build_options_unconstrained(clock_period)
        result['message'] = (
            f'Unconstrained timing detected (WNS = {wns}). '
            f'SDC clock port does not match RTL — the GDS is non-functional.'
        )
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(json.dumps(result, indent=2), encoding='utf-8')

        print(f'TIMING GATE FAILED [UNCONSTRAINED]')
        print(summary_line)
        print()
        print('Root cause: SDC clock port name does not match RTL port.')
        print('The entire backend ran without timing constraints.')
        print()
        print('DECISION NEEDED — choose an option:')
        for opt in result['options']:
            print(f"  [{opt['number']}] {opt['description']} (risk: {opt['risk']})")
        sys.exit(1)

    # --- Tier: SEVERE ---
    if combined_tier == 'severe':
        result['auto_fixable'] = False
        result['options'] = build_options_severe(
            wns, tns, violation_count, clock_period, utilization, wns_tier, tns_tier)
        escalation = []
        if wns_tier == 'severe':
            escalation.append(f'WNS={wns:.4f}ns is below {wns_severe}ns threshold')
        if tns_tier == 'severe':
            escalation.append(f'TNS={tns_display}ns is below {tns_severe}ns threshold')
        result['message'] = (
            f'Severe timing violations. {"; ".join(escalation)}. '
            f'The chip will NOT work at the target frequency.'
        )
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(json.dumps(result, indent=2), encoding='utf-8')

        print(f'TIMING GATE FAILED [SEVERE] (wns_tier={wns_tier}, tns_tier={tns_tier})')
        print(summary_line)
        if wns_tier != tns_tier:
            print(f'  Note: tier escalated by {"TNS" if tns_tier == "severe" else "WNS"}')
        print()
        print('DECISION NEEDED — choose an option:')
        for opt in result['options']:
            print(f"  [{opt['number']}] {opt['description']} (risk: {opt['risk']})")
        sys.exit(1)

    # --- Tier: MODERATE ---
    if combined_tier == 'moderate':
        result['auto_fixable'] = False
        result['options'] = build_options_moderate(
            wns, tns, violation_count, clock_period, utilization, wns_tier, tns_tier)
        escalation = []
        if wns_tier in ('moderate', 'severe'):
            escalation.append(f'WNS={wns:.4f}ns')
        if tns_tier in ('moderate', 'severe'):
            escalation.append(f'TNS={tns_display}ns')
        result['message'] = (
            f'Moderate timing violations ({", ".join(escalation)}). '
            f'Timing is not closed — user decision required.'
        )
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(json.dumps(result, indent=2), encoding='utf-8')

        print(f'TIMING GATE FAILED [MODERATE] (wns_tier={wns_tier}, tns_tier={tns_tier})')
        print(summary_line)
        if wns_tier != tns_tier:
            print(f'  Note: tier escalated by {"TNS" if TIER_ORDER[tns_tier] > TIER_ORDER[wns_tier] else "WNS"}')
        print()
        print('DECISION NEEDED — choose an option:')
        for opt in result['options']:
            print(f"  [{opt['number']}] {opt['description']} (risk: {opt['risk']})")
        sys.exit(1)

    # --- Tier: MINOR ---
    if combined_tier == 'minor':
        new_period = None
        if clock_period:
            new_period = math.ceil((clock_period + abs(wns) + 1.0) * 2) / 2
        result['auto_fixable'] = True
        result['suggested_clock_period'] = new_period
        result['message'] = (
            f'Minor timing violations (WNS={wns:.4f}ns [{wns_tier}], '
            f'TNS={tns_display}ns [{tns_tier}]). '
            f'Auto-fix: increase clock period '
            f'from {clock_period}ns to {new_period}ns and re-run backend.'
            if clock_period and new_period else
            f'Minor timing violations (WNS={wns:.4f}ns, TNS={tns_display}ns). '
            f'Auto-fix: increase clock period and re-run backend.'
        )
        result['options'] = []
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(json.dumps(result, indent=2), encoding='utf-8')

        print(f'TIMING GATE [MINOR] (wns_tier={wns_tier}, tns_tier={tns_tier}) — auto-fixable')
        print(summary_line)
        if new_period and clock_period:
            print(f'  Suggested fix: increase clock period '
                  f'{clock_period}ns -> {new_period}ns (+{new_period - clock_period:.1f}ns)')
        print(f'  Agent should apply fix and re-run backend.')
        sys.exit(0)

    # --- Tier: CLEAN ---
    result['auto_fixable'] = False
    result['options'] = []
    result['message'] = f'Timing clean. WNS={wns:.4f}ns, TNS={tns_display}ns.'
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(result, indent=2), encoding='utf-8')

    print(f'TIMING GATE PASSED [CLEAN]')
    print(summary_line)
    if hold_wns is not None and isinstance(hold_wns, (int, float)) and hold_wns < 0:
        hold_tns_val = f', hold_tns={hold_tns:.4f}ns' if isinstance(hold_tns, (int, float)) else ''
        print(f'  Note: Hold violations present (hold_wns={hold_wns:.4f}ns{hold_tns_val}) — '
              f'not blocking, but worth reviewing.')
    sys.exit(0)


if __name__ == '__main__':
    main()
