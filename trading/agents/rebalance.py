from __future__ import annotations

"""
Shared rebalancing logic. Given a {symbol: score} map, decide what to buy and
sell to hold an equal-weighted basket of the top-scoring names. Both the stock
agent and the crypto agent produce scores their own way, then delegate the
"turn scores into trades" step here so the portfolio logic lives in one place.
"""

from ..broker.base import BrokerBase
from .base import Decision


def rebalance_decisions(
    broker: BrokerBase,
    scores: dict[str, float],
    *,
    buy_threshold: float,
    sell_threshold: float,
    max_positions: int,
    stop_loss_pct: float,
    fractional: bool = False,   # crypto trades fractional units; stocks whole shares
) -> list[Decision]:
    if not scores:
        return []

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    targets = {sym for sym, sc in ranked[:max_positions] if sc >= buy_threshold}

    account = broker.get_account()
    held = {p.symbol: p for p in broker.get_positions()}
    decisions: list[Decision] = []

    # --- SELL: exit anything we shouldn't hold anymore ---
    for symbol, pos in held.items():
        score = scores.get(symbol)
        if pos.unrealized_pl_pct <= -stop_loss_pct:
            decisions.append(Decision(
                symbol, "sell", pos.qty,
                f"stop-loss hit ({pos.unrealized_pl_pct:.1f}%)"))
        elif score is not None and score < sell_threshold:
            decisions.append(Decision(
                symbol, "sell", pos.qty,
                f"score fell to {score:.0f} (< {sell_threshold:.0f})"))
        elif symbol not in targets:
            decisions.append(Decision(
                symbol, "sell", pos.qty, "dropped out of top ranks"))

    # --- BUY: enter target names we don't yet hold, equal-weighted ---
    per_position_dollars = account.equity / max_positions
    for symbol in targets:
        if symbol in held:
            continue
        price = broker.get_price(symbol)
        if not price:
            continue
        # Don't spend more than available cash on this slice.
        budget = min(per_position_dollars, account.cash)
        qty = round(budget / price, 6) if fractional else int(budget // price)
        if qty > 0 and price * qty <= account.cash + 1e-9:
            decisions.append(Decision(
                symbol, "buy", qty,
                f"score {scores[symbol]:.0f} ≥ {buy_threshold:.0f}, "
                f"top-{max_positions} pick"))

    return decisions
