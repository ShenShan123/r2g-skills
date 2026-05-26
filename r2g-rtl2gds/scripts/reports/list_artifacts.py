#!/usr/bin/env python3
from pathlib import Path
import json
import sys


def main():
    if len(sys.argv) < 3:
        print('usage: list_artifacts.py <project-root> <output.json>', file=sys.stderr)
        sys.exit(1)

    root = Path(sys.argv[1])
    out = Path(sys.argv[2])
    files = []
    for p in sorted(root.rglob('*')):
        if p.is_file() and p.suffix.lower() in [
            '.gds', '.def', '.odb', '.png', '.md', '.json',
            '.vcd', '.log', '.v', '.sdc', '.mk', '.rpt'
        ]:
            files.append(str(p.relative_to(root)))

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({'artifacts': files}, indent=2, ensure_ascii=False), encoding='utf-8')
    print(out)


if __name__ == '__main__':
    main()
