import pytest
from pydantic import ValidationError

from app.models import Decision, Finding, TxRequest, WalletType
from tests.helpers import BOB, HOT, SWEEPER, tx


def test_valid_tx_normalizes():
    t = TxRequest.model_validate(tx(to=BOB.upper().replace("0X", "0x"), value="0x10"))
    assert t.to == BOB
    assert t.value == 16
    assert t.from_ == HOT
    assert t.wallet_type is WalletType.CUSTOMER_HOT
    assert t.authorization_list is None


def test_authorization_list_parses():
    t = TxRequest.model_validate(
        tx(authorization_list=[{"chain_id": 0, "address": SWEEPER, "nonce": "0x5"},
                               {"chain_id": 1, "address": SWEEPER, "nonce": 6, "y_parity": 1}])
    )
    assert [a.nonce for a in t.authorization_list] == [5, 6]
    assert t.authorization_list[0].chain_id == 0


@pytest.mark.parametrize(
    "bad",
    [
        {"wallet_type": "cold"},
        {"wallet_type": None},
        {"to": "0x1234"},
        {"from": "not-an-address"},
        {"data": "0xabc"},
        {"data": "deadbeef"},
        {"value": -1},
        {"value": str(2**256)},
        {"value": True},
        {"chain_id": 0},
        {"authorization_list": [{"chain_id": 1, "address": "0x12", "nonce": 0}]},
        {"authorization_list": [{"chain_id": 1, "address": SWEEPER, "nonce": 2**64}]},
        {"authorization_list": [{"chain_id": 1, "address": SWEEPER, "nonce": 0, "y_parity": 2}]},
    ],
)
def test_invalid_tx_rejected(bad):
    with pytest.raises(ValidationError):
        TxRequest.model_validate(tx(**bad))


def test_missing_wallet_type_rejected():
    body = tx()
    del body["wallet_type"]
    with pytest.raises(ValidationError):
        TxRequest.model_validate(body)


def test_finding_is_frozen():
    f = Finding(rule_id="R1", action=Decision.BLOCK, title="t")
    with pytest.raises(ValidationError):
        f.action = Decision.ALLOW


def test_decision_order():
    assert [d.rank for d in (Decision.ALLOW, Decision.DELAY, Decision.NEEDS_HUMAN, Decision.BLOCK)] == [0, 1, 2, 3]
