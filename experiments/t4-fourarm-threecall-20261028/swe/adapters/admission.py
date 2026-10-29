"""Exact UNKNOWN acknowledgements; actual usage is never rewritten."""
import hashlib
import json
from pathlib import Path


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def row_sha(row):
    return hashlib.sha256(json.dumps(row,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def interruption_policy(root):
    """A queue-level waiver must be explicitly authorized and hash bound."""
    root=Path(root)
    pointer=root/'inputs/active-registration.json'
    if not pointer.exists():
        return 'pause'
    active=json.loads(pointer.read_text())
    path=root/active['path']
    if sha(path)!=active['sha256']:
        raise ValueError('active registration hash mismatch')
    data=json.loads(path.read_text())
    if data.get('interruption_policy')!='skip_local_interruptions_no_retry':
        return 'pause'
    rel=data['continuation_authorization']
    auth=root/rel
    if sha(auth)!=data['files'][rel]:
        raise ValueError('interruption authorization hash mismatch')
    if json.loads(auth.read_text()).get('interruption_policy')!=data['interruption_policy']:
        raise ValueError('interruption policy not authorized')
    return data['interruption_policy']


def admission_blockers(root):
    """Keep UNKNOWN auditable, but isolate it to its episode when authorized.

    An affected MeteredProvider remains blocked itself. No failed request is
    retried and the other lane/later tasks may continue. Identity/integrity faults
    remain global blockers; no outcome or actual usage is upgraded.
    """
    root=Path(root)
    if (root/'reports/TECHNICAL-PAUSE.json').exists():
        return [{'global_technical_fault':str(root/'reports/TECHNICAL-PAUSE.json')}]
    outstanding=unresolved_unknowns(root)
    if interruption_policy(root)!='skip_local_interruptions_no_retry':
        return outstanding
    blockers=[]
    for item in outstanding:
        if 'unacknowledged_pause' in item:
            source=json.loads(Path(item['unacknowledged_pause']).read_text()).get('source')
            if not any('journal' in u and str((root/u['journal']).parent)==source for u in outstanding):
                blockers.append(item) # Never bypass an unexplained pause.
    return blockers


def unresolved_unknowns(root):
    root=Path(root)
    pointer=root/'inputs/active-registration.json'
    acknowledgements=[]
    if pointer.exists():
        active=json.loads(pointer.read_text())
        registration=root/active['path']
        if sha(registration)!=active['sha256']:
            raise ValueError('active registration hash mismatch')
        data=json.loads(registration.read_text())
        auth_path=root/data['continuation_authorization']
        if sha(auth_path)!=data['files'][data['continuation_authorization']]:
            raise ValueError('UNKNOWN authorization hash mismatch')
        acknowledgements=json.loads(auth_path.read_text())['unknown_acknowledgements']
    allowed={}
    for ack in acknowledgements:
        path=root/ack['journal']
        if sha(path)!=ack['journal_sha256']:
            raise ValueError('acknowledged journal changed')
        rows=[json.loads(line) for line in path.read_text().splitlines()]
        hits=[r for r in rows if r.get('event')=='request_ended'
              and r.get('request_local_id')==ack['request_local_id']]
        if len(hits)!=1 or hits[0].get('usage')!='UNKNOWN' or row_sha(hits[0])!=ack['event_sha256']:
            raise ValueError('UNKNOWN acknowledgement does not bind one original UNKNOWN event')
        allowed[(ack['journal'],ack['request_local_id'])]=True
    outstanding=[]
    journals=list((root/'hosts').glob('swe-agent/*/episodes/*/provider/requests.jsonl'))
    for journal in journals:
        relative=str(journal.relative_to(root))
        for line in journal.read_text().splitlines():
            row=json.loads(line)
            if row.get('event')=='request_ended' and row.get('usage')=='UNKNOWN':
                if (relative,row.get('request_local_id')) not in allowed:
                    outstanding.append({'journal':relative,'request_local_id':row.get('request_local_id')})
    pause=root/'reports/UNKNOWN-PAUSE.json'
    if pause.exists():
        source=json.loads(pause.read_text()).get('source')
        acknowledged_sources={str((root/rel).parent) for rel,_ in allowed}
        # An unexplained old pause cannot be bypassed by an unrelated approval.
        if source not in acknowledged_sources:
            outstanding.append({'unacknowledged_pause':str(pause)})
    return outstanding


def active_registration(root):
    root=Path(root)
    pointer=root/'inputs/active-registration.json'
    if not pointer.exists():
        return json.loads((root/'inputs/registration.json').read_text())
    active=json.loads(pointer.read_text())
    path=root/active['path']
    if sha(path)!=active['sha256']:
        raise ValueError('active registration hash mismatch')
    value=json.loads(path.read_text())
    if sha(root/'inputs/registration.json')!=value['base_registration_sha256']:
        raise ValueError('original registration changed')
    for rel,digest in value['protected_first_pair'].items():
        if sha(root/rel)!=digest:
            raise ValueError('protected first-pair evidence changed: '+rel)
    for rel,target in value.get('protected_first_pair_symlinks',{}).items():
        path=root/rel
        if not path.is_symlink() or str(path.readlink())!=target:
            raise ValueError('protected first-pair symlink changed: '+rel)
    return value
