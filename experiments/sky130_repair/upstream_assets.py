"""Install hash-verified original memory data into a private repair directory."""
import hashlib
import json
from pathlib import Path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def restore_assets(bundle, root, rtl, original_files):
    manifest_path=Path(bundle)/root.name/'manifest.json'
    if not manifest_path.is_file():return {},{},None
    manifest=json.loads(manifest_path.read_text())
    if manifest['source_root']!=root.name:raise ValueError('asset repository mismatch')
    identities={f['path']:f for c in manifest['source_identities'] for f in c['files']}
    for original in original_files:
        rel=str(original.relative_to(root))
        if rel not in identities or sha(original)!=identities[rel]['sha256']:
            raise ValueError('frozen source does not match audited upstream source: '+rel)
    restored={}
    for entry in manifest['files']:
        rel=Path(entry['path'])
        if rel.is_absolute() or '..' in rel.parts or rel.suffix.lower() not in ('.hex','.mem','.bin'):
            raise ValueError('asset is not a safe relative memory-data path')
        source=manifest_path.parent/'files'/rel;data=source.read_bytes()
        blob=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
        if sha(source)!=entry['sha256'] or blob!=entry['git_blob_sha1']:
            raise ValueError('asset content does not match upstream evidence: '+str(rel))
        target=rtl/rel
        if target.exists() and sha(target)!=entry['sha256']:
            raise ValueError('upstream asset would replace different original data: '+str(rel))
        target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
        restored[str(rel)]={**entry,'repository':manifest['repository']}
    overrides=manifest['reference_overrides']
    if any(path not in restored for path in overrides.values()):raise ValueError('reference has no verified data')
    return restored,overrides,manifest
