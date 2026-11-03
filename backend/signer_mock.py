"""Mock wallet signer: a separate component that knows ONLY the Guard's public key.

It signs only if ALL hold:
  1. there is an approval,
  2. the approval signature is valid for the Guard's public key,
  3. the approval says ALLOW,
  4. its request hash matches the request it is asked to sign, exactly,
  5. it is not expired,
  6. its approval_id was never used before (replay store).
Otherwise it refuses with a clear reason. No approval, no signature.

The "signature" is a secp256k1 ECDSA signature by an Anvil demo key (public test key)
over the request hash. It shows that the signer acted; it is not a broadcastable tx.
"""

import sqlite3
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import Prehashed

from app.models import TxRequest
from approval import Approval, request_hash, verify_approval

# Anvil test account #0. Public, known to everyone: demo only, never a real key.
ANVIL_DEMO_KEY = 0xAC0974BEC39A17E36BA4A6B4D238FF944BACB478CBED5EFCAE784D7BF4F2FF80


@dataclass(frozen=True)
class SignResult:
    signed: bool
    reason: str
    request_hash: str
    signature: str | None = None
    signer_public_key: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class ReplayStore:
    """approval_ids that were already used. SQLite so it survives restarts (file path)."""

    def __init__(self, path: str | Path = ":memory:"):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock, self.conn:
            self.conn.execute("CREATE TABLE IF NOT EXISTS used (approval_id TEXT PRIMARY KEY, ts REAL NOT NULL)")

    def claim(self, approval_id: str, now: float) -> bool:
        """Atomically mark as used. False if it was used before."""
        with self.lock, self.conn:
            cur = self.conn.execute("INSERT OR IGNORE INTO used VALUES (?, ?)", (approval_id, now))
            return cur.rowcount == 1


class MockSigner:
    def __init__(self, guard_public_key_hex: str, replay: ReplayStore | None = None, clock=time.time):
        self.guard_public_key_hex = guard_public_key_hex  # the only thing it knows about the Guard
        self.replay = replay or ReplayStore()
        self.clock = clock
        self._key = ec.derive_private_key(ANVIL_DEMO_KEY, ec.SECP256K1())
        self.public_key_hex = self._key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.CompressedPoint).hex()

    def __repr__(self) -> str:
        return f"MockSigner(guard_key={self.guard_public_key_hex[:16]}...)"

    def sign(self, tx: TxRequest, approval: Approval | None) -> SignResult:
        rh = request_hash(tx)

        def refuse(reason: str) -> SignResult:
            return SignResult(signed=False, reason=f"refused: {reason}", request_hash=rh)

        if approval is None:
            return refuse("no approval from the Guard (no approval, no signature)")
        if not verify_approval(approval, self.guard_public_key_hex):
            return refuse("approval signature is not valid for the Guard's public key")
        if approval.verdict != "ALLOW":
            return refuse(f"approval verdict is {approval.verdict}, not ALLOW")
        if approval.request_hash != rh:
            return refuse(f"request does not match the approved request "
                          f"(approved {approval.request_hash[:18]}..., asked {rh[:18]}...)")
        now = self.clock()
        if now > approval.expiry:
            return refuse(f"approval expired {int(now - approval.expiry)} s ago")
        if not self.replay.claim(approval.approval_id, now):
            return refuse(f"approval {approval.approval_id} was already used (replay)")

        sig = self._key.sign(bytes.fromhex(rh[2:]), ec.ECDSA(Prehashed(hashes.SHA256())))
        return SignResult(signed=True, reason="signed: valid Guard approval for exactly this request",
                          request_hash=rh, signature=sig.hex(), signer_public_key=self.public_key_hex)
