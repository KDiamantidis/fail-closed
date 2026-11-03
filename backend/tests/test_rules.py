"""One block of tests per rule. Time is injected (ctx.now); history is in-memory SQLite."""

import pytest

from app.config import Settings
from app.models import Decision, Effect, Path, SimulationResult, TxRequest
from decode import decode
from history import CheckRecord, History
from policies.engine import RuleContext, RuleInputMissing, run_rules
from policies.request_rules import (
    REQUEST_RULES,
    c1_customer_shape,
    f1_frozen_destination,
    f2_paused_wallet,
    r2_authorizations,
    r3_request_outflow,
    r4_treasury_limit,
    r5_treasury_allowlist,
    r6_outflow_window,
    r7_multi_chain_destination,
    r8_many_assets,
)
from policies.sim_rules import TRANSFER_TOPIC, r1_delegation, r3_simulation_outflow
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
_n = 0


def settings(**kw):
    base = dict(known_tokens=[TOKEN], treasury_allowlist=[SAFE], delegate_allowlist=[],
                r4_treasury_limit_wei=10 * ETH, r6_outflow_limit_wei=50 * ETH)
    return Settings(**{**base, **kw})


STATE = StartingState.model_validate({"accounts": {
    HOT: {"balance": 1000 * ETH, "token_balances": {TOKEN: 1_000_000 * ETH}},
    TREAS: {"balance": 1000 * ETH},
}})


def req(**kw) -> TxRequest:
    base = {"wallet_type": "customer_hot", "chain_id": 1, "from": HOT, "to": CUST, "value": ETH}
    return TxRequest.model_validate({**base, **kw})


def erc20(to: str, amt: int) -> str:
    return "0xa9059cbb" + to[2:].rjust(64, "0") + format(amt, "064x")


def ctx(tx: TxRequest, history: History | None = None, now=NOW, sim=None, state=STATE, **skw):
    s = settings(**skw)
    return RuleContext(tx=tx, path=Path.SLOW if sim else Path.FAST, simulation=sim,
                       decoded=decode(tx, s.known_tokens), settings=s,
                       history=history or History(), state=state, now=now)


def record(h: History, ts, dest=ATT, chain=1, asset="native", amount=ETH, verdict="ALLOW",
           wallet=HOT, nonces=()):
    global _n
    _n += 1
    h.record_check(CheckRecord(id=f"r{_n}", ts=ts, wallet=wallet, chain_id=chain, wallet_type="customer_hot",
                               kind="x", destination=dest, asset=asset, amount=amount, path="fast",
                               path_reasons=[], verdict=verdict, findings=[], timings={}, llm=None,
                               request={}, auth_nonces=list(nonces)))


# --- F1 / F2 -----------------------------------------------------------------------

def test_f1_frozen_destination_blocks():
    h = History()
    assert f1_frozen_destination(ctx(req(to=ATT), h)) == []
    h.freeze(ATT, "R8", now=1)
    [f] = f1_frozen_destination(ctx(req(to=ATT), h))
    assert (f.rule_id, f.action, f.subject) == ("F1", Decision.BLOCK, ATT)
    assert "until a human resets it" in f.detail


def test_f1_checks_token_recipient_too():
    h = History()
    h.freeze(ATT, "R7", now=1)
    [f] = f1_frozen_destination(ctx(req(to=TOKEN, value=0, data=erc20(ATT, 5)), h))
    assert f.subject == ATT


def test_f2_paused_wallet_blocks_sender_and_authority():
    h = History()
    h.pause(HOT, "R1", now=1)
    assert f2_paused_wallet(ctx(req(), h))[0].rule_id == "F2"
    relayed = req(**{"from": ATT}, authorization_list=[{"chain_id": 1, "address": SWEEPER, "nonce": 1,
                                                        "authority": HOT}])
    assert f2_paused_wallet(ctx(relayed, h))[0].subject == HOT


# --- R2 ----------------------------------------------------------------------------

def auth(n, chain=1):
    return {"chain_id": chain, "address": SWEEPER, "nonce": n}


def test_r2_many_authorizations_in_one_request():
    [f] = r2_authorizations(ctx(req(authorization_list=[auth(n) for n in range(3, 8)])))
    assert f.action == Decision.BLOCK and "5 EIP-7702 authorizations" in f.title and "3, 4, 5, 6, 7" in f.detail


def test_r2_single_authorization_is_fine_alone():
    assert r2_authorizations(ctx(req(authorization_list=[auth(7)]))) == []


def test_r2_consecutive_to_recent_nonce_from_history():
    h = History()
    record(h, NOW - 3600, nonces=[(HOT, 1, 6)])
    [f] = r2_authorizations(ctx(req(authorization_list=[auth(7)]), h))
    assert "consecutive" in f.detail


def test_r2_history_window_and_chain():
    h = History()
    record(h, NOW - 25 * 3600, nonces=[(HOT, 1, 6)])  # outside 24 h
    record(h, NOW - 60, nonces=[(HOT, 10, 6)])  # other chain
    assert r2_authorizations(ctx(req(authorization_list=[auth(7)]), h)) == []
    record(h, NOW - 60, nonces=[(HOT, 0, 8)])  # chain 0 = any chain
    assert r2_authorizations(ctx(req(authorization_list=[auth(7)]), h))[0].rule_id == "R2"


# --- R3 (request) -------------------------------------------------------------------

def test_r3_request_native_share_of_balance():
    assert r3_request_outflow(ctx(req(value=250 * ETH))) == []  # exactly 25 %
    [f] = r3_request_outflow(ctx(req(value=251 * ETH)))
    assert f.action == Decision.BLOCK and "25.1%" in f.detail


def test_r3_request_unknown_wallet_has_no_balance():
    assert r3_request_outflow(ctx(req(**{"from": CUST}, to=ATT, value=1)))[0].rule_id == "R3"


def test_r3_request_token_uses_token_balance():
    assert r3_request_outflow(ctx(req(to=TOKEN, value=0, data=erc20(ATT, 10 * ETH)))) == []
    big = r3_request_outflow(ctx(req(to=TOKEN, value=0, data=erc20(ATT, 300_000 * ETH))))
    assert big[0].rule_id == "R3"


def test_r3_request_missing_token_balance_fails_closed():
    state = StartingState.model_validate({"accounts": {HOT: {"balance": ETH}}})
    with pytest.raises(RuleInputMissing):
        r3_request_outflow(ctx(req(to=TOKEN, value=0, data=erc20(ATT, 1)), state=state))


def test_r3_request_treasury_allowlisted_destination_skipped():
    t = req(wallet_type="treasury", **{"from": TREAS}, to=SAFE, value=900 * ETH)
    assert r3_request_outflow(ctx(t)) == []


# --- R4 / R5 ------------------------------------------------------------------------

def treas(**kw):
    return req(wallet_type="treasury", **{"from": TREAS, **kw})


def test_r4_treasury_limit():
    assert r4_treasury_limit(ctx(treas(to=SAFE, value=10 * ETH))) == []
    [f] = r4_treasury_limit(ctx(treas(to=SAFE, value=11 * ETH)))
    assert f.action == Decision.NEEDS_HUMAN
    assert r4_treasury_limit(ctx(req(value=999 * ETH))) == []  # customer_hot: not R4


def test_r4_treasury_tokens_need_human():
    [f] = r4_treasury_limit(ctx(treas(to=TOKEN, value=0, data=erc20(SAFE, 1))))
    assert f.action == Decision.NEEDS_HUMAN and "not configured" in f.detail


def test_r5_treasury_allowlist():
    assert r5_treasury_allowlist(ctx(treas(to=SAFE, value=ETH))) == []
    [small] = r5_treasury_allowlist(ctx(treas(to=ATT, value=ETH)))
    assert small.action == Decision.NEEDS_HUMAN
    [big] = r5_treasury_allowlist(ctx(treas(to=ATT, value=11 * ETH)))
    assert big.action == Decision.BLOCK and "above limit" in big.title
    assert r5_treasury_allowlist(ctx(req(to=ATT))) == []  # customer_hot: no allowlist


# --- R6 -----------------------------------------------------------------------------

def test_r6_outflow_window():
    h = History()
    record(h, NOW - 30 * 60, dest=CUST, amount=45 * ETH)
    record(h, NOW - 61 * 60, dest=CUST, amount=100 * ETH)  # outside 60 min
    record(h, NOW - 10, dest=CUST, amount=100 * ETH, verdict="BLOCK")  # never moved
    assert r6_outflow_window(ctx(req(value=5 * ETH), h)) == []  # 50 = limit, not above
    [f] = r6_outflow_window(ctx(req(value=6 * ETH), h))
    assert f.action == Decision.BLOCK and "51 ETH" in f.detail


# --- R7 / R8 ------------------------------------------------------------------------

def test_r7_delay_at_three_chains_block_and_freeze_at_five():
    h = History()
    record(h, NOW - 100, chain=1)
    record(h, NOW - 90, chain=10)
    assert r7_multi_chain_destination(ctx(req(to=ATT, chain_id=10), h)) == []
    [d] = r7_multi_chain_destination(ctx(req(to=ATT, chain_id=42161), h))
    assert d.action == Decision.DELAY and d.effect is None
    record(h, NOW - 80, chain=42161)
    record(h, NOW - 70, chain=137)
    [b] = r7_multi_chain_destination(ctx(req(to=ATT, chain_id=43114), h))
    assert b.action == Decision.BLOCK and b.effect is Effect.FREEZE_DESTINATION and b.subject == ATT


def test_r7_window_is_six_hours():
    h = History()
    record(h, NOW - 7 * 3600, chain=10)
    record(h, NOW - 7 * 3600, chain=137)
    assert r7_multi_chain_destination(ctx(req(to=ATT, chain_id=1), h)) == []


def test_r8_ten_assets_to_one_address_freezes():
    h = History()
    for i in range(9):
        record(h, NOW - 60, asset=f"0x{i:040x}")
    [f] = r8_many_assets(ctx(req(to=ATT), h))  # + native on chain 1 = 10
    assert f.action == Decision.BLOCK and f.effect is Effect.FREEZE_DESTINATION


def test_r8_window_and_distinct_count():
    h = History()
    for i in range(9):
        record(h, NOW - 31 * 60, asset=f"0x{i:040x}")  # outside 30 min
    for _ in range(5):
        record(h, NOW - 60, asset=TOKEN)  # same asset many times
    assert r8_many_assets(ctx(req(to=ATT), h)) == []


# --- C1 -----------------------------------------------------------------------------

@pytest.mark.parametrize(
    "kw,allowed",
    [
        ({"value": ETH}, True),
        ({"to": TOKEN, "value": 0, "data": erc20(ATT, 1)}, True),
        ({"to": TOKEN, "value": 0, "data": "0x095ea7b3" + "00" * 64}, False),
        ({"data": "0xdeadbeef"}, False),
        ({"authorization_list": [auth(1)]}, False),
    ],
)
def test_c1_customer_hot_shapes(kw, allowed):
    out = c1_customer_shape(ctx(req(**kw)))
    assert (out == []) is allowed
    if out:
        assert out[0].action == Decision.NEEDS_HUMAN


def test_c1_not_for_treasury():
    assert c1_customer_shape(ctx(treas(to=SAFE, data="0xdeadbeef"))) == []


# --- R1 (simulation) ----------------------------------------------------------------

def sim(diff, logs=(), probe=(), mode="type4"):
    return SimulationResult(ok=True, receipt={"status": "0x1", "logs": list(logs)}, state_diff=diff,
                            delegation_mode=mode, probe=list(probe))


DELEGATED = {"pre": {HOT: {"balance": hex(1000 * ETH), "nonce": 6}},
             "post": {HOT: {"code": "0xef0100" + SWEEPER[2:], "nonce": 8}}}


def test_r1_unknown_delegate_blocks():
    [f] = r1_delegation(ctx(req(), sim=sim(DELEGATED)))
    assert f.action == Decision.BLOCK and SWEEPER in f.detail and "0xef0100" in f.detail


def test_r1_allowlisted_delegate_and_clearing_are_fine():
    assert r1_delegation(ctx(req(), sim=sim(DELEGATED), delegate_allowlist=[SWEEPER])) == []
    cleared = {"pre": {HOT: {"code": "0xef0100" + SWEEPER[2:]}}, "post": {HOT: {"code": "0xef0100" + "00" * 20}}}
    assert r1_delegation(ctx(req(), sim=sim(cleared))) == []


# --- R3 (simulation) ----------------------------------------------------------------

def test_r3_sim_native_drain_from_diff():
    diff = {"pre": {HOT: {"balance": hex(1000 * ETH)}, ATT: {"balance": "0x0"}},
            "post": {HOT: {"balance": hex(100 * ETH)}, ATT: {"balance": hex(900 * ETH)}}}
    [f] = r3_simulation_outflow(ctx(req(), sim=sim(diff)))
    assert f.subject == ATT and "90.0%" in f.detail and "state diff" in f.detail


def test_r3_sim_unchanged_balance_is_not_a_loss():
    # post omits balance = unchanged (diffMode), must not be read as "drained to 0"
    diff = {"pre": {HOT: {"balance": hex(1000 * ETH), "nonce": 1}}, "post": {HOT: {"nonce": 2}}}
    assert r3_simulation_outflow(ctx(req(), sim=sim(diff))) == []


def test_r3_sim_token_drain_from_known_token_logs_only():
    log = {"address": TOKEN, "topics": [TRANSFER_TOPIC, "0x" + HOT[2:].rjust(64, "0"),
                                        "0x" + ATT[2:].rjust(64, "0")], "data": hex(900_000 * ETH)}
    other = {**log, "address": "0x" + "99" * 20}
    diff = {"pre": {HOT: {"nonce": 1}}, "post": {HOT: {"nonce": 2}}}
    [f] = r3_simulation_outflow(ctx(req(), sim=sim(diff, logs=[log, other])))
    assert TOKEN[:6] in f.detail and "Transfer log" in f.detail


def test_r3_sim_probe_evidence():
    diff = {"pre": {HOT: {"nonce": 6}}, "post": {HOT: {"code": "0xef0100" + SWEEPER[2:]}}}
    probe = [{"wallet": HOT, "caller": ATT, "caller_kind": "address hard-coded in delegate code",
              "call": "sweep()", "native_out": {ATT: str(1000 * ETH)}, "token_out": []}]
    [f] = r3_simulation_outflow(ctx(req(), sim=sim(diff, probe=probe)))
    assert "probe" in f.detail and "sweep()" in f.detail and "100.0%" in f.detail


def test_r3_sim_small_customer_withdrawal_is_fine():
    diff = {"pre": {HOT: {"balance": hex(1000 * ETH)}}, "post": {HOT: {"balance": hex(999 * ETH)},
                                                                 CUST: {"balance": hex(ETH)}}}
    assert r3_simulation_outflow(ctx(req(), sim=sim(diff))) == []


# --- all request rules together ----------------------------------------------------

def test_plain_small_withdrawal_has_no_findings():
    assert run_rules(REQUEST_RULES, ctx(req(value=ETH))) == []


def test_every_rule_has_readable_title_and_detail():
    h = History()
    h.freeze(ATT, "R8", now=1)
    t = req(to=ATT, value=900 * ETH, data="0xdeadbeef",
            authorization_list=[auth(n) for n in range(3, 8)])
    for f in run_rules(REQUEST_RULES, ctx(t, h)):
        assert f.title and f.detail and f.rule_id != "R0"
