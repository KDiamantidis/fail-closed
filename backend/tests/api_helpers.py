"""Build the API with injectable parts (fake or real sandbox) for tests."""

from pathlib import Path

from fastapi.testclient import TestClient

from app.api import Services, create_app
from app.config import Settings
from app.models import SimulationResult
from approval import Approver, GuardKey
from breaker import Breaker
from events import EventBus
from guard import Guard
from history import History
from llm import LLMExplainer
from scenario import discover, load, merged_policy, merged_state
from sandbox_gate import SandboxGate
from signer_mock import MockSigner
from state_provider import StaticStateProvider

SCENARIOS = Path(__file__).resolve().parents[2] / "scenarios"
ADMIN = "test-admin-token-123"
SECRET_KEY = "vultr-key-must-not-leak"


class FakeRunner:
    """Answers like the sandbox would: a plain transfer moves exactly the declared value,
    a delegation shows the 0xef0100 code and a probe that sweeps the wallet."""

    def __init__(self):
        self.calls = 0

    def run(self, tx, state):
        self.calls += 1
        acct = state.accounts.get(tx.from_)
        bal = acct.balance if acct else 0
        if tx.authorization_list:
            delegate = tx.authorization_list[0].address
            attacker = "0x3c44cdddb6a900fa2b585dd299e03d12fa4293bc"
            return SimulationResult(
                ok=True, receipt={"status": "0x1", "logs": []}, delegation_mode="type4",
                state_diff={"pre": {tx.from_: {"balance": hex(bal), "nonce": 6}},
                            "post": {tx.from_: {"code": "0xef0100" + delegate[2:], "nonce": 8}}},
                probe=[{"wallet": tx.from_, "delegate": delegate, "caller": attacker,
                        "caller_kind": "address hard-coded in delegate code", "call": "sweep()",
                        "status": "0x1", "native_out": {attacker: str(bal)}, "token_out": [], "moved": True}],
                timings={"tx": 0.01})
        return SimulationResult(
            ok=True, receipt={"status": "0x1", "logs": []},
            state_diff={"pre": {tx.from_: {"balance": hex(bal)}, tx.to: {"balance": "0x0"}},
                        "post": {tx.from_: {"balance": hex(bal - tx.value)}, tx.to: {"balance": hex(tx.value)}}},
            timings={"tx": 0.01})


def make_services(tmp_path, runner=None, admin_token=ADMIN, clock=None, gate=None) -> Services:
    scenarios = [load(p) for p in discover(SCENARIOS)]
    s = Settings(**{**Settings(llm_mode="mock").model_dump(), **merged_policy(scenarios),
                    "history_db": str(tmp_path / "h.sqlite3"), "admin_token": admin_token,
                    "vultr_inference_api_key": SECRET_KEY, "llm_mode": "mock"})
    history = History(s.history_db)
    bus = EventBus()
    from sandbox_runner import SandboxRunner

    guard = Guard(s, history, StaticStateProvider(merged_state(scenarios)),
                  runner if runner is not None else SandboxRunner(s), LLMExplainer(s),
                  breaker=Breaker(history, s), events=bus.publish, gate=gate or SandboxGate(4, 10),
                  **({"clock": clock} if clock else {}))
    key = GuardKey.load_or_create(tmp_path / "keys" / "guard.pem")
    return Services(s, guard, history, Approver(key, 120), MockSigner(key.public_key_hex), bus, SCENARIOS)


def client(tmp_path, **kw) -> tuple[TestClient, Services]:
    svc = make_services(tmp_path, **kw)
    return TestClient(create_app(svc)), svc


def known(svc: Services, request: dict) -> dict:
    """Make the request's destination a known one (an earlier payout from that wallet), so the
    escalation policy may keep it on the fast path. Returns the request."""
    svc.history.seed_payout(request["from"].lower(), request["to"].lower(), request["chain_id"], 0.0,
                            f"seed-{request['from'][-4:]}-{request['to'][-4:]}")
    return request


def scenario_request(name: str, index: int = 0) -> dict:
    return load(SCENARIOS / f"{name}.json").requests[index].request
