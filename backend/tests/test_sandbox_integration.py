"""Real Docker runs. Skipped automatically when Docker or the sandbox image is missing.

Build the image first: sandbox/build.sh (or build.ps1 on Windows).
"""

import pytest

from app.config import Settings
from app.models import TxRequest
from sandbox_runner import ANVIL_ACCOUNTS, SandboxRunner
from state_provider import StartingState
from tests.helpers import BOB, tx

pytestmark = pytest.mark.docker

HOT = ANVIL_ACCOUNTS[1]
ATTACKER = ANVIL_ACCOUNTS[2]
SWEEPER = "0x00000000000000000000000000000000005eeeee"
ETH = 10**18


def test_plain_transfer_real_sandbox():
    sender = ANVIL_ACCOUNTS[0]
    state = StartingState.model_validate({"accounts": {sender: {"balance": 2 * ETH}}})
    req = TxRequest.model_validate(tx(**{"from": sender, "to": BOB, "value": ETH}))
    res = SandboxRunner(Settings()).run(req, state)
    assert res.ok and res.receipt["status"] == "0x1"
    assert int(res.state_diff["post"][BOB]["balance"], 16) == ETH
    assert res.sandbox["NetworkMode"] == "none" and res.sandbox["ReadonlyRootfs"] is True


def test_7702_delegation_and_sweep_real_sandbox():
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
    assert post["code"] == "0xef0100" + SWEEPER[2:]
    assert post["nonce"] == 8  # only nonce 7 matched
    assert len(res.tx["authorizationList"]) == 5
    assert int(post.get("balance", "0x0"), 16) == 0  # swept
