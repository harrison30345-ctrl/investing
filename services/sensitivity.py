"""
What a company's shares have actually moved with.

Commentary is full of claims like "this name is rate-sensitive" or "a strong
dollar hurts them". Those are testable. This module tests them: it regresses a
company's daily returns on each macro factor's daily move over the trailing
year and reports the slope together with the R-squared.

WHY THE R-SQUARED IS NEVER OPTIONAL
-----------------------------------
A beta on its own always looks like a finding. Fitting a line through a cloud of
noise returns a number regardless, and for most stock/macro pairs that number
explains almost none of the variation. Reporting the slope without the fit is
the single easiest way to make noise look like insight, so `describe()` refuses
to characterise a relationship below MIN_R2 and says there is no measurable one.

LIMITS, PLAINLY
---------------
* This is correlation over one window. It is not causation, and the window
  matters -- a different year gives different numbers.
* Relationships are unstable. A stock that tracked rates last year may not now.
* Overlapping factors (the dollar and oil, yields and the curve) are each
  regressed separately, so their sensitivities are not independent and must not
  be added together.
* It says nothing about what happens next.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

__all__ = [
    "FactorBeta", "FACTORS", "MIN_OBSERVATIONS", "MIN_R2",
    "sensitivities", "describe",
]

MIN_OBSERVATIONS = 120      # roughly six months of trading days
MIN_R2 = 0.05               # below this, the factor explains ~nothing

# Factor symbol -> (label, mode). "price" factors are measured in percentage
# returns; "level" factors (yields) in absolute daily change, because the
# percentage change of a percentage is not a quantity anyone can interpret.
FACTORS = {
    "^TNX": ("the 10-year yield", "level"),
    "DX-Y.NYB": ("the US dollar", "price"),
    "CL=F": ("crude oil", "price"),
    "^VIX": ("volatility (VIX)", "price"),
    "^GSPC": ("the S&P 500", "price"),
}


@dataclass(frozen=True)
class FactorBeta:
    ticker: str
    factor: str
    label: str
    beta: float            # % share move per 1 unit of factor move
    r2: float              # 0-1, share of the stock's variation explained
    observations: int
    mode: str

    @property
    def measurable(self) -> bool:
        return self.r2 >= MIN_R2

    @property
    def unit(self) -> str:
        return "per 1pp" if self.mode == "level" else "per 1%"

    @property
    def direction(self) -> str:
        return "rises with" if self.beta > 0 else "falls with"


def _returns(series: pd.Series, mode: str) -> pd.Series:
    series = series.dropna()
    if mode == "level":
        return series.diff().dropna()
    return series.pct_change().replace([np.inf, -np.inf], np.nan).dropna() * 100


def _fit(stock: pd.Series, factor: pd.Series) -> tuple:
    """OLS slope and R-squared on the overlapping dates. (None, None, n) if thin."""
    paired = pd.concat([stock, factor], axis=1, join="inner").dropna()
    n = len(paired)
    if n < MIN_OBSERVATIONS:
        return None, None, n

    y = paired.iloc[:, 0].to_numpy(dtype=float)
    x = paired.iloc[:, 1].to_numpy(dtype=float)
    if x.std() == 0 or y.std() == 0:
        return None, None, n

    slope, intercept = np.polyfit(x, y, 1)
    predicted = slope * x + intercept
    residual = float(((y - predicted) ** 2).sum())
    total = float(((y - y.mean()) ** 2).sum())
    r2 = 0.0 if total == 0 else max(0.0, 1 - residual / total)
    return float(slope), float(r2), n


def sensitivities(prices: pd.DataFrame, factors: pd.DataFrame) -> dict:
    """Map each column of `prices` to its factor betas, strongest fit first.

    Both arguments are date-indexed closing-price frames: `prices` one column
    per company, `factors` one column per symbol in FACTORS.
    """
    out: dict = {}
    if prices is None or prices.empty or factors is None or factors.empty:
        return out

    factor_moves = {}
    for symbol, (label, mode) in FACTORS.items():
        if symbol in factors.columns:
            moves = _returns(factors[symbol], mode)
            if len(moves) >= MIN_OBSERVATIONS:
                factor_moves[symbol] = (label, mode, moves)

    for ticker in prices.columns:
        stock_returns = _returns(prices[ticker], "price")
        if len(stock_returns) < MIN_OBSERVATIONS:
            continue
        rows = []
        for symbol, (label, mode, moves) in factor_moves.items():
            beta, r2, n = _fit(stock_returns, moves)
            if beta is None or r2 is None:
                continue
            rows.append(FactorBeta(str(ticker), symbol, label, beta, r2, n, mode))
        if rows:
            out[str(ticker)] = sorted(rows, key=lambda r: r.r2, reverse=True)
    return out


def describe(row: FactorBeta) -> str:
    """One sentence, hedged in proportion to how well the line actually fits."""
    if not row.measurable:
        return (f"No measurable relationship with {row.label} over the past year "
                f"(it explains under {MIN_R2:.0%} of the share's daily moves).")

    strength = "loosely" if row.r2 < 0.15 else ("moderately" if row.r2 < 0.35 else "closely")
    move = (f"{abs(row.beta):.2f}% {row.unit}" if row.mode == "level"
            else f"{abs(row.beta):.2f}% for every 1% move")
    return (f"Has tracked {row.label} {strength} over the past year: "
            f"{row.direction} it, about {move}. "
            f"R² {row.r2:.2f} on {row.observations} days -- an association in past "
            f"data, not a mechanism.")
