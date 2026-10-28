"""Cross-process locks for generated data, outside directories being removed."""
from contextlib import contextmanager
import fcntl
import re
from .paths import r2g_home


@contextmanager
def resource_lock(kind: str, identifier: str, *, exclusive=False, blocking=True):
    if kind not in ('job', 'session') or not re.fullmatch(r'[A-Za-z0-9_-]+', identifier):
        raise ValueError('invalid resource identifier')
    directory = r2g_home() / 'lifecycle_locks'
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / f'{kind}_{identifier}.lock').open('a') as handle:
        flags = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        fcntl.flock(handle, flags | (0 if blocking else fcntl.LOCK_NB))
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
