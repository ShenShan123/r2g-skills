#!/usr/bin/env python3
"""
Extract PPA metrics from ORFS report files.
Reads reports from the ORFS results/logs directories.
"""
from pathlib import Path
import json
import re
import sys


def parse_area_report(text: str) -> dict:
    """Parse OpenROAD area report."""
    area = {}
    for line in text.splitlines():
        if 'Design area' in line:
            m = re.search(r'Design area\s+([\d.]+)\s+u\^2\s+([\d.]+)%\s+utilization', line)
            if m:
                area['design_area_um2'] = float(m.group(1))
                area['utilization'] = float(m.group(2)) / 100.0
        if 'Total cell area' in line or 'total cell area' in line.lower():
            m = re.search(r'([\d.]+)', line)
            if m:
                area['total_cell_area'] = float(m.group(1))
    return area


def parse_timing_report(text: str) -> dict:
    """Parse OpenSTA timing report for WNS/TNS."""
    timing = {}
    # Look for worst negative slack
    for line in text.splitlines():
        if 'wns' in line.lower():
            m = re.search(r'wns\s+([-\d.]+)', line, re.I)
            if m:
                timing['setup_wns'] = float(m.group(1))
        if 'tns' in line.lower():
            m = re.search(r'tns\s+([-\d.]+)', line, re.I)
            if m:
                timing['setup_tns'] = float(m.group(1))
        if 'slack' in line.lower() and 'MET' in line:
            m = re.search(r'slack\s*\(MET\)\s*([-\d.]+)', line)
            if m:
                timing.setdefault('setup_wns', float(m.group(1)))
        if 'slack' in line.lower() and 'VIOLATED' in line:
            m = re.search(r'slack\s*\(VIOLATED\)\s*([-\d.]+)', line)
            if m:
                timing['setup_wns'] = float(m.group(1))
    return timing


def parse_power_report(text: str) -> dict:
    """Parse OpenROAD power report."""
    power = {}
    for line in text.splitlines():
        if 'Total' in line and ('W' in line or 'mW' in line or 'uW' in line):
            parts = line.split()
            for i, p in enumerate(parts):
                try:
                    val = float(p)
                    # Check if next part is a unit
                    if i + 1 < len(parts):
                        unit = parts[i + 1].lower()
                        if unit in ('w', 'mw', 'uw', 'nw'):
                            multiplier = {'w': 1.0, 'mw': 1e-3, 'uw': 1e-6, 'nw': 1e-9}.get(unit, 1.0)
                            power['total'] = val * multiplier
                            break
                except ValueError:
                    continue
        lower = line.lower()
        if 'internal' in lower and 'power' in lower:
            m = re.search(r'([\d.eE+-]+)\s*(w|mw|uw|nw)', lower)
            if m:
                val = float(m.group(1))
                mult = {'w': 1.0, 'mw': 1e-3, 'uw': 1e-6, 'nw': 1e-9}.get(m.group(2), 1.0)
                power['internal'] = val * mult
        if 'switching' in lower and 'power' in lower:
            m = re.search(r'([\d.eE+-]+)\s*(w|mw|uw|nw)', lower)
            if m:
                val = float(m.group(1))
                mult = {'w': 1.0, 'mw': 1e-3, 'uw': 1e-6, 'nw': 1e-9}.get(m.group(2), 1.0)
                power['switching'] = val * mult
        if 'leakage' in lower and 'power' in lower:
            m = re.search(r'([\d.eE+-]+)\s*(w|mw|uw|nw)', lower)
            if m:
                val = float(m.group(1))
                mult = {'w': 1.0, 'mw': 1e-3, 'uw': 1e-6, 'nw': 1e-9}.get(m.group(2), 1.0)
                power['leakage'] = val * mult
    return power


def parse_drc_report(text: str) -> dict:
    """Parse DRC report for violation count."""
    drc = {}
    m = re.search(r'(\d+)\s*violation', text, re.I)
    if m:
        drc['drc_violations'] = int(m.group(1))
    elif 'no violations' in text.lower() or 'clean' in text.lower():
        drc['drc_violations'] = 0
    return drc


def find_reports(project_root: Path) -> dict:
    """Find report files in ORFS backend directory."""
    reports = {}
    # Check backend directory for latest run
    backend = project_root / 'backend'
    if not backend.exists():
        return reports

    runs = sorted([d for d in backend.iterdir() if d.is_dir() and d.name.startswith('RUN_')])
    if not runs:
        return reports

    latest = runs[-1]
    reports['run_dir'] = str(latest)

    # Search in copied reports
    for rpt_dir in [latest / 'reports_orfs', latest / 'logs', latest / 'results']:
        if not rpt_dir.exists():
            continue
        for f in rpt_dir.rglob('*'):
            if not f.is_file():
                continue
            name = f.name.lower()
            if 'area' in name and f.suffix in ('.rpt', '.log', '.txt'):
                reports['area_report'] = f
            elif 'timing' in name or 'setup' in name or name.endswith('sta.log'):
                reports.setdefault('timing_report', f)
            elif 'power' in name:
                reports.setdefault('power_report', f)
            elif 'drc' in name:
                reports.setdefault('drc_report', f)

    # Also check flow.log for embedded metrics
    flow_log = latest / 'flow.log'
    if flow_log.exists():
        reports['flow_log'] = flow_log

    return reports


def main():
    if len(sys.argv) < 3:
        print('usage: extract_ppa.py <project-root> <output.json>', file=sys.stderr)
        sys.exit(1)

    project_root = Path(sys.argv[1])
    out_path = Path(sys.argv[2])

    reports = find_reports(project_root)

    ppa = {
        'summary': {
            'area': {},
            'timing': {},
            'power': {},
            'drc': {},
        },
        'geometry': {},
        'run_dir': reports.get('run_dir'),
    }

    for key, parser in [
        ('area_report', parse_area_report),
        ('timing_report', parse_timing_report),
        ('power_report', parse_power_report),
        ('drc_report', parse_drc_report),
    ]:
        rpt_path = reports.get(key)
        if rpt_path and Path(rpt_path).exists():
            text = Path(rpt_path).read_text(encoding='utf-8', errors='ignore')
            category = key.replace('_report', '')
            ppa['summary'][category].update(parser(text))

    # Try parsing flow.log for metrics if specific reports weren't found
    flow_log = reports.get('flow_log')
    if flow_log and Path(flow_log).exists():
        text = Path(flow_log).read_text(encoding='utf-8', errors='ignore')
        if not ppa['summary']['area']:
            ppa['summary']['area'] = parse_area_report(text)
        if not ppa['summary']['timing']:
            ppa['summary']['timing'] = parse_timing_report(text)
        if not ppa['summary']['power']:
            ppa['summary']['power'] = parse_power_report(text)

    # Extract detailed geometry from 6_report.json
    if reports.get('run_dir'):
        report_json = Path(reports['run_dir']) / 'logs' / '6_report.json'
        if report_json.exists():
            rj = json.loads(report_json.read_text(encoding='utf-8', errors='ignore'))
            geo = {}
            key_map = {
                'die_area_um2': 'finish__design__die__area',
                'core_area_um2': 'finish__design__core__area',
                'utilization': 'finish__design__instance__utilization',
                'instance_count': 'finish__design__instance__count',
                'stdcell_count': 'finish__design__instance__count__stdcell',
                'stdcell_area_um2': 'finish__design__instance__area__stdcell',
                'macro_count': 'finish__design__instance__count__macros',
                'macro_area_um2': 'finish__design__instance__area__macros',
                'io_count': 'finish__design__io',
                'rows': 'finish__design__rows',
                'sites': 'finish__design__sites',
                'clock_buffer_count': 'finish__design__instance__count__class:clock_buffer',
                'sequential_count': 'finish__design__instance__count__class:sequential_cell',
                'warnings': 'finish__flow__warnings__count',
                'errors': 'finish__flow__errors__count',
            }
            for out_key, json_key in key_map.items():
                if json_key in rj:
                    geo[out_key] = rj[json_key]
            ppa['geometry'] = geo

            # Extract timing from 6_report.json — these values are authoritative
            # and OVERWRITE the flow.log-parsed values which can be wrong
            # (e.g. regex matches ORFS command '-repair_tns 100' instead of actual TNS).
            timing_map = {
                'setup_wns': 'finish__timing__setup__ws',
                'setup_tns': 'finish__timing__setup__tns',
                'hold_wns': 'finish__timing__hold__ws',
                'hold_tns': 'finish__timing__hold__tns',
                'clock_skew_setup': 'finish__clock__skew__setup',
                'clock_skew_hold': 'finish__clock__skew__hold',
                'setup_violation_count': 'finish__timing__drv__setup_violation_count',
                'hold_violation_count': 'finish__timing__drv__hold_violation_count',
                'max_cap_violations': 'finish__timing__drv__max_cap',
                'max_slew_violations': 'finish__timing__drv__max_slew',
            }
            report_timing = {}
            for out_key, json_key in timing_map.items():
                if json_key in rj:
                    report_timing[out_key] = rj[json_key]
            if report_timing:
                ppa['summary']['timing'] = report_timing

            # Extract power from 6_report.json — overwrites flow.log-parsed values.
            power_map = {
                'total_power_w': 'finish__power__total',
                'internal_power_w': 'finish__power__internal__total',
                'switching_power_w': 'finish__power__switching__total',
                'leakage_power_w': 'finish__power__leakage__total',
            }
            report_power = {}
            for out_key, json_key in power_map.items():
                if json_key in rj:
                    report_power[out_key] = rj[json_key]
            if report_power:
                ppa['summary']['power'] = report_power

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(ppa, indent=2, ensure_ascii=False), encoding='utf-8')
    print(out_path)


if __name__ == '__main__':
    main()
