#!/usr/bin/env python3
"""
Extract DRC results from KLayout lyrdb and count report.
Produces a JSON summary with violation counts and categories.
"""
from pathlib import Path
import json
import re
import sys
import xml.etree.ElementTree as ET


def parse_drc_count(drc_dir: Path) -> int:
    """Parse 6_drc_count.rpt for total violation count."""
    count_file = drc_dir / '6_drc_count.rpt'
    if count_file.exists():
        text = count_file.read_text(encoding='utf-8', errors='ignore').strip()
        try:
            return int(text)
        except ValueError:
            pass
    return -1


def parse_lyrdb(drc_dir: Path) -> dict:
    """Parse KLayout lyrdb (XML) for DRC violation categories."""
    lyrdb_file = drc_dir / '6_drc.lyrdb'
    if not lyrdb_file.exists():
        return {}

    categories = {}
    try:
        tree = ET.parse(lyrdb_file)
        root = tree.getroot()

        # Parse categories
        cat_map = {}
        for cat in root.iter('category'):
            name_el = cat.find('name')
            desc_el = cat.find('description')
            if name_el is not None and name_el.text:
                cat_name = name_el.text.strip()
                cat_desc = desc_el.text.strip() if desc_el is not None and desc_el.text else ''
                cat_map[cat_name] = cat_desc

        # Count violations per category
        for item in root.iter('item'):
            cat_el = item.find('category')
            if cat_el is not None and cat_el.text:
                cat_name = cat_el.text.strip()
                if cat_name not in categories:
                    categories[cat_name] = {
                        'count': 0,
                        'description': cat_map.get(cat_name, ''),
                    }
                categories[cat_name]['count'] += 1

    except ET.ParseError:
        # Fallback: count <value> tags
        text = lyrdb_file.read_text(encoding='utf-8', errors='ignore')
        count = text.count('<value>')
        if count > 0:
            categories['unknown'] = {'count': count, 'description': 'Parsed via fallback'}

    return categories


def parse_drc_log(drc_dir: Path) -> dict:
    """Parse DRC log for runtime info."""
    info = {}
    log_file = drc_dir / '6_drc.log'
    if not log_file.exists():
        log_file = drc_dir / 'drc_run.log'
    if not log_file.exists():
        return info

    text = log_file.read_text(encoding='utf-8', errors='ignore')

    # Look for elapsed time
    m = re.search(r'(?:real|elapsed|Total time)[:\s]+([\d.]+)', text)
    if m:
        info['elapsed_seconds'] = float(m.group(1))

    # Look for errors
    if 'error' in text.lower():
        error_lines = [l.strip() for l in text.splitlines() if 'error' in l.lower()]
        info['errors'] = error_lines[:5]

    return info


def main():
    if len(sys.argv) < 3:
        print('usage: extract_drc.py <project-root> <output.json>', file=sys.stderr)
        sys.exit(1)

    project_root = Path(sys.argv[1])
    out_path = Path(sys.argv[2])

    drc_dir = project_root / 'drc'

    raw = parse_drc_count(drc_dir)
    categories = parse_lyrdb(drc_dir)
    log_info = parse_drc_log(drc_dir)

    # Prefer the true item count (sum of parsed category counts from the lyrdb)
    # over the inflated count.rpt value.  count.rpt is produced by
    # `grep -c "<value>"` over the lyrdb, which counts polygon value-tags
    # (~7 per violation), NOT actual violations.  The lyrdb item count is
    # exact; fall back to count.rpt only when no lyrdb is present.
    cat_sum = sum(c['count'] for c in categories.values()) if categories else None
    if cat_sum is not None:
        total_count = cat_sum
    elif raw >= 0:
        total_count = raw
    else:
        total_count = -1

    # Prefer the structured drc_result.json written by run_drc.sh — it carries
    # explicit status="stuck"/"timeout" markers that are more informative than
    # what we can infer from violation counts alone.
    drc_result_path = drc_dir / 'drc_result.json'
    drc_result = None
    if drc_result_path.exists():
        try:
            drc_result = json.loads(drc_result_path.read_text(encoding='utf-8', errors='ignore'))
        except (ValueError, OSError):
            drc_result = None

    # BEOL-only runs disable BOTH the FEOL and ANTENNA rule groups (run_drc.sh,
    # commit 56a1175), so a 0-violation result only proves metal/via/cut routing
    # is clean — it does NOT cover FEOL geometry or antenna ratios.  Mark it with
    # the qualified status 'clean_beol' (cf. LVS 'clean_algorithmic') so that
    # status-based aggregation cannot silently miscount it as a full clean.
    # Any BEOL-class mode (beol_only, beol_only_no_contact) skips ≥2 rule groups, so
    # a 0-violation result is the qualified status clean_beol, never plain clean.
    beol_only = bool(drc_result) and str(drc_result.get('drc_mode') or '').startswith('beol_only')

    if drc_result and drc_result.get('status') in ('stuck', 'timeout', 'failed', 'skipped'):
        status = drc_result['status']
    elif total_count == 0:
        status = 'clean_beol' if beol_only else 'clean'
    elif total_count > 0:
        status = 'fail'
    else:
        status = 'unknown'

    result = {
        'status': status,
        'total_violations': total_count if total_count >= 0 else None,
        'raw_marker_count': raw if raw >= 0 else None,
        'categories': categories,
        'log_info': log_info,
    }
    if drc_result:
        # Carry through extra context (stuck_at_rule, reason, timeout_s, drc_mode, etc.)
        for k in ('stuck_at_rule', 'reason', 'timeout_s', 'exit_code', 'note', 'drc_mode'):
            if k in drc_result and k not in result:
                result[k] = drc_result[k]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    print(out_path)


if __name__ == '__main__':
    main()
