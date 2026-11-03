"""Request / verdict data models."""

import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import parse_uint

_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
_HEX_BYTES = re.compile(r"^0x([0-9a-fA-F]{2})*$")


class TxRequest(BaseModel):
    """An unsigned EVM transaction as the wallet would sign it."""

    model_config = ConfigDict(populate_by_name=True)

    to: str
    data: str = "0x"
    value: int = 0
    from_: str = Field(alias="from")
    chain_id: int = Field(ge=1)

    @field_validator("to", "from_")
    @classmethod
    def _address(cls, v: str) -> str:
        if not _ADDRESS.match(v):
            raise ValueError("must be a 0x-prefixed 20-byte hex address")
        return v.lower()

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
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"


class Severity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Finding(BaseModel):
    """One observation. Frozen so nothing downstream (e.g. the LLM step) can flip `blocking`."""

    model_config = ConfigDict(frozen=True)

    rule_id: str
    severity: Severity
    blocking: bool
    title: str
    detail: str = ""
    source: str = "deterministic"  # deterministic | llm


class SimulationResult(BaseModel):
    """What the sandbox returned. `ok` is True only if the tx was actually simulated."""

    ok: bool
    error: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class LLMAssessment(BaseModel):
    """What the agent returned via its tool call."""

    block: bool
    risk: Severity
    explanation: str


class Verdict(BaseModel):
    verdict: Decision
    reasons: list[str]
    findings: list[Finding]
    explanation: str
    trace_id: str
    simulation: dict[str, Any] | None = None
