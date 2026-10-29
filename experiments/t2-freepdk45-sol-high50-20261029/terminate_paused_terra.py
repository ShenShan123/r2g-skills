"""User-authorised termination; require the exact paused Terra process tree."""
import hashlib
import json
import os
from pathlib import Path
import signal
import time

from pause_terra import HERE, TERRA, process_table, descendants


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    output=HERE/'terra-operator-termination.json'
    if output.exists():raise RuntimeError('Termination receipt already exists')
    pause=json.loads((HERE/'terra-operator-pause.json').read_text());root=pause['controller']
    table=process_table();command=str(TERRA/'experiment.py').encode()
    if root not in table or command not in table[root]['argv'] or b'run' not in table[root]['argv']:
        raise RuntimeError('Terra controller identity mismatch')
    targets=descendants(table,[root])
    if not targets.issubset(set(pause['stopped_pids'])):raise RuntimeError('Unexpected descendant after pause')
    if any(not table[pid]['state'].startswith(('T','t')) for pid in targets):
        raise RuntimeError('Terra is not fully paused')
    preserved=[TERRA/'registration.json',TERRA/'phase/resource_ledger.jsonl']
    preserved.extend((TERRA/'episodes').glob('*/episode.json'))
    preserved.extend((TERRA/'episodes').glob('*/reports/resource_ledger.jsonl'))
    preserved.extend((TERRA/'episodes').glob('*/*.response.json'))
    hashes={str(p):digest(p) for p in preserved if p.is_file()}
    if hashes[str(TERRA/'registration.json')]!=pause['terra_registration_sha256']:
        raise RuntimeError('Terra registration drift')
    # No SIGCONT: a stopped worker must never send another request while dying.
    order=sorted(targets-{root})+[root]
    for pid in order:
        try:os.kill(pid,signal.SIGKILL)
        except ProcessLookupError:pass
    survivors={}
    for _ in range(10):
        latest=process_table()
        survivors={pid:latest[pid]['state'] for pid in targets if pid in latest and not latest[pid]['state'].startswith('Z')}
        if not survivors:break
        time.sleep(0.1)
    changed=[p for p,h in hashes.items() if not Path(p).exists() or digest(Path(p))!=h]
    data={'status':'TERMINATED_BY_USER' if not survivors and not changed else 'TERMINATION_REQUIRES_REVIEW',
        'time':time.time(),'signal':'SIGKILL','target_pids':sorted(targets),'remaining_non_zombie_processes':survivors,
        'record_hashes_before_and_after_match':not changed,'changed_records':changed,'preserved_record_hashes':hashes,
        'raw_operator_interrupted_cells_not_relabelled_as_failures':True,'no_files_deleted':True}
    with output.open('x') as stream:json.dump(data,stream,indent=2,sort_keys=True)
    print(json.dumps({k:v for k,v in data.items() if k!='preserved_record_hashes'}))
    if survivors or changed:raise SystemExit(1)


if __name__=='__main__':main()
