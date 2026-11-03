"""Settings from environment / .env. No secrets have defaults."""

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

UINT256_MAX = 2**256 - 1


def parse_uint(v: object) -> int:
    """Accept int, decimal string, or 0x-hex string. Reject anything outside uint256."""
    if isinstance(v, bool):
        raise ValueError("bool is not a number")
    if isinstance(v, int):
        n = v
    elif isinstance(v, str):
        s = v.strip().lower()
        n = int(s, 16) if s.startswith("0x") else int(s, 10)
    else:
        raise ValueError(f"expected int or numeric string, got {type(v).__name__}")
    if not 0 <= n <= UINT256_MAX:
        raise ValueError("value out of uint256 range")
    return n


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM (Vultr Serverless Inference)
    vultr_inference_api_key: str = ""
    vultr_inference_base_url: str = "https://api.vultrinference.com/v1"
    vultr_model: str = "glm-5.3-flash"
    llm_mode: str = "auto"  # auto | mock | live
    llm_timeout_seconds: float = 20.0
    llm_max_retries: int = 1

    # Sandbox
    sandbox_image: str = "fail-closed-sandbox:latest"
    sandbox_timeout_seconds: float = 30.0
    sandbox_memory_limit: str = "512m"

    # Policies
    approval_large_threshold: int = 2**128

    @field_validator("approval_large_threshold", mode="before")
    @classmethod
    def _parse_threshold(cls, v: object) -> int:
        n = parse_uint(v)
        if n == 0:
            raise ValueError("threshold 0 would flag every approval")
        return n


@lru_cache
def get_settings() -> Settings:
    return Settings()
