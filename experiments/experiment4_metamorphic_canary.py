#!/usr/bin/env python3
"""Replay frozen converters on equivalent Verilog inputs, without API or ORFS.

This is retrospective robustness evidence, not a fresh held-out test. Only the
core graph payload is compared; passing does not establish semantic correctness.
"""
import argparse
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

import torch

from experiment4_semantic_audit import CORE_EDGES, NODES, STAGES, digest, logical_oracle, save


def execute(command, log, timeout):
    env = dict(os.environ, OMP_NUM_THREADS='1', MKL_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1')
    started = time.monotonic()
    with log.open('w') as f:
        p = subprocess.Popen(command, stdout=f, stderr=subprocess.STDOUT, env=env, start_new_session=True)
        try:
            code = p.wait(timeout=timeout)
            status = 'COMPLETED' if code == 0 else 'PROCESS_FAILURE'
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            code = p.wait()
            status = 'TIMEOUT'
    return {'command': command, 'status': status, 'returncode': code,
            'elapsed_seconds': time.monotonic() - started, 'log': str(log)}


def core_payload(graph, include_labels=True):
    ids, payload = {}, {}
    for node in NODES:
        store = graph[node]
        if node == 'pin':
            if len(store.inst_name) != len(store.pin_name):
                raise ValueError('unaligned pin identity')
            names = list(zip(store.inst_name, store.pin_name))
        else:
            names = list(store[{'gate': 'inst_name', 'net': 'net_name', 'io_pin': 'iopin_name'}[node]])
        if len(set(names)) != len(names):
            raise ValueError('duplicate identities')
        ids[node] = names
        rows = sorted(range(len(names)), key=lambda i: names[i])
        payload[node] = {'ids': [names[i] for i in rows], 'x_schema': list(store.x_schema)}
        if include_labels:
            payload[node]['y_schema'] = list(store.y_schema)
        for field in (('x', 'y', 'y_valid_mask') if include_labels else ('x',)):
            if store[field].shape[0] != len(names):
                raise ValueError('unaligned node rows')
            payload[node][field] = store[field][rows].tolist()
    for rel in CORE_EDGES:
        store = graph[rel]
        index = store.edge_index
        if index.dtype != torch.int64 or index.ndim != 2 or index.shape[0] != 2:
            raise ValueError('invalid edge index')
        edges = []
        for i, (a, b) in enumerate(index.T.tolist()):
            if not (0 <= a < len(ids[rel[0]]) and 0 <= b < len(ids[rel[2]])):
                raise ValueError('edge endpoint out of bounds')
            record = [ids[rel[0]][a], ids[rel[2]][b]]
            for field in (('edge_attr', 'y', 'y_valid_mask') if include_labels else ('edge_attr',)):
                if field in store:
                    record.append([field, store[field][i].tolist()])
            edges.append(record)
        payload['|'.join(rel)] = sorted(edges, key=lambda x: json.dumps(x, sort_keys=True))
    payload['global_schema'] = list(graph.global_feature_schema)
    payload['global_values'] = graph.global_features.tolist()
    # NaN is represented consistently here for equality, not exported as JSON data.
    return json.dumps(payload, sort_keys=True, allow_nan=True)


def variant_manifest(source, netlist, destination):
    manifest = json.loads(source.read_text())
    for artifact in manifest['artifacts'].values():
        artifact['path'] = str((source.parent / artifact['path']).resolve())
    manifest['artifacts']['yosys_netlist'].update(path=str(netlist), sha256=digest(netlist))
    manifest['metamorphic_provenance'] = {'source': str(source), 'sha256': digest(source),
                                        'purpose': 'isolated equivalent-input canary'}
    save(destination, manifest)


def perturb_slack(text):
    def replace(match):
        value = float(match.group(1)) + 1.25
        return f' {value:.2f} slack ({"MET" if value >= 0 else "VIOLATED"})'
    result, count = re.subn(r'(?m)^\s*([-+0-9.eE]+)\s+slack\s+\((?:MET|VIOLATED)\)', replace, text)
    if not count:
        raise ValueError('no slack lines to perturb')
    return result


def main(args):
    torch.set_num_threads(1)
    output = args.output.resolve()
    campaign = args.campaign.resolve()
    if output == campaign or campaign in output.parents:
        raise ValueError('canary output must be outside original campaign')
    if output.exists():
        raise ValueError('use a new output directory; canary never overwrites prior runs')
    output.mkdir(parents=True)
    methods = ('r2g-frozen-v3', 'llm-gpt-frozen', 'llm-claude-frozen', 'llm-qwen-frozen')
    cfg = json.loads((campaign / 'methods' / methods[0] / args.task / 'method_config.json').read_text())
    original = Path(cfg['yosys_v'])
    raw = original.read_bytes()
    variant = 'comment_only' if args.perturbation == 'comment' else 'slack_only'
    variants = {'original': raw, variant: raw if variant == 'slack_only' else
                b'/* exp4 comment: sky130_fd_sc_hd__inv_1 exp4_fake (.A(a), .Y(b)); */\n\n' + raw}
    truth = []
    for name, data in variants.items():
        root = output / name
        root.mkdir()
        netlist = root / 'input.v'
        netlist.write_bytes(data)
        variant_manifest(Path(cfg['raw_manifest']), netlist, root / 'manifest.json')
        if name == 'slack_only':
            manifest = json.loads(Path(cfg['timing_manifest']).read_text())
            for field, key in [('timing_max_rpt', 'paths_max'), ('timing_min_rpt', 'paths_min')]:
                report = root / (key + '.rpt')
                report.write_text(perturb_slack(Path(cfg[field]).read_text()))
                manifest['reports'][key].update(path=str(report), sha256=digest(report))
            manifest['synthetic_test'] = 'slack +1.25 ns negative control; not a physical STA result'
            save(root / 'timing_manifest.json', manifest)
        ys = root / 'read.ys'
        ys.write_text(f'read_verilog {json.dumps(str(netlist))}\nwrite_json {json.dumps(str(root / "netlist.json"))}\n')
        result = execute([str(args.yosys), '-Q', '-T', '-s', str(ys)], root / 'yosys.log', 120)
        if result['status'] != 'COMPLETED':
            raise RuntimeError('raw input equivalence check failed: ' + str(result))
        module = json.loads((root / 'netlist.json').read_text())['modules'][cfg['top_module']]
        truth.append((logical_oracle(module), {k: v['type'] for k, v in module['cells'].items()}, module['ports']))
    if truth[0] != truth[1]:
        raise ValueError('mutation changed logical input')
    results = []
    for method in methods:
        state = json.loads((campaign / 'methods' / method / args.task / 'run_state.json').read_text())
        runs = {}
        for name in variants:
            root = output / name / method
            root.mkdir()
            config = dict(cfg, yosys_v=str(output / name / 'input.v'), output_dir=str(root / 'generated'),
                          raw_manifest=str(output / name / 'manifest.json'))
            if name == 'slack_only':
                config.update(timing_manifest=str(output / name / 'timing_manifest.json'),
                              timing_max_rpt=str(output / name / 'paths_max.rpt'),
                              timing_min_rpt=str(output / name / 'paths_min.rpt'))
            config_path = root / 'config.json'
            save(config_path, config)
            records = []
            for i, previous in enumerate(state['commands']):
                command = list(previous['command'])
                command[0] = sys.executable
                command[command.index('--config') + 1] = str(config_path)
                result = execute(command, root / f'step_{i}.log', args.timeout)
                result['script_sha256'] = digest(command[1])
                records.append(result)
                save(root / 'run.json', records)
                if result['status'] != 'COMPLETED':
                    break
            runs[name] = records
        checks = []
        if any(not rs or any(r['status'] != 'COMPLETED' for r in rs) for rs in runs.values()):
            checks.append({'status': 'EXECUTION_FAILURE'})
        else:
            for stage in STAGES:
                try:
                    graphs = [torch.load(output / name / method / 'generated/stages' / stage / 'heterograph.pt',
                                         weights_only=False, map_location='cpu') for name in variants]
                    include_labels = args.perturbation == 'comment'
                    status = 'PASS' if core_payload(graphs[0], include_labels) == core_payload(graphs[1], include_labels) else 'FAIL'
                    row = {'stage': stage, 'status': status}
                    if not include_labels:
                        row['label_response'] = 'CHANGED' if core_payload(graphs[0]) != core_payload(graphs[1]) else 'UNCHANGED'
                    checks.append(row)
                except (KeyError, ValueError, AttributeError, IndexError, OSError, RuntimeError) as exc:
                    checks.append({'stage': stage, 'status': 'UNASSESSABLE', 'error': str(exc)[:400]})
        row = {'method': method, 'checks': checks, 'runs': runs}
        results.append(row)
        save(output / 'report.json', {'role': 'retrospective ' + args.perturbation + ' canary', 'task': args.task,
             'source_sha256': digest(original), 'harness_sha256': digest(__file__),
             'raw_logical_equivalence': True, 'scope': 'named nodes, core edges, features/globals; labels compared only for comment invariance',
             'not_tested': ['all future-input noninterference', 'all auxiliary edges', 'general rename invariance'],
             'rows': results})
        print(json.dumps({'method': method, 'checks': checks}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--task', required=True)
    parser.add_argument('--yosys', type=Path, required=True)
    parser.add_argument('--timeout', type=int, default=300)
    parser.add_argument('--perturbation', choices=['comment', 'slack'], default='comment')
    main(parser.parse_args())
