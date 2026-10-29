"""Once-only C3 dispatcher; preserves all seven ended integration attempts."""
import json
import subprocess
import time

from bridge import ROOT
from continuation_c3 import ROSTER, unstarted, validate
from dispatch_c2 import terminal_status
from dispatch_continuation import save
from phase_gate import require_clear


def dispatch():
    validate()
    require_clear()
    path = ROOT/'reports/continuation-state-c3.json'
    if path.exists():
        raise ValueError('C3 already started; never restart or overwrite')
    host, arm, task = ROSTER[0]
    unstarted(host, arm, task)
    state = {'schema': 'table4-continuation-state-c3', 'paper_data': False,
             'status': 'RUNNING', 'started': time.time(), 'done': [], 'software_withheld': 4}
    save(path, state)
    logpath = ROOT/'reports/rtlscout-fifo_sync4-tehm-c3.log'
    with logpath.open('x') as log:
        process = subprocess.Popen([str(ROOT/'envs/modern/bin/python'), '-u',
            str(ROOT/'adapters/run_scout.py'), '--arm', arm, '--task', task, '--continuation-c3'],
            cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        state['running'] = {'host': host, 'arm': arm, 'task': task, 'pid': process.pid}
        save(path, state)
        from summarize_integration import collect
        save(ROOT/'reports/integration-status.json', collect())
        rc = process.wait()
    status = terminal_status(ROOT/'hosts'/host/arm/'episodes'/(task+'__c3'), rc)
    state.pop('running', None)
    state['done'] = [{'host': host, 'arm': arm, 'task': task, 'rc': rc, 'status': status,
                      'log': str(logpath.relative_to(ROOT))}]
    state.update(ended=time.time())
    if rc or status not in {'ACCEPTED', 'NOT_ACCEPTED'}:
        state.update(status='PAUSED', reason='technical/accounting stop')
    else:
        state.update(status='EDA_COMPLETED_SOFTWARE_WITHHELD')
    save(path, state)
    save(ROOT/'reports/integration-status.json', collect())


if __name__ == '__main__':
    dispatch()
