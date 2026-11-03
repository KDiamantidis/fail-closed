"""Guard routing and end-to-end decision with a fake sandbox and a fake LLM."""

import pytest

from app.config import Settings
from app.models import Decision, LLMAssessment, Path, SimulationResult, TxRequest
from guard import Guard
from history import History
from sandbox_runner import SandboxError
from state_provider import StartingState

ETH = 10**18
HOT = "0x" + "a1" * 20
TREAS = "0x" + "7e" * 20
CUST = "0x" + "c1" * 20
ATT = "0x" + "ba" * 20
SAFE = "0x" + "5a" * 20
TOKEN = "0x" + "70" * 20
SWEEPER = "0x" + "5e" * 20
NOW = 1_000_000.0

STATE = StartingState.model_validate({"accounts": {
    HOT: {"balance": 1000 * ETH, "token_balances": {TOKEN: 10**30}},
    TREAS: {"balance": 1000 * ETH},
}})


class StaticState:
    def __init__(self, state=STATE, error=None):
        self.state, self.error = state, error

    def get_state(self, tx):
        if self.error:
            raise self.error
        return self.state


class FakeRunner:
    def __init__(self, sim=None, error=None):
        self.calls = 0
        self.sim = sim or SimulationResult(ok=True, receipt={"status": "0x1", "logs": []},
                                           state_diff={"pre": {HOT: {"balance": hex(1000 * ETH)}},
                                                       "post": {HOT: {"balance": hex(999 * ETH)}}},
                                           timings={"tx": 0.1})
        self.error = error

    def run(self, tx, state):
        self.calls += 1
        if self.error:
            raise self.error
        return self.sim


class FakeExplainer:
    def __init__(self, escalate=False, fail=False):
        self.escalate, self.fail, self.calls = escalate, fail, 0

    def assess(self, tx, sim):
        self.calls += 1
        if self.fail:
            return None
        return LLMAssessment(intent_summary="s", risk_score=50, explanation="e", escalate=self.escalate)


def guard(runner=None, explainer=None, state=None, **settings_kw):
    s = Settings(known_tokens=[TOKEN], treasury_allowlist=[SAFE], r4_treasury_limit_wei=10 * ETH,
                 **settings_kw)
    return Guard(s, History(), state or StaticState(), runner or FakeRunner(), explainer or FakeExplainer(),
                 clock=lambda: NOW)


def req(**kw):
    base = {"wallet_type": "customer_hot", "chain_id": 1, "from": HOT, "to": CUST, "value": ETH}
    return TxRequest.model_validate({**base, **kw})


def erc20(to, amt):
    return "0xa9059cbb" + to[2:].rjust(64, "0") + format(amt, "064x")


# --- fast path ---------------------------------------------------------------------

def test_small_native_withdrawal_is_fast_allow():
    g = guard()
    r = g.check(req())
    assert (r.verdict.verdict, r.verdict.path) == (Decision.ALLOW, Path.FAST)
    assert g.runner.calls == 0 and g.explainer.calls == 1
    assert "known-safe shape (native_transfer)" in r.verdict.path_reasons[0]
    assert r.verdict.path_reasons[-1] == "LLM: no escalation"


def test_known_token_transfer_is_fast():
    r = guard().check(req(to=TOKEN, value=0, data=erc20(CUST, ETH)))
    assert r.verdict.path is Path.FAST and "to a known token" in r.verdict.path_reasons[0]


def test_llm_escalation_sends_to_slow_path_but_never_sets_verdict():
    g = guard(explainer=FakeExplainer(escalate=True))
    r = g.check(req())
    assert r.verdict.path is Path.SLOW and g.runner.calls == 1
    assert "LLM asked for a deeper check" in r.verdict.path_reasons
    assert r.verdict.verdict is Decision.ALLOW  # the sandbox found nothing, so still ALLOW


def test_llm_failure_on_fast_path_goes_slow():
    g = guard(explainer=FakeExplainer(fail=True))
    r = g.check(req())
    assert r.verdict.path is Path.SLOW and g.runner.calls == 1
    assert any("LLM error or timeout" in x for x in r.verdict.path_reasons)


def test_escalation_switch_off_skips_llm_on_fast_path():
    g = guard(fast_path_llm_escalation=False)
    r = g.check(req())
    assert r.verdict.path is Path.FAST and g.explainer.calls == 0
    assert "LLM escalation off" in r.verdict.path_reasons


# --- slow path ---------------------------------------------------------------------

def test_non_transfer_shape_goes_slow_with_reason():
    g = guard()
    r = g.check(req(to=TOKEN, value=0, data="0x095ea7b3" + "00" * 64))
    assert r.verdict.path is Path.SLOW
    assert r.verdict.verdict is Decision.NEEDS_HUMAN  # C1
    assert any("erc20_approve" in x for x in r.verdict.path_reasons)


def test_delegation_goes_slow_and_runs_sim_rules():
    sim = SimulationResult(ok=True, receipt={"status": "0x1", "logs": []},
                           state_diff={"pre": {HOT: {"balance": hex(1000 * ETH), "nonce": 7}},
                                       "post": {HOT: {"code": "0xef0100" + SWEEPER[2:], "nonce": 8}}})
    g = guard(runner=FakeRunner(sim=sim))
    r = g.check(req(to=HOT, value=0, authorization_list=[{"chain_id": 1, "address": SWEEPER, "nonce": 7}]))
    assert r.verdict.path is Path.SLOW and r.verdict.verdict is Decision.BLOCK
    assert {"R1", "C1"} <= {f.rule_id for f in r.verdict.findings}


def test_sandbox_error_is_r0_block():
    g = guard(runner=FakeRunner(error=SandboxError("timeout after 30s")))
    r = g.check(req(to=TOKEN, value=0, data="0xdeadbeef"))
    assert r.verdict.verdict is Decision.BLOCK
    assert any("timeout after 30s" in x for x in r.verdict.reasons)


def test_missing_state_fails_closed():
    g = guard(state=StaticState(error=RuntimeError("rpc down")))
    r = g.check(req())
    assert r.verdict.verdict is Decision.BLOCK
    assert any("R0" in x for x in r.verdict.reasons)


# --- treasury -------------------------------------------------------------------------

def treas(**kw):
    return req(wallet_type="treasury", **{"from": TREAS, **kw})


def test_treasury_allowlisted_small_is_fast():
    assert guard().check(treas(to=SAFE, value=ETH)).verdict.path is Path.FAST


def test_treasury_allowlisted_above_limit_needs_human():
    r = guard().check(treas(to=SAFE, value=20 * ETH))
    assert (r.verdict.verdict, r.verdict.path) == (Decision.NEEDS_HUMAN, Path.SLOW)
    assert [f.rule_id for f in r.verdict.findings] == ["R4"]


def test_treasury_unknown_destination_above_limit_is_early_block():
    g = guard()
    r = g.check(treas(to=ATT, value=20 * ETH))
    assert (r.verdict.verdict, r.verdict.path) == (Decision.BLOCK, Path.EARLY)
    assert g.runner.calls == 0
    assert "R5" in {f.rule_id for f in r.verdict.findings}


# --- history, delay, errors -------------------------------------------------------------

def test_every_check_is_recorded_with_path_and_reasons():
    g = guard()
    g.check(req())
    [c] = g.history.checks()
    assert c["path"] == "fast" and c["verdict"] == "ALLOW" and c["path_reasons"]
    assert c["llm"]["risk_score"] == 50 and "total" in c["timings"]


def test_delay_sets_release_time():
    g = guard()
    for i, chain in enumerate((10, 137)):
        g.clock = lambda i=i: NOW + i
        g.check(req(to=ATT, chain_id=chain))
    g.clock = lambda: NOW + 10
    r = g.check(req(to=ATT, chain_id=42161))
    assert r.verdict.verdict is Decision.DELAY
    assert r.verdict.release_at == NOW + 10 + 15 * 60
    assert g.history.checks()[-1]["release_at"] == r.verdict.release_at


def test_unexpected_error_blocks_and_is_recorded():
    class Boom(FakeRunner):
        def run(self, tx, state):
            raise ZeroDivisionError("x")

    g = guard(runner=Boom())
    r = g.check(req(to=TOKEN, value=0, data="0xdeadbeef"))
    assert r.verdict.verdict is Decision.BLOCK and "guard error" in r.verdict.reasons[0]
    assert g.history.checks()[0]["verdict"] == "BLOCK"


def test_authorization_nonces_are_recorded_for_r2():
    g = guard(runner=FakeRunner())
    g.check(req(to=HOT, value=0, authorization_list=[{"chain_id": 1, "address": SWEEPER, "nonce": 6}]))
    r = g.check(req(to=HOT, value=0, authorization_list=[{"chain_id": 1, "address": SWEEPER, "nonce": 7}]))
    assert any(f.rule_id == "R2" and "consecutive" in f.detail for f in r.verdict.findings)


# --- breaker effects reach the next request -------------------------------------------

def guarded_with_breaker(**kw):
    from breaker import Breaker

    g = guard(**kw)
    g.breaker = Breaker(g.history)
    return g


def test_r1_block_pauses_wallet_and_next_request_hits_f2():
    sim = SimulationResult(ok=True, receipt={"status": "0x1", "logs": []},
                           state_diff={"pre": {HOT: {"balance": hex(1000 * ETH), "nonce": 7}},
                                       "post": {HOT: {"code": "0xef0100" + SWEEPER[2:], "nonce": 8}}})
    g = guarded_with_breaker(runner=FakeRunner(sim=sim))
    r = g.check(req(to=HOT, value=0, authorization_list=[{"chain_id": 1, "address": SWEEPER, "nonce": 7}]))
    assert r.breaker_actions == [f"level 2: paused wallet {HOT} (R1)"]
    nxt = g.check(req())  # an ordinary withdrawal from the paused wallet
    assert (nxt.verdict.verdict, nxt.verdict.path) == (Decision.BLOCK, Path.EARLY)
    assert [f.rule_id for f in nxt.verdict.findings] == ["F2"]


def test_r7_freeze_blocks_that_destination_only():
    g = guarded_with_breaker()
    for i, chain in enumerate((1, 10, 42161, 137)):
        g.clock = lambda i=i: NOW + i
        g.check(req(to=ATT, chain_id=chain))
    g.clock = lambda: NOW + 10
    r = g.check(req(to=ATT, chain_id=43114))
    assert r.verdict.verdict is Decision.BLOCK and r.breaker_actions == [f"level 1: froze destination {ATT} (R7)"]
    assert g.check(req(to=ATT, chain_id=1)).verdict.findings[0].rule_id == "F1"
    assert g.check(req(to=CUST, chain_id=1)).verdict.verdict is Decision.ALLOW  # others keep going


@pytest.mark.parametrize("escalation", [True, False])
def test_llm_never_changes_the_verdict(escalation):
    verdicts = set()
    for explainer in (FakeExplainer(), FakeExplainer(fail=True)):
        g = guard(explainer=explainer, fast_path_llm_escalation=escalation)
        verdicts.add(g.check(treas(to=SAFE, value=20 * ETH)).verdict.verdict)
    assert verdicts == {Decision.NEEDS_HUMAN}
