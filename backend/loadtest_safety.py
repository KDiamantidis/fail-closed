"""Load test safety: free-RAM check, and no sandbox containers left behind.

- Every container a load test starts carries the label fail-closed.owner=loadtest-<pid>.
- Ctrl+C, SIGTERM or Ctrl+Break (Windows): remove this run's containers, then exit.
- The process that started us is gone (its terminal or task was killed): the same.
- A hard kill (SIGKILL, TerminateProcess) cannot be caught: the next load test removes the
  containers of any earlier load test whose process is no longer alive, before it starts.
The load test runs threads only, no child processes; containers are what can outlive it.
"""

import atexit
import os
import signal
import sys
import threading
import time
from typing import Any

OWNER_LABEL = "fail-closed.owner"
PREFIX = "loadtest-"


def owner_for(pid: int | None = None) -> str:
    return f"{PREFIX}{os.getpid() if pid is None else pid}"


def free_ram_gb() -> float | None:
    """Free (available) physical memory in GB, or None if it cannot be read."""
    try:
        if sys.platform == "win32":
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            m = MemoryStatus()
            m.dwLength = ctypes.sizeof(MemoryStatus)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
                return None
            return m.ullAvailPhys / 2**30
        with open("/proc/meminfo", encoding="ascii") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024 / 2**30
    except Exception:  # noqa: BLE001 - unknown is reported as unknown, not as plenty
        return None
    return None


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(k.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
        finally:
            k.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def remove_containers(client: Any, owner: str | None = None, stale: bool = False) -> int:
    """Remove load-test containers: this owner's, or (stale=True) those of dead load tests."""
    removed = 0
    for c in client.containers.list(all=True, filters={"label": OWNER_LABEL}):
        o = (getattr(c, "labels", None) or {}).get(OWNER_LABEL, "")
        if owner is not None:
            match = o == owner
        elif stale:
            pid = o[len(PREFIX):] if o.startswith(PREFIX) else ""
            match = pid.isdigit() and not pid_alive(int(pid))
        else:
            match = False
        if match:
            try:
                c.remove(force=True)
                removed += 1
            except Exception as e:  # noqa: BLE001
                if type(e).__name__ != "NotFound":
                    print(f"could not remove container {getattr(c, 'name', '?')}: {type(e).__name__}",
                          file=sys.stderr)
    return removed


class Cleanup:
    """Install once per load-test process: signals, exit, and a watch on the parent process."""

    def __init__(self, owner: str, client_factory, watch_parent: bool = True, poll: float = 1.0,
                 passes: int = 30, gap: float = 1.0, before=None, quiet_passes: int = 3):
        self.owner, self.client_factory, self.poll = owner, client_factory, poll
        self.before, self.quiet_passes = before, quiet_passes  # before(): stop new containers first
        self.passes, self.gap = passes, gap
        self._done = threading.Lock()
        self._ran = False
        self.parent = os.getppid() if watch_parent else None

    def run(self, why: str = "exit") -> int:
        with self._done:
            if self._ran:
                return 0
            self._ran = True
        try:
            if self.before is not None:
                self.before()  # no new sandbox containers from now on
            client = self.client_factory()
            # repeat: a container whose creation was already in flight may still appear
            n, quiet = 0, 0
            for _ in range(self.passes):
                got = remove_containers(client, owner=self.owner)
                n, quiet = n + got, (0 if got else quiet + 1)
                if quiet >= self.quiet_passes:
                    break
                time.sleep(self.gap)
        except Exception as e:  # noqa: BLE001
            print(f"load test cleanup failed ({type(e).__name__}); remove containers labelled "
                  f"{OWNER_LABEL}={self.owner} by hand", file=sys.stderr)
            return 0
        if n or why != "exit":
            print(f"load test stopped ({why}): removed {n} sandbox container(s)", file=sys.stderr, flush=True)
        return n

    def _on_signal(self, signum, frame) -> None:  # noqa: ARG002
        self.run(f"signal {signal.Signals(signum).name}")
        os._exit(128 + int(signum))

    def _watch(self) -> None:
        while True:
            time.sleep(self.poll)
            if self.parent and (os.getppid() != self.parent if sys.platform != "win32"
                                else not pid_alive(self.parent)):
                self.run("parent process ended")
                os._exit(1)

    def install(self) -> "Cleanup":
        for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            sig = getattr(signal, name, None)
            if sig is not None:
                try:
                    signal.signal(sig, self._on_signal)
                except (ValueError, OSError):  # not the main thread / not supported
                    pass
        atexit.register(self.run)
        if self.parent:
            threading.Thread(target=self._watch, name="loadtest-parent-watch", daemon=True).start()
        return self
