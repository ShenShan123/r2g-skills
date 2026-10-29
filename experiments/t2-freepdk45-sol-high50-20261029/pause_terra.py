"""Identity-bound SIGSTOP of only the user-requested Terra process tree."""
import hashlib
import json
import os
from pathlib import Path
import signal
import time

HERE=Path(__file__).resolve().parent
TERRA=HERE.parent/'t2-freepdk45-terra-high50-20261029'


def process_table():
    result={}
    for p in Path('/proc').iterdir():
        if not p.name.isdigit():continue
        try:
            fields={k:v.strip() for s in (p/'status').read_text().splitlines() if ':' in s for k,v in [s.split(':',1)]}
            if int(fields['Uid'].split()[0])!=os.getuid():continue
            result[int(p.name)]={'ppid':int(fields['PPid']),'state':fields['State'],
                                 'argv':(p/'cmdline').read_bytes().split(b'\0')}
        except (OSError,KeyError,ValueError):continue
    return result


def descendants(table, roots):
    found=set(roots)
    while True:
        more={pid for pid,p in table.items() if p['ppid'] in found}-found
        if not more:return found
        found.update(more)


def main():
    receipt=HERE/'terra-operator-pause.json'
    if receipt.exists():raise RuntimeError('Pause receipt already exists')
    launch=json.loads((TERRA/'launch.json').read_text());controller=launch['controller_pid']
    table=process_table();wanted=str(TERRA/'experiment.py').encode()
    roots=[controller,*launch['lanes'].values()]
    if controller not in table or wanted not in table[controller]['argv'] or b'run' not in table[controller]['argv']:
        raise RuntimeError('Terra controller identity differs')
    stopped=set();states={}
    # Stop producers first, then their running descendants, with re-discovery.
    for root in roots:
        if root not in table:continue
        if wanted not in table[root]['argv']:raise RuntimeError('Terra lane identity differs')
        os.kill(root,signal.SIGSTOP);stopped.add(root)
    for _ in range(4):
        table=process_table()
        for pid in descendants(table,roots):
            if pid in stopped or pid not in table:continue
            try:os.kill(pid,signal.SIGSTOP);stopped.add(pid)
            except ProcessLookupError:pass
    table=process_table();alive={pid for pid in stopped if pid in table}
    not_stopped={pid:table[pid]['state'] for pid in alive if not table[pid]['state'].startswith(('T','t'))}
    if not_stopped:raise RuntimeError('Processes not stopped: '+str(not_stopped))
    counts={}
    for f in (TERRA/'episodes').glob('*/episode.json'):
        s=json.loads(f.read_text())['status'];counts[s]=counts.get(s,0)+1
    data={'status':'OPERATOR_PAUSED_FOR_MODEL_SWITCH','signal':'SIGSTOP','controller':controller,
          'stopped_pids':sorted(alive),'time':time.time(),'episode_status_counts_at_pause':counts,
          'raw_running_cells_are_operator_paused_not_measured_failures':True,
          'no_rerun_or_model_substitution_inside_terra':True,'terra_registration':str(TERRA/'registration.json'),
          'terra_registration_sha256':hashlib.sha256((TERRA/'registration.json').read_bytes()).hexdigest()}
    with receipt.open('x') as stream:json.dump(data,stream,indent=2,sort_keys=True)
    print(json.dumps(data))


if __name__=='__main__':main()
