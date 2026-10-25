"""Reports: readable performance and validation summaries (Phase 5)."""
from __future__ import annotations

from quantedge.ui import components as C

C.page_header("Reports", "Plain-English summaries of strategy performance and validation")
C.empty_state("Reports not built yet (Phase 5)",
              "Reports will summarise each strategy's train/validation results, costs, drawdowns and known biases. "
              "Until then, the Backtests page shows each experiment's full metrics.")
