"""Apply the user's explicit decision to abandon difficult repair cases."""
import json
from pathlib import Path
import re


def load(path):
    data=json.loads(Path(path).read_text())
    if data.get('policy')!='reuse_existing_recipes_only':raise ValueError('unknown repair scope')
    rows=data['decisions'];decisions={r['design']:r['disposition'] for r in rows}
    if len(decisions)!=len(rows) or not set(decisions.values())<={'verified','candidate','abandon'}:
        raise ValueError('ambiguous repair scope')
    return decisions


def select(cases, decisions, externally_managed=()):
    if any(c['design'] not in decisions for c in cases):raise ValueError('case missing from explicit repair scope')
    if any(decisions.get(d)!='candidate' for d in externally_managed):raise ValueError('external assignment must name a candidate')
    return [c for c in cases if decisions[c['design']]!='abandon' and c['design'] not in externally_managed]


def cancelled_container(info, decisions, root):
    """Return a design only for an abandoned case's own live flow container."""
    name=info.get('Name','').lstrip('/')
    match=re.fullmatch(r's130repair_(C\d+)_(\d+)(?:_sv)?',name)
    if not match or not info.get('State',{}).get('Running'):return None
    abandoned=[d for d,state in decisions.items() if state in ('abandon','external') and d.split('__')[0]==match[1]]
    if len(abandoned)!=1:return None
    design=abandoned[0];boundary=(Path(root)/'campaign/cases'/design).resolve()
    mounts=[m.get('Source','') for m in info.get('Mounts',[]) if m.get('Destination')=='/work']
    if len(mounts)!=1 or not Path(mounts[0]).resolve().is_relative_to(boundary):return None
    return design
