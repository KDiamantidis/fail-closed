"""Red-team: judges by content, identical container limits, RED fails, API stream."""

import json
import threading
from types import SimpleNamespace

import pytest

from app.config import Settings
from redteam import (
    Context,
    Observed,
    RedTeam,
    judge_cpu,
    judge_fork,
    judge_memory,
    judge_network,
    judge_privilege,
    judge_secrets,
    judge_write,
    payloads,
)
from sandbox_runner import container_options
from tests.api_helpers import ADMIN, SCENARIOS, FakeRunner, client

S = Settings(admin_token="admin-secret-value-123", vultr_inference_api_key="vultr-secret-value-456")
CTX = Context(S, ["admin-secret-value-123", "vultr-secret-value-456"], 128, 512 * 2**20)


def o(output="", **kw):
    return Observed(output=output, **kw)


# --- judges: contained vs breached ---------------------------------------------------------

NET_OK = "\n".join([*(f"NET {n} BLOCKED" for n in "abcde"), "DNS vultr.com BLOCKED", "IFACES lo ", "ROUTES 0"])


def test_network():
    assert judge_network(o(NET_OK), CTX)[0]
    assert not judge_network(o(NET_OK.replace("NET a BLOCKED", "NET a OPEN")), CTX)[0]
    assert not judge_network(o(NET_OK.replace("IFACES lo", "IFACES lo eth0")), CTX)[0]
    assert not judge_network(o(NET_OK.replace("DNS vultr.com BLOCKED", "")), CTX)[0]  # missing = not proven


SEC_OK = ("== env\nPATH=/usr/bin\nHOME=/home/foundry\n== /proc/1/environ\nPATH=/usr/bin\n== find\n== end find\n"
          "SOCKET /var/run/docker.sock ABSENT\nSOCKET /run/docker.sock ABSENT\nGUARDKEY ABSENT\n")


def test_secrets():
    assert judge_secrets(o(SEC_OK), CTX)[0]
    assert not judge_secrets(o(SEC_OK.replace("HOME=", "VULTR_INFERENCE_API_KEY=x\nHOME=")), CTX)[0]
    assert not judge_secrets(o(SEC_OK + "leaked vultr-secret-value-456"), CTX)[0]
    assert not judge_secrets(o(SEC_OK.replace("== end find", "/app/.env\n== end find")), CTX)[0]
    assert not judge_secrets(o(SEC_OK.replace("/run/docker.sock ABSENT", "/run/docker.sock PRESENT")), CTX)[0]


WRITE_OK = "\n".join([*(f"WRITE {d} BLOCKED" for d in ("/", "/etc", "/usr", "/sandbox", "/home")),
                      "TMP writable", "EXEC /tmp BLOCKED"])


def test_write():
    assert judge_write(o(WRITE_OK), CTX)[0]
    assert not judge_write(o(WRITE_OK.replace("WRITE /etc BLOCKED", "WRITE /etc OPEN")), CTX)[0]
    assert not judge_write(o(WRITE_OK.replace("EXEC /tmp BLOCKED", "EXEC /tmp OPEN")), CTX)[0]


def test_fork():
    good = "PIDS_MAX 128\nPIDS_NOW 1 REFUSED max 1172\n"
    assert judge_fork(o(good, max_pids=128), CTX)[0]
    assert judge_fork(o("PIDS_MAX 128\n", max_pids=128), CTX)[0]  # stats evidence alone
    assert not judge_fork(o("PIDS_MAX 128\n", max_pids=0), CTX)[0]  # nothing observed: not proven
    assert not judge_fork(o("PIDS_MAX max\n", max_pids=500), CTX)[0]  # no limit
    assert not judge_fork(o(good, max_pids=200), CTX)[0]


def test_memory():
    good = "MEM_MAX 536870912 SWAP_MAX 0\nKilled\nPERL_EXIT 137\nMEMORY_EVENTS low 0 max 57 oom 1 oom_kill 1 \n"
    assert judge_memory(o(good), CTX)[0]
    assert not judge_memory(o(good + "ALLOCATED 2000 MiB\n"), CTX)[0]
    assert not judge_memory(o(good.replace("oom_kill 1", "oom_kill 0")), CTX)[0]
    assert not judge_memory(o(good.replace("536870912", "max")), CTX)[0]


def test_cpu():
    assert judge_cpu(o("CPU_MAX 100000 100000\n", timed_out=True), CTX)[0]
    assert not judge_cpu(o("CPU_MAX 100000 100000\n", timed_out=False), CTX)[0]
    assert not judge_cpu(o("CPU_MAX max 100000\n", timed_out=True), CTX)[0]


PRIV_OK = ("CapPrm:\t0000000000000000\nCapEff:\t0000000000000000\nCapBnd:\t0000000000000000\nNoNewPrivs:\t1\n"
           "MOUNT BLOCKED\nCHOWN BLOCKED\nSU su: Authentication failure\nSETPRIV setpriv: failed\n"
           "RAW BLOCKED (Operation not permitted)\nUID 1000\n")


def test_privilege():
    assert judge_privilege(o(PRIV_OK), CTX)[0]
    assert not judge_privilege(o(PRIV_OK.replace("CapEff:\t0000000000000000", "CapEff:\t00000000a80425fb")), CTX)[0]
    assert not judge_privilege(o(PRIV_OK.replace("NoNewPrivs:\t1", "NoNewPrivs:\t0")), CTX)[0]
    assert not judge_privilege(o(PRIV_OK.replace("SU su: Authentication failure", "SU 0")), CTX)[0]
    assert not judge_privilege(o(PRIV_OK.replace("RAW BLOCKED (Operation not permitted)", "RAW OPEN")), CTX)[0]


# --- the runner -------------------------------------------------------------------------------

class FakeContainer:
    def __init__(self, output, oom=False, hang=False):
        self.output, self.oom, self.hang = output, oom, hang
        self.attrs = {"State": {"OOMKilled": oom}}
        self.removed = threading.Event()

    def wait(self, timeout):
        if self.hang:
            raise TimeoutError("read timeout")
        return {"StatusCode": 0}

    def kill(self):
        pass

    def reload(self):
        pass

    def logs(self, **kw):
        return self.output.encode()

    def stats(self, stream, decode):
        yield {"pids_stats": {"current": 128}, "memory_stats": {"usage": 100 * 2**20}}

    def remove(self, force):
        self.removed.set()


OUTPUTS = {
    "network": NET_OK, "secrets": SEC_OK, "write": WRITE_OK,
    "fork_bomb": "PIDS_MAX 128\nPIDS_NOW 1 REFUSED max 900\n",
    "memory_bomb": "MEM_MAX 536870912 SWAP_MAX 0\nPERL_EXIT 137\nMEMORY_EVENTS oom_kill 1\n",
    "cpu_spin": "CPU_MAX 100000 100000\n", "privilege": PRIV_OK,
}


class FakeClient:
    def __init__(self, override=None):
        self.calls, self.created = [], []
        self.override = override or {}
        self.containers = SimpleNamespace(run=self.run)

    def run(self, image, entrypoint, command, name, **opts):
        self.calls.append({"image": image, "entrypoint": entrypoint, "command": command, "opts": opts})
        kind = name.split("-")[2]
        c = FakeContainer(self.override.get(kind, OUTPUTS[kind]), hang=(kind == "cpu_spin"))
        self.created.append(c)
        return c


def test_every_payload_uses_exactly_the_normal_check_limits():
    fc = FakeClient()
    RedTeam(S, client=fc).run()
    assert len(fc.calls) == len(payloads(S)) == 7
    for call in fc.calls:
        assert call["opts"] == container_options(S)  # only entrypoint/command differ
        assert call["entrypoint"] == ["sh", "-c"] and call["image"] == S.sandbox_image
    assert all(c.removed.is_set() for c in fc.created)  # always destroyed


def test_all_contained_report():
    rep = RedTeam(S, client=FakeClient(), health_probe=lambda: {"ok": True}).run()
    assert rep["all_contained"] and rep["red"] == []
    by = {r["name"]: r for r in rep["results"]}
    assert by["fork_bomb"]["health"]["answered"] == by["fork_bomb"]["health"]["calls"] >= 1
    assert by["cpu_spin"]["timed_out"] is True


def test_a_breach_is_red_and_fails():
    rep = RedTeam(S, client=FakeClient({"network": NET_OK.replace("NET a BLOCKED", "NET a OPEN")})).run()
    assert not rep["all_contained"] and rep["red"] == ["network"]


def test_host_health_failure_is_red():
    rep = RedTeam(S, client=FakeClient(), health_probe=lambda: {"ok": False}).run(only=["memory_bomb"])
    assert rep["red"] == ["memory_bomb"] and "HOST HEALTH FAILED" in rep["results"][0]["result"]


# --- API --------------------------------------------------------------------------------------

@pytest.mark.skipif(not SCENARIOS.exists(), reason="scenarios/ not available")
def test_api_redteam_stream_and_last(tmp_path):
    c, svc = client(tmp_path, runner=FakeRunner())
    svc.health_probe = lambda: {"ok": True}
    svc.make_redteam = lambda probe: RedTeam(svc.settings, client=FakeClient(), health_probe=probe)
    assert c.post("/redteam/run").status_code == 401
    assert c.get("/redteam/last").status_code == 404
    with c.stream("POST", "/redteam/run", headers={"X-Admin-Token": ADMIN}) as r:
        body = "".join(r.iter_text())
    events = [ln[6:] for ln in body.splitlines() if ln.startswith("data: ")]
    payload_events = [json.loads(e) for e in events[1:-1]]
    assert [e["name"] for e in payload_events] == [p.name for p in payloads(svc.settings)]
    assert all(e["contained"] == "yes" and "tried" in e and "seconds" in e for e in payload_events)
    assert json.loads(events[-1])["all_contained"] is True
    last = c.get("/redteam/last").json()
    assert last["all_contained"] and len(last["results"]) == 7


# --- real Docker --------------------------------------------------------------------------------

@pytest.mark.docker
def test_real_sandbox_contains_every_payload():
    s = Settings(sandbox_timeout_seconds=6)  # same limits; a shorter hard timeout keeps the test fast
    calls = []
    rep = RedTeam(s, health_probe=lambda: calls.append(1) or {"ok": True}).run()
    assert rep["all_contained"], [(r["name"], r["result"]) for r in rep["results"] if not r["contained"]]
    assert calls  # the host was probed during the bombs
