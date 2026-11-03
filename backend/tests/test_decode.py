import pytest

from app.models import TxRequest
from decode import NATIVE, Kind, authority_of, decode
from tests.helpers import BOB, HOT, SWEEPER, tx

TOKEN = "0x" + "70" * 20
OTHER_TOKEN = "0x" + "71" * 20
KNOWN = [TOKEN]


def call(selector: str, addr: str, amount: int, extra: str = "") -> str:
    return selector + addr[2:].rjust(64, "0") + format(amount, "064x") + extra


def d(**kw):
    return decode(TxRequest.model_validate(tx(**kw)), KNOWN)


def test_native_transfer():
    r = d(value=5)
    assert (r.kind, r.destination, r.asset, r.amount) == (Kind.NATIVE_TRANSFER, BOB, NATIVE, 5)


def test_erc20_transfer_to_known_token():
    r = d(to=TOKEN, value=0, data=call("0xa9059cbb", BOB, 123))
    assert (r.kind, r.destination, r.asset, r.amount) == (Kind.ERC20_TRANSFER, BOB, TOKEN, 123)


def test_erc20_transfer_unknown_token_is_other_call():
    r = d(to=OTHER_TOKEN, value=0, data=call("0xa9059cbb", BOB, 1))
    assert r.kind == Kind.OTHER_CALL and "KNOWN_TOKENS" in r.note


@pytest.mark.parametrize(
    "kw,why",
    [
        ({"value": 1}, "native value on a token transfer"),
        ({"data": call("0xa9059cbb", BOB, 1, extra="00")}, "trailing byte"),
        ({"data": call("0xa9059cbb", BOB, 1)[:-2]}, "short"),
        ({"data": "0xa9059cbb" + "ff" * 12 + BOB[2:] + "00" * 31 + "01"}, "dirty address padding"),
    ],
)
def test_broken_transfer_never_guessed(kw, why):
    base = {"to": TOKEN, "value": 0, "data": call("0xa9059cbb", BOB, 1)}
    assert d(**{**base, **kw}).kind == Kind.OTHER_CALL, why


def test_approve():
    r = d(to=TOKEN, value=0, data=call("0x095ea7b3", BOB, 2**256 - 1))
    assert (r.kind, r.destination, r.asset) == (Kind.ERC20_APPROVE, BOB, TOKEN)


def test_broken_approve_is_other_call():
    assert d(to=TOKEN, value=0, data="0x095ea7b3" + "00" * 10).kind == Kind.OTHER_CALL


def test_delegation_wins_over_everything():
    r = d(authorization_list=[{"chain_id": 1, "address": SWEEPER, "nonce": 1}])
    assert r.kind == Kind.DELEGATION
    # even an empty list is a type-4 shape, never a plain transfer
    assert d(authorization_list=[]).kind == Kind.DELEGATION


@pytest.mark.parametrize("data", ["0x12", "0xdeadbeef", "0x35faa416"])
def test_unknown_calldata_is_other_call(data):
    assert d(data=data).kind == Kind.OTHER_CALL


def test_authority_defaults_to_signer():
    t = TxRequest.model_validate(tx(authorization_list=[
        {"chain_id": 1, "address": SWEEPER, "nonce": 1},
        {"chain_id": 1, "address": SWEEPER, "nonce": 2, "authority": BOB},
    ]))
    assert [authority_of(a, t) for a in t.authorization_list] == [HOT, BOB]
