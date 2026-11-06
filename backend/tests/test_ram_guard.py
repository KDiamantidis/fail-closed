"""RAM guard (laptop safety): below RAM_GUARD_MIN_FREE_GB free RAM, no new sandbox starts; new
slow-path work waits in the gate's queue, then gets "Guard busy" (503, BLOCK recorded, nothing
signed). Never an approval."""

import threading
import time
from types import SimpleNamespace

import pytest

from sandbox_gate import GuardBusy, SandboxGate, low_memory_check
from sandbox_runner import WarmPool
from tests.api_helpers import ADMIN, FakeRunner, client, scenario_request


def test_guard_is_off_by_default_and_unknown_ram_is_not_low():
    assert low_memory_check(0)() is False
    assert low_memory_check(1.0, lambda: None)() is False  # cannot read RAM: do not stop everything
    assert low_memory_check(1.0, lambda: 0.4)() is True and low_memory_check(1.0, lambda: 2.0)() is False


def test_low_memory_queues_then_refuses_with_a_plain_message():
    gate = SandboxGate(4, 0.4, low_memory=lambda: True, min_free_gb=0.75)
    t0 = time.monotonic()
    with pytest.raises(GuardBusy, match="free memory on this machine is below 0.75 GB"):
        with gate.slot():
            pass
    assert time.monotonic() - t0 >= 0.35 and gate.rejected == 1 and gate.held_for_memory == 1


def test_work_goes_on_when_memory_comes_back_within_the_wait():
    low = [True]
    threading.Timer(0.3, lambda: low.__setitem__(0, False)).start()
    gate = SandboxGate(4, 2.0, low_memory=lambda: low[0], min_free_gb=0.75)
    with gate.slot():
        ran = True
    assert ran and gate.rejected == 0 and gate.held_for_memory == 1


def test_low_memory_never_approves_a_slow_path_request(tmp_path):
    gate = SandboxGate(4, 0.3, low_memory=lambda: True, min_free_gb=0.75)
    c, svc = client(tmp_path, runner=FakeRunner(), gate=gate)
    r = c.post("/withdraw", json=scenario_request("c_treasury_unknown_above_limit"), headers={"X-Admin-Token": ADMIN})
    assert r.status_code == 503 and "free memory on this machine" in r.json()["detail"]
    assert "Nothing was approved or signed" in r.json()["detail"]
    [rec] = svc.history.recent(5)
    assert rec["verdict"] == "BLOCK"


def test_the_warm_pool_starts_no_container_while_memory_is_low():
    started = []
    runner = SimpleNamespace(low_memory=lambda: True, _start_container=lambda *a: started.append(a))
    pool = WarmPool(runner, size=2, max_uses=50, max_age=600)
    pool.prefill()
    assert started == [] and pool.idle == 0
