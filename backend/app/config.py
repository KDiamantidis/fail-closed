"""Settings from environment / the repo-root .env. No secrets have defaults."""

from functools import lru_cache
from pathlib import Path

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

    # Sandbox: fresh container per check, always destroyed.
    sandbox_image: str = "fail-closed-sandbox:foundry-v1.8.5"
    sandbox_timeout_seconds: float = 30.0
    sandbox_memory: str = "512m"
    sandbox_cpus: float = 1.0
    sandbox_pids: int = 128

    # Rule thresholds: starting values, tune later.
    delay_minutes: int = 15
    r3_max_single_dest_pct: float = 25.0
    r4_treasury_limit_wei: int = 10 * 10**18
    r6_window_minutes: int = 60
    r6_outflow_limit_wei: int = 50 * 10**18
    r7_window_hours: int = 6
    r7_delay_chains: int = 3
    r7_freeze_chains: int = 5
    r8_window_minutes: int = 30
    r8_freeze_assets: int = 10


@lru_cache
def get_settings() -> Settings:
    return Settings()
