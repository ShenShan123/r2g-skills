"""Prepare candidate figures from existing, signed-off outputs; never run EDA."""
import hashlib
import json
import shutil
from pathlib import Path

import cairo
import gi
from PIL import Image, ImageDraw, ImageFont

gi.require_version('Rsvg', '2.0')
from gi.repository import Rsvg

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
SHOWCASE = REPO / 'experiments/layout_showcase_20261116'
manifest = json.loads((SHOWCASE / 'manifest.json').read_text())
names = ['jpeg_idct_transpose', 'qoi_decoder', 'systolic_accel_top', 'picorv32_axi']
font = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
title = ImageFont.truetype(font, 28)
label = ImageFont.truetype(font, 21)
small = ImageFont.truetype(font, 16)
records = []
screen = Image.new('RGB', (1200, 4 * 345 + 60), '#0b1220')
draw = ImageDraw.Draw(screen)
draw.text((24, 15), 'Existing placement snapshot (legacy view) / Final layout (current UI)', font=label, fill='white')

for number, name in enumerate(names, 1):
    record = next(r for r in manifest['selection'] if r['top'] == name)
    project = Path(record['source_image']).parents[1]
    gate = json.loads((project / 'reports/signoff_gate.json').read_text())
    assert gate['status'] == 'pass'
    assert json.loads((project / 'reports/drc.json').read_text())['status'] == 'clean'
    assert json.loads((project / 'reports/lvs.json').read_text())['status'] == 'clean'
    verify = json.loads((project / 'reports/verify.json').read_text())
    assert verify['results'] and all(r['ok'] for r in verify['results'])
    fingerprint = gate['checks']['binding']['def_fingerprint']
    assert hashlib.sha256(Path(fingerprint['path']).read_bytes()).hexdigest() == fingerprint['sha256']
    target = ROOT / f'{number:02d}_{name}.png'
    shutil.copyfile(SHOWCASE / record['ui_style_image'], target)
    source_svg = SHOWCASE / record['ui_style_svg']
    shutil.copyfile(source_svg, target.with_suffix('.svg'))
    index = json.loads((project / 'reports/stage_previews/index.json').read_text())
    placement = project / index['placement']['image']
    # Rasterize the archived placement image without changing its geometry/style.
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 800, 500)
    viewport = Rsvg.Rectangle()
    viewport.x = viewport.y = 0
    viewport.width, viewport.height = 800, 500
    Rsvg.Handle.new_from_data(placement.read_bytes()).render_document(cairo.Context(surface), viewport)
    legacy = ROOT / f'{number:02d}_{name}_placement_archived.png'
    surface.write_to_png(str(legacy))
    surface.finish()
    y = 60 + (number - 1) * 345
    with Image.open(legacy) as image:
        screen.paste(image.resize((480, 300), Image.LANCZOS), (15, y))
    with Image.open(target) as image:
        screen.paste(image.convert('RGB').resize((300, 300), Image.LANCZOS), (520, y))
    draw.text((850, y + 80), f'{number:02d} {name}', font=label, fill='white')
    draw.text((850, y + 115), 'DRC clean / LVS clean', font=small, fill='#a7ddce')
    records.append({'number': number, 'top': name, 'project': str(project),
                    'image': target.name, 'svg': target.with_suffix('.svg').name,
                    'source_image': str(SHOWCASE / record['ui_style_image']),
                    'image_sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
                    'source_def': fingerprint, 'drc': 'clean', 'lvs': 'clean',
                    'verify_passed': len(verify['results']),
                    'archived_placement_svg': str(placement),
                    'archived_placement_source': index['placement']['source'],
                    'archived_placement_source_sha256': index['placement']['source_sha256']})
screen.save(ROOT / 'screening_placement_and_final.png')

board = Image.new('RGB', (1400, 1600), '#0b1220')
draw = ImageDraw.Draw(board)
draw.text((32, 22), 'R2G | Final layout candidates', font=title, fill='white')
draw.text((32, 65), 'Existing signed-off results | Same renderer as UI | No new flow run', font=label, fill='#a6b7cf')
for record in records:
    i = record['number'] - 1
    x, y = 30 + (i % 2) * 700, 120 + (i // 2) * 730
    with Image.open(ROOT / record['image']) as image:
        board.paste(image.convert('RGB').resize((640, 640), Image.LANCZOS), (x, y))
    draw.text((x + 12, y + 642), f"{record['number']:02d}  {record['top']}", font=label, fill='white')
    draw.text((x + 12, y + 673), 'Nangate45 | DRC clean | LVS clean', font=small, fill='#a6b7cf')
board.save(ROOT / 'candidates.png')
(ROOT / 'manifest.json').write_text(json.dumps({'flow_rerun': False,
    'geometry_modified': False, 'renderer_modified': False,
    'view': 'Existing final DEF rendered by unmodified current UI renderer; physical details may be sampled.',
    'candidates': records}, ensure_ascii=False, indent=2) + '\n')
print('Prepared four existing candidates; no new job submitted.')
