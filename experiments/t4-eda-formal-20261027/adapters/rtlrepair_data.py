"""Complete pinned upstream registry, independent of either arm's outcomes."""
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'sources/rtl-repair'
sys.path.insert(0,str(SOURCE))


def inventory():
    import benchmarks
    out=[]
    for group,project in benchmarks.load_all_projects().items():
        for bug in project.bugs:
            tests=[]
            for test in project.testbenches:
                if test.bugs is not None and bug.name not in test.bugs:continue
                if isinstance(test,benchmarks.VerilogOracleTestbench):
                    tests.append({'kind':'output_oracle','name':test.name,
                        'sources':[str(p) for p in test.sources],'oracle':str(test.oracle),
                        'output':test.output,'init_files':[str(p) for p in test.init_files],
                        'timeout':test.timeout or 120})
                else:
                    tests.append({'kind':'cycle_trace','name':test.name,'table':str(test.table)})
            task=group+'__'+bug.name
            out.append({'task':task,'source_group':group,'bug':bug.name,
                'buggy':str(bug.buggy),'reference':str(bug.original),
                'sources':[str(p) for p in project.design.sources],
                'include_dir':str(project.design.directory),'top':project.design.top,
                'tests':tests})
    # Identical online order on every host and arm, fixed before qualification.
    out.sort(key=lambda x:hashlib.sha256(('table4-rtlrepair-full-v1:'+x['task']).encode()).hexdigest())
    return out


def main():
    path=ROOT/'inputs/rtlrepair-private.json'
    if path.exists():raise ValueError('inventory already fixed')
    rows=inventory()
    assert len(rows)==77 and len({r['source_group'] for r in rows})==24
    path.write_text(json.dumps(rows,indent=2)+'\n');path.chmod(0o600)
    public=[{k:r[k] for k in ('task','source_group','bug','top')} for r in rows]
    (ROOT/'inputs/rtlrepair-roster.json').write_text(json.dumps(public,indent=2)+'\n')
    print(json.dumps({'projects':24,'variants':77,'hosts':['rtlfixer','rtlscout'],
                      'arms':['native','tehm'],'registered_episodes':308}))


if __name__=='__main__':main()
