"""Request / finding / verdict data models."""

import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import parse_uint

_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
_HEX_BYTES = re.compile(r"^0x([0-9a-fA-F]{2})*$")


def _check_address(v: str) -> str:
    if not _ADDRESS.match(v):
        raise ValueError("must be a 0x-prefixed 20-byte hex address")
    return v.lower()


class WalletType(StrEnum):
    CUSTOMER_HOT = "customer_hot"  # pays customers to unknown addresses; behavior rules
    TREASURY = "treasury"  # sends to a few known addresses; strict allowlist


class Authorization(BaseModel):
    """One EIP-7702 authorization tuple. Signature fields are optional."""

    chain_id: int = Field(ge=0)  # 0 = valid on every chain
    address: str  # the delegate (code target)
    nonce: int
    # The account asked to sign this authorization (e.g. the hot wallet). Needed to
    # simulate an unsigned authorization; with a signature it is recovered instead.
    authority: str | None = None
    y_parity: int | None = Field(default=None, ge=0, le=1)
    r: str | None = None
    s: str | None = None

    @field_validator("address")
    @classmethod
    def _address(cls, v: str) -> str:
        return _check_address(v)

    @field_validator("authority")
    @classmethod
    def _authority(cls, v: str | None) -> str | None:
        return None if v is None else _check_address(v)

    @property
    def signed(self) -> bool:
        return self.y_parity is not None and self.r is not None and self.s is not None

    @field_validator("nonce", mode="before")
    @classmethod
    def _nonce(cls, v: object) -> int:
        return parse_uint(v, bits=64)


class TxRequest(BaseModel):
    """A signing request as received from the (untrusted) withdrawal service."""

    model_config = ConfigDict(populate_by_name=True)

    wallet_type: WalletType
    chain_id: int = Field(ge=1)
    from_: str = Field(alias="from")
    to: str
    data: str = "0x"
    value: int = 0
    authorization_list: list[Authorization] | None = None

    @field_validator("to", "from_")
    @classmethod
    def _addresses(cls, v: str) -> str:
        return _check_address(v)

    @field_validator("data")
    @classmethod
    def _data(cls, v: str) -> str:
        if not _HEX_BYTES.match(v):
            raise ValueError("must be 0x-prefixed hex bytes")
        return v.lower()

    @field_validator("value", mode="before")
    @classmethod
    def _value(cls, v: object) -> int:
        return parse_uint(v)


class Decision(StrEnum):
    """Ordered from least to most strict. The final verdict is the strictest one."""

    ALLOW = "ALLOW"
    DELAY = "DELAY"
    NEEDS_HUMAN = "NEEDS_HUMAN"
    BLOCK = "BLOCK"

    @property
    def rank(self) -> int:
        return _RANK[self]


_RANK = {d: i for i, d in enumerate(Decision)}


class Path(StrEnum):
    FAST = "fast"  # deterministic only, no sandbox, no LLM
    SLOW = "slow"  # fresh sandbox + state diff + rules (+ LLM explanation)


class Finding(BaseModel):
    """One rule result. Frozen: nothing downstream can change its action."""

    model_config = ConfigDict(frozen=True)

    rule_id: str
    action: Decision
    title: str
    detail: str = ""


class SimulationResult(BaseModel):
    """Raw sandbox output. Judged by content (receipt + state diff), not by exit codes."""

    ok: bool
    error: str | None = None
    receipt: dict[str, Any] | None = None
    state_diff: dict[str, Any] | None = None  # prestateTracer diffMode: {"pre": ..., "post": ...}


class LLMAssessment(BaseModel):
    """Explanation only. Never read by the decision logic."""

    intent_summary: str
    risk_score: int = Field(ge=0, le=100)


class Verdict(BaseModel):
    verdict: Decision
    reasons: list[str]
    findings: list[Finding]
    explanation: str | None = None
    risk_score: int | None = None
    path: Path | None = None
    wallet_type: WalletType | None = None
    chain_id: int | None = None
    trace_id: str
