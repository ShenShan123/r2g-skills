from r2g_agent import processes


def test_transient_proc_inspection_retries_before_confirming_exit(monkeypatch):
    calls = []

    def groups(_):
        calls.append(1)
        if len(calls) == 1:
            raise processes.ProcessInspectionError('process disappeared during scan')
        return []

    monkeypatch.setattr(processes, 'group_members', groups)
    monkeypatch.setattr(processes.time, 'sleep', lambda _: None)
    assert processes.stop_orphan(123, '/design', grace=0.1)
    assert len(calls) == 2


def test_persistently_unreadable_proc_never_confirms_exit(monkeypatch):
    def groups(_):
        raise processes.ProcessInspectionError('permission denied')

    ticks = iter([0.0, 0.0, 0.2])
    monkeypatch.setattr(processes, 'group_members', groups)
    monkeypatch.setattr(processes.time, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(processes.time, 'sleep', lambda _: None)
    assert not processes.stop_orphan(123, '/design', grace=0.1)
