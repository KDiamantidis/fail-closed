"""In-process event bus for the live stream (SSE).

The Guard runs in worker threads; subscribers are asyncio queues on the server loop.
publish() is thread-safe and never blocks or raises into the Guard. A ring buffer keeps
recent events so a client can catch up (and tests can read them without streaming).
"""

import asyncio
import itertools
import threading
import time
from collections import deque
from typing import Any


class EventBus:
    def __init__(self, keep: int = 5000, queue_size: int = 1000):  # keep: enough for polling clients
        self._subs: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
        self._lock = threading.Lock()
        self._seq = itertools.count(1)
        self.recent: deque[dict[str, Any]] = deque(maxlen=keep)
        self.queue_size = queue_size

    def publish(self, check_id: str, kind: str, data: dict[str, Any], run_id: str = "main") -> dict[str, Any]:
        with self._lock:
            event = {"seq": next(self._seq), "ts": time.time(), "type": kind, "run_id": run_id,
                     "check_id": check_id, "data": data}
            self.recent.append(event)
            subs = list(self._subs)
        for loop, queue in subs:
            try:
                loop.call_soon_threadsafe(self._offer, queue, event)
            except RuntimeError:  # loop closed: subscriber is gone
                pass
        return event

    @staticmethod
    def _offer(queue: asyncio.Queue, event: dict) -> None:
        if queue.full():  # a slow client loses old events, never blocks the Guard
            queue.get_nowait()
        queue.put_nowait(event)

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=self.queue_size)
        with self._lock:
            self._subs.append((asyncio.get_running_loop(), queue))
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._subs = [(lp, q) for lp, q in self._subs if q is not queue]

    def since(self, seq: int, check_id: str | None = None, run_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            return [e for e in self.recent if e["seq"] > seq
                    and (check_id is None or e["check_id"] == check_id)
                    and (run_id is None or e["run_id"] == run_id)]
