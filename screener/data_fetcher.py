from __future__ import annotations

"""
Data fetcher module with swappable backend.

To use a different data source, create a new class implementing the same
interface as YFinanceFetcher and pass it to the screener.
"""

import logging
import os
import time
from abc import ABC, abstractmethod
from pathlib import Path

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

CACHE_DIR = Path.home() / ".screener" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


class DataFetcherBase(ABC):
    """Interface that any data source must implement."""

    @abstractmethod
    def fetch_ticker_data(self, ticker: str) -> dict:
        """
        Return a dict with keys:
            - 'info': dict of stock info/fundamentals
            - 'financials': annual income statement (DataFrame)
            - 'quarterly_financials': quarterly income statement (DataFrame)
            - 'balance_sheet': annual balance sheet (DataFrame)
            - 'quarterly_balance_sheet': quarterly balance sheet (DataFrame)
            - 'cashflow': annual cash flow statement (DataFrame)
            - 'quarterly_cashflow': quarterly cash flow statement (DataFrame)
        """
        ...

    @abstractmethod
    def fetch_batch(self, tickers: list[str]) -> dict[str, dict]:
        """Fetch data for a list of tickers. Returns {ticker: data_dict}."""
        ...


class YFinanceFetcher(DataFetcherBase):
    """Primary data source using yfinance (free, no API key)."""

    def __init__(self, cache_expiry_hours: int = 24):
        self.cache_expiry_seconds = cache_expiry_hours * 3600

    def _cache_path(self, ticker: str) -> Path:
        return CACHE_DIR / f"{ticker.upper().replace('.', '_')}.parquet"

    def _info_cache_path(self, ticker: str) -> Path:
        return CACHE_DIR / f"{ticker.upper().replace('.', '_')}_info.parquet"

    def _is_cache_valid(self, path: Path) -> bool:
        if not path.exists():
            return False
        age = time.time() - path.stat().st_mtime
        return age < self.cache_expiry_seconds

    def _cache_dataframe(self, df: pd.DataFrame, path: Path) -> None:
        if df is not None and not df.empty:
            df.to_parquet(path)

    def _load_cached_dataframe(self, path: Path) -> pd.DataFrame | None:
        if self._is_cache_valid(path):
            try:
                return pd.read_parquet(path)
            except Exception:
                return None
        return None

    def _cache_info(self, info: dict, ticker: str) -> None:
        path = self._info_cache_path(ticker)
        try:
            df = pd.DataFrame([info])
            df.to_parquet(path)
        except Exception as e:
            logger.debug(f"Could not cache info for {ticker}: {e}")

    def _load_cached_info(self, ticker: str) -> dict | None:
        path = self._info_cache_path(ticker)
        if self._is_cache_valid(path):
            try:
                df = pd.read_parquet(path)
                return df.iloc[0].to_dict()
            except Exception:
                return None
        return None

    def fetch_ticker_data(self, ticker: str) -> dict:
        """Fetch all fundamental data for a single ticker."""
        # Check if we have a full cached dataset
        cached_info = self._load_cached_info(ticker)
        statement_keys = [
            "financials", "quarterly_financials",
            "balance_sheet", "quarterly_balance_sheet",
            "cashflow", "quarterly_cashflow",
        ]
        cached_statements = {}
        all_cached = cached_info is not None
        if all_cached:
            for key in statement_keys:
                path = CACHE_DIR / f"{ticker.upper().replace('.', '_')}_{key}.parquet"
                df = self._load_cached_dataframe(path)
                if df is not None:
                    cached_statements[key] = df
                else:
                    all_cached = False
                    break

        if all_cached:
            logger.info(f"{ticker}: using cached data")
            return {"info": cached_info, **cached_statements}

        # Fetch fresh data
        logger.info(f"{ticker}: fetching from yfinance...")
        try:
            t = yf.Ticker(ticker)
            info = t.info or {}

            data = {
                "info": info,
                "financials": t.financials,
                "quarterly_financials": t.quarterly_financials,
                "balance_sheet": t.balance_sheet,
                "quarterly_balance_sheet": t.quarterly_balance_sheet,
                "cashflow": t.cashflow,
                "quarterly_cashflow": t.quarterly_cashflow,
            }

            # Cache everything
            self._cache_info(info, ticker)
            for key in statement_keys:
                df = data.get(key)
                if df is not None and not df.empty:
                    path = CACHE_DIR / f"{ticker.upper().replace('.', '_')}_{key}.parquet"
                    self._cache_dataframe(df, path)

            return data

        except Exception as e:
            logger.error(f"{ticker}: fetch failed — {e}")
            return {
                "info": {},
                "financials": pd.DataFrame(),
                "quarterly_financials": pd.DataFrame(),
                "balance_sheet": pd.DataFrame(),
                "quarterly_balance_sheet": pd.DataFrame(),
                "cashflow": pd.DataFrame(),
                "quarterly_cashflow": pd.DataFrame(),
            }

    def fetch_batch(self, tickers: list[str]) -> dict[str, dict]:
        """Fetch data for multiple tickers sequentially with rate limiting."""
        results = {}
        total = len(tickers)
        for i, ticker in enumerate(tickers, 1):
            logger.info(f"[{i}/{total}] Processing {ticker}")
            results[ticker] = self.fetch_ticker_data(ticker)
            # Small delay to be polite to yfinance
            if i < total:
                time.sleep(0.5)
        return results
