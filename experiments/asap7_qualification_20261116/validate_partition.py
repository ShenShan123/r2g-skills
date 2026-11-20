"""Validate the two-group ASAP7 DRC schedule against the full official deck (run on memlab216).

Group A = every rule check except ACTIVE.AUX.3 (333), group B = ACTIVE.AUX.3 alone. Both use the unchanged
official deck through DRC SELECT CHECK, the same mode as the full run (-hier -turbo 4 -hyper), and ALL results.
A design's partitioned outcome is accepted as equivalent only if, for EVERY one of the 334 rule names, the
multiset of result records in the results database equals the full run's.

  python3 validate_partition.py compare FULL_RUN_DIR A_RUN_DIR B_RUN_DIR      # prints JSON verdict
"""
import collections
import json
import re
import sys
from pathlib import Path

RULES = [l.strip() for l in Path(__file__).with_name('rule_names_334.txt').read_text().splitlines() if l.strip()]
SLOW = 'ACTIVE.AUX.3'


def parse_db(path: Path) -> dict:
    """{rule: Counter(result-record strings)} from a Calibre ASCII results database.

    The database is: a header line (top cell + precision), then per rule check a line with the rule name, a
    counts line (current count, original count, number of text lines, date), the rule's text lines, and result
    records ('p'/'e' records with coordinate lines, optionally preceded by 'CN' cell-name lines in
    hierarchical output). Records are compared as an order-free multiset; the date and text lines are not."""
    names = set(RULES)
    out = {}
    lines = path.read_text(errors='replace').splitlines()
    i = 1
    while i < len(lines):
        name = lines[i].strip()
        if name not in names:
            i += 1
            continue
        counts = lines[i + 1].split()
        ntext = int(counts[2]) if len(counts) > 2 and counts[2].isdigit() else 0
        j = i + 2 + ntext
        records, cur = collections.Counter(), []
        while j < len(lines) and lines[j].strip() not in names:
            ln = lines[j].strip()
            if re.match(r'^(p|e)\s+\d+\s+\d+', ln) or ln.startswith('CN '):
                if cur:
                    records['\n'.join(cur)] += 1
                cur = [re.sub(r'^(p|e)\s+\d+', r'\1 #', ln)]   # result ordinal is not part of the geometry
            elif cur:
                cur.append(ln)
            j += 1
        if cur:
            records['\n'.join(cur)] += 1
        out[name] = records
        i = j
    return out


def report_counts(path: Path) -> dict:
    return {m[0]: int(m[2] or m[1]) for m in re.findall(
        r'^RULECHECK\s+(\S+)\s+\.+\s+TOTAL Result Count =\s*(\d+)(?:\s*\((\d+)\))?', path.read_text(), re.M)}


def compare(full: Path, a: Path, b: Path) -> dict:
    full_db, a_db, b_db = (parse_db(d / 'drc' / 'drc_results.db') for d in (full, a, b))
    full_rep = report_counts(full / 'drc' / 'drc_report')
    part_rep = {**report_counts(a / 'drc' / 'drc_report'), **report_counts(b / 'drc' / 'drc_report')}
    a_rules, b_rules = set(report_counts(a / 'drc' / 'drc_report')), set(report_counts(b / 'drc' / 'drc_report'))
    problems = []
    if a_rules & b_rules:
        problems.append('groups overlap: %s' % sorted(a_rules & b_rules))
    if (a_rules | b_rules) != set(RULES):
        problems.append('groups do not cover the 334 rules: missing %s extra %s' % (
            sorted(set(RULES) - a_rules - b_rules)[:10], sorted((a_rules | b_rules) - set(RULES))[:10]))
    if set(full_rep) != set(RULES):
        problems.append('full run does not report the 334 rules')
    per_rule_diff = []
    for r in RULES:
        part = (b_db if r == SLOW else a_db).get(r, collections.Counter())
        if full_rep.get(r) != part_rep.get(r) or full_db.get(r, collections.Counter()) != part:
            per_rule_diff.append({'rule': r, 'full_count': full_rep.get(r), 'partitioned_count': part_rep.get(r),
                                  'full_records': sum(full_db.get(r, {}).values()), 'partitioned_records': sum(part.values())})
    total_full = sum(full_rep.values())
    return {'full_run': str(full), 'group_a': str(a), 'group_b': str(b),
            'rules_compared': len(RULES), 'full_expanded_results': total_full,
            'partitioned_expanded_results': sum(part_rep.values()),
            'records_compared': sum(sum(c.values()) for c in full_db.values()),
            'rules_with_any_difference': per_rule_diff, 'structural_problems': problems,
            'equivalent': not problems and not per_rule_diff}


if __name__ == '__main__':
    if sys.argv[1] != 'compare':
        raise SystemExit(__doc__)
    print(json.dumps(compare(*(Path(p) for p in sys.argv[2:5])), indent=1))
