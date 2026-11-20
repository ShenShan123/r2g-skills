"""Re-export this demo's saved stages with the renderer used by the live UI.

The ORFS subprocess imports an older installed package after clearing PYTHONPATH.
Only display SVGs/metrics are refreshed; source artifacts and verdicts are intact.
"""
import hashlib
import json
import os
import sys
import urllib.request
from pathlib import Path

RELEASE = Path('/data/yangao/r2g_ui_20261115/release')
ROOT = Path('/data/yangao/r2g_ui_20261115/showcase_20261116')
os.environ['R2G_REPO'] = str(RELEASE)
os.environ['ORFS_ROOT'] = '/data/yangao/orfs203'
os.environ['OPENROAD_EXE'] = '/home/yangao/r2g_toolchain/bin/openroad'
sys.path.insert(0, str(RELEASE / 'agent'))
from r2g_agent import stage_views

job_id = json.loads((ROOT / 'launch.json').read_text())['job_id']
job = json.loads(urllib.request.urlopen('http://127.0.0.1:8770/api/jobs/' + job_id).read())
design = job['designs'][0]
project = Path(design['project'])
index = project / 'reports/stage_previews/index.json'
before = json.loads(index.read_text())
records = []
for stage in ['synth', 'floorplan', 'placement', 'cts']:
    entry = before[stage]
    source = project / entry['source']
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    assert digest == entry['source_sha256']
    stage_views.save_snapshot(project, stage, source, entry['caption'], 'synth' if stage == 'synth' else source.suffix[1:],
                              design['detail'], entry['source'], publish=True)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
    after = json.loads(index.read_text())[stage]
    records.append({'stage': stage, 'source': entry['source'], 'source_sha256': digest,
                    'old_image': entry['image'], 'new_image': after['image'], 'metrics': after['metrics']})
(ROOT / 'preview_refresh.json').write_text(json.dumps({'reason': 'Stage hook resolved older installed renderer; use current UI source renderer.',
    'renderer_source': str(Path(stage_views.__file__)), 'renderer_sha256': hashlib.sha256(Path(stage_views.__file__).read_bytes()).hexdigest(),
    'geometry_modified': False, 'verdict_modified': False, 'stages': records}, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(records, ensure_ascii=False), flush=True)
