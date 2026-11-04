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
    # "" = the provider's default. "minimal" keeps thinking tokens (billed as output) and latency low.
    llm_reasoning_effort: str = "minimal"
    llm_json_mode: bool = True  # response_format json_object (we still parse and validate ourselves)
    llm_max_tokens: int = 1500
    # Mock only (no Vultr key yet): make the numbers realistic and say so in every result.
    mock_llm_latency_ms: int = 800  # each mock call sleeps this long, like a real model call
    mock_llm_escalate_rate: float = 0.0  # share of plain transfers the mock escalates (deterministic)
    # Fast path (Phase 2): the LLM may only ask "escalate to slow path"; LLM error -> slow path.
    fast_path_llm_escalation: bool = True
    fast_path_llm_timeout_seconds: float = 2.0  # escalation call only; timeout -> slow path

    # Sandbox: fresh container per check, always destroyed.
    sandbox_image: str = "fail-closed-sandbox:foundry-v1.8.5"
    sandbox_timeout_seconds: float = 10.0  # slow checks take 1-5 s; a cold Docker start can take ~8 s
    sandbox_memory: str = "512m"
    sandbox_cpus: float = 1.0
    sandbox_pids: int = 128
    sandbox_max_concurrent: int = 4  # sandboxes at once on this host
    sandbox_mode: str = "fresh"  # fresh: new container per check | warm: pool + snapshot/revert
    sandbox_pool_size: int = 4  # warm: containers kept ready
    sandbox_max_uses: int = 50  # warm: checks per container, then it is replaced
    sandbox_max_age_seconds: float = 600.0  # warm: age limit per container
    sandbox_queue_seconds: float = 10.0  # wait for a slot, then 503 "Guard busy" (fail closed)

    # History (SQLite)
    history_db: str = str(REPO_ROOT / "data" / "history.sqlite3")

    # Guard approval key (ed25519). Outside the repo, owner-only. Never in the sandbox.
    guard_key_path: str = str(Path.home() / ".fail-closed" / "guard_ed25519.pem")
    approval_ttl_seconds: int = 120

    # API
    admin_token: str = ""  # required for admin actions (reset, approve/reject); empty = disabled
    redteam_public: bool = False  # true: POST /redteam/run without the admin token (one run at a time)
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    signer_db: str = str(REPO_ROOT / "data" / "signer.sqlite3")  # mock signer replay store

    # Guard-side policy lists (JSON arrays in env). Addresses are normalized to lowercase.
    known_tokens: list[str] = []  # ERC-20s whose transfer() counts as a plain withdrawal
    delegate_allowlist: list[str] = []  # EIP-7702 delegates that are allowed (R1)
    treasury_allowlist: list[str] = []  # destinations a treasury wallet may pay (R5)

    # Rule thresholds: starting values, tune later.
    delay_minutes: int = 15
    delay_check_seconds: float = 5.0  # how often the DELAY worker looks for due holds
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
    # Circuit breaker level 3 (safe mode on one chain): starting values
    safe_mode_paused_wallets: int = 2  # wallets paused by checks on the chain within the window
    safe_mode_evidence_blocks: int = 3  # R1/R2/R3-evidence BLOCKs on the chain within the window
    safe_mode_window_minutes: int = 60
    # Level 4 (global stop) needs at least this many independent signals
    global_stop_signals: int = 2

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
