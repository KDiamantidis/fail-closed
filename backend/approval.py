"""The Guard's own approval key and the canonical request hash.

- ed25519 key, generated on first start, stored OUTSIDE the repo (GUARD_KEY_PATH),
  owner-only file. Never in the sandbox, never logged. The public key goes to the signer.
- Canonical request: every request field, sorted keys, lowercase hex, integers as decimal
  strings, no whitespace. Any change to any field changes the hash.
- Approval = signature over {request_hash, verdict, expiry, approval_id, key_id}.
  Only ALLOW is approved here (DELAY release and human approval: Phase 3).
"""

import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from pydantic import BaseModel, ConfigDict

from app.config import REPO_ROOT
from app.models import Authorization, Decision, TxRequest, Verdict

REQUEST_VERSION = "fail-closed/request/v1"
APPROVAL_VERSION = "fail-closed/approval/v1"

# Every field of the request models must be in the canonical form. If someone adds a
# field and forgets it here, importing this module fails (fail closed), so an approval
# can never silently ignore part of a request.
_TX_FIELDS = {"wallet_type", "chain_id", "from_", "to", "data", "value", "authorization_list"}
_AUTH_FIELDS = {"chain_id", "address", "nonce", "authority", "y_parity", "r", "s"}
if set(TxRequest.model_fields) != _TX_FIELDS or set(Authorization.model_fields) != _AUTH_FIELDS:
    raise RuntimeError("canonical request encoding does not cover every request field")


def _canon_bytes(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _hex_int(v: str | None) -> str | None:
    """Signature words as decimal strings (0x01 and 0x1 are the same value)."""
    if v is None:
        return None
    try:
        return str(int(v, 16))
    except ValueError:
        return v.lower()  # not hex: keep it, the hash still covers it


def canonical_request(tx: TxRequest) -> dict[str, Any]:
    auths = None
    if tx.authorization_list is not None:
        auths = [{
            "chain_id": str(a.chain_id),
            "address": a.address.lower(),
            "nonce": str(a.nonce),
            "authority": a.authority.lower() if a.authority else None,
            "y_parity": None if a.y_parity is None else str(a.y_parity),
            "r": _hex_int(a.r),
            "s": _hex_int(a.s),
        } for a in tx.authorization_list]
    return {
        "v": REQUEST_VERSION,
        "wallet_type": tx.wallet_type.value,
        "chain_id": str(tx.chain_id),
        "from": tx.from_.lower(),
        "to": tx.to.lower(),
        "value": str(tx.value),
        "data": tx.data.lower(),
        "authorization_list": auths,
    }


def canonical_bytes(tx: TxRequest) -> bytes:
    return _canon_bytes(canonical_request(tx))


def request_hash(tx: TxRequest) -> str:
    return "0x" + hashlib.sha256(canonical_bytes(tx)).hexdigest()


class Approval(BaseModel):
    model_config = ConfigDict(frozen=True)

    approval_id: str
    request_hash: str
    verdict: str
    expiry: int  # unix seconds
    key_id: str
    signature: str  # hex, ed25519 over approval_payload()


def approval_payload(approval_id: str, req_hash: str, verdict: str, expiry: int, key_id: str) -> bytes:
    return _canon_bytes({"v": APPROVAL_VERSION, "approval_id": approval_id, "request_hash": req_hash,
                         "verdict": verdict, "expiry": str(expiry), "key_id": key_id})


def key_id_of(public_key_hex: str) -> str:
    return hashlib.sha256(bytes.fromhex(public_key_hex)).hexdigest()[:16]


def verify_approval(approval: Approval, public_key_hex: str) -> bool:
    """True only if the signature over the approval fields is valid for this public key."""
    try:
        pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        if approval.key_id != key_id_of(public_key_hex):
            return False
        pub.verify(bytes.fromhex(approval.signature), approval_payload(
            approval.approval_id, approval.request_hash, approval.verdict, approval.expiry, approval.key_id))
        return True
    except (InvalidSignature, ValueError):
        return False


class GuardKey:
    """The Guard's ed25519 approval key. repr/str never show the private key."""

    def __init__(self, private_key: Ed25519PrivateKey):
        self._private = private_key
        raw = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.public_key_hex = raw.hex()
        self.key_id = key_id_of(self.public_key_hex)

    def __repr__(self) -> str:
        return f"GuardKey(key_id={self.key_id})"

    __str__ = __repr__

    def sign(self, payload: bytes) -> str:
        return self._private.sign(payload).hex()

    @classmethod
    def load_or_create(cls, path: str | Path) -> "GuardKey":
        p = Path(path).expanduser().resolve()
        if p == REPO_ROOT or REPO_ROOT in p.parents:
            raise PermissionError(f"refusing to keep the Guard key inside the repository: {p}")
        if p.exists():
            if os.name == "posix" and p.stat().st_mode & 0o077:
                raise PermissionError(f"Guard key file {p} is readable by others (needs mode 600)")
            key = serialization.load_pem_private_key(p.read_bytes(), password=None)
            if not isinstance(key, Ed25519PrivateKey):
                raise ValueError(f"{p} is not an ed25519 private key")
            return cls(key)
        p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        key = Ed25519PrivateKey.generate()
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)  # never overwrite a key
        with os.fdopen(fd, "wb") as f:
            f.write(pem)
        return cls(key)


class Approver:
    def __init__(self, key: GuardKey, ttl_seconds: int, clock=time.time):
        self.key = key
        self.ttl = ttl_seconds
        self.clock = clock

    @property
    def public_key_hex(self) -> str:
        return self.key.public_key_hex

    def issue(self, tx: TxRequest, verdict: Verdict) -> Approval | None:
        """An approval only for ALLOW. Everything else gets none (no approval, no signature)."""
        if verdict.verdict is not Decision.ALLOW:
            return None
        approval_id = uuid.uuid4().hex
        rh = request_hash(tx)
        expiry = int(self.clock()) + self.ttl
        sig = self.key.sign(approval_payload(approval_id, rh, Decision.ALLOW.value, expiry, self.key.key_id))
        return Approval(approval_id=approval_id, request_hash=rh, verdict=Decision.ALLOW.value,
                        expiry=expiry, key_id=self.key.key_id, signature=sig)
