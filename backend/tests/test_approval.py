import os
import stat

import pytest

from app.config import REPO_ROOT
from app.models import Decision, TxRequest, Verdict
from approval import (
    Approver,
    GuardKey,
    approval_payload,
    canonical_bytes,
    canonical_request,
    request_hash,
    verify_approval,
)
from tests.helpers import BOB, HOT, SWEEPER, tx

BASE = tx(authorization_list=[{"chain_id": 1, "address": SWEEPER, "nonce": 7, "authority": HOT,
                               "y_parity": 1, "r": "0x01", "s": "0x02"}])


def h(body: dict) -> str:
    return request_hash(TxRequest.model_validate(body))


def allow():
    return Verdict(verdict=Decision.ALLOW, reasons=[], findings=[], trace_id="t")


@pytest.fixture
def key(tmp_path):
    return GuardKey.load_or_create(tmp_path / "keys" / "guard.pem")


# --- canonical request hash -----------------------------------------------------------

def test_hash_is_deterministic_and_compact():
    a, b = TxRequest.model_validate(BASE), TxRequest.model_validate(dict(reversed(list(BASE.items()))))
    assert request_hash(a) == request_hash(b)
    raw = canonical_bytes(a)
    assert b" " not in raw and b"\n" not in raw
    assert raw.index(b'"authorization_list"') < raw.index(b'"chain_id"')  # sorted keys


def test_equivalent_spellings_hash_the_same():
    same = {**BASE, "to": BOB.upper().replace("0X", "0x"), "value": hex(BASE["value"]),
            "from": HOT.upper().replace("0X", "0x"), "data": "0x"}
    same["authorization_list"] = [{**BASE["authorization_list"][0], "r": "0x1", "s": "0x0002",
                                   "address": SWEEPER.upper().replace("0X", "0x")}]
    assert h(same) == h(BASE)


def test_integers_are_decimal_strings():
    c = canonical_request(TxRequest.model_validate(BASE))
    assert c["value"] == str(BASE["value"]) and c["chain_id"] == "1"
    assert c["authorization_list"][0]["nonce"] == "7"


def _auth(**kw):
    return {**BASE, "authorization_list": [{**BASE["authorization_list"][0], **kw}]}


@pytest.mark.parametrize(
    "changed",
    [
        {**BASE, "wallet_type": "treasury"},
        {**BASE, "chain_id": 10},
        {**BASE, "from": "0x" + "99" * 20},
        {**BASE, "to": "0x" + "98" * 20},
        {**BASE, "value": BASE["value"] + 1},
        {**BASE, "data": "0x00"},
        {**BASE, "authorization_list": None},
        {**BASE, "authorization_list": []},
        {**BASE, "authorization_list": BASE["authorization_list"] * 2},
        _auth(chain_id=0),
        _auth(address="0x" + "97" * 20),
        _auth(nonce=8),
        _auth(authority="0x" + "96" * 20),
        _auth(authority=None),
        _auth(y_parity=0),
        _auth(r="0x03"),
        _auth(s="0x04"),
    ],
)
def test_changing_any_field_changes_the_hash(changed):
    assert h(changed) != h(BASE)


def test_every_model_field_is_covered():
    c = canonical_request(TxRequest.model_validate(BASE))
    assert set(c) == {"v", "wallet_type", "chain_id", "from", "to", "value", "data", "authorization_list"}
    assert set(c["authorization_list"][0]) == {"chain_id", "address", "nonce", "authority", "y_parity", "r", "s"}


# --- the key ---------------------------------------------------------------------------

def test_key_is_created_once_and_reused(tmp_path):
    p = tmp_path / "k" / "guard.pem"
    k1 = GuardKey.load_or_create(p)
    k2 = GuardKey.load_or_create(p)
    assert k1.public_key_hex == k2.public_key_hex and len(k1.public_key_hex) == 64


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_key_file_is_owner_only(tmp_path):
    p = tmp_path / "guard.pem"
    GuardKey.load_or_create(p)
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    p.chmod(0o644)
    with pytest.raises(PermissionError, match="readable by others"):
        GuardKey.load_or_create(p)


def test_key_never_inside_the_repo():
    with pytest.raises(PermissionError, match="inside the repository"):
        GuardKey.load_or_create(REPO_ROOT / "data" / "guard.pem")


def test_key_repr_shows_no_secret(key):
    assert "PRIVATE" not in repr(key) and key.key_id in repr(key)
    assert not hasattr(key, "private_key")


# --- approvals ---------------------------------------------------------------------------

def test_only_allow_gets_an_approval(key):
    a = Approver(key, ttl_seconds=60, clock=lambda: 1000)
    t = TxRequest.model_validate(BASE)
    for d in (Decision.DELAY, Decision.NEEDS_HUMAN, Decision.BLOCK):
        assert a.issue(t, Verdict(verdict=d, reasons=["x"], findings=[], trace_id="t")) is None
    ap = a.issue(t, allow())
    assert ap.verdict == "ALLOW" and ap.expiry == 1060 and ap.request_hash == request_hash(t)
    assert verify_approval(ap, key.public_key_hex)


def test_approval_signature_covers_every_field(key, tmp_path):
    ap = Approver(key, 60).issue(TxRequest.model_validate(BASE), allow())
    for change in ({"request_hash": "0x" + "00" * 32}, {"verdict": "BLOCK"}, {"expiry": ap.expiry + 1},
                   {"approval_id": "other"}, {"signature": "00" * 64}):
        assert not verify_approval(ap.model_copy(update=change), key.public_key_hex)
    other = GuardKey.load_or_create(tmp_path / "other.pem")
    assert not verify_approval(ap, other.public_key_hex)


def test_approval_payload_is_canonical():
    p = approval_payload("id", "0xab", "ALLOW", 5, "kid")
    assert p == b'{"approval_id":"id","expiry":"5","key_id":"kid","request_hash":"0xab","v":"fail-closed/approval/v1","verdict":"ALLOW"}'
