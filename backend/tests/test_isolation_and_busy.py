"""Demo isolation (one visitor never changes another's result) and the sandbox concurrency cap."""

import threading
import time

import pytest

from sandbox_gate import GuardBusy, SandboxGate
from tests.api_helpers import SCENARIOS, FakeRunner, client, known, scenario_request

pytestmark = pytest.mark.skipif(not SCENARIOS.exists(), reason="scenarios/ not available")
HOT_D = "0x70997970c51812dc3a010c7d01b50e0d17dc79c8"


def summary(run):
    return [(r["check"]["verdict"], r["check"]["path"], r["signer"]["signed"]) for r in run["results"]]


def test_each_scenario_run_has_its_own_state(tmp_path):
    c, svc = client(tmp_path, runner=FakeRunner())
    first = c.post("/scenarios/d_delegation_drain/run").json()
    second = c.post("/scenarios/d_delegation_drain/run").json()
    # the second visitor sees the same result, not "wallet already paused"
    assert summary(first) == summary(second) == [("BLOCK", "slow", False)]
    assert first["run_id"] != second["run_id"]
    assert [w["wallet"] for w in first["breaker"]["paused_wallets"]] == [HOT_D]
    # the main state is untouched by demo runs
    assert c.get("/breaker").json()["paused_wallets"] == []
    assert c.get("/history").json() == []
    assert c.post("/withdraw", json={**scenario_request("d_delegation_drain"), "authorization_list": None,
                                     "to": "0xc000000000000000000000000000000000000d01", "value": "1"}
                  ).json()["check"]["path"] != "early"


def test_multichain_drain_twice_gives_identical_results(tmp_path):
    c, _ = client(tmp_path, runner=FakeRunner())
    a = c.post("/scenarios/e_multichain_drain/run").json()
    b = c.post("/scenarios/e_multichain_drain/run").json()
    assert summary(a) == summary(b)
    assert summary(a)[20] == ("ALLOW", "slow", True)  # another customer, new destination: simulated


def test_events_carry_the_run_id(tmp_path):
    c, _ = client(tmp_path, runner=FakeRunner())
    run = c.post("/scenarios/a_customer_small_withdrawal/run").json()
    mine = c.get(f"/events?run_id={run['run_id']}").json()
    assert mine and {e["run_id"] for e in mine} == {run["run_id"]}
    assert [e["type"] for e in mine][0] == "run_started" and mine[-1]["type"] == "run_finished"
    c.post("/withdraw", json=scenario_request("a_customer_small_withdrawal"))
    assert {e["run_id"] for e in c.get("/events?run_id=main").json()} == {"main"}


# --- concurrency cap -------------------------------------------------------------------

class SlowRunner(FakeRunner):
    def __init__(self, seconds):
        super().__init__()
        self.seconds = seconds

    def run(self, tx, state):
        time.sleep(self.seconds)
        return super().run(tx, state)


def test_gate_limits_and_rejects_after_the_queue_time():
    gate = SandboxGate(max_concurrent=2, queue_seconds=0.1)
    with gate.slot(), gate.slot():
        assert gate.active == 2
        t0 = time.monotonic()
        with pytest.raises(GuardBusy, match="all 2 sandboxes busy"):
            with gate.slot():
                pass
        assert 0.1 <= time.monotonic() - t0 < 1
    assert gate.active == 0 and gate.rejected == 1
    with gate.slot():  # slots come back
        pass


def test_api_answers_503_when_full_and_nothing_is_signed(tmp_path):
    c, svc = client(tmp_path, runner=SlowRunner(1.0), gate=SandboxGate(1, 0.2))
    slow_body = scenario_request("b_treasury_allowlisted_above_limit")  # slow path
    t = threading.Thread(target=lambda: c.post("/withdraw", json=slow_body))
    t.start()
    while svc.guard.gate.active == 0:
        time.sleep(0.01)
    r = c.post("/withdraw", json=scenario_request("c_treasury_unknown_above_limit"))
    assert r.status_code == 503 and "Guard busy" in r.json()["detail"]
    assert "Nothing was approved or signed" in r.json()["detail"]
    t.join()
    busy = [h for h in c.get("/history").json() if any("guard busy" in str(f) for f in h["findings"])]
    assert busy and busy[0]["verdict"] == "BLOCK"
    assert c.post("/scenarios/d_delegation_drain/run").status_code == 200  # free again


def test_fast_path_needs_no_slot(tmp_path):
    c, svc = client(tmp_path, runner=SlowRunner(1.0), gate=SandboxGate(1, 0.1))
    t = threading.Thread(target=lambda: c.post("/withdraw", json=scenario_request("b_treasury_allowlisted_above_limit")))
    t.start()
    while svc.guard.gate.active == 0:
        time.sleep(0.01)
    r = c.post("/withdraw", json=known(svc, scenario_request("a_customer_small_withdrawal")))
    assert r.status_code == 200 and r.json()["signer"]["signed"] is True
    t.join()
