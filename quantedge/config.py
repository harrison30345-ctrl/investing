"""
Runtime configuration.

Every setting comes from an environment variable (optionally loaded from a
`.env` file in the project root). Nothing secret lives in source code; see
`.env.example` for the full list with placeholder values.

SAFETY
------
`QE_TRADING_MODE` may only be "research" or "paper". Any other value raises at
start-up. There is no broker execution code in QuantEdge, and this check means a
typo or a copied config can't quietly suggest otherwise.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

try:  # python-dotenv is optional; plain environment variables always work.
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
except ImportError:  # pragma: no cover
    pass

ALLOWED_TRADING_MODES = ("research", "paper")
DATA_PROVIDERS = ("yahoo", "eodhd", "demo")
HOME_DIR = Path(os.environ.get("QE_HOME", Path.home() / ".quantedge"))


class ConfigError(RuntimeError):
    """Raised when configuration is invalid or unsafe."""


@dataclass(frozen=True)
class Settings:
    data_provider: str
    database_url: str
    trading_mode: str
    eodhd_api_key: Optional[str]
    llm_provider: str
    llm_model: str
    anthropic_api_key: Optional[str]
    llm_monthly_budget_usd: float
    llm_cycle_budget_usd: float
    llm_effort: str
    llm_max_tokens: int
    llm_timeout_s: float
    agent_max_attempts: int
    agent_max_experiments: int
    agent_autorun_experiments: bool
    agent_cycle_timeout_s: float
    history_years: int

    @property
    def is_demo(self) -> bool:
        return self.data_provider == "demo"

    @property
    def llm_configured(self) -> bool:
        return self.llm_provider == "anthropic" and bool(self.anthropic_api_key)


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else default


def load_settings() -> Settings:
    mode = (_env("QE_TRADING_MODE", "research") or "research").lower()
    if mode not in ALLOWED_TRADING_MODES:
        raise ConfigError(
            f"QE_TRADING_MODE={mode!r} is not allowed. QuantEdge supports only "
            f"{ALLOWED_TRADING_MODES}; real-money execution is not implemented."
        )

    provider = (_env("QE_DATA_PROVIDER", "yahoo") or "yahoo").lower()
    if provider not in DATA_PROVIDERS:
        raise ConfigError(f"QE_DATA_PROVIDER must be one of {DATA_PROVIDERS}, got {provider!r}")

    default_db = f"sqlite:///{HOME_DIR / 'quantedge.db'}"
    return Settings(
        data_provider=provider,
        database_url=_env("QE_DATABASE_URL", default_db),
        trading_mode=mode,
        eodhd_api_key=_env("EODHD_API_KEY"),
        llm_provider=(_env("QE_LLM_PROVIDER", "none") or "none").lower(),
        llm_model=_env("QE_LLM_MODEL", "claude-opus-5-5"),
        anthropic_api_key=_env("ANTHROPIC_API_KEY"),
        llm_monthly_budget_usd=float(_env("QE_LLM_MONTHLY_BUDGET_USD", "10") or 10),
        llm_cycle_budget_usd=float(_env("QE_LLM_CYCLE_BUDGET_USD", "2.5") or 2.5),
        llm_effort=(_env("QE_LLM_EFFORT", "high") or "high").lower(),
        llm_max_tokens=int(_env("QE_LLM_MAX_TOKENS", "16000") or 16000),
        llm_timeout_s=float(_env("QE_LLM_TIMEOUT_S", "240") or 240),
        agent_max_attempts=int(_env("QE_AGENT_MAX_ATTEMPTS", "2") or 2),
        agent_max_experiments=int(_env("QE_AGENT_MAX_EXPERIMENTS", "5") or 5),
        agent_autorun_experiments=(_env("QE_AGENT_AUTORUN", "true") or "true").lower() in ("1", "true", "yes"),
        agent_cycle_timeout_s=float(_env("QE_AGENT_CYCLE_TIMEOUT_S", "1200") or 1200),
        history_years=int(_env("QE_HISTORY_YEARS", "10") or 10),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
