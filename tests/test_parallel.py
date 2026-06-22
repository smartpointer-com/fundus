import threading
import time

from fundus.core.parallel import parallel_map


def test_parallel_map_returns_all_results():
    assert sorted(parallel_map(range(20), lambda x: x * 2, workers=4)) == [x * 2 for x in range(20)]


def test_parallel_map_sequential_when_one_worker():
    assert list(parallel_map([1, 2, 3], lambda x: x + 1, workers=1)) == [2, 3, 4]


def test_parallel_map_actually_runs_concurrently():
    current = 0
    peak = 0
    lock = threading.Lock()

    def fn(x):
        nonlocal current, peak
        with lock:
            current += 1
            peak = max(peak, current)
        time.sleep(0.02)
        with lock:
            current -= 1
        return x

    list(parallel_map(range(8), fn, workers=4))
    assert peak >= 2  # work overlapped across threads
