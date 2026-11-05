"""Load test safety: refuse with little free RAM; never leave sandbox containers behind."""

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import cli
import loadtest_safety as safety
from app.config import Settings
from sandbox_runner import container_options

BACKEND = Path(__file__).resolve().parents[1]


class FakeContainer:
    def __init__(self, name, owner=None):
        self.name = name
        self.labels = {"fail-closed.sandbox": "1", **({safety.OWNER_LABEL: owner} if owner else {})}
        self.removed = False

    def remove(self, force=False):
        assert force
        self.removed = True


class FakeClient:
    def __init__(self, containers):
        self._all = containers
        self.containers = SimpleNamespace(list=self.list)

    def list(self, all=False, filters=None):  # noqa: A002 - docker SDK signature
        assert all and filters == {"label": safety.OWNER_LABEL}
        return [c for c in self._all if safety.OWNER_LABEL in c.labels and not c.removed]


def dead_pid() -> int:
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def test_removes_only_this_runs_containers():
    me, other_alive, stale = safety.owner_for(), safety.owner_for(os.getppid()), safety.owner_for(dead_pid())
    cs = [FakeContainer("mine-1", me), FakeContainer("mine-2", me), FakeContainer("other", other_alive),
          FakeContainer("stale", stale), FakeContainer("api-sandbox")]
    client = FakeClient(cs)
    assert safety.remove_containers(client, owner=me) == 2
    assert [c.name for c in cs if c.removed] == ["mine-1", "mine-2"]
    # next start: only containers of load tests whose process is gone
    assert safety.remove_containers(client, stale=True) == 1
    assert [c.name for c in cs if c.removed] == ["mine-1", "mine-2", "stale"]


def test_cleanup_runs_once_and_reports():
    me = safety.owner_for()
    cs = [FakeContainer("a", me)]
    c = safety.Cleanup(me, lambda: FakeClient(cs), watch_parent=False, gap=0)
    assert c.run("signal SIGINT") == 1 and c.run("exit") == 0 and cs[0].removed


def test_pid_alive():
    assert safety.pid_alive(os.getpid()) and not safety.pid_alive(dead_pid())


def test_free_ram_is_read():
    free = safety.free_ram_gb()
    assert free is not None and free > 0


def test_containers_carry_the_owner_label():
    assert container_options(Settings(sandbox_owner="loadtest-42"))["labels"] == {
        "fail-closed.sandbox": "1", "fail-closed.owner": "loadtest-42"}
    assert container_options(Settings())["labels"] == {"fail-closed.sandbox": "1"}


@pytest.mark.parametrize("free", [1.2, None])
def test_refuses_to_start_with_little_free_ram(monkeypatch, capsys, free):
    monkeypatch.setattr(safety, "free_ram_gb", lambda: free)
    assert cli.main(["loadtest", "--normal", "10"]) == 2
    out = capsys.readouterr().out
    assert "refusing to start: free RAM" in out and "need at least 2 GB" in out
    assert ("1.2 GB" if free else "unknown") in out


def _owned(owner: str) -> list:
    import docker

    return docker.from_env().containers.list(all=True, filters={"label": f"{safety.OWNER_LABEL}={owner}"})


@pytest.mark.docker
def test_interrupt_removes_the_runs_containers(tmp_path):
    """A real load test (warm pool, mock LLM) interrupted mid-run leaves no container behind."""
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    env = {**os.environ, "LLM_MODE": "mock", "VULTR_INFERENCE_API_KEY": "", "PYTHONUNBUFFERED": "1"}
    p = subprocess.Popen([sys.executable, "cli.py", "loadtest", "--normal", "200", "--configs", "warm/escalation-on",
                          "--concurrency", "2", "--latency-ms", "800", "--new-dest-share", "0.5",
                          "--min-free-gb", "0", "--out", str(tmp_path)],
                         cwd=BACKEND, env=env, creationflags=flags,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    # the owner label names the load test's own pid (on Windows a venv python.exe is a launcher
    # that starts the real interpreter as a child, so p.pid is not it): read it from the output
    lines: list[str] = []
    reader = threading.Thread(target=lambda: lines.extend(iter(p.stdout.readline, "")), daemon=True)
    reader.start()
    owner = ""
    try:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline and p.poll() is None:
            owner = next((ln.split("fail-closed.owner=")[1].split(";")[0].strip()
                          for ln in list(lines) if "fail-closed.owner=" in ln), "")
            if owner and _owned(owner):
                break
            time.sleep(0.5)
        assert owner and _owned(owner), "the load test never started a container: " + "".join(lines)[-2000:]
        time.sleep(2)  # mid-run
        p.send_signal(signal.CTRL_BREAK_EVENT if sys.platform == "win32" else signal.SIGINT)
        p.wait(timeout=60)
        reader.join(10)
        out = "".join(lines)
        assert p.returncode != 0 and "load test stopped (signal" in out, out[-2000:]
        assert _owned(owner) == []
    finally:
        if p.poll() is None:
            p.kill()
        for c in _owned(owner) if owner else []:
            c.remove(force=True)
