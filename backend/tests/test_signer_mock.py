import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import Prehashed

from app.models import Decision, TxRequest, Verdict
from approval import Approval, Approver, GuardKey, approval_payload, request_hash
from signer_mock import MockSigner, ReplayStore
from tests.helpers import tx

NOW = 1_000_000.0


@pytest.fixture
def key(tmp_path):
    return GuardKey.load_or_create(tmp_path / "guard.pem")


def allow():
    return Verdict(verdict=Decision.ALLOW, reasons=[], findings=[], trace_id="t")


def setup(key, ttl=60):
    approver = Approver(key, ttl_seconds=ttl, clock=lambda: NOW)
    signer = MockSigner(key.public_key_hex, clock=lambda: NOW + 1)
    t = TxRequest.model_validate(tx())
    return approver, signer, t, approver.issue(t, allow())


def test_signs_with_a_valid_approval_for_exactly_this_request(key):
    _, signer, t, ap = setup(key)
    r = signer.sign(t, ap)
    assert r.signed and r.reason.startswith("signed")
    # the mock signature really is a secp256k1 signature over the request hash
    pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256K1(), bytes.fromhex(r.signer_public_key))
    pub.verify(bytes.fromhex(r.signature), bytes.fromhex(request_hash(t)[2:]), ec.ECDSA(Prehashed(hashes.SHA256())))


def test_missing_approval_refused(key):
    _, signer, t, _ = setup(key)
    r = signer.sign(t, None)
    assert not r.signed and "no approval" in r.reason


def test_approval_from_another_key_refused(key, tmp_path):
    other = GuardKey.load_or_create(tmp_path / "attacker.pem")
    ap = Approver(other, 60, clock=lambda: NOW).issue(TxRequest.model_validate(tx()), allow())
    _, signer, t, _ = setup(key)
    r = signer.sign(t, ap)
    assert not r.signed and "not valid for the Guard's public key" in r.reason


def test_tampered_request_refused(key):
    _, signer, t, ap = setup(key)
    changed = TxRequest.model_validate(tx(to="0x" + "ba" * 20))  # "to" changed after approval
    r = signer.sign(changed, ap)
    assert not r.signed and "does not match the approved request" in r.reason
    assert signer.sign(TxRequest.model_validate(tx(value=10**18 + 1)), ap).signed is False


def test_tampered_approval_fields_refused(key):
    _, signer, t, ap = setup(key)
    for change in ({"expiry": ap.expiry + 3600}, {"request_hash": request_hash(t)[:-1] + "0"}):
        assert not signer.sign(t, ap.model_copy(update=change)).signed


def test_expired_refused(key):
    approver, _, t, ap = setup(key, ttl=60)
    late = MockSigner(key.public_key_hex, clock=lambda: NOW + 61)
    r = late.sign(t, ap)
    assert not r.signed and "expired" in r.reason


def test_reused_approval_refused(key):
    _, signer, t, ap = setup(key)
    assert signer.sign(t, ap).signed
    r = signer.sign(t, ap)
    assert not r.signed and "already used (replay)" in r.reason


def test_replay_store_survives_restart(key, tmp_path):
    _, _, t, ap = setup(key)
    db = tmp_path / "signer.sqlite3"
    assert MockSigner(key.public_key_hex, ReplayStore(db), clock=lambda: NOW).sign(t, ap).signed
    assert not MockSigner(key.public_key_hex, ReplayStore(db), clock=lambda: NOW).sign(t, ap).signed


def test_non_allow_approval_refused_even_if_validly_signed(key):
    # the Guard never issues this, but the signer must not trust that
    t = TxRequest.model_validate(tx())
    rh = request_hash(t)
    sig = key.sign(approval_payload("x1", rh, "BLOCK", int(NOW) + 60, key.key_id))
    ap = Approval(approval_id="x1", request_hash=rh, verdict="BLOCK", expiry=int(NOW) + 60,
                  key_id=key.key_id, signature=sig)
    r = MockSigner(key.public_key_hex, clock=lambda: NOW).sign(t, ap)
    assert not r.signed and "not ALLOW" in r.reason


def test_refusal_does_not_burn_the_approval(key):
    _, signer, t, ap = setup(key)
    assert not signer.sign(TxRequest.model_validate(tx(value=1)), ap).signed
    assert signer.sign(t, ap).signed


def test_signer_knows_only_the_public_key(key):
    signer = MockSigner(key.public_key_hex)
    assert not any(isinstance(v, GuardKey) for v in vars(signer).values())
    assert "PRIVATE" not in repr(signer)
