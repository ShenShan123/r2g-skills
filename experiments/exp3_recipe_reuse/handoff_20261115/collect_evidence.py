#!/usr/bin/env python3
"""Copy bounded diagnosis artifacts; no EDA and no model calls."""
import argparse
import json
from pathlib import Path
import shutil


def copy_small(source, target, limit=2 * 1024 * 1024):
    if not source.is_file():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.stat().st_size <= limit:
        shutil.copyfile(source, target)
    else:
        with source.open('rb') as f:
            f.seek(-limit, 2)
            data = f.read()
        target.with_name(target.name + '.tail').write_bytes(data)


def copy_stop(source, target):
    for sub in ['rtl', 'constraints', 'hooks', 'signoff_fix']:
        path = source / sub
        if path.is_dir():
            shutil.copytree(path, target / sub, dirs_exist_ok=True)
    for sub in ['reports', 'agent_logs']:
        for p in (source / sub).glob('*'):
            if p.is_file() and p.suffix in ['.json', '.jsonl', '.log', '.txt', '.rpt']:
                copy_small(p, target / sub / p.name)
    copy_small(source / 'metadata.json', target / 'metadata.json')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', required=True)
    ap.add_argument('--selection', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args()
    selection = json.loads(args.selection.read_text())
    for row in selection['selected']:
        dest = args.out / row['platform'] / row['id']
        if row['stop_host'] == args.host:
            copy_stop(Path(row['stop_project']), dest / 'r2g_stop')
            copy_small(Path(row['job_receipt']), dest / 'r2g_stop/job_receipt.json')
        if args.host == '216' and row.get('baseline_dir'):
            base = Path(row['baseline_dir'])
            for name in ['config.mk', 'constraint.sdc', 'result.json', 'flow.log']:
                copy_small(base / name, dest / 'baseline' / name)
        print(row['id'], flush=True)


if __name__ == '__main__':
    main()
