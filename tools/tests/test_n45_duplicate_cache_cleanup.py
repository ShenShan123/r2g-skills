import importlib.util
import json
from pathlib import Path


spec = importlib.util.spec_from_file_location('cache_cleanup', '/home/yangao/cleanup_n45_completed_cache_20260918.py')
cleanup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cleanup)


def test_only_identical_completed_cache_files_removed(tmp_path, monkeypatch):
    root = tmp_path/'campaign'
    cache_root = tmp_path/'flow/results/nangate45'
    audit = root/'audit'
    audit.mkdir(parents=True)
    monkeypatch.setattr(cleanup, 'ROOT', root)
    monkeypatch.setattr(cleanup, 'CACHE', cache_root)
    monkeypatch.setattr(cleanup, 'AUDIT', audit)
    recovery = root/'drc_recovery'
    recovery.mkdir()
    cleanup.save(recovery/'status.json', {'stage': 'stopped_user_no_additional_retries'})
    for task, status in (('finished', 'baseline_clean_single_run'), ('timeout', 'inconclusive_timeout')):
        work = root/'jobs'/task
        results = work/'project/backend/RUN_test/results'
        results.mkdir(parents=True)
        cache = cache_root/'top'/('variant_'+task)
        cache.mkdir(parents=True)
        cleanup.save(work/'complete.json', {'status': status})
        cleanup.save(results.parent/'run-meta.json', {'make_status': 0, 'orfs_results': str(cache)})
        for folder in (results, cache):
            (folder/'equal.odb').write_bytes(b'identical')
        (results/'different.odb').write_bytes(b'original')
        (cache/'different.odb').write_bytes(b'modified')
        (cache/'unique.lvsdb').write_bytes(b'unique evidence')
        (cache/'symlink.odb').symlink_to(results/'equal.odb')
    assert len(cleanup.candidates()) == 1
    cleanup.clean(1000000)
    finished = cache_root/'top/variant_finished'
    assert not (finished/'equal.odb').exists()
    assert (finished/'different.odb').read_bytes() == b'modified'
    assert (finished/'unique.lvsdb').exists()
    assert (finished/'symlink.odb').is_symlink()
    assert (cache_root/'top/variant_timeout/equal.odb').exists()
    assert (root/'jobs/finished/project/backend/RUN_test/results/equal.odb').read_bytes() == b'identical'
    summary = json.loads((audit/'summary.json').read_text())
    assert summary['removed_duplicate_files'] == 1
