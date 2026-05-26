#!/usr/bin/env python3
"""
Extract LVS results from KLayout lvsdb report.
Produces a JSON summary with match/mismatch status and details.
"""
from pathlib import Path
import json
import re
import sys
import xml.etree.ElementTree as ET


def parse_lvsdb(lvs_dir: Path) -> dict:
    """Parse KLayout lvsdb (XML) for LVS comparison results."""
    lvsdb_file = lvs_dir / '6_lvs.lvsdb'
    if not lvsdb_file.exists():
        return {}

    result = {}
    try:
        tree = ET.parse(lvsdb_file)
        root = tree.getroot()

        # Look for status elements
        for status_el in root.iter('status'):
            if status_el.text:
                result['raw_status'] = status_el.text.strip()

        # Count mismatches
        mismatches = 0
        for mismatch in root.iter('mismatch'):
            mismatches += 1
        result['mismatch_count'] = mismatches

        # Look for net/device counts
        for net_el in root.iter('net_count'):
            if net_el.text:
                result.setdefault('net_count', int(net_el.text))
        for dev_el in root.iter('device_count'):
            if dev_el.text:
                result.setdefault('device_count', int(dev_el.text))
        for pin_el in root.iter('pin_count'):
            if pin_el.text:
                result.setdefault('pin_count', int(pin_el.text))

    except ET.ParseError:
        # KLayout lvsdb may use text format (#%lvsdb-klayout), not XML
        text = lvsdb_file.read_text(encoding='utf-8', errors='ignore')
        lower_text = text.lower()
        if 'mismatch' in lower_text:
            result['raw_status'] = 'text_mismatch_found'
            result['mismatch_count'] = sum(1 for line in text.splitlines() if 'mismatch' in line.lower())
        elif "don't match" in lower_text or 'not match' in lower_text:
            result['raw_status'] = 'text_not_match'
            result['mismatch_count'] = -1  # unknown count, but known mismatch
        elif 'match' in lower_text:
            result['raw_status'] = 'text_match_found'
            result['mismatch_count'] = 0
        else:
            result['raw_status'] = 'text_unparsed'
            # Do NOT set mismatch_count — leave it absent so status logic doesn't assume clean

    return result


def parse_lvs_log(lvs_dir: Path) -> dict:
    """Parse LVS log for status and runtime info."""
    info = {}
    log_file = lvs_dir / '6_lvs.log'
    if not log_file.exists():
        log_file = lvs_dir / 'lvs_run.log'
    if not log_file.exists():
        return info

    text = log_file.read_text(encoding='utf-8', errors='ignore')
    lower = text.lower()

    # Determine match status from log — check negative patterns FIRST
    # because "netlists match" is a substring of "netlists don't match"
    if "don't match" in lower or 'do not match' in lower or 'not match' in lower:
        info['log_status'] = 'mismatch'
    elif 'netlists match' in lower or 'lvs clean' in lower or 'circuits match' in lower:
        info['log_status'] = 'match'
    elif 'not supported' in lower:
        info['log_status'] = 'not_supported'

    # Look for elapsed time
    m = re.search(r'(?:real|elapsed|Total time)[:\s]+([\d.]+)', text)
    if m:
        info['elapsed_seconds'] = float(m.group(1))

    # Look for errors
    error_lines = [l.strip() for l in text.splitlines()
                   if 'error' in l.lower() and 'no error' not in l.lower()]
    if error_lines:
        info['errors'] = error_lines[:5]

    return info


def main():
    if len(sys.argv) < 3:
        print('usage: extract_lvs.py <project-root> <output.json>', file=sys.stderr)
        sys.exit(1)

    project_root = Path(sys.argv[1])
    out_path = Path(sys.argv[2])

    lvs_dir = project_root / 'lvs'

    # Check for skip marker
    skip_file = lvs_dir / 'lvs_result.json'
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

    lvsdb_result = parse_lvsdb(lvs_dir)
    log_info = parse_lvs_log(lvs_dir)

    # Determine overall status
    mismatch_count = lvsdb_result.get('mismatch_count', -1)
    log_status = log_info.get('log_status', '')

    if log_status == 'mismatch':
        status = 'fail'
    elif log_status == 'match' and mismatch_count <= 0:
        status = 'clean'
    elif mismatch_count > 0:
        status = 'fail'
    elif mismatch_count == 0 and log_status == '':
        status = 'clean'  # lvsdb says clean, no log to contradict
    elif log_status == 'not_supported':
        status = 'skipped'
    else:
        status = 'unknown'

    result = {
        'status': status,
        'mismatch_count': mismatch_count if mismatch_count >= 0 else None,
        'lvsdb': lvsdb_result,
        'log_info': log_info,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    print(out_path)


if __name__ == '__main__':
    main()
