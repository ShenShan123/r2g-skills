"""Exercise real training graphs on a GPU without accessing test designs."""
import argparse
import copy
import importlib.metadata
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'gnn-node'))
import torch
from stage_train import GraphHead, loader_for
from stage_data import Preprocessor, SchemaFeatureEncoder, load_graph, save_json, digest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    torch.set_num_threads(2)
    torch.manual_seed(42)
    assert torch.cuda.is_available()
    torch.cuda.set_device(0)
    manifest = json.loads(args.manifest.read_text())
    records = sorted((r for r in manifest['records'] if r['split']=='train'),
                     key=lambda r: (r.get('mapped_cells', 0), r['design_id']))
    assert records
    versions = {p: importlib.metadata.version(p) for p in
                ('torch', 'torch-geometric', 'pyg-lib', 'torch-scatter', 'torch-sparse', 'numpy')}
    receipt = dict(status='running', device=torch.cuda.get_device_name(0),
                   capability=torch.cuda.get_device_capability(0), cuda=torch.version.cuda,
                   architectures=torch.cuda.get_arch_list(), python=sys.version,
                   versions=versions, manifest_sha256=digest(args.manifest),
                   test_evaluated=False, cases=[])
    started = time.monotonic()
    for stage in ('cts', 'route'):
        for target in ('wirelength', 'congestion'):
            for record in records:
                raw = load_graph(record, stage, target)
                processor = Preprocessor.fit([raw], target)
                graph = processor.transform(raw)
                if graph.valid_mask.any():
                    break
            else:
                raise ValueError('No training labels')
            settings = SimpleNamespace(pilot=False, layers=3, neighbors=10, batch_size=64)
            batch = next(iter(loader_for(graph, settings, True)))
            for model_name in ('gine', 'mlp'):
                settings_model = SimpleNamespace(hid_dim=32, task='regression', task_level='node',
                    model='gine', num_gnn_layers=3 if model_name=='gine' else 0,
                    src_dst_agg='add', num_head_layers=2, use_bn=False, act_fn='relu',
                    dropout=.1, layer_norm=True)
                cpu = GraphHead(settings_model, SchemaFeatureEncoder(processor.state, 32)).eval()
                gpu = copy.deepcopy(cpu).cuda().eval()
                with torch.no_grad():
                    expected = cpu(batch.clone())[0]
                    actual = gpu(batch.clone().cuda())[0].cpu()
                torch.testing.assert_close(actual, expected, rtol=1e-3, atol=1e-4)
                gpu.train()
                optimizer = torch.optim.Adam(gpu.parameters(), lr=.001)
                pred, _, labels = gpu(batch.clone().cuda())
                loss = torch.nn.functional.smooth_l1_loss(pred.reshape(-1), labels)
                assert torch.isfinite(loss)
                loss.backward()
                gradients = [p.grad for p in gpu.parameters() if p.grad is not None]
                assert gradients and all(torch.isfinite(g).all() for g in gradients)
                optimizer.step()
                assert all(torch.isfinite(p).all() for p in gpu.parameters())
                torch.cuda.synchronize()
                row = dict(stage=stage, target=target, model=model_name,
                           design=record['design_id'], loss=float(loss.detach()),
                           max_cpu_gpu_difference=float((actual-expected).abs().max()))
                receipt['cases'].append(row)
                print(json.dumps(row), flush=True)
    receipt.update(status='PASS', seconds=time.monotonic()-started,
                   peak_cuda_allocated_bytes=torch.cuda.max_memory_allocated())
    save_json(args.output, receipt)
    print(json.dumps(receipt), flush=True)


if __name__ == '__main__':
    main()
