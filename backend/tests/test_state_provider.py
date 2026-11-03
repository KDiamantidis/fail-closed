import json

import pytest
from pydantic import ValidationError

from app.models import TxRequest
from state_provider import AccountState, ScenarioStateProvider, StartingState, request_accounts
from tests.helpers import BOB, HOT, SWEEPER, tx

OTHER = "0x" + "0f" * 20


def write_state(tmp_path, accounts):
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"accounts": accounts}), encoding="utf-8")
    return p


def test_returns_only_request_accounts(tmp_path):
    p = write_state(tmp_path, {
        HOT.upper().replace("0X", "0x"): {"balance": "0x56bc75e2d63100000", "nonce": 7},
        OTHER: {"balance": 1},
    })
    state = ScenarioStateProvider(p).get_state(TxRequest.model_validate(tx()))
    assert set(state.accounts) == {HOT}
    assert state.accounts[HOT].balance == 100 * 10**18
    assert state.accounts[HOT].nonce == 7


def test_includes_delegates_and_authorities(tmp_path):
    p = write_state(tmp_path, {SWEEPER: {"code": "artifact:MockSweeper"}, HOT: {"balance": 5}})
    req = TxRequest.model_validate(tx(
        **{"from": BOB, "to": HOT},
        authorization_list=[{"chain_id": 1, "address": SWEEPER, "nonce": 7, "authority": HOT}],
    ))
    assert request_accounts(req) == {BOB, HOT, SWEEPER}
    state = ScenarioStateProvider(p).get_state(req)
    assert state.accounts[SWEEPER].code == "artifact:MockSweeper"
    assert state.accounts[HOT].balance == 5


def test_missing_accounts_start_empty(tmp_path):
    p = write_state(tmp_path, {})
    assert ScenarioStateProvider(p).get_state(TxRequest.model_validate(tx())).accounts == {}


def test_state_never_comes_from_request(tmp_path):
    # Extra fields in the request (e.g. a fake "balance") are not a state source.
    p = write_state(tmp_path, {HOT: {"balance": 1}})
    req = TxRequest.model_validate({**tx(), "balance": 10**30, "state": {HOT: {"balance": 10**30}}})
    assert ScenarioStateProvider(p).get_state(req).accounts[HOT].balance == 1


@pytest.mark.parametrize(
    "acct",
    [
        {"balance": -1},
        {"nonce": 2**64},
        {"code": "0x123"},
        {"code": "artifact:../../etc/passwd"},
        {"storage": {"0x01": "0x" + "00" * 33}},
        {"storage": {"slot": "0x01"}},
        {"unknown": 1},
    ],
)
def test_invalid_account_state_rejected(acct):
    with pytest.raises(ValidationError):
        AccountState.model_validate(acct)


def test_invalid_address_key_rejected():
    with pytest.raises(ValidationError):
        StartingState.model_validate({"accounts": {"0x12": {}}})
