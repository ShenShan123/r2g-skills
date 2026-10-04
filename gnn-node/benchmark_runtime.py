"""Bounded throughput-only benchmark of the existing stage training pipeline.

No formal split, prediction metrics, reusable weights or held-out evaluation.
All training seeds are used, unlike the 128-seed software pilot.
"""
import argparse
from collections import Counter
import gc
import json
import math
import os
from pathlib import Path
import random
import statistics
import time
from types import SimpleNamespace

import numpy as np
import torch

from model import GraphHead
from stage_data import Preprocessor, SchemaFeatureEncoder, digest, load_graph, save_json
from stage_train import loader_for


def band(cells):
    return 'small' if cells < 500 else 'medium' if cells < 2000 else 'large'


def select_cases(checkpoints):
    candidates = []
    for c in checkpoints:
        if c['status'] != 'PASS' or c.get('audit', {}).get('status') != 'PASS':
            continue
        if any(c['label_counts'][s][t]['valid'] == 0
               for s in ('cts', 'route') for t in ('wirelength', 'congestion')):
            continue
        if c['record'].get('split') != 'unassigned':
            raise ValueError('Performance sampling must not select from a formal split')
        candidates.append(c)
    selected = []
    for size in ('small', 'medium', 'large'):
        rows = sorted((c for c in candidates if band(c['record']['mapped_cells']) == size),
                      key=lambda c: (c['record']['mapped_cells'], c['task_id']))
        if len(rows) < 2:
            raise ValueError('Need two eligible cases per size band')
        selected += [rows[(len(rows)-1)//2], rows[-1]]
    return selected


def projections(rows, populations, epochs, seeds):
    """Planning envelope from per-cell timing; not a confidence interval."""
    results = {}
    for name, population in populations.items():
        configs = []
        for stage in ('cts', 'route'):
            for target in ('wirelength', 'congestion'):
                selected = [r for r in rows if r['stage'] == stage and r['target'] == target]
                if len(selected) != 6:
                    continue
                totals = {k: 0. for k in ('low', 'central', 'high')}
                for record in population:
                    sample = [r for r in selected if r['size_band'] == band(record['mapped_cells'])]
                    # The existing split helper uses approximately 60/20/20 families.
                    rates = [(0.6*r['train_seconds_median'] + 0.2*r['inference_seconds_median']) /
                             r['mapped_cells'] for r in sample]
                    for key, rate in zip(totals, (min(rates), statistics.median(rates), max(rates))):
                        totals[key] += rate * record['mapped_cells'] * epochs * seeds
                configs.append(dict(stage=stage, target=target, hours={k: v/3600 for k,v in totals.items()}))
        results[name] = dict(designs=len(population), epochs=epochs, seeds=seeds,
                            configurations=configs,
                            total_hours={k: sum(c['hours'][k] for c in configs) for k in ('low','central','high')})
    return results


def time_pass(model, data, optimizer, args, device, training):
    model.train(training)
    torch.cuda.synchronize(device)
    begin = time.perf_counter()
    count, seen = 0, 0
    context = torch.enable_grad() if training else torch.no_grad()
    with context:
        for batch in loader_for(data, args, training):
            seen += batch.batch_size
            if training:
                optimizer.zero_grad()
            pred, _, y = model(batch.to(device))
            if training:
                loss = torch.nn.functional.smooth_l1_loss(pred.reshape(-1), y)
                if not torch.isfinite(loss):
                    raise ValueError('Nonfinite loss in runtime benchmark')
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1., error_if_nonfinite=True)
                optimizer.step()
                # Match stage_train's per-batch synchronization, but record no loss.
                float(loss.detach())
            count += 1
    torch.cuda.synchronize(device)
    if seen != int(data.valid_mask.sum()):
        raise ValueError('Runtime benchmark must visit every valid seed')
    return dict(seconds=time.perf_counter()-begin, batches=count, seeds=seen)


def run(args):
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise FileExistsError('Use an empty benchmark output directory')
    torch.set_num_threads(args.cpu_threads)
    random.seed(42)
    np.random.seed(42)
    torch.manual_seed(42)
    if torch.cuda.device_count() != 1:
        raise ValueError('Expose exactly one allocated GPU with CUDA_VISIBLE_DEVICES')
    device = torch.device('cuda:0')
    torch.cuda.set_per_process_memory_fraction(.2, device)
    free, total = torch.cuda.mem_get_info(device)
    if free < 8*1024**3:
        raise RuntimeError('Less than 8 GiB free on selected GPU')
    root = Path(args.corpus)
    paths = sorted((root/'checkpoints').glob('*.json'))
    checkpoints = [json.loads(p.read_text()) for p in paths]
    cases = select_cases(checkpoints)
    plan = json.loads((root/'plan.json').read_text())
    populations = {'exported': [c['record'] for c in checkpoints if c['status']=='PASS'],
                   'candidate_pool': plan['rows']}
    identity = dict(purpose='throughput_only_not_accuracy', device=torch.cuda.get_device_name(device),
        cuda_visible_devices=os.environ.get('CUDA_VISIBLE_DEVICES'), torch_version=torch.__version__,
        config=vars(args), cpu_affinity=sorted(os.sched_getaffinity(0)),
        selected=[dict(task_id=c['task_id'],mapped_cells=c['record']['mapped_cells'],
                       size_band=band(c['record']['mapped_cells'])) for c in cases],
        selection_rule='median and maximum mapped cells in each size band; no score-based selection',
        code_hashes={p.name:digest(p) for p in Path(__file__).parent.glob('*.py')},
        checkpoint_hashes={str(p):digest(p) for p in paths if p.stem in {c['task_id'] for c in cases}},
        corpus_plan_sha256=digest(root/'plan.json'), test_evaluated=False, formal_training_started=False,
        weights_saved=False, gpu_allocator_limit_fraction=.2)
    save_json(out/'plan.json', identity)
    results = []
    for stage in ('cts','route'):
        for target in ('wirelength','congestion'):
            raw = []
            loads = {}
            for c in cases:
                save_json(out/'status.json',dict(status='loading',stage=stage,target=target,active=c['task_id'],completed=len(results)))
                t=time.perf_counter()
                if c['audit']['graph_sha256'] != {s:v['sha256'] for s,v in c['record']['graphs'].items()}:
                    raise ValueError('Independent audit does not match selected tensors')
                raw.append(load_graph(c['record'],stage,target))
                loads[c['task_id']]=time.perf_counter()-t
            t=time.perf_counter()
            processor=Preprocessor.fit(raw,target)
            data=[processor.transform(g) for g in raw]
            preprocessing=time.perf_counter()-t
            del raw
            for c,graph in zip(cases,data):
                save_json(out/'status.json',dict(status='timing',stage=stage,target=target,active=c['task_id'],completed=len(results)))
                torch.manual_seed(42)
                model_args=SimpleNamespace(hid_dim=args.hidden,task='regression',task_level='node',
                    model='gine',num_gnn_layers=args.layers,src_dst_agg='add',num_head_layers=2,
                    use_bn=False,act_fn='relu',dropout=.1,layer_norm=True)
                model=GraphHead(model_args,SchemaFeatureEncoder(processor.state,args.hidden)).to(device)
                optimizer=torch.optim.Adam(model.parameters(),lr=.001,weight_decay=1e-5)
                torch.cuda.reset_peak_memory_stats(device)
                passes=[]
                for epoch in range(args.measured_epochs+1):
                    passes.append(dict(epoch=epoch,warmup=epoch==0,
                        train=time_pass(model,graph,optimizer,args,device,True),
                        inference=time_pass(model,graph,optimizer,args,device,False)))
                row=dict(design_id=c['task_id'],mapped_cells=c['record']['mapped_cells'],
                    size_band=band(c['record']['mapped_cells']),stage=stage,target=target,
                    nodes=graph.num_nodes,edges=graph.num_edges,valid_seeds=int(graph.valid_mask.sum()),
                    parameters=sum(p.numel() for p in model.parameters()),
                    load_seconds=loads[c['task_id']],preprocess_seconds_for_six_designs=preprocessing,
                    train_seconds_median=statistics.median(p['train']['seconds'] for p in passes[1:]),
                    inference_seconds_median=statistics.median(p['inference']['seconds'] for p in passes[1:]),
                    gpu_peak_allocated_mib=torch.cuda.max_memory_allocated(device)/1024**2,
                    gpu_peak_reserved_mib=torch.cuda.max_memory_reserved(device)/1024**2,passes=passes)
                results.append(row)
                save_json(out/'measurements.json',results)
                print(json.dumps({k:row[k] for k in ('design_id','stage','target','train_seconds_median','gpu_peak_allocated_mib')}),flush=True)
                del model,optimizer
                gc.collect()
                torch.cuda.empty_cache()
            del data,processor
            gc.collect()
    result=dict(status='complete',test_evaluated=False,formal_training_started=False,
                measurements=len(results),projections=projections(results,populations,args.project_epochs,args.project_seeds),
                caveats=['60/20/20 design-equivalent split approximation; actual split is by family',
                    'Per-cell extrapolation, not a confidence interval; unexported larger designs may be slower',
                    'Excludes formal metric aggregation, checkpoint IO and final test evaluation',
                    'Only current logical-incidence GINE adapter; not a measured B-F sweep',
                    'Random benchmark models discarded; no predictive scores or reusable weights'])
    save_json(out/'result.json',result)
    save_json(out/'status.json',dict(status='complete',completed=len(results)))
    print(json.dumps(result),flush=True)


if __name__ == '__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--corpus',required=True)
    p.add_argument('--output',required=True)
    p.add_argument('--hidden',type=int,default=32)
    p.add_argument('--layers',type=int,default=3)
    p.add_argument('--neighbors',type=int,default=10)
    p.add_argument('--batch-size',type=int,default=128)
    p.add_argument('--cpu-threads',type=int,default=2)
    p.add_argument('--measured-epochs',type=int,default=2)
    p.add_argument('--project-epochs',type=int,default=100)
    p.add_argument('--project-seeds',type=int,default=3)
    args=p.parse_args()
    args.pilot=False
    if min(args.measured_epochs,args.batch_size,args.layers,args.cpu_threads)<1:
        p.error('Positive bounds required')
    run(args)
