#!/usr/bin/env python3
"""
Generate a multi-project dashboard for EDA runs.
Produces static HTML files: index.html + per-project detail pages.
"""
from pathlib import Path
import base64
import json
import html
import subprocess
import sys

# Base directory for all EDA runs - configurable via argv or env
BASE = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path('design_cases').resolve()
OUT = BASE / '_dashboard'


def load_json(path: Path, default=None):
    if default is None:
        default = {}
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def fmt(v):
    if v is None:
        return '-'
    if isinstance(v, float):
        return f'{v:.6g}'
    return str(v)


def fmt_timing(k, v):
    """Format timing value with color indicator."""
    if v is None:
        return '-'
    if isinstance(v, (int, float)):
        if v > 1e+30:
            return '<span style="color:#ff9800;font-weight:bold">UNCONSTRAINED</span>'
        if v < -0.001:
            return f'<span style="color:#f44336;font-weight:bold">{v:.4g}</span>'
        if v >= 0:
            return f'<span style="color:#4caf50">{v:.4g}</span>'
    return fmt(v)


def find_latest_run(project: Path):
    backend = project / 'backend'
    if not backend.exists():
        return None
    runs = sorted([d for d in backend.iterdir() if d.is_dir() and d.name.startswith('RUN_')])
    return runs[-1] if runs else None


def infer_project_status(project: Path, latest, summary: str):
    if latest and list(latest.rglob('*.gds')):
        return 'pass'
    s = (summary or '').lower()
    if 'fail' in s or 'error' in s:
        return 'fail'
    if latest is not None:
        return 'running'
    return 'unknown'


def collect_project(project: Path):
    reports = project / 'reports'
    latest = find_latest_run(project)
    ppa = load_json(reports / 'ppa.json', {})
    summary_text = ''
    if (reports / 'demo-summary.md').exists():
        summary_text = (reports / 'demo-summary.md').read_text(encoding='utf-8', errors='ignore')

    raw_spec = ''
    if (project / 'input' / 'raw-spec.md').exists():
        raw_spec = (project / 'input' / 'raw-spec.md').read_text(encoding='utf-8', errors='ignore')

    normalized_spec = ''
    if (project / 'input' / 'normalized-spec.yaml').exists():
        normalized_spec = (project / 'input' / 'normalized-spec.yaml').read_text(encoding='utf-8', errors='ignore')

    # Find GDS, DEF, ODB artifacts
    gds = None
    def_file = None
    odb_file = None
    if latest:
        for f in latest.rglob('*.gds'):
            gds = str(f)
            break
        for f in latest.rglob('*.def'):
            def_file = str(f)
            break
        for f in latest.rglob('*.odb'):
            odb_file = str(f)
            break

    # Find preview image and encode as base64 for embedding
    # Auto-render if GDS exists but no preview image found
    preview = None
    preview_b64 = None
    if reports.exists():
        imgs = sorted(reports.glob('*preview*.png'))
        if not imgs and gds:
            # Auto-render GDS preview
            preview_path = reports / 'gds_preview.png'
            render_script = Path(__file__).parent / 'render_gds_preview.py'
            if render_script.exists():
                try:
                    print(f'  Rendering GDS preview for {project.name}...')
                    subprocess.run(
                        [sys.executable, str(render_script), gds, str(preview_path), '800'],
                        check=True, capture_output=True, timeout=120,
                    )
                    imgs = [preview_path]
                except Exception as e:
                    print(f'  Warning: GDS preview render failed for {project.name}: {e}', file=sys.stderr)
        if imgs:
            preview = imgs[0]
            try:
                preview_b64 = base64.b64encode(preview.read_bytes()).decode('ascii')
            except Exception:
                preview_b64 = None

    progress = load_json(reports / 'progress.json', {})
    run_history = load_json(reports / 'run-history.json', {})
    run_compare = load_json(reports / 'run-compare.json', {})
    diagnosis = load_json(reports / 'diagnosis.json', {})
    drc_result = load_json(reports / 'drc.json', {})
    lvs_result = load_json(reports / 'lvs.json', {})
    rcx_result = load_json(reports / 'rcx.json', {})

    spec_desc = ''
    top_module = ''
    for line in normalized_spec.splitlines():
        if line.startswith('description:') and not spec_desc:
            spec_desc = line.split(':', 1)[1].strip()
        if line.startswith('top_module:') and not top_module:
            top_module = line.split(':', 1)[1].strip()

    raw_spec_brief = raw_spec.strip().splitlines()[0] if raw_spec.strip() else ''

    return {
        'name': project.name,
        'path': str(project),
        'latest_run': str(latest) if latest else None,
        'preview': str(preview) if preview else None,
        'preview_b64': preview_b64,
        'gds': gds,
        'def': def_file,
        'odb': odb_file,
        'ppa': ppa,
        'summary': summary_text,
        'raw_spec': raw_spec,
        'raw_spec_brief': raw_spec_brief,
        'normalized_spec': normalized_spec,
        'spec_desc': spec_desc,
        'top_module': top_module,
        'progress': progress,
        'run_history': run_history,
        'run_compare': run_compare,
        'diagnosis': diagnosis,
        'drc_result': drc_result,
        'lvs_result': lvs_result,
        'rcx_result': rcx_result,
        'status': infer_project_status(project, latest, summary_text),
    }


def status_badge(status):
    colors = {
        'pass': '#4caf50', 'fail': '#f44336',
        'running': '#ff9800', 'unknown': '#9e9e9e'
    }
    color = colors.get(status, '#9e9e9e')
    return f'<span style="background:{color};color:#fff;padding:2px 10px;border-radius:4px;font-weight:bold">{html.escape(status.upper())}</span>'


def geometry_table(ppa):
    """Render detailed geometric info from PPA geometry data."""
    geo = ppa.get('geometry', {}) if ppa else {}
    if not geo:
        return '<p>No geometry data available.</p>'

    # Organize into labeled rows
    labels = {
        'die_area_um2': ('Die Area', 'um²'),
        'core_area_um2': ('Core Area', 'um²'),
        'utilization': ('Utilization', '%'),
        'instance_count': ('Total Instances', ''),
        'stdcell_count': ('Std Cells', ''),
        'stdcell_area_um2': ('Std Cell Area', 'um²'),
        'sequential_count': ('Sequential Cells', ''),
        'clock_buffer_count': ('Clock Buffers', ''),
        'macro_count': ('Macros', ''),
        'macro_area_um2': ('Macro Area', 'um²'),
        'io_count': ('I/O Ports', ''),
        'rows': ('Placement Rows', ''),
        'sites': ('Placement Sites', ''),
        'warnings': ('Flow Warnings', ''),
        'errors': ('Flow Errors', ''),
    }
    rows = []
    for key, (label, unit) in labels.items():
        v = geo.get(key)
        if v is None:
            continue
        if key == 'utilization':
            val_str = f'{v * 100:.2f}%'
        elif isinstance(v, float):
            val_str = f'{v:.4g} {unit}'.strip()
        else:
            val_str = f'{v} {unit}'.strip()
        rows.append(f'<tr><td>{html.escape(label)}</td><td>{html.escape(val_str)}</td></tr>')

    if not rows:
        return '<p>No geometry data available.</p>'
    return f'<table class="geo-table"><tr><th>Property</th><th>Value</th></tr>{"".join(rows)}</table>'


def layout_section(data):
    """Render the GDS layout image alongside geometric info."""
    preview_b64 = data.get('preview_b64')
    ppa = data.get('ppa', {})

    geo_html = geometry_table(ppa)

    if preview_b64:
        img_html = f'<img src="data:image/png;base64,{preview_b64}" alt="GDS Layout" style="max-width:100%;border-radius:8px;border:1px solid #333;">'
    else:
        img_html = '<p style="color:#888;text-align:center;padding:40px;">No GDS preview available.<br>Run <code>render_gds_preview.py</code> to generate.</p>'

    return f'''<div class="layout-row">
    <div class="layout-img">{img_html}</div>
    <div class="layout-info">{geo_html}</div>
</div>'''


def signoff_badge(status):
    colors = {
        'clean': '#4caf50', 'fail': '#f44336', 'complete': '#4caf50',
        'skipped': '#ff9800', 'unknown': '#9e9e9e', 'no_spef': '#9e9e9e',
        'empty': '#ff9800',
        # `stuck` and `timeout` are tool-failure modes (the tool didn't
        # converge but the design itself is not necessarily broken). Mark
        # them yellow rather than red to distinguish from real violations.
        'stuck': '#ffc107', 'timeout': '#ffc107', 'failed': '#f44336',
        'violations': '#f44336',
    }
    color = colors.get(status, '#9e9e9e')
    return f'<span style="background:{color};color:#fff;padding:2px 8px;border-radius:4px;font-size:12px;font-weight:bold">{html.escape(status.upper())}</span>'


def signoff_section(data):
    """Render DRC / LVS / RCX signoff results."""
    drc = data.get('drc_result', {})
    lvs = data.get('lvs_result', {})
    rcx = data.get('rcx_result', {})

    if not drc and not lvs and not rcx:
        return '<p>No signoff checks run yet. Use <code>run_drc.sh</code>, <code>run_lvs.sh</code>, <code>run_rcx.sh</code>.</p>'

    rows = []

    # DRC
    if drc:
        drc_status = drc.get('status', 'unknown')
        violations = drc.get('total_violations')
        viol_str = str(violations) if violations is not None else '-'
        cats = drc.get('categories', {})
        cat_summary = ', '.join(f'{k}: {v["count"]}' for k, v in list(cats.items())[:5]) if cats else '-'
        rows.append(f'<tr><td>DRC</td><td>{signoff_badge(drc_status)}</td><td>{viol_str}</td><td>{html.escape(cat_summary)}</td></tr>')

    # LVS
    if lvs:
        lvs_status = lvs.get('status', 'unknown')
        mismatch = lvs.get('mismatch_count')
        mismatch_str = str(mismatch) if mismatch is not None else '-'
        log_status = lvs.get('log_info', {}).get('log_status', '-')
        rows.append(f'<tr><td>LVS</td><td>{signoff_badge(lvs_status)}</td><td>{mismatch_str}</td><td>{html.escape(log_status)}</td></tr>')

    # RCX
    if rcx:
        rcx_status = rcx.get('status', 'unknown')
        net_count = rcx.get('net_count', '-')
        cap = rcx.get('total_cap_ff')
        cap_str = f'{cap:.2f} fF' if cap is not None else '-'
        rows.append(f'<tr><td>RCX</td><td>{signoff_badge(rcx_status)}</td><td>{net_count}</td><td>{html.escape(cap_str)}</td></tr>')

    if not rows:
        return '<p>No signoff data available.</p>'

    return f'''<table class="signoff-table">
<tr><th>Check</th><th>Status</th><th>Count</th><th>Details</th></tr>
{"".join(rows)}
</table>'''


def power_breakdown(ppa):
    """Render power breakdown bar chart."""
    power = ppa.get('summary', {}).get('power', {}) if ppa else {}
    total = power.get('total_power_w', 0)
    if not total or total <= 0:
        return ''
    internal = power.get('internal_power_w', 0)
    switching = power.get('switching_power_w', 0)
    leakage = power.get('leakage_power_w', 0)
    rows = []
    for label, val, color in [
        ('Internal', internal, '#42a5f5'),
        ('Switching', switching, '#66bb6a'),
        ('Leakage', leakage, '#ef5350'),
    ]:
        pct = (val / total * 100) if total > 0 else 0
        rows.append(
            f'<tr><td>{label}</td><td>{val:.4g} W</td>'
            f'<td><div style="background:{color};width:{pct:.0f}%;height:14px;border-radius:3px;min-width:2px"></div></td>'
            f'<td>{pct:.1f}%</td></tr>'
        )
    return (f'<h3>Power Breakdown</h3>'
            f'<table><tr><th>Component</th><th>Power</th><th></th><th>%</th></tr>'
            f'{"".join(rows)}</table>')


def ppa_table(ppa):
    summary = ppa.get('summary', {}) if ppa else {}
    rows = []
    timing_keys = {'setup_wns', 'setup_tns', 'hold_wns', 'hold_tns'}
    for category, metrics in summary.items():
        if isinstance(metrics, dict):
            for k, v in metrics.items():
                if k in timing_keys:
                    val_html = fmt_timing(k, v)
                else:
                    val_html = html.escape(fmt(v))
                rows.append(f'<tr><td>{html.escape(category)}</td><td>{html.escape(k)}</td><td>{val_html}</td></tr>')
    if not rows:
        return '<p>No PPA data available.</p>'
    return f'<table class="ppa-table"><tr><th>Category</th><th>Metric</th><th>Value</th></tr>{"".join(rows)}</table>'


def render_project_page(data):
    name = html.escape(data['name'])
    status = data['status']
    ppa_html = ppa_table(data.get('ppa'))
    layout_html = layout_section(data)
    signoff_html = signoff_section(data)

    # Progress stages
    stages = data.get('progress', {}).get('stages', [])
    if stages:
        stage_rows = ''.join(
            f'<tr><td>{html.escape(s.get("name",""))}</td>'
            f'<td>{html.escape(s.get("status",""))}</td></tr>'
            for s in stages
        )
        progress_html = f'<table class="stage-table"><tr><th>Stage</th><th>Status</th></tr>{stage_rows}</table>'
    else:
        progress_html = '<p>No progress data yet.</p>'

    # Run history
    runs = data.get('run_history', {}).get('runs', [])
    if runs:
        run_rows = ''.join(
            f'<tr><td>{html.escape(r.get("run",""))}</td>'
            f'<td>{html.escape(r.get("status",""))}</td>'
            f'<td>{fmt(r.get("utilization"))}</td></tr>'
            for r in runs
        )
        history_html = f'<table><tr><th>Run</th><th>Status</th><th>Utilization</th></tr>{run_rows}</table>'
    else:
        history_html = '<p>No run history.</p>'

    # Run compare
    compare = data.get('run_compare', {})
    delta = compare.get('delta', {})
    if delta:
        delta_rows = ''.join(
            f'<tr><td>{html.escape(k)}</td><td>{fmt(v)}</td></tr>'
            for k, v in delta.items() if v is not None
        )
        compare_html = f'<table><tr><th>Metric</th><th>Delta</th></tr>{delta_rows}</table>' if delta_rows else '<p>No comparison data.</p>'
    else:
        compare_html = '<p>No comparison data.</p>'

    # Diagnosis — handle both old (single dict) and new (list) format
    diag = data.get('diagnosis', {})
    if isinstance(diag, dict) and 'issues' in diag:
        issues_list = diag['issues']
    elif isinstance(diag, dict) and diag.get('kind', 'none') != 'none':
        issues_list = [diag]
    else:
        issues_list = []

    if issues_list:
        diag_items = []
        for issue in issues_list:
            kind = html.escape(issue.get('kind', ''))
            summary_text = html.escape(issue.get('summary', ''))
            suggestion = html.escape(issue.get('suggestion', ''))
            diag_items.append(
                f'<div style="margin:8px 0;padding:10px;background:#2a1a1a;border-radius:6px;'
                f'border-left:4px solid #f44336">'
                f'<b>{kind}</b>: {summary_text}<br><i style="color:#aaa">{suggestion}</i></div>'
            )
        diag_html = ''.join(diag_items)
    else:
        diag_html = '<p style="color:#4caf50">No issues detected.</p>'

    page = f'''<!doctype html>
<html><head><meta charset="utf-8"><title>{name}</title>
<meta http-equiv="refresh" content="10">
<style>
body {{ font-family: -apple-system, sans-serif; max-width: 1100px; margin: 40px auto; padding: 0 20px; background: #1a1a2e; color: #e0e0e0; }}
h1, h2 {{ color: #fff; }}
a {{ color: #64b5f6; }}
table {{ border-collapse: collapse; width: 100%; margin: 10px 0; }}
th, td {{ border: 1px solid #333; padding: 8px; text-align: left; }}
th {{ background: #2a2a4a; }}
pre {{ background: #0d0d1a; padding: 15px; border-radius: 8px; overflow-x: auto; font-size: 13px; }}
.card {{ background: #16213e; border-radius: 10px; padding: 20px; margin: 15px 0; }}
.layout-row {{ display: flex; gap: 24px; align-items: flex-start; }}
.layout-img {{ flex: 1; min-width: 0; }}
.layout-info {{ flex: 0 0 320px; }}
.geo-table td:first-child {{ color: #aaa; }}
.geo-table td:last-child {{ color: #64b5f6; font-weight: bold; font-variant-numeric: tabular-nums; }}
.signoff-table td:first-child {{ font-weight: bold; color: #e0e0e0; }}
@media (max-width: 768px) {{ .layout-row {{ flex-direction: column; }} .layout-info {{ flex: 1; }} }}
</style></head><body>
<p><a href="index.html">&larr; All Projects</a></p>
<h1>{name} {status_badge(status)}</h1>

<div class="card">
<h2>GDS Layout</h2>
{layout_html}
</div>

<div class="card">
<h2>Spec</h2>
<pre>{html.escape(data.get("raw_spec","(none)"))}</pre>
<h3>Normalized</h3>
<pre>{html.escape(data.get("normalized_spec","(none)"))}</pre>
</div>

<div class="card">
<h2>PPA</h2>
{ppa_html}
{power_breakdown(data.get('ppa'))}
</div>

<div class="card">
<h2>Signoff Checks (DRC / LVS / RCX)</h2>
{signoff_html}
</div>

<div class="card">
<h2>Progress</h2>
{progress_html}
</div>

<div class="card">
<h2>Run History</h2>
{history_html}
</div>

<div class="card">
<h2>Run Compare</h2>
{compare_html}
</div>

<div class="card">
<h2>Diagnosis</h2>
{diag_html}
</div>

<div class="card">
<h2>Artifacts</h2>
<ul>
<li>GDS: <code>{html.escape(data.get("gds") or "not found")}</code></li>
<li>DEF: <code>{html.escape(data.get("def") or "not found")}</code></li>
<li>ODB: <code>{html.escape(data.get("odb") or "not found")}</code></li>
<li>Latest Run: <code>{html.escape(data.get("latest_run") or "none")}</code></li>
</ul>
</div>

</body></html>'''
    return page


def render_index(projects):
    cards = []
    for p in projects:
        name = html.escape(p['name'])
        desc = html.escape(p.get('spec_desc') or p.get('raw_spec_brief') or '')
        status = p['status']
        ppa = p.get('ppa', {}).get('summary', {})
        util = fmt(ppa.get('area', {}).get('utilization'))

        cards.append(f'''
        <a href="{name}.html" class="card-link">
        <div class="card">
            <h3>{name} {status_badge(status)}</h3>
            <p class="desc">{desc}</p>
            <p class="metric">Utilization: {util}</p>
        </div>
        </a>''')

    page = f'''<!doctype html>
<html><head><meta charset="utf-8"><title>EDA Dashboard</title>
<meta http-equiv="refresh" content="10">
<style>
body {{ font-family: -apple-system, sans-serif; max-width: 1200px; margin: 40px auto; padding: 0 20px; background: #1a1a2e; color: #e0e0e0; }}
h1 {{ color: #fff; text-align: center; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 20px; }}
.card {{ background: #16213e; border-radius: 10px; padding: 20px; transition: transform 0.2s; }}
.card:hover {{ transform: translateY(-3px); }}
.card-link {{ text-decoration: none; color: inherit; }}
h3 {{ color: #fff; margin-top: 0; }}
.desc {{ color: #aaa; font-size: 14px; }}
.metric {{ color: #64b5f6; font-weight: bold; }}
</style></head><body>
<h1>EDA Spec-to-GDS Dashboard</h1>
<p style="text-align:center;color:#888">OpenROAD-flow-scripts | Auto-refresh: 10s</p>
<div class="grid">
{"".join(cards)}
</div>
</body></html>'''
    return page


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    # Find all project directories
    projects = []
    for d in sorted(BASE.iterdir()):
        if d.is_dir() and d.name != '_dashboard' and (d / 'metadata.json').exists():
            projects.append(collect_project(d))

    # Generate index
    (OUT / 'index.html').write_text(render_index(projects), encoding='utf-8')

    # Generate per-project pages
    for p in projects:
        page = render_project_page(p)
        (OUT / f'{p["name"]}.html').write_text(page, encoding='utf-8')

    print(f'Dashboard generated: {OUT}/index.html ({len(projects)} projects)')


if __name__ == '__main__':
    main()
