"""DELAY release worker: re-check after release_at; release only if nothing new."""

import time

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from app.api import create_app
from app.models import Decision, TxRequest
from approval import Approver, GuardKey
from holds import HoldManager
from signer_mock import MockSigner
from tests.api_helpers import SCENARIOS, FakeRunner, client, make_services, scenario_request
from tests.test_scenarios import neutral_guard

pytestmark = pytest.mark.skipif(not SCENARIOS.exists(), reason="scenarios/ not available")
NOW = 1_000_000.0
DELAY = 15 * 60


def holds_for(guard, when):
    key = GuardKey(Ed25519PrivateKey.generate())
    return HoldManager(guard, Approver(key, 120, clock=lambda: when), MockSigner(key.public_key_hex, clock=lambda: when))


def test_mercado_delays_are_not_released_because_the_destination_froze():
    sc, g = neutral_guard("e_mercado_multichain")
    delayed = []
    for r in sc.requests:
        g.clock = lambda r=r: NOW + r.t
        res = g.check(r.tx())
        if res.verdict.verdict is Decision.DELAY:
            delayed.append(res.check_id)
    assert len(delayed) == 2
    last = NOW + sc.requests[-1].t
    assert holds_for(g, last).release_due(now=NOW + sc.requests[8].t + 60) == []  # not due yet
    out = holds_for(g, last + DELAY).release_due(now=last + DELAY)
    assert [d["id"] for d in out] == delayed
    for d in out:
        assert d["status"] == "blocked" and d["verdict"] == "BLOCK" and d["signed"] is False
        assert "[F1] Destination is frozen" in d["reason"]
    assert {g.history.get_held(i)["status"] for i in delayed} == {"blocked"}


def test_delay_without_a_new_signal_is_released_and_signed():
    _, g = neutral_guard("a_customer_small_withdrawal")
    att = "0xbad0000000000000000000000000000000000a99"
    body = {**scenario_request("a_customer_small_withdrawal"), "to": att}
    ids = []
    for i, chain in enumerate((1, 10, 42161)):
        g.clock = lambda i=i: NOW + i
        ids.append(g.check(TxRequest.model_validate({**body, "chain_id": chain})))
    assert ids[-1].verdict.verdict is Decision.DELAY
    later = NOW + 2 + DELAY + 1
    [d] = holds_for(g, later).release_due(now=later)
    assert d["status"] == "released" and d["signed"] is True and d["by"] == "delay-worker"
    assert "re-check found nothing new" in d["reason"]
    # decided once: a second pass finds nothing to release
    assert holds_for(g, later).release_due(now=later + 60) == []


def test_api_demo_run_shows_the_delay_worker_result(tmp_path):
    c, _ = client(tmp_path, runner=FakeRunner())
    out = c.post("/scenarios/e_mercado_multichain/run").json()
    w = out["delay_worker"]["results"]
    assert len(w) == 2 and all(d["status"] == "blocked" and not d["signed"] for d in w)


def test_background_worker_releases_due_holds(tmp_path):
    svc = make_services(tmp_path, runner=FakeRunner())
    svc.settings.delay_check_seconds = 0.05
    svc.history.hold("h1", "delay", 1.0, scenario_request("a_customer_small_withdrawal"), ["R7"], ["test"],
                     release_at=1.0)
    with TestClient(create_app(svc)):  # the lifespan starts the worker
        deadline = time.monotonic() + 5
        while svc.history.get_held("h1")["status"] == "pending" and time.monotonic() < deadline:
            time.sleep(0.05)
    item = svc.history.get_held("h1")
    assert item["status"] == "released" and item["signed"] is True and item["decided_by"] == "delay-worker"
