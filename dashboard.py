"""
Streamlit Cloud entry point.

Streamlit Cloud runs this file at the repository root, but the application
lives in the `screener` package. This module is a launcher, not a copy.

WHY runpy AND NOT `import screener.dashboard`
---------------------------------------------
Streamlit re-executes the entry script on every interaction. `import` does not
re-execute: after the first run the module sits in sys.modules and the import
becomes a no-op, so every rerun renders an empty page. That is a real bug this
file shipped with -- the app appeared to work on first load and then went blank
on the next interaction.

runpy.run_path executes the module's source each time, which is what a
Streamlit entry point has to do.

Do not add application code here. The repository root previously held a full
duplicate of the package that had to be hand-synced and had already drifted;
there is now exactly one copy of each module, in `screener/`.
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent

# Make both the repository root and the package importable, so the app's own
# `from screener.x import y` and its `from x import y` fallback both resolve.
for path in (str(_ROOT), str(_ROOT / "screener")):
    if path not in sys.path:
        sys.path.insert(0, path)

runpy.run_path(str(_ROOT / "screener" / "dashboard.py"), run_name="__main__")
