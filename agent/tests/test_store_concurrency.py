from concurrent.futures import ThreadPoolExecutor
import sqlite3

from r2g_agent.jobs import JobStore
from r2g_agent.agent_loop import SessionStore


def test_concurrent_job_events_and_model_ledger_preserve_all_rows(tmp_path):
    jobs = JobStore(tmp_path / 'jobs.sqlite')
    sessions = SessionStore(tmp_path / 'sessions.sqlite')
    def worker(index):
        sid = sessions.create()
        jid = jobs.create_job(str(index), {'session_id': sid}, [{'name': 'dut'}])
        for step in range(12):
            jobs.set_design(jid, 'dut', stage='flow', status='running', detail={str(step): step})
            jobs.event(jid, 'dut', 'flow', 'info', str(step))
            sessions.record_usage(sid, f'{index}-{step}', {'total_tokens': step})
            assert sessions.exists(sid) and not sessions.is_deleting(sid)
            assert jobs.job(jid)['designs'][0]['detail'][str(step)] == step
        assert len(sessions.usage(sid)['calls']) == 12
        assert len(jobs.events(jid)) == 13
        return jid
    with ThreadPoolExecutor(max_workers=12) as pool:
        ids = list(pool.map(worker, range(12)))
    assert len(set(ids)) == 12
    # External report readers coexist with the application's persistent WAL.
    with sqlite3.connect(f'file:{jobs.path}?mode=ro', uri=True) as reader:
        assert reader.execute('SELECT COUNT(*) FROM events').fetchone()[0] == 12 * 13
        jobs.event(ids[0], 'dut', 'flow', 'info', 'reader remains connected')
