from __future__ import annotations

"""
Metrics calculator — extracts and computes all fundamental metrics from raw data.

Each function returns a float or None (if data unavailable).
"""

import logging
import math

import pandas as pd

logger = logging.getLogger(__name__)


def _safe_get(info: dict, key: str) -> float | None:
    """Get a numeric value from info dict, returning None if missing/invalid."""
    val = info.get(key)
    if val is None:
        return None
    try:
        val = float(val)
        if math.isnan(val) or math.isinf(val):
            return None
        return val
    except (ValueError, TypeError):
        return None


def _get_statement_value(df: pd.DataFrame, row_labels: list[str], col_index: int = 0) -> float | None:
    """
    Extract a value from a financial statement DataFrame.
    Tries each label in row_labels (yfinance naming varies).
    col_index=0 is most recent period.
    """
    if df is None or df.empty:
        return None
    for label in row_labels:
        if label in df.index:
            try:
                val = df.loc[label].iloc[col_index]
                if pd.notna(val):
                    return float(val)
            except (IndexError, TypeError):
                continue
    return None


def _cagr(start_val: float | None, end_val: float | None, years: int) -> float | None:
    """Compound annual growth rate. Returns as percentage (e.g. 15.0 for 15%)."""
    if start_val is None or end_val is None or years <= 0:
        return None
    if start_val <= 0 or end_val <= 0:
        return None
    try:
        return ((end_val / start_val) ** (1 / years) - 1) * 100
    except (ZeroDivisionError, ValueError):
        return None


def _yoy_growth(current: float | None, previous: float | None) -> float | None:
    """Year-over-year growth as percentage."""
    if current is None or previous is None or previous == 0:
        return None
    return ((current - previous) / abs(previous)) * 100


def _get_revenue_series(financials: pd.DataFrame) -> list[float | None]:
    """Get revenue values from most recent to oldest."""
    labels = ["Total Revenue", "Revenue", "Operating Revenue"]
    if financials is None or financials.empty:
        return []
    for label in labels:
        if label in financials.index:
            return [
                float(v) if pd.notna(v) else None
                for v in financials.loc[label]
            ]
    return []


def _get_eps_series(financials: pd.DataFrame, info: dict) -> list[float | None]:
    """Get EPS series. yfinance financials have net income; divide by shares."""
    if financials is None or financials.empty:
        return []
    net_income_labels = ["Net Income", "Net Income Common Stockholders"]
    shares = _safe_get(info, "sharesOutstanding")

    for label in net_income_labels:
        if label in financials.index:
            values = []
            for v in financials.loc[label]:
                if pd.notna(v) and shares and shares > 0:
                    values.append(float(v) / shares)
                else:
                    values.append(None)
            return values
    return []


def _get_fcf_series(cashflow: pd.DataFrame) -> list[float | None]:
    """Get free cash flow series (operating CF - capex)."""
    if cashflow is None or cashflow.empty:
        return []
    opcf_labels = ["Operating Cash Flow", "Total Cash From Operating Activities",
                    "Cash Flow From Continuing Operating Activities"]
    capex_labels = ["Capital Expenditure", "Capital Expenditures",
                    "Purchase Of Property Plant And Equipment"]

    opcf_row = None
    for label in opcf_labels:
        if label in cashflow.index:
            opcf_row = cashflow.loc[label]
            break

    capex_row = None
    for label in capex_labels:
        if label in cashflow.index:
            capex_row = cashflow.loc[label]
            break

    if opcf_row is None:
        return []

    result = []
    for i in range(len(opcf_row)):
        opcf = float(opcf_row.iloc[i]) if pd.notna(opcf_row.iloc[i]) else None
        capex = 0.0
        if capex_row is not None and i < len(capex_row) and pd.notna(capex_row.iloc[i]):
            capex = float(capex_row.iloc[i])
        if opcf is not None:
            # capex is usually negative in yfinance, so FCF = opcf + capex (which is negative)
            result.append(opcf + capex if capex < 0 else opcf - abs(capex))
        else:
            result.append(None)
    return result


def _shares_outstanding_series(balance_sheet: pd.DataFrame) -> list[float | None]:
    """Get shares outstanding series from balance sheet."""
    if balance_sheet is None or balance_sheet.empty:
        return []
    labels = ["Ordinary Shares Number", "Share Issued", "Common Stock Shares Outstanding"]
    for label in labels:
        if label in balance_sheet.index:
            return [
                float(v) if pd.notna(v) else None
                for v in balance_sheet.loc[label]
            ]
    return []


def calculate_all_metrics(ticker: str, data: dict) -> dict:
    """
    Calculate all fundamental metrics for a ticker.
    Returns a flat dict of {metric_name: value_or_None}.
    """
    info = data.get("info", {})
    financials = data.get("financials", pd.DataFrame())
    balance_sheet = data.get("balance_sheet", pd.DataFrame())
    cashflow = data.get("cashflow", pd.DataFrame())

    m = {"ticker": ticker, "name": info.get("shortName", info.get("longName", ticker))}

    # ── Valuation ──
    m["pe_trailing"] = _safe_get(info, "trailingPE")
    m["pe_forward"] = _safe_get(info, "forwardPE")
    m["pb"] = _safe_get(info, "priceToBook")
    m["ps"] = _safe_get(info, "priceToSalesTrailing12Months")
    m["ev_ebitda"] = _safe_get(info, "enterpriseToEbitda")
    m["peg"] = _safe_get(info, "pegRatio")

    # ── Profitability ──
    m["roe"] = _pct_from_info(info, "returnOnEquity")
    m["roa"] = _pct_from_info(info, "returnOnAssets")
    m["roic"] = _calculate_roic(financials, balance_sheet)
    m["gross_margin"] = _pct_from_info(info, "grossMargins")
    m["operating_margin"] = _pct_from_info(info, "operatingMargins")
    m["net_margin"] = _pct_from_info(info, "profitMargins")

    # ── Financial health ──
    m["debt_to_equity"] = _safe_get(info, "debtToEquity")
    if m["debt_to_equity"] is not None:
        # yfinance always returns D/E as percentage (e.g. 102.63 = 1.0263 ratio)
        m["debt_to_equity"] = m["debt_to_equity"] / 100.0
    m["current_ratio"] = _safe_get(info, "currentRatio")
    m["quick_ratio"] = _safe_get(info, "quickRatio")
    m["interest_coverage"] = _calculate_interest_coverage(financials)
    m["net_debt_to_ebitda"] = _calculate_net_debt_to_ebitda(info, balance_sheet, financials)

    # ── Growth ──
    revenue_series = _get_revenue_series(financials)
    eps_series = _get_eps_series(financials, info)
    fcf_series = _get_fcf_series(cashflow)

    # Revenue growth
    m["revenue_growth_1y"] = _yoy_growth(
        revenue_series[0] if len(revenue_series) > 0 else None,
        revenue_series[1] if len(revenue_series) > 1 else None,
    )
    m["revenue_growth_3y_cagr"] = _cagr(
        revenue_series[3] if len(revenue_series) > 3 else None,
        revenue_series[0] if len(revenue_series) > 0 else None,
        3,
    )
    m["revenue_growth_5y_cagr"] = _cagr(
        revenue_series[4] if len(revenue_series) > 4 else None,
        revenue_series[0] if len(revenue_series) > 0 else None,
        5,
    )

    # EPS growth
    m["eps_growth_1y"] = _yoy_growth(
        eps_series[0] if len(eps_series) > 0 else None,
        eps_series[1] if len(eps_series) > 1 else None,
    )
    m["eps_growth_3y_cagr"] = _cagr(
        eps_series[3] if len(eps_series) > 3 else None,
        eps_series[0] if len(eps_series) > 0 else None,
        3,
    )
    m["eps_growth_5y_cagr"] = _cagr(
        eps_series[4] if len(eps_series) > 4 else None,
        eps_series[0] if len(eps_series) > 0 else None,
        5,
    )

    # FCF growth
    m["fcf_growth_1y"] = _yoy_growth(
        fcf_series[0] if len(fcf_series) > 0 else None,
        fcf_series[1] if len(fcf_series) > 1 else None,
    )

    # ── Cash flow ──
    m["fcf"] = fcf_series[0] if len(fcf_series) > 0 else None
    market_cap = _safe_get(info, "marketCap")
    if m["fcf"] is not None and market_cap and market_cap > 0:
        m["fcf_yield"] = (m["fcf"] / market_cap) * 100
    else:
        m["fcf_yield"] = None

    total_revenue = revenue_series[0] if len(revenue_series) > 0 else None
    if m["fcf"] is not None and total_revenue and total_revenue > 0:
        m["fcf_margin"] = (m["fcf"] / total_revenue) * 100
    else:
        m["fcf_margin"] = None

    # ── Dividends ──
    m["dividend_yield"] = _pct_from_info(info, "dividendYield")
    m["payout_ratio"] = _pct_from_info(info, "payoutRatio")
    m["dividend_growth_streak"] = _estimate_dividend_streak(info)

    # ── Quality flags ──
    m["fcf_positive_3y"] = _check_fcf_positive_3y(fcf_series)
    m["no_dilution_3y"] = _check_no_dilution(balance_sheet)

    # ── Data completeness ──
    metric_keys = [k for k in m if k not in ("ticker", "name", "fcf_positive_3y", "no_dilution_3y")]
    total = len(metric_keys)
    present = sum(1 for k in metric_keys if m[k] is not None)
    m["data_completeness"] = round((present / total) * 100, 1) if total > 0 else 0.0

    return m


def _pct_from_info(info: dict, key: str) -> float | None:
    """Get a value from info that yfinance stores as a decimal (0.15) and return as % (15.0)."""
    val = _safe_get(info, key)
    if val is None:
        return None
    # yfinance always stores these as decimals (1.52 = 152%, 0.15 = 15%)
    return val * 100


def _calculate_roic(financials: pd.DataFrame, balance_sheet: pd.DataFrame) -> float | None:
    """ROIC = NOPAT / Invested Capital."""
    ebit = _get_statement_value(financials, ["EBIT", "Operating Income"])
    tax_rate_labels = ["Tax Provision", "Income Tax Expense"]
    pretax_labels = ["Pretax Income", "Income Before Tax"]

    tax = _get_statement_value(financials, tax_rate_labels)
    pretax = _get_statement_value(financials, pretax_labels)

    if ebit is None:
        return None

    # Estimate tax rate
    if tax is not None and pretax is not None and pretax != 0:
        tax_rate = tax / pretax
    else:
        tax_rate = 0.25  # Assume 25% if unknown

    nopat = ebit * (1 - tax_rate)

    total_debt = _get_statement_value(balance_sheet, [
        "Total Debt", "Long Term Debt And Capital Lease Obligation",
        "Long Term Debt", "Total Non Current Liabilities Net Minority Interest",
    ])
    total_equity = _get_statement_value(balance_sheet, [
        "Stockholders Equity", "Total Equity Gross Minority Interest",
        "Common Stock Equity",
    ])
    cash = _get_statement_value(balance_sheet, [
        "Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments",
    ])

    if total_equity is None:
        return None

    invested_capital = (total_equity or 0) + (total_debt or 0) - (cash or 0)
    if invested_capital <= 0:
        return None

    return (nopat / invested_capital) * 100


def _calculate_interest_coverage(financials: pd.DataFrame) -> float | None:
    """Interest coverage = EBIT / Interest Expense."""
    ebit = _get_statement_value(financials, ["EBIT", "Operating Income"])
    interest = _get_statement_value(financials, [
        "Interest Expense", "Interest Expense Non Operating",
        "Net Interest Income",
    ])
    if ebit is None or interest is None or interest == 0:
        return None
    # Interest expense is often negative in yfinance
    return ebit / abs(interest)


def _calculate_net_debt_to_ebitda(info: dict, balance_sheet: pd.DataFrame,
                                   financials: pd.DataFrame) -> float | None:
    """Net debt / EBITDA."""
    total_debt = _get_statement_value(balance_sheet, [
        "Total Debt", "Long Term Debt", "Long Term Debt And Capital Lease Obligation",
    ])
    cash = _get_statement_value(balance_sheet, [
        "Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments",
    ])
    ebitda = _safe_get(info, "ebitda")
    if ebitda is None:
        ebitda = _get_statement_value(financials, ["EBITDA", "Normalized EBITDA"])

    if total_debt is None or ebitda is None or ebitda <= 0:
        return None

    net_debt = total_debt - (cash or 0)
    return net_debt / ebitda


def _estimate_dividend_streak(info: dict) -> float | None:
    """
    yfinance doesn't provide dividend growth streak directly.
    We use a proxy: if there's a trailing annual dividend rate, report it.
    For a true streak you'd need historical dividend data — mark as limited.
    """
    # This is a known limitation; the README will note it
    rate = _safe_get(info, "trailingAnnualDividendRate")
    if rate is not None and rate > 0:
        return _safe_get(info, "trailingAnnualDividendRate")
    return None


def _check_fcf_positive_3y(fcf_series: list[float | None]) -> bool | None:
    """Check if FCF was positive in the last 3 years."""
    if len(fcf_series) < 3:
        return None  # Not enough data
    for i in range(3):
        if fcf_series[i] is None or fcf_series[i] <= 0:
            return False
    return True


def _check_no_dilution(balance_sheet: pd.DataFrame) -> bool | None:
    """Check if share count is stable or falling over last 3 years."""
    shares = _shares_outstanding_series(balance_sheet)
    if len(shares) < 3:
        return None
    # shares[0] is most recent
    if shares[0] is None or shares[2] is None:
        return None
    # Allow up to 2% dilution as noise
    return shares[0] <= shares[2] * 1.02
