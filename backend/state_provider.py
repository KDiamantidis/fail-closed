"""Starting state for the sandbox.

The state always comes from the Guard side (scenario files now, the Guard's own RPC later),
never from the request: the withdrawal service is untrusted.
"""

import json
import re
from abc import ABC, abstractmethod
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import parse_uint
from app.models import TxRequest

_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
_WORD = re.compile(r"^0x[0-9a-fA-F]{1,64}$")
_CODE = re.compile(r"^0x([0-9a-fA-F]{2})*$")
_ARTIFACT = re.compile(r"^artifact:[A-Za-z0-9_]+$")


class AccountState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    balance: int = 0
    nonce: int = 0
    # Runtime bytecode as hex, or "artifact:<Name>" for code compiled into the sandbox image.
    code: str | None = None
    storage: dict[str, str] = Field(default_factory=dict)  # slot -> value, both 32-byte hex
    # ERC-20 balances held by this account: token address -> amount (base units).
    # Injected into the token's balanceOf mapping (slot 0, MockToken layout).
    token_balances: dict[str, int] = Field(default_factory=dict)

    @field_validator("token_balances", mode="before")
    @classmethod
    def _token_balances(cls, v: object) -> dict[str, int]:
        if not isinstance(v, dict):
            raise ValueError("token_balances must be an object")
        out = {}
        for token, amount in v.items():
            if not isinstance(token, str) or not _ADDRESS.match(token):
                raise ValueError(f"bad token address: {token}")
            out[token.lower()] = parse_uint(amount)
        return out

    @field_validator("balance", mode="before")
    @classmethod
    def _balance(cls, v: object) -> int:
        return parse_uint(v)

    @field_validator("nonce", mode="before")
    @classmethod
    def _nonce(cls, v: object) -> int:
        return parse_uint(v, bits=64)

    @field_validator("code")
    @classmethod
    def _code(cls, v: str | None) -> str | None:
        if v is None or _ARTIFACT.match(v):
            return v
        if not _CODE.match(v):
            raise ValueError("code must be 0x-hex bytes or artifact:<Name>")
        return v.lower()

    @field_validator("storage")
    @classmethod
    def _storage(cls, v: dict[str, str]) -> dict[str, str]:
        for slot, value in v.items():
            if not _WORD.match(slot) or not _WORD.match(value):
                raise ValueError("storage slots and values must be 0x-hex, at most 32 bytes")
        return {k.lower(): val.lower() for k, val in v.items()}


class StartingState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accounts: dict[str, AccountState] = Field(default_factory=dict)

    @field_validator("accounts")
    @classmethod
    def _keys(cls, v: dict[str, AccountState]) -> dict[str, AccountState]:
        for addr in v:
            if not _ADDRESS.match(addr):
                raise ValueError(f"bad address key: {addr}")
        lowered = {k.lower(): s for k, s in v.items()}
        if len(lowered) != len(v):
            raise ValueError("duplicate address (case-insensitive)")
        return lowered


def request_accounts(tx: TxRequest) -> set[str]:
    """Every account the request touches directly: sender, target, delegates, authorities."""
    accounts = {tx.from_, tx.to}
    for auth in tx.authorization_list or []:
        accounts.add(auth.address)
        if auth.authority is not None:
            accounts.add(auth.authority)
    return accounts


class StateProvider(ABC):
    """Returns the starting state for the accounts in a request."""

    @abstractmethod
    def get_state(self, tx: TxRequest) -> StartingState: ...


class ScenarioStateProvider(StateProvider):
    """Reads the starting state from a scenario file (its "state" section) or a bare
    state file ({"accounts": ...}). Both are Guard-side files, never the request.

    Accounts that the request touches but the scenario does not list start empty,
    exactly like a fresh address on chain.
    """

    def __init__(self, state_file: Path):
        self.state_file = Path(state_file)
        raw = json.loads(self.state_file.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and "state" in raw:
            raw = raw["state"]
        self._state = StartingState.model_validate(raw)

    def get_state(self, tx: TxRequest) -> StartingState:
        wanted = request_accounts(tx)
        return StartingState(
            accounts={a: s for a, s in self._state.accounts.items() if a in wanted}
        )


class StaticStateProvider(StateProvider):
    """A fixed Guard-side state (e.g. the merged demo scenarios for the API)."""

    def __init__(self, state: StartingState):
        self._state = state

    def get_state(self, tx: TxRequest) -> StartingState:
        wanted = request_accounts(tx)
        return StartingState(accounts={a: s for a, s in self._state.accounts.items() if a in wanted})
