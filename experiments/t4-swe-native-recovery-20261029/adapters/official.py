"""Load pinned upstream parsers/specs, without importing Docker CLI entrypoints.

The namespace shim bypasses only swebench/__init__.py (which imports every CLI).
Imported spec and grading implementation files themselves remain unmodified.
"""
from pathlib import Path
import sys
import types

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'sources/SWE-bench-v4.1.0'
sys.path.insert(0,str(ROOT/'env-deps'))
for name,sub in [('swebench','swebench'),('swebench.harness','swebench/harness'),
                 ('swebench.harness.test_spec','swebench/harness/test_spec')]:
    if name not in sys.modules:
        module=types.ModuleType(name)
        module.__path__=[str(SOURCE/sub)]
        module.__file__=str(SOURCE/sub/'__init__.py')
        sys.modules[name]=module

from swebench.harness.constants import MAP_REPO_VERSION_TO_SPECS, MAP_REPO_TO_INSTALL
from swebench.harness.test_spec.python import get_test_directives, make_env_script_list_py
from swebench.harness.log_parsers import MAP_REPO_TO_PARSER
from swebench.harness.grading import get_eval_tests_report, get_resolution_status


def spec_for(task):
    return MAP_REPO_VERSION_TO_SPECS[task['repo']][task['version']]


def parse_and_grade(task, output):
    import json
    from types import SimpleNamespace
    parser=MAP_REPO_TO_PARSER[task['repo']]
    status=parser(output,SimpleNamespace(repo=task['repo'],version=task['version']))
    gold={k:json.loads(task[k]) if isinstance(task[k],str) else task[k]
          for k in ('FAIL_TO_PASS','PASS_TO_PASS')}
    if not gold['FAIL_TO_PASS'] or not status:
        return {'status':'UNKNOWN','reason':'no parsed tests or empty target set','test_status':status}
    missing=[name for names in gold.values() for name in names if name not in status]
    # Never let absent cases become a false pass or a measured repair failure.
    if missing:
        return {'status':'UNKNOWN','reason':'expected tests absent from parsed output',
                'missing_tests':missing,'test_status':status}
    report=get_eval_tests_report(status,gold)
    resolved=get_resolution_status(report)=='RESOLVED_FULL'
    return {'status':'PASS' if resolved else 'FAIL','resolved':resolved,
            'report':report,'test_status':status}
