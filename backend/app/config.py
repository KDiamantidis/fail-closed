"""Settings from environment / the repo-root .env. No secrets have defaults."""

import re
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

UINT256_MAX = 2**256 - 1
REPO_ROOT = Path(__file__).resolve().parents[2]


def parse_uint(v: object, bits: int = 256) -> int:
    """Accept int, decimal string, or 0x-hex string. Reject bools and out-of-range values."""
    if isinstance(v, bool):
        raise ValueError("bool is not a number")
    if isinstance(v, int):
        n = v
    elif isinstance(v, str):
        s = v.strip().lower()
        n = int(s, 16) if s.startswith("0x") else int(s, 10)
    else:
        raise ValueError(f"expected int or numeric string, got {type(v).__name__}")
    if not 0 <= n < 2**bits:
        raise ValueError(f"value out of uint{bits} range")
    return n


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    # LLM (Vultr Serverless Inference). Explains only; never decides.
    vultr_inference_api_key: str = ""
    vultr_inference_base_url: str = "https://api.vultrinference.com/v1"
    vultr_model: str = "glm-5.3-flash"
    llm_mode: str = "auto"  # auto | mock | live
    llm_timeout_seconds: float = 15.0
    llm_max_retries: int = 1
    # Fast path (Phase 2): the LLM may only ask "escalate to slow path"; LLM error -> slow path.
    fast_path_llm_escalation: bool = True

    # Sandbox: fresh container per check, always destroyed.
    sandbox_image: str = "fail-closed-sandbox:foundry-v1.8.5"
    sandbox_timeout_seconds: float = 30.0
    sandbox_memory: str = "512m"
    sandbox_cpus: float = 1.0
    sandbox_pids: int = 128

    # History (SQLite)
    history_db: str = str(REPO_ROOT / "data" / "history.sqlite3")

    # Guard approval key (ed25519). Outside the repo, owner-only. Never in the sandbox.
    guard_key_path: str = str(Path.home() / ".fail-closed" / "guard_ed25519.pem")
    approval_ttl_seconds: int = 120

    # Guard-side policy lists (JSON arrays in env). Addresses are normalized to lowercase.
    known_tokens: list[str] = []  # ERC-20s whose transfer() counts as a plain withdrawal
    delegate_allowlist: list[str] = []  # EIP-7702 delegates that are allowed (R1)
    treasury_allowlist: list[str] = []  # destinations a treasury wallet may pay (R5)

    # Rule thresholds: starting values, tune later.
    delay_minutes: int = 15
    r2_window_hours: int = 24
    r3_max_single_dest_pct: float = 25.0
    r4_treasury_limit_wei: int = 10 * 10**18
    r6_window_minutes: int = 60
    r6_outflow_limit_wei: int = 50 * 10**18
    r7_window_hours: int = 6
    r7_delay_chains: int = 3
    r7_freeze_chains: int = 5
    r8_window_minutes: int = 30
    r8_freeze_assets: int = 10

    @field_validator("known_tokens", "delegate_allowlist", "treasury_allowlist")
    @classmethod
    def _addresses(cls, v: list[str]) -> list[str]:
        out = []
        for a in v:
            if not re.fullmatch(r"0x[0-9a-fA-F]{40}", a):
                raise ValueError(f"not an address: {a}")
            out.append(a.lower())
        return out


@lru_cache
def get_settings() -> Settings:
    return Settings()
