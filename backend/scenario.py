"""Scenario files: scenarios/<name>.json, one file each.

    title, description, labels   display only
    policy                       Guard-side settings overrides (allowlists, known tokens, limits)
    state                        Guard-side starting state (StateProvider), never from a request
    requests[]                   what the untrusted withdrawal service asks, in order:
                                 {t: seconds from start, label, request, expect: {verdict, path, rules}}
"""

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.config import Settings
from app.models import TxRequest
from state_provider import StartingState


class Expect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: str | None = None
    path: str | None = None
    rules: list[str] = Field(default_factory=list)  # must all be present
    wallet_paused: bool | None = None  # state of the sender's pause after this request
    after_hold: str | None = None  # DELAY / NEEDS_HUMAN: outcome after the worker (released | blocked)
    safe_mode_chains: list[int] | None = None  # breaker level 3 state after this step
    global_stop: bool | None = None  # breaker level 4 state after this step


ACTIONS = ("reset_destination", "reset_wallet", "reset_safe_mode", "reset_global")


class ScenarioRequest(BaseModel):
    """One step: a request from the (untrusted) withdrawal service, or a human breaker action."""

    model_config = ConfigDict(extra="forbid")

    t: float = 0.0
    label: str  # technical label (CLI, "Technical detail" in the UI)
    plain: str = ""  # plain-language label the UI shows
    request: dict[str, Any] | None = None
    action: str | None = None  # one of ACTIONS (an admin resetting the breaker)
    target: str | int | None = None  # address, wallet or chain id for the action
    actor: str = "ops"
    expect: Expect = Field(default_factory=Expect)

    @model_validator(mode="after")
    def _one_kind(self) -> "ScenarioRequest":
        if (self.request is None) == (self.action is None):
            raise ValueError("a step has exactly one of request or action")
        if self.action is not None and self.action not in ACTIONS:
            raise ValueError(f"unknown action {self.action}")
        return self

    @property
    def is_action(self) -> bool:
        return self.action is not None

    def tx(self) -> TxRequest:
        if self.request is None:
            raise ValueError(f"step '{self.label}' is an action, not a request")
        return TxRequest.model_validate(self.request)

    def apply(self, breaker: Any, now: float) -> bool:
        """Perform a human breaker action (logged with the actor)."""
        if self.action == "reset_destination":
            return breaker.reset_destination(str(self.target), self.actor, now)
        if self.action == "reset_wallet":
            return breaker.reset_wallet(str(self.target), self.actor, now)
        if self.action == "reset_safe_mode":
            return breaker.reset_safe_mode(int(self.target), self.actor, now)
        return breaker.reset_global(self.actor, now)


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    title: str
    description: str = ""
    labels: dict[str, str] = Field(default_factory=dict)
    technical_labels: dict[str, str] = Field(default_factory=dict)  # shown only under "Technical detail"
    policy: dict[str, Any] = Field(default_factory=dict)
    state: StartingState
    requests: list[ScenarioRequest]
    path: Path | None = None

    def settings(self, base: Settings) -> Settings:
        """Base settings with this scenario's Guard-side policy applied (validated)."""
        return Settings(**{**base.model_dump(), **self.policy})

    def label(self, addr: str) -> str:
        return self.labels.get(addr.lower(), "")


def load(path: Path) -> Scenario:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    raw["labels"] = {k.lower(): v for k, v in raw.get("labels", {}).items()}
    raw["technical_labels"] = {k.lower(): v for k, v in raw.get("technical_labels", {}).items()}
    sc = Scenario.model_validate(raw)
    sc.path = Path(path)
    return sc


def discover(directory: Path) -> list[Path]:
    """Scenario files in run order (file names sort a_, b_, c_, ...)."""
    return sorted(p for p in Path(directory).glob("*.json") if p.is_file())


def merged_state(scenarios: list[Scenario]) -> StartingState:
    """All demo scenarios' Guard-side state in one (addresses must not conflict)."""
    accounts: dict = {}
    for sc in scenarios:
        for addr, acct in sc.state.accounts.items():
            if addr in accounts and accounts[addr] != acct:
                raise ValueError(f"scenario {sc.name} redefines account {addr}")
            accounts[addr] = acct
    return StartingState(accounts=accounts)


def merged_policy(scenarios: list[Scenario]) -> dict[str, Any]:
    """All demo scenarios' Guard-side policy in one: lists are joined, scalars must agree."""
    policy: dict[str, Any] = {}
    for sc in scenarios:
        for k, v in sc.policy.items():
            if isinstance(v, list):
                policy[k] = list(dict.fromkeys([*policy.get(k, []), *(x.lower() for x in v)]))
            elif k in policy and str(policy[k]) != str(v):
                raise ValueError(f"scenario {sc.name} sets {k}={v}, another set {policy[k]}")
            else:
                policy[k] = v
    return policy
