"""Provider registry. `get_provider()` returns the one configured by QE_DATA_PROVIDER."""
from __future__ import annotations

from typing import Optional

from .base import (MarketDataProvider, NotSupported, ProviderError, ProviderInfo, RateLimited,
                   SymbolNotFound)


def get_provider(key: Optional[str] = None) -> MarketDataProvider:
    from ...config import get_settings

    settings = get_settings()
    key = key or settings.data_provider
    if key == "yahoo":
        from .yahoo import YahooProvider
        return YahooProvider()
    if key == "demo":
        from .demo import DemoProvider
        return DemoProvider()
    if key == "eodhd":
        from .eodhd import EODHDProvider
        return EODHDProvider(settings.eodhd_api_key or "")
    raise ProviderError(f"Unknown data provider {key!r}")


def provider_info(key: Optional[str] = None) -> ProviderInfo:
    """Static description without constructing a client (no network, no key needed)."""
    from ...config import get_settings
    key = key or get_settings().data_provider
    if key == "yahoo":
        from .yahoo import YahooProvider
        return YahooProvider.info
    if key == "demo":
        from .demo import DemoProvider
        return DemoProvider.info
    from .eodhd import EODHDProvider
    return EODHDProvider.info


__all__ = ["get_provider", "provider_info", "MarketDataProvider", "ProviderInfo", "ProviderError",
           "SymbolNotFound", "RateLimited", "NotSupported"]
