#!/usr/bin/env python3
"""V3: build counterfactual raw design directories (symlinks + replaced route-stage files).

Route-stage artifacts (anything produced at or after global routing):
  5_route.def, 5_1_grt.odb, 5_2_route.odb, 6_final.spef, result.json,
  logs/**/{5_*,6_*}, reports/**/{5_*,6_*}
Variants:
  perturbed  own route files, modified consistently: +1 duplicated wire segment on every 5th
             routed net, GRT-0053 adjusted resources x0.8, finish ws -0.05 ns / tns -1.0 ns
             (6_report.json, result.json, 6_finish.rpt), 5_2_route.json wirelength +1000
  donor      all route-stage files copied from DONOR design
Pre-route files (1_2_yosys.v, 2_floorplan.def, 3_place.def, 4_cts.def, config.mk,
constraint.sdc, rtl/, pre-route logs) are symlinks to the untouched originals.
Usage: v3_make_cf.py RAW_ROOT OUT_ROOT DESIGN VARIANT [DONOR]
"""
import json, os, re, shutil, sys
from pathlib import Path

ROUTE_TOP = {'5_route.def', '5_1_grt.odb', '5_2_route.odb', '6_final.spef', 'result.json'}


def is_route(rel):
    p = Path(rel)
    if len(p.parts) == 1:
        return p.name in ROUTE_TOP
    return p.parts[0] in ('logs', 'reports') and re.match(r'[56]_', p.name) is not None


def main():
    raw, out, design, variant = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4]
    donor = sys.argv[5] if len(sys.argv) > 5 else None
    src = raw / design; dst = out / design
    if dst.exists():
        shutil.rmtree(dst)
    replaced = []
    for p in sorted(src.rglob('*')):
        if p.is_dir():
            continue
        rel = p.relative_to(src)
        q = dst / rel; q.parent.mkdir(parents=True, exist_ok=True)
        if is_route(rel):
            replaced.append(str(rel))
            if variant == 'perturbed':
                shutil.copyfile(p, q)
            continue
        os.symlink(p, q)
    if variant == 'donor':
        dsrc = raw / donor
        for p in sorted(dsrc.rglob('*')):
            if p.is_dir():
                continue
            rel = p.relative_to(dsrc)
            if not is_route(rel):
                continue
            # donor log dirs carry the donor's top-module name; map into this design's log dir
            parts = list(rel.parts)
            if parts[0] in ('logs', 'reports'):
                mine = next((d for d in (src / parts[0]).glob('*/*/base')), None)
                rel2 = (mine.relative_to(src) / parts[-1]) if mine else Path(*parts)
            else:
                rel2 = rel
            q = dst / rel2; q.parent.mkdir(parents=True, exist_ok=True)
            if q.exists() or q.is_symlink():
                q.unlink()
            shutil.copyfile(p, q)
        # remove own route files that had no donor counterpart (none expected)
    else:
        perturb(dst)
    (out / f'{design}.{variant}.manifest.json').write_text(json.dumps(dict(design=design, variant=variant, donor=donor, replaced=replaced), indent=1))
    print('CF', design, variant, len(replaced), 'route-stage files replaced')


def perturb(d):
    route = d / '5_route.def'
    lines = route.read_text().splitlines(keepends=True)
    out, in_nets, k, cur_first = [], False, 0, None
    for ln in lines:
        s = ln.strip()
        if s.startswith('NETS '):
            in_nets = True
        elif s.startswith('END NETS'):
            in_nets = False
        if in_nets:
            m = re.match(r'(\s*)\+ ROUTED (\S+) (\( \S+ \S+ \) \( \S+ \S+ \))(.*)$', ln.rstrip('\n'))
            if m:
                k += 1
                if k % 5 == 0:
                    body = ln.rstrip('\n'); term = body.endswith(';')
                    core = body[:-1].rstrip() if term else body
                    ln = core + f'\n      NEW {m.group(2)} {m.group(3)}' + (' ;' if term else '') + '\n'
        out.append(ln)
    route.write_text(''.join(out))
    for log in d.glob('logs/**/5_1_grt.log'):
        t = log.read_text()
        head, sep, tail = t.rpartition('Routing resources analysis:')

        def scale(m):
            a, b = int(m.group(3)), int(m.group(4))
            nb = max(1, int(b * 0.8)) if b > 0 else 0
            return f'{m.group(1)}{m.group(2)}{a:>13}{nb:>14}{m.group(5)}'
        tail = re.sub(r'^(\S+\s+)(Horizontal|Vertical)\s+(\d+)\s+(\d+)(\s+[-+\d.]+%\s*)$', scale, tail, flags=re.M)
        log.write_text(head + sep + tail)
    for rep in d.glob('logs/**/6_report.json'):
        j = json.loads(rep.read_text())
        ws, tns = j.get('finish__timing__setup__ws'), j.get('finish__timing__setup__tns')
        if ws is not None and abs(ws) < 1e20:
            j['finish__timing__setup__ws'] = ws - 0.05
            j['finish__timing__setup__tns'] = (tns or 0.0) - 1.0
        rep.write_text(json.dumps(j, indent=1))
        r = json.loads((d / 'result.json').read_text())
        if r.get('timing_path_constrained'):
            r['wns_ns'] = j['finish__timing__setup__ws']; r['tns_ns'] = j['finish__timing__setup__tns']
        (d / 'result.json').write_text(json.dumps(r, indent=1))
        for fr in d.glob('reports/**/6_finish.rpt'):
            t = fr.read_text()
            if ws is not None and abs(ws) < 1e20:
                t = re.sub(r'^tns max .*$', f'tns max {j["finish__timing__setup__tns"]:.2f}', t, count=1, flags=re.M)
                t = re.sub(r'^wns max .*$', f'wns max {min(0.0, j["finish__timing__setup__ws"]):.2f}', t, count=1, flags=re.M)
                t = re.sub(r'^worst slack max .*$', f'worst slack max {j["finish__timing__setup__ws"]:.2f}', t, count=1, flags=re.M)
            fr.write_text(t)
    # keep the detailed-router report CONSISTENT with the perturbed DEF (the builder cross-checks
    # them within 1 um; an inconsistent perturbation is refused - recorded separately as a guard)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from gt_raw import parse_nets
    _, segs, _ = parse_nets(route, with_routes=True)
    total = sum(abs(r[3] - r[1]) + abs(r[4] - r[2]) for rows in segs.values() for r in rows) / 2000.0
    for rj in d.glob('logs/**/5_2_route.json'):
        j = json.loads(rj.read_text())
        j['detailedroute__route__wirelength'] = int(round(total))
        rj.write_text(json.dumps(j, indent=1))


if __name__ == '__main__':
    main()
