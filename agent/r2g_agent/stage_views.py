"""Evidence-backed stage views; never rerun a flow or change acceptance.

Snapshots live in reports so slimming backend binaries cannot erase them.
Legacy views use only surviving artifacts and explicitly identify their stage.
"""
from __future__ import annotations

import hashlib
import html
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from functools import lru_cache
from pathlib import Path

STAGES = ('synth', 'floorplan', 'placement', 'cts', 'route', 'repair')
IMAGE_STAGES = STAGES + ('final',)
LABELS = ('综合', 'Floorplan', 'Placement', 'CTS', 'Route', '修复后')
PHYSICAL = {
    'floorplan': ('2_floorplan', '2_4_floorplan_pdn', '2_1_floorplan'),
    'placement': ('3_place', '3_5_place_dp'),
    'cts': ('4_cts', '4_1_cts'),
    'route': ('6_final', '5_route', '5_2_route'),
}
CAPTURE_STAGES = STAGES + ('flow', 'signoff', 'graph', 'verify')
MAX_BYTES = 128 * 1024 * 1024
_EXPORT_LOCK = threading.Semaphore(1)


def safe_file(project, relative):
    p = project / relative
    if not p.resolve().is_relative_to(project.resolve()) or not p.is_file():
        return None
    return p


def read_json(p):
    try:
        data = json.loads(p.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def source(project, stage, run=None, route_only=False):
    runs = sorted((project / 'backend').glob('RUN_*'))
    if stage == 'synth':
        for candidate in ([run] if run else runs):
            p = candidate / 'reports_orfs/synth_stat.txt'
            if p.is_file():
                return p, '综合单元组成（无物理坐标）', 'synth'
    elif runs or run:
        # Never substitute a previous successful attempt for a failed latest run.
        run = run or runs[-1]
        names = PHYSICAL.get(stage, ('6_final', '5_route', '4_cts', '3_place', '2_floorplan'))
        if stage == 'route' and route_only:
            names = ('5_route', '5_2_route', '6_final')
        for stem in names:
            for suffix in ('.def', '.odb'):
                for folder in ('results', 'final'):
                    p = run / folder / (stem + suffix)
                    if p.is_file():
                        return p, {'6': '最终布线', '5': '详细布线', '4': '时钟树', '3': '放置后', '2': '初始布局'}[stem[0]] + ' · 物理视图', suffix[1:]
    return None


def svg(body, width=800, height=500):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}">'
            '<rect width="100%" height="100%" fill="#0b1220"/>' + body + '</svg>').encode()


def render_synth(text):
    counts = {}
    # Yosys 0.64 hierarchical/local-count table and older "type count" tables.
    for line in text.splitlines():
        m = re.match(r'^\s*(\d+)\s+[\d.eE+\-]+\s+\d+\s+[\d.eE+\-]+\s{2,}(\S+)\s*$', line)
        if m and m[2] not in ('cells', 'wires', 'ports'):
            counts[m[2]] = int(m[1])
        m = re.match(r'^\s+(\S+)\s+(\d+)\s*$', line)
        if m and ('_' in m[1] or m[1].startswith('$')):
            counts[m[1]] = int(m[2])
    if not counts:
        raise ValueError('综合报告中没有可识别的单元统计')
    rows = sorted(counts.items(), key=lambda x: -x[1])[:10]
    body = '<text x="30" y="36" fill="#cbd5e1" font-size="18">Cell types · synthesis report</text>'
    for i, (label, n) in enumerate(rows):
        y = 72 + 39 * i
        body += f'<text x="30" y="{y+17}" fill="#cbd5e1" font-size="12">{html.escape(label[:36])}</text>'
        w = 430 * n / rows[0][1]
        body += f'<rect x="280" y="{y}" width="{w}" height="23" rx="4" fill="#22d3ee"/>'
        body += f'<text x="{min(750,290+w)}" y="{y+17}" fill="#cbd5e1" font-size="13">{n}</text>'
    body += '<text x="30" y="480" fill="#94a3b8" font-size="12">Top cell types by count · no placement coordinates at synthesis</text>'
    return svg(body), {'cell_types': len(counts), 'shown_types': len(rows)}


def render_def(text):
    area = re.search(r'DIEAREA\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)', text)
    if not area:
        raise ValueError('DEF 未保存矩形 die 边界')
    x0, y0, x1, y1 = map(int, area.groups())
    if x1 <= x0 or y1 <= y0:
        raise ValueError('DEF die 边界无效')
    scale = 440 / max(x1-x0, y1-y0)
    ox, oy = (800-(x1-x0)*scale)/2, (500-(y1-y0)*scale)/2
    def point(x, y):
        return ox+(float(x)-x0)*scale, 500-oy-(float(y)-y0)*scale
    body = f'<rect x="{ox}" y="{oy}" width="{(x1-x0)*scale}" height="{(y1-y0)*scale}" fill="#111e30" stroke="#64748b"/>'
    for row in re.finditer(r'(?m)^ROW\s+\S+\s+\S+\s+(-?\d+)\s+(-?\d+)\s+\S+\s+DO\s+(\d+)\s+BY\s+\d+\s+STEP\s+(\d+)', text):
        x, y, count, step = map(int, row.groups())
        a, b = point(x, y); c, _ = point(x+(count-1)*step, y)
        body += f'<path d="M{a:.2f},{b:.2f}L{c:.2f},{b:.2f}" stroke="#334155" stroke-width="0.5"/>'
    components = re.search(r'(?ms)^COMPONENTS\s+\d+\s*;(.*?)^END COMPONENTS', text)
    positions = re.findall(r'\+\s+(?:PLACED|FIXED|COVER)\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)', components[1] if components else '')
    stride = max(1, math.ceil(len(positions)/6000))
    for x, y in positions[::stride]:
        px, py = point(x, y)
        body += f'<circle cx="{px:.2f}" cy="{py:.2f}" r="1.2" fill="#22d3ee"/>'
    nets = re.search(r'(?ms)^NETS\s+\d+\s*;(.*?)^END NETS', text)
    segments = []
    if nets:
        for route in re.split(r'\+\s+(?:ROUTED|FIXED|COVER)\s+|\bNEW\s+', nets[1])[1:]:
            route = route.split(';', 1)[0]
            last = None
            for a, b in re.findall(r'\(\s*(-?\d+|\*)\s+(-?\d+|\*)(?:\s+\d+)?\s*\)', route):
                if '*' in (a, b) and last is None:
                    continue
                xy = (last[0] if a == '*' else int(a), last[1] if b == '*' else int(b))
                if last:
                    segments.append((*point(*last), *point(*xy)))
                last = xy
    stride = max(1, math.ceil(len(segments)/12000))
    lines = ''.join(f'M{a:.2f},{b:.2f}L{c:.2f},{d:.2f}' for a,b,c,d in segments[::stride])
    body += f'<path d="{lines}" fill="none" stroke="#a78bfa" stroke-width="0.65" opacity="0.65"/>'
    pins = re.search(r'(?ms)^PINS\s+\d+\s*;(.*?)^END PINS', text)
    if pins:
        for x, y in re.findall(r'\+\s+(?:PLACED|FIXED)\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)', pins[1]):
            px, py = point(x, y)
            body += f'<rect x="{px-1.5:.2f}" y="{py-1.5:.2f}" width="3" height="3" fill="#fbbf24"/>'
    body += '<text x="16" y="486" fill="#94a3b8" font-size="12">Cyan: placed cell origins · violet: signal route centerlines</text>'
    return svg(body), {'placed_cells': len(positions), 'route_segments': len(segments),
                       'sampled': len(positions)>6000 or len(segments)>12000}


def odb_text(path):
    """Read an existing database and export coordinates; never run a flow."""
    from .housekeeping import orfs_flow_dir
    flow = orfs_flow_dir()
    pinned = flow.parent/'tools/install/OpenROAD/bin/openroad' if flow else None
    exe = os.environ.get('OPENROAD_EXE') or (str(pinned) if pinned and pinned.is_file() else shutil.which('openroad'))
    if not exe:
        raise ValueError('需要 OpenROAD 读取已有 ODB 快照')
    with _EXPORT_LOCK, tempfile.TemporaryDirectory(prefix='r2g-stage-view-') as temp:
        root = Path(temp); out = root/'view.def'; script = root/'export.tcl'
        script.write_text('read_db $::env(R2G_PREVIEW_ODB)\nwrite_def $::env(R2G_PREVIEW_DEF)\n')
        env = os.environ.copy()
        env.update(R2G_PREVIEW_ODB=str(path), R2G_PREVIEW_DEF=str(out), OMP_NUM_THREADS='1')
        result = subprocess.run([exe, '-no_splash', '-exit', '-threads', '1', str(script)],
                                env=env, cwd=temp, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
        if result.returncode or not out.is_file():
            raise ValueError('现有 ODB 读取失败，未重跑设计')
        if out.stat().st_size > MAX_BYTES:
            raise ValueError('导出的 DEF 过大，暂不生成轻量预览')
        return out.read_text(errors='replace')


def render(path, mtime, size, kind):
    if size > MAX_BYTES:
        raise ValueError('该阶段文件过大，暂不生成轻量预览')
    # Filesystems may reuse mtime for a same-size rewrite within one clock tick.
    # Geometry history must be keyed by content, never just path/mtime/size.
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    return _render_cached(path, digest, kind)


@lru_cache(maxsize=32)
def _render_cached(path, digest, kind):
    data = Path(path).read_bytes()
    if len(data) > MAX_BYTES or hashlib.sha256(data).hexdigest() != digest:
        raise ValueError('阶段产物仍在写入，稍后再生成预览')
    text = odb_text(path) if kind == 'odb' else data.decode(errors='replace')
    image, metrics = (render_synth if kind == 'synth' else render_def)(text)
    return image, metrics, digest


def capture(project, stage, detail):
    if stage == 'size':
        stage = 'synth'
    if stage == 'flow':
        # ORFS normally saves ODB rather than DEF for these intermediate stages.
        # Capture them before slimming, including partial failed flows.
        for physical in PHYSICAL:
            try:
                capture(project, physical, detail)
            except (OSError, ValueError, subprocess.SubprocessError):
                continue
    if stage in ('flow', 'repair'):
        # Every attempt retains its own small previews, including repair stages.
        # This does not overwrite the original timeline kept for older clients.
        runs = sorted((project / 'backend').glob('RUN_*'))
        if runs:
            for physical in ('synth', *PHYSICAL):
                try:
                    found = source(project, physical, runs[-1], route_only=True)
                    if found:
                        path, caption, kind = found
                        label = str(path.relative_to(project))
                        if safe_file(project, label):
                            save_snapshot(project, physical, path, caption, kind, detail, label, publish=False)
                except (OSError, ValueError, subprocess.SubprocessError):
                    continue
    if stage not in CAPTURE_STAGES:
        return
    found = source(project, stage)
    if not found:
        return
    path, caption, kind = found
    if not safe_file(project, str(path.relative_to(project))):
        return
    save_snapshot(project, stage, path, caption, kind, detail, str(path.relative_to(project)))


def snapshot_run(entry):
    parts = Path(entry.get('source', '')).parts
    return parts[1] if len(parts) >= 3 and parts[0] == 'backend' and re.fullmatch(r'RUN_[\w.-]+', parts[1]) else None


def save_snapshot(project, stage, path, caption, kind, detail, source_label, *, publish=True, run_stage=None):
    st = path.stat()
    image, metrics, digest = render(str(path), st.st_mtime_ns, st.st_size, kind)
    folder = project / 'reports/stage_previews'
    folder.mkdir(parents=True, exist_ok=True)
    image_digest = hashlib.sha256(image).hexdigest()
    filename = image_digest + '.svg'
    if not (folder / filename).exists():
        tmp_image = folder / (filename + '.tmp')
        tmp_image.write_bytes(image)
        tmp_image.replace(folder / filename)
    entry = dict(stage=stage, image='reports/stage_previews/'+filename,
                           source=source_label, source_sha256=digest,
                           geometry_sha256=image_digest,
                           caption=caption, metrics=metrics, captured_at=time.time(),
                           signoff=detail.get('signoff'), verify=detail.get('verify'))
    targets = [(folder, stage)] if publish else []
    run_id = snapshot_run(entry)
    if run_id:
        targets.append((folder / 'runs' / run_id, run_stage or ('final' if stage in ('flow', 'repair', 'signoff', 'graph', 'verify') else stage)))
    for target, key in targets:
        target.mkdir(parents=True, exist_ok=True)
        manifest = read_json(target / 'index.json')
        manifest[key] = dict(entry, stage=key, run=run_id)
        with tempfile.NamedTemporaryFile(mode='w', dir=target, prefix='index.', suffix='.tmp', delete=False) as tmp:
            json.dump(manifest, tmp, ensure_ascii=False, indent=2)
        Path(tmp.name).replace(target / 'index.json')


def capture_completed_stage(project, run, results, flow_stage, phase):
    """Optional Agent producer hook, invoked only after an ORFS stage succeeds."""
    from .housekeeping import orfs_flow_dir
    stage = {'synth':'synth', 'floorplan':'floorplan', 'place':'placement', 'cts':'cts', 'route':'route', 'finish':'route'}.get(flow_stage)
    if not stage or phase not in ('flow', 'repair'):
        return
    flow = orfs_flow_dir()
    if not flow or not results.resolve().is_relative_to((flow/'results').resolve()) or results.name != project.name:
        raise ValueError('阶段预览拒绝读取其他任务的 ORFS 目录')
    if not run.resolve().is_relative_to((project/'backend').resolve()):
        raise ValueError('阶段预览的运行目录越界')
    if stage == 'synth':
        reports = flow/'reports'/results.relative_to(flow/'results')
        path = reports/'synth_stat.txt'
        if path.is_file() and path.resolve().is_relative_to(reports.resolve()):
            label = str((run/'reports_orfs'/path.name).relative_to(project))
            save_snapshot(project, stage, path, '综合单元组成（无物理坐标）', 'synth', {}, label, publish=phase=='flow')
        return
    names = ('5_route', '5_2_route') if flow_stage == 'route' else PHYSICAL[stage]
    for stem in names:
        for suffix in ('.def','.odb'):
            path = results/(stem+suffix)
            if path.is_file() and path.resolve().is_relative_to(results.resolve()):
                label = str((run/'results'/path.name).relative_to(project))
                save_snapshot(project, 'repair' if phase=='repair' and stage=='route' else stage, path,
                              dict(zip(STAGES,LABELS))[stage]+' · 阶段完成快照', suffix[1:], {}, label,
                              publish=phase=='flow' or stage=='route', run_stage='final' if flow_stage=='finish' else stage)
                return


def final_view(project, design, saved):
    """The latest routed result, independently of the physical-stage timeline.

    A preserved pre-repair Route is useful history, but cannot prove what a
    subsequent repair produced. Missing final evidence stays explicitly missing.
    """
    detail = design.get('detail') or {}
    current, status = design.get('stage'), design.get('status')
    terminal = status in ('passed', 'failed', 'cancelled')
    repaired = ('repair' in detail or current == 'repair' or
                detail.get('failed_stage') == 'repair' or bool(saved.get('repair')))
    progressed = current in ('graph', 'verify', 'preview', 'done') or status == 'passed'
    verdict = detail.get('signoff') or {}
    if status == 'cancelled' or (status == 'failed' and not verdict):
        signoff_status = 'interrupted'
    elif current in (None, 'prepare', 'synth', 'size', 'flow', 'signoff'):
        # Recovery merges detail rather than clearing it: a previous attempt's
        # clean verdict can survive through a fresh flow and signoff invocation.
        signoff_status = 'interrupted' if terminal else 'pending'
    elif current == 'repair' and (not terminal or verdict.get('dual_clean') is True):
        # Running repair still carries the initial verdict. A failed repair with
        # an old clean value does not prove that this attempt finished checking.
        signoff_status = 'interrupted' if terminal else 'pending'
    elif verdict.get('dual_clean') is True:
        signoff_status = 'clean'
    elif verdict.get('dual_clean') is False:
        signoff_status = 'failed'
    else:
        signoff_status = 'unknown' if terminal else 'pending'
    label = '修复后的最终版图' if repaired else '最终版图'
    item = dict(stage='final', label=label, available=False,
                state='missing' if terminal else 'pending', repair_attempted=repaired,
                signoff_status=signoff_status,
                caption=('最终版图未保留，无法还原' if terminal else '等待最终布线产物'))
    runs = sorted((project / 'backend').glob('RUN_*'))
    latest = runs[-1].name if runs else None

    def usable(entry):
        return (entry and Path(entry.get('source', '')).stem in PHYSICAL['route']
                and safe_file(project, entry.get('image', ''))
                and (latest is None or snapshot_run(entry) == latest))

    # Later verification captures contain the same final geometry. A repair
    # snapshot also exists on unsuccessful repair, so its verdict is kept separate.
    keys = ('repair', 'verify', 'graph') if repaired else ('verify', 'graph', 'signoff', 'route', 'flow')
    if current in (None, 'prepare', 'synth', 'size'):
        keys = ()
    for key in keys:
        if repaired and key != 'repair' and not progressed:
            continue
        entry = saved.get(key)
        if usable(entry):
            item.update(entry, stage='final', label=label, available=True, state='ready')
            break
    if not item['available'] and current not in (None, 'prepare', 'synth', 'size'):
        # For a running repair, an initial route in the same run is not evidence
        # of a repair result. Require a distinct run or completed repair verdict.
        base_run = snapshot_run(saved.get('flow') or saved.get('route') or {})
        repair_complete = 'repair' in detail and progressed
        new_repair_run = repaired and latest is not None and base_run is not None and latest != base_run
        found = source(project, 'route') if not repaired or repair_complete or new_repair_run else None
        if found:
            path, caption, kind = found
            relative = str(path.relative_to(project))
            if safe_file(project, relative):
                item.update(available=True, state='ready', source=relative, kind=kind, caption=caption)
        # The old PNG producer only ran after dataset verification. In incomplete
        # attempts a leftover PNG has no provenance proving the current result.
        elif status == 'passed' and safe_file(project, 'reports/layout.png'):
            item.update(available=True, state='ready', image='reports/layout.png', source='reports/layout.png')
    if item['available']:
        suffix = {'clean': 'DRC / LVS 双清', 'failed': '签核未通过',
                  'pending': '等待签核结论', 'interrupted': '任务中断，未确认最终签核',
                  'unknown': '签核结论未记录'}[signoff_status]
        item['caption'] = label + ' · ' + suffix
        # Snapshot entries may themselves contain the prior attempt's verdict.
        # Attach a verdict only when the current stage has established it.
        item.pop('signoff', None)
        if signoff_status in ('clean', 'failed'):
            item['signoff'] = verdict
    return item


def _run_snapshot(project, run_id, stage, saved):
    manifest = read_json(project/'reports/stage_previews/runs'/run_id/'index.json')
    candidates = [manifest.get(stage)]
    keys = ('route', 'flow', 'signoff') if stage == 'route' else (stage,)
    candidates.extend(saved.get(key) for key in keys)
    for entry in candidates:
        if not isinstance(entry, dict) or snapshot_run(entry) != run_id:
            continue
        if stage != 'synth' and Path(entry.get('source', '')).stem not in PHYSICAL[stage]:
            continue
        if safe_file(project, entry.get('image', '')):
            return entry
    return None


def _verified_parent(project, run, stage, resume):
    """Only recorded, digest-verified resume lineage can supply old stages."""
    flow_stage = 'place' if stage == 'placement' else stage
    row = (resume.get('parent_lineage') or {}).get(flow_stage) or {}
    parent = row.get('parent_run', '')
    digest = row.get('sha256', '')
    if (row.get('verified') is not True or row.get('source') != 'recorded'
            or not isinstance(parent, str) or not re.fullmatch(r'RUN_[\w.-]+', parent)
            or parent == run.name or not isinstance(digest, str)
            or not re.fullmatch(r'[a-f0-9]{64}', digest) or row.get('parent_sha256') != digest):
        return None
    path = safe_file(project, f'backend/{parent}/stage_artifact_manifest.jsonl')
    if not path:
        return None
    latest = None
    for line in path.read_text().splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and record.get('stage') == flow_stage:
            latest = record
    if not latest or latest.get('status') != 0 or latest.get('sha256') != digest:
        return None
    artifact = row.get('artifact') or latest.get('artifact')
    if artifact and Path(artifact).name == artifact:
        existing = safe_file(project, f'backend/{parent}/results/{artifact}')
        if existing:
            actual = hashlib.sha256()
            with existing.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024*1024), b''):
                    actual.update(chunk)
            if actual.hexdigest() != digest:
                return None
    # A recorded digest for another task is never valid lineage for this one.
    for key in ('platform', 'design', 'flow_variant'):
        if resume.get(key) and latest.get(key) and resume[key] != latest[key]:
            return None
    return parent


def final_views(project, design, saved):
    """One timeline belonging to the final attempt; never mix initial repairs."""
    final = final_view(project, design, saved)
    runs = sorted((project/'backend').glob('RUN_*'))
    run_id = snapshot_run(final)
    # A legacy final PNG carries no RUN provenance. Repaired designs therefore
    # cannot use nearby initial snapshots as proof of the final evolution.
    if not run_id and (not final['available'] or not final['repair_attempted']):
        run_id = runs[-1].name if runs else None
    initial_run = snapshot_run(saved.get('flow') or saved.get('route') or {})
    if not final['available'] and final['repair_attempted'] and run_id == initial_run:
        run_id = None  # Repair has not yet produced a distinct attempt.
    run = project/'backend'/run_id if run_id else None
    if run and not run.resolve().is_relative_to((project/'backend').resolve()):
        run_id = run = None
    terminal = design.get('status') in ('passed', 'failed', 'cancelled')
    current = design.get('stage')
    reached_flow = current not in (None, 'prepare', 'synth', 'size')
    resume = read_json(run/'resume_meta.json') if run else {}
    out = []
    for stage, label in zip(STAGES[:-1], LABELS[:-1]):
        item = dict(stage=stage, label=label, version='final', run=run_id,
                    available=False, state='missing' if terminal else 'pending',
                    caption='最终版本的该阶段快照未保留，无法还原' if terminal else '等待本次运行的阶段产物')
        allowed = stage == 'synth' or reached_flow
        flow_stage = 'place' if stage == 'placement' else stage
        reused = flow_stage in resume.get('reused_stages', [])
        parent = _verified_parent(project, run, stage, resume) if run and reused else None
        if reused and not parent:
            allowed = False
            item['state'] = 'unverified'
            item['caption'] = '该阶段沿用旧产物，但运行血缘未核验，无法确认属于最终版本'
        if run and allowed:
            evidence_run = parent or run_id
            entry = _run_snapshot(project, evidence_run, stage, saved)
            if entry:
                item.update(entry, stage=stage, label=label, run=run_id, available=True, state='ready')
            else:
                found = source(project, stage, project/'backend'/evidence_run, route_only=True)
                if found:
                    path, caption, kind = found
                    relative = str(path.relative_to(project))
                    if safe_file(project, relative):
                        item.update(available=True, state='ready', source=relative, kind=kind, caption=caption)
            if item['available'] and parent:
                item.update(reused_from=parent, caption=item['caption']+f' · 沿用 {parent}（血缘已核验）')
        if not item['available'] and allowed and not final['repair_attempted']:
            # Legacy successful single-flow tasks retained these files outside
            # backend. They are explicitly labelled, never used after repairs.
            if stage == 'placement' and safe_file(project, 'images/3_place.def'):
                item.update(available=True, state='ready', source='images/3_place.def', kind='def',
                            caption='放置后 · 已保留的历史 Placement 快照')
            elif stage == 'route' and design.get('status') == 'passed' and safe_file(project, 'reports/layout.png'):
                item.update(available=True, state='ready', image='reports/layout.png', source='reports/layout.png',
                            caption='最终布线版图（历史 Route 快照未单独保留）')
        out.append(item)
    final.update(version='final', run=run_id)
    out.append(final)
    return out


def views(project, design, version=None):
    saved = read_json(project / 'reports/stage_previews/index.json')
    if version == 'final':
        return final_views(project, design, saved)
    detail = design.get('detail') or {}
    current = design.get('stage')
    terminal = design.get('status') in ('passed', 'failed', 'cancelled')
    reached_flow = current not in (None, 'prepare', 'synth', 'size')
    out = []
    for stage, label in zip(STAGES[:-1], LABELS[:-1]):
        item = dict(stage=stage, label=label, available=False, state='missing' if terminal else 'pending',
                    caption=('历史中间文件已清理，无法还原此阶段' if terminal else '等待该阶段产物'))
        entry = saved.get(stage)
        # Migrate only a snapshot whose filename proves its physical stage.
        if not entry:
            for old in ('flow', 'signoff'):
                candidate = saved.get(old, {})
                stem = Path(candidate.get('source', '')).stem
                if stem in PHYSICAL.get(stage, ()):
                    entry = candidate
                    break
        if entry and safe_file(project, entry.get('image', '')):
            item.update(entry, stage=stage, label=label, available=True, state='ready')
        else:
            found = source(project, stage) if stage == 'synth' or reached_flow else None
            # Use the preserved before-repair run when the earlier UI recorded it.
            base = saved.get('flow', {}).get('source')
            if base and stage in PHYSICAL:
                relative = Path(base)
                if len(relative.parts) >= 3 and relative.parts[0] == 'backend':
                    run = project / Path(*relative.parts[:2])
                    if run.resolve().is_relative_to(project.resolve()):
                        found = source(project, stage, run=run)
            if not found and stage == 'placement' and reached_flow and safe_file(project, 'images/3_place.def'):
                found = (project/'images/3_place.def', '放置后 · 已保留的 Placement 快照', 'def')
            if found:
                path, caption, kind = found
                if safe_file(project, str(path.relative_to(project))):
                    item.update(available=True, state='ready', source=str(path.relative_to(project)), kind=kind, caption=caption)
            elif stage == 'route' and reached_flow and safe_file(project, 'reports/layout.png'):
                # This is the final geometry, not an invented pre-repair state.
                item.update(available=True, state='ready', image='reports/layout.png', source='reports/layout.png',
                            caption='最终布线版图' + ('（包含已完成的修复）' if detail.get('repair') else ''))
        out.append(item)
    repair = saved.get('repair')
    route = out[-1]
    if repair and safe_file(project, repair.get('image', '')):
        # A check that did not change geometry adds no duplicate picture.
        changed = (repair.get('geometry_sha256', repair.get('source_sha256')) != route.get('geometry_sha256', route.get('source_sha256')))
        same_final = route.get('source') == 'reports/layout.png'
        if changed and not same_final:
            out.append(dict(repair, stage='repair', label='修复后', available=True, state='ready',
                            caption=repair.get('caption', '') + ' · 与修复前 Route 对比'))
    out.append(final_view(project, design, saved))
    return out


def image(project, item):
    path = safe_file(project, item.get('image') or item.get('source', ''))
    if not path:
        raise FileNotFoundError('该阶段预览尚未保存')
    if item.get('image'):
        return path.read_bytes(), 'image/png' if path.suffix == '.png' else 'image/svg+xml'
    st = path.stat()
    return render(str(path), st.st_mtime_ns, st.st_size, item['kind'])[0], 'image/svg+xml'


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    for name in ('project','run','results'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--stage', required=True)
    parser.add_argument('--phase', required=True)
    args = parser.parse_args()
    capture_completed_stage(args.project, args.run, args.results, args.stage, args.phase)
