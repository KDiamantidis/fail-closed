"""Fail closed, end to end: every sandbox failure ends in R0 BLOCK, no approval, no signature.

Each case goes through POST /withdraw with the real SandboxRunner, faulted at the Docker
level. The request is scenario (b) (normally NEEDS_HUMAN on the slow path), so a failure
that was NOT caught would show up as a different verdict.
"""

import threading
import time

import pytest

from app.config import Settings
from llm import LLMExplainer
from sandbox_runner import SandboxRunner
from tests.api_helpers import SCENARIOS, FakeRunner, client, scenario_request
from tests.fake_docker import FakeChain, FakeDocker

pytestmark = pytest.mark.skipif(not SCENARIOS.exists(), reason="scenarios/ not available")
BODY = scenario_request("b_treasury_allowlisted_above_limit")


def runner_with(chain: FakeChain, **settings) -> SandboxRunner:
    return SandboxRunner(Settings(**{"sandbox_timeout_seconds": 5, **settings}), client=FakeDocker(chain))


def assert_r0(out: dict, why: str) -> None:
    chk = out["check"]
    assert chk["verdict"] == "BLOCK", chk["reasons"]
    r0 = [f for f in chk["findings"] if f["rule_id"] == "R0"]
    assert r0 and why in (r0[0]["detail"] + r0[0]["title"]), r0
    assert out["approval"] is None
    assert out["signer"]["signed"] is False and "no approval" in out["signer"]["reason"]


def chain(**attrs) -> FakeChain:
    c = FakeChain()
    for k, v in attrs.items():
        setattr(c, k, v)
    return c


def test_baseline_without_faults_is_needs_human(tmp_path):
    c, _ = client(tmp_path, runner=runner_with(FakeChain()))
    out = c.post("/withdraw", json=BODY).json()
    assert out["check"]["verdict"] == "NEEDS_HUMAN" and out["check"]["simulation"]["ok"] is True


@pytest.mark.parametrize(
    "fault,why",
    [
        (chain(kill_on="debug_traceTransaction"), "collect failed (exit 137)"),
        (chain(kill_on="anvil_setBalance"), "state inject failed (exit 137)"),
        (chain(diff={"pre": {}, "post": {}}), "empty state diff"),
        (chain(trace_raw="{this is not json"), "JSONDecodeError"),
        (chain(trace_raw=""), "JSONDecodeError"),
        (chain(drop_set_nonce=True), "state inject mismatch"),
    ],
    ids=["killed-mid-trace", "killed-mid-inject", "empty-trace", "broken-json", "no-output", "readback-mismatch"],
)
def test_sandbox_faults_block(tmp_path, fault, why):
    c, svc = client(tmp_path, runner=runner_with(fault))
    assert_r0(c.post("/withdraw", json=BODY).json(), why)
    assert svc.history.held() == []  # an R0 BLOCK is never held for later release


def test_missing_receipt_blocks(tmp_path, monkeypatch):
    monkeypatch.setattr("sandbox_runner.RECEIPT_TRIES", 2)
    c, _ = client(tmp_path, runner=runner_with(chain(receipt=None)))
    assert_r0(c.post("/withdraw", json=BODY).json(), "missing receipt")


def test_sandbox_timeout_blocks(tmp_path):
    t0 = time.monotonic()
    c, _ = client(tmp_path, runner=runner_with(chain(block_on="cast send"), sandbox_timeout_seconds=0.5))
    assert_r0(c.post("/withdraw", json=BODY).json(), "timeout after 0.5s")
    assert time.monotonic() - t0 < 5


def test_docker_daemon_not_reachable_blocks(tmp_path):
    import docker

    dead = docker.DockerClient(base_url="tcp://127.0.0.1:1", version="1.43", timeout=2)  # nothing listens
    c, _ = client(tmp_path, runner=SandboxRunner(Settings(), client=dead))
    out = c.post("/withdraw", json=BODY).json()
    assert_r0(out, "Sandbox failed")
    # refused (Linux) or timed out (Windows): either way the daemon is not reachable
    assert out["check"]["findings"][-1]["detail"].startswith(("ConnectionError", "ConnectTimeout"))


def explanation_event(c, check_id: str) -> dict:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        ev = [e for e in c.get(f"/events?check_id={check_id}").json() if e["type"] == "explanation"]
        if ev:
            return ev[0]["data"]
        time.sleep(0.02)
    raise AssertionError("no explanation event")


class DownLLM:
    """A Vultr client whose every call fails (outage)."""

    def __init__(self):
        from types import SimpleNamespace

        self.calls = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._fail))

    def _fail(self, **kw):
        self.calls += 1
        raise ConnectionError("vultr inference unreachable")


@pytest.mark.parametrize("name", ["a_customer_small_withdrawal", "d_binance_7702", "f_honest_large_withdrawal"])
def test_llm_down_changes_nothing_but_the_explanation(tmp_path, name):
    body = scenario_request(name)
    c_ok, _ = client(tmp_path / "ok", runner=FakeRunner())
    with_llm = c_ok.post("/withdraw", json=body).json()
    c_down, svc = client(tmp_path / "down", runner=FakeRunner())
    live = Settings(**{**svc.settings.model_dump(), "llm_mode": "live", "vultr_inference_api_key": "k" * 20})
    svc.guard.explainer = LLMExplainer(live, client=DownLLM())
    down = c_down.post("/withdraw", json=body).json()
    assert down["check"]["verdict"] == with_llm["check"]["verdict"]
    assert down["signer"]["signed"] == with_llm["signer"]["signed"]
    assert {f["rule_id"] for f in down["check"]["findings"]} == {f["rule_id"] for f in with_llm["check"]["findings"]}
    if with_llm["check"]["path"] == "fast":  # fast path: the escalation call is synchronous
        assert down["check"]["llm"] is None and with_llm["check"]["llm"] is not None
    else:  # slow path: the explanation comes later as an SSE event
        ok = explanation_event(c_ok, with_llm["check"]["check_id"])
        bad = explanation_event(c_down, down["check"]["check_id"])
        assert ok["available"] is True and bad["available"] is False
        assert "verdict is the same" in bad["reason"]
    assert svc.guard.explainer.client.calls >= 2  # tried, retried once, then gave up


@pytest.mark.docker
def test_real_container_killed_in_the_middle_of_a_check(tmp_path):
    import docker

    d = docker.from_env()
    c, _ = client(tmp_path, runner=SandboxRunner(Settings()))
    killed = []

    def killer():
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not killed:
            for ctr in d.containers.list(filters={"label": "fail-closed.sandbox", "name": "fc-sandbox-"}):
                try:
                    ctr.kill()
                    killed.append(ctr.name)
                except Exception:  # noqa: BLE001
                    pass
            time.sleep(0.02)

    t = threading.Thread(target=killer)
    t.start()
    out = c.post("/withdraw", json=BODY).json()
    t.join()
    assert killed
    assert out["check"]["verdict"] == "BLOCK" and out["approval"] is None and not out["signer"]["signed"]
    assert any(f["rule_id"] == "R0" for f in out["check"]["findings"])
    assert d.containers.list(all=True, filters={"name": killed[0]}) == []  # still cleaned up
