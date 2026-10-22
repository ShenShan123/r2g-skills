"""A unit test simulates one process; backend locking still holds inside it."""
import pytest
import factory


def test_backend_lock_remains_enforced_within_a_test():
    factory.reset()
    factory.open_memory_backend('none')
    with pytest.raises(factory.BackendLockError):
        factory.open_memory_backend('legacy')


def test_next_test_does_not_inherit_another_simulated_process_backend():
    assert factory.opened_backend() is None
    assert factory.opened_signature() is None
