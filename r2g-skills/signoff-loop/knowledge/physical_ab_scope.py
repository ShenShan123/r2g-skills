"""Read-only provenance gate for reuse of durable repeated physical A/B evidence."""
import json
import sqlite3
from knowledge_db import is_success

def has_durable_repeated_wins(conn, lifecycle, key):
    try:
        if lifecycle.get_status(conn, default=None, **key) != 'promoted':return False
        rows=conn.execute('SELECT verdict, metrics_json FROM ab_trials WHERE symptom_id=? AND design_class=? AND platform=? AND strategy=?',tuple(key[k] for k in ('symptom_id','design_class','platform','strategy'))).fetchall()
        families=set();used=set()
        for verdict, raw in rows:
            if verdict=='loss':return False
            if verdict!='win':continue
            m=json.loads(raw)
            if m.get('provenance_complete') is not True or m.get('source')!='durable_copy_of_verified_physical_AB':continue
            a=m.get('arm_a_samples',[]);b=m.get('arm_b_samples',[])
            if len(a)!=2 or len(b)!=2:continue
            if sorted(s['repeat'] for s in a)!=[0,1] or sorted(s['repeat'] for s in b)!=[0,1]:continue
            if any(s['is_success'] is not False for s in a) or any(s['is_success'] is not True for s in b):continue
            ids={s['run_id'] for s in a+b};family={s['source_repository'] for s in a+b}
            if len(ids)!=4 or ids & used or len(family)!=1:continue
            verified=True
            for sample in a+b:
                cur=conn.execute('SELECT * FROM runs WHERE run_id=?',(sample['run_id'],))
                values=cur.fetchone()
                if values is None:
                    verified=False;break
                run=dict(zip([c[0] for c in cur.description],values))
                if (run.get('platform')!=key['platform']
                        or sample.get('platform',run['platform'])!=run['platform']
                        or run.get('design_family')!=sample['source_repository']
                        or is_success(run)!=sample['is_success']):
                    verified=False;break
            if not verified:continue
            used.update(ids);families.update(family)
        return len(families)>=2
    except (sqlite3.Error,ValueError,TypeError,KeyError):
        return False
