"""Refresh the repair run's saved Route SVG using the current UI renderer."""
import hashlib
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path('/data/yangao/r2g_ui_20261115/showcase_20261116')
RELEASE = Path('/data/yangao/r2g_ui_20261115/release')
os.environ.update(R2G_REPO=str(RELEASE), ORFS_ROOT='/data/yangao/orfs203',
                  OPENROAD_EXE='/home/yangao/r2g_toolchain/bin/openroad')
sys.path.insert(0, str(RELEASE / 'agent'))
from r2g_agent import stage_views

job_id = json.loads((ROOT / 'launch.json').read_text())['job_id']
job = json.loads(urllib.request.urlopen('http://127.0.0.1:8770/api/jobs/' + job_id).read())
design = job['designs'][0]
project = Path(design['project'])
run = sorted((project / 'backend').glob('RUN_*'))[-1]
index = project / 'reports/stage_previews/runs' / run.name / 'index.json'
before = json.loads(index.read_text())['route']
source = project / before['source']
digest = hashlib.sha256(source.read_bytes()).hexdigest()
assert digest == before['source_sha256']
stage_views.save_snapshot(project, 'route', source, before['caption'], source.suffix[1:], design['detail'],
                          before['source'], publish=False, run_stage='route')
assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
after = json.loads(index.read_text())['route']
(ROOT / 'route_preview_refresh.json').write_text(json.dumps({'run': run.name,
    'source': before['source'], 'source_sha256': digest, 'old_image': before['image'],
    'new_image': after['image'], 'metrics': after['metrics'], 'geometry_modified': False,
    'verdict_modified': False}, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(after['metrics'], ensure_ascii=False), flush=True)
