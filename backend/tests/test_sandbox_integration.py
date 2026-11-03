"""Real Docker runs. Skipped automatically when Docker or the sandbox image is missing.

Build the image first: sandbox/build.sh (or build.ps1 on Windows).
"""

import pytest

from app.config import Settings
from app.models import TxRequest
from policies.sim_rules import TRANSFER_TOPIC
from sandbox_runner import ANVIL_ACCOUNTS, SandboxRunner
from state_provider import StartingState
from tests.helpers import BOB, tx

pytestmark = pytest.mark.docker

HOT = ANVIL_ACCOUNTS[1]
ATTACKER = ANVIL_ACCOUNTS[2]
SWEEPER = "0x00000000000000000000000000000000005eeeee"
TOKEN = "0x000000000000000000000000000000000070c0e1"
ETH = 10**18


def test_plain_transfer_real_sandbox():
    sender = ANVIL_ACCOUNTS[0]
    state = StartingState.model_validate({"accounts": {sender: {"balance": 2 * ETH}}})
    req = TxRequest.model_validate(tx(**{"from": sender, "to": BOB, "value": ETH}))
    res = SandboxRunner(Settings()).run(req, state)
    assert res.ok and res.receipt["status"] == "0x1"
    assert int(res.state_diff["post"][BOB]["balance"], 16) == ETH
    # zero fees: the sender loses exactly the value
    assert int(res.state_diff["post"][sender]["balance"], 16) == ETH
    assert res.sandbox["NetworkMode"] == "none" and res.sandbox["ReadonlyRootfs"] is True


def test_7702_relayed_delegation_and_sweep_real_sandbox():
    state = StartingState.model_validate({"accounts": {
        HOT: {"balance": 100 * ETH, "nonce": 7},
        ATTACKER: {"balance": ETH},
        SWEEPER: {"code": "artifact:MockSweeper"},
    }})
    req = TxRequest.model_validate(tx(**{
        "from": ATTACKER, "to": HOT, "data": "0x35faa416", "value": 0,  # sweep()
        "authorization_list": [
            {"chain_id": 1, "address": SWEEPER, "nonce": n, "authority": HOT} for n in range(3, 8)
        ],
    }))
    res = SandboxRunner(Settings()).run(req, state)
    post = res.state_diff["post"][HOT]
    assert res.delegation_mode == "type4"
    assert post["code"] == "0xef0100" + SWEEPER[2:]
    assert post["nonce"] == 8  # only nonce 7 matched
    assert len(res.tx["authorizationList"]) == 5
    assert int(post["balance"], 16) == 0  # swept


def self_sponsored_request() -> tuple[TxRequest, StartingState]:
    state = StartingState.model_validate({"accounts": {
        HOT: {"balance": 100 * ETH, "nonce": 6},
        SWEEPER: {"code": "artifact:MockSweeper"},
    }})
    req = TxRequest.model_validate(tx(**{
        "from": HOT, "to": HOT, "data": "0x", "value": 0,
        "authorization_list": [{"chain_id": 1, "address": SWEEPER, "nonce": n} for n in range(3, 8)],
    }))
    return req, state


def test_type4_and_effect_modes_give_the_same_diff():
    req, state = self_sponsored_request()
    runner = SandboxRunner(Settings())
    real = runner.run(req, state)
    effect = runner.run(req, state, force_mode="effect")
    assert real.delegation_mode == "type4" and effect.delegation_mode == "effect"
    assert real.state_diff == effect.state_diff
    assert real.state_diff["post"][HOT] == {"code": "0xef0100" + SWEEPER[2:], "nonce": 8}


def test_probe_finds_the_sweeper_without_any_attacker_call_in_the_request():
    req, state = self_sponsored_request()
    res = SandboxRunner(Settings()).run(req, state)
    moved = [p for p in res.probe if p["moved"]]
    assert len(moved) == 1
    p = moved[0]
    assert p["caller"] == ATTACKER and p["call"] == "sweep()"
    assert p["caller_kind"] == "address hard-coded in delegate code"
    assert p["native_out"] == {ATTACKER: str(100 * ETH)}


def test_mock_token_transfer_emits_transfer_log():
    state = StartingState.model_validate({"accounts": {
        HOT: {"balance": ETH, "token_balances": {TOKEN: 1000 * ETH}},
        TOKEN: {"code": "artifact:MockToken"},
    }})
    data = "0xa9059cbb" + BOB[2:].rjust(64, "0") + format(7 * ETH, "064x")
    req = TxRequest.model_validate(tx(**{"from": HOT, "to": TOKEN, "data": data, "value": 0}))
    res = SandboxRunner(Settings()).run(req, state)
    assert res.receipt["status"] == "0x1"
    [log] = res.receipt["logs"]
    assert log["topics"][0] == TRANSFER_TOPIC and int(log["data"], 16) == 7 * ETH


@pytest.mark.parametrize("chain_id", [1, 10, 56, 137, 8453, 42161, 43114])
def test_every_common_chain_id_works(chain_id):
    """Regression: anvil --chain-id 10 loads the Optimism config and exits with --hardfork prague.
    The runner starts on the neutral chain id and sets the real one via RPC."""
    sender = ANVIL_ACCOUNTS[0]
    state = StartingState.model_validate({"accounts": {sender: {"balance": 2 * ETH}}})
    req = TxRequest.model_validate(tx(**{"from": sender, "to": BOB, "value": ETH, "chain_id": chain_id}))
    res = SandboxRunner(Settings()).run(req, state)
    assert res.ok and int(res.tx["chainId"], 16) == chain_id
