import pytest

from fundus.core.locking import AlreadyRunning, run_lock


def test_lock_excludes_second_holder(tmp_path):
    lock = tmp_path / "f.lock"
    with run_lock(lock), pytest.raises(AlreadyRunning), run_lock(lock):
        pass
    with run_lock(lock):  # released -> acquirable again
        pass
