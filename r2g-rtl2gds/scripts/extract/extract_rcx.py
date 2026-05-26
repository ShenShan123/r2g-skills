#!/usr/bin/env python3
"""
Extract OpenRCX parasitic extraction results from SPEF output.
Produces a JSON summary with net counts, total capacitance, and resistance stats.
"""
from pathlib import Path
import json
import re
import sys


def parse_spef(spef_path: Path) -> dict:
    """Parse SPEF file for summary statistics."""
    if not spef_path.exists():
        return {}

    stats = {
        'net_count': 0,
        'total_cap_ff': 0.0,
        'total_res_ohm': 0.0,
        'cap_unit': None,
        'res_unit': None,
    }

    cap_multiplier = 1.0  # default to fF
    res_multiplier = 1.0  # default to Ohm

    with open(spef_path, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            line = line.strip()

            # Parse header for units
            if line.startswith('*C_UNIT'):
                parts = line.split()
                if len(parts) >= 3:
                    try:
                        val = float(parts[1])
                        unit = parts[2].upper()
                        stats['cap_unit'] = f'{val} {unit}'
                        if 'PF' in unit:
                            cap_multiplier = val * 1000  # convert to fF
                        elif 'FF' in unit:
                            cap_multiplier = val
                    except ValueError:
                        pass

            elif line.startswith('*R_UNIT'):
                parts = line.split()
                if len(parts) >= 3:
                    try:
                        val = float(parts[1])
                        unit = parts[2].upper()
                        stats['res_unit'] = f'{val} {unit}'
                        if 'KOHM' in unit:
                            res_multiplier = val * 1000
                        elif 'OHM' in unit:
                            res_multiplier = val
                    except ValueError:
                        pass

            # Count nets
            elif line.startswith('*D_NET') or line.startswith('*R_NET'):
                stats['net_count'] += 1
                # *D_NET net_name total_cap
                parts = line.split()
                if len(parts) >= 3:
                    try:
                        stats['total_cap_ff'] += float(parts[2]) * cap_multiplier
                    except ValueError:
                        pass

            # Sum resistance values
            elif line.startswith('*RES'):
                # Skip the *RES section header
                pass
            elif re.match(r'^\d+\s+\S+\s+\S+\s+[\d.eE+-]+', line):
                # Resistance entry: id node1 node2 value
                parts = line.split()
                if len(parts) >= 4:
                    try:
                        stats['total_res_ohm'] += float(parts[3]) * res_multiplier
                    except ValueError:
                        pass

    return stats


def parse_spef_header(spef_path: Path) -> dict:
    """Parse SPEF header for design metadata."""
    header = {}
    if not spef_path.exists():
        return header

    with open(spef_path, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            line = line.strip()
            if line.startswith('*DESIGN'):
                header['design'] = line.split('"')[1] if '"' in line else line.split()[-1]
            elif line.startswith('*DATE'):
                header['date'] = line.split('"')[1] if '"' in line else ' '.join(line.split()[1:])
            elif line.startswith('*VENDOR'):
                header['vendor'] = line.split('"')[1] if '"' in line else ' '.join(line.split()[1:])
            elif line.startswith('*DIVIDER'):
                header['divider'] = line.split()[-1] if line.split() else ''
            elif line.startswith('*D_NET') or line.startswith('*R_NET'):
                break  # Done with header
    return header


def parse_rcx_log(rcx_dir: Path) -> dict:
    """Parse RCX log for runtime info."""
    info = {}
    log_file = rcx_dir / 'rcx.log'
    if not log_file.exists():
        return info

    text = log_file.read_text(encoding='utf-8', errors='ignore')

    # Look for timing
    m = re.search(r'Elapsed time:\s+([\d:.]+)', text)
    if m:
        info['elapsed'] = m.group(1)

    # Look for extraction stats from OpenRCX output
    m = re.search(r'Extract\s+(\d+)\s+nets', text, re.I)
    if m:
        info['nets_extracted'] = int(m.group(1))

    # Check for errors
    if 'error' in text.lower():
        error_lines = [l.strip() for l in text.splitlines()
                       if 'error' in l.lower() and 'no error' not in l.lower()]
        if error_lines:
            info['errors'] = error_lines[:5]

    return info


def main():
    if len(sys.argv) < 3:
        print('usage: extract_rcx.py <project-root> <output.json>', file=sys.stderr)
        sys.exit(1)

    project_root = Path(sys.argv[1])
    out_path = Path(sys.argv[2])

    rcx_dir = project_root / 'rcx'

    # Check for skip marker
    skip_file = rcx_dir / 'rcx_result.json'
    if skip_file.exists():
        try:
            skip_data = json.loads(skip_file.read_text(encoding='utf-8'))
            if skip_data.get('status') == 'skipped':
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(json.dumps(skip_data, indent=2), encoding='utf-8')
                print(out_path)
                return
        except Exception:
            pass

    # Find SPEF file
    spef_path = rcx_dir / '6_final.spef'
    if not spef_path.exists():
        # Check backend runs
        backend = project_root / 'backend'
        if backend.exists():
            runs = sorted([d for d in backend.iterdir() if d.is_dir() and d.name.startswith('RUN_')])
            if runs:
                for candidate in [runs[-1] / 'rcx' / '6_final.spef',
                                  runs[-1] / 'results' / '6_final.spef']:
                    if candidate.exists():
                        spef_path = candidate
                        break

    if not spef_path.exists():
        result = {'status': 'no_spef', 'reason': 'No SPEF file found. Run RCX extraction first.'}
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(out_path)
        return

    header = parse_spef_header(spef_path)
    stats = parse_spef(spef_path)
    log_info = parse_rcx_log(rcx_dir)

    result = {
        'status': 'complete' if stats.get('net_count', 0) > 0 else 'empty',
        'spef_file': str(spef_path),
        'spef_size_bytes': spef_path.stat().st_size,
        'header': header,
        'net_count': stats.get('net_count', 0),
        'total_cap_ff': round(stats.get('total_cap_ff', 0.0), 4),
        'total_res_ohm': round(stats.get('total_res_ohm', 0.0), 4),
        'cap_unit': stats.get('cap_unit'),
        'res_unit': stats.get('res_unit'),
        'log_info': log_info,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    print(out_path)


if __name__ == '__main__':
    main()
