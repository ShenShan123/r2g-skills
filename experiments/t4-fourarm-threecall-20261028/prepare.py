"""Stage a fresh six-lane Table 4 rerun. Never copy old episodes or stores."""
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parent
EDA = BASE / 't4-rtlfixer-threecall-20261028-r2'
SWE = BASE / 't4-swe20-local-20261026'


def copy_tree(source, target):
    shutil.copytree(source, target, symlinks=True,
                    ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache', '*.pyc'))


def main():
    if (ROOT/'eda').exists() or (ROOT/'swe').exists():
        raise ValueError('staging already exists; never replace it')
    for name, source in [('eda', EDA), ('swe', SWE)]:
        target = ROOT/name
        target.mkdir()
        for directory in ('adapters', 'tests'):
            copy_tree(source/directory, target/directory)
        for directory in ('inputs', 'reports', 'qualification'):
            (target/directory).mkdir()
        # The core is an immutable input, not another executable store.
        (target/'code').symlink_to((source/'code').resolve(), target_is_directory=True)
        (target/'inputs/sixarm-control.json').write_text(json.dumps({'root':str(ROOT)})+'\n')
    eda = ROOT/'eda'
    copy_tree(EDA/'bin', eda/'bin')
    (eda/'sources').mkdir()
    copy_tree(EDA/'sources/rtlfixer', eda/'sources/rtlfixer')
    for name in ('rtlscout', 'rtl-repair', 'verilog-eval'):
        (eda/'sources'/name).symlink_to((EDA/'sources'/name).resolve(), target_is_directory=True)
    for name in ('rtlrepair-roster.json', 'rtlrepair-private.json', 'empty.env'):
        shutil.copy2(EDA/'inputs'/name, eda/'inputs'/name)
    original = BASE/'t4-eda-formal-20261027'
    for task in json.loads((eda/'inputs/rtlrepair-roster.json').read_text()):
        dest = eda/'qualification'/task['task']
        dest.mkdir()
        shutil.copy2(original/'qualification'/task['task']/'qualification.json', dest/'qualification.json')
    swe = ROOT/'swe'
    for name in ('bootstrap', 'python-runtimes', 'sources'):
        (swe/name).symlink_to((SWE/name).resolve(), target_is_directory=True)
    for name in ('public-tasks.json', 'native.yaml', 'tehm.yaml'):
        shutil.copy2(SWE/'inputs'/name, swe/'inputs'/name)
    (swe/'inputs/evaluator-private').mkdir()
    shutil.copy2(SWE/'inputs/evaluator-private/tasks.json', swe/'inputs/evaluator-private/tasks.json')
    tasks=json.loads((swe/'inputs/public-tasks.json').read_text())['public_tasks']
    for task in tasks:
        dest = swe/'qualification'/task['instance_id']
        dest.mkdir()
        shutil.copy2(SWE/'qualification'/task['instance_id']/'qualification.json', dest/'qualification.json')
    (ROOT/'reports').mkdir()
    print(json.dumps({'staged':str(ROOT), 'eda_tasks':77, 'swe_tasks':20, 'old_hosts_copied':False}))


if __name__ == '__main__':
    main()
