import pytest

from app.models import TxRequest
from policies.rules import check_large_approval
from tests.helpers import BOB, approve_calldata, tx

THRESHOLD = 2**128


def run(data: str, threshold: int = THRESHOLD):
    return check_large_approval(TxRequest.model_validate(tx(data=data)), threshold)


@pytest.mark.parametrize("amount", [2**256 - 1, 2**255, 2**128])
def test_large_approval_blocks(amount):
    [f] = run(approve_calldata(BOB, amount))
    assert f.rule_id == "approval.unlimited"
    assert f.blocking is True
    assert BOB in f.detail


@pytest.mark.parametrize("amount", [0, 1, 2**28, 10**18, 2**128 - 1])
def test_normal_approval_not_flagged(amount):
    # 2**28 (~268M base units) is an ordinary amount and must not be flagged.
    assert run(approve_calldata(BOB, amount)) == []


def test_increase_allowance_also_checked():
    [f] = run(approve_calldata(BOB, 2**256 - 1, selector="0x39509351"))
    assert "increaseAllowance" in f.title


def test_custom_threshold():
    assert run(approve_calldata(BOB, 10**30), threshold=10**30)[0].blocking


def test_other_calls_ignored():
    transfer = "0xa9059cbb" + BOB[2:].rjust(64, "0") + format(2**256 - 1, "064x")
    assert run(transfer) == []
    assert run("0x") == []


def test_malformed_approve_blocks():
    [f] = run("0x095ea7b3" + "00" * 10)
    assert f.rule_id == "approval.malformed"
    assert f.blocking is True
