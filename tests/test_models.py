import pytest
from pydantic import ValidationError

from app.config import Settings
from app.models import Finding, Severity, TxRequest
from tests.helpers import ALICE, tx


def test_valid_tx_normalizes():
    t = TxRequest.model_validate(tx(to=ALICE.upper().replace("0X", "0x"), value="0x10"))
    assert t.to == ALICE
    assert t.value == 16
    assert t.from_ == ALICE


@pytest.mark.parametrize(
    "bad",
    [
        {"to": "0x1234"},
        {"from": "not-an-address"},
        {"data": "0xabc"},  # odd length
        {"data": "deadbeef"},  # no 0x
        {"value": -1},
        {"value": str(2**256)},
        {"value": True},
        {"chain_id": 0},
    ],
)
def test_invalid_tx_rejected(bad):
    with pytest.raises(ValidationError):
        TxRequest.model_validate(tx(**bad))


def test_finding_is_frozen():
    f = Finding(rule_id="x", severity=Severity.HIGH, blocking=True, title="t")
    with pytest.raises(ValidationError):
        f.blocking = False


def test_threshold_accepts_hex_and_decimal():
    assert Settings(approval_large_threshold="0x100").approval_large_threshold == 256
    assert Settings(approval_large_threshold="1000").approval_large_threshold == 1000
    with pytest.raises(ValidationError):
        Settings(approval_large_threshold="0")
