#!/usr/bin/env python3
from pathlib import Path
import json
import sys


def tail(path: Path, n: int = 30) -> list:
    if not path.exists():
        return []
    return path.read_text(encoding='utf-8', errors='ignore').splitlines()[-n:]


def main():
    if len(sys.argv) < 3:
        print('usage: write_success_summary.py <orfs-results.json> <output.md>', file=sys.stderr)
        sys.exit(1)

    results_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])
    results = json.loads(results_path.read_text(encoding='utf-8'))

    flow_log_path = results.get('reports', {}).get('flow.log')

    lines = []
    lines.append('# EDA Run Summary')
    lines.append('')
    lines.append(f"- Project: `{results.get('project_dir')}`")
    lines.append(f"- Latest run: `{results.get('latest_run')}`")
    lines.append(f"- Final GDS: `{results.get('gds')}`")
    lines.append(f"- Final DEF: `{results.get('def')}`")
    lines.append('')
    lines.append('## Status')
    lines.append('')

    if results.get('gds'):
        lines.append('- ORFS flow completed and final GDS was generated.')
    else:
        lines.append('- ORFS flow completed but no GDS was found.')
    lines.append('')

    if flow_log_path:
        flow_tail = tail(Path(flow_log_path), 20)
        if flow_tail:
            lines.append('## Flow Log Tail')
            lines.append('')
            lines.append('```')
            lines.extend(flow_tail)
            lines.append('```')
            lines.append('')

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(out_path)


if __name__ == '__main__':
    main()
