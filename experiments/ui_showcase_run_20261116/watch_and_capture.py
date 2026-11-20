"""Wait for this frontend job, preserve its final record and take real screenshots."""
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path('/data/yangao/r2g_ui_20261115/showcase_20261116')
job_id = json.loads((ROOT / 'launch.json').read_text())['job_id']
previous = None
last_printed = 0
while True:
    job = json.loads(urllib.request.urlopen('http://127.0.0.1:8770/api/jobs/' + job_id).read())
    design = job['designs'][0]
    status = (job['status'], design['stage'], design['status'])
    if status != previous or time.monotonic() - last_printed >= 60:
        summary = {'elapsed_seconds': round(time.time() - job['created']), 'status': status,
                   'signoff': design['detail'].get('signoff'), 'verify': design['detail'].get('verify')}
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        previous, last_printed = status, time.monotonic()
    (ROOT / 'latest_job.json').write_text(json.dumps(job, ensure_ascii=False, indent=2) + '\n')
    if design['status'] in ('passed', 'failed', 'cancelled') and job['status'] in ('done', 'failed', 'cancelled'):
        break
    time.sleep(15)
assert design['status'] == 'passed', design
subprocess.run([sys.executable, str(ROOT / 'capture_frontend.py')], check=True)
