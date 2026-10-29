"""Cross-check dataset-server sampling against the exact pinned Parquet bytes."""
import hashlib
import io
import json
from pathlib import Path
import urllib.request

from select_tasks import ROOT, DATASET, PUBLIC, select, save


def main():
    import pyarrow.parquet as pq
    receipt = ROOT/'inputs/dataset-pin-verification.json'
    if receipt.exists():
        raise ValueError('pin verification already recorded')
    meta = json.loads((ROOT/'inputs/public-tasks.json').read_text())
    revision = meta['revision']
    with urllib.request.urlopen(f'https://huggingface.co/api/datasets/{DATASET}/tree/{revision}/data',timeout=90) as r:
        files = json.load(r)
    rows, hashes = [], {}
    for item in sorted(files,key=lambda x:x['path']):
        if not item['path'].endswith('.parquet'):
            continue
        url = f'https://huggingface.co/datasets/{DATASET}/resolve/{revision}/'+item['path']
        with urllib.request.urlopen(url,timeout=120) as r:
            raw = r.read()
        path = ROOT/'inputs/evaluator-private'/Path(item['path']).name
        with path.open('xb') as out:
            out.write(raw)
        path.chmod(0o600)
        hashes[item['path']] = hashlib.sha256(raw).hexdigest()
        rows.extend(pq.read_table(io.BytesIO(raw)).to_pylist())
    if len(rows)!=500:
        raise ValueError('pinned Parquet does not contain 500 tasks')
    chosen = select(rows)
    old_private=json.loads((ROOT/'inputs/evaluator-private/tasks.json').read_text())
    if chosen!=old_private:
        raise ValueError('dataset-server rows do not exactly match the pinned version; no launch')
    public=[{k:r[k] for k in sorted(PUBLIC)} for r in chosen]
    if public!=meta['public_tasks']:
        raise ValueError('public roster changed')
    save(receipt,{'status':'PASS','revision':revision,'raw_parquet_sha256':hashes,
                  'all_20_private_records_identical':True,'all_public_inputs_identical':True})
    print('Pinned Parquet verified: 500 rows; selected 20 records exactly match.')


if __name__=='__main__':
    main()
