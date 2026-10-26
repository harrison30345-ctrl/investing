"""
Chronological data splits.

    |---- train (60%) ----|-- validation (20%) --|-- test (20%) --|

* Train: explore ideas and tune parameters freely.
* Validation: check that the tuned choice holds up on data it wasn't tuned on.
* Test: touch once, at the end, for a final honest estimate. Running on it
  needs an explicit confirmation, and the run is flagged in the experiment log,
  so you can see how often it has been looked at.

Walk-forward: repeat "tune on a window, evaluate on the next window", rolling
forward, then stitch the out-of-sample pieces together.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import pandas as pd

PERIODS = ("train", "validation", "test", "full")


@dataclass(frozen=True)
class Split:
    train: Tuple[pd.Timestamp, pd.Timestamp]
    validation: Tuple[pd.Timestamp, pd.Timestamp]
    test: Tuple[pd.Timestamp, pd.Timestamp]
    holdout_start: Optional[str] = None      # set when 'test' is a fixed calendar holdout

    def window(self, period: str) -> Tuple[pd.Timestamp, pd.Timestamp]:
        if period == "full":
            return self.train[0], self.test[1]
        return getattr(self, period)

    def to_dict(self) -> Dict[str, List[str]]:
        out = {k: [getattr(self, k)[0].strftime("%Y-%m-%d"), getattr(self, k)[1].strftime("%Y-%m-%d")]
               for k in ("train", "validation", "test")}
        if self.holdout_start:
            out["holdout_start"] = self.holdout_start
        return out


def chronological_split(dates: pd.DatetimeIndex, train: float = 0.6, validation: float = 0.2) -> Split:
    dates = pd.DatetimeIndex(sorted(dates))
    if len(dates) < 30:
        raise ValueError("Not enough history to split (need at least 30 sessions)")
    n = len(dates)
    i1, i2 = int(n * train), int(n * (train + validation))
    return Split((dates[0], dates[i1 - 1]), (dates[i1], dates[i2 - 1]), (dates[i2], dates[-1]))


def walk_forward_windows(dates: pd.DatetimeIndex, train_bars: int, test_bars: int,
                         step: int = 0) -> List[Tuple[Tuple[pd.Timestamp, pd.Timestamp], Tuple[pd.Timestamp, pd.Timestamp]]]:
    """[(in_sample_window, out_of_sample_window), ...] rolling forward by `step` (default test_bars)."""
    step = step or test_bars
    dates = pd.DatetimeIndex(sorted(dates))
    out = []
    i = 0
    while i + train_bars + test_bars <= len(dates):
        ins = (dates[i], dates[i + train_bars - 1])
        oos = (dates[i + train_bars], dates[i + train_bars + test_bars - 1])
        out.append((ins, oos))
        i += step
    return out
