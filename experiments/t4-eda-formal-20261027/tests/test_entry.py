import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'adapters'))


def test_entry_preserves_unknown_and_terminal_on_interruption(tmp_path,monkeypatch):
    import formal_runtime as runtime
    import formal_queue
    monkeypatch.setattr(runtime,'ROOT',tmp_path)
    monkeypatch.setattr(runtime,'flash_key',lambda:'OFFLINE')
    monkeypatch.setattr(formal_queue,'validate',lambda:None)
    (tmp_path/'inputs').mkdir()
    task={'task':'fixture','source_group':'fixture'}
    (tmp_path/'inputs/rtlrepair-private.json').write_text(json.dumps([task]))
    qual=tmp_path/'qualification/fixture';qual.mkdir(parents=True)
    (qual/'qualification.json').write_text('{"status":"PASS"}')
    def no_reply(task,memory,provider,episode):
        def fail(*a,**k):raise OSError('offline drop')
        provider.request({'messages':[]},transport=fail)
    monkeypatch.setattr(runtime,'run_fixer',no_reply)
    previous=Path.cwd()
    import tempfile
    old_tmp=tempfile.tempdir
    try:
        assert runtime.run('rtlfixer','native','fixture')==2
        result=json.loads((tmp_path/'hosts/rtlfixer/native/episodes/fixture/episode.json').read_text())
        assert result['status']=='ACCOUNTING_FAILURE'
        assert result['cost']['online_requests']==1
        assert result['cost']['unknown_requests']==1
        assert result['cost']['actual_total_tokens'] is None
        assert (tmp_path/'hosts/rtlfixer/native/episodes/fixture/terminal-accounting.json').exists()
    finally:
        import os
        os.chdir(previous);tempfile.tempdir=old_tmp
