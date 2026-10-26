"""Watchlist persistence."""
from __future__ import annotations

from typing import List, Tuple

from sqlalchemy import func, select

from .db import session_scope
from .db.models import Watchlist, WatchlistItem
from .universe import DEFAULT_WATCHLIST

DEFAULT_NAME = "Core"


def get_or_create_default() -> int:
    with session_scope() as s:
        wl = s.scalar(select(Watchlist).order_by(Watchlist.id).limit(1))
        if wl is None:
            wl = Watchlist(name=DEFAULT_NAME)
            s.add(wl)
            s.flush()
            s.add_all([WatchlistItem(watchlist_id=wl.id, symbol=sym, position=i)
                       for i, sym in enumerate(DEFAULT_WATCHLIST)])
        return wl.id


def all_watchlists() -> List[Tuple[int, str]]:
    get_or_create_default()
    with session_scope() as s:
        return [(w.id, w.name) for w in s.scalars(select(Watchlist).order_by(Watchlist.id))]


def create(name: str) -> int:
    with session_scope() as s:
        wl = Watchlist(name=name.strip())
        s.add(wl)
        s.flush()
        return wl.id


def delete(watchlist_id: int) -> None:
    with session_scope() as s:
        s.query(WatchlistItem).filter_by(watchlist_id=watchlist_id).delete()
        s.query(Watchlist).filter_by(id=watchlist_id).delete()


def list_symbols(watchlist_id: int) -> List[str]:
    with session_scope() as s:
        return list(s.scalars(select(WatchlistItem.symbol).where(WatchlistItem.watchlist_id == watchlist_id)
                              .order_by(WatchlistItem.position)))


def add_symbol(watchlist_id: int, symbol: str) -> None:
    symbol = symbol.strip().upper()
    with session_scope() as s:
        if s.get(WatchlistItem, (watchlist_id, symbol)) is None:
            pos = s.scalar(select(func.coalesce(func.max(WatchlistItem.position), -1))
                           .where(WatchlistItem.watchlist_id == watchlist_id)) + 1
            s.add(WatchlistItem(watchlist_id=watchlist_id, symbol=symbol, position=pos))


def remove_symbol(watchlist_id: int, symbol: str) -> None:
    with session_scope() as s:
        s.query(WatchlistItem).filter_by(watchlist_id=watchlist_id, symbol=symbol).delete()


def all_watched_symbols() -> List[str]:
    get_or_create_default()
    with session_scope() as s:
        return list(dict.fromkeys(s.scalars(select(WatchlistItem.symbol).order_by(WatchlistItem.position))))
