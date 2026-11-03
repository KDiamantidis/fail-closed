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

from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings
from app.models import TxRequest
from state_provider import StartingState


class Expect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: str | None = None
    path: str | None = None
    rules: list[str] = Field(default_factory=list)  # must all be present
    wallet_paused: bool | None = None  # state of the sender's pause after this request


class ScenarioRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    t: float = 0.0
    label: str
    request: dict[str, Any]
    expect: Expect = Field(default_factory=Expect)

    def tx(self) -> TxRequest:
        return TxRequest.model_validate(self.request)


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    title: str
    description: str = ""
    labels: dict[str, str] = Field(default_factory=dict)
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
    sc = Scenario.model_validate(raw)
    sc.path = Path(path)
    return sc


def discover(directory: Path) -> list[Path]:
    """Scenario files in run order (file names sort a_, b_, c_, ...)."""
    return sorted(p for p in Path(directory).glob("*.json") if p.is_file())
