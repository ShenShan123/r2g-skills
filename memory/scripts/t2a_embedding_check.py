#!/usr/bin/env python3
"""Verify the pinned public embedding downloads and smoke-test CPU retrieval offline.

Run with the isolated t2a-embeddings environment, never the EDA Python environment.
The receipt distinguishes local smoke-test costs from unmeasured installation costs.
"""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import resource
import time

PINS = {'all-mpnet-base-v2': ('e8c3b32edf5434bc2275fc9bab85f82640a19130', 768),
        'all-MiniLM-L6-v2': ('1110a243fdf4706b3f48f1d95db1a4f5529b4d41', 384)}


def check(root):
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_IMPLICIT_TOKEN='1')
    import faiss
    import numpy as np
    import torch
    from sentence_transformers import SentenceTransformer
    torch.set_num_threads(2)
    started, cpu = time.monotonic(), time.process_time()
    records = {}
    for model, (revision, dim) in PINS.items():
        path = root / model
        files = {}
        for file in sorted(path.rglob('*')):
            if not file.is_file() or '.cache' in file.relative_to(path).parts:
                continue
            rel = str(file.relative_to(path))
            metadata = (path / '.cache/huggingface/download' / (rel + '.metadata')).read_text().splitlines()
            assert metadata[0] == revision, rel
            data = file.read_bytes()
            sha = hashlib.sha256(data).hexdigest()
            actual = sha if len(metadata[1]) == 64 else hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
            assert actual == metadata[1], rel
            files[rel] = {'sha256': sha, 'bytes': len(data), 'hub_digest_verified': True}
        encoder = SentenceTransformer(str(path), device='cpu', local_files_only=True, trust_remote_code=False)
        vectors = encoder.encode(['routing congestion repair', 'cell timing optimization'], normalize_embeddings=True)
        assert vectors.shape == (2, dim) and np.isfinite(vectors).all()
        index = faiss.IndexFlatIP(dim)
        index.add(vectors.astype('float32'))
        _, ids = index.search(vectors[:1].astype('float32'), 1)
        assert int(ids[0, 0]) == 0
        records[model] = {'revision': revision, 'dimension': dim, 'files': files, 'offline_cpu_smoke': 'PASS'}
    out = {'models': records, 'package_versions': {d.metadata['Name']: d.version for d in importlib.metadata.distributions()},
           'wall_s': time.monotonic() - started, 'cpu_s': time.process_time() - cpu,
           'peak_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
           'llm_requests': 0, 'gpu_used': False,
           'installation_wall_cpu': 'UNKNOWN; package-manager logs retained in the session, not inferred as zero'}
    receipt = root / 'verified_embedding_receipt.json'
    with receipt.open('x') as f:
        json.dump(out, f, indent=2, sort_keys=True)
        f.write('\n')
    print(json.dumps({'receipt': str(receipt), 'models': {m: v['offline_cpu_smoke'] for m, v in records.items()}}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    check(parser.parse_args().root.resolve())
