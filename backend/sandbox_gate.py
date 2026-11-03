"""Concurrency cap for sandboxes (shared by every check on this host, demo runs included).

At most SANDBOX_MAX_CONCURRENT sandboxes run at once. A check waits at most
SANDBOX_QUEUE_SECONDS for a slot; then it gets GuardBusy: the API answers 503 and nothing
is approved or signed (fail closed). Waiting longer would just pile up work on the host.
"""

import threading
from contextlib import contextmanager


class GuardBusy(Exception):
    """No sandbox slot in time. Never an approval, never a signature."""


class SandboxGate:
    def __init__(self, max_concurrent: int, queue_seconds: float):
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be >= 1")
        self.max_concurrent = max_concurrent
        self.queue_seconds = queue_seconds
        self._sem = threading.BoundedSemaphore(max_concurrent)
        self._lock = threading.Lock()
        self.active = 0
        self.rejected = 0

    @contextmanager
    def slot(self, wait: float | None = None):
        wait = self.queue_seconds if wait is None else wait
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
