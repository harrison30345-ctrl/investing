from __future__ import annotations

"""
Trading engine configuration and the LIVE-TRADING SAFETY GATE.

Design principle: paper trading is the default and requires nothing. Live
trading with real money is deliberately hard to turn on — it requires an
explicit environment flag AND live API keys AND (by default) per-order human
approval. This makes it impossible to "accidentally" trade real money, and
means no agent can autonomously drain a live account.

Environment variables:
    TRADING_MODE          "paper" (default) or "live"
    ALPACA_API_KEY        Alpaca key id (paper or live)
    ALPACA_API_SECRET     Alpaca secret
    TRADING_REQUIRE_APPROVAL  "1" (default) — live orders need confirmation
                              set to "0" only if you have your own approval UI
    TRADING_STATE_DIR     where portfolio/P&L state is stored
                          (default ~/.trading)
"""

import os
from dataclasses import dataclass
from pathlib import Path

STATE_DIR = Path(os.environ.get("TRADING_STATE_DIR", str(Path.home() / ".trading")))
STATE_DIR.mkdir(parents=True, exist_ok=True)

# Alpaca REST endpoints. Paper and live are identical APIs on different hosts —
# this is the ONLY thing that changes between simulated and real money.
ALPACA_PAPER_URL = "https://paper-api.alpaca.markets"
ALPACA_LIVE_URL = "https://api.alpaca.markets"
ALPACA_DATA_URL = "https://data.alpaca.markets"


@dataclass
class TradingConfig:
    mode: str  # "paper" or "live"
    alpaca_key: str | None
    alpaca_secret: str | None
    require_approval: bool
    state_dir: Path

    @property
    def is_live(self) -> bool:
        return self.mode == "live"

    @property
    def alpaca_base_url(self) -> str:
        return ALPACA_LIVE_URL if self.is_live else ALPACA_PAPER_URL

    def assert_live_allowed(self) -> None:
        """
        Fail loudly if something tries to go live without the full opt-in.
        Called by any broker adapter before it will touch a live endpoint.
        """
        if not self.is_live:
            return
        if not (self.alpaca_key and self.alpaca_secret):
            raise RuntimeError(
                "LIVE trading requested but ALPACA_API_KEY / ALPACA_API_SECRET "
                "are not set. Refusing to proceed."
            )
        # A visible, unmissable confirmation that real money is in play.
        banner = (
            "\n" + "!" * 64 + "\n"
            "  LIVE TRADING MODE — REAL MONEY IS AT RISK\n"
            "  Orders will be placed against a real Alpaca brokerage account.\n"
            + "!" * 64 + "\n"
        )
        print(banner)


def load_config() -> TradingConfig:
    mode = os.environ.get("TRADING_MODE", "paper").strip().lower()
    if mode not in ("paper", "live"):
        raise ValueError(f"TRADING_MODE must be 'paper' or 'live', got {mode!r}")

    require_approval = os.environ.get("TRADING_REQUIRE_APPROVAL", "1").strip() != "0"

    return TradingConfig(
        mode=mode,
        alpaca_key=os.environ.get("ALPACA_API_KEY"),
        alpaca_secret=os.environ.get("ALPACA_API_SECRET"),
        require_approval=require_approval,
        state_dir=STATE_DIR,
    )
