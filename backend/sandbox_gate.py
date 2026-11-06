"""Concurrency cap for sandboxes (shared by every check on this host, demo runs included).

At most SANDBOX_MAX_CONCURRENT sandboxes run at once. A check waits at most
SANDBOX_QUEUE_SECONDS for a slot; then it gets GuardBusy: the API answers 503 and nothing
is approved or signed (fail closed). Waiting longer would just pile up work on the host.

RAM guard (RAM_GUARD_MIN_FREE_GB > 0, e.g. on a laptop): while free memory is below the floor,
new sandbox work waits in the same queue; if memory does not come back within the wait, it gets
GuardBusy with a plain message. Never an approval.
"""

import threading
import time
from collections.abc import Callable
from contextlib import contextmanager


class GuardBusy(Exception):
    """No sandbox slot in time. Never an approval, never a signature."""


def low_memory_check(min_free_gb: float, free_gb: Callable[[], float | None] | None = None) -> Callable[[], bool]:
    """True while free RAM is below the floor (0 = guard off). Unknown free RAM is not 'low'."""
    if min_free_gb <= 0:
        return lambda: False
    if free_gb is None:
        from loadtest_safety import free_ram_gb as free_gb

    def low() -> bool:
        free = free_gb()
        return free is not None and free < min_free_gb
    return low


class SandboxGate:
    def __init__(self, max_concurrent: int, queue_seconds: float, low_memory: Callable[[], bool] | None = None,
                 min_free_gb: float = 0.0):
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be >= 1")
        self.max_concurrent = max_concurrent
        self.queue_seconds = queue_seconds
        self.low_memory = low_memory or (lambda: False)
        self.min_free_gb = min_free_gb
        self.held_for_memory = 0
        self._sem = threading.BoundedSemaphore(max_concurrent)
        self._lock = threading.Lock()
        self.active = 0
        self.rejected = 0

    @contextmanager
    def slot(self, wait: float | None = None):
        wait = self.queue_seconds if wait is None else wait
        deadline = time.monotonic() + wait
        if self.low_memory():  # queue until memory is back, within the same wait
            with self._lock:
                self.held_for_memory += 1
            while self.low_memory():
                if time.monotonic() >= deadline:
                    with self._lock:
                        self.rejected += 1
                    raise GuardBusy(f"free memory on this machine is below {self.min_free_gb:g} GB, so no new "
                                    f"sandbox check can start now; please try again in a minute")
                time.sleep(0.25)
            wait = max(0.0, deadline - time.monotonic())
        if not self._sem.acquire(timeout=wait):
            with self._lock:
                self.rejected += 1
            raise GuardBusy(f"all {self.max_concurrent} sandboxes busy for {wait:g}s")
        with self._lock:
            self.active += 1
        try:
            yield
        finally:
            with self._lock:
                self.active -= 1
            self._sem.release()
